import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { Adjust, Clip } from '../lib/types'
import FilterPicker, { FILTER_CSS } from './FilterPicker'

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

/** Filter preset + brightness/saturation/contrast, previewed LIVE on the
 *  editor's video element via CSS (approximate; the re-render burns the
 *  real thing). Lives inside the full-screen editor. */
export default function ColorControls({
  clip,
  videoRef,
  onChanged
}: {
  clip: Clip
  videoRef: React.RefObject<HTMLVideoElement>
  onChanged: () => void
}): JSX.Element {
  const renderedFilter = clip.render_opts?.filter ?? 'none'
  const renderedAdjust: Required<Adjust> = { ...NEUTRAL_ADJUST, ...clip.render_opts?.adjust }
  const [clipFilter, setClipFilter] = useState(renderedFilter)
  const [adjust, setAdjust] = useState<Required<Adjust>>(renderedAdjust)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')

  useEffect(() => {
    setClipFilter(clip.render_opts?.filter ?? 'none')
    setAdjust({ ...NEUTRAL_ADJUST, ...clip.render_opts?.adjust })
    setNotice('')
  }, [clip.id])

  const dirty = clipFilter !== renderedFilter || !sameAdjust(adjust, renderedAdjust)

  // Live (approximate) preview of PENDING color changes on the editor video.
  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    const css = [
      clipFilter !== renderedFilter && FILTER_CSS[clipFilter] !== 'none'
        ? FILTER_CSS[clipFilter]
        : '',
      !sameAdjust(adjust, renderedAdjust) ? adjustCss(adjust) : ''
    ]
      .filter(Boolean)
      .join(' ')
    el.style.filter = css
    return () => {
      el.style.filter = ''
    }
  }, [clipFilter, adjust, renderedFilter, videoRef])

  const sliders: { key: keyof Required<Adjust>; label: string; min: number; max: number }[] = [
    { key: 'brightness', label: 'Brightness', min: -50, max: 50 },
    { key: 'saturation', label: 'Saturation', min: 0, max: 300 },
    { key: 'contrast', label: 'Contrast', min: 50, max: 200 }
  ]

  return (
    <div className="border border-raised/60 rounded-lg p-3 space-y-3">
      <p className="font-medium text-sm">🎨 Color &amp; look</p>
      <FilterPicker value={clipFilter} onChange={setClipFilter} />
      <div className="space-y-2">
        {sliders.map(({ key, label, min, max }) => (
          <div key={key}>
            <label htmlFor={`adj-${key}-${clip.id}`} className="label flex justify-between">
              <span>{label}</span>
              <span className="tabular-nums">
                {key === 'brightness'
                  ? `${adjust.brightness > 0 ? '+' : ''}${Math.round(adjust.brightness * 100)}`
                  : `${Math.round(adjust[key] * 100)}%`}
              </span>
            </label>
            <input
              id={`adj-${key}-${clip.id}`}
              type="range"
              min={min}
              max={max}
              value={Math.round(adjust[key] * 100)}
              className="w-full accent-[#38BDF8]"
              onChange={(e) => setAdjust((a) => ({ ...a, [key]: Number(e.target.value) / 100 }))}
            />
          </div>
        ))}
        <button className="text-xs text-muted hover:text-ink" onClick={() => setAdjust({ ...NEUTRAL_ADJUST })}>
          Reset adjustments
        </button>
      </div>
      {dirty && (
        <button
          className="btn-accent w-full"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            try {
              await api.rerenderClip(clip.id, undefined, { filter: clipFilter, adjust })
              setNotice('Color changes queued — re-rendering. Preview is approximate.')
              onChanged()
            } catch (e) {
              setNotice(String(e))
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? 'Queueing…' : 'Apply color changes (re-render)'}
        </button>
      )}
      {notice && <p className="text-xs text-muted">{notice}</p>}
    </div>
  )
}
