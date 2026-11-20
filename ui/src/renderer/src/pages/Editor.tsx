/** The Editor page (editor suite K2, U2 to U5).
 *
 *  Clips Kitty Editor (a separate program, based on Concat) is drawn by the
 *  main process INSIDE this window, over this page's editor area. With no
 *  project shown the page lists recent projects and the two ways to start one
 *  here; with a project shown it is a 36 px strip of the app's own and the
 *  area, which the editor covers.
 *
 *  The editor is a native window above the web page, so nothing of the app's
 *  own may be drawn into the area while it shows: things that would hold the
 *  cover (lib/editorCover.ts), questions are native dialogs, notices go in
 *  the strip. The page's state lives in lib/editor.ts, so leaving the page
 *  and coming back shows the work as it was. */
import { useEffect, useRef, useState } from 'react'
import { api, editorApi, type EditorProject, type EditorShape } from '../lib/api'
import {
  appPointerRecent,
  clearEditorNotice,
  clearOpenError,
  closeEditorProject,
  editorStore,
  ensureEditorShown,
  markNotesSeen,
  noteSentences,
  openEditorProject,
  openInEditor,
  restartEditor,
  useEditorStore
} from '../lib/editor'
import { useEditorCoverHolds } from '../lib/editorCover'
import { t } from '../lib/i18n'
import { useEvents } from '../lib/useEvents'
import type { StudioEvent, Video } from '../lib/types'

export const SHAPES: { value: EditorShape; label: string }[] = [
  { value: '16:9', label: '16:9' },
  { value: '9:16', label: '9:16' },
  { value: '1:1', label: '1:1' },
  { value: '4:5', label: '4:5' }
]

/** The shape select, shared by both ways to start a project here and by the
 *  Library's "Edit whole video". */
export function ShapeSelect({
  value,
  onChange,
  id
}: {
  value: EditorShape
  onChange: (shape: EditorShape) => void
  id?: string
}): JSX.Element {
  return (
    <select
      id={id}
      className="input !w-24 text-sm"
      value={value}
      onChange={(e) => onChange(e.target.value as EditorShape)}
      aria-label={t('Shape')}
      title={t('The shape of the video you make: 16:9 wide, 9:16 tall, 1:1 square, 4:5 portrait')}
    >
      {SHAPES.map((s) => (
        <option key={s.value} value={s.value}>
          {s.label}
        </option>
      ))}
    </select>
  )
}

const KIND_ICON: Record<EditorProject['kind'], { icon: string; label: string }> = {
  clip: { icon: '✂', label: 'A clip' },
  video: { icon: '▭', label: 'A whole video' },
  file: { icon: '⇪', label: 'A file from your PC' }
}

function when(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

/** Recent projects, "Edit a whole video", "Edit a file from your PC". */
function ProjectLists(): JSX.Element {
  const store = useEditorStore()
  const [projects, setProjects] = useState<EditorProject[]>([])
  const [videos, setVideos] = useState<Video[]>([])
  const [videoId, setVideoId] = useState('')
  const [videoShape, setVideoShape] = useState<EditorShape>('16:9')
  const [fileShape, setFileShape] = useState<EditorShape>('16:9')
  const [listError, setListError] = useState('')

  const load = (): void => {
    editorApi
      .projects()
      .then((r) => {
        setProjects(r.projects)
        setListError('')
      })
      .catch(() => setListError(t('The projects could not be read.')))
  }
  useEffect(() => {
    load()
    api
      .videos()
      .then((v) => {
        setVideos(v)
        if (v.length) setVideoId((current) => current || v[0].video_id)
      })
      .catch(() => setVideos([]))
  }, [])
  useEvents((e: StudioEvent) => {
    if ((e.type as string) === 'editor_project' && e.state !== 'building') load()
  })
  // A project that finished being made or opening shows at the top.
  useEffect(load, [store.opening === null])

  const remove = async (p: EditorProject): Promise<void> => {
    if (!window.confirm(t('Delete this project? Videos you exported from it stay.'))) return
    try {
      await editorApi.remove(p.id)
    } catch (e) {
      const reason = (e as { reason?: string }).reason
      setListError(
        reason === 'open_in_editor'
          ? t('This project is open in the editor. Close it first.')
          : reason === 'building'
            ? t('This project is still being made.')
            : t('The project could not be deleted.')
      )
    }
    load()
  }

  const pickFile = async (): Promise<void> => {
    const path = await window.studio.pickVideoFile()
    if (path) void openInEditor({ kind: 'file', path, shape: fileShape })
  }

  return (
    <div className="p-6 space-y-5 max-w-4xl">
      <h2 className="text-2xl font-bold">{t('Editor')}</h2>
      {store.opening && (
        <div className="card space-y-2" role="status" aria-live="polite">
          <p className="text-sm">
            {store.opening.name
              ? t('Getting “{name}” ready…').replace('{name}', store.opening.name)
              : t('Getting the project ready…')}
          </p>
          <div className="h-1.5 rounded bg-raised overflow-hidden">
            <div
              className="h-full bg-accent transition-all"
              style={{ width: `${Math.round(Math.min(1, Math.max(0.02, store.opening.progress)) * 100)}%` }}
            />
          </div>
        </div>
      )}
      {store.openError && (
        <p className="text-sm text-amber-400" role="alert">
          {store.openError}{' '}
          <button type="button" className="underline text-muted" onClick={clearOpenError}>
            {t('Dismiss')}
          </button>
        </p>
      )}
      {/* Always there, so a notice that appears is read out (a live region
          added together with its text is not). */}
      <p role="status" className="text-sm text-accent empty:hidden" title={store.notice?.tooltip}>
        {store.notice && !store.notice.failure ? store.notice.text : null}
      </p>
      <p role="alert" className="text-sm text-amber-400 empty:hidden" title={store.notice?.tooltip}>
        {store.notice?.failure ? store.notice.text : null}
      </p>

      <section className="card space-y-2" aria-labelledby="editor-recent">
        <h3 id="editor-recent" className="font-semibold">
          {t('Recent projects')}
        </h3>
        {listError && <p className="text-sm text-amber-400">{listError}</p>}
        {projects.length === 0 ? (
          <p className="text-sm text-muted">{t('No projects yet.')}</p>
        ) : (
          <ul className="divide-y divide-raised/60">
            {projects.map((p) => (
              <li key={p.id} className="flex items-center gap-3 py-1.5">
                <span aria-hidden title={t(KIND_ICON[p.kind]?.label ?? 'A clip')} className="w-5 text-center text-muted">
                  {KIND_ICON[p.kind]?.icon ?? '✂'}
                </span>
                <button
                  type="button"
                  className="flex-1 min-w-0 text-left text-sm hover:text-accent truncate disabled:opacity-60"
                  onClick={() => void openEditorProject(p.id)}
                  disabled={p.state === 'building' || !!p.damaged}
                  title={p.state === 'failed' ? p.message : t(KIND_ICON[p.kind]?.label ?? 'A clip')}
                >
                  {p.name || t('Untitled project')}
                  {p.state === 'failed' && <span className="text-amber-400"> · {t('Not finished')}</span>}
                  {p.clip_gone && <span className="text-muted"> · {t('Clip deleted')}</span>}
                </button>
                {p.state === 'building' ? (
                  <div className="w-24 h-1.5 rounded bg-raised overflow-hidden" aria-label={t('Being made')}>
                    <div className="h-full bg-accent" style={{ width: `${Math.round((p.progress || 0) * 100)}%` }} />
                  </div>
                ) : (
                  <span className="text-xs text-muted tabular-nums shrink-0">{when(p.last_opened_at)}</span>
                )}
                <button
                  type="button"
                  className="text-xs text-muted hover:text-red-400 shrink-0"
                  onClick={() => void remove(p)}
                  disabled={p.state === 'building'}
                  aria-label={t('Delete project')}
                  title={t('Delete project')}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card space-y-2" aria-labelledby="editor-whole">
        <h3 id="editor-whole" className="font-semibold">
          {t('Edit a whole video')}
        </h3>
        <div className="flex flex-wrap gap-2 items-center">
          <select
            className="input !w-80 text-sm"
            value={videoId}
            onChange={(e) => setVideoId(e.target.value)}
            aria-label={t('Library video')}
            disabled={videos.length === 0}
          >
            {videos.length === 0 && <option value="">{t('No videos yet')}</option>}
            {videos.map((v) => (
              <option key={v.video_id} value={v.video_id}>
                {v.title || v.video_id}
              </option>
            ))}
          </select>
          <ShapeSelect value={videoShape} onChange={setVideoShape} />
          <button
            type="button"
            className="btn-accent !py-1.5 text-sm"
            disabled={!videoId || store.opening !== null}
            onClick={() => void openInEditor({ kind: 'video', video_id: videoId, shape: videoShape })}
          >
            {t('Open')}
          </button>
        </div>
      </section>

      <section className="card space-y-2" aria-labelledby="editor-file">
        <h3 id="editor-file" className="font-semibold">
          {t('Edit a file from your PC')}
        </h3>
        <div className="flex flex-wrap gap-2 items-center">
          <ShapeSelect value={fileShape} onChange={setFileShape} />
          <button
            type="button"
            className="btn-ghost !py-1.5 text-sm"
            disabled={store.opening !== null}
            onClick={() => void pickFile()}
            title={t('Open a video file in the editor. Nothing is added to the Library and no AI runs.')}
          >
            {t('Choose a file…')}
          </button>
        </div>
      </section>
    </div>
  )
}

/** The state at the strip's right: "Saved", "Saving…", "Exporting 42%". */
function stateText(store: ReturnType<typeof editorStore>): string {
  const rt = store.runtime
  if (!rt.running) return ''
  if (rt.exporting) return `${t('Exporting')} ${Math.round(rt.exporting.fraction * 100)}%`
  return rt.dirty ? t('Saving…') : t('Saved')
}

/** What a screen reader is told of an export: its start and each quarter,
 *  not every half second of it. Empty when no export runs. */
export function exportAnnouncement(store: ReturnType<typeof editorStore>): string {
  const exporting = store.runtime.running ? store.runtime.exporting : null
  if (!exporting) return ''
  const quarter = Math.min(3, Math.floor(Math.max(0, exporting.fraction) * 4)) * 25
  return `${t('Exporting')} ${quarter}%`
}

/** A project shown: the strip and the area the editor covers. */
function ProjectView({
  approvals,
  onShowApprovals
}: {
  approvals: number
  onShowApprovals: () => void
}): JSX.Element {
  const store = useEditorStore()
  const holds = useEditorCoverHolds()
  const area = useRef<HTMLDivElement>(null)
  const projectsButton = useRef<HTMLButtonElement>(null)
  const rt = store.runtime
  const bridge = window.studio.editor

  // Where the editor goes: the area's rectangle in physical pixels, at most
  // once per frame, on every change of size, place or screen scale.
  useEffect(() => {
    const el = area.current
    if (!el || !bridge) return
    let frame = 0
    const send = (): void => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        const r = el.getBoundingClientRect()
        const dpr = window.devicePixelRatio || 1
        void bridge.setBounds({
          x: Math.floor(r.left * dpr),
          y: Math.floor(r.top * dpr),
          width: Math.max(1, Math.round(r.width * dpr)),
          height: Math.max(1, Math.round(r.height * dpr))
        })
      })
    }
    send()
    const observer = new ResizeObserver(send)
    observer.observe(el)
    if (el.parentElement) observer.observe(el.parentElement)
    window.addEventListener('resize', send)
    let media = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
    const onScale = (): void => {
      media.removeEventListener('change', onScale)
      media = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
      media.addEventListener('change', onScale)
      send()
    }
    media.addEventListener('change', onScale)
    const stopMeasure = bridge.onMeasure(send)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      window.removeEventListener('resize', send)
      media.removeEventListener('change', onScale)
      stopMeasure()
    }
  }, [bridge, store.shownId])

  // Shown only while this page is up, a project is shown and nothing of the
  // app's own is open over the area. Hidden when the page is left: the
  // editor and its work stay as they are.
  const wanted = !!store.shownId && holds === 0
  useEffect(() => {
    void bridge?.setVisible(wanted)
  }, [bridge, wanted])
  useEffect(() => () => void bridge?.setVisible(false), [bridge])

  // Started again when it is not running (closed after a while hidden), but
  // not after an error or a crash: the page then says so and offers to.
  useEffect(() => {
    if (store.shownId && !rt.running && !rt.error && rt.ended !== 'unexpected') void ensureEditorShown()
  }, [store.shownId, rt.running, rt.error, rt.ended])

  // A click in the editor takes the keys: no button of the page stays
  // focused (Space would press it). Ctrl+F6 in the editor gives them back,
  // and focus goes to "Projects".
  const wasFocused = useRef(false)
  useEffect(() => {
    if (rt.focused) {
      ;(document.activeElement as HTMLElement | null)?.blur?.()
    } else if (wasFocused.current && !appPointerRecent()) {
      // The keys came back without a click in the app: Ctrl+F6.
      projectsButton.current?.focus()
    }
    wasFocused.current = rt.focused
  }, [rt.focused])

  const notes = noteSentences(store.project?.notes)
  const showNotes = notes.length > 0 && !store.notesSeen
  const status = stateText(store)
  const announced = exportAnnouncement(store)
  const covered = holds > 0

  let message: { text: string; tooltip?: string; button: string; action: () => void } | null = null
  if (rt.software) {
    message = {
      text: t('The editor cannot use this PC’s graphics card, so it cannot be shown here.'),
      tooltip: t('Updating the graphics driver usually fixes this.'),
      button: t('Try again'),
      action: () => void restartEditor()
    }
  } else if (rt.error === 'cannot_start' || rt.error === 'missing' || rt.error === 'no_project') {
    message = {
      text: rt.error === 'missing' ? t('The editor is not installed with this copy of Clips Kitty.') : t('The editor could not start.'),
      tooltip: t('If it keeps happening, restart Clips Kitty.'),
      button: t('Try again'),
      action: () => void restartEditor()
    }
  } else if (rt.ended === 'unexpected') {
    message = {
      text: t('The editor closed unexpectedly. Your work is saved as you go.'),
      button: t('Open again'),
      action: () => void ensureEditorShown()
    }
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="h-9 shrink-0 flex items-center gap-3 px-3 border-b border-raised/60 bg-surface text-sm">
        <button
          ref={projectsButton}
          type="button"
          className="px-2 py-0.5 rounded-md bg-raised hover:bg-raised/70"
          onClick={() => void closeEditorProject()}
          title={t('Save and close this project')}
        >
          ← {t('Projects')}
        </button>
        <span className="font-medium truncate max-w-[16rem]" title={store.project?.name}>
          {store.project?.name}
        </span>
        {store.opening?.name && (
          <span className="text-muted truncate" role="status">
            {t('Getting “{name}” ready…').replace('{name}', store.opening.name)}{' '}
            {Math.round(store.opening.progress * 100)}%
          </span>
        )}
        {showNotes && (
          <span className="text-muted truncate" title={notes.join('\n')}>
            {t('Some things from the clip are not carried over.')}{' '}
            <button type="button" className="underline" onClick={markNotesSeen} aria-label={t('Dismiss')}>
              ✕
            </button>
          </span>
        )}
        {/* The notice's live regions are always there, so a notice that
            appears (an export taken in) is read out: role="status", or
            role="alert" when something went wrong. */}
        <span role="status" className="sr-only">
          {store.notice && !store.notice.failure ? store.notice.text : ''}
        </span>
        <span role="alert" className="sr-only">
          {store.notice?.failure ? store.notice.text : ''}
        </span>
        {store.notice && (
          <span className={`${store.notice.failure ? 'text-amber-400' : 'text-accent'} truncate`} title={store.notice.tooltip}>
            <span aria-hidden="true">{store.notice.text}</span>
            {store.notice.revealProject && (
              <button
                type="button"
                className="ml-2 underline"
                onClick={() => void window.studio.editor?.reveal(store.notice?.revealProject ?? '', -1)}
              >
                {t('Show in folder')}
              </button>
            )}{' '}
            <button type="button" className="underline text-muted" onClick={clearEditorNotice} aria-label={t('Dismiss')}>
              ✕
            </button>
          </span>
        )}
        {approvals > 0 && (
          <button type="button" className="px-2 py-0.5 rounded-md bg-accent/20 text-accent shrink-0" onClick={onShowApprovals}>
            {t('Clip approvals')} ({approvals})
          </button>
        )}
        <span className="ml-auto text-muted shrink-0">{status}</span>
        {/* An export's start and each quarter, not every poll of it. */}
        <span className="sr-only" aria-live="polite">
          {announced}
        </span>
      </div>
      <div
        ref={area}
        tabIndex={0}
        role="application"
        aria-label={t('Editor. Press Enter to use it, Ctrl+F6 to come back.')}
        className="flex-1 min-h-0 relative bg-base outline-none focus-visible:ring-2 focus-visible:ring-accent"
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            void bridge?.focus()
          }
        }}
      >
        {message ? (
          <div className="absolute inset-0 grid place-items-center p-6">
            <div className="text-center space-y-3 max-w-md">
              <p title={message.tooltip}>{message.text}</p>
              <button type="button" className="btn-accent !py-1.5 text-sm" onClick={message.action} title={message.tooltip}>
                {message.button}
              </button>
            </div>
          </div>
        ) : (
          !rt.visible &&
          !covered && (
            <p className="absolute inset-0 grid place-items-center text-muted text-sm" role="status">
              {t('Opening the editor…')}
            </p>
          )
        )}
      </div>
    </div>
  )
}

export default function Editor({
  approvals,
  onShowApprovals
}: {
  approvals: number
  onShowApprovals: () => void
}): JSX.Element {
  const store = useEditorStore()
  return store.shownId ? <ProjectView approvals={approvals} onShowApprovals={onShowApprovals} /> : <ProjectLists />
}
