/** The timeline's arithmetic: which tracks show in which order, where an item
 *  sits in pixels at a zoom, snapping, where a dragged main-track item lands,
 *  and how far a trim may go. Pure functions over the step 5 document, so the
 *  timeline component only draws and sends operations. Times are whole
 *  microseconds; a zoom is pixels per second. */
import { frameAt, frameUs, programLength } from './editorProgram'
import type { Fps, Item, MediaEntry, ProjectDoc, Track } from './editorTypes'

/** The zoom's range: a 4-hour video fits in about 900 px at the least, and
 *  one frame at 60 fps is 10 px at the most. */
export const MIN_PPS = 0.05
export const MAX_PPS = 600
export const ZOOM_STEP = 1.25

export function clampPps(pps: number): number {
  return Math.min(MAX_PPS, Math.max(MIN_PPS, pps))
}

export const usToPx = (us: number, pps: number): number => (us * pps) / 1e6
export const pxToUs = (px: number, pps: number): number => Math.round((px * 1e6) / pps)

export function itemEnd(it: Item): number {
  return it.start_us + it.duration_us
}

/** The timeline's length: the last end on any track (V1 sets the video's
 *  length; an overlay or a sound may run past it, and is shown there). */
export function timelineLength(doc: ProjectDoc): number {
  let end = programLength(doc)
  for (const tr of doc.tracks) for (const it of tr.items) end = Math.max(end, itemEnd(it))
  return end
}

/** The zoom that shows the whole timeline in `widthPx`. */
export function fitPps(doc: ProjectDoc, widthPx: number): number {
  const length = timelineLength(doc)
  if (length <= 0 || widthPx <= 0) return 50
  return clampPps((widthPx * 0.96 * 1e6) / length)
}

export type RowKind = 'main' | 'overlay' | 'text' | 'audio'

export interface Row {
  track: Track
  kind: RowKind
  height: number
}

export const ROW_HEIGHT: Record<RowKind, number> = { main: 64, overlay: 44, text: 28, audio: 40 }

/** The rows top to bottom, as in CapCut: text above the overlays, the higher
 *  overlay above the lower, the main track, then the sounds (A2 and up; A1 is
 *  V1's own sound, drawn on V1's items). Step 9's caption lane goes between
 *  the main track and the sounds. */
export function rowsOf(doc: ProjectDoc): Row[] {
  const video = doc.tracks.filter((t) => t.kind === 'video')
  const text = doc.tracks.filter((t) => t.kind === 'text')
  const audio = doc.tracks.filter((t) => t.kind === 'audio' && t.id !== 'A1')
  const rows: Row[] = []
  for (const tr of [...text].reverse()) rows.push({ track: tr, kind: 'text', height: ROW_HEIGHT.text })
  for (const tr of [...video].reverse())
    rows.push(
      tr.id === 'V1'
        ? { track: tr, kind: 'main', height: ROW_HEIGHT.main }
        : { track: tr, kind: 'overlay', height: ROW_HEIGHT.overlay }
    )
  for (const tr of audio) rows.push({ track: tr, kind: 'audio', height: ROW_HEIGHT.audio })
  return rows
}

/** Items of a track that reach into [fromUs, toUs). */
export function itemsIn(track: Track, fromUs: number, toUs: number): Item[] {
  return track.items.filter((it) => it.start_us < toUs && itemEnd(it) > fromUs)
}

/** The times an edge snaps to: the playhead, every item's edges on every
 *  track (but the moving item's own), and the video's start and end. */
export function snapPoints(doc: ProjectDoc, playheadUs: number, skip: Set<string>): number[] {
  const out = new Set<number>([0, programLength(doc), playheadUs])
  for (const tr of doc.tracks)
    for (const it of tr.items) {
      if (skip.has(it.id)) continue
      out.add(it.start_us)
      out.add(itemEnd(it))
    }
  return [...out].sort((a, b) => a - b)
}

/** The nearest point within `withinUs` of t, or null. */
export function snapTo(t: number, points: number[], withinUs: number): number | null {
  let best: number | null = null
  let bestD = withinUs
  for (const p of points) {
    const d = Math.abs(p - t)
    if (d <= bestD) {
      best = p
      bestD = d
    }
  }
  return best
}

/** Where a main-track item dragged to time t goes among the others (step 5's
 *  move_item index on V1: its place once it is taken out). */
export function dropIndex(items: Item[], movingId: string, t: number): number {
  let index = 0
  for (const it of items) {
    if (it.id === movingId) continue
    if (it.start_us + it.duration_us / 2 < t) index++
  }
  return index
}

export type ItemShape = 'clip' | 'picture' | 'text' | 'sound' | 'loop'

/** What an item is, for how it trims: a clip or a sound by its source times,
 *  text, a picture or looped music by its start and end in the video. */
export function shapeOf(item: Item, track: Track, media: MediaEntry | undefined): ItemShape {
  if (track.kind === 'text' || item.text) return 'text'
  if (track.kind === 'audio') return item.loop ? 'loop' : 'sound'
  if (media?.kind === 'image') return 'picture'
  return 'clip'
}

/** A trim of one edge by `dtUs` of video time, held to what the source and
 *  the step 5 rules allow (at least one frame; never past the media's ends).
 *  Returns the operation's fields and the item's new place, for the ghost. */
export function trimEdge(
  item: Item,
  shape: ItemShape,
  edge: 'left' | 'right',
  dtUs: number,
  mediaDurationUs: number,
  fps: Fps
): { fields: Record<string, number>; start: number; end: number } {
  const one = Math.max(1, frameUs(1, fps))
  const speed = item.speed || 1
  const start = item.start_us
  const end = itemEnd(item)
  if (shape === 'clip' || shape === 'sound') {
    const maxOut = mediaDurationUs > 0 ? mediaDurationUs : Number.MAX_SAFE_INTEGER
    if (edge === 'left') {
      const inUs = Math.round(Math.min(item.source_out_us - one * speed, Math.max(0, item.source_in_us + dtUs * speed)))
      const moved = (inUs - item.source_in_us) / speed
      return { fields: { source_in_us: inUs }, start: start + moved, end }
    }
    const outUs = Math.round(Math.max(item.source_in_us + one * speed, Math.min(maxOut, item.source_out_us + dtUs * speed)))
    return { fields: { source_out_us: outUs }, start, end: start + (outUs - item.source_in_us) / speed }
  }
  if (edge === 'left') {
    const s = Math.round(Math.min(end - one, Math.max(0, start + dtUs)))
    return { fields: { start_us: s }, start: s, end }
  }
  const e = Math.round(Math.max(start + one, end + dtUs))
  return { fields: { end_us: e }, start, end: e }
}

/** Seconds and frames typed into a field, in the form formatTime shows
 *  ("1:23.12" is 1 minute, 23 seconds and 12 frames; "1:02:03.12" past an
 *  hour), or plain seconds ("83.4"). null when it isn't a time. */
export function parseTime(text: string, fps: Fps): number | null {
  const s = text.trim()
  if (!s) return null
  if (!s.includes(':')) {
    const v = Number(s)
    return Number.isFinite(v) && v >= 0 ? Math.round(v * 1e6) : null
  }
  const m = /^(\d+):(\d{1,2})(?::(\d{1,2}))?(?:\.(\d{1,3}))?$/.exec(s)
  if (!m) return null
  const [h, mi, se] = m[3] !== undefined ? [Number(m[1]), Number(m[2]), Number(m[3])] : [0, Number(m[1]), Number(m[2])]
  if (mi > 59 && m[3] !== undefined) return null
  if (se > 59) return null
  const whole = (h * 3600 + mi * 60 + se) * 1e6
  const frames = m[4] !== undefined ? Number(m[4]) : 0
  const first = frameAt(whole, fps) + (frameUs(frameAt(whole, fps), fps) < whole ? 1 : 0)
  return frameUs(first + frames, fps)
}

/** The ruler's spacing: the smallest of these (in microseconds) whose
 *  labelled ticks stay at least `minPx` apart. */
const RULER_STEPS = [
  100_000, 200_000, 500_000, 1e6, 2e6, 5e6, 10e6, 15e6, 30e6, 60e6, 120e6, 300e6, 600e6, 900e6, 1800e6, 3600e6
]
export function rulerStep(pps: number, minPx = 80): number {
  for (const s of RULER_STEPS) if (usToPx(s, pps) >= minPx) return s
  return RULER_STEPS[RULER_STEPS.length - 1]
}

/** "1:05" or "1:02:03" for a ruler label; tenths below a second. */
export function rulerLabel(us: number, step: number): string {
  const total = us / 1e6
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const ss = step < 1e6 ? s.toFixed(1).padStart(4, '0') : String(Math.floor(s + 1e-9)).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}
