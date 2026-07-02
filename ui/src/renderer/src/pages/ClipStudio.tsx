import { useEffect, useMemo, useState } from 'react'
import ClipCard from '../components/ClipCard'
import ClipEditor from '../components/ClipEditor'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from '../components/CaptionStyleControls'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { CaptionStyle, Clip, StudioEvent, Video } from '../lib/types'

const STYLE_KEY = 'generate-caption-style'

function loadSavedStyle(): Required<CaptionStyle> {
  try {
    return { ...DEFAULT_CAPTION_STYLE, ...JSON.parse(localStorage.getItem(STYLE_KEY) ?? '{}') }
  } catch {
    return { ...DEFAULT_CAPTION_STYLE }
  }
}

export default function ClipStudio(): JSX.Element {
  const [url, setUrl] = useState('')
  const [videos, setVideos] = useState<Video[]>([])
  const [activeVideo, setActiveVideo] = useState<string | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [selectedClip, setSelectedClip] = useState<number | null>(null)
  const [stage, setStage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [styleOpen, setStyleOpen] = useState(false)
  const [captionStyle, setCaptionStyle] = useState<Required<CaptionStyle>>(loadSavedStyle)
  const [burnCaptions, setBurnCaptions] = useState<boolean>(
    localStorage.getItem('generate-captions') !== 'false'
  )
  const [longClips, setLongClips] = useState<boolean>(
    localStorage.getItem('generate-long-clips') === 'true'
  )

  const toggleBurnCaptions = (on: boolean): void => {
    setBurnCaptions(on)
    localStorage.setItem('generate-captions', String(on))
  }

  const toggleLongClips = (on: boolean): void => {
    setLongClips(on)
    localStorage.setItem('generate-long-clips', String(on))
  }

  const setStyleField = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => {
    setCaptionStyle((s) => {
      const next = { ...s, [key]: value }
      localStorage.setItem(STYLE_KEY, JSON.stringify(next))
      return next
    })
  }

  const refreshVideos = async (): Promise<void> => {
    try {
      const v = await api.videos()
      setVideos(v)
      if (!activeVideo && v.length > 0) setActiveVideo(v[0].video_id)
    } catch {
      /* backend starting up */
    }
  }

  const refreshClips = async (videoId: string): Promise<void> => {
    try {
      setClips(await api.clips(videoId))
    } catch {
      setClips([])
    }
  }

  useEffect(() => {
    refreshVideos()
  }, [])

  useEffect(() => {
    if (activeVideo) refreshClips(activeVideo)
    setSelectedClip(null)
  }, [activeVideo])

  useEvents((e: StudioEvent) => {
    if (e.type === 'progress') {
      if (e.stage === 'render') setStage(`Rendering clip ${e.clip}/${e.total}…`)
      else if (e.stage === 'done') {
        setStage(null)
        refreshVideos()
        if (e.video_id) {
          setActiveVideo(e.video_id)
          refreshClips(e.video_id)
        }
      } else setStage(`${e.stage}…`)
    }
    if (e.type === 'job' && e.status === 'failed') {
      setStage(null)
      setError(e.error ?? 'processing failed')
    }
    if (e.type === 'job' && e.status === 'done' && activeVideo) refreshClips(activeVideo)
  })

  const generate = async (): Promise<void> => {
    if (!url.trim()) return
    setError(null)
    try {
      await api.createJob(url.trim(), { captionStyle, captions: burnCaptions, longClips })
      setStage('Queued…')
      setUrl('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const current = useMemo(() => clips.find((c) => c.id === selectedClip) ?? null, [clips, selectedClip])

  return (
    <div className="p-6 space-y-5">
      <h2 className="text-2xl font-bold">Clip Studio</h2>

      <div className="card flex gap-3 items-center flex-wrap">
        <input
          className="input flex-1 min-w-64"
          placeholder="Paste a YouTube URL…"
          aria-label="YouTube video URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && generate()}
        />
        <label className="flex items-center gap-2 cursor-pointer text-sm shrink-0">
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={burnCaptions}
            onChange={(e) => toggleBurnCaptions(e.target.checked)}
          />
          Captions
        </label>
        <label
          className="flex items-center gap-2 cursor-pointer text-sm shrink-0"
          title="TikTok monetization requires videos over 1 minute. On: clips run 61-180s. Off: the engine picks whatever length makes the best clip."
        >
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={longClips}
            onChange={(e) => toggleLongClips(e.target.checked)}
          />
          60s+ <span className="text-muted">(TikTok monetization)</span>
        </label>
        <button
          className="btn-ghost shrink-0"
          onClick={() => setStyleOpen(!styleOpen)}
          aria-expanded={styleOpen}
          disabled={!burnCaptions}
        >
          Caption style {styleOpen ? '▾' : '▸'}
        </button>
        <button className="btn-accent shrink-0" onClick={generate} disabled={stage !== null}>
          Generate clips
        </button>
        <p className="w-full text-[11px] text-muted -mt-1">
          Every clip that scores above the quality bar is kept — no arbitrary limit.
        </p>
        {styleOpen && (
          <div className="w-full space-y-3 border-t border-raised/60 pt-3">
            <p className="label">Caption style for all new clips (remembered)</p>
            <CaptionStyleControls idPrefix="gen" style={captionStyle} onChange={setStyleField} />
          </div>
        )}
      </div>

      {stage && (
        <div className="card flex items-center gap-3">
          <span className="size-2 rounded-full bg-accent animate-pulse" />
          <p className="text-sm">{stage}</p>
        </div>
      )}
      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}

      {videos.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          {videos.map((v) => (
            <button
              key={v.video_id}
              onClick={() => setActiveVideo(v.video_id)}
              className={`px-3 py-1.5 rounded-lg text-sm max-w-64 truncate ${
                activeVideo === v.video_id ? 'bg-accent/15 text-accent' : 'bg-raised text-muted hover:text-ink'
              }`}
            >
              {v.channel_name ? `${v.channel_name} — ` : ''}
              {v.title || v.video_id}
            </button>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-5 gap-5 items-start">
        <div className="xl:col-span-3 grid grid-cols-2 md:grid-cols-3 gap-4">
          {clips.map((clip) => (
            <ClipCard
              key={clip.id}
              clip={clip}
              selected={clip.id === selectedClip}
              onClick={() => setSelectedClip(clip.id)}
            />
          ))}
          {clips.length === 0 && (
            <p className="text-muted text-sm col-span-full">
              No clips for this video yet — or pick another video above.
            </p>
          )}
        </div>
        <div className="xl:col-span-2">
          {current ? (
            <ClipEditor clip={current} onChanged={() => activeVideo && refreshClips(activeVideo)} />
          ) : (
            <div className="card text-muted text-sm">Select a clip to preview and edit it.</div>
          )}
        </div>
      </div>
    </div>
  )
}
