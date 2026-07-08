import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { Adjust, Clip, FilterName } from '../lib/types'
import CaptionEditor from './CaptionEditor'
import EditChat from './EditChat'
import FilterPicker, { FILTER_CSS } from './FilterPicker'
import TimelineEditor from './TimelineEditor'

const NEUTRAL_ADJUST: Required<Adjust> = { brightness: 0, saturation: 1, contrast: 1 }

function adjustCss(a: Required<Adjust>): string {
  const parts: string[] = []
  if (Math.abs(a.brightness) > 0.005) parts.push(`brightness(${(1 + a.brightness).toFixed(3)})`)
  if (Math.abs(a.saturation - 1) > 0.005) parts.push(`saturate(${a.saturation.toFixed(3)})`)
  if (Math.abs(a.contrast - 1) > 0.005) parts.push(`contrast(${a.contrast.toFixed(3)})`)
  return parts.join(' ')
}

function sameAdjust(a: Required<Adjust>, b: Required<Adjust>): boolean {
  return (
    Math.abs(a.brightness - b.brightness) < 0.005 &&
    Math.abs(a.saturation - b.saturation) < 0.005 &&
    Math.abs(a.contrast - b.contrast) < 0.005
  )
}

const CHANNELS = ['text', 'audio', 'visual', 'reaction', 'engagement'] as const

export default function ClipEditor({
  clip,
  onChanged
}: {
  clip: Clip
  onChanged: () => void
}): JSX.Element {
  const [title, setTitle] = useState(clip.title)
  const [description, setDescription] = useState(clip.description)
  const [hashtags, setHashtags] = useState(clip.hashtags.join(' '))
  const [start, setStart] = useState(clip.start_s)
  const [end, setEnd] = useState(clip.end_s)
  const [folder, setFolder] = useState('exports')
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [clipFilter, setClipFilter] = useState<FilterName>(clip.render_opts?.filter ?? 'none')
  const renderedFilter = clip.render_opts?.filter ?? 'none'
  const renderedAdjust: Required<Adjust> = { ...NEUTRAL_ADJUST, ...clip.render_opts?.adjust }
  const [adjust, setAdjust] = useState<Required<Adjust>>(renderedAdjust)
  const colorDirty = clipFilter !== renderedFilter || !sameAdjust(adjust, renderedAdjust)

  useEffect(() => {
    setTitle(clip.title)
    setDescription(clip.description)
    setHashtags(clip.hashtags.join(' '))
    setStart(clip.start_s)
    setEnd(clip.end_s)
    setNotice(null)
    setClipFilter(clip.render_opts?.filter ?? 'none')
    setAdjust({ ...NEUTRAL_ADJUST, ...clip.render_opts?.adjust })
  }, [clip.id])

  const flash = (msg: string): void => {
    setNotice(msg)
    setTimeout(() => setNotice(null), 4000)
  }

  const run = async (label: string, fn: () => Promise<void>): Promise<void> => {
    setBusy(label)
    try {
      await fn()
    } catch (e) {
      flash(`Error: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(null)
    }
  }

  const saveMetadata = (): Promise<void> =>
    run('save', async () => {
      await api.patchClip(clip.id, {
        title,
        description,
        hashtags: hashtags.split(/\s+/).filter(Boolean)
      })
      flash('Saved')
      onChanged()
    })

  const rerender = (): Promise<void> =>
    run('render', async () => {
      const range = start !== clip.start_s || end !== clip.end_s ? { start, end } : undefined
      await api.rerenderClip(clip.id, range)
      flash('Re-render queued — watch the Dashboard activity feed')
    })

  const exportOne = (): Promise<void> =>
    run('export', async () => {
      const res = await api.exportClip(clip.id, folder)
      flash(res.exported.length ? `Exported: ${res.exported[0]}` : 'Nothing exported')
    })

  return (
    <div className="card space-y-4 sticky top-6">
      <video
        key={clip.id}
        ref={videoRef}
        src={api.mediaUrl(clip.id)}
        controls
        aria-label={`Preview of clip: ${clip.title || clip.hook || 'untitled'}. Captions are burned into the video.`}
        className="w-full rounded-lg bg-base max-h-96"
        style={{
          // Live example of pending color changes (approximate); the file
          // itself already has the rendered filter/adjustments burned in.
          filter:
            [
              clipFilter !== renderedFilter && FILTER_CSS[clipFilter] !== 'none'
                ? FILTER_CSS[clipFilter]
                : '',
              !sameAdjust(adjust, renderedAdjust) ? adjustCss(adjust) : ''
            ]
              .filter(Boolean)
              .join(' ') || 'none'
        }}
      />

      <div className="flex gap-2 flex-wrap text-xs">
        {CHANNELS.map((ch) => (
          <span key={ch} className="bg-raised px-2 py-1 rounded-md text-muted">
            {ch} <span className="text-ink font-semibold">{clip.scores[ch] ?? '–'}</span>
          </span>
        ))}
      </div>

      <TimelineEditor clip={clip} videoRef={videoRef} onChanged={onChanged} />

      <div className="border border-raised/60 rounded-lg p-3 space-y-3">
        <p className="font-medium text-sm">Color &amp; look</p>
        <FilterPicker value={clipFilter} onChange={setClipFilter} />

        <div className="space-y-2">
          <div>
            <label htmlFor={`adj-b-${clip.id}`} className="label flex justify-between">
              <span>Brightness</span>
              <span className="tabular-nums">
                {adjust.brightness > 0 ? '+' : ''}
                {Math.round(adjust.brightness * 100)}
              </span>
            </label>
            <input
              id={`adj-b-${clip.id}`}
              type="range"
              min={-50}
              max={50}
              value={Math.round(adjust.brightness * 100)}
              className="w-full accent-[#38BDF8]"
              onChange={(e) => setAdjust((a) => ({ ...a, brightness: Number(e.target.value) / 100 }))}
            />
          </div>
          <div>
            <label htmlFor={`adj-s-${clip.id}`} className="label flex justify-between">
              <span>Saturation</span>
              <span className="tabular-nums">{Math.round(adjust.saturation * 100)}%</span>
            </label>
            <input
              id={`adj-s-${clip.id}`}
              type="range"
              min={0}
              max={300}
              value={Math.round(adjust.saturation * 100)}
              className="w-full accent-[#38BDF8]"
              onChange={(e) => setAdjust((a) => ({ ...a, saturation: Number(e.target.value) / 100 }))}
            />
          </div>
          <div>
            <label htmlFor={`adj-c-${clip.id}`} className="label flex justify-between">
              <span>Contrast</span>
              <span className="tabular-nums">{Math.round(adjust.contrast * 100)}%</span>
            </label>
            <input
              id={`adj-c-${clip.id}`}
              type="range"
              min={50}
              max={200}
              value={Math.round(adjust.contrast * 100)}
              className="w-full accent-[#38BDF8]"
              onChange={(e) => setAdjust((a) => ({ ...a, contrast: Number(e.target.value) / 100 }))}
            />
          </div>
          <button
            className="text-xs text-muted hover:text-ink"
            onClick={() => setAdjust({ ...NEUTRAL_ADJUST })}
          >
            Reset adjustments
          </button>
        </div>

        {colorDirty && (
          <button
            className="btn-accent w-full"
            disabled={busy !== null}
            onClick={() =>
              run('filter', async () => {
                await api.rerenderClip(clip.id, undefined, { filter: clipFilter, adjust })
                flash('Color changes queued — the clip is re-rendering. Preview above is approximate.')
              })
            }
          >
            {busy === 'filter' ? 'Queueing…' : 'Apply color changes (re-render)'}
          </button>
        )}
      </div>

      <div className="space-y-3">
        <div>
          <label className="label">Title</label>
          <input className="input mt-1" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <label className="label">Description</label>
          <textarea
            className="input mt-1 h-20 resize-none"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="label">Hashtags (space-separated)</label>
          <input className="input mt-1" value={hashtags} onChange={(e) => setHashtags(e.target.value)} />
        </div>
        <div className="flex gap-3">
          <div className="flex-1">
            <label className="label">Start (s)</label>
            <input
              type="number"
              className="input mt-1"
              value={start}
              step={0.5}
              onChange={(e) => setStart(Number(e.target.value))}
            />
          </div>
          <div className="flex-1">
            <label className="label">End (s)</label>
            <input
              type="number"
              className="input mt-1"
              value={end}
              step={0.5}
              onChange={(e) => setEnd(Number(e.target.value))}
            />
          </div>
        </div>
      </div>

      <div className="flex gap-2 flex-wrap items-center">
        <button className="btn-accent" onClick={saveMetadata} disabled={busy !== null}>
          {busy === 'save' ? 'Saving…' : 'Save metadata'}
        </button>
        <button className="btn-ghost" onClick={rerender} disabled={busy !== null}>
          {busy === 'render' ? 'Queueing…' : 'Re-render'}
        </button>
        <input
          className="input !w-36"
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          placeholder="export folder"
        />
        <button className="btn-ghost" onClick={exportOne} disabled={busy !== null}>
          {busy === 'export' ? 'Exporting…' : 'Export'}
        </button>
      </div>
      {notice && <p className="text-sm text-accent">{notice}</p>}

      <CaptionEditor clip={clip} onQueued={flash} />
      <EditChat clip={clip} onQueued={flash} />
    </div>
  )
}
