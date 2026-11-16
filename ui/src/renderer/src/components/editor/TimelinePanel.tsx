/** The timeline, across the bottom, laid out as creators know it from CapCut:
 *  text tracks on top, the overlays (V2 and up, the higher above), the main
 *  track (V1) with its pictures and its sound's waveform, then the sound
 *  tracks (A2 and up) with theirs. Step 9's caption lane goes between the
 *  main track and the sounds.
 *
 *  - Click an item to select it (Shift or Ctrl for more); click the ruler or
 *    an empty part of a track to move the playhead there.
 *  - Drag an item to move it: on the main track it changes places with the
 *    others (the main track never has gaps); on the others it goes where it
 *    is dropped, onto another track of its kind too. Drag either edge to trim.
 *  - Edges snap to the playhead, every item's edges and the video's start and
 *    end, while snapping is on.
 *  - Ctrl+wheel zooms about the pointer; Fit shows the whole video.
 *
 *  Every change is one of step 5's operations, sent when the drag ends, so a
 *  drag is one undo step. Only what is on screen is drawn: the items near the
 *  view as buttons (each a screen reader can name and a keyboard can reach),
 *  and their pictures and waveforms on one canvas per track, drawn again as
 *  the view scrolls. */
import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { formatTime, frameUs, v1Items } from '../../lib/editorProgram'
import type { PlayheadStore } from '../../lib/editorPlayhead'
import type { Item, Op, OpsAnswer, ProjectDoc, Track } from '../../lib/editorTypes'
import {
  ZOOM_STEP,
  clampPps,
  dropIndex,
  fitPps,
  itemEnd,
  itemsIn,
  pxToUs,
  rowsOf,
  rulerLabel,
  rulerStep,
  shapeOf,
  snapPoints,
  snapTo,
  timelineLength,
  trimEdge,
  usToPx,
  type Row
} from '../../lib/editorTimeline'
import { TimelineMedia, drawThumbs, drawWave } from './timelineMedia'
import type { MediaInfos } from './useMediaInfos'

export interface TimelineHandle {
  /** S: split the selected items under the playhead (or the main track's). */
  split: () => void
  /** Delete: remove the selected items (the main track closes the gap). */
  remove: () => void
  /** + and -: zoom in or out about the playhead. */
  zoom: (dir: 1 | -1) => void
  /** Shift+Z: the whole video in view. */
  fit: () => void
  /** Alt+← →: move the one selected item a place (main track) or a frame
   *  (Shift: a second) earlier or later. */
  nudge: (dir: 1 | -1, big: boolean) => void
}

/** The timeline's keys, for its tooltip and the editor's. */
export const TIMELINE_SHORTCUTS: [string, string][] = [
  ['S', 'Split at the playhead'],
  ['Delete', 'Delete the selected items'],
  ['Alt+← →', 'Move the selected item earlier or later'],
  ['+ −', 'Zoom the timeline in or out'],
  ['Ctrl+wheel', 'Zoom the timeline in or out'],
  ['Shift+Z', 'Fit the whole video in the timeline'],
  ['Shift/Ctrl+click', 'Select more than one item']
]

/** The media bin's drag, carrying a media id. */
export const MEDIA_DRAG = 'application/x-clips-kitty-media'
/** A picture added to a track lasts 3 seconds (trim it to change that). */
export const PICTURE_US = 3_000_000

/** Key names in tooltips (not words to translate). */
const KEYS = { split: 'S', zoomOut: '−, Ctrl+wheel', zoomIn: '+, Ctrl+wheel', fit: 'Shift+Z' }

const HEADER_W = 112
const RULER_H = 22
const EDGE_PX = 7
const SNAP_PX = 8
const DRAG_PX = 3
const SNAP_KEY = 'new-editor-snap'

interface Props {
  doc: ProjectDoc
  playhead: PlayheadStore
  selected: string[]
  onSelect: (ids: string[]) => void
  onSeek: (tUs: number) => void
  apply: (ops: Op[], label?: string) => Promise<OpsAnswer | null>
  infos: MediaInfos
  readOnly: boolean
}

interface Drag {
  id: string
  trackId: string
  mode: 'move' | 'left' | 'right'
  x0: number
  y0: number
  /** Where the pointer was in the item, as a time, for moving. */
  grabUs: number
  moved: boolean
  /** The ghost: where the item would go. */
  start: number
  end: number
  targetTrack: string
  index?: number
  fields?: Record<string, number>
}

function loadSnap(): boolean {
  try {
    return localStorage.getItem(SNAP_KEY) !== '0'
  } catch {
    return true
  }
}

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function trackLabel(track: Track): string {
  if (track.id === 'V1') return t('Main track')
  return track.name && track.name !== track.id ? `${track.name} (${track.id})` : track.id
}

const ITEM_TONE: Record<Row['kind'], string> = {
  main: 'border-sky-300/50',
  overlay: 'border-violet-300/60 bg-violet-500/10',
  text: 'border-amber-300/60 bg-amber-500/25',
  audio: 'border-emerald-300/60 bg-emerald-500/15'
}

interface ItemHandlers {
  down: (e: React.PointerEvent<HTMLButtonElement>, id: string) => void
  move: (e: React.PointerEvent<HTMLButtonElement>) => void
  up: (e: React.PointerEvent<HTMLButtonElement>) => void
  cancel: () => void
  key: (id: string, e: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }) => void
}

/** One item on a track: a button a keyboard can reach and a screen reader
 *  can name, over its pictures on the track's canvas, with a handle at each
 *  edge to trim it. Drawn again only when its own props change. */
const ItemButton = memo(function ItemButton({
  id,
  kind,
  x,
  w,
  on,
  away,
  readOnly,
  say,
  title,
  text,
  handlers
}: {
  id: string
  kind: Row['kind']
  x: number
  w: number
  on: boolean
  away: boolean
  readOnly: boolean
  say: string
  title: string
  text: string
  handlers: ItemHandlers
}): JSX.Element {
  return (
    <button
      type="button"
      data-item={id}
      aria-pressed={on}
      aria-label={say}
      title={title}
      className={`absolute top-px bottom-px rounded-[4px] border text-left overflow-hidden select-none touch-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${
        ITEM_TONE[kind]
      } ${on ? '!border-accent ring-1 ring-accent' : ''} ${away ? 'opacity-40' : ''}`}
      // Contained, so a zoom step lays out each item on its own.
      style={{ left: x, width: w, cursor: readOnly ? 'default' : 'grab', contain: 'strict' }}
      onPointerDown={(e) => handlers.down(e, id)}
      onPointerMove={handlers.move}
      onPointerUp={handlers.up}
      onPointerCancel={handlers.cancel}
      onClick={(e) => {
        // A click from the keyboard (Enter or Space) selects; a mouse click
        // already did on pointer down.
        if (e.detail === 0) handlers.key(id, e)
      }}
    >
      {w > 24 && (
        <span
          className={`block truncate px-1 text-[10px] leading-4 ${
            kind === 'text' ? 'text-ink' : 'text-white [text-shadow:0_1px_2px_rgba(0,0,0,0.9)]'
          }`}
        >
          {text}
        </span>
      )}
      {!readOnly && w > 3 * EDGE_PX && (
        <>
          <span data-edge="left" className="absolute left-0 top-0 bottom-0 cursor-ew-resize hover:bg-white/30" style={{ width: EDGE_PX }} aria-hidden="true" />
          <span data-edge="right" className="absolute right-0 top-0 bottom-0 cursor-ew-resize hover:bg-white/30" style={{ width: EDGE_PX }} aria-hidden="true" />
        </>
      )}
    </button>
  )
})

const TimelinePanel = forwardRef<TimelineHandle, Props>(function TimelinePanel(
  { doc, playhead, selected, onSelect, onSeek, apply, infos, readOnly },
  ref
) {
  const fps = doc.canvas.fps
  const rows = useMemo(() => rowsOf(doc), [doc])
  const mainItems = useMemo(() => v1Items(doc), [doc])
  const length = useMemo(() => timelineLength(doc), [doc])
  const selectedSet = useMemo(() => new Set(selected), [selected])

  // ---- the view: its width, the zoom, the scroll ----
  const scroller = useRef<HTMLDivElement>(null)
  const [laneW, setLaneW] = useState(800)
  const [userPps, setUserPps] = useState<number | null>(null) // null: fit
  const pps = userPps ?? fitPps(doc, laneW)
  const contentW = Math.max(laneW, Math.ceil(usToPx(length, pps) + laneW * 0.4))
  const scrollLeft = useRef(0)
  /** Which items are buttons: those in the view and a view's width each side
   *  of where the view was when this was last placed (`bucket`, in pixels),
   *  placed again only when the view nears its edge or the zoom changes. */
  const [bucket, setBucket] = useState(0)
  const win = { from: Math.max(0, pxToUs(bucket - laneW, pps)), to: pxToUs(bucket + 2 * laneW, pps) }
  const live = useRef({ doc, pps, laneW, rows })
  live.current = { doc, pps, laneW, rows }
  const [snapOn, setSnapOn] = useState(loadSnap)

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const ro = new ResizeObserver(() => setLaneW(Math.max(200, el.clientWidth - HEADER_W)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const placeWindow = useCallback((): void => {
    const { laneW: w } = live.current
    const left = scrollLeft.current
    setBucket((cur) => (Math.abs(left - cur) > w / 2 ? left : cur))
  }, [])

  // ---- drawing the canvases ----
  const canvases = useRef(new Map<string, HTMLCanvasElement>())
  const ruler = useRef<HTMLCanvasElement>(null)
  const drag = useRef<Drag | null>(null)
  const [ghost, setGhost] = useState<Drag | null>(null)
  const frame = useRef(0)
  const redraw = useRef<() => void>(() => undefined)
  // Made in an effect, so React's development double start doesn't leave a
  // stopped one in use.
  const mediaRef = useRef<TimelineMedia | null>(null)
  const infosRef = useRef(infos)
  infosRef.current = infos
  mediaRef.current?.setInfos(infos)
  useEffect(() => {
    const m = new TimelineMedia(() => redraw.current())
    m.setInfos(infosRef.current)
    mediaRef.current = m
    redraw.current()
    return () => {
      m.destroy()
      if (mediaRef.current === m) mediaRef.current = null
    }
  }, [])

  const drawNow = (): void => {
    frame.current = 0
    const { doc: d, pps: p, laneW: w, rows: rs } = live.current
    const left = scrollLeft.current
    const dpr = window.devicePixelRatio || 1
    const tone = {
      item: cssVar('--color-raised', '#1c3354'),
      wave: 'rgba(110, 231, 183, 0.85)',
      mainWave: 'rgba(125, 211, 252, 0.8)',
      tick: cssVar('--color-muted', '#94a3b8')
    }
    const g = drag.current
    const media = mediaRef.current
    // The ruler.
    const rc = ruler.current
    if (rc) {
      if (rc.width !== Math.round(w * dpr)) rc.width = Math.round(w * dpr)
      if (rc.height !== Math.round(RULER_H * dpr)) rc.height = Math.round(RULER_H * dpr)
      const ctx = rc.getContext('2d')
      if (ctx) {
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
        ctx.clearRect(0, 0, w, RULER_H)
        const step = rulerStep(p)
        const minor = step / 5
        ctx.fillStyle = tone.tick
        ctx.font = '10px system-ui, sans-serif'
        const first = Math.floor(pxToUs(left, p) / minor)
        for (let n = first; usToPx(n * minor, p) - left < w; n++) {
          const u = n * minor
          const x = Math.round(usToPx(u, p) - left) + 0.5
          const major = n % 5 === 0
          ctx.fillRect(x, major ? 10 : 16, 1, major ? 12 : 6)
          if (major) ctx.fillText(rulerLabel(u, step), x + 3, 10)
        }
      }
    }
    // The tracks.
    for (const row of rs) {
      const c = canvases.current.get(row.track.id)
      if (!c) continue
      const h = row.height
      if (c.width !== Math.round(w * dpr)) c.width = Math.round(w * dpr)
      if (c.height !== Math.round(h * dpr)) c.height = Math.round(h * dpr)
      const ctx = c.getContext('2d')
      if (!ctx) continue
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      ctx.clearRect(0, 0, w, h)
      if (row.kind === 'text') continue
      const track = d.tracks.find((tr) => tr.id === row.track.id) ?? row.track
      const from = pxToUs(left, p)
      const to = pxToUs(left + w, p)
      for (const it of itemsIn(track, from, to)) {
        let start = it.start_us
        let end = itemEnd(it)
        let sIn = it.source_in_us
        let sOut = it.source_out_us
        if (g && g.moved && g.id === it.id && g.mode !== 'move') {
          start = g.start
          end = g.end
          if (g.fields?.source_in_us !== undefined) sIn = g.fields.source_in_us
          if (g.fields?.source_out_us !== undefined) sOut = g.fields.source_out_us
        }
        const x = usToPx(start, p) - left
        const iw = Math.max(1, usToPx(end - start, p))
        ctx.fillStyle = tone.item
        ctx.fillRect(x + 0.5, 1, Math.max(0.5, iw - 1), h - 2)
        if (iw < 3 || !it.media_id || !media) continue
        // A picture has no source stretch: its one tile all along.
        const a = sIn === sOut ? 0 : sIn
        const b = sIn === sOut ? 1 : sOut
        if (row.kind === 'main' || row.kind === 'overlay') {
          const index = media.thumbIndex(it.media_id)
          const th = row.kind === 'main' ? h - 22 : h - 4
          if (index) drawThumbs(ctx, media, index, a, b, x + 1, iw - 2, 2, th, 0, w)
          if (row.kind === 'main') {
            const levels = media.peakLevels(it.media_id)
            if (levels) drawWave(ctx, levels, sIn, sOut, x + 1, iw - 2, h - 19, 17, 0, w, tone.mainWave)
          }
        } else {
          const levels = media.peakLevels(it.media_id)
          if (levels) drawWave(ctx, levels, sIn, sOut, x + 1, iw - 2, 3, h - 6, 0, w, tone.wave)
        }
      }
    }
  }
  const requestDraw = (): void => {
    if (!frame.current) frame.current = window.requestAnimationFrame(drawNow)
  }
  redraw.current = requestDraw
  useEffect(
    () => () => {
      window.cancelAnimationFrame(frame.current)
      frame.current = 0
    },
    []
  )
  // Draw after every render (an edit, a zoom, a new window, the ghost).
  useLayoutEffect(() => {
    requestDraw()
  })

  // ---- scrolling and zooming ----
  // A new zoom keeps the time under the pointer (or the playhead) where it
  // was on screen: its scroll is worked out with the zoom and set right
  // after the timeline is drawn at it, in one render.
  const pendingScroll = useRef<number | null>(null)
  const zoomTo = (next: number, anchorUs: number, anchorX: number): void => {
    const pps2 = clampPps(next)
    if (pps2 === live.current.pps) return
    const left = Math.max(0, usToPx(anchorUs, pps2) - anchorX)
    pendingScroll.current = left
    setUserPps(pps2)
    setBucket(left)
  }
  useLayoutEffect(() => {
    const el = scroller.current
    const left = pendingScroll.current
    if (!el || left === null) return
    pendingScroll.current = null
    // Not read back: reading it would make the browser lay out the
    // timeline here, and again when it draws.
    el.scrollLeft = left
    scrollLeft.current = left
  }, [pps])

  const zoomRef = useRef(zoomTo)
  zoomRef.current = zoomTo
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const onScroll = (): void => {
      scrollLeft.current = el.scrollLeft
      placeWindow()
      redraw.current()
    }
    // Wheel steps that come between two frames are joined into one zoom.
    let steps = 0
    let wheelFrame = 0
    let anchorX = 0
    const onWheel = (e: WheelEvent): void => {
      if (!e.ctrlKey) return
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      anchorX = Math.max(0, e.clientX - rect.left - HEADER_W)
      steps += e.deltaY < 0 ? 1 : -1
      if (wheelFrame) return
      wheelFrame = window.requestAnimationFrame(() => {
        wheelFrame = 0
        const { pps: p } = live.current
        const at = pxToUs(scrollLeft.current + anchorX, p)
        const n = steps
        steps = 0
        if (n !== 0) zoomRef.current(p * Math.pow(ZOOM_STEP, n), at, anchorX)
      })
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      window.cancelAnimationFrame(wheelFrame)
      el.removeEventListener('scroll', onScroll)
      el.removeEventListener('wheel', onWheel)
    }
  }, [placeWindow])

  // ---- the playhead: a line moved without drawing the timeline again ----
  const line = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const place = (): void => {
      const el = scroller.current
      const now = playhead.get()
      const { pps: p, laneW: w } = live.current
      const x = usToPx(now, p)
      if (line.current) line.current.style.transform = `translateX(${HEADER_W + x}px)`
      // While playing, the view turns the page when the playhead reaches its edge.
      if (el && playhead.playing() && !drag.current && (x > scrollLeft.current + w - 16 || x < scrollLeft.current)) {
        el.scrollLeft = Math.max(0, x - 24)
      }
    }
    place()
    return playhead.subscribe(place)
  }, [playhead, pps, laneW])

  // ---- where the pointer is ----
  const timeAt = (clientX: number): number => {
    const el = scroller.current
    if (!el) return 0
    const x = clientX - el.getBoundingClientRect().left - HEADER_W + scrollLeft.current
    return Math.max(0, pxToUs(x, live.current.pps))
  }
  const lanes = useRef(new Map<string, HTMLDivElement>())
  const rowAt = (clientY: number): Row | undefined =>
    live.current.rows.find((r) => {
      const el = lanes.current.get(r.track.id)
      if (!el) return false
      const b = el.getBoundingClientRect()
      return clientY >= b.top && clientY < b.bottom
    })

  const findItem = (id: string): { item: Item; track: Track } | undefined => {
    for (const track of doc.tracks) {
      const item = track.items.find((i) => i.id === id)
      if (item) return { item, track }
    }
    return undefined
  }

  // ---- selecting, moving, trimming ----
  const select = (id: string, e: { shiftKey: boolean; ctrlKey: boolean; metaKey: boolean }): void => {
    if (e.ctrlKey || e.metaKey) onSelect(selectedSet.has(id) ? selected.filter((s) => s !== id) : [...selected, id])
    else if (e.shiftKey) onSelect(selectedSet.has(id) ? selected : [...selected, id])
    else onSelect([id])
  }

  const onItemDown = (e: React.PointerEvent<HTMLButtonElement>, row: Row, item: Item): void => {
    if (e.button !== 0) return
    if (!selectedSet.has(item.id) || e.shiftKey || e.ctrlKey || e.metaKey) select(item.id, e)
    if (readOnly) return
    const edge = (e.target as HTMLElement).dataset.edge as 'left' | 'right' | undefined
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = {
      id: item.id,
      trackId: row.track.id,
      mode: edge ?? 'move',
      x0: e.clientX,
      y0: e.clientY,
      grabUs: timeAt(e.clientX) - item.start_us,
      moved: false,
      start: item.start_us,
      end: itemEnd(item),
      targetTrack: row.track.id
    }
  }

  const onItemMove = (e: React.PointerEvent<HTMLButtonElement>): void => {
    const g = drag.current
    if (!g) return
    if (!g.moved && Math.abs(e.clientX - g.x0) < DRAG_PX && Math.abs(e.clientY - g.y0) < DRAG_PX) return
    const found = findItem(g.id)
    if (!found) return
    const { item, track } = found
    const p = live.current.pps
    const within = pxToUs(SNAP_PX, p)
    const points = snapOn ? snapPoints(doc, playhead.get(), new Set([g.id])) : []
    const next: Drag = { ...g, moved: true }
    if (g.mode === 'move') {
      const over = rowAt(e.clientY)
      next.targetTrack = over && over.track.kind === track.kind ? over.track.id : g.trackId
      const span = itemEnd(item) - item.start_us
      let start = Math.max(0, timeAt(e.clientX) - g.grabUs)
      if (next.targetTrack === 'V1') {
        next.index = dropIndex(mainItems, item.id, timeAt(e.clientX))
      } else {
        const a = snapTo(start, points, within)
        const b = snapTo(start + span, points, within)
        if (a !== null && (b === null || Math.abs(a - start) <= Math.abs(b - start - span))) start = a
        else if (b !== null) start = Math.max(0, b - span)
        next.index = undefined
      }
      next.start = Math.round(start)
      next.end = Math.round(start + span)
    } else {
      const m = item.media_id ? doc.media[item.media_id] : undefined
      const shape = shapeOf(item, track, m)
      const edgeAt = g.mode === 'left' ? item.start_us : itemEnd(item)
      let target = edgeAt + pxToUs(e.clientX - g.x0, p)
      const snapped = snapTo(target, points, within)
      if (snapped !== null) target = snapped
      const r = trimEdge(item, shape, g.mode, target - edgeAt, m?.duration_us ?? 0, fps)
      next.start = Math.round(r.start)
      next.end = Math.round(r.end)
      next.fields = r.fields
    }
    drag.current = next
    setGhost(next)
  }

  const onItemUp = (e: React.PointerEvent<HTMLButtonElement>): void => {
    const g = drag.current
    drag.current = null
    setGhost(null)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    if (!g || !g.moved) return
    const found = findItem(g.id)
    if (!found) return
    const { item, track } = found
    if (g.mode === 'move') {
      if (g.targetTrack === 'V1') {
        const now = mainItems.findIndex((i) => i.id === item.id)
        if (track.id === 'V1' && now === g.index) return
        void apply(
          [{ op: 'move_item', item_id: item.id, ...(track.id !== 'V1' ? { track_id: 'V1' } : {}), index: g.index ?? 0 }],
          t('Move')
        )
      } else {
        if (g.targetTrack === track.id && g.start === item.start_us) return
        void apply(
          [{ op: 'move_item', item_id: item.id, ...(g.targetTrack !== track.id ? { track_id: g.targetTrack } : {}), start_us: g.start }],
          t('Move')
        )
      }
    } else if (g.fields) {
      void apply([{ op: 'trim_item', item_id: item.id, ...g.fields }], t('Trim'))
    }
  }

  const cancelDrag = (): void => {
    drag.current = null
    setGhost(null)
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && drag.current) {
        e.stopPropagation()
        drag.current = null
        setGhost(null)
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  // ---- the playhead from the ruler or an empty part of a track ----
  const scrubbing = useRef(false)
  const onRulerDown = (e: React.PointerEvent<HTMLElement>): void => {
    if (e.button !== 0) return
    scrubbing.current = true
    e.currentTarget.setPointerCapture(e.pointerId)
    onSeek(timeAt(e.clientX))
  }
  const onRulerMove = (e: React.PointerEvent<HTMLElement>): void => {
    if (scrubbing.current) onSeek(timeAt(e.clientX))
  }
  const onRulerUp = (): void => {
    scrubbing.current = false
  }
  const onLaneDown = (e: React.PointerEvent<HTMLDivElement>): void => {
    if (e.button !== 0) return
    if ((e.target as HTMLElement).closest('[data-item]')) return
    if (!e.shiftKey && !e.ctrlKey) onSelect([])
    onRulerDown(e)
  }

  // ---- media dropped from the bin ----
  const onDragOver = (e: React.DragEvent<HTMLDivElement>, row: Row): void => {
    if (readOnly || !e.dataTransfer.types.includes(MEDIA_DRAG) || (row.kind !== 'main' && row.kind !== 'overlay')) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
  }
  const onDrop = (e: React.DragEvent<HTMLDivElement>, row: Row): void => {
    const mediaId = e.dataTransfer.getData(MEDIA_DRAG)
    const m = doc.media[mediaId]
    if (!m || readOnly || m.kind === 'audio') return
    e.preventDefault()
    const at = timeAt(e.clientX)
    const out = m.kind === 'image' ? PICTURE_US : m.duration_us
    if (row.kind === 'main')
      void apply(
        [{ op: 'insert_clip', track_id: 'V1', media_id: mediaId, source_in_us: 0, source_out_us: out, index: dropIndex(mainItems, '', at) }],
        t('Add to the timeline')
      )
    else
      void apply(
        [{ op: 'insert_clip', track_id: row.track.id, media_id: mediaId, source_in_us: 0, source_out_us: out, start_us: at }],
        t('Add to the timeline')
      )
  }

  // ---- the toolbar's and the keys' actions ----
  const split = (): void => {
    if (readOnly) return
    const at = playhead.get()
    const inside = (it: Item): boolean => it.start_us < at && at < itemEnd(it)
    let targets = doc.tracks.flatMap((tr) => tr.items).filter((it) => selectedSet.has(it.id) && inside(it))
    if (targets.length === 0) targets = mainItems.filter(inside)
    if (targets.length === 0) return
    void apply(
      targets.map((it) => ({ op: 'split_item', item_id: it.id, at_us: at })),
      t('Split')
    )
  }
  const remove = (): void => {
    if (readOnly || selected.length === 0) return
    void apply([{ op: 'delete_items', item_ids: [...selected] }], t('Delete'))
  }
  const zoomBy = (dir: 1 | -1): void => {
    const now = playhead.get()
    const x = usToPx(now, pps) - scrollLeft.current
    const shown = x >= 0 && x <= laneW
    zoomTo(pps * (dir > 0 ? ZOOM_STEP : 1 / ZOOM_STEP), shown ? now : pxToUs(scrollLeft.current + laneW / 2, pps), shown ? x : laneW / 2)
  }
  const fit = (): void => {
    if (userPps === null) {
      if (scroller.current) scroller.current.scrollLeft = 0
      return
    }
    pendingScroll.current = 0
    setUserPps(null)
    setBucket(0)
  }
  const nudge = (dir: 1 | -1, big: boolean): void => {
    if (readOnly || selected.length !== 1) return
    const found = findItem(selected[0])
    if (!found) return
    const { item, track } = found
    if (track.id === 'V1') {
      const index = mainItems.findIndex((i) => i.id === item.id) + dir
      if (index < 0 || index >= mainItems.length) return
      void apply([{ op: 'move_item', item_id: item.id, index }], t('Move'))
    } else {
      const start = Math.max(0, item.start_us + dir * (big ? 1_000_000 : frameUs(1, fps)))
      if (start === item.start_us) return
      void apply([{ op: 'move_item', item_id: item.id, start_us: start }], t('Move'))
    }
  }
  useImperativeHandle(ref, () => ({ split, remove, zoom: zoomBy, fit, nudge }))

  const toggleTrack = (track: Track): void => {
    if (readOnly) return
    void apply([{ op: 'set_track_props', track_id: track.id, mute: !track.mute }], track.mute ? t('Show') : t('Hide'))
  }
  const a1 = doc.tracks.find((tr) => tr.id === 'A1')

  const shortcutList = TIMELINE_SHORTCUTS.map(([k, what]) => `${k}: ${t(what)}`).join('\n')
  const btn = 'px-2 py-0.5 rounded-md text-xs bg-raised text-muted hover:text-ink disabled:opacity-40'
  const canSplit = !readOnly && mainItems.length > 0

  // What each item is called (for screen readers and its tooltip), worked
  // out once per edit rather than at every zoom step.
  const meta = useMemo(() => {
    const out = new Map<string, { say: string; title: string; text: string }>()
    const place = new Map(mainItems.map((it, i) => [it.id, i + 1]))
    for (const track of doc.tracks) {
      const label = trackLabel(track)
      for (const it of track.items) {
        const m = it.media_id ? doc.media[it.media_id] : undefined
        const name = it.text?.content || m?.name || it.id
        const at = place.get(it.id)
        const where = track.id === 'V1' && at ? `${at}/${mainItems.length}, ` : ''
        const span = `${formatTime(it.start_us, fps)} – ${formatTime(itemEnd(it), fps)}`
        out.set(it.id, {
          say: `${label}, ${where}${name}, ${span}`,
          title: `${name} · ${span}`,
          text: it.speed !== 1 && track.kind !== 'text' ? `${it.speed}× · ${name}` : name
        })
      }
    }
    return out
  }, [doc, mainItems, fps])

  // The items' handlers, the same object at every render (so an item drawn
  // again only when its own place or state changes), calling the latest.
  const latest = useRef({ onItemDown, onItemMove, onItemUp, cancelDrag, select, rows })
  latest.current = { onItemDown, onItemMove, onItemUp, cancelDrag, select, rows }
  const handlers = useMemo<ItemHandlers>(
    () => ({
      down: (e, id) => {
        const { rows: rs, onItemDown: down } = latest.current
        for (const r of rs) {
          const it = r.track.items.find((i) => i.id === id)
          if (it) return down(e, r, it)
        }
      },
      move: (e) => latest.current.onItemMove(e),
      up: (e) => latest.current.onItemUp(e),
      cancel: () => latest.current.cancelDrag(),
      key: (id, e) => latest.current.select(id, e)
    }),
    []
  )

  // Where the line goes when a main-track item is dragged: before the item
  // it would go in front of, or after the last.
  const markerUs = (g: Drag): number => {
    const others = mainItems.filter((i) => i.id !== g.id)
    const index = g.index ?? 0
    if (index < others.length) return others[index].start_us
    return others.length ? itemEnd(others[others.length - 1]) : 0
  }

  return (
    <section className="h-64 shrink-0 border-t border-raised/60 bg-surface flex flex-col" aria-label={t('Timeline')}>
      {/* ---- the toolbar ---- */}
      <div className="flex items-center gap-1.5 px-2 py-1 border-b border-raised/60" role="toolbar" aria-label={t('Timeline tools')}>
        <button type="button" className={btn} onClick={split} disabled={!canSplit} title={`${t('Split at the playhead')} (${KEYS.split})`}>
          {t('Split')}
        </button>
        <button
          type="button"
          className={btn}
          onClick={remove}
          disabled={readOnly || selected.length === 0}
          title={`${t('Delete the selected items')} (${t('Delete')}). ${t('The main track closes the gap.')}`}
        >
          {t('Delete')}
        </button>
        <span className="w-px h-4 bg-raised mx-1" aria-hidden />
        <button
          type="button"
          className={`${btn} ${snapOn ? '!bg-accent/20 !text-accent' : ''}`}
          aria-pressed={snapOn}
          onClick={() => {
            setSnapOn(!snapOn)
            try {
              localStorage.setItem(SNAP_KEY, snapOn ? '0' : '1')
            } catch {
              /* not remembered */
            }
          }}
          title={t('Snapping: edges you drag stick to the playhead and to the edges of other items')}
        >
          {t('Snap')}
        </button>
        <span
          className="text-[11px] text-muted px-1 cursor-help"
          title={t('The main track has no gaps: deleting or moving an item there closes the gap by itself.')}
        >
          {t('Main track magnet')}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" className={btn} onClick={() => zoomBy(-1)} aria-label={t('Zoom out')} title={`${t('Zoom out')} (${KEYS.zoomOut})`}>
            −
          </button>
          <button type="button" className={btn} onClick={() => zoomBy(1)} aria-label={t('Zoom in')} title={`${t('Zoom in')} (${KEYS.zoomIn})`}>
            +
          </button>
          <button
            type="button"
            className={`${btn} ${userPps === null ? '!text-accent' : ''}`}
            onClick={fit}
            title={`${t('Fit the whole video in the timeline')} (${KEYS.fit})`}
          >
            {t('Fit')}
          </button>
          <span className="text-muted text-xs cursor-help px-1" title={shortcutList} aria-label={t('Keyboard shortcuts')} role="note">
            ⌨
          </span>
        </div>
      </div>

      {/* ---- the tracks ---- */}
      <div ref={scroller} className="flex-1 min-h-0 overflow-auto relative" data-timeline-scroller="">
        <div className="relative" style={{ width: HEADER_W + contentW }}>
          {/* The ruler. */}
          <div className="sticky top-0 z-20 flex bg-surface border-b border-raised/60" style={{ height: RULER_H }}>
            <div className="sticky left-0 z-30 bg-surface shrink-0" style={{ width: HEADER_W }} />
            <div
              className="relative cursor-pointer"
              style={{ width: contentW }}
              onPointerDown={onRulerDown}
              onPointerMove={onRulerMove}
              onPointerUp={onRulerUp}
              aria-hidden="true"
            >
              <canvas ref={ruler} className="block" style={{ position: 'sticky', left: HEADER_W, width: laneW, height: RULER_H }} />
            </div>
          </div>

          {rows.map((row) => {
            const track = row.track
            const items = track.items.filter(
              (it) => (it.start_us < win.to && itemEnd(it) > win.from) || selectedSet.has(it.id) || ghost?.id === it.id
            )
            const hidden = row.kind === 'main' ? a1?.mute : track.mute
            const label = trackLabel(track)
            return (
              <div key={track.id}>
                <div className="flex border-b border-raised/40" style={{ height: row.height }} role="group" aria-label={label}>
                  {/* The track's header. */}
                  <div
                    className="sticky left-0 z-10 shrink-0 bg-surface border-r border-raised/60 flex items-center gap-1 px-2 text-[11px]"
                    style={{ width: HEADER_W }}
                  >
                    <span className={`flex-1 truncate ${row.kind === 'main' ? 'font-semibold text-ink' : 'text-muted'}`} title={label}>
                      {label}
                    </span>
                    {row.kind !== 'text' && (
                      <button
                        type="button"
                        className={`px-1 rounded ${hidden ? 'text-amber-300' : 'text-muted hover:text-ink'}`}
                        aria-pressed={Boolean(hidden)}
                        disabled={readOnly || (row.kind === 'main' && !a1)}
                        onClick={() => (row.kind === 'main' ? a1 && toggleTrack(a1) : toggleTrack(track))}
                        aria-label={row.kind === 'overlay' ? t('Hide this track') : t('Mute this track')}
                        title={
                          row.kind === 'overlay'
                            ? t('Hide this track: it is left out of the video')
                            : row.kind === 'main'
                              ? t("Mute the main track's own sound")
                              : t('Mute this track')
                        }
                      >
                        <span aria-hidden>{row.kind === 'overlay' ? (hidden ? '◌' : '◉') : hidden ? '🔇' : '🔊'}</span>
                      </button>
                    )}
                  </div>
                  {/* The lane. */}
                  <div
                    ref={(el) => {
                      if (el) lanes.current.set(track.id, el)
                      else lanes.current.delete(track.id)
                    }}
                    className={`relative ${hidden ? 'opacity-50' : ''}`}
                    style={{ width: contentW }}
                    onPointerDown={onLaneDown}
                    onPointerMove={onRulerMove}
                    onPointerUp={onRulerUp}
                    onDragOver={(e) => onDragOver(e, row)}
                    onDrop={(e) => onDrop(e, row)}
                  >
                    <canvas
                      ref={(el) => {
                        if (el) canvases.current.set(track.id, el)
                        else canvases.current.delete(track.id)
                      }}
                      className="block pointer-events-none"
                      style={{ position: 'sticky', left: HEADER_W, width: laneW, height: row.height }}
                      aria-hidden="true"
                    />
                    {items.map((it) => {
                      const g = ghost && ghost.id === it.id && ghost.moved ? ghost : null
                      const trimming = g !== null && g.mode !== 'move'
                      const freeMove = g !== null && g.mode === 'move' && g.targetTrack === track.id && track.id !== 'V1'
                      const start = trimming || freeMove ? (g as Drag).start : it.start_us
                      const end = trimming || freeMove ? (g as Drag).end : itemEnd(it)
                      const info = meta.get(it.id)
                      return (
                        <ItemButton
                          key={it.id}
                          id={it.id}
                          kind={row.kind}
                          x={usToPx(start, pps)}
                          w={Math.max(2, usToPx(end - start, pps))}
                          on={selectedSet.has(it.id)}
                          away={g !== null && g.mode === 'move' && !freeMove}
                          readOnly={readOnly}
                          say={info?.say ?? it.id}
                          title={info?.title ?? it.id}
                          text={info?.text ?? it.id}
                          handlers={handlers}
                        />
                      )
                    })}
                    {/* Where a dragged item lands: on another track, its ghost;
                        on the main track, a line before the item it goes in
                        front of. */}
                    {ghost && ghost.moved && ghost.mode === 'move' && ghost.targetTrack === track.id && ghost.trackId !== track.id && track.id !== 'V1' && (
                      <div
                        className="absolute top-px bottom-px rounded-[4px] border border-dashed border-accent bg-accent/10 pointer-events-none"
                        style={{ left: usToPx(ghost.start, pps), width: Math.max(2, usToPx(ghost.end - ghost.start, pps)) }}
                        aria-hidden="true"
                      />
                    )}
                    {ghost && ghost.moved && ghost.mode === 'move' && ghost.targetTrack === 'V1' && track.id === 'V1' && (
                      <div
                        className="absolute top-0 bottom-0 w-0.5 -ml-px bg-accent pointer-events-none"
                        style={{ left: usToPx(markerUs(ghost), pps) }}
                        aria-hidden="true"
                      />
                    )}
                  </div>
                </div>
                {/* Step 9's caption lane goes here, under the main track. */}
              </div>
            )
          })}

          {/* The playhead. */}
          <div
            ref={line}
            className="absolute top-0 bottom-0 left-0 w-px bg-ink z-[5] pointer-events-none"
            style={{ transform: `translateX(${HEADER_W}px)` }}
            aria-hidden="true"
          >
            <div className="absolute -left-1 top-0 w-2.5 h-2.5 bg-ink rotate-45 -translate-y-1" />
          </div>
        </div>
      </div>
    </section>
  )
})

export default TimelinePanel
