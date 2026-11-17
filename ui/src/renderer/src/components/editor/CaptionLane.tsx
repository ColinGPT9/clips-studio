/** The caption lane: its own row of the timeline, under the main track, as
 *  in CapCut (step 9, part C). One block per caption line, where it plays in
 *  the program (from GET /projects/{id}/captions, the same remap the export
 *  burns; a part cut out by an edit has no block).
 *
 *  - Click a block: that line is selected (and the viewer's caption layer).
 *  - Drag an edge to retime it, or the block to move it. It snaps to the
 *    cuts, the playhead, the items' edges and the other blocks, and never
 *    goes over another caption or across a cut. Esc cancels a drag.
 *  - Double-click: type its words. Enter keeps, Esc cancels.
 *  - Split at the playhead (S), merge with the next line, delete (Delete),
 *    add a caption at the playhead: the timeline's toolbar and keys call
 *    this lane's handle.
 *  - The header: Hide (the project's captions switch) and Lock (no edits on
 *    the lane or to the captions on the viewer; kept while the editor is
 *    open, not in the project).
 *
 *  Each action is one change, one undo step (lib/captionLane.ts says what is
 *  sent). A dropped block's program times become source times through the
 *  engine (captions/source_times), so it never lands a frame off. */
import { forwardRef, memo, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { editorApi } from '../../lib/editorApi'
import { formatTime, frameAt, frameUs } from '../../lib/editorProgram'
import type { PlayheadStore } from '../../lib/editorPlayhead'
import type { CaptionLane as Lane, Fps, Op, OpsAnswer, ProjectDoc } from '../../lib/editorTypes'
import { pxToUs, snapPoints, snapTo, usToPx } from '../../lib/editorTimeline'
import {
  addLine,
  blockAt,
  blocksIn,
  blocksOf,
  cutBeforeNext,
  dragBlock,
  firstLineAt,
  laneRevisionFor,
  mergeLines,
  newBlockAt,
  nextLine,
  retimeEdit,
  room,
  splitLine,
  trimmedLine,
  withSave,
  wordsEdit,
  wordsToEdit,
  type Block,
  type MainItem
} from '../../lib/captionLane'

export interface CaptionLaneHandle {
  /** S: split the selected caption at the playhead. False when no caption
   *  line is selected (the timeline's items are split then). */
  split: () => boolean
  /** Delete: remove the selected caption line. */
  remove: () => boolean
  /** Make the selected line and the next one a single line. */
  merge: () => void
  /** Add a caption line at the playhead, and type its words. */
  add: () => void
  /** Alt+← →: move the selected caption a frame (big: a second) earlier or
   *  later, as dragging it does. False when no caption line is selected
   *  (the timeline's items are moved then). */
  nudge: (dir: 1 | -1, big: boolean) => boolean
  /** The inspector's Starts at / Ends at: the selected caption's new program
   *  times. Only the edge that changed moves. */
  retime: (start: number, end: number) => void
}

/** How long a caption added at the playhead is, at most (up to the next one). */
export const NEW_CAPTION_US = 2_000_000
export const LANE_H = 28
const EDGE_PX = 7
const SNAP_PX = 8
const DRAG_PX = 3

interface Props {
  projectId: string
  doc: ProjectDoc
  revision: number
  lane: Lane | null
  playhead: PlayheadStore
  pps: number
  contentW: number
  headerW: number
  /** The times whose blocks are buttons (the timeline's window). */
  win: { from: number; to: number }
  snapOn: boolean
  selectedLine: string | null
  onSelectLine: (id: string | null) => void
  onSeek: (tUs: number) => void
  apply: (ops: Op[], label?: string, at?: number | null) => Promise<OpsAnswer | null>
  readOnly: boolean
  locked: boolean
  onLock: (locked: boolean) => void
}

interface Drag {
  block: Block
  mode: 'left' | 'right' | 'move'
  x0: number
  moved: boolean
  start: number
  end: number
  pointer: number
}

/** The frame edge nearest t. */
function onFrame(tUs: number, fps: Fps): number {
  const n = frameAt(tUs, fps)
  const a = frameUs(n, fps)
  const b = frameUs(n + 1, fps)
  return tUs - a <= b - tUs ? a : b
}

const CaptionLane = forwardRef<CaptionLaneHandle, Props>(function CaptionLane(
  { projectId, doc, revision, lane, playhead, pps, contentW, headerW, win, snapOn, selectedLine, onSelectLine, onSeek, apply, readOnly, locked, onLock },
  ref
) {
  const fps = doc.canvas.fps
  const fresh = lane !== null && lane.revision === revision
  const lines = useMemo(() => lane?.lines ?? [], [lane])
  const blocks = useMemo(() => blocksOf(lines), [lines])
  const spans = lane?.spans ?? []
  const canEdit = fresh && !readOnly && !locked
  const shown = lane ? lane.burned : true
  const laneEl = useRef<HTMLDivElement>(null)
  const drag = useRef<Drag | null>(null)
  const [ghost, setGhost] = useState<Drag | null>(null)
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null)
  /** A line just added: its words are typed as soon as its block is drawn. */
  const [typeNew, setTypeNew] = useState<string | null>(null)

  const timeAt = (clientX: number): number => {
    const r = laneEl.current?.getBoundingClientRect()
    return r ? Math.max(0, pxToUs(clientX - r.left, pps)) : 0
  }
  const frame = (tUs: number): number => frameUs(frameAt(tUs, fps) + 1, fps) - frameUs(frameAt(tUs, fps), fps)

  // A change built from the lane: one that sends the whole caption list
  // goes at the lane's revision, never over a newer change (9-D13).
  const send = (ops: Op[], label: string): Promise<OpsAnswer | null> =>
    apply(ops, label, lane ? laneRevisionFor(ops, lane.revision) : null)

  // ---- dragging a block or an edge ----
  const onDown = (e: React.PointerEvent<HTMLButtonElement>, block: Block): void => {
    if (e.button !== 0) return
    onSelectLine(block.id)
    if (!canEdit || editing) return
    const edge = (e.target as HTMLElement).dataset.edge as 'left' | 'right' | undefined
    // Only a line in one piece is retimed or moved (a line cut through by
    // an edit plays in parts; its words can still be fixed).
    if (block.pieces !== 1) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { block, mode: edge ?? 'move', x0: e.clientX, moved: false, start: block.start, end: block.end, pointer: e.pointerId }
  }
  const onMove = (e: React.PointerEvent<HTMLButtonElement>): void => {
    const g = drag.current
    if (!g) return
    if (!g.moved && Math.abs(e.clientX - g.x0) < DRAG_PX) return
    const within = pxToUs(SNAP_PX, pps)
    const points = snapOn
      ? [
          ...snapPoints(doc, playhead.get(), new Set()),
          ...spans.flatMap((s) => [s.start_us, s.end_us]),
          ...blocks.filter((b) => b.id !== g.block.id).flatMap((b) => [b.start, b.end])
        ]
      : []
    const limits = room(blocks, spans, g.block)
    const min = frame(g.block.start)
    const r = dragBlock(g.block, g.mode, pxToUs(e.clientX - g.x0, pps), limits, min, (x) => snapTo(x, points, within))
    // On the program's frames, inside its room. A moved block keeps its
    // length; one held where it was stays exactly there.
    let start: number
    let end: number
    if (g.mode === 'move') {
      const len = g.block.end - g.block.start
      if (r.start === g.block.start) {
        start = g.block.start
        end = g.block.end
      } else {
        start = Math.max(limits.lo, onFrame(r.start, fps))
        end = Math.min(limits.hi, start + len)
        if (end - start < len) start = Math.max(limits.lo, end - len)
      }
    } else {
      start = g.mode === 'right' ? r.start : Math.max(limits.lo, onFrame(r.start, fps))
      end = g.mode === 'left' ? r.end : Math.min(limits.hi, onFrame(r.end, fps))
      if (end - start < min) {
        if (g.mode === 'left') start = end - min
        else end = start + min
      }
    }
    start = Math.round(start)
    end = Math.round(end)
    const next = { ...g, moved: true, start, end }
    drag.current = next
    setGhost(next)
  }
  const onUp = (e: React.PointerEvent<HTMLButtonElement>): void => {
    const g = drag.current
    drag.current = null
    setGhost(null)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    if (!g || !g.moved || !lane || !fresh) return
    if (g.start === g.block.start && g.end === g.block.end) return
    void drop(g)
  }
  const drop = (g: Drag): Promise<void> => place(g.block, g.mode, g.start, g.end)
  // A block's new program times become source times through the engine, so
  // the drag, the keys and the inspector's fields all land on its frames.
  const place = async (block: Block, mode: Drag['mode'], start: number, end: number): Promise<void> => {
    if (!lane) return
    let got: { in_us: number; out_us: number } | null = null
    try {
      const answer = await editorApi.captionSourceTimes(projectId, lane.revision, [{ start_us: start, end_us: end }])
      got = answer.ranges[0] ?? null
    } catch {
      got = null // the project moved on: the lane is drawn again from the new one
    }
    if (!got) return
    const line = lane.lines.find((l) => l.id === block.id)
    const edit = retimeEdit(block.id, mode, got, line ? trimmedLine(line, lane.spans) : null)
    await send(withSave(lane.saved, lane.lines, [{ op: 'edit_caption_words', edits: [edit] }]), mode === 'move' ? t('Move caption') : t('Retime caption'))
  }
  const cancelDrag = (): void => {
    drag.current = null
    setGhost(null)
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape' && drag.current) {
        e.stopPropagation()
        cancelDrag()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [])

  // ---- the words ----
  const commitWords = async (id: string, text: string): Promise<void> => {
    setEditing(null)
    if (!lane) return
    const line = lane.lines.find((l) => l.id === id)
    const words = text.replace(/\s*\n\s*/g, ' ').trim()
    if (!line || !words || words === wordsToEdit(line, lane.spans).trim()) return
    await send(withSave(lane.saved, lane.lines, [{ op: 'edit_caption_words', edits: [wordsEdit(line, lane.spans, words)] }]), t('Edit caption'))
  }
  // A line just added: type its words once its block is there.
  useEffect(() => {
    if (!typeNew || !fresh) return
    const line = lines.find((l) => l.id === typeNew)
    if (line) {
      setEditing({ id: line.id, text: wordsToEdit(line, spans) })
      setTypeNew(null)
    }
  }, [typeNew, fresh, lines])

  // ---- the toolbar's and the keys' actions ----
  const split = (): boolean => {
    if (!selectedLine || !lane) return false
    if (!canEdit) return true
    const at = playhead.get()
    // The selected line, where the playhead is on it.
    const block = blockAt(blocks, at, selectedLine)
    if (!block || block.id !== selectedLine) return true
    const cut = Math.max(block.start, onFrame(at, fps))
    if (cut <= block.start || cut >= block.end) return true
    void (async () => {
      try {
        const answer = await editorApi.captionSourceTimes(projectId, lane.revision, [{ start_us: cut, end_us: block.end }])
        const r = answer.ranges[0]
        const next = r ? splitLine(lane.lines, block.id, r.in_us, lane.spans) : null
        if (next) await send([{ op: 'set_captions', lines: next }], t('Split caption'))
      } catch {
        /* the project moved on */
      }
    })()
    return true
  }
  const remove = (): boolean => {
    if (!selectedLine || !lane) return false
    if (!canEdit) return true
    const id = selectedLine
    void send(withSave(lane.saved, lane.lines, [{ op: 'edit_caption_words', edits: [{ line_id: id, text: '' }] }]), t('Delete caption')).then(
      (a) => a?.changed && onSelectLine(null)
    )
    return true
  }
  const merge = (): void => {
    if (!canEdit || !lane || !selectedLine) return
    const next = mergeLines(lane.lines, selectedLine, lane.spans)
    if (next) void send([{ op: 'set_captions', lines: next }], t('Merge captions'))
  }
  const add = (): void => {
    if (!canEdit || !lane) return
    const at = onFrame(playhead.get(), fps)
    // A project whose captions name no video yet (made by hand): the main
    // track's video under the playhead becomes the captions' video, and the
    // captions are switched on, in the same change.
    if (!lane.media_id) {
      const first = firstLineAt(mainItemsOf(doc), at, NEW_CAPTION_US, frame(at))
      if (!first) return
      void (async () => {
        const done = await send(
          [
            {
              op: 'set_captions',
              media_id: first.media_id,
              ...(lane.enabled === null && !lane.burned ? { enabled: true } : {}),
              lines: [{ in_us: first.in_us, out_us: first.out_us, text: t('New caption') }]
            }
          ],
          t('Add caption')
        )
        const made = ((done?.document.captions?.lines ?? null) as { id: string }[] | null)?.[0]
        if (made) {
          onSelectLine(made.id)
          setTypeNew(made.id)
        }
      })()
      return
    }
    const place = newBlockAt(blocks, spans, at, NEW_CAPTION_US, frame(at))
    if (!place) return
    void (async () => {
      try {
        const answer = await editorApi.captionSourceTimes(projectId, lane.revision, [{ start_us: place.start, end_us: place.end }])
        const r = answer.ranges[0]
        if (!r) return
        const before = new Set(lane.lines.map((l) => l.id))
        const done = await send([{ op: 'set_captions', lines: addLine(lane.lines, { in_us: r.in_us, out_us: r.out_us, text: t('New caption') }) }], t('Add caption'))
        const saved = (done?.document.captions?.lines ?? null) as { id: string; in_us: number; out_us: number }[] | null
        const made = saved?.find((l) => !before.has(l.id) && l.in_us === r.in_us && l.out_us === r.out_us)
        if (made) {
          onSelectLine(made.id)
          setTypeNew(made.id)
        }
      } catch {
        /* the project moved on */
      }
    })()
  }
  // The selected line when it plays in one piece (a line cut through by an
  // edit is retimed on the main track only, as with the mouse).
  const movableSelected = (): Block | null => {
    if (!selectedLine) return null
    const own = blocks.filter((b) => b.id === selectedLine)
    return own.length === 1 && own[0].pieces === 1 ? own[0] : null
  }
  const nudge = (dir: 1 | -1, big: boolean): boolean => {
    if (!selectedLine || !lane) return false
    const block = movableSelected()
    if (!canEdit || !block) return true
    const limits = room(blocks, spans, block)
    const len = block.end - block.start
    const step = big ? 1_000_000 : dir > 0 ? frame(block.start) : frame(Math.max(0, block.start - 1))
    let start = onFrame(block.start + dir * step, fps)
    start = Math.max(limits.lo, start)
    let end = start + len
    if (end > limits.hi) {
      end = limits.hi
      start = Math.max(limits.lo, end - len)
    }
    start = Math.round(start)
    end = Math.round(end)
    if (start === block.start && end === block.end) return true
    void place(block, 'move', start, end)
    return true
  }
  const retime = (start: number, end: number): void => {
    const block = movableSelected()
    if (!canEdit || !block) return
    const limits = room(blocks, spans, block)
    const min = frame(block.start)
    const moveStart = start !== block.start
    const moveEnd = end !== block.end
    if (!moveStart && !moveEnd) return
    let a = moveStart ? Math.max(limits.lo, onFrame(start, fps)) : block.start
    let b = moveEnd ? Math.min(limits.hi, onFrame(end, fps)) : block.end
    if (b - a < min) {
      if (moveStart) a = b - min
      else b = a + min
    }
    a = Math.round(a)
    b = Math.round(b)
    if (a === block.start && b === block.end) return
    void place(block, moveStart && moveEnd ? 'move' : moveStart ? 'left' : 'right', a, b)
  }
  useImperativeHandle(ref, () => ({ split, remove, merge, add, nudge, retime }))

  const hide = (): void => {
    if (readOnly || !lane) return
    void send([{ op: 'set_captions', enabled: !lane.burned }], lane.burned ? t('Hide captions') : t('Show captions'))
  }

  // What each block is called (screen readers, its tooltip), worked out
  // once per answer rather than at every zoom step.
  const meta = useMemo(() => {
    const out = new Map<string, { say: string; title: string }>()
    for (const b of blocks) {
      const span = `${formatTime(b.start, fps)} – ${formatTime(b.end, fps)}`
      out.set(`${b.id}:${b.piece}`, {
        say: `${t('Caption')}, ${b.text}, ${span}`,
        title:
          b.pieces > 1
            ? [`${b.text} · ${span}`, t('An edit cuts this caption in parts: its words can be changed, its times only on the main track.')].join('\n')
            : `${b.text} · ${span}`
      })
    }
    return out
  }, [blocks, fps])
  // The blocks' handlers: the same object at every render (a block is drawn
  // again only when its own place or state changes), calling the latest.
  const startEdit = (id: string): void => {
    if (!canEdit) return
    const line = lines.find((l) => l.id === id)
    if (line) setEditing({ id: line.id, text: wordsToEdit(line, spans) })
  }
  const latest = useRef({ onDown, onMove, onUp, cancelDrag, onSelectLine, startEdit, selectedLine })
  latest.current = { onDown, onMove, onUp, cancelDrag, onSelectLine, startEdit, selectedLine }
  const handlers = useMemo<BlockHandlers>(
    () => ({
      down: (e, b) => latest.current.onDown(e, b),
      move: (e) => latest.current.onMove(e),
      up: (e) => latest.current.onUp(e),
      cancel: () => latest.current.cancelDrag(),
      select: (id) => latest.current.onSelectLine(id),
      edit: (id) => latest.current.startEdit(id),
      selected: (id) => latest.current.selectedLine === id
    }),
    []
  )

  // Buttons only for the blocks near the view (and the one being dragged).
  const visible = blocksIn(blocks, win.from, win.to)
  if (ghost && !visible.some((b) => b.id === ghost.block.id && b.piece === ghost.block.piece)) visible.push(ghost.block)
  const editingBlock = editing ? blocks.find((b) => b.id === editing.id) : undefined
  const headerBtn = 'px-1 rounded text-muted hover:text-ink disabled:opacity-40'

  return (
    <div className="flex border-b border-raised/40" style={{ height: LANE_H }} role="group" aria-label={t('Captions')}>
      {/* The lane's header. */}
      <div
        className="sticky left-0 z-10 shrink-0 bg-surface border-r border-raised/60 flex items-center gap-0.5 px-2 text-[11px]"
        style={{ width: headerW }}
      >
        <span className="flex-1 truncate text-muted" title={t('Captions')}>
          {t('Captions')}
        </span>
        <button
          type="button"
          className={`${headerBtn} ${!shown ? '!text-amber-300' : ''}`}
          aria-pressed={!shown}
          disabled={readOnly || !lane}
          onClick={hide}
          aria-label={t('Hide captions')}
          title={t('Hide captions: they are left out of the video and the preview. Click again to show them.')}
        >
          <span aria-hidden>{shown ? '◉' : '◌'}</span>
        </button>
        <button
          type="button"
          className={`${headerBtn} ${locked ? '!text-amber-300' : ''}`}
          aria-pressed={locked}
          disabled={readOnly}
          onClick={() => onLock(!locked)}
          aria-label={t('Lock captions')}
          title={t('Lock captions: nothing on this lane or on the video moves or changes until you unlock it')}
        >
          <span aria-hidden>{locked ? '🔒' : '🔓'}</span>
        </button>
      </div>
      {/* The lane. */}
      <div
        ref={laneEl}
        className={`relative ${!shown ? 'opacity-50' : ''}`}
        style={{ width: contentW }}
        onPointerDown={(e) => {
          if (e.button !== 0 || (e.target as HTMLElement).closest('[data-caption]')) return
          onSelectLine(null)
          onSeek(timeAt(e.clientX))
        }}
      >
        {visible.map((b) => {
          const g = ghost && ghost.block.id === b.id && ghost.block.piece === b.piece ? ghost : null
          const start = g ? g.start : b.start
          const end = g ? g.end : b.end
          const info = meta.get(`${b.id}:${b.piece}`)
          return (
            <BlockButton
              key={`${b.id}:${b.piece}`}
              block={b}
              x={usToPx(start, pps)}
              w={Math.max(2, usToPx(end - start, pps))}
              on={b.id === selectedLine}
              dragged={g !== null}
              movable={canEdit && b.pieces === 1}
              say={info?.say ?? b.text}
              title={info?.title ?? b.text}
              handlers={handlers}
            />
          )
        })}
        {editing && editingBlock && (
          <WordsEditor
            key={editing.id}
            text={editing.text}
            left={usToPx(editingBlock.start, pps)}
            width={Math.max(200, usToPx(editingBlock.end - editingBlock.start, pps))}
            onDone={(keep, text) => (keep ? void commitWords(editing.id, text) : setEditing(null))}
          />
        )}
      </div>
    </div>
  )
})

interface BlockHandlers {
  down: (e: React.PointerEvent<HTMLButtonElement>, b: Block) => void
  move: (e: React.PointerEvent<HTMLButtonElement>) => void
  up: (e: React.PointerEvent<HTMLButtonElement>) => void
  cancel: () => void
  select: (id: string) => void
  edit: (id: string) => void
  selected: (id: string) => boolean
}

/** One caption block: a button a keyboard can reach and a screen reader can
 *  name, with a handle at each edge to retime it. Drawn again only when its
 *  own props change. */
const BlockButton = memo(function BlockButton({
  block,
  x,
  w,
  on,
  dragged,
  movable,
  say,
  title,
  handlers
}: {
  block: Block
  x: number
  w: number
  on: boolean
  dragged: boolean
  movable: boolean
  say: string
  title: string
  handlers: BlockHandlers
}): JSX.Element {
  return (
    <button
      type="button"
      data-caption={block.id}
      aria-pressed={on}
      aria-label={say}
      title={title}
      className={`absolute top-px bottom-px rounded-[4px] border text-left overflow-hidden select-none touch-none border-rose-300/60 bg-rose-500/20 focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent ${
        on ? '!border-accent ring-1 ring-accent' : ''
      } ${dragged ? 'border-dashed' : ''}`}
      style={{ left: x, width: w, cursor: movable ? 'grab' : 'default', contain: 'strict' }}
      onPointerDown={(e) => handlers.down(e, block)}
      onPointerMove={handlers.move}
      onPointerUp={handlers.up}
      onPointerCancel={handlers.cancel}
      onClick={(e) => {
        // A click from the keyboard (Enter or Space) selects; a mouse click already did.
        if (e.detail === 0) handlers.select(block.id)
      }}
      onDoubleClick={() => handlers.edit(block.id)}
      onKeyDown={(e) => {
        // Enter on the selected block: type its words.
        if (e.key === 'Enter' && handlers.selected(block.id)) {
          e.preventDefault()
          e.stopPropagation()
          handlers.edit(block.id)
        }
      }}
    >
      {w > 24 && <span className="block truncate px-1 text-[10px] leading-[22px] text-ink">{block.text}</span>}
      {movable && w > 3 * EDGE_PX && (
        <>
          <span data-edge="left" className="absolute left-0 top-0 bottom-0 cursor-ew-resize hover:bg-white/30" style={{ width: EDGE_PX }} aria-hidden="true" />
          <span data-edge="right" className="absolute right-0 top-0 bottom-0 cursor-ew-resize hover:bg-white/30" style={{ width: EDGE_PX }} aria-hidden="true" />
        </>
      )}
    </button>
  )
})

/** Typing a caption's words on the lane: Enter keeps, Esc cancels, leaving keeps. */
function WordsEditor({ text, left, width, onDone }: { text: string; left: number; width: number; onDone: (keep: boolean, text: string) => void }): JSX.Element {
  const ref = useRef<HTMLInputElement>(null)
  const done = useRef(false)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  const finish = (keep: boolean): void => {
    if (done.current) return
    done.current = true
    onDone(keep, ref.current?.value ?? text)
  }
  return (
    <input
      ref={ref}
      type="text"
      defaultValue={text}
      maxLength={500}
      className="absolute top-0 bottom-0 z-20 bg-surface text-ink text-[11px] px-1 rounded outline-none ring-2 ring-accent"
      style={{ left, width }}
      aria-label={t('Edit the caption. Enter keeps it, Esc cancels.')}
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Escape') {
          e.preventDefault()
          finish(false)
        } else if (e.key === 'Enter') {
          e.preventDefault()
          finish(true)
        }
      }}
      onBlur={() => finish(true)}
    />
  )
}

/** Whether the selected line can be merged with the next one (for the toolbar). */
export function canMerge(lane: Lane | null, id: string | null): boolean {
  return lane !== null && id !== null && nextLine(lane.lines, id, lane.spans) !== null
}

/** Merge's tooltip: why it is greyed when a cut lies between the selected
 *  caption and the next one (9-D5). */
export function mergeTitle(lane: Lane | null, id: string | null): string {
  return lane !== null && id !== null && cutBeforeNext(lane.lines, id, lane.spans)
    ? t('A cut lies between this caption and the next one, so they stay two captions')
    : t('Make the selected caption and the next one a single caption')
}

/** The main track's items as lib/captionLane.ts takes them. */
function mainItemsOf(doc: ProjectDoc): MainItem[] {
  return (doc.tracks.find((tr) => tr.id === 'V1')?.items ?? []).map((it) => ({
    media_id: it.media_id,
    video: it.media_id !== null && doc.media[it.media_id]?.kind === 'video',
    start_us: it.start_us,
    duration_us: it.duration_us,
    source_in_us: it.source_in_us,
    source_out_us: it.source_out_us,
    speed: it.speed || 1
  }))
}

/** Whether a caption can be added at the playhead (for the toolbar). */
export function canAddAt(lane: Lane | null, doc: ProjectDoc, tUs: number): boolean {
  if (!lane) return false
  const fps = doc.canvas.fps
  const at = onFrame(tUs, fps)
  const min = frameUs(frameAt(at, fps) + 1, fps) - frameUs(frameAt(at, fps), fps)
  if (!lane.media_id) return firstLineAt(mainItemsOf(doc), at, NEW_CAPTION_US, min) !== null
  return newBlockAt(blocksOf(lane.lines), lane.spans, at, NEW_CAPTION_US, min) !== null
}

export default CaptionLane
