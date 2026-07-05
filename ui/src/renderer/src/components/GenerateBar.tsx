import { useState } from 'react'
import { api } from '../lib/api'
import type { CaptionStyle } from '../lib/types'
import CaptionStyleControls, { DEFAULT_CAPTION_STYLE } from './CaptionStyleControls'

const STYLE_KEY = 'generate-caption-style'

function loadSavedStyle(): Required<CaptionStyle> {
  try {
    return { ...DEFAULT_CAPTION_STYLE, ...JSON.parse(localStorage.getItem(STYLE_KEY) ?? '{}') }
  } catch {
    return { ...DEFAULT_CAPTION_STYLE }
  }
}

/** The "paste a link to make clips" bar. Shared by the Dashboard (top) and
 *  Clip Studio so creators always see where to post a link. Processing
 *  progress shows via the global <ProcessingBar>, so this stays lightweight. */
export default function GenerateBar(): JSX.Element {
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [reprocessUrl, setReprocessUrl] = useState<string | null>(null)
  const [queued, setQueued] = useState(false)
  const [styleOpen, setStyleOpen] = useState(false)
  const [captionStyle, setCaptionStyle] = useState<Required<CaptionStyle>>(loadSavedStyle)
  const [burnCaptions, setBurnCaptions] = useState<boolean>(
    localStorage.getItem('generate-captions') !== 'false'
  )
  const [longClips, setLongClips] = useState<boolean>(
    localStorage.getItem('generate-long-clips') === 'true'
  )

  const setStyleField = <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]): void => {
    setCaptionStyle((s) => {
      const next = { ...s, [key]: value }
      localStorage.setItem(STYLE_KEY, JSON.stringify(next))
      return next
    })
  }

  const generate = async (targetUrl?: string, force = false): Promise<void> => {
    const u = (targetUrl ?? url).trim()
    if (!u) return
    setError(null)
    setReprocessUrl(null)
    try {
      const res = await api.createJob(u, { force, captionStyle, captions: burnCaptions, longClips })
      if (res.already_processed) {
        setReprocessUrl(u)
        return
      }
      setUrl('')
      setQueued(true)
      setTimeout(() => setQueued(false), 4000)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="space-y-3">
      <div className="card flex gap-3 items-center flex-wrap">
        <input
          className="input flex-1 min-w-64"
          placeholder="Paste a YouTube video, Twitch VOD, or Kick VOD URL…"
          aria-label="Video URL to make clips from"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && generate()}
        />
        <label className="flex items-center gap-2 cursor-pointer text-sm shrink-0">
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={burnCaptions}
            onChange={(e) => {
              setBurnCaptions(e.target.checked)
              localStorage.setItem('generate-captions', String(e.target.checked))
            }}
          />
          Captions
        </label>
        <button
          className="btn-ghost shrink-0"
          onClick={() => setStyleOpen(!styleOpen)}
          aria-expanded={styleOpen}
          disabled={!burnCaptions}
        >
          Caption style {styleOpen ? '▾' : '▸'}
        </button>
        <label
          className="flex items-center gap-2 cursor-pointer text-sm shrink-0"
          title="TikTok monetization requires videos over 1 minute. On: clips run 61-180s. Off: clips are a natural 10-60s."
        >
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={longClips}
            onChange={(e) => {
              setLongClips(e.target.checked)
              localStorage.setItem('generate-long-clips', String(e.target.checked))
            }}
          />
          60s+ <span className="text-muted">(TikTok monetization)</span>
        </label>
        <button className="btn-accent shrink-0" onClick={() => generate()}>
          Generate clips
        </button>
        {styleOpen && (
          <div className="w-full space-y-3 border-t border-raised/60 pt-3">
            <p className="label">Caption style for all new clips (remembered)</p>
            <CaptionStyleControls idPrefix="gen" style={captionStyle} onChange={setStyleField} />
          </div>
        )}
      </div>

      {queued && <p className="text-sm text-accent px-1">Queued — processing will start shortly.</p>}
      {error && <div className="card border-error/40 text-error text-sm">{error}</div>}
      {reprocessUrl && (
        <div className="card flex items-center gap-3 flex-wrap">
          <p className="text-sm flex-1 min-w-64">
            This video was already processed. Generate again with your <b>current settings</b>
            {longClips ? ' (60s+ clips)' : ' (regular clips)'}? Existing clips are kept — new ones
            are added alongside them.
          </p>
          <button className="btn-accent shrink-0" onClick={() => generate(reprocessUrl, true)}>
            Process again
          </button>
          <button className="btn-ghost shrink-0" onClick={() => setReprocessUrl(null)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  )
}
