/** The new editor: a full screen of its own, laid out as creators know it from
 *  CapCut. The media bin on the left, the viewer in the middle, the inspector
 *  on the right, the timeline across the bottom.
 *
 *  There is nothing to save: every change is saved by the engine as it is made
 *  (step 5's operations), so Close just closes. */
import { useEffect, useMemo, useRef, useState } from 'react'
import { t } from '../../lib/i18n'
import { createPlayhead } from '../../lib/editorPlayhead'
import { formatLength, programLength } from '../../lib/editorProgram'
import type { Op } from '../../lib/editorTypes'
import ExportDialog from './ExportDialog'
import MediaBin from './MediaBin'
import Inspector from './Inspector'
import TimelinePanel, { type TimelineHandle } from './TimelinePanel'
import TranscriptPanel from './TranscriptPanel'
import Viewer, { type ViewerHandle } from './Viewer'
import type { LayerTarget } from './CaptionTextLayer'
import { useCaptionLayer } from './useCaptionLayer'
import { useMediaInfos } from './useMediaInfos'
import { useProject } from './useProject'

/** Key names in tooltips (not words to translate). */
const KEYS = { undo: 'Ctrl+Z', redo: 'Ctrl+Shift+Z, Ctrl+Y' }

/** Whether a key press belongs to a field being typed in, not the editor. */
function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el) return false
  const tag = el.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable
}

export default function EditorScreen({
  projectId,
  openNotice,
  onClose
}: {
  projectId: string
  /** A plain line to show as it opens (a start-again, 8-D12). */
  openNotice?: string
  onClose: () => void
}): JSX.Element {
  const state = useProject(projectId, openNotice)
  const { project, loadError, notice, clearNotice, apply, undo, redo } = state
  const doc = project?.document ?? null
  const infos = useMediaInfos(projectId, doc)
  const playhead = useMemo(() => createPlayhead(), [projectId])
  const viewer = useRef<ViewerHandle>(null)
  const timeline = useRef<TimelineHandle>(null)
  const [exporting, setExporting] = useState(false)
  const exportButton = useRef<HTMLButtonElement>(null)
  const [source, setSource] = useState<string | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  // The caption layer selected on the viewer (step 9). A text item selected
  // there is an item: it is in `selected`.
  const [captionLayer, setCaptionLayer] = useState(false)
  const layer = useCaptionLayer(projectId, project?.revision ?? -1)
  // The caption line selected on the lane or in the list (step 9, part C);
  // selecting one selects the caption layer too. The lane's Lock, kept
  // while the editor is open. A text just added, whose words are typed.
  const [captionLine, setCaptionLine] = useState<string | null>(null)
  const [captionsLocked, setCaptionsLocked] = useState(false)
  const [focusText, setFocusText] = useState<string | null>(null)
  // The left column: the media bin, or the captions as a list.
  const [leftTab, setLeftTab] = useState<'media' | 'captions'>('media')
  const selectItems = (ids: string[]): void => {
    setSelected(ids)
    setFocusText(null)
    if (ids.length > 0) {
      setCaptionLayer(false)
      setCaptionLine(null)
    }
  }
  const selectLayer = (target: LayerTarget | null): void => {
    // A text picked again on the viewer keeps the keys there: the inspector's
    // Words box takes focus only right after "+ Text".
    setFocusText(null)
    setCaptionLayer(target?.kind === 'captions')
    setSelected(target?.kind === 'text' ? [target.id] : [])
    if (target?.kind !== 'captions') setCaptionLine(null)
  }
  const selectLine = (id: string | null): void => {
    setFocusText(null)
    setCaptionLine(id)
    if (id !== null) {
      setCaptionLayer(true)
      setSelected([])
    } else setCaptionLayer(false)
  }
  // Focus comes into the editor when it opens. App.tsx notes what opened it
  // and gives that focus back when it closes.
  const root = useRef<HTMLDivElement>(null)
  const loaded = project !== null
  useEffect(() => {
    root.current?.focus()
  }, [loaded])

  // A selected item an edit removed is no longer selected.
  useEffect(() => {
    if (!doc) return
    const ids = new Set(doc.tracks.flatMap((tr) => tr.items.map((i) => i.id)))
    setSelected((cur) => (cur.every((id) => ids.has(id)) ? cur : cur.filter((id) => ids.has(id))))
    if (source && !doc.media[source]) setSource(null)
  }, [doc, source])

  // A notice goes after a while.
  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(clearNotice, 8000)
    return () => window.clearTimeout(timer)
  }, [notice, clearNotice])

  // The editor's keys: the viewer's, and the timeline's (split, delete,
  // move, zoom). None while the export dialog is open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (typing(e.target) || exporting) return
      const v = viewer.current
      const tl = timeline.current
      const key = e.key
      const mod = e.ctrlKey || e.metaKey
      if (e.altKey) {
        if (!mod && tl && (key === 'ArrowLeft' || key === 'ArrowRight')) {
          e.preventDefault()
          tl.nudge(key === 'ArrowLeft' ? -1 : 1, e.shiftKey)
        }
        return
      }
      if (mod && (key === 'z' || key === 'Z')) {
        e.preventDefault()
        if (e.shiftKey) void redo()
        else void undo()
        return
      }
      if (mod && (key === 'y' || key === 'Y')) {
        e.preventDefault()
        void redo()
        return
      }
      if (mod || !v) return
      if (tl && !source) {
        if (key === 's' || key === 'S') {
          e.preventDefault()
          tl.split()
          return
        }
        if (key === 'Delete' || key === 'Backspace') {
          e.preventDefault()
          tl.remove()
          return
        }
        if (key === '+' || key === '=') {
          e.preventDefault()
          tl.zoom(1)
          return
        }
        if (key === '-' || key === '_') {
          e.preventDefault()
          tl.zoom(-1)
          return
        }
        if ((key === 'z' || key === 'Z') && e.shiftKey) {
          e.preventDefault()
          tl.fit()
          return
        }
      }
      // A focused button answers Space and Enter itself.
      const onButton = (e.target as HTMLElement | null)?.tagName === 'BUTTON'
      if (key === ' ' && !onButton) {
        e.preventDefault()
        v.togglePlay()
      } else if (key === 'ArrowLeft' || key === ',') {
        e.preventDefault()
        v.step(e.shiftKey ? -10 : -1)
      } else if (key === 'ArrowRight' || key === '.') {
        e.preventDefault()
        v.step(e.shiftKey ? 10 : 1)
      } else if (key === 'Home') {
        e.preventDefault()
        v.toStart()
      } else if (key === 'End') {
        e.preventDefault()
        v.toEnd()
      } else if (key === 'i' || key === 'I') {
        v.markIn()
      } else if (key === 'o' || key === 'O') {
        v.markOut()
      } else if (key === 'Escape' && source) {
        setSource(null)
      } else if (key === 'Escape' && (selected.length > 0 || captionLayer)) {
        setSelected([])
        setCaptionLayer(false)
        setCaptionLine(null)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [undo, redo, source, exporting, selected.length, captionLayer])

  const addRange = async (mediaId: string, inUs: number, outUs: number): Promise<void> => {
    const answer = await apply(
      [{ op: 'insert_clip', track_id: 'V1', media_id: mediaId, source_in_us: Math.round(inUs), source_out_us: Math.round(outUs) }],
      t('Add range')
    )
    const made = answer?.created?.find((c) => c.kind === 'item')
    if (made) {
      setSelected([made.id])
      const item = answer?.document.tracks.find((tr) => tr.id === 'V1')?.items.find((i) => i.id === made.id)
      setSource(null)
      if (item) window.setTimeout(() => viewer.current?.seek(item.start_us), 0)
    }
  }

  const applyOps = (ops: Op[], label?: string): Promise<unknown> => apply(ops, label)

  if (!project || !doc) {
    return (
      <div ref={root} tabIndex={-1} className="h-full flex flex-col items-center justify-center gap-3 text-sm outline-none">
        <p className={loadError ? 'text-red-300' : 'text-muted'}>{loadError || t('Opening the project…')}</p>
        <button type="button" className="btn-ghost" onClick={onClose}>
          {t('Close')}
        </button>
      </div>
    )
  }

  const length = programLength(doc)
  // What the viewer shows as selected: the caption layer, or the one text item selected.
  const textTracks = new Set(doc.tracks.filter((tr) => tr.kind === 'text').map((tr) => tr.id))
  const selectedText =
    selected.length === 1 ? doc.tracks.find((tr) => textTracks.has(tr.id) && tr.items.some((it) => it.id === selected[0])) : undefined
  const layerSelection: LayerTarget | null = captionLayer
    ? { kind: 'captions' }
    : selectedText
      ? { kind: 'text', id: selected[0] }
      : null
  const iconBtn = 'px-2 py-1 rounded-md text-sm bg-raised text-muted hover:text-ink disabled:opacity-40'

  return (
    <div ref={root} tabIndex={-1} className="h-full flex flex-col bg-base text-ink outline-none">
      {/* ---- the bar at the top ---- */}
      <header className="flex items-center gap-2 px-3 py-2 border-b border-raised/60 bg-surface">
        <button type="button" className="btn-ghost !py-1 !px-3 text-xs" onClick={onClose} title={t('Close the editor. Every change is already saved.')}>
          ← {t('Close')}
        </button>
        <h2 className="font-semibold truncate max-w-[40ch]" title={doc.name}>
          {doc.name || t('Untitled project')}
        </h2>
        <span className="text-xs text-muted tabular-nums">{formatLength(length)}</span>
        <span className="text-[10px] uppercase tracking-wide bg-accent/15 text-accent rounded px-1.5 py-0.5">{t('Beta')}</span>
        {project.read_only && (
          <span className="text-xs text-amber-300" title={t('This project was saved by a newer version of Clips Kitty, so it can only be viewed here.')}>
            {t('View only')}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1.5">
          <button
            ref={exportButton}
            type="button"
            className="btn-accent !py-1 !px-3 text-xs mr-2"
            onClick={() => setExporting(true)}
            disabled={length === 0}
            title={
              project.clip_id !== null
                ? t("This project was made from a clip. Exporting replaces that clip's video in your Library with this one; the clip keeps its title and its posts.")
                : t('Makes the video file of this project')
            }
          >
            {t('Export')}
          </button>
          <button
            type="button"
            className={iconBtn}
            onClick={() => void undo()}
            disabled={!project.can_undo || project.read_only}
            aria-label={t('Undo')}
            title={`${t('Undo')} (${KEYS.undo})`}
          >
            <span aria-hidden>↶</span>
          </button>
          <button
            type="button"
            className={iconBtn}
            onClick={() => void redo()}
            disabled={!project.can_redo || project.read_only}
            aria-label={t('Redo')}
            title={`${t('Redo')} (${KEYS.redo})`}
          >
            <span aria-hidden>↷</span>
          </button>
        </div>
      </header>
      {notice && (
        <div className="px-3 py-1.5 text-xs bg-amber-500/10 text-amber-200 border-b border-amber-500/20 flex items-center gap-2" role="status">
          <span className="flex-1">{notice}</span>
          <button type="button" className="text-muted hover:text-ink" onClick={clearNotice} aria-label={t('Dismiss')}>
            ×
          </button>
        </div>
      )}

      {/* ---- bin, viewer, inspector ---- */}
      <div className="flex-1 min-h-0 grid grid-cols-[minmax(180px,15rem)_minmax(0,1fr)_minmax(200px,17rem)]">
        <div className="min-h-0 flex flex-col bg-surface">
          <div className="flex border-b border-raised/60 text-xs" role="tablist" aria-label={t('Media and captions')}>
            {(
              [
                ['media', 'Media', 'The videos, pictures and sounds of this project'],
                ['captions', 'Captions', 'Every caption as a list: click a time to go there, type to fix the words']
              ] as const
            ).map(([id, label, tip]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={leftTab === id}
                className={`flex-1 px-2 py-1.5 border-b-2 ${leftTab === id ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink'}`}
                onClick={() => setLeftTab(id)}
                title={t(tip)}
              >
                {t(label)}
              </button>
            ))}
          </div>
          {leftTab === 'media' ? (
            <div className="min-h-0 flex-1 flex flex-col" role="tabpanel" aria-label={t('Media')}>
              <MediaBin
                doc={doc}
                infos={infos}
                source={source}
                onSource={setSource}
                apply={apply}
                playhead={playhead}
                readOnly={project.read_only}
              />
            </div>
          ) : (
            <div className="min-h-0 flex-1 flex flex-col" role="tabpanel" aria-label={t('Captions')}>
              <TranscriptPanel
                lane={layer.lane}
                revision={project.revision}
                fps={doc.canvas.fps}
                playhead={playhead}
                selectedLine={captionLine}
                onSelectLine={selectLine}
                onSeek={(tUs) => viewer.current?.seek(tUs)}
                apply={apply}
                readOnly={project.read_only}
                locked={captionsLocked}
              />
            </div>
          )}
        </div>
        <div className="min-w-0 min-h-0 border-x border-raised/60">
          <Viewer
            ref={viewer}
            project={project}
            infos={infos}
            playhead={playhead}
            source={source}
            onSource={setSource}
            onAddRange={(m, a, b) => void addRange(m, a, b)}
            layer={layer}
            layerSelection={layerSelection}
            onLayerSelect={selectLayer}
            captionsLocked={captionsLocked}
            apply={apply}
          />
        </div>
        <Inspector
          project={project}
          selected={selected}
          apply={applyOps}
          captionLayer={captionLayer}
          lane={layer.lane}
          focusText={focusText}
          onTextFocused={() => setFocusText(null)}
          captionLine={captionLine}
          onRetimeCaption={(start, end) => timeline.current?.retimeCaption(start, end)}
        />
      </div>

      {/* ---- the timeline ---- */}
      <TimelinePanel
        ref={timeline}
        doc={doc}
        playhead={playhead}
        selected={selected}
        onSelect={selectItems}
        onSeek={(tUs) => viewer.current?.seek(tUs)}
        apply={apply}
        infos={infos}
        readOnly={project.read_only}
        projectId={projectId}
        revision={project.revision}
        lane={layer.lane}
        selectedLine={captionLine}
        onSelectLine={selectLine}
        captionsLocked={captionsLocked}
        onCaptionsLock={setCaptionsLocked}
        onTextAdded={(id) => {
          selectItems([id])
          setFocusText(id)
        }}
      />
      {exporting && (
        <ExportDialog
          project={project}
          captionsShown={layer.lane ? layer.lane.burned : true}
          onClose={() => {
            setExporting(false)
            window.setTimeout(() => exportButton.current?.focus(), 0)
          }}
        />
      )}
    </div>
  )
}
