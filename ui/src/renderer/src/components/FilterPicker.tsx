import type { FilterName } from '../lib/types'

/** CSS approximations of the FFmpeg color presets (video/filters.py is the
 *  source of truth for the real render). Used two ways:
 *  - tile previews in this picker (example gradient swatches)
 *  - live preview on the clip editor's <video> element */
export const FILTER_CSS: Record<FilterName, string> = {
  none: 'none',
  vibrant: 'saturate(1.35) contrast(1.08)',
  warm: 'sepia(0.22) saturate(1.2) hue-rotate(-8deg)',
  cool: 'saturate(1.05) hue-rotate(12deg)',
  cinematic: 'contrast(1.12) saturate(0.92) hue-rotate(-4deg)',
  vintage: 'sepia(0.38) contrast(0.95) saturate(0.85)',
  bw: 'grayscale(1) contrast(1.15)',
  fade: 'contrast(0.92) saturate(0.8) brightness(1.06)'
}

export const FILTER_NAMES = Object.keys(FILTER_CSS) as FilterName[]

const LABELS: Record<FilterName, string> = {
  none: 'None',
  vibrant: 'Vibrant',
  warm: 'Warm',
  cool: 'Cool',
  cinematic: 'Cinematic',
  vintage: 'Vintage',
  bw: 'B&W',
  fade: 'Fade'
}

/** A colorful sample every preset visibly transforms. */
const SWATCH_BG =
  'linear-gradient(135deg, #f59e0b 0%, #ef4444 30%, #8b5cf6 60%, #0ea5e9 100%)'

export default function FilterPicker({
  value,
  onChange
}: {
  value: FilterName
  onChange: (f: FilterName) => void
}): JSX.Element {
  return (
    <div className="grid grid-cols-4 gap-2" role="radiogroup" aria-label="Color filter">
      {FILTER_NAMES.map((name) => (
        <button
          key={name}
          role="radio"
          aria-checked={value === name}
          onClick={() => onChange(name)}
          className={`rounded-lg overflow-hidden border text-left transition-colors ${
            value === name ? 'border-accent' : 'border-raised/60 hover:border-raised'
          }`}
        >
          <div
            className="h-10 w-full"
            style={{ background: SWATCH_BG, filter: FILTER_CSS[name] }}
            aria-hidden
          />
          <p className={`px-2 py-1 text-xs ${value === name ? 'text-accent' : 'text-muted'}`}>
            {LABELS[name]}
          </p>
        </button>
      ))}
    </div>
  )
}
