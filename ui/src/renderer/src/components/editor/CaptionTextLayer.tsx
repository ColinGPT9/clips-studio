/** Captions and text on the viewer, as a layer you select, drag, resize and
 *  edit in place, as in CapCut (step 9).
 *
 *  - Click a caption: the caption layer is selected (a box with handles
 *    round the caption on screen). Dragging moves ALL captions together (the
 *    caption style's x and y); a corner handle sets the font size.
 *  - Click a text: that text is selected; drag moves it ({x, y}), a corner
 *    sets its size. Each text moves on its own.
 *  - While dragging: the centre lines show at once; the box snaps to them,
 *    to the five named rows (lib/captionLayer.ts SNAP_ROWS), the frame's
 *    edges, the chosen app's safe lines and the edges of what it draws, and
 *    the other layers' edges. Alt held turns snapping off. On a 9:16 video
 *    the chosen app's screen is drawn over the picture during the drag
 *    (faintly, the app last chosen, when the picker says "No overlay"). On
 *    other canvases the apps' screens aren't drawn (they are 9:16), as the
 *    picker already says.
 *  - A caption or text under the app's buttons, bottom text or top bar gets
 *    an amber outline and a tooltip naming the app. It is never moved for
 *    you.
 *  - Keyboard: the selected box takes focus; the arrow keys move it (Shift
 *    moves further), and the new place is announced.
 *  - Double-click: type into it where it is. Enter keeps, Esc cancels,
 *    Shift+Enter is a new line in a text.
 *  - Each drag, resize or edit is one change (one undo step). While it is
 *    under way, and until the engine has it, JASSUB draws the moved layer
 *    (lib/captionLayer.ts placeEvents) and the paused still is hidden, so
 *    what you see never jumps back.
 *
 *  Where a caption or a text is comes from the engine's own ASS file: the
 *  boxes are measured estimates of what libass draws (the still is the
 *  truth). Positions go to the engine as % of the frame, as
 *  docs/editor-suite/api/step9-captions.md says. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { captionFontWeight, useBundledFonts } from '../../lib/captionLook'
import {
  appAreas,
  captionBounds,
  captionNear,
  centrePercent,
  eventBox,
  heldCentre,
  parseAss,
  placeEvents,
  resized,
  layerTargets,
  rowAt,
  textEventItems,
  underApp,
  withoutEvents,
  type AppAreaKind,
  type AppShape,
  type AssEvent,
  type Box,
  type ParsedAss
} from '../../lib/captionLayer'
import { dragLayer, type Targets } from '../../lib/layerDrag'
import { laneRevisionFor, savedLines, wordsEdit, wordsToEdit } from '../../lib/captionLane'
import { usePlayheadTime, type PlayheadStore } from '../../lib/editorPlayhead'
import type { CaptionLane, Op, OpsAnswer, ProjectDoc } from '../../lib/editorTypes'
import { lastPlatformApp, type PlatformChoice } from '../../lib/newEditor'
import { SAFE_ZONES } from '../../lib/gamingLayout'
import PlatformOverlay, { PLATFORM_UI } from '../PlatformOverlay'

/** What is selected on the viewer: the caption layer, a text item, or neither. */
export type LayerTarget = { kind: 'captions' } | { kind: 'text'; id: string }

/** Where the caption layer's and a text's size may go: the sizes the engine
 *  draws (it takes 20 to 200 for captions but draws 40 to 140). */
const CAPTION_SIZE: [number, number] = [40, 140]
const TEXT_SIZE: [number, number] = [8, 400]
/** Arrow keys move a layer this many % of the frame; with Shift, NUDGE_FAR. */
const NUDGE = 1
const NUDGE_FAR = 5
/** Snapping distance on screen, in px. */
const SNAP_PX = 6
const GUIDE = '#F472B6'

/** The tooltip for a layer under an app's screen, per app and part. */
const UNDER: Record<string, Record<AppAreaKind, string>> = {
  tiktok: {
    buttons: "Under TikTok's buttons",
    bottom: "Under TikTok's text at the bottom",
    top: "Under TikTok's top bar"
  },
  reels: {
    buttons: "Under Instagram Reels' buttons",
    bottom: "Under Instagram Reels' text at the bottom",
    top: "Under Instagram Reels' top bar"
  },
  shorts: {
    buttons: "Under YouTube Shorts' buttons",
    bottom: "Under YouTube Shorts' text at the bottom",
    top: "Under YouTube Shorts' top bar"
  }
}

let measureCtx: CanvasRenderingContext2D | null = null
/** A line's width in a font, measured the way the page draws it (the
 *  bundled fonts are registered by lib/captionLook.ts). */
function measure(text: string, font: string, sizePx: number, bold: boolean): number {
  if (!measureCtx) measureCtx = document.createElement('canvas').getContext('2d')
  if (!measureCtx) return text.length * sizePx * 0.55
  const weight = bold ? captionFontWeight(font) : 400
  measureCtx.font = `${weight} ${sizePx}px "${font}", "Liberation Sans", Arial, sans-serif`
  return measureCtx.measureText(text).width
}

interface Drag {
  target: LayerTarget
  mode: 'move' | 'resize'
  start: Box
  ox: number
  oy: number
  startDist: number
  size: number
  pointer: number
}

/** A change made here that the engine hasn't drawn yet: shown until it has. */
interface Pending {
  target: LayerTarget
  at: [number, number] | null
  fs: number
  revision: number | null
}

export default function CaptionTextLayer({
  doc,
  revision,
  lane,
  ass,
  playhead,
  platform,
  vertical,
  selection,
  onSelect,
  apply,
  readOnly,
  captionsLocked = false,
  onLive,
  onBusy
}: {
  doc: ProjectDoc
  revision: number
  lane: CaptionLane | null
  /** The engine's ASS file for `lane.revision`. */
  ass: string | null
  playhead: PlayheadStore
  platform: PlatformChoice
  vertical: boolean
  selection: LayerTarget | null
  onSelect: (target: LayerTarget | null) => void
  apply: (ops: Op[], label?: string, at?: number | null) => Promise<OpsAnswer | null>
  readOnly: boolean
  /** The caption lane's Lock: the captions can't be moved, resized or typed into. */
  captionsLocked?: boolean
  /** The script JASSUB should draw while a layer moves (null: the engine's). */
  onLive: (ass: string | null) => void
  /** A drag, an edit or a change on its way: the paused still is hidden. */
  onBusy: (busy: boolean) => void
}): JSX.Element | null {
  const W = doc.canvas.width
  const H = doc.canvas.height
  const fonts = useBundledFonts()
  const now = usePlayheadTime(playhead) / 1e6
  const parsed = useMemo<ParsedAss | null>(() => (ass ? parseAss(ass) : null), [ass])
  const fresh = lane !== null && lane.revision === revision
  // Which text item each Dialogue: 1 event draws (compose.build_ass's order).
  const textIds = useMemo(() => textEventItems(doc.tracks), [doc.tracks])
  const textIndex = useMemo(() => {
    const out = new Map<string, number>()
    if (!parsed) return out
    const evs = parsed.events.map((e, i) => ({ e, i })).filter(({ e }) => e.layer === 1)
    if (evs.length !== textIds.length) return out
    textIds.forEach((id, k) => out.set(id, evs[k].i))
    return out
  }, [parsed, textIds])

  const layerRef = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const [moving, setMoving] = useState<{ target: LayerTarget; at: [number, number] | null; fs: number } | null>(null)
  const [pending, setPending] = useState<Pending | null>(null)
  const [guides, setGuides] = useState<Targets>({ xs: [], ys: [] })
  const [dragging, setDragging] = useState(false)
  const [editing, setEditing] = useState<{
    target: LayerTarget
    lineId: string
    text: string
    box: Box
    font: string
    size: number
    upper: boolean
    /** Program seconds whose caption events are hidden while typing (the line's pieces). */
    hide: [number, number][]
  } | null>(null)
  const [announce, setAnnounce] = useState('')
  const nudgeTimer = useRef<number | undefined>()
  const boxFocus = useRef<HTMLDivElement>(null)

  // The caption style as drawn: the project's own (current with every
  // answer), else the lane's (the settings' style, as the engine draws it).
  const own = doc.captions?.style
  const style: Record<string, unknown> =
    own && typeof own === 'object' ? (own as Record<string, unknown>) : (lane?.style ?? {})

  // ---- the layers' places in the script, with what is moving put where it is going ----
  const shown = moving ?? (pending ? { target: pending.target, at: pending.at, fs: pending.fs } : null)
  const which = (target: LayerTarget) => (e: AssEvent, index: number): boolean =>
    target.kind === 'captions' ? e.layer === 0 : textIndex.get(target.id) === index
  // A dragged caption is kept inside the frame (and the safe zone) by the
  // engine, each caption by its own size: the live picture does the same,
  // so the words don't jump when they are dropped (9-D2).
  const bounds = captionBounds(style, W, H)
  const holdCaption = (p: ParsedAss, fs: number) => (e: AssEvent, at: [number, number]): [number, number] => {
    const b = eventBox(p, e, measure)
    return heldCentre(at, [b[2] * fs, b[3] * fs], bounds)
  }
  const liveAss = useMemo(() => {
    if (!parsed) return null
    // Typing into a caption or a text: the words are in the box instead.
    if (editing) {
      const ed = editing
      return withoutEvents(parsed, (e, index) =>
        ed.target.kind === 'captions'
          ? e.layer === 0 && ed.hide.some(([a, b]) => e.start < b && e.end > a)
          : textIndex.get(ed.target.id) === index
      )
    }
    if (!shown) return null
    const hold = shown.target.kind === 'captions' ? holdCaption(parsed, shown.fs) : undefined
    return placeEvents(parsed, which(shown.target), shown.at, shown.fs, hold)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [parsed, editing, shown?.target, shown?.at?.[0], shown?.at?.[1], shown?.fs, textIndex, bounds.join(','), fonts])
  const liveParsed = useMemo(() => (liveAss ? parseAss(liveAss) : parsed), [liveAss, parsed])

  useEffect(() => onLive(liveAss), [liveAss, onLive])
  const busy = dragging || editing !== null || pending !== null
  useEffect(() => onBusy(busy), [busy, onBusy])
  useEffect(() => () => onBusy(false), [onBusy])

  // A change the engine has drawn (or refused) is no longer pending.
  useEffect(() => {
    if (pending && pending.revision !== null && lane && lane.revision >= pending.revision && ass) setPending(null)
  }, [lane, ass, pending])

  // ---- the boxes on screen now ----
  const boxOf = (p: ParsedAss | null, e: AssEvent | null): Box | null => (p && e ? eventBox(p, e, measure) : null)
  const cap = liveParsed ? captionNear(liveParsed, now) : null
  const captionBox = boxOf(liveParsed, cap?.event ?? null)
  const texts: { id: string; box: Box; event: AssEvent }[] = []
  if (liveParsed) {
    for (const [id, index] of textIndex) {
      const e = liveParsed.events[index]
      if (e && e.start <= now && now < e.end) texts.push({ id, box: eventBox(liveParsed, e, measure), event: e })
    }
  }
  // Fonts that arrive change the measurements: draw again (the list is a dependency).
  void fonts

  const selectedBox: Box | null =
    selection?.kind === 'captions'
      ? captionBox
      : selection?.kind === 'text'
        ? (texts.find((x) => x.id === selection.id)?.box ?? null)
        : null

  // ---- the app over the picture ----
  const dragApp = platform !== 'none' ? platform : lastPlatformApp()
  const app = vertical ? (dragging ? dragApp : platform !== 'none' ? platform : null) : null
  const areas = useMemo(() => {
    if (!app || !SAFE_ZONES[app] || !PLATFORM_UI[app]) return []
    return appAreas(SAFE_ZONES[app], PLATFORM_UI[app] as unknown as AppShape[], W, H)
  }, [app, W, H])
  const underOf = (box: Box | null): string => {
    if (!box || !app || areas.length === 0) return ''
    const kinds = underApp(box, areas, H)
    return kinds.map((k) => t(UNDER[app]?.[k] ?? '')).filter(Boolean).join(' · ')
  }

  // ---- where things are, in % ----
  const styleAt = (): [number, number] | null => {
    const x = style.x
    const y = style.y
    return typeof x === 'number' && typeof y === 'number' ? [x, y] : null
  }
  const itemOf = (id: string) => doc.tracks.flatMap((tr) => tr.items).find((it) => it.id === id) ?? null
  const placeOf = (target: LayerTarget, box: Box | null): { x: number; y: number } | null => {
    if (target.kind === 'captions') {
      const s = styleAt()
      if (s) return { x: s[0], y: s[1] }
    } else {
      const pos = itemOf(target.id)?.text?.position as { x?: unknown; y?: unknown } | null | undefined
      if (pos && typeof pos === 'object' && typeof pos.x === 'number' && typeof pos.y === 'number') return { x: pos.x, y: pos.y }
    }
    return box ? centrePercent(box, W, H) : null
  }
  const sizeOf = (target: LayerTarget): number => {
    if (target.kind === 'captions') return typeof style.font_size === 'number' ? style.font_size : 84
    const s = itemOf(target.id)?.text?.size
    return typeof s === 'number' ? s : 96
  }

  // ---- sending a change ----
  const send = async (target: LayerTarget, change: { x: number; y: number } | { size: number }, local: Pending): Promise<void> => {
    let ops: Op[]
    let label: string
    if (target.kind === 'captions') {
      const base: Record<string, unknown> = { ...style }
      if ('size' in change) base.font_size = change.size
      else {
        base.x = change.x
        base.y = change.y
      }
      ops = [{ op: 'set_captions', style: base }]
      label = 'size' in change ? t('Resize captions') : t('Move captions')
    } else {
      ops = [{ op: 'set_text', item_id: target.id, ...('size' in change ? { size: change.size } : { position: { x: change.x, y: change.y } }) }]
      label = 'size' in change ? t('Resize text') : t('Move text')
    }
    await commit(ops, label, local)
  }
  /** One change, shown as made (`local`) until the engine has drawn it. */
  const commit = async (ops: Op[], label: string, local: Pending, at: number | null = null): Promise<void> => {
    setPending({ ...local, revision: null })
    const answer = await apply(ops, label, at)
    // Refused (a 409 reloads the project and says so): nothing to wait for.
    if (!answer || !answer.changed) setPending(null)
    else setPending({ ...local, revision: answer.revision })
  }

  // ---- pointer ----
  const toCanvas = (e: { clientX: number; clientY: number }): [number, number] => {
    const r = layerRef.current?.getBoundingClientRect()
    if (!r || r.width === 0) return [0, 0]
    return [((e.clientX - r.left) / r.width) * W, ((e.clientY - r.top) / r.height) * H]
  }
  const scale = (): number => {
    const r = layerRef.current?.getBoundingClientRect()
    return r && W ? r.width / W : 1
  }
  const begin = (e: React.PointerEvent, target: LayerTarget, box: Box, mode: 'move' | 'resize'): void => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    onSelect(target)
    // Focus on the box now (the arrow keys move it). Not later: a
    // double-click's editor takes the focus right after.
    ;(e.currentTarget as HTMLElement).closest<HTMLElement>('[role="button"]')?.focus()
    if (readOnly || editing || (target.kind === 'captions' && captionsLocked)) return
    const [ox, oy] = toCanvas(e)
    const cx = box[0] + box[2] / 2
    const cy = box[1] + box[3] / 2
    drag.current = { target, mode, start: box, ox, oy, startDist: Math.hypot(ox - cx, oy - cy), size: sizeOf(target), pointer: e.pointerId }
  }
  const others = (target: LayerTarget): Box[] => {
    const out: Box[] = []
    if (target.kind !== 'captions' && captionBox && cap?.showing) out.push(captionBox)
    for (const x of texts) if (target.kind !== 'text' || x.id !== target.id) out.push(x.box)
    return out
  }
  const onMove = (e: React.PointerEvent): void => {
    const d = drag.current
    if (!d) return
    const [px, py] = toCanvas(e)
    if (!dragging) {
      if (Math.hypot(px - d.ox, py - d.oy) * scale() < 3) return // a click, not a drag
      setDragging(true)
      // Held from here on, even off the picture. (Not at the press: a
      // double-click must still reach the box.)
      layerRef.current?.setPointerCapture(e.pointerId)
    }
    if (d.mode === 'move') {
      const zone = vertical ? (SAFE_ZONES[dragApp] ?? null) : null
      const dragAreas = vertical && SAFE_ZONES[dragApp] && PLATFORM_UI[dragApp]
        ? appAreas(SAFE_ZONES[dragApp], PLATFORM_UI[dragApp] as unknown as AppShape[], W, H)
        : []
      const { box, guides: lines } = dragLayer(d.start, 'move', px - d.ox, py - d.oy, {
        aspect: null,
        canvas: [W, H],
        minW: 1,
        minH: 1,
        targets: e.altKey ? null : layerTargets(W, H, zone, dragAreas, others(d.target)), // hold Alt to place freely
        tol: SNAP_PX / scale()
      })
      setGuides(lines)
      setMoving({ target: d.target, at: [box[0] + box[2] / 2, box[1] + box[3] / 2], fs: 1 })
    } else {
      const cx = d.start[0] + d.start[2] / 2
      const cy = d.start[1] + d.start[3] / 2
      const [lo, hi] = d.target.kind === 'captions' ? CAPTION_SIZE : TEXT_SIZE
      const size = resized(d.size, d.startDist, Math.hypot(px - cx, py - cy), lo, hi)
      setGuides({ xs: [], ys: [] })
      // A caption placed by name grows from where it stands; one with a place, from its middle.
      const placed = d.target.kind === 'captions' ? styleAt() !== null : placeOf(d.target, null) !== null
      setMoving({ target: d.target, at: placed ? [cx, cy] : null, fs: size / d.size })
    }
  }
  const onUp = (e: React.PointerEvent): void => {
    const d = drag.current
    drag.current = null
    if (!d) return
    if (layerRef.current?.hasPointerCapture?.(d.pointer)) layerRef.current.releasePointerCapture(d.pointer)
    const was = moving
    setDragging(false)
    setGuides({ xs: [], ys: [] })
    setMoving(null)
    if (!was || !dragging) return
    if (d.mode === 'move' && was.at) {
      const [cx, cy] = was.at
      const pc = centrePercent([cx, cy, 0, 0], W, H)
      void send(d.target, pc, { target: d.target, at: was.at, fs: 1, revision: null })
      setAnnounce(`${t('Moved to')} ${pc.x}% ${t('across')}, ${pc.y}% ${t('down')}`)
    } else if (d.mode === 'resize') {
      const size = Math.round(d.size * was.fs)
      if (size !== d.size) {
        void send(d.target, { size }, { target: d.target, at: was.at, fs: was.fs, revision: null })
        setAnnounce(`${t('Size')} ${size}`)
      }
    }
    void e
  }

  // ---- keyboard: the arrow keys move the selected layer ----
  const nudge = (e: React.KeyboardEvent): void => {
    if (!selection || !selectedBox || readOnly || editing || (selection.kind === 'captions' && captionsLocked)) return
    const dir: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
    if (e.key === 'Enter') {
      e.preventDefault()
      e.stopPropagation()
      startEdit(selection, selectedBox)
      return
    }
    const d = dir[e.key]
    if (!d) return
    e.preventDefault()
    e.stopPropagation() // not the viewer's frame step
    const step = e.shiftKey ? NUDGE_FAR : NUDGE
    const from = shown && shown.at && shown.target.kind === selection.kind
      ? centrePercent([shown.at[0], shown.at[1], 0, 0], W, H)
      : placeOf(selection, selectedBox)
    if (!from) return
    const to = {
      x: Math.min(100, Math.max(0, Math.round((from.x + d[0] * step) * 100) / 100)),
      y: Math.min(100, Math.max(0, Math.round((from.y + d[1] * step) * 100) / 100))
    }
    const at: [number, number] = [(to.x * W) / 100, (to.y * H) / 100]
    setMoving({ target: selection, at, fs: 1 })
    setAnnounce(`${t('Moved to')} ${to.x}% ${t('across')}, ${to.y}% ${t('down')}`)
    // Sent when the keys rest, as one change.
    window.clearTimeout(nudgeTimer.current)
    const target = selection
    nudgeTimer.current = window.setTimeout(() => {
      setMoving(null)
      void send(target, to, { target, at, fs: 1, revision: null })
    }, 400)
  }
  useEffect(() => () => window.clearTimeout(nudgeTimer.current), [])

  // ---- editing in place ----
  /** The caption line playing at `us` (program time). */
  const lineAt = (us: number): { id: string; text: string; hide: [number, number][] } | null => {
    if (!fresh || !lane) return null
    const found = lane.lines.find((l) => !l.hidden && l.pieces.some((p) => p.start_us <= us && us < p.end_us))
    // A line an edit cut off shows (and saves) only the words that play (9-D6).
    return found ? { id: found.id, text: wordsToEdit(found, lane.spans), hide: found.pieces.map((p) => [p.start_us / 1e6, p.end_us / 1e6]) } : null
  }
  const startEdit = (target: LayerTarget, box: Box): void => {
    if (readOnly || !liveParsed || (target.kind === 'captions' && captionsLocked)) return
    if (target.kind === 'captions') {
      // The caption in the box: the one on screen, else the nearest (the
      // playhead stays where it is either way).
      const near = captionNear(liveParsed, now)
      if (!near) return
      const e = near.event
      const line = near.showing ? lineAt(playhead.get()) : lineAt(Math.round(((e.start + e.end) / 2) * 1e6))
      if (!line) return
      const st = liveParsed.styles[e.style]
      setEditing({ target, lineId: line.id, text: line.text, box, font: st?.font ?? 'Arial', size: st?.size ?? 84, upper: style.uppercase !== false, hide: line.hide })
    } else {
      const item = itemOf(target.id)
      const index = textIndex.get(target.id)
      if (!item || index === undefined) return
      const st = liveParsed.styles[liveParsed.events[index]?.style ?? '']
      setEditing({ target, lineId: '', text: item.text?.content ?? '', box, font: st?.font ?? 'Arial', size: st?.size ?? 96, upper: false, hide: [] })
    }
  }
  const finishEdit = async (keep: boolean, text: string): Promise<void> => {
    const ed = editing
    setEditing(null)
    window.setTimeout(() => boxFocus.current?.focus(), 0)
    if (!ed || !keep) return
    if (ed.target.kind === 'captions') {
      const words = text.replace(/\s*\n\s*/g, ' ').trim()
      if (words === ed.text.trim() || !lane) return
      const line = lane.lines.find((l) => l.id === ed.lineId)
      if (!line) return
      const edit: Op = { op: 'edit_caption_words', edits: [wordsEdit(line, lane.spans, words)] }
      // Lines made from the transcript are saved first, with their ids, in the same change.
      const ops: Op[] = lane.saved ? [edit] : [{ op: 'set_captions', lines: savedLines(lane.lines) }, edit]
      // Saved whole (lines from the transcript): at the lane's revision (9-D13).
      await commit(ops, t('Edit caption'), { target: ed.target, at: null, fs: 1, revision: null }, laneRevisionFor(ops, lane.revision))
    } else {
      const content = text.replace(/\r/g, '').trim()
      if (!content || content === ed.text) return
      await commit([{ op: 'set_text', item_id: ed.target.id, content }], t('Edit text'), { target: ed.target, at: null, fs: 1, revision: null })
    }
  }

  // Esc lets go of the selection (EditorScreen's Escape does the rest).
  if (!parsed) return null

  const k = scale()
  const css = (b: Box): React.CSSProperties => ({
    left: `${(b[0] / W) * 100}%`,
    top: `${(b[1] / H) * 100}%`,
    width: `${(b[2] / W) * 100}%`,
    height: `${(b[3] / H) * 100}%`
  })
  const handles: [string, string][] = [
    ['nw', '-left-1.5 -top-1.5 cursor-nwse-resize'],
    ['ne', '-right-1.5 -top-1.5 cursor-nesw-resize'],
    ['sw', '-left-1.5 -bottom-1.5 cursor-nesw-resize'],
    ['se', '-right-1.5 -bottom-1.5 cursor-nwse-resize']
  ]
  const isSel = (target: LayerTarget): boolean =>
    selection !== null && selection.kind === target.kind && (target.kind === 'captions' || (selection.kind === 'text' && selection.id === target.id))
  const showOutlines = dragging || !playhead.playing()

  // The layers you can click: the caption on screen (or the selected caption
  // layer's nearest caption), and each text on screen.
  const hits: { target: LayerTarget; box: Box; dim: boolean }[] = []
  if (captionBox && cap && (cap.showing || selection?.kind === 'captions')) hits.push({ target: { kind: 'captions' }, box: captionBox, dim: !cap.showing })
  for (const x of texts) hits.push({ target: { kind: 'text', id: x.id }, box: x.box, dim: false })
  const selPlace = selection && selectedBox ? placeOf(selection, selectedBox) : null
  const nameOf = (target: LayerTarget): string =>
    target.kind === 'captions'
      ? t('Captions. Drag to move them all, or use the arrow keys (Shift moves further). Double-click to edit the words.')
      : t('Text. Drag to move it, or use the arrow keys (Shift moves further). Double-click to edit it.')

  return (
    <div
      ref={layerRef}
      className="absolute inset-0"
      onPointerDown={(e) => {
        if (e.button === 0 && !editing) onSelect(null)
      }}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      {/* The app's screen while dragging, faintly when the picker says "No overlay". */}
      {dragging && vertical && platform === 'none' && PLATFORM_UI[dragApp] && (
        <div className="absolute inset-0 pointer-events-none opacity-45">
          <PlatformOverlay platform={dragApp} />
        </div>
      )}
      {/* The centre lines, from the start of a drag; the lines it snapped to, strong. */}
      {dragging && (
        <svg className="absolute inset-0 w-full h-full pointer-events-none" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
          <line x1={W / 2} x2={W / 2} y1={0} y2={H} stroke={GUIDE} strokeOpacity={0.45} strokeWidth={2 / k} strokeDasharray={`${8 / k} ${6 / k}`} />
          <line x1={0} x2={W} y1={H / 2} y2={H / 2} stroke={GUIDE} strokeOpacity={0.45} strokeWidth={2 / k} strokeDasharray={`${8 / k} ${6 / k}`} />
          {guides.xs.map((gx) => (
            <line key={`gx${gx}`} x1={gx} x2={gx} y1={0} y2={H} stroke={GUIDE} strokeWidth={2 / k} />
          ))}
          {guides.ys.map((gy) => (
            <line key={`gy${gy}`} x1={0} x2={W} y1={gy} y2={gy} stroke={GUIDE} strokeWidth={2 / k} />
          ))}
        </svg>
      )}
      {dragging &&
        guides.ys.map((gy) => {
          const row = rowAt(gy, H)
          return row ? (
            <span
              key={`row${gy}`}
              className="absolute left-1 -translate-y-full text-[10px] font-medium px-1 rounded bg-pink-400 text-black pointer-events-none"
              style={{ top: `${(gy / H) * 100}%` }}
            >
              {t(row.label)}
            </span>
          ) : null
        })}

      {/* The layers on screen: click to select, double-click to edit. */}
      {hits.map(({ target, box, dim }) => {
        const sel = isSel(target)
        const under = showOutlines ? underOf(box) : ''
        const key = target.kind === 'captions' ? 'captions' : target.id
        return (
          <div
            key={key}
            ref={sel ? boxFocus : undefined}
            className={`absolute rounded-sm ${readOnly ? 'cursor-default' : 'cursor-move'} outline-none ${
              under
                ? 'ring-2 ring-amber-400'
                : sel
                  ? `ring-2 ${dim ? 'ring-accent/50' : 'ring-accent'}`
                  : 'hover:ring-1 hover:ring-white/60'
            } focus-visible:ring-4`}
            style={css(box)}
            title={under || nameOf(target)}
            aria-label={sel && selPlace ? `${nameOf(target)} ${selPlace.x}% ${t('across')}, ${selPlace.y}% ${t('down')}` : nameOf(target)}
            role="button"
            // Reached with Tab too: focusing one selects it.
            tabIndex={0}
            onFocus={() => {
              if (!sel) onSelect(target)
            }}
            onPointerDown={(e) => begin(e, target, box, 'move')}
            onDoubleClick={(e) => {
              e.stopPropagation()
              onSelect(target)
              startEdit(target, box)
            }}
            onKeyDown={sel ? nudge : undefined}
          >
            {sel &&
              !readOnly &&
              !(target.kind === 'captions' && captionsLocked) &&
              handles.map(([id, place]) => (
                <span
                  key={id}
                  className={`absolute w-3 h-3 rounded-sm bg-white border border-accent ${place}`}
                  onPointerDown={(e) => begin(e, target, box, 'resize')}
                  title={target.kind === 'captions' ? t('Drag to make the captions bigger or smaller') : t('Drag to make the text bigger or smaller')}
                />
              ))}
          </div>
        )
      })}

      {/* Typing into a caption or a text where it is. */}
      {editing && (
        <InPlaceEditor
          key={`${editing.target.kind}${editing.lineId}`}
          text={editing.text}
          box={editing.box}
          W={W}
          H={H}
          font={editing.font}
          size={editing.size}
          multiline={editing.target.kind === 'text'}
          upper={editing.upper}
          scale={k}
          onDone={(keep, text) => void finishEdit(keep, text)}
        />
      )}
      <span className="sr-only" aria-live="polite">
        {announce}
      </span>
    </div>
  )
}

function InPlaceEditor({
  text,
  box,
  W,
  H,
  font,
  size,
  multiline,
  upper,
  scale,
  onDone
}: {
  text: string
  box: Box
  W: number
  H: number
  font: string
  size: number
  multiline: boolean
  /** Drawn in capitals, as the captions burn (the words are kept as typed). */
  upper: boolean
  scale: number
  onDone: (keep: boolean, text: string) => void
}): JSX.Element {
  const ref = useRef<HTMLTextAreaElement>(null)
  const [value, setValue] = useState(text)
  const done = useRef(false)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const finish = (keep: boolean): void => {
    if (done.current) return
    done.current = true
    onDone(keep, ref.current?.value ?? value)
  }
  const width = Math.min(W * 0.96, Math.max(box[2], W * 0.4))
  const rows = Math.max(1, value.split('\n').length)
  const cx = box[0] + box[2] / 2
  const cy = box[1] + box[3] / 2
  return (
    <textarea
      ref={ref}
      value={value}
      rows={rows}
      onChange={(e) => setValue(e.target.value)}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape') {
          e.preventDefault()
          finish(false)
        } else if (e.key === 'Enter' && !(multiline && e.shiftKey)) {
          e.preventDefault()
          finish(true)
        }
      }}
      onBlur={() => finish(true)}
      aria-label={multiline ? t('Edit the text. Enter keeps it, Esc cancels, Shift+Enter starts a new line.') : t('Edit the caption. Enter keeps it, Esc cancels.')}
      className="absolute resize-none overflow-hidden text-center bg-black/40 text-white rounded outline-none ring-2 ring-accent leading-tight"
      style={{
        left: `${((cx - width / 2) / W) * 100}%`,
        width: `${(width / W) * 100}%`,
        top: `${((cy - (rows * size) / 2) / H) * 100}%`,
        fontFamily: `"${font}", "Liberation Sans", Arial, sans-serif`,
        fontWeight: captionFontWeight(font),
        fontSize: `${size * 0.87 * scale}px`,
        textTransform: upper ? 'uppercase' : undefined,
        textShadow: '0 0 3px #000, 0 0 3px #000'
      }}
    />
  )
}
