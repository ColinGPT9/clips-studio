import type { CaptionStyle } from '../lib/types'

export const DEFAULT_CAPTION_STYLE: Required<CaptionStyle> = {
  font_size: 84,
  color: '#FFFFFF',
  position: 'bottom',
  words_per_caption: 3,
  uppercase: true
}

/** The caption style controls (colour, size, position, words, casing),
 *  shared between the Generate bar (style for all new clips) and the
 *  per-clip caption editor. */
export default function CaptionStyleControls({
  idPrefix,
  style,
  onChange
}: {
  idPrefix: string
  style: Required<CaptionStyle>
  onChange: <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) => void
}): JSX.Element {
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor={`${idPrefix}-color`} className="label">
            Text colour
          </label>
          <input
            id={`${idPrefix}-color`}
            type="color"
            className="mt-1 h-9 w-full rounded-lg bg-raised cursor-pointer"
            value={style.color}
            onChange={(e) => onChange('color', e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-size`} className="label">
            Size ({style.font_size})
          </label>
          <input
            id={`${idPrefix}-size`}
            type="range"
            min={40}
            max={140}
            className="mt-3 w-full accent-[#38BDF8]"
            value={style.font_size}
            onChange={(e) => onChange('font_size', Number(e.target.value))}
          />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-pos`} className="label">
            Position
          </label>
          <select
            id={`${idPrefix}-pos`}
            className="input mt-1"
            value={style.position}
            onChange={(e) => onChange('position', e.target.value as CaptionStyle['position'])}
          >
            <option value="bottom">Bottom</option>
            <option value="middle">Middle</option>
            <option value="top">Top</option>
          </select>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-words`} className="label">
            Words per caption
          </label>
          <select
            id={`${idPrefix}-words`}
            className="input mt-1"
            value={style.words_per_caption}
            onChange={(e) => onChange('words_per_caption', Number(e.target.value))}
          >
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>
      <label className="flex items-center gap-2 cursor-pointer text-sm">
        <input
          type="checkbox"
          className="size-4 accent-[#38BDF8]"
          checked={style.uppercase}
          onChange={(e) => onChange('uppercase', e.target.checked)}
        />
        UPPERCASE captions
      </label>
    </>
  )
}
