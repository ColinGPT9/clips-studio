/** The program's time, for the viewer and the timeline: which item plays at a
 *  moment, where in its source, and the canvas's frame grid. Pure functions
 *  over the step 5 document; times are whole microseconds. */
import type { Fps, Item, ProjectDoc, Track } from './editorTypes'

/** Where frame n starts, to the nearest microsecond: exactly step 5's
 *  model.frame_us, (2 * n * 1e6 * den + num) // (2 * num), worked out from the
 *  frame number each time so it never drifts. The paused viewer asks for the
 *  still at this time and split rounds to it, so the two must agree to the
 *  microsecond (8-D7). Exact in whole numbers; past 2^53 in BigInt. */
export function frameUs(n: number, fps: Fps): number {
  const top = 2 * n * 1e6 * fps.den + fps.num
  if (Number.isSafeInteger(top)) return Math.floor(top / (2 * fps.num))
  const big = (BigInt(2) * BigInt(n) * BigInt(1e6) * BigInt(fps.den) + BigInt(fps.num)) / (BigInt(2) * BigInt(fps.num))
  return Number(big)
}

/** The frame that holds time t (the last frame starting at or before it). */
export function frameAt(tUs: number, fps: Fps): number {
  let n = Math.floor((tUs * fps.num) / (1e6 * fps.den))
  // Rounding of the division can put n one off near an edge.
  while (n > 0 && frameUs(n, fps) > tUs) n--
  while (frameUs(n + 1, fps) <= tUs) n++
  return Math.max(0, n)
}

/** One frame's length near t, in microseconds. */
export function frameLength(tUs: number, fps: Fps): number {
  const n = frameAt(tUs, fps)
  return frameUs(n + 1, fps) - frameUs(n, fps)
}

export function v1(doc: ProjectDoc): Track | undefined {
  return doc.tracks.find((t) => t.id === 'V1')
}

/** V1's items in program order. */
export function v1Items(doc: ProjectDoc): Item[] {
  return [...(v1(doc)?.items ?? [])].sort((a, b) => a.start_us - b.start_us)
}

/** The program's length: V1's end. */
export function programLength(doc: ProjectDoc): number {
  const items = v1Items(doc)
  const last = items[items.length - 1]
  return last ? last.start_us + last.duration_us : 0
}

/** The index of the item playing at t in a list sorted by start, or -1. */
export function itemIndexAt(items: Item[], tUs: number): number {
  let lo = 0
  let hi = items.length - 1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const it = items[mid]
    if (tUs < it.start_us) hi = mid - 1
    else if (tUs >= it.start_us + it.duration_us) lo = mid + 1
    else return mid
  }
  return -1
}

/** Where in its source an item is at program time t (microseconds). */
export function sourceAt(item: Item, tUs: number): number {
  const into = Math.min(Math.max(0, tUs - item.start_us), item.duration_us)
  return Math.min(item.source_out_us, item.source_in_us + into * (item.speed || 1))
}

/** The program time of source time s in an item (inverse of sourceAt). */
export function programAt(item: Item, sourceUs: number): number {
  return item.start_us + (sourceUs - item.source_in_us) / (item.speed || 1)
}

/** Whether `b` carries straight on from `a` in the same file at the same
 *  speed (a split that was never moved): one player plays both, no cut. */
export function continues(a: Item, b: Item): boolean {
  return (
    a.media_id === b.media_id &&
    a.speed === b.speed &&
    Math.abs(a.source_out_us - b.source_in_us) < 1000 &&
    a.start_us + a.duration_us === b.start_us
  )
}

/** The overlay tracks (V2 and up) in stacking order, the lower first. */
export function overlayTracks(doc: ProjectDoc): Track[] {
  return doc.tracks.filter((t) => t.kind === 'video' && t.id !== 'V1')
}

/** An AI item, framed the way an AI clip is (the cropper, tracking, a Gaming
 *  layout), as step 7's compose.is_ai_item tells it: framing {crop} or
 *  {gaming}; or no framing at all on the clip's own video in a project made
 *  from a clip (a clip saved without "crop" is tracked, its default). */
export function isAiItem(doc: ProjectDoc, item: Item): boolean {
  const f = (item.framing ?? {}) as Record<string, unknown>
  if ('crop' in f || 'gaming' in f) return true
  if ('fit' in f || 'align' in f || 'pad' in f) return false
  const ctx = (doc.context ?? {}) as { origin?: string; media_id?: string }
  return ctx.origin === 'clip' && item.media_id === ctx.media_id
}

/** 9:16 within a pixel or two: the phone apps' overlays are drawn for it. */
export function isVertical916(width: number, height: number): boolean {
  return height > 0 && Math.abs(width / height - 9 / 16) < 0.01
}

/** "1:23.12" (minutes, seconds and frames) or "1:02:03.12" past an hour. */
export function formatTime(tUs: number, fps: Fps): string {
  const n = frameAt(Math.max(0, tUs), fps)
  const perSecond = fps.num / fps.den
  const wholeSeconds = Math.floor(frameUs(n, fps) / 1e6 + 1e-9)
  const frame = Math.max(0, n - Math.ceil((wholeSeconds * perSecond) - 1e-9))
  const h = Math.floor(wholeSeconds / 3600)
  const m = Math.floor((wholeSeconds % 3600) / 60)
  const s = wholeSeconds % 60
  const ff = String(frame).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}.${ff}` : `${m}:${ss}.${ff}`
}

/** "1:23" for a length (lists, the media bin). */
export function formatLength(us: number): string {
  const total = Math.round(us / 1e6)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}
