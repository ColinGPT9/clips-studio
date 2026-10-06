import { useEffect, useState } from 'react'
import type { CaptionStyle } from '../lib/types'

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
  post_style: 'default',
  card_position: 'lower'
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

/** The look a Highlights clip's captions burn in, whatever its own style
 *  says (video/post_style.py CAPTION_LOOK). Size and words per caption stay
 *  the user's. */
const HIGHLIGHTS_CAPTION_LOOK = {
  font: 'Impact',
  color: '#F5FA00',
  uppercase: true,
  position: 'middle',
  highlight: false,
  second_speaker: false
} as const

/** Whether a style draws the Highlights look. Never on a 16:9 clip: the
 *  renderer applies the post style to vertical clips only. */
export function isHighlights(style: CaptionStyle, landscape = false): boolean {
  return style.post_style === 'highlights' && !landscape
}

/** The caption style a clip really burns with, as
 *  video/post_style.caption_style_for decides it. The editor's live preview
 *  draws pending captions, and masks the ones already burned, with this, so
 *  a Highlights clip's are drawn where and how they actually are. */
export function burnedCaptionStyle(
  style: Required<CaptionStyle>,
  landscape = false
): Required<CaptionStyle> {
  return isHighlights(style, landscape) ? { ...style, ...HIGHLIGHTS_CAPTION_LOOK } : style
}

/** A hashtag as video/post_style.card_text drops it from a card line: a #
 *  word with a letter in it, so a rank or a jersey number (#1, #23) stays.
 *  Unicode classes because JavaScript's \w is ASCII only. */
const HASHTAG = /(^|\s)#(?=[\p{L}\p{N}_]*\p{L})[\p{L}\p{N}_]+/gu

/** A Highlights card headline from a clip's title, for a clip switched to
 *  the style by hand: the fallback the pipeline uses when the model wrote
 *  none (video/post_style.headline_from_title). */
export function headlineFromTitle(title: string): string {
  return title
    .replace(HASHTAG, ' ')
    .replace(/\s+/g, ' ')
    .replace(/^[ \-|·]+|[ \-|·]+$/g, '')
    .toUpperCase()
}

/** The highlights post style in miniature (video/post_style.py): the clip
 *  framed as usual, the stacked title card, yellow ALL CAPS captions. */
function HighlightsExample({ style }: { style: Required<CaptionStyle> }): JSX.Element {
  const words = ['no', 'way', 'he', 'hit', 'that', 'shot'].slice(
    0,
    Math.max(1, Math.min(6, style.words_per_caption))
  )
  const card = (
    <div className="flex flex-col items-center">
      {/* One line each, as the real card draws these: a sample short
          enough to stay inside the ~99px-wide mock even where the face
          is not condensed, and nowrap so it never breaks into two boxes. */}
      <span
        className="rounded-[3px] bg-black px-1.5 py-1 leading-none text-[#F5FA00] whitespace-nowrap"
        style={{
          fontFamily: "'Bahnschrift', 'Impact', sans-serif",
          fontWeight: 700,
          fontStretch: 'condensed',
          fontSize: '9px'
        }}
      >
        LOGO THREE!😤
      </span>
      <span
        className="rounded-[3px] bg-[#F5FA00] px-1 py-0.5 leading-none text-black whitespace-nowrap"
        style={{
          fontFamily: "'Bahnschrift', 'Impact', sans-serif",
          fontWeight: 700,
          fontStretch: 'condensed',
          fontSize: '6.5px'
        }}
      >
        HE CALLED GAME👀
      </span>
    </div>
  )
  const top = style.card_position === 'top'
  return (
    <div
      className="relative rounded-lg bg-gradient-to-b from-slate-800 via-amber-900 to-amber-700 h-44 aspect-[9/16] mx-auto overflow-hidden"
      aria-label="Highlights style example"
    >
      {top && <div className="absolute inset-x-0 top-[13%]">{card}</div>}
      <p
        className="absolute inset-x-0 top-1/2 -translate-y-1/2 text-center px-2 leading-tight text-[#F5FA00]"
        style={{
          fontFamily: "'Impact', sans-serif",
          fontSize: `${(style.font_size / 1920) * 176 * 2.2}px`,
          WebkitTextStroke: '0.8px black'
        }}
      >
        {words.join(' ').toUpperCase()}
      </p>
      {!top && <div className="absolute inset-x-0 bottom-[20%]">{card}</div>}
    </div>
  )
}

/** Live example of how the burned-in captions will look (9:16 mock). */
function CaptionExample({ style }: { style: Required<CaptionStyle> }): JSX.Element {
  // Show one caption group of exactly words_per_caption words — the same
  // grouping the burn-in uses, so changing the setting changes the example.
  const sample = ['your', 'captions', 'look', 'like', 'this', 'onscreen']
  const words = sample.slice(0, Math.max(1, Math.min(6, style.words_per_caption)))
  const text = style.uppercase ? words.join(' ').toUpperCase() : words.join(' ')
  // Cycle the highlight through the words so the example shows the effect
  // moving, which is the whole point of it.
  const [hot, setHot] = useState(0)
  useEffect(() => {
    if (!style.highlight) return
    const id = setInterval(() => setHot((h) => (h + 1) % words.length), 550)
    return () => clearInterval(id)
  }, [style.highlight, words.length])
  const align =
    style.position === 'top' ? 'items-start' : style.position === 'middle' ? 'items-center' : 'items-end'
  return (
    <div
      className={`relative rounded-lg bg-gradient-to-br from-slate-700 via-slate-800 to-slate-900 aspect-[9/16] max-h-44 mx-auto w-auto flex ${align} justify-center overflow-hidden`}
      aria-label="Caption style example"
    >
      <p
        className="text-center px-2 py-4 leading-tight"
        style={{
          fontFamily: `'${style.font}', sans-serif`,
          color: style.color,
          // 84px at 1920 tall ≈ scale into this ~176px-tall mock
          fontSize: `${(style.font_size / 1920) * 176 * 2.2}px`,
          fontWeight: 700,
          WebkitTextStroke: '0.8px black',
          textShadow: '1px 1px 2px rgba(0,0,0,0.9)'
        }}
      >
        {style.highlight
          ? words.map((w, i) => (
              <span key={i} style={{ color: i === hot ? style.highlight_color : style.color }}>
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
  )
}

/** The post style: the clip's whole look, not only its captions. Kept
 *  apart from the caption controls because it applies with captions off
 *  too (the Highlights title card, and the titles written for it), so the
 *  queue screens keep it in reach while their caption controls are hidden.
 *  Not offered where captions are only translated (MultilingualExport):
 *  there it would change nothing. */
export function PostStyleControls({
  idPrefix,
  style,
  onChange,
  landscape = false,
  alsoLandscape = false
}: {
  idPrefix: string
  style: Required<CaptionStyle>
  onChange: <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) => void
  /** Every output is 16:9 (Longform with no 9:16 Shorts). The renderer
   *  draws a post style on vertical clips only, so none is offered. */
  landscape?: boolean
  /** Some outputs are 16:9 as well (Longform that also makes 9:16 Shorts).
   *  Those keep the standard look, so a Highlights pick says it is for the
   *  Shorts only. */
  alsoLandscape?: boolean
}): JSX.Element {
  const highlights = isHighlights(style, landscape)
  return (
    <div>
      <label htmlFor={`${idPrefix}-post`} className="label">
        Post style
      </label>
      <select
        id={`${idPrefix}-post`}
        className="input mt-1"
        // What the output gets: 16:9 is Standard whatever was picked.
        value={landscape ? 'default' : style.post_style}
        disabled={landscape}
        onChange={(e) => onChange('post_style', e.target.value as CaptionStyle['post_style'])}
      >
        <option value="default">Standard</option>
        <option value="highlights">Highlights (House of Highlights look)</option>
      </select>
      {landscape && (
        <p className="text-xs text-muted mt-1">
          Post styles are for 9:16 Shorts. This video makes only 16:9 output, which keeps the
          standard look.
        </p>
      )}
      {highlights && (
        <>
          <p className="text-xs text-muted mt-1">
            Each clip gets a title card like the big highlight pages: a yellow headline on black
            with a second line on yellow under it, and yellow captions. Add your own handle or
            logo with a watermark.
          </p>
          {alsoLandscape && (
            <p className="text-xs text-muted mt-1">
              Only the 9:16 Shorts get this look. The 16:9 clips keep the standard one, and the
              caption settings apply to them.
            </p>
          )}
          <label htmlFor={`${idPrefix}-card`} className="label mt-3 block">
            Title card
          </label>
          <select
            id={`${idPrefix}-card`}
            className="input mt-1"
            value={style.card_position}
            onChange={(e) =>
              onChange('card_position', e.target.value as CaptionStyle['card_position'])
            }
          >
            <option value="lower">Lower third</option>
            <option value="top">Top</option>
          </select>
          {style.card_position === 'top' && (
            <p className="text-xs text-muted mt-1">
              A clip with a hook title keeps its card in the lower third, clear of the hook.
            </p>
          )}
        </>
      )}
    </div>
  )
}

/** The caption style controls (colour, size, position, words, casing),
 *  shared between the Generate bar (style for all new clips) and the
 *  per-clip caption editor. */
export default function CaptionStyleControls({
  idPrefix,
  style,
  onChange,
  hideWordsPerCaption = false,
  hidePosition = false,
  hideSecondSpeaker = false,
  landscape = false
}: {
  idPrefix: string
  style: Required<CaptionStyle>
  onChange: <K extends keyof CaptionStyle>(key: K, value: CaptionStyle[K]) => void
  /** Translated subtitles inherit their grouping from the lines they
   *  replace, so regrouping does nothing there — hide it rather than offer
   *  a control that silently has no effect. */
  hideWordsPerCaption?: boolean
  /** Translated subtitles on a vertical Highlights clip always go in the
   *  middle, clear of its title card (multilingual/publish.py), so a
   *  position chosen for them would change nothing. */
  hidePosition?: boolean
  /** Translated subtitles are burned in one colour, whoever is talking. */
  hideSecondSpeaker?: boolean
  /** Some output is 16:9 (a 16:9 clip, or a job that makes 16:9 clips),
   *  where the post style draws nothing, so every caption control applies
   *  whatever the style says. */
  landscape?: boolean
}): JSX.Element {
  // A Highlights clip burns its captions in the style's own look, wherever
  // the post style was picked: the controls that look replaces would
  // change nothing, so they go.
  const highlights = isHighlights(style, landscape)
  return (
    <>
      <div className="grid grid-cols-2 gap-3">
        {!highlights && (
          <div className="col-span-2">
            <label htmlFor={`${idPrefix}-font`} className="label">
              Font
            </label>
            <select
              id={`${idPrefix}-font`}
              className="input mt-1"
              value={style.font}
              style={{ fontFamily: `'${style.font}', sans-serif` }}
              onChange={(e) => onChange('font', e.target.value)}
            >
              {CAPTION_FONTS.map((f) => (
                <option key={f} value={f} style={{ fontFamily: `'${f}', sans-serif` }}>
                  {f}
                </option>
              ))}
            </select>
          </div>
        )}
        {!highlights && (
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
        )}
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
        {!highlights && !hidePosition && (
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
        )}
        {!hideWordsPerCaption && (
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
        )}
      </div>
      {!highlights && (
        <label className="flex items-center gap-2 cursor-pointer text-sm">
          <input
            type="checkbox"
            className="size-4 accent-[#38BDF8]"
            checked={style.uppercase}
            onChange={(e) => onChange('uppercase', e.target.checked)}
          />
          UPPERCASE captions
        </label>
      )}

      {!highlights && (
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={style.highlight}
              onChange={(e) => onChange('highlight', e.target.checked)}
            />
            Highlight each word as it&apos;s said
          </label>
          {style.highlight && (
            <input
              type="color"
              aria-label="Highlight colour"
              className="h-7 w-10 rounded-md bg-raised cursor-pointer shrink-0"
              value={style.highlight_color}
              onChange={(e) => onChange('highlight_color', e.target.value.toUpperCase())}
            />
          )}
        </div>
      )}

      {!highlights && !hideSecondSpeaker && (
        <div className="flex items-center gap-3">
          <label
            className="flex items-center gap-2 cursor-pointer text-sm"
            title="When two people talk in a clip, the main speaker keeps the text colour and the other person's captions take this one. A clip with one voice looks the same as always."
          >
            <input
              type="checkbox"
              className="size-4 accent-[#38BDF8]"
              checked={style.second_speaker}
              onChange={(e) => onChange('second_speaker', e.target.checked)}
            />
            Second speaker in another colour
          </label>
          {style.second_speaker && (
            <input
              type="color"
              aria-label="Second speaker's colour"
              className="h-7 w-10 rounded-md bg-raised cursor-pointer shrink-0"
              value={style.second_speaker_color}
              onChange={(e) => onChange('second_speaker_color', e.target.value.toUpperCase())}
            />
          )}
        </div>
      )}

      <div>
        <p className="label mb-1">Example</p>
        {highlights ? <HighlightsExample style={style} /> : <CaptionExample style={style} />}
      </div>
    </>
  )
}
