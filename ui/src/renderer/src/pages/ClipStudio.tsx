import { useEffect, useMemo, useState } from 'react'
import ClipCard from '../components/ClipCard'
import ClipEditor from '../components/ClipEditor'
import { api } from '../lib/api'
import { useEvents } from '../lib/useEvents'
import type { Clip, StudioEvent, Video } from '../lib/types'

export default function ClipStudio(): JSX.Element {
  const [url, setUrl] = useState('')
  const [videos, setVideos] = useState<Video[]>([])
  const [activeVideo, setActiveVideo] = useState<string | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [selectedClip, setSelectedClip] = useState<number | null>(null)
  const [stage, setStage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

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
      await api.createJob(url.trim())
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

      <div className="card flex gap-3 items-center">
        <input
          className="input flex-1"
          placeholder="Paste a YouTube URL…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && generate()}
        />
        <button className="btn-accent shrink-0" onClick={generate} disabled={stage !== null}>
          Generate clips
        </button>
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
