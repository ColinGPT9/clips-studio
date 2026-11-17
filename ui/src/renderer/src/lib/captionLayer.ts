/** Captions and text as a layer on the new editor's viewer (step 9): what the
 *  viewer needs to find them in the engine's own ASS file, draw a box around
 *  them, move them while a drag is in progress, snap them, and tell when one
 *  sits under a phone app's buttons. Pure maths and text, in the canvas's own
 *  pixels, with no imports, so it can be tested on its own under Node
 *  (tests/test_ui_caption_layer.py).
 *
 *  The ASS file is the export's (GET /projects/{id}/captions.ass): captions
 *  are `Dialogue: 0`, text items and the hook `Dialogue: 1` (in the order
 *  compose.build_ass writes them), the text watermark `Dialogue: 2`. */

export type Box = [number, number, number, number]

/** The five named heights a caption or a text snaps to while it is dragged,
 *  as % of the frame's height: where its middle goes. Defined once, here;
 *  the guide drawn at one is labelled with its name. Bottom (75%) is where
 *  a bottom caption sits today; lower middle and higher middle are the
 *  places #238 asked for. */
export const SNAP_ROWS: readonly { id: string; label: string; y: number }[] = [
  { id: 'top', label: 'Top', y: 15 },
  { id: 'higher-middle', label: 'Higher middle', y: 33 },
  { id: 'middle', label: 'Middle', y: 50 },
  { id: 'lower-middle', label: 'Lower middle', y: 63 },
  { id: 'bottom', label: 'Bottom', y: 75 }
]

// ---- reading the ASS file ------------------------------------------------------------------

export interface AssStyle {
  name: string
  font: string
  size: number
  bold: boolean
  scaleX: number
  outline: number
  shadow: number
  align: number
  marginL: number
  marginR: number
  marginV: number
}

export interface AssEvent {
  /** Its line in the file. */
  line: number
  layer: number
  /** Seconds of the program. */
  start: number
  end: number
  style: string
  /** The Text field, override blocks and all. */
  text: string
}

export interface ParsedAss {
  lines: string[]
  width: number
  height: number
  styles: Record<string, AssStyle>
  events: AssEvent[]
}

/** "h:mm:ss.cc" in seconds. */
export function assSeconds(v: string): number {
  const m = /^\s*(\d+):(\d+):(\d+(?:\.\d+)?)\s*$/.exec(v)
  return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) : 0
}

const STYLE_FIELDS = ['Name', 'Fontname', 'Fontsize', 'Bold', 'ScaleX', 'Outline', 'Shadow', 'Alignment', 'MarginL', 'MarginR', 'MarginV']

/** The font families a script names (its styles' Fontname and every \fn in
 *  its events), in lower case: the live layer adds this PC's copy of each
 *  Windows caption font among them (9-D9). */
export function assFamilies(text: string): string[] {
  const out = new Set<string>()
  for (const s of Object.values(parseAss(text).styles)) if (s.font) out.add(s.font.toLowerCase())
  for (const m of text.matchAll(/\\fn([^\\}]+)/g)) out.add(m[1].trim().toLowerCase())
  return [...out]
}

export function parseAss(text: string): ParsedAss {
  const lines = text.split(/\r?\n/)
  let width = 0
  let height = 0
  let section = ''
  let styleFormat: string[] = []
  const styles: Record<string, AssStyle> = {}
  const events: AssEvent[] = []
  lines.forEach((raw, i) => {
    const line = raw.trim()
    if (line.startsWith('[')) {
      section = line.toLowerCase()
      return
    }
    if (line.startsWith('PlayResX:')) width = Number(line.slice(9)) || 0
    else if (line.startsWith('PlayResY:')) height = Number(line.slice(9)) || 0
    else if (section.includes('styles') && line.startsWith('Format:'))
      styleFormat = line.slice(7).split(',').map((s) => s.trim())
    else if (line.startsWith('Style:')) {
      const v = line.slice(6).split(',').map((s) => s.trim())
      const at = (name: string): string => {
        const k = (styleFormat.length ? styleFormat : DEFAULT_STYLE_FORMAT).indexOf(name)
        return k >= 0 ? (v[k] ?? '') : ''
      }
      const s: AssStyle = {
        name: at(STYLE_FIELDS[0]),
        font: at('Fontname'),
        size: Number(at('Fontsize')) || 0,
        bold: at('Bold') !== '0' && at('Bold') !== '',
        scaleX: Number(at('ScaleX')) || 100,
        outline: Number(at('Outline')) || 0,
        shadow: Number(at('Shadow')) || 0,
        align: Number(at('Alignment')) || 2,
        marginL: Number(at('MarginL')) || 0,
        marginR: Number(at('MarginR')) || 0,
        marginV: Number(at('MarginV')) || 0
      }
      styles[s.name] = s
    } else if (line.startsWith('Dialogue:')) {
      // Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
      const body = raw.slice(raw.indexOf('Dialogue:') + 9).replace(/^\s/, '')
      const parts: string[] = []
      let rest = body
      for (let k = 0; k < 9; k++) {
        const c = rest.indexOf(',')
        if (c < 0) return
        parts.push(rest.slice(0, c))
        rest = rest.slice(c + 1)
      }
      events.push({
        line: i,
        layer: Number(parts[0]) || 0,
        start: assSeconds(parts[1]),
        end: assSeconds(parts[2]),
        style: parts[3].trim(),
        text: rest
      })
    }
  })
  return { lines, width, height, styles, events }
}

const DEFAULT_STYLE_FORMAT = [
  'Name', 'Fontname', 'Fontsize', 'PrimaryColour', 'SecondaryColour', 'OutlineColour', 'BackColour', 'Bold',
  'Italic', 'Underline', 'StrikeOut', 'ScaleX', 'ScaleY', 'Spacing', 'Angle', 'BorderStyle', 'Outline', 'Shadow',
  'Alignment', 'MarginL', 'MarginR', 'MarginV', 'Encoding'
]

/** The event on screen at `t` seconds among `events` (libass: start <= t < end). */
export function eventAt(events: AssEvent[], t: number): AssEvent | null {
  return events.find((e) => e.start <= t && t < e.end) ?? null
}

/** The caption on screen at `t`, else the one nearest to it (to place a
 *  selected caption layer's box when no caption is showing). */
export function captionNear(p: ParsedAss, t: number): { event: AssEvent; showing: boolean } | null {
  const caps = p.events.filter((e) => e.layer === 0)
  const on = eventAt(caps, t)
  if (on) return { event: on, showing: true }
  let best: AssEvent | null = null
  let gap = Infinity
  for (const e of caps) {
    const d = t < e.start ? e.start - t : t - e.end
    if (d < gap) {
      gap = d
      best = e
    }
  }
  return best ? { event: best, showing: false } : null
}

/** The text item of each `Dialogue: 1` event, in compose.build_ass's order:
 *  the text tracks that aren't muted, each one's items by start, every item
 *  with words in it (a blank one draws nothing). */
export function textEventItems(
  tracks: { kind: string; mute?: boolean; items: { id: string; start_us: number; text?: { content?: string } | null }[] }[]
): string[] {
  const out: string[] = []
  for (const tr of tracks) {
    if (tr.kind !== 'text' || tr.mute) continue
    const items = [...tr.items].sort((a, b) => a.start_us - b.start_us)
    for (const it of items) {
      const words = String(it.text?.content ?? '').replace(/[\\{}]/g, '').trim()
      if (words) out.push(it.id)
    }
  }
  return out
}

/** The words an event shows: no override blocks, \N as a new line. */
export function eventWords(text: string): string {
  return text
    .replace(/\{[^}]*\}/g, '')
    .replace(/\\N/g, '\n')
    .replace(/\\[nh]/g, ' ')
}

// ---- where an event is drawn ----------------------------------------------------------------

/** Width of a line of text at a CSS pixel size, in the same pixels. */
export type Measure = (text: string, font: string, sizePx: number, bold: boolean) => number

/** libass sizes a font by its whole height (ascender and descender), so its
 *  letters are about this much smaller than a CSS font of the same size. */
const ASS_EM = 0.87
/** One line's height, for the size in the style. */
const LINE = 1.0

function lastNumber(tags: string, name: string): number | null {
  const re = new RegExp(`\\\\${name}(-?\\d+(?:\\.\\d+)?)`, 'g')
  let m: RegExpExecArray | null
  let v: number | null = null
  while ((m = re.exec(tags)) !== null) v = Number(m[1])
  return v
}

/** Where an event's anchor is: (x, y, alignment). \pos and \move's end
 *  point win over the style's place. */
export function eventAnchor(p: ParsedAss, e: AssEvent): { x: number; y: number; align: number; placed: boolean } {
  const st = p.styles[e.style]
  const first = /^\{([^}]*)\}/.exec(e.text)?.[1] ?? ''
  const an = lastNumber(first, 'an')
  const align = an !== null && an >= 1 && an <= 9 ? an : (st?.align ?? 2)
  const pos = /\\pos\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)/.exec(first)
  if (pos) return { x: Number(pos[1]), y: Number(pos[2]), align, placed: true }
  const mv = /\\move\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)/.exec(first)
  if (mv) return { x: Number(mv[3]), y: Number(mv[4]), align, placed: true }
  const W = p.width
  const H = p.height
  const mL = st?.marginL ?? 0
  const mR = st?.marginR ?? 0
  const mV = st?.marginV ?? 0
  const col = (align - 1) % 3
  const row = Math.floor((align - 1) / 3)
  const x = col === 0 ? mL : col === 2 ? W - mR : (mL + W - mR) / 2
  const y = row === 0 ? H - mV : row === 2 ? mV : H / 2
  return { x, y, align, placed: false }
}

/** The box an event takes on the frame (canvas px): measured with `measure`
 *  in its font and size, wrapped at the style's margins as libass wraps
 *  (lines of about equal width), with its outline. An estimate for the
 *  selection box and the outline; the engine's still is the truth. */
export function eventBox(p: ParsedAss, e: AssEvent, measure: Measure): Box {
  const st = p.styles[e.style]
  const first = /^\{([^}]*)\}/.exec(e.text)?.[1] ?? ''
  const size = lastNumber(first, 'fs') ?? st?.size ?? 48
  const scaleX = (lastNumber(first, 'fscx') ?? st?.scaleX ?? 100) / 100
  const font = st?.font ?? 'Arial'
  const bold = st?.bold ?? true
  const maxW = Math.max(40, p.width - (st?.marginL ?? 0) - (st?.marginR ?? 0))
  let width = 0
  let rows = 0
  for (const part of eventWords(e.text).split('\n')) {
    const w = measure(part.trim(), font, size * ASS_EM, bold) * scaleX
    const n = Math.max(1, Math.ceil(w / maxW))
    width = Math.max(width, Math.min(maxW, w / n))
    rows += n
  }
  const pad = (st?.outline ?? 0) + (st?.shadow ?? 0)
  const w = width + 2 * pad
  const h = rows * size * LINE + 2 * pad
  const a = eventAnchor(p, e)
  const col = (a.align - 1) % 3
  const row = Math.floor((a.align - 1) / 3)
  const x = col === 0 ? a.x : col === 2 ? a.x - w : a.x - w / 2
  const y = row === 0 ? a.y - h : row === 2 ? a.y : a.y - h / 2
  return [x, y, w, h]
}

// ---- the live picture while a layer moves --------------------------------------------------

/** The script with some events centred on `at` (canvas px) and their size
 *  scaled by `fs`, the way the engine draws a dragged caption (\an5\pos,
 *  slide's \move rising into the point): what JASSUB draws while a caption
 *  or a text is dragged or resized, before the engine has the change. `at`
 *  null keeps each event where it is (a resize of a caption placed by name).
 *  `hold` moves an event's centre the way the engine keeps it inside the
 *  frame (captions: heldCentre with the event's own size, 9-D2). */
export function placeEvents(
  p: ParsedAss,
  which: (e: AssEvent, index: number) => boolean,
  at: [number, number] | null,
  fs = 1,
  hold?: (e: AssEvent, at: [number, number]) => [number, number]
): string {
  const lines = [...p.lines]
  p.events.forEach((e, index) => {
    if (!which(e, index)) return
    const size = p.styles[e.style]?.size ?? 0
    let body = e.text
    const sized = fs !== 1 && size > 0 ? `\\fs${Math.max(1, Math.round(size * fs))}` : ''
    if (at === null) {
      if (!sized) return
      body = body.startsWith('{') ? `{${sized}${body.slice(1)}` : `{${sized}}${body}`
    } else {
      const [hx, hy] = hold ? hold(e, at) : at
      const x = Math.round(hx)
      const y = Math.round(hy)
      let moved = false
      body = body.replace(/\\move\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)/g, (_m, x1, y1, x2, y2) => {
        moved = true
        return `\\move(${x + Math.round(Number(x1) - Number(x2))},${y + Math.round(Number(y1) - Number(y2))},${x},${y}`
      })
      body = body.replace(/\\an\d/g, '').replace(/\\pos\([^)]*\)/g, '').replace(/\{\}/g, '')
      const tag = moved ? `\\an5${sized}` : `\\an5\\pos(${x},${y})${sized}`
      body = body.startsWith('{') ? `{${tag}${body.slice(1)}` : `{${tag}}${body}`
    }
    const raw = lines[e.line]
    const cut = nthComma(raw, 9)
    if (cut < 0) return
    lines[e.line] = raw.slice(0, cut + 1) + body
  })
  return lines.join('\n')
}

/** The script without some events: what JASSUB draws while one is being
 *  typed into where it stands (the box shows the words instead). */
export function withoutEvents(p: ParsedAss, which: (e: AssEvent, index: number) => boolean): string {
  const drop = new Set(p.events.filter((e, i) => which(e, i)).map((e) => e.line))
  return p.lines.filter((_l, i) => !drop.has(i)).join('\n')
}

function nthComma(s: string, n: number): number {
  let at = -1
  for (let k = 0; k < n; k++) {
    at = s.indexOf(',', at + 1)
    if (at < 0) return -1
  }
  return at
}

// ---- the phone apps' screens ---------------------------------------------------------------

/** An app's safe zone on a 1080x1920 Short (gaming/layouts.json). */
export interface AppZone {
  top: number
  bottom: number
  left: number
  right: number
}

/** A shape the app draws (components/PlatformOverlay.tsx), on 1080x1920. */
export interface AppShape {
  k: string
  x: number
  y: number
  s?: number
  r?: number
  w?: number
  h?: number
  labelY?: number
}

export type AppAreaKind = 'buttons' | 'bottom' | 'top'

/** What an app draws over a 9:16 video, as boxes on a W x H canvas: its top
 *  bar, its column of buttons down the right (as tall as the buttons
 *  drawn), and the name, caption and sound at the bottom. */
export function appAreas(zone: AppZone, shapes: AppShape[], W: number, H: number): { kind: AppAreaKind; box: Box }[] {
  const k = W / 1080
  const kh = H / 1920
  const colLeft = 1080 - zone.right
  let top = Infinity
  let bottom = -Infinity
  for (const s of shapes) {
    if (s.x < colLeft || s.y <= zone.top) continue
    const half = s.s !== undefined ? s.s / 2 : s.r !== undefined ? s.r : (s.h ?? 40) / 2
    top = Math.min(top, s.y - half)
    bottom = Math.max(bottom, s.y + half, s.labelY !== undefined ? s.labelY + 10 : -Infinity)
  }
  const out: { kind: AppAreaKind; box: Box }[] = [{ kind: 'top', box: [0, 0, W, zone.top * kh] }]
  if (top < bottom) out.push({ kind: 'buttons', box: [colLeft * k, top * kh, zone.right * k, (bottom - top) * kh] })
  out.push({ kind: 'bottom', box: [0, (1920 - zone.bottom) * kh, colLeft * k, zone.bottom * kh] })
  return out
}

/** The app areas a box runs under (by more than a sliver). */
export function underApp(box: Box, areas: { kind: AppAreaKind; box: Box }[], H: number): AppAreaKind[] {
  const slack = (4 * H) / 1920
  const [x, y, w, h] = box
  return areas
    .filter(({ box: [ax, ay, aw, ah] }) => x + w - slack > ax && ax + aw - slack > x && y + h - slack > ay && ay + ah - slack > y)
    .map((a) => a.kind)
}

// ---- what a dragged layer snaps to ---------------------------------------------------------

/** The lines a caption or a text snaps to on a W x H frame: the frame's
 *  edges and middle (the centre lines), the five named rows, the chosen
 *  app's safe lines and the edges of what it draws, and the other layers'
 *  edges and middles. */
export function layerTargets(
  W: number,
  H: number,
  zone: AppZone | null,
  areas: { kind: AppAreaKind; box: Box }[],
  others: Box[]
): { xs: number[]; ys: number[] } {
  const xs = [0, W / 2, W]
  const ys = [0, H / 2, H, ...SNAP_ROWS.map((r) => (r.y * H) / 100)]
  if (zone) {
    const k = W / 1080
    const kh = H / 1920
    xs.push(zone.left * k, W - zone.right * k)
    ys.push(zone.top * kh, H - zone.bottom * kh)
  }
  for (const { box } of areas) {
    xs.push(box[0], box[0] + box[2])
    ys.push(box[1], box[1] + box[3])
  }
  for (const [x, y, w, h] of others) {
    xs.push(x, x + w / 2, x + w)
    ys.push(y, y + h / 2, y + h)
  }
  const uniq = (v: number[]): number[] => [...new Set(v.map((n) => Math.round(n * 100) / 100))].filter((n) => n >= 0)
  return { xs: uniq(xs).filter((n) => n <= W), ys: uniq(ys).filter((n) => n <= H) }
}

/** The named row a guide line is on, or null. */
export function rowAt(y: number, H: number): (typeof SNAP_ROWS)[number] | null {
  return SNAP_ROWS.find((r) => Math.abs((r.y * H) / 100 - y) < 0.5) ?? null
}

/** A box's middle as % of the frame, to two decimals and inside 0..100:
 *  the x and y the engine takes. */
export function centrePercent(box: Box, W: number, H: number): { x: number; y: number } {
  const pc = (v: number, of: number): number => Math.min(100, Math.max(0, Math.round((v / of) * 10000) / 100))
  return { x: pc(box[0] + box[2] / 2, W), y: pc(box[1] + box[3] / 2, H) }
}

// ---- where the engine keeps a dragged caption (9-D2) ---------------------------------------

/** The phone apps' bars a caption is kept clear of when a safe zone is
 *  chosen, on the 1080x1920 frame: video/captions.py SAFE_ZONES (the test
 *  checks they are the same). */
export const CAPTION_SAFE_ZONES: Record<string, { top: number; bottom: number; right: number }> = {
  tiktok: { top: 180, bottom: 420, right: 190 },
  reels: { top: 245, bottom: 400, right: 205 },
  shorts: { top: 250, bottom: 350, right: 190 }
}
/** video/captions.py _SAFE_CLEARANCE, _INK_SPREAD and _SLIDE_PX. */
const SAFE_CLEARANCE = 20
const INK_SPREAD = 9
const SLIDE_PX = 40

/** The area a dragged caption is kept inside, (left, top, right, bottom) in
 *  canvas px: video/captions.py _place_bounds. Across, 60 px from each edge
 *  (with a safe zone on a portrait frame, the app's buttons on the right);
 *  down, the frame, or the space between the app's bars. */
export function captionBounds(style: Record<string, unknown>, W: number, H: number): [number, number, number, number] {
  const name = style.safe_zone
  const zone = typeof name === 'string' && H > W ? CAPTION_SAFE_ZONES[name] : undefined
  let right = 60
  let top = 0
  let bottom = H
  if (zone) {
    right = Math.round(((Math.max(60, zone.right) + INK_SPREAD) * W) / 1080)
    const s = H / 1920
    const rise = style.animation === 'slide' ? SLIDE_PX : 0
    top = Math.round((zone.top + SAFE_CLEARANCE) * s)
    bottom = H - Math.round((zone.bottom + SAFE_CLEARANCE + rise) * s)
  }
  return [60, top, W - right, bottom]
}

/** A caption's centre `at` moved only as far as it takes to keep a box of
 *  `size` (w, h) inside `bounds`: video/captions.py _placed_at and _held.
 *  The engine places every caption this way with its own measured size, so
 *  the live picture while dragging shows the words where they will land. */
export function heldCentre(
  at: [number, number],
  size: [number, number],
  bounds: [number, number, number, number]
): [number, number] {
  const hold = (v: number, lo: number, hi: number): number => {
    const a = Math.ceil(lo)
    const b = Math.floor(hi)
    if (a > b) return Math.round((a + b) / 2)
    return Math.min(Math.max(Math.round(v), a), b)
  }
  const [left, top, right, bottom] = bounds
  return [hold(at[0], left + size[0] / 2, right - size[0] / 2), hold(at[1], top + size[1] / 2, bottom - size[1] / 2)]
}

/** The new size when a corner handle is pulled: the box grows from its
 *  middle, by how much further from the middle the pointer is than where it
 *  started, held to the range the engine takes. */
export function resized(size: number, startDist: number, dist: number, min: number, max: number): number {
  if (startDist <= 0) return size
  return Math.min(max, Math.max(min, Math.round((size * dist) / startDist)))
}
