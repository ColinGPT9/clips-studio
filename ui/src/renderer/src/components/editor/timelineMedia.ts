/** The timeline's pictures and waveforms (step 6's thumbnails and peaks),
 *  fetched once per media and drawn on the timeline's canvases.
 *
 *  - Thumbnails: the index, then each sheet (a JPEG of 10 x 10 tiles) as it
 *    is first needed, decoded to an ImageBitmap (the page's rules let no
 *    blob: or data: address into an <img>, and the dev server none of the
 *    engine's pictures). The newest 48 sheets are kept.
 *  - Waveforms: the whole media at 64 bins a second with every coarser level
 *    (1.8 MB for 2 hours), read with a DataView as step 6 describes (CKPK).
 *
 *  Whatever arrives asks the timeline to draw again (`onChange`). A product
 *  still being made (202) is asked for again a few seconds later. */
import { editorApi } from '../../lib/editorApi'
import type { ThumbIndex } from '../../lib/editorTypes'
import { isInfo, type MediaInfos } from './useMediaInfos'

export interface PeakLevel {
  perSecond: number
  first: number
  count: number
  bins: Int8Array
}

/** Step 6's CKPK bytes: header, then each level finest first. */
export function parsePeaks(buffer: ArrayBuffer): PeakLevel[] {
  const view = new DataView(buffer)
  if (buffer.byteLength < 20 || view.getUint32(0, false) !== 0x434b504b) return []
  const levels = view.getUint16(6, true)
  const out: PeakLevel[] = []
  let offset = 20 + 12 * levels
  for (let i = 0; i < levels; i++) {
    const at = 20 + 12 * i
    const perSecond = view.getUint32(at, true)
    const count = view.getUint32(at + 4, true)
    const first = view.getUint32(at + 8, true)
    if (offset + 2 * count > buffer.byteLength) break
    out.push({ perSecond, first, count, bins: new Int8Array(buffer, offset, 2 * count) })
    offset += 2 * count
  }
  return out
}

const SHEETS_KEPT = 48
const RETRY_MS = 4000

type Slot<T> = { state: 'loading' } | { state: 'ready'; value: T } | { state: 'none' } | { state: 'wait'; until: number }

export class TimelineMedia {
  private thumbs = new Map<string, Slot<ThumbIndex>>()
  private peaks = new Map<string, Slot<PeakLevel[]>>()
  private sheets = new Map<string, ImageBitmap | 'loading' | 'failed'>()
  private ask = new AbortController()
  private infos: MediaInfos = {}
  constructor(private onChange: () => void) {}

  setInfos(infos: MediaInfos): void {
    this.infos = infos
  }

  destroy(): void {
    this.ask.abort()
    for (const b of this.sheets.values()) if (typeof b !== 'string') b.close()
    this.sheets.clear()
  }

  private later(): void {
    window.setTimeout(() => this.onChange(), RETRY_MS + 50)
  }

  thumbIndex(mediaId: string): ThumbIndex | null {
    const slot = this.thumbs.get(mediaId)
    if (slot?.state === 'ready') return slot.value
    if (slot?.state === 'loading' || slot?.state === 'none') return null
    if (slot?.state === 'wait' && Date.now() < slot.until) return null
    const info = this.infos[mediaId]
    if (!isInfo(info) || info.kind === 'audio' || !info.urls.thumbs) return null
    this.thumbs.set(mediaId, { state: 'loading' })
    editorApi
      .thumbs(info.urls.thumbs, this.ask.signal)
      .then((index) => {
        if (index) this.thumbs.set(mediaId, { state: 'ready', value: index })
        else {
          this.thumbs.set(mediaId, { state: 'wait', until: Date.now() + RETRY_MS })
          this.later()
        }
        this.onChange()
      })
      .catch(() => this.thumbs.set(mediaId, { state: 'none' }))
    return null
  }

  sheet(url: string): ImageBitmap | null {
    const got = this.sheets.get(url)
    if (got && typeof got !== 'string') {
      // The newest used last, so the oldest goes first.
      this.sheets.delete(url)
      this.sheets.set(url, got)
      return got
    }
    if (got) return null
    this.sheets.set(url, 'loading')
    editorApi
      .bitmap(url, this.ask.signal)
      .then((bitmap) => {
        this.sheets.set(url, bitmap)
        while (this.sheets.size > SHEETS_KEPT) {
          const [oldest, value] = this.sheets.entries().next().value as [string, ImageBitmap | string]
          if (typeof value !== 'string') value.close()
          this.sheets.delete(oldest)
        }
        this.onChange()
      })
      .catch(() => this.sheets.set(url, 'failed'))
    return null
  }

  peakLevels(mediaId: string): PeakLevel[] | null {
    const slot = this.peaks.get(mediaId)
    if (slot?.state === 'ready') return slot.value
    if (slot?.state === 'loading' || slot?.state === 'none') return null
    if (slot?.state === 'wait' && Date.now() < slot.until) return null
    const info = this.infos[mediaId]
    if (!isInfo(info) || !info.has_audio || !info.urls.peaks) return null
    this.peaks.set(mediaId, { state: 'loading' })
    editorApi
      .peaks(info.urls.peaks, 64, this.ask.signal)
      .then((answer) => {
        if (answer === 'none') this.peaks.set(mediaId, { state: 'none' })
        else if (answer === null) {
          this.peaks.set(mediaId, { state: 'wait', until: Date.now() + RETRY_MS })
          this.later()
        } else this.peaks.set(mediaId, { state: 'ready', value: parsePeaks(answer) })
        this.onChange()
      })
      .catch(() => this.peaks.set(mediaId, { state: 'none' }))
    return null
  }
}

/** The tile nearest before source time t (counted from the source's start). */
function tileAt(index: ThumbIndex, tUs: number): number {
  const times = index.times_us
  let lo = 0
  let hi = times.length - 1
  if (hi < 0) return 0
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (times[mid] <= tUs) lo = mid
    else hi = mid - 1
  }
  return lo
}

/** Thumbnails along an item: a tile every tile-width, each showing the source
 *  at that point. Only the part between clipL and clipR is drawn. */
export function drawThumbs(
  ctx: CanvasRenderingContext2D,
  media: TimelineMedia,
  index: ThumbIndex,
  sourceInUs: number,
  sourceOutUs: number,
  x: number,
  w: number,
  y: number,
  h: number,
  clipL: number,
  clipR: number
): void {
  const tw = Math.max(8, (h * index.tile.width) / Math.max(1, index.tile.height))
  const from = Math.max(x, clipL)
  const to = Math.min(x + w, clipR)
  if (to <= from) return
  ctx.save()
  ctx.beginPath()
  ctx.rect(from, y, to - from, h)
  ctx.clip()
  const firstK = Math.floor((from - x) / tw)
  const lastK = Math.floor((to - x) / tw)
  const span = sourceOutUs - sourceInUs
  for (let k = firstK; k <= lastK; k++) {
    const px = x + k * tw
    const t = sourceInUs + (Math.max(0, k * tw) / Math.max(1, w)) * span
    const i = Math.min(index.count - 1, tileAt(index, t))
    const sheet = index.sheets[Math.floor(i / 100)]
    if (!sheet) continue
    const bitmap = media.sheet(sheet.url)
    if (!bitmap) continue
    const local = i % 100
    const col = local % 10
    const row = Math.floor(local / 10)
    ctx.drawImage(bitmap, col * index.tile.width, row * index.tile.height, index.tile.width, index.tile.height, px, y, tw, h)
  }
  ctx.restore()
}

/** A waveform of source time [sourceIn, sourceOut) drawn into x..x+w,
 *  centred in y..y+h, from the coarsest level that still has a bin for
 *  each pixel. */
export function drawWave(
  ctx: CanvasRenderingContext2D,
  levels: PeakLevel[],
  sourceInUs: number,
  sourceOutUs: number,
  x: number,
  w: number,
  y: number,
  h: number,
  clipL: number,
  clipR: number,
  color: string
): void {
  if (levels.length === 0 || w <= 0) return
  const seconds = (sourceOutUs - sourceInUs) / 1e6
  let level = levels[0]
  for (const l of levels) if (l.perSecond * seconds >= w) level = l
  const from = Math.max(Math.floor(x), Math.floor(clipL))
  const to = Math.min(Math.ceil(x + w), Math.ceil(clipR))
  if (to <= from) return
  const mid = y + h / 2
  const scale = h / 2 / 128
  const binsPerPx = (level.perSecond * seconds) / w
  const startBin = (sourceInUs / 1e6) * level.perSecond - level.first
  ctx.fillStyle = color
  for (let px = from; px < to; px++) {
    const a = Math.floor(startBin + (px - x) * binsPerPx)
    const b = Math.max(a + 1, Math.floor(startBin + (px + 1 - x) * binsPerPx))
    let lo = 0
    let hi = 0
    for (let i = Math.max(0, a); i < Math.min(level.count, b); i++) {
      const mn = level.bins[2 * i]
      const mx = level.bins[2 * i + 1]
      if (mn < lo) lo = mn
      if (mx > hi) hi = mx
    }
    const top = mid - hi * scale
    const height = Math.max(1, (hi - lo) * scale)
    ctx.fillRect(px, top, 1, height)
  }
}
