import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CaptionAnimation, CaptionPreset, CaptionSafeZone, CaptionStyle } from '../lib/types'
import { t } from '../lib/i18n'
import {
  captionFontWeight,
  captionLayout,
  safeZoneArea,
  useBundledFonts,
  useCaptionPresets
} from '../lib/captionLook'

export const DEFAULT_CAPTION_STYLE: Required<CaptionStyle> = {
  font: 'Arial',
  font_size: 84,
  color: '#FFFFFF',
  position: 'bottom',
  words_per_caption: 3,
  uppercase: true,
  highlight: false,
  highlight_color: '#FFE600',
  second_speaker: false,
  second_speaker_color: '#5CE1FF',
  animation: 'none',
  safe_zone: 'off'
}

/** Fonts on every stock Windows install — matches video/captions.py FONTS. */
export const CAPTION_FONTS = [
  'Arial',
  'Arial Black',
  'Impact',
  'Verdana',
  'Tahoma',
  'Trebuchet MS',
  'Segoe UI',
  'Georgia',
  'Comic Sans MS',
  'Courier New'
]

/** video/captions.py ANIMATIONS, with the names a person sees. */
const ANIMATIONS: [CaptionAnimation, string][] = [
  ['none', 'None'],
  ['pop', 'Pop'],
  ['fade', 'Fade'],
  ['bounce', 'Bounce'],
  ['karaoke', 'Karaoke'],
  ['slide', 'Slide up']
]

/** video/captions.py SAFE_ZONE_CHOICES. */
const SAFE_ZONES: [CaptionSafeZone, string][] = [
  ['off', 'Off'],
  ['tiktok', 'TikTok'],
  ['reels', 'Instagram Reels'],
  ['shorts', 'YouTube Shorts']
]

/** A preset fills every control except the second-speaker switch and colour,
 *  which are the person's own (every preset has them off). */
const KEPT_ON_PICK = ['second_speaker', 'second_speaker_color']

function presetStyle(p: CaptionPreset, current: Required<CaptionStyle>): Required<CaptionStyle> {
  return {
    ...DEFAULT_CAPTION_STYLE,
    ...p.style,
    second_speaker: current.second_speaker,
    second_speaker_color: current.second_speaker_color
  }
}

/** Whether the style is still exactly this preset (colours in any case). */
function matchesPreset(p: CaptionPreset, style: Required<CaptionStyle>, ignore: string[]): boolean {
  const want = presetStyle(p, style)
  return (Object.keys(want) as (keyof CaptionStyle)[]).every((k) => {
    if (ignore.includes(k)) return true
    const a = want[k]
    const b = style[k]
    return typeof a === 'string' && typeof b === 'string' ? a.toUpperCase() === b.toUpperCase() : a === b
  })
}

// One step of the example's word cycle, as before: the highlight moves on a
// word every 550 ms, and a caption lasts at least three steps.
const STEP_MS = 550

/** Live example of how the burned-in captions will look (9:16 mock). The
 *  animation is a CSS copy of video/captions.py's tags at their real speed;
 *  the safe zone shades what the app covers, and the caption stands where
 *  the burn puts it. */
function CaptionExample({ style, weight }: { style: Required<CaptionStyle>; weight: number }): JSX.Element {
  // Show one caption group of exactly words_per_caption words — the same
  // grouping the burn-in uses, so changing the setting changes the example.
  const sample = ['your', 'captions', 'look', 'like', 'this', 'onscreen']
  const words = sample.slice(0, Math.max(1, Math.min(6, style.words_per_caption)))
  const text = style.uppercase ? words.join(' ').toUpperCase() : words.join(' ')
  const karaoke = style.animation === 'karaoke'
  // Reduced motion: the example shows the caption in place, without the
  // movement (the word colours still change).
  const still =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const animated = style.animation !== 'none' && !karaoke && !still
  // A caption comes on screen every `period` steps. The highlight (and
  // karaoke's fill) moves through the words; an animation replays each time.
  const period = Math.max(3, words.length)
  const [step, setStep] = useState(0)
  useEffect(() => {
    setStep(0)
    if (!style.highlight && !karaoke && !animated) return
    const id = setInterval(() => setStep((s) => (s + 1) % period), STEP_MS)
    return () => clearInterval(id)
  }, [style.highlight, karaoke, animated, period])
  const hot = Math.min(step, words.length - 1)

  const boxRef = useRef<HTMLDivElement>(null)
  const capRef = useRef<HTMLParagraphElement>(null)
  useEffect(() => {
    const el = capRef.current
    if (!el || !animated || step !== 0 || typeof el.animate !== 'function') return
    const h = boxRef.current?.clientHeight || 176
    const caption = period * STEP_MS
    let anim: Animation
    if (style.animation === 'pop') {
      anim = el.animate([{ transform: 'scale(0.8)' }, { transform: 'scale(1)' }], { duration: 120 })
    } else if (style.animation === 'bounce') {
      anim = el.animate(
        [
          { transform: 'scale(0.8)', offset: 0 },
          { transform: 'scale(1.1)', offset: 0.6 },
          { transform: 'scale(1)', offset: 1 }
        ],
        { duration: 200 }
      )
    } else if (style.animation === 'slide') {
      anim = el.animate([{ transform: `translateY(${(40 / 1920) * h}px)` }, { transform: 'translateY(0)' }], {
        duration: 150
      })
    } else {
      // fade: in over 120 ms, out over the caption's last 80 ms
      anim = el.animate(
        [
          { opacity: 0, offset: 0 },
          { opacity: 1, offset: 120 / caption },
          { opacity: 1, offset: 1 - 80 / caption },
          { opacity: 0, offset: 1 }
        ],
        { duration: caption }
      )
    }
    return () => anim.cancel()
  }, [step, animated, style.animation, period])

  // The example's text is drawn 2.2x larger than true scale so it can be
  // read in a mock this small. A word that would then stick out past the
  // margins (which it would not in the video) is shrunk until it fits, never
  // below true scale, so the caption stays inside the safe area it is in.
  const base = (style.font_size / 1920) * 176 * 2.2
  const [fontPx, setFontPx] = useState(base)
  useLayoutEffect(() => {
    const p = capRef.current
    if (!p) return
    const fit = (): void => {
      p.style.fontSize = `${base}px`
      const over = p.scrollWidth > p.clientWidth + 0.5 ? p.clientWidth / p.scrollWidth : 1
      const px = over < 1 ? Math.max(base / 2.2, base * over * 0.96) : base
      p.style.fontSize = `${px}px`
      setFontPx(px)
    }
    fit()
    document.fonts?.addEventListener?.('loadingdone', fit)
    return () => document.fonts?.removeEventListener?.('loadingdone', fit)
  }, [base, style.font, weight, style.uppercase, words.length, style.safe_zone, style.position, style.second_speaker])

  const zone = safeZoneArea(style.safe_zone)
  const lay = captionLayout(style, 1080, 1920)
  const side = `${((lay.side ?? 60) / 1080) * 100}%`
  const place: React.CSSProperties =
    lay.position === 'top'
      ? { top: `${(lay.margin / 1920) * 100}%` }
      : lay.position === 'middle'
        ? { top: 0, bottom: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }
        : { bottom: `${(lay.margin / 1920) * 100}%` }
  const origin = lay.position === 'top' ? 'top center' : lay.position === 'middle' ? 'center' : 'bottom center'
  const wordColor = (i: number): string =>
    karaoke ? (i <= hot ? style.highlight_color : style.color) : i === hot ? style.highlight_color : style.color

  return (
    <div
      ref={boxRef}
      className="relative rounded-lg bg-gradient-to-br from-slate-700 via-slate-800 to-slate-900 aspect-[9/16] max-h-44 mx-auto w-auto overflow-hidden"
      aria-label={t('Caption style example')}
      title={[
        style.animation !== 'none'
          ? t('A rough preview of the animation. The finished clip shows the real thing.')
          : '',
        zone ? t('The shaded areas are where the app puts its own buttons and text.') : ''
      ]
        .filter(Boolean)
        .join(' ')}
    >
      {zone && (
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none"
          viewBox="0 0 1080 1920"
          preserveAspectRatio="none"
          aria-hidden="true"
        >
          <g
            fill="white"
            fillOpacity={0.13}
            stroke="white"
            strokeOpacity={0.35}
            strokeWidth={6}
            strokeDasharray="24 16"
          >
            <rect x={0} y={0} width={1080} height={zone.top} />
            <rect x={0} y={1920 - zone.bottom} width={1080} height={zone.bottom} />
            <rect x={1080 - zone.right} y={zone.top} width={zone.right} height={1920 - zone.top - zone.bottom} />
          </g>
        </svg>
      )}
      <div className="absolute" style={{ left: side, right: side, ...place }}>
        <p
          ref={capRef}
          className="text-center leading-tight"
          style={{
            fontFamily: `'${style.font}', sans-serif`,
            color: style.color,
            // 84px at 1920 tall ≈ scale into this ~176px-tall mock (fitted above)
            fontSize: `${fontPx}px`,
            fontWeight: weight,
            width: '100%',
            minWidth: 0,
            WebkitTextStroke: '0.8px black',
            textShadow: '1px 1px 2px rgba(0,0,0,0.9)',
            transformOrigin: origin
          }}
        >
          {style.highlight || karaoke
            ? words.map((w, i) => (
                <span
                  key={i}
                  style={{ color: wordColor(i), transition: karaoke ? 'color 0.3s linear' : undefined }}
                >
                  {(style.uppercase ? w.toUpperCase() : w) + (i < words.length - 1 ? ' ' : '')}
                </span>
              ))
            : text}
          {/* The other speaker's caption, as it follows the main speaker's:
              the two colours side by side are what is being chosen. */}
          {style.second_speaker && (
            <span className="block" style={{ color: style.second_speaker_color }}>
              {style.uppercase ? 'SECOND SPEAKER' : 'second speaker'}
            </span>
          )}
        </p>
      </div>
    </div>
  )
}

/** The caption style controls (look, font, colour, size, position, words,
 *  casing, animation, safe zone), shared between the Generate bar (style for
 *  all new clips) and the per-clip caption editor. */
export default function CaptionStyleControls({
  idPrefix,
  style,
  onChange,
  onPick,
  hideWordsPerCaption = false,
  hideSecondSpeaker = false
}: {
  idPrefix: string
  style: Required<CaptionStyle>
  onChange: <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) => void
  /** A preset was picked: the whole new style. Without it the changed fields
   *  go through onChange one by one, which needs an onChange that builds on
   *  the latest style (a functional state update). */
  onPick?: (style: Required<CaptionStyle>) => void
  /** Translated subtitles inherit their grouping from the lines they
   *  replace, so regrouping does nothing there — hide it rather than offer
   *  a control that silently has no effect. */
  hideWordsPerCaption?: boolean
  /** Translated subtitles are burned in one colour, whoever is talking. */
  hideSecondSpeaker?: boolean
}): JSX.Element {
  const bundled = useBundledFonts()
  const presets = useCaptionPresets()
  const karaoke = style.animation === 'karaoke'
  const ignored = [...KEPT_ON_PICK, ...(hideWordsPerCaption ? ['words_per_caption'] : [])]
  const known = CAPTION_FONTS.includes(style.font) || bundled.some((f) => f.family === style.font)

  const pick = (p: CaptionPreset): void => {
    const next = presetStyle(p, style)
    if (onPick) {
      onPick(next)
      return
    }
    for (const k of Object.keys(next) as (keyof CaptionStyle)[]) {
      if (next[k] !== style[k]) onChange(k, next[k])
    }
  }

  return (
    <>
      {presets.length > 0 && (
        <div>
          <p
            className="label mb-1"
            title={t(
              'Pick a ready-made look. It fills in the settings below, and you can change any of them afterwards.'
            )}
          >
            {t('Presets')}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {presets.map((p) => {
              const on = matchesPreset(p, style, ignored)
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => pick(p)}
                  aria-pressed={on}
                  className={`px-2.5 py-1 rounded-lg text-xs border transition-colors ${
                    on ? 'border-accent bg-accent/15 text-ink' : 'border-raised text-muted hover:text-ink'
                  }`}
                  style={{
                    fontFamily: `'${p.style.font}', sans-serif`,
                    fontWeight: captionFontWeight(p.style.font ?? 'Arial', bundled)
                  }}
                >
                  {t(p.name)}
                </button>
              )
            })}
          </div>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label htmlFor={`${idPrefix}-font`} className="label">
            {t('Font')}
          </label>
          <select
            id={`${idPrefix}-font`}
            className="input mt-1"
            value={style.font}
            title={t(
              "The fonts that come with Clips Kitty are burned in from the app's own files, so they look the same on every PC. If a font can't write your clip's language, the captions switch to a Windows font that can."
            )}
            style={{
              fontFamily: `'${style.font}', sans-serif`,
              fontWeight: captionFontWeight(style.font, bundled)
            }}
            onChange={(e) => onChange('font', e.target.value)}
          >
            {!known && <option value={style.font}>{style.font}</option>}
            <optgroup label={t('Windows fonts')}>
              {CAPTION_FONTS.map((f) => (
                <option key={f} value={f} style={{ fontFamily: `'${f}', sans-serif`, fontWeight: 700 }}>
                  {f}
                </option>
              ))}
            </optgroup>
            {bundled.length > 0 && (
              <optgroup label={t('Fonts that come with Clips Kitty')}>
                {bundled.map((f) => (
                  <option
                    key={f.family}
                    value={f.family}
                    style={{
                      fontFamily: `'${f.family}', sans-serif`,
                      fontWeight: captionFontWeight(f.family, bundled)
                    }}
                  >
                    {f.family}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-color`} className="label">
            {t('Text colour')}
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
            {t('Size')} ({style.font_size})
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
            {t('Position')}
          </label>
          <select
            id={`${idPrefix}-pos`}
            className="input mt-1"
            value={style.position}
            onChange={(e) => onChange('position', e.target.value as CaptionStyle['position'])}
          >
            <option value="bottom">{t('Bottom')}</option>
            <option value="middle">{t('Middle')}</option>
            <option value="top">{t('Top')}</option>
          </select>
        </div>
        {!hideWordsPerCaption && (
          <div>
            <label htmlFor={`${idPrefix}-words`} className="label">
              {t('Words per caption')}
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
        )}
        <div>
          <label htmlFor={`${idPrefix}-anim`} className="label">
            {t('Animation')}
          </label>
          <select
            id={`${idPrefix}-anim`}
            className="input mt-1"
            value={style.animation}
            title={t(
              "How each caption comes on screen. Karaoke fills each word with the highlight colour as it's said."
            )}
            onChange={(e) => onChange('animation', e.target.value as CaptionAnimation)}
          >
            {ANIMATIONS.map(([v, label]) => (
              <option key={v} value={v}>
                {t(label)}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={`${idPrefix}-safe`} className="label">
            {t('Safe zone')}
          </label>
          <select
            id={`${idPrefix}-safe`}
            className="input mt-1"
            value={style.safe_zone}
            title={t(
              'Keeps captions away from the buttons and text the app puts over your video. Only changes vertical clips.'
            )}
            onChange={(e) => onChange('safe_zone', e.target.value as CaptionSafeZone)}
          >
            {SAFE_ZONES.map(([v, label]) => (
              <option key={v} value={v}>
                {t(label)}
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
        {t('UPPERCASE captions')}
      </label>

      <div className="flex items-center gap-3">
        <label
          className={`flex items-center gap-2 text-sm ${karaoke ? 'opacity-60 cursor-default' : 'cursor-pointer'}`}
          title={karaoke ? t("Karaoke already lights up each word as it's said, in this colour.") : undefined}
        >
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={style.highlight || karaoke}
            disabled={karaoke}
            onChange={(e) => onChange('highlight', e.target.checked)}
          />
          {t("Highlight each word as it's said")}
        </label>
        {(style.highlight || karaoke) && (
          <input
            type="color"
            aria-label={t('Highlight colour')}
            className="h-7 w-10 rounded-md bg-raised cursor-pointer shrink-0"
            value={style.highlight_color}
            onChange={(e) => onChange('highlight_color', e.target.value.toUpperCase())}
          />
        )}
      </div>

      {!hideSecondSpeaker && (
        <div className="flex items-center gap-3">
          <label
            className="flex items-center gap-2 cursor-pointer text-sm"
            title={t(
              "When two people talk in a clip, the main speaker keeps the text colour and the other person's captions take this one. A clip with one voice looks the same as always."
            )}
          >
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={style.second_speaker}
              onChange={(e) => onChange('second_speaker', e.target.checked)}
            />
            {t('Second speaker in another colour')}
          </label>
          {style.second_speaker && (
            <input
              type="color"
              aria-label={t("Second speaker's colour")}
              className="h-7 w-10 rounded-md bg-raised cursor-pointer shrink-0"
              value={style.second_speaker_color}
              onChange={(e) => onChange('second_speaker_color', e.target.value.toUpperCase())}
            />
          )}
        </div>
      )}

      <div>
        <p className="label mb-1">{t('Example')}</p>
        <CaptionExample style={style} weight={captionFontWeight(style.font, bundled)} />
      </div>
    </>
  )
}
