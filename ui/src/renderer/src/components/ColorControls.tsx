import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import type { Adjust, Clip } from '../lib/types'
import FilterPicker, { FILTER_CSS } from './FilterPicker'
import { Palette } from './icons'

const NEUTRAL_ADJUST: Required<Adjust> = {
  brightness: 0,
  saturation: 1,
  contrast: 1,
  temperature: 0,
  tint: 0,
  sharpen: 0,
  vignette: 0
}

function adjustCss(a: Required<Adjust>): string {
  const parts: string[] = []
  if (Math.abs(a.brightness) > 0.005) parts.push(`brightness(${(1 + a.brightness).toFixed(3)})`)
  if (Math.abs(a.saturation - 1) > 0.005) parts.push(`saturate(${a.saturation.toFixed(3)})`)
  if (Math.abs(a.contrast - 1) > 0.005) parts.push(`contrast(${a.contrast.toFixed(3)})`)
  // Rough CSS stand-ins — the re-render does the real color math.
  if (a.temperature > 0.005)
    parts.push(`sepia(${(a.temperature * 0.25).toFixed(3)}) hue-rotate(${(-8 * a.temperature).toFixed(1)}deg) saturate(${(1 + 0.08 * a.temperature).toFixed(3)})`)
  if (a.temperature < -0.005) parts.push(`hue-rotate(${(-12 * a.temperature).toFixed(1)}deg)`)
  if (Math.abs(a.tint) > 0.005) parts.push(`hue-rotate(${(-20 * a.tint).toFixed(1)}deg)`)
  if (a.sharpen > 0.005) parts.push(`contrast(${(1 + 0.06 * a.sharpen).toFixed(3)})`)
  return parts.join(' ')
}

function sameAdjust(a: Required<Adjust>, b: Required<Adjust>): boolean {
  return (Object.keys(NEUTRAL_ADJUST) as (keyof Required<Adjust>)[]).every(
    (k) => Math.abs(a[k] - b[k]) < 0.005
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

    // Vignette can't be a CSS filter — preview it as an inset-shadow overlay
    // on the video's container (removed on cleanup / when set back to 0).
    const box = el.parentElement
    const pendingVignette =
      Math.abs(adjust.vignette - renderedAdjust.vignette) >= 0.005 ? adjust.vignette : 0
    let shade = box?.querySelector<HTMLDivElement>(':scope > .live-vignette') ?? null
    if (box && pendingVignette > 0.005) {
      if (!shade) {
        shade = document.createElement('div')
        shade.className = 'live-vignette'
        shade.style.cssText =
          'position:absolute;inset:0;pointer-events:none;z-index:5;border-radius:0.75rem'
        box.appendChild(shade)
      }
      shade.style.boxShadow = `inset 0 0 ${Math.round(140 * pendingVignette)}px ${Math.round(
        50 * pendingVignette
      )}px rgba(0,0,0,0.85)`
    } else {
      shade?.remove()
    }
    return () => {
      el.style.filter = ''
      box?.querySelector(':scope > .live-vignette')?.remove()
    }
  }, [clipFilter, adjust, renderedFilter, videoRef])

  // signed: shows +N / −N around a neutral centre; % shows a percentage
  const sliders: {
    key: keyof Required<Adjust>
    label: string
    min: number
    max: number
    signed?: boolean
  }[] = [
    { key: 'brightness', label: 'Brightness', min: -50, max: 50, signed: true },
    { key: 'saturation', label: 'Saturation', min: 0, max: 300 },
    { key: 'contrast', label: 'Contrast', min: 50, max: 200 },
    { key: 'temperature', label: 'Temperature (cool ↔ warm)', min: -100, max: 100, signed: true },
    { key: 'tint', label: 'Tint (green ↔ magenta)', min: -100, max: 100, signed: true },
    { key: 'sharpen', label: 'Sharpen', min: 0, max: 100 },
    { key: 'vignette', label: 'Vignette', min: 0, max: 100 }
  ]

  return (
    <div className="border border-raised/60 rounded-lg p-3 space-y-3">
      <p className="font-medium text-sm inline-flex items-center gap-1.5">
        <Palette size={15} /> Color &amp; look
      </p>
      <FilterPicker value={clipFilter} onChange={setClipFilter} />
      <div className="space-y-2">
        {sliders.map(({ key, label, min, max, signed }) => (
          <div key={key}>
            <label htmlFor={`adj-${key}-${clip.id}`} className="label flex justify-between">
              <span>{label}</span>
              <span className="tabular-nums">
                {signed
                  ? `${adjust[key] > 0 ? '+' : ''}${Math.round(adjust[key] * 100)}`
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
