/** The Editor page's state, kept outside the page (editor suite K2, U2 and U4).
 *
 *  Which project is shown, the editor's own state as the main process reports
 *  it, a project being made, and the last notice. It lives in this module, not
 *  in the page component, so leaving the page and coming back shows the work
 *  exactly as it was, and a project being made keeps being made while the
 *  person is on another page.
 *
 *  Every way in (a Library clip's Edit, Quick edit's Edit, a video's Edit
 *  whole video, the page's own lists) calls openInEditor() or
 *  openEditorProject(); App.tsx switches to the page on 'open-editor-page'. */
import { useEffect, useState } from 'react'
import { editorApi, EditorRouteError, type EditorOpenRequest, type EditorProject, type EditorTakeIn } from './api'
import { getExportFolder } from './exportFolder'
import { t } from './i18n'

export const OPEN_EDITOR_PAGE = 'open-editor-page'

/** What the main process says about the editor (ui/src/main/editor.ts). */
export interface EditorRuntime {
  running: boolean
  projectId: string
  visible: boolean
  focused: boolean
  dirty: boolean
  exporting: { fraction: number; stage: string } | null
  software: boolean
  /** '' | 'missing' | 'cannot_start' | 'no_project' */
  error: string
  /** '' | 'unexpected' | 'idle' */
  ended: string
  code: number | null
}

export interface EditorNotice {
  text: string
  tooltip?: string
  /** "Show in folder" for this project's latest recorded export. */
  revealProject?: string
  /** Something went wrong: said at once to a screen reader (role="alert"). */
  failure?: boolean
}

export interface EditorStore {
  /** The project shown on the page; null shows the lists. */
  shownId: string | null
  project: EditorProject | null
  /** A project being made or opened: its name and how far it is (and its
   *  id once known, so another project's progress is never shown for it). */
  opening: { name: string; progress: number; id?: string } | null
  /** Why the last open failed, in plain words. */
  openError: string
  runtime: EditorRuntime
  notice: EditorNotice | null
  /** The shown project's notes were shown once for this open. */
  notesSeen: boolean
}

let store: EditorStore = {
  shownId: null,
  project: null,
  opening: null,
  openError: '',
  runtime: {
    running: false,
    projectId: '',
    visible: false,
    focused: false,
    dirty: false,
    exporting: null,
    software: false,
    error: '',
    ended: '',
    code: null
  },
  notice: null,
  notesSeen: false
}
const listeners = new Set<(s: EditorStore) => void>()

function set(patch: Partial<EditorStore>): void {
  store = { ...store, ...patch }
  for (const fn of listeners) fn(store)
}

export function editorStore(): EditorStore {
  return store
}

export function useEditorStore(): EditorStore {
  const [value, setValue] = useState(store)
  useEffect(() => {
    listeners.add(setValue)
    setValue(store)
    return () => {
      listeners.delete(setValue)
    }
  }, [])
  return value
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

/** The plain words for a refused open. */
function openErrorText(e: unknown): string {
  if (e instanceof EditorRouteError) {
    if (e.reason === 'missing') return t('The editor is not installed with this copy of Clips Kitty.')
    if (e.status === 404) return t('That clip or video is no longer in the Library.')
    if (e.status === 400 && /look like a video/.test(e.message)) return t('That file doesn’t look like a video.')
    if (e.status === 400) return t('Clips Kitty can’t open that file. Choose a video file.')
    if (e.status === 0) return t('Clips Kitty is still starting. Try again in a moment.')
  }
  return t('The project could not be opened.')
}

/** What the export answer means for the person, said in the page's strip. */
export function exportNotice(answer: EditorTakeIn): EditorNotice | null {
  switch (answer.result) {
    case 'replaced_clip':
      // No tooltip: the folder is Clips Kitty's own, nothing a person looks for (K-D21).
      return { text: t('Saved as this clip’s video.') }
    case 'kept_beside':
      return {
        text: t('This clip was changed in the Clip Editor meanwhile, so the export was saved next to it.'),
        tooltip: answer.path,
        revealProject: answer.project_id
      }
    case 'saved_to_folder':
      return {
        text: t('Saved to {folder}').replace('{folder}', answer.folder ?? ''),
        tooltip: answer.path,
        revealProject: answer.project_id
      }
    case 'failed':
      return { text: t('The export did not finish.'), tooltip: answer.error || undefined, failure: true }
    default:
      return null // cancelled: nothing to say
  }
}

/** The sentences of a project's notes (what is not carried over). */
export function noteSentences(codes: string[] | undefined): string[] {
  const words: Record<string, string> = {
    framing_baked: t('The AI’s framing is part of the video: it cannot be re-aimed in the editor.'),
    captions_plain: t('Captions are plain text in the editor: the lit-up word and the caption animation are not carried.'),
    font_default: t('The caption font is not on this PC for the editor, so the editor’s own font is used.'),
    music_duck: t('Music no longer dips under speech by itself; its volume is the one set.'),
    source_missing: t(
      'The original video is gone, so the clip’s finished file is used: its captions are part of the picture and its cuts cannot be taken back.'
    ),
    from_export: t(
      'This clip was already made in the editor and that project is gone, so its finished file is used: its captions are part of the picture and its cuts cannot be taken back.'
    ),
    watermark_still: t('A moving watermark stands still in the editor.'),
    music_missing: t('The clip’s music file is gone, so there is no music in the editor.')
  }
  return (codes ?? []).map((code) => words[code]).filter((s): s is string => !!s)
}

let openToken = 0

function gotoPage(): void {
  window.dispatchEvent(new CustomEvent(OPEN_EDITOR_PAGE))
}

/** A native question (drawn above the editor). The index of the button. */
export async function askNative(message: string, buttons: string[], detail?: string): Promise<number> {
  const bridge = window.studio?.editor
  if (!bridge) return window.confirm(message) ? 0 : 1
  return bridge.ask({ message, detail, buttons, defaultId: 0, cancelId: 0 })
}

/** "This clip was changed in Quick edit after this project was made": open
 *  the project as it is (default), or start a new one from the clip. */
async function wantsFresh(project: EditorProject): Promise<boolean> {
  if (project.kind !== 'clip' || !project.clip_changed || project.clip_gone) return false
  const answer = await askNative(t('This clip was changed in the Clip Editor after this project was made.'), [
    t('Open the project'),
    t('Start a new project from the clip')
  ])
  return answer === 1
}

/** Wait for a project to be made, then open it on the page. */
async function openReady(first: EditorProject, token: number, mayRemake = true): Promise<void> {
  let project = first
  const wasFailed = project.state === 'failed'
  set({ opening: { name: project.name, progress: openingProgress(project), id: project.id }, openError: '' })
  while (project.state === 'building') {
    await buildNews(1000)
    if (token !== openToken) return
    try {
      project = await editorApi.project(project.id)
    } catch (e) {
      set({ opening: null, openError: openErrorText(e) })
      return
    }
    set({ opening: { name: project.name, progress: openingProgress(project), id: project.id } })
  }
  if (token !== openToken) return
  // A project that had failed when it was asked for is made again by opening
  // it (the engine's choice 8), once; one that fails while it is made here
  // says why.
  if (project.state === 'failed' && (project.damaged || !wasFailed || !mayRemake)) {
    set({ opening: null, openError: project.message || t('The project could not be opened.') })
    return
  }
  let opened: EditorProject
  try {
    opened = await editorApi.reopen(project.id, await getExportFolder())
  } catch (e) {
    if (e instanceof EditorRouteError && e.reason === 'building') {
      await sleep(1000)
      if (token === openToken) await openReady({ ...project, state: 'building' }, token, false)
      return
    }
    set({ opening: null, openError: openErrorText(e) })
    return
  }
  if (opened.state === 'building') {
    await openReady(opened, token, false)
    return
  }
  if (opened.state !== 'ready') {
    set({ opening: null, openError: opened.message || t('The project could not be opened.') })
    return
  }
  if (token !== openToken) return
  const bridge = window.studio?.editor
  if (!bridge) {
    set({ opening: null, openError: t('The editor could not start.') })
    return
  }
  const shown = await bridge.show(opened.id, await showOptions(opened))
  if (token !== openToken) return
  if (!shown.ok && shown.error === 'exporting') {
    set({ opening: null, notice: { text: t('The editor is exporting. Open another project when it has finished.') } })
    return
  }
  const recovered = (opened.recovered ?? []).map(exportNotice).filter((n): n is EditorNotice => !!n)
  set({
    shownId: opened.id,
    project: opened,
    opening: null,
    openError: '',
    notesSeen: false,
    notice: recovered.length ? recovered[recovered.length - 1] : null
  })
}

/** Where the export folder is, and the line the editor's Export sheet shows
 *  under the name: what Clips Kitty will do with the file. */
export async function showOptions(
  project: EditorProject | null
): Promise<{ exportFolder: string; destination: string }> {
  const exportFolder = await getExportFolder()
  let destination: string
  if (project?.kind === 'clip' && !project.clip_gone) {
    destination = project.clip_changed
      ? t('Saved next to this clip, which was changed since')
      : t('Replaces this clip in the Library')
  } else {
    destination = exportFolder
      ? t('Export folder: {folder}').replace('{folder}', exportFolder)
      : t('Goes to your Downloads folder')
  }
  return { exportFolder, destination }
}

/** Open a clip, a Library video or a file in the editor: made first when
 *  there is no project for it yet. */
export async function openInEditor(req: EditorOpenRequest): Promise<void> {
  const token = ++openToken
  gotoPage()
  set({ opening: { name: '', progress: 0 }, openError: '' })
  try {
    let answer = await editorApi.open(req)
    if (token !== openToken) return
    if (req.kind === 'clip' && !answer.created && (await wantsFresh(answer.project))) {
      answer = await editorApi.open({ ...req, fresh: true })
    }
    if (token !== openToken) return
    await openReady(answer.project, token)
  } catch (e) {
    if (token === openToken) set({ opening: null, openError: openErrorText(e) })
  }
}

/** Open a project from the page's list (or from Quick edit's question). */
export async function openEditorProject(id: string): Promise<void> {
  const token = ++openToken
  gotoPage()
  set({ opening: { name: '', progress: 0 }, openError: '' })
  try {
    let project = await editorApi.project(id)
    if (token !== openToken) return
    if (project.clip && (await wantsFresh(project))) {
      project = (await editorApi.open({ kind: 'clip', clip_id: project.clip.id, fresh: true })).project
    }
    if (token !== openToken) return
    await openReady(project, token)
  } catch (e) {
    if (token === openToken) set({ opening: null, openError: openErrorText(e) })
  }
}

/** Start the editor again on the shown project (Try again, Open again, or
 *  coming back after it was closed while hidden for a long time). */
export async function ensureEditorShown(): Promise<void> {
  const bridge = window.studio?.editor
  const id = store.shownId
  if (!bridge || !id) return
  set({ runtime: { ...store.runtime, error: '', ended: '' } })
  await bridge.show(id, await showOptions(store.project))
}

/** Try again after a start that failed or a software-only start. */
export async function restartEditor(): Promise<void> {
  const bridge = window.studio?.editor
  if (!bridge) return
  if (store.runtime.running) await bridge.close()
  await ensureEditorShown()
}

/** "Projects": the editor saves and leaves, and the page shows the lists.
 *  While it exports, the person is asked first: leaving stops the export. */
export async function closeEditorProject(): Promise<void> {
  if (store.runtime.exporting) {
    const answer = await askNative(t('The editor is still exporting. Close the project and stop the export?'), [
      t('Keep exporting'),
      t('Close')
    ])
    if (answer !== 1) return
  }
  ++openToken
  set({ shownId: null, project: null, opening: null, notesSeen: false })
  await window.studio?.editor?.close()
}

export function clearEditorNotice(): void {
  set({ notice: null })
}

export function clearOpenError(): void {
  set({ openError: '' })
}

export function markNotesSeen(): void {
  set({ notesSeen: true })
}

let bridged = false
/** Listen to the main process once (App.tsx calls this at start). */
export function connectEditorBridge(): void {
  const bridge = window.studio?.editor
  if (bridged || !bridge) return
  bridged = true
  bridge.onState((s) => set({ runtime: { ...store.runtime, ...(s as Partial<EditorRuntime>) } }))
  bridge.onExport((answer) => {
    const notice = exportNotice(answer as unknown as EditorTakeIn)
    if (notice) set({ notice })
  })
  void bridge.getState().then((s) => {
    if (s) set({ runtime: { ...store.runtime, ...(s as Partial<EditorRuntime>) } })
  })
  // The main process asks before quitting during an export; it has no t(),
  // so its words come from here, in the app's language.
  const sendWords = (): void => {
    void bridge.setWords?.({
      message: t('The editor is still exporting. Quit and stop the export?'),
      keep: t('Keep exporting'),
      quit: t('Quit')
    })
  }
  sendWords()
  window.addEventListener('app-language-changed', sendWords)
}

/** A project's progress from the engine's event stream. */
export function onEditorProjectEvent(e: { id?: string; state?: string; progress?: number }): void {
  if (!store.opening || !e.id || (store.opening.id && store.opening.id !== e.id)) return
  if (typeof e.progress === 'number') {
    set({ opening: { ...store.opening, progress: openingProgress({ id: e.id, progress: e.progress }) } })
  }
  if (e.state && e.state !== 'building') newsWaiter?.()
}

let newsWaiter: (() => void) | null = null

/** A second, or less when the engine says a project being made is done:
 *  the page then asks for it at once rather than at its next look. */
function buildNews(ms: number): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(done, ms)
    function done(): void {
      clearTimeout(timer)
      if (newsWaiter === done) newsWaiter = null
      resolve()
    }
    newsWaiter = done
  })
}

/** The most the bar says before the editor shows the project: a project is
 *  "ready" (1.0) a moment before the editor has drawn it. */
export const OPENING_MOST = 0.99

/** How far the project being opened is, for its bar: never less than it
 *  already said for that project (the event stream and the page's own
 *  asking can arrive out of order), and never 100% while it is opening. */
export function openingProgress(project: { id?: string; progress?: number }): number {
  const before = store.opening && (!store.opening.id || store.opening.id === project.id) ? store.opening.progress : 0
  return Math.min(OPENING_MOST, Math.max(before || 0, project.progress || 0))
}

let appPointerAt = 0
/** A click or focus in the app's own page (App.tsx): the keys go back to it. */
export function noteAppPointer(): void {
  appPointerAt = Date.now()
  if (store.runtime.running) void window.studio?.editor?.blur()
}

/** Whether the keys left the editor because of a click in the app. */
export function appPointerRecent(): boolean {
  return Date.now() - appPointerAt < 1500
}
