/** Plays the program (V1) with two `<video>` elements.
 *
 *  One element plays the item on screen while the other waits, hidden and
 *  silent, a little before the first frame of the next cut. Shortly before the
 *  cut the waiting one starts playing (still hidden and silent), early by the
 *  time a player takes to get going, which is measured at each cut. It is
 *  shown, and heard, on the frame that is the cut's first, and the other is
 *  paused at the same moment; if it comes late, the outgoing one holds its
 *  last frame meanwhile, never a frame past the cut. So a cut shows at most
 *  about a frame of glitch and never a black flash. Items that carry straight
 *  on in the same file (a split never moved) play on one element with no cut.
 *  The program's clock is the playing element's own media time, read on each
 *  presented frame (requestVideoFrameCallback).
 *
 *  Everything here is imperative on purpose: it runs every frame, and React
 *  only hears of the playhead through its store. */
import { engineUrl } from '../../lib/editorApi'
import { continues, frameAt, frameLength, frameUs, itemIndexAt, programAt, sourceAt } from '../../lib/editorProgram'
import type { Canvas, Item } from '../../lib/editorTypes'
import type { PlayheadStore } from '../../lib/editorPlayhead'
import { isInfo, playable, type MediaInfos } from './useMediaInfos'

interface Slot {
  el: HTMLVideoElement
  /** The program item this element is loaded for, or -1. */
  idx: number
  url: string | null
  /** Its media's clock offset (the source's first timestamp, or 0). */
  offsetUs: number
}

type FrameCallback = (now: number, meta: { mediaTime: number }) => void
type WithFrameCallback = HTMLVideoElement & {
  requestVideoFrameCallback?: (cb: FrameCallback) => number
  cancelVideoFrameCallback?: (id: number) => void
}

/** How far before a cut the waiting player sits, in program time: it plays
 *  this much, hidden and silent, before it is shown. Long enough to bring it
 *  into step with the outgoing one by speed alone (below). */
const PREROLL_MS = 300
/** How much the hidden player's speed may be nudged to arrive on time. */
const NUDGE = 0.2
/** A first guess at how long a player takes from play() to its first moving
 *  frame; replaced by what each cut measures. */
const START_GUESS_MS = 110

const ALIGN: Record<string, string> = {
  center: '50% 50%',
  top: '50% 0%',
  bottom: '50% 100%',
  left: '0% 50%',
  right: '100% 50%'
}

/** How a framed picture is shown in the browser: the export's framing in CSS.
 *  An AI item's kept framing (a tracked crop, a Gaming layout) is shown as a
 *  centre fill while playing; pausing shows the engine's own frame. */
export function framingStyle(item: Item, ai = false): { fit: 'contain' | 'cover'; position: string; pad: 'blur' | 'none' | string } {
  if (ai) return { fit: 'cover', position: '50% 50%', pad: 'none' }
  const f = item.framing ?? {}
  const position = ALIGN[f.align ?? 'center'] ?? ALIGN.center
  if (f.fit === 'fill') return { fit: 'cover', position, pad: 'none' }
  return { fit: 'contain', position, pad: f.pad && f.pad !== 'blur' ? f.pad : 'blur' }
}

/** The CSS transform of an item's `transform` (step 7: scaled about its
 *  centre, turned clockwise, its centre moved by x canvas widths and y canvas
 *  heights). The element is the canvas's size, so percentages are of it. */
export function transformCss(tr: Item['transform']): { transform: string; opacity: number } {
  const x = tr?.x ?? 0
  const y = tr?.y ?? 0
  const scale = tr?.scale ?? 1
  const rotation = tr?.rotation ?? 0
  const parts: string[] = []
  if (x || y) parts.push(`translate(${x * 100}%, ${y * 100}%)`)
  if (rotation) parts.push(`rotate(${rotation}deg)`)
  if (scale !== 1) parts.push(`scale(${scale})`)
  return { transform: parts.join(' '), opacity: tr?.opacity ?? 1 }
}

export class ProgramPlayer {
  private slots: [Slot, Slot]
  private active = 0
  private idx = -1
  private playing = false
  private items: Item[] = []
  private infos: MediaInfos = {}
  private canvas: Canvas | null = null
  private muted = false
  private length = 0
  private ai: (item: Item) => boolean = () => false
  /** Each element's pending frame callback, to cancel it. */
  private frameHandles: [number | null, number | null] = [null, null]
  private rafHandle: number | null = null
  /** The next cut, once its player was started ahead of it. */
  private rolling: { n: number; calledAt: number; fromUs: number; measured: boolean } | null = null
  /** The outgoing player was paused on its last frame, waiting for the cut. */
  private holding = false
  /** When the playing element's current item ends, in wall time (ms). */
  private endsAt = 0
  private startMs = START_GUESS_MS
  /** For tests and the black-box check: each cut's measured lateness, ms. */
  readonly cutLog: number[] = []

  /** `onStall`: playing reached a part with nothing playable yet. */
  constructor(
    a: HTMLVideoElement,
    b: HTMLVideoElement,
    private bg: HTMLCanvasElement,
    private playhead: PlayheadStore,
    private onStall: () => void
  ) {
    this.slots = [
      { el: a, idx: -1, url: null, offsetUs: 0 },
      { el: b, idx: -1, url: null, offsetUs: 0 }
    ]
    for (const s of this.slots) {
      s.el.addEventListener('seeked', () => this.drawBg(s))
      s.el.addEventListener('loadeddata', () => this.drawBg(s))
    }
    this.show(0)
  }

  /** The program changed (an edit, a proxy now ready): follow it at once. */
  setProgram(
    items: Item[],
    infos: MediaInfos,
    canvas: Canvas,
    a1Muted: boolean,
    length: number,
    ai: (item: Item) => boolean
  ): void {
    this.ai = ai
    this.items = items
    this.infos = infos
    this.canvas = canvas
    this.muted = a1Muted
    this.length = length
    for (const s of this.slots) s.idx = -1
    this.seek(this.playhead.get())
  }

  isPlaying(): boolean {
    return this.playing
  }

  play(): void {
    if (this.playing || !this.canvas || this.items.length === 0) return
    let t = this.playhead.get()
    if (t >= this.lastFrame()) t = 0
    this.idx = Math.max(0, itemIndexAt(this.items, t))
    const slot = this.slots[this.active]
    if (!this.load(slot, this.idx, t)) {
      this.onStall()
      return
    }
    this.playhead.set(t)
    this.show(this.active)
    this.playing = true
    this.playhead.setPlaying(true)
    void slot.el.play().catch(() => undefined)
    this.prepareNext()
    this.watch(this.active)
  }

  pause(): void {
    this.playing = false
    this.playhead.setPlaying(false)
    this.stopWatching()
    for (const s of this.slots) s.el.pause()
    // The paused frame is the one the playhead names.
    this.playhead.set(this.frameStart(this.playhead.get()))
  }

  /** Move to program time t: playing goes on from there; paused, the playing
   *  element shows that frame (the engine's still is drawn over it). */
  seek(tUs: number): void {
    if (!this.canvas) return
    const t = Math.min(Math.max(0, tUs), this.lastFrame())
    this.stopWatching()
    this.playhead.set(this.playing ? t : this.frameStart(t))
    if (this.items.length === 0) return
    this.idx = Math.max(0, itemIndexAt(this.items, t))
    const slot = this.slots[this.active]
    const other = this.slots[1 - this.active]
    other.el.pause()
    other.idx = -1 // made ready again for the new place
    const ready = this.load(slot, this.idx, t)
    this.show(this.active)
    if (this.playing) {
      if (!ready) {
        this.pause()
        this.onStall()
        return
      }
      void slot.el.play().catch(() => undefined)
      this.watch(this.active)
    }
    this.prepareNext()
  }

  /** Frames of the canvas's frame rate forward or back (paused). */
  step(frames: number): void {
    if (!this.canvas) return
    if (this.playing) this.pause()
    const fps = this.canvas.fps
    const n = frameAt(this.playhead.get(), fps) + frames
    this.seek(frameUs(Math.max(0, n), fps))
  }

  lastFrame(): number {
    if (!this.canvas || this.length <= 0) return 0
    return this.frameStart(this.length - 1)
  }

  destroy(): void {
    this.playing = false
    this.stopWatching()
    for (const s of this.slots) {
      s.el.pause()
      s.el.removeAttribute('src')
      s.el.load()
    }
  }

  // ---- inside -------------------------------------------------------------------------

  private frameStart(t: number): number {
    return this.canvas ? frameUs(frameAt(Math.max(0, t), this.canvas.fps), this.canvas.fps) : t
  }

  private stopWatching(): void {
    if (this.rafHandle !== null) window.cancelAnimationFrame(this.rafHandle)
    this.rafHandle = null
    this.slots.forEach((s, k) => {
      const el = s.el as WithFrameCallback
      const h = this.frameHandles[k]
      if (h !== null && el.cancelVideoFrameCallback) el.cancelVideoFrameCallback(h)
      this.frameHandles[k] = null
    })
    if (this.rolling) {
      const other = this.slots[1 - this.active]
      other.el.pause()
      other.idx = -1
    }
    this.rolling = null
    this.holding = false
  }

  /** Load item idx on an element at program time t, `prerollUs` of source
   *  earlier. False when the item has nothing playable yet (its proxy is
   *  being made, its file is missing). */
  private load(slot: Slot, idx: number, tUs: number, prerollUs = 0): boolean {
    const item = this.items[idx]
    if (!item || !item.media_id) return false
    const info = this.infos[item.media_id]
    const p = isInfo(info) ? playable(info) : null
    if (!p) {
      slot.idx = idx
      return false
    }
    if (slot.url !== p.url) {
      slot.el.src = engineUrl(p.url)
      slot.url = p.url
    }
    slot.offsetUs = p.offsetUs
    const target = Math.max(0, sourceAt(item, tUs) + p.offsetUs - prerollUs) / 1e6
    if (slot.idx !== idx || Math.abs(slot.el.currentTime - target) > 0.0005) slot.el.currentTime = target
    slot.idx = idx
    this.style(slot, item)
    return true
  }

  private style(slot: Slot, item: Item): void {
    const el = slot.el
    el.playbackRate = item.speed || 1
    el.volume = Math.min(1, Math.max(0, item.volume ?? 1))
    el.muted = this.muted
    const f = framingStyle(item, this.ai(item))
    el.style.objectFit = f.fit
    el.style.objectPosition = f.position
    const tr = transformCss(item.transform)
    el.style.transform = tr.transform
    el.style.opacity = String(tr.opacity)
  }

  private show(i: number): void {
    this.slots[i].el.style.visibility = 'visible'
    this.slots[1 - i].el.style.visibility = 'hidden'
    this.drawBg(this.slots[i])
  }

  /** The first item after the active one that needs a cut (not a carry-on). */
  private nextCut(from: number): number {
    let j = from + 1
    while (j < this.items.length && continues(this.items[j - 1], this.items[j])) j++
    return j
  }

  /** Put the waiting player a little before the next cut's first frame. */
  private prepareNext(): void {
    const n = this.nextCut(this.idx)
    if (n >= this.items.length) return
    const other = this.slots[1 - this.active]
    if (other.idx === n) return
    other.el.pause()
    const item = this.items[n]
    this.load(other, n, item.start_us, PREROLL_MS * 1000 * (item.speed || 1))
    other.el.muted = true
  }

  private watch(k: number): void {
    const el = this.slots[k].el as WithFrameCallback
    if (this.frameHandles[k] !== null) return // already watched
    if (el.requestVideoFrameCallback) {
      this.frameHandles[k] = el.requestVideoFrameCallback((now, meta) => {
        this.frameHandles[k] = null
        if (!this.playing) return
        if (k === this.active) this.tick(now, meta.mediaTime)
        else this.tickNext(now, meta.mediaTime)
        if (this.playing && (k === this.active || this.rolling)) this.watch(k)
      })
    } else if (k === this.active) {
      this.rafHandle = window.requestAnimationFrame(() => {
        if (!this.playing) return
        this.tick(performance.now(), el.currentTime)
        this.watch(k)
      })
    }
  }

  /** A frame of the playing element. */
  private tick(now: number, mediaTime: number): void {
    const slot = this.slots[this.active]
    let item = this.items[this.idx]
    if (!item || !this.canvas) return
    const src = mediaTime * 1e6 - slot.offsetUs
    let tp = programAt(item, src)
    // Carry on through items that continue in the same file.
    while (
      tp >= item.start_us + item.duration_us &&
      this.idx + 1 < this.items.length &&
      continues(item, this.items[this.idx + 1])
    ) {
      this.idx += 1
      item = this.items[this.idx]
      slot.idx = this.idx
      this.style(slot, item)
      tp = programAt(item, src)
    }
    const end = item.start_us + item.duration_us
    if (!this.holding) this.playhead.set(Math.min(Math.max(item.start_us, tp), end))
    this.drawBg(slot)
    this.endsAt = now + (end - tp) / 1000
    const frame = frameLength(tp, this.canvas.fps)
    const remainingMs = (end - tp) / 1000
    const n = this.nextCut(this.idx)
    // Start the next cut's player early by what a start takes, so it is
    // moving when its first frame is due.
    if (!this.rolling && n < this.items.length && remainingMs <= this.startMs + PREROLL_MS) {
      const other = this.slots[1 - this.active]
      if (other.idx !== n) this.prepareNext()
      if (other.idx === n && other.url) {
        other.el.muted = true
        this.rolling = { n, calledAt: performance.now(), fromUs: other.el.currentTime * 1e6, measured: false }
        void other.el.play().catch(() => undefined)
        this.watch(1 - this.active)
      }
    }
    // The last frame of this item is on screen: hold it till the cut.
    if (tp >= end - frame * 1.5) {
      if (n >= this.items.length) {
        this.finish()
        return
      }
      if (!this.rolling) {
        this.hardCut(n)
        return
      }
      if (!this.holding) {
        slot.el.pause()
        this.holding = true
        // A player that never gets going: cut to it anyway after a second.
        const r = this.rolling
        window.setTimeout(() => {
          if (this.playing && this.holding && this.rolling === r && r) {
            const item = this.items[r.n]
            const first = sourceAt(item, item.start_us) + this.slots[1 - this.active].offsetUs
            this.tickNext(performance.now(), first / 1e6)
          }
        }, 1000)
      }
    }
  }

  /** A frame of the player started ahead of the next cut. */
  private tickNext(now: number, mediaTime: number): void {
    const r = this.rolling
    if (!r) return
    const slot = this.slots[1 - this.active]
    const item = this.items[r.n]
    if (!r.measured && mediaTime * 1e6 > r.fromUs + 1000) {
      // How long it took to get going: when it showed this frame, less how
      // long that frame is after where it started.
      const took = now - r.calledAt - (mediaTime * 1e6 - r.fromUs) / 1000 / (item.speed || 1)
      if (took > 0 && took < 2000) this.startMs = this.startMs * 0.5 + took * 0.5
      r.measured = true
    }
    const speed = item.speed || 1
    const firstUs = sourceAt(item, item.start_us) + slot.offsetUs
    const half = frameLength(item.start_us, this.canvas!.fps) * 0.5 * speed
    if (mediaTime * 1e6 < firstUs - half) {
      // Bring it into step: when it would reach the cut at its speed, against
      // when the outgoing item ends. Nudge its speed (it is silent) so both
      // fall on the same moment.
      const leftMs = (firstUs - mediaTime * 1e6) / 1000 / speed
      const lateMs = now + leftMs - this.endsAt
      const want = leftMs > 20 ? 1 + lateMs / leftMs : 1
      slot.el.playbackRate = speed * Math.min(1 + NUDGE, Math.max(1 - NUDGE, want))
      return
    }
    slot.el.playbackRate = speed
    // The cut's first frame: show it, and stop the outgoing player.
    const outgoing = this.slots[this.active]
    this.cutLog.push(Math.round((mediaTime * 1e6 - firstUs) / 1000 / (item.speed || 1)))
    outgoing.el.pause()
    this.active = 1 - this.active
    this.idx = r.n
    this.rolling = null
    this.holding = false
    slot.el.muted = this.muted
    this.show(this.active)
    this.playhead.set(item.start_us)
    // The one now waiting is made ready for the cut after.
    window.setTimeout(() => {
      if (this.playing) this.prepareNext()
    }, 30)
  }

  /** A cut with no player started ahead (its media wasn't ready to play). */
  private hardCut(n: number): void {
    const cur = this.slots[this.active]
    const next = this.slots[1 - this.active]
    cur.el.pause()
    if (!this.load(next, n, this.items[n].start_us)) {
      this.playing = false
      this.playhead.setPlaying(false)
      this.stopWatching()
      this.idx = n
      this.playhead.set(this.items[n].start_us)
      this.onStall()
      return
    }
    next.el.muted = this.muted
    this.active = 1 - this.active
    this.idx = n
    this.show(this.active)
    this.playhead.set(this.items[n].start_us)
    void next.el.play().catch(() => undefined)
    this.watch(this.active)
    window.setTimeout(() => {
      if (this.playing) this.prepareNext()
    }, 30)
  }

  private finish(): void {
    this.slots[this.active].el.pause()
    this.playing = false
    this.playhead.setPlaying(false)
    this.stopWatching()
    this.playhead.set(this.lastFrame())
  }

  /** The blurred pad behind a fitted picture, from the playing element. */
  private drawBg(slot: Slot): void {
    if (this.slots[this.active] !== slot || !this.canvas) return
    const item = this.items[slot.idx]
    const ctx = this.bg.getContext('2d')
    if (!ctx) return
    const { width: W, height: H } = this.bg
    const f = item ? framingStyle(item, this.ai(item)) : null
    if (!f || f.pad === 'none') {
      ctx.clearRect(0, 0, W, H)
      return
    }
    if (f.pad !== 'blur') {
      ctx.fillStyle = f.pad
      ctx.fillRect(0, 0, W, H)
      return
    }
    const v = slot.el
    if (v.readyState < 2 || !v.videoWidth) return
    // Cover the pad with the picture, as the export's wash does.
    const k = Math.max(W / v.videoWidth, H / v.videoHeight)
    const sw = W / k
    const sh = H / k
    try {
      ctx.drawImage(v, (v.videoWidth - sw) / 2, (v.videoHeight - sh) / 2, sw, sh, 0, 0, W, H)
    } catch {
      /* a frame not ready yet */
    }
  }
}
