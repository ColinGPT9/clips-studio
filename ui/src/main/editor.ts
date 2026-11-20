// Clips Kitty Editor inside the app window (editor suite step K2, U1).
//
// The editor is a separate program (the fork of Concat, AGPL-3.0-or-later)
// that draws as a child window of this app's window. This file is the only
// place that starts it, talks to it and stops it:
//
// - The engine says where the program is (GET /editor/launch) and where each
//   project lives (GET /editor/projects/<id>). The renderer only ever names a
//   project by its id; no path from the page reaches the command line.
// - The editor's own API (JSON-RPC lines on a loopback port chosen here) is
//   opened with a token made here and given to the editor in its environment
//   only: never on the command line, never logged, never sent to the page or
//   the engine.
// - It is started detached with no pipes, so a crash of this app leaves it
//   free to save and leave by itself (its own watcher notices the host
//   window is gone). It is never killed from here: closing is host.close,
//   which saves first.
// - One editor at a time. Another project is opened with host.open_project;
//   if that fails, the editor is closed and started again on the new one.
// - It reports finished exports in host.state (polled); each one is handed to
//   the engine by file name only, and the engine takes it in (E4).
//
// The contract is docs/editor-suite/api/k2-editor.md ("The app").
import { app, BrowserWindow, dialog, ipcMain, screen, shell, type IpcMainInvokeEvent } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { connect, createServer } from 'node:net'
import { win32 } from 'node:path'

const PROJECT_ID = /^[0-9a-f]{32}$/
/** How long a start may take before the editor counts as not starting. */
const START_TIMEOUT_MS = 20000
/** Closed after this long hidden, when it is neither exporting nor saving. */
const IDLE_CLOSE_MS = 10 * 60 * 1000

export interface EditorRect {
  x: number
  y: number
  width: number
  height: number
}

/** What the page is told, on every change. */
export interface EditorPublicState {
  running: boolean
  projectId: string
  visible: boolean
  focused: boolean
  dirty: boolean
  exporting: { fraction: number; stage: string } | null
  software: boolean
  /** '' | 'missing' | 'cannot_start' | 'no_project' */
  error: string
  /** '' | 'unexpected' (it ended by itself) | 'idle' (closed after a while hidden) */
  ended: string
  code: number | null
}

interface HostExport {
  seq: number
  state: 'done' | 'failed' | 'cancelled'
  project: string
  output: string
  error?: string
}

interface HostState {
  hostApi: number
  visible: boolean
  focused: boolean
  graphics?: { software?: boolean }
  project: { path: string; name: string; dirty: boolean } | null
  export: { running: boolean; fraction: number; stage: string }
  exports: HostExport[]
}

interface Running {
  child: ChildProcess
  pid: number
  port: number
  token: string
  startedAt: number
  ready: boolean
  closing: boolean
  idleClosing: boolean
  software: boolean
  projectId: string
  projectDir: string
  exportFolder: string
  lastSeq: number
  /** Every project this process has had open, by its folder (normalised):
   *  an export is matched to its project by the folder the editor reports. */
  known: Map<string, { id: string; exportFolder: string }>
  timer: ReturnType<typeof setTimeout> | null
  takeIn: Promise<void>
}

interface Deps {
  window: () => BrowserWindow | null
  apiPort: number
  secret: string
}

let deps: Deps | null = null
let ed: Running | null = null
let wantVisible = false
let hiddenSince = Date.now()
let bounds: EditorRect | null = null
let lastScale = 0
let pub: EditorPublicState = {
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
}

// ---- small helpers ----------------------------------------------------------

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))
// An export's report asked again while the engine is busy with the same
// project or can't be reached for a moment (takeIn).
const TAKE_IN_RETRIES = 6
const TAKE_IN_RETRY_MS = 2000

/** A folder as the editor and the engine may each spell it (slashes, case, a
 *  trailing separator), as one key for comparing. */
function folderKey(path: string): string {
  return path.replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase()
}

function send(channel: string, payload: unknown): void {
  const win = deps?.window()
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload)
}

function setPub(patch: Partial<EditorPublicState>): void {
  const next = { ...pub, ...patch }
  if (JSON.stringify(next) === JSON.stringify(pub)) return
  pub = next
  send('editor:state', pub)
}

/** Calls run one after another: a second "show" while the first is still
 *  starting the editor waits for it instead of starting a second editor. */
let chain: Promise<unknown> = Promise.resolve()
function serial<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn, fn)
  chain = next.catch(() => undefined)
  return next
}

/** Only this app's own window may drive the editor. */
function ownFrame(event: IpcMainInvokeEvent): boolean {
  const win = deps?.window()
  if (!win || win.isDestroyed() || event.sender !== win.webContents) return false
  const url = event.senderFrame?.url ?? ''
  return (
    url.startsWith('file:') ||
    (!!process.env.ELECTRON_RENDERER_URL && url.startsWith(process.env.ELECTRON_RENDERER_URL))
  )
}

interface EngineAnswer {
  ok: boolean
  status: number
  body: Record<string, unknown> | null
}

/** A call to the engine's editor routes, with the session header. */
async function engine(method: string, route: string, body?: unknown, timeoutMs = 15000): Promise<EngineAnswer> {
  if (!deps) return { ok: false, status: 0, body: null }
  try {
    const res = await fetch(`http://127.0.0.1:${deps.apiPort}${route}`, {
      method,
      headers: { 'Content-Type': 'application/json', 'X-Clips-Kitty-Session': deps.secret },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs)
    })
    const parsed = (await res.json().catch(() => null)) as Record<string, unknown> | null
    return { ok: res.ok, status: res.status, body: parsed }
  } catch {
    return { ok: false, status: 0, body: null }
  }
}

/** The plain sentence in an engine refusal, if it has one. */
function engineMessage(answer: EngineAnswer): string {
  const detail = answer.body?.detail as unknown
  if (typeof detail === 'string') return detail
  const message = (detail as { message?: unknown } | undefined)?.message
  return typeof message === 'string' ? message : ''
}

function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close(() => resolve(port))
    })
  })
}

let nextId = 1
/** One call on the editor's API: auth, then the method, on a fresh
 *  connection (as the stage-1 harness proved). */
function rpc(
  target: Running,
  method: string,
  params: Record<string, unknown> = {},
  timeoutMs = 4000
): Promise<{ result?: unknown; error?: string }> {
  return new Promise((resolve) => {
    const id = nextId++
    const sock = connect(target.port, '127.0.0.1')
    let buffer = ''
    let done = false
    const finish = (value: { result?: unknown; error?: string }): void => {
      if (done) return
      done = true
      clearTimeout(timer)
      sock.destroy()
      resolve(value)
    }
    const timer = setTimeout(() => finish({ error: 'timeout' }), timeoutMs)
    sock.on('connect', () => {
      sock.write(JSON.stringify({ jsonrpc: '2.0', id: 0, method: 'auth', params: { token: target.token } }) + '\n')
      sock.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n')
    })
    sock.on('data', (chunk) => {
      buffer += chunk.toString()
      let at: number
      while ((at = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, at)
        buffer = buffer.slice(at + 1)
        if (!line.trim()) continue
        let message: { id?: unknown; result?: unknown; error?: { message?: unknown } }
        try {
          message = JSON.parse(line)
        } catch {
          continue
        }
        if (message.id === 0 && message.error) finish({ error: 'auth' })
        if (message.id === id) {
          finish(
            message.error
              ? { error: typeof message.error.message === 'string' ? message.error.message : 'error' }
              : { result: message.result }
          )
        }
      }
    })
    sock.on('error', () => finish({ error: 'connection' }))
    sock.on('close', () => finish({ error: 'closed' }))
  })
}

function alive(pid: number): boolean {
  try {
    process.kill(pid, 0) // signal 0 only asks whether it is there; nothing is sent
    return true
  } catch {
    return false
  }
}

async function waitForExit(target: Running, ms: number): Promise<boolean> {
  const end = Date.now() + ms
  while (Date.now() < end) {
    if (target.child.exitCode !== null || !alive(target.pid)) return true
    await sleep(100)
  }
  return false
}

function physicalBounds(): EditorRect {
  if (bounds) return bounds
  const win = deps?.window()
  const size = win && !win.isDestroyed() ? win.getContentSize() : [800, 600]
  const scale = win && !win.isDestroyed() ? screen.getDisplayMatching(win.getBounds()).scaleFactor : 1
  return { x: 0, y: 0, width: Math.max(1, Math.round(size[0] * scale)), height: Math.max(1, Math.round(size[1] * scale)) }
}

// ---- start, switch, close ---------------------------------------------------

interface ProjectPlaces {
  dir: string
  exportDir: string
  exportName: string
}

/** The line each project's Export sheet shows under the name (the page's
 *  words, already translated): kept per project, passed at start
 *  (--export-note) and with host.open_project (destination). */
const destinations = new Map<string, string>()

function destinationOf(projectId: string): string {
  return destinations.get(projectId) ?? ''
}

async function projectPlaces(projectId: string): Promise<ProjectPlaces | null> {
  const answer = await engine('GET', `/editor/projects/${projectId}`)
  const body = answer.body
  if (!answer.ok || !body || body.state !== 'ready') return null
  const dir = body.project_dir
  const exportDir = body.export_dir
  const exportName = body.export_name
  if (typeof dir !== 'string' || typeof exportDir !== 'string' || !dir || !exportDir) return null
  return { dir, exportDir, exportName: typeof exportName === 'string' ? exportName : '' }
}

async function start(projectId: string, exportFolder: string): Promise<{ ok: boolean; error?: string }> {
  const launch = await engine('GET', '/editor/launch')
  const exe = launch.body?.exe
  if (!launch.ok || typeof exe !== 'string' || !exe) {
    setPub({ running: false, projectId, error: 'missing', ended: '', code: null })
    return { ok: false, error: 'missing' }
  }
  const places = await projectPlaces(projectId)
  if (!places) {
    setPub({ running: false, projectId, error: 'no_project', ended: '', code: null })
    return { ok: false, error: 'no_project' }
  }
  const win = deps?.window()
  if (!win || win.isDestroyed()) return { ok: false, error: 'cannot_start' }

  const token = randomBytes(32).toString('hex')
  const port = await freePort()
  const hwnd = win.getNativeWindowHandle().readBigUInt64LE(0).toString()
  const r = physicalBounds()
  const env: NodeJS.ProcessEnv = { ...process.env, CONCAT_EMBED_TOKEN: token }
  delete env.ELECTRON_RUN_AS_NODE
  delete env.CLIPS_KITTY_SESSION_SECRET // the engine's, not the editor's
  let child: ChildProcess
  try {
    child = spawn(
      exe,
      [
        '--embed', hwnd,
        '--bounds', `${r.x},${r.y},${r.width},${r.height}`,
        '--api', `127.0.0.1:${port}`,
        '--project', places.dir,
        '--export-dir', places.exportDir,
        '--export-name', places.exportName,
        ...(destinationOf(projectId) ? ['--export-note', destinationOf(projectId)] : []),
        '--hidden'
      ],
      { detached: true, stdio: 'ignore', windowsHide: true, cwd: app.getPath('videos'), env }
    )
  } catch {
    setPub({ running: false, projectId, error: 'cannot_start', ended: '', code: null })
    return { ok: false, error: 'cannot_start' }
  }
  child.unref()
  const target: Running = {
    child,
    pid: child.pid ?? 0,
    port,
    token,
    startedAt: Date.now(),
    ready: false,
    closing: false,
    idleClosing: false,
    software: false,
    projectId,
    projectDir: places.dir,
    exportFolder,
    lastSeq: 0,
    known: new Map([[folderKey(places.dir), { id: projectId, exportFolder }]]),
    timer: null,
    takeIn: Promise.resolve()
  }
  ed = target
  child.on('error', () => onExit(target, null))
  child.on('exit', (code) => onExit(target, code))
  setPub({ running: true, projectId, error: '', ended: '', code: null, software: false, dirty: false, exporting: null })

  let state: HostState | null = null
  const deadline = Date.now() + START_TIMEOUT_MS
  while (Date.now() < deadline && ed === target && target.child.exitCode === null) {
    const answer = await rpc(target, 'host.state', {}, 1500)
    if (answer.result) {
      state = answer.result as HostState
      break
    }
    await sleep(100)
  }
  if (ed !== target) return { ok: false, error: pub.error || 'cannot_start' }
  if (!state || !(state.hostApi >= 2)) {
    // Not this app's editor, or it never answered: asked to leave if it
    // answers, otherwise left to end by itself (it is hidden; never killed).
    target.closing = true
    if (state) void rpc(target, 'host.close')
    ed = null
    setPub({ running: false, error: 'cannot_start', ended: '', code: null })
    return { ok: false, error: 'cannot_start' }
  }
  target.ready = true
  target.lastSeq = Math.max(0, ...(state.exports ?? []).map((e) => e.seq))
  target.software = state.graphics?.software === true
  if (target.software) {
    // It cannot draw on the graphics card here: it stays hidden and the page
    // says so (7.6).
    setPub({ software: true })
  } else {
    await rpc(target, 'host.set_bounds', { ...physicalBounds() })
    if (wantVisible) await rpc(target, 'host.set_visible', { visible: true })
  }
  applyState(target, state)
  schedulePoll(target)
  return { ok: true }
}

function onExit(target: Running, code: number | null): void {
  if (target.timer) clearTimeout(target.timer)
  target.timer = null
  if (ed !== target) return
  ed = null
  if (target.closing) {
    setPub({
      running: false,
      visible: false,
      focused: false,
      dirty: false,
      exporting: null,
      software: false,
      ended: target.idleClosing ? 'idle' : '',
      code: null
    })
    return
  }
  // It ended by itself. Its work is saved as it goes (1.5 s after each change).
  const early = Date.now() - target.startedAt < START_TIMEOUT_MS
  setPub({
    running: false,
    visible: false,
    focused: false,
    exporting: null,
    error: early ? 'cannot_start' : '',
    ended: early ? '' : 'unexpected',
    code
  })
}

async function switchTo(target: Running, projectId: string, exportFolder: string): Promise<{ ok: boolean; error?: string }> {
  const live = await rpc(target, 'host.state', {}, 3000)
  if ((live.result as HostState | undefined)?.export?.running) return { ok: false, error: 'exporting' }
  const places = await projectPlaces(projectId)
  if (!places) return { ok: false, error: 'no_project' }
  const answer = await rpc(
    target,
    'host.open_project',
    { path: places.dir, exportDir: places.exportDir, exportName: places.exportName, destination: destinationOf(projectId) },
    30000
  )
  if (answer.error?.startsWith('busy:')) return { ok: false, error: 'exporting' }
  if (answer.error) {
    // The editor could not switch by itself: closed (it saves first) and
    // started again on the new project.
    await closeNow(target)
    return start(projectId, exportFolder)
  }
  target.projectId = projectId
  target.projectDir = places.dir
  target.exportFolder = exportFolder
  target.known.set(folderKey(places.dir), { id: projectId, exportFolder })
  setPub({ projectId, dirty: false })
  return { ok: true }
}

async function closeNow(target: Running, waitMs = 10000, idle = false): Promise<void> {
  target.closing = true
  target.idleClosing = idle
  if (target.timer) clearTimeout(target.timer)
  target.timer = null
  // Exports that ended since the last poll are asked for once more, then
  // every export reported is taken in first, so nothing is left behind.
  if (target.ready) {
    const last = await rpc(target, 'host.state', {}, 3000)
    if (last.result) queueEnded(target, last.result as HostState)
  }
  await Promise.race([target.takeIn, sleep(waitMs)])
  if (target.ready) await rpc(target, 'host.close', {}, 3000)
  const gone = await waitForExit(target, waitMs)
  if (ed === target) {
    ed = null
    setPub({
      running: false,
      visible: false,
      focused: false,
      dirty: false,
      exporting: null,
      software: false,
      ended: idle ? 'idle' : '',
      code: null
    })
  }
  if (!gone) console.warn('Clips Kitty Editor did not leave in time; it leaves by itself once it has saved')
}

// ---- while it runs ----------------------------------------------------------

function schedulePoll(target: Running): void {
  if (ed !== target || target.closing) return
  const fast = wantVisible || pub.exporting !== null
  target.timer = setTimeout(() => void poll(target), fast ? 500 : 2000)
}

async function poll(target: Running): Promise<void> {
  if (ed !== target || target.closing || !target.ready) return
  const answer = await rpc(target, 'host.state', {}, 3000)
  if (ed !== target || target.closing) return
  if (answer.result) applyState(target, answer.result as HostState)
  if (
    !wantVisible &&
    Date.now() - hiddenSince > IDLE_CLOSE_MS &&
    pub.exporting === null &&
    !pub.dirty
  ) {
    // Hidden for a long while with nothing to save: its memory goes back to
    // the PC (and to clipping runs). Showing the page starts it again.
    void serial(() => closeNow(target, 10000, true))
    return
  }
  schedulePoll(target)
}

function applyState(target: Running, state: HostState): void {
  const software = state.graphics?.software === true
  if (software && !target.software) {
    target.software = true
    void rpc(target, 'host.set_visible', { visible: false })
  }
  // The editor's fraction starts again at 0 for an export's last step; the
  // page shows the furthest it got, so it never reads "Exporting 0%" at the end.
  const fraction = Number(state.export?.fraction) || 0
  setPub({
    visible: state.visible === true,
    focused: state.focused === true,
    dirty: state.project?.dirty === true,
    exporting: state.export?.running
      ? { fraction: Math.max(fraction, pub.exporting?.fraction ?? 0), stage: String(state.export.stage || '') }
      : null,
    software: target.software
  })
  queueEnded(target, state)
}

/** Each export the editor reports as ended, newer than the last one seen,
 *  queued to be taken in (once each, in order). */
function queueEnded(target: Running, state: HostState): void {
  const ended = (state.exports ?? []).filter((e) => e.seq > target.lastSeq).sort((a, b) => a.seq - b.seq)
  for (const entry of ended) {
    target.lastSeq = entry.seq
    target.takeIn = target.takeIn.then(() => takeIn(target, entry))
  }
}

/** A finished export, handed to the engine by its file name only. */
async function takeIn(target: Running, entry: HostExport): Promise<void> {
  const project = target.known.get(folderKey(String(entry.project || '')))
  if (!project) {
    console.warn(`an export of a project this window did not open was ignored (#${entry.seq})`)
    return
  }
  if (entry.state !== 'done') {
    send('editor:export', { project_id: project.id, result: entry.state, error: entry.error ?? '' })
    return
  }
  const file = win32.basename(String(entry.output || ''))
  // The engine waits while a reopen's recover takes this project's exports
  // in, and answers a file it took as it recorded it ("again"): so a 409
  // (still busy after that wait) or an engine that can't be reached for a
  // moment is asked again, never reported as a failed export (K-D17). The
  // engine takes each file in once, whatever the number of asks.
  let answer = await engine(
    'POST',
    `/editor/projects/${project.id}/exports`,
    { file, export_folder: project.exportFolder || null },
    10 * 60 * 1000
  )
  for (let attempt = 1; attempt <= TAKE_IN_RETRIES && (answer.status === 0 || answer.status === 409); attempt++) {
    await sleep(TAKE_IN_RETRY_MS)
    answer = await engine(
      'POST',
      `/editor/projects/${project.id}/exports`,
      { file, export_folder: project.exportFolder || null },
      10 * 60 * 1000
    )
  }
  if (answer.ok && answer.body) {
    send('editor:export', { ...answer.body, project_id: project.id })
  } else {
    send('editor:export', { project_id: project.id, result: 'failed', error: engineMessage(answer) })
  }
}

// ---- what the rest of the main process uses ---------------------------------

export function editorRunning(): boolean {
  return ed !== null
}

/** Asked fresh, not from the last poll: is this editor exporting right now? */
async function exportingOn(target: Running): Promise<boolean> {
  if (!target.ready) return false
  const answer = await rpc(target, 'host.state', {}, 2000)
  return (answer.result as HostState | undefined)?.export?.running === true
}

/** Asked fresh, not from the last poll: is the editor exporting right now? */
export async function editorExporting(): Promise<boolean> {
  const target = ed
  return target ? exportingOn(target) : false
}

/** The quit question's words, translated by the page (editor:words), which
 *  has the app's language; English until the page has said them. */
let quitWords = {
  message: 'The editor is still exporting. Quit and stop the export?',
  keep: 'Keep exporting',
  quit: 'Quit'
}

/** Ask before quitting while the editor exports: quitting stops the export.
 *  True to quit. Worded like confirmQuit in index.ts. */
export async function confirmQuitWhileExporting(parent: BrowserWindow | null): Promise<boolean> {
  if (!(await editorExporting())) return true
  const options = {
    type: 'warning' as const,
    buttons: [quitWords.keep, quitWords.quit],
    defaultId: 0,
    cancelId: 0,
    title: 'Clips Kitty',
    message: quitWords.message
  }
  if (parent && !parent.isDestroyed()) {
    if (!parent.isVisible()) parent.show()
    return (await dialog.showMessageBox(parent, options)).response === 1
  }
  return (await dialog.showMessageBox(options)).response === 1
}

/** Ask the editor to save and leave (host.close), and wait for it at most
 *  `waitMs`. Never kills it: one whose host window is gone saves and leaves
 *  by itself. */
export function closeEditor(waitMs = 10000): Promise<void> {
  const target = ed
  if (!target) return Promise.resolve()
  return serial(() => closeNow(target, waitMs))
}

// ---- set up -----------------------------------------------------------------

function validRect(value: unknown): EditorRect | null {
  const r = value as Partial<EditorRect> | null
  if (!r || typeof r !== 'object') return null
  const nums = [r.x, r.y, r.width, r.height]
  if (!nums.every((n) => typeof n === 'number' && Number.isFinite(n) && Math.abs(n) < 100000)) return null
  return {
    x: Math.floor(r.x as number),
    y: Math.floor(r.y as number),
    width: Math.max(1, Math.round(r.width as number)),
    height: Math.max(1, Math.round(r.height as number))
  }
}

async function place(): Promise<void> {
  const target = ed
  if (target?.ready && !target.software && bounds) await rpc(target, 'host.set_bounds', { ...bounds })
}

function scaleOf(win: BrowserWindow): number {
  return screen.getDisplayMatching(win.getBounds()).scaleFactor
}

/** A child window gets no message when the screen's scale changes, so the
 *  editor is started again on the same project (about 2 s, a rare event). */
async function checkScale(): Promise<void> {
  const win = deps?.window()
  if (!win || win.isDestroyed()) return
  const scale = scaleOf(win)
  if (lastScale === 0 || scale === lastScale) {
    lastScale = scale
    return
  }
  lastScale = scale
  send('editor:measure', null)
  const target = ed
  if (!target || !target.ready) return
  await restartForScale(target)
}

let scaleRestart: Running | null = null
/** Start the editor again for a new screen scale, but never while it
 *  exports: closing it would stop the export, which only the person may do.
 *  Until the export has ended it keeps its old scale and is only re-placed
 *  (editor:measure above). One wait per editor; a second scale change while
 *  it waits is covered by the same restart. */
async function restartForScale(target: Running): Promise<void> {
  if (scaleRestart === target) return
  scaleRestart = target
  try {
    const { projectId } = target
    for (;;) {
      while (ed === target && !target.closing && (await exportingOn(target))) await sleep(1000)
      if (ed !== target || target.closing) return
      // Asked again inside the queue, just before the close: an export may
      // have started while this waited for its turn.
      const done = await serial(async () => {
        if (ed !== target || target.closing) return true
        if (await exportingOn(target)) return false
        const exportFolder = target.exportFolder
        await closeNow(target)
        await start(projectId, exportFolder)
        return true
      })
      if (done) return
    }
  } finally {
    if (scaleRestart === target) scaleRestart = null
  }
}

/** Hook the window's events; call once per window. */
export function watchEditorWindow(win: BrowserWindow): void {
  lastScale = scaleOf(win)
  const measure = (): void => {
    send('editor:measure', null)
    void place()
  }
  for (const name of ['resize', 'maximize', 'unmaximize', 'restore', 'enter-full-screen', 'leave-full-screen'] as const) {
    win.on(name as 'resize', measure)
  }
  win.on('moved', () => void checkScale())
}

export function setupEditor(options: Deps): void {
  deps = options
  screen.on('display-metrics-changed', (_event, _display, changed) => {
    if (changed.includes('scaleFactor')) void checkScale()
  })

  ipcMain.handle('editor:show', (event, projectId: unknown, opts: unknown) => {
    if (!ownFrame(event)) return { ok: false, error: 'refused' }
    if (typeof projectId !== 'string' || !PROJECT_ID.test(projectId)) return { ok: false, error: 'refused' }
    const folder = (opts as { exportFolder?: unknown } | null)?.exportFolder
    // With no folder in Settings, the one the app's own Export buttons use
    // (lib/exportFolder.ts): Windows' Downloads folder (K-D22).
    const exportFolder =
      typeof folder === 'string' && folder.length < 1024 && win32.isAbsolute(folder) ? folder : app.getPath('downloads')
    const note = (opts as { destination?: unknown } | null)?.destination
    // One line of the page's own words: control characters out, kept short.
    const destination = typeof note === 'string' ? note.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 400) : ''
    destinations.set(projectId, destination)
    return serial(async () => {
      const target = ed
      if (target && target.projectId === projectId) {
        target.exportFolder = exportFolder
        target.known.set(folderKey(target.projectDir), { id: projectId, exportFolder })
        if (target.ready && !target.software) {
          await rpc(target, 'host.set_bounds', { ...physicalBounds() })
          await rpc(target, 'host.set_visible', { visible: wantVisible })
        }
        return { ok: true }
      }
      if (target) return switchTo(target, projectId, exportFolder)
      return start(projectId, exportFolder)
    })
  })

  ipcMain.handle('editor:setBounds', (event, rect: unknown) => {
    if (!ownFrame(event)) return { ok: false }
    const r = validRect(rect)
    if (!r) return { ok: false }
    bounds = r
    void place()
    return { ok: true }
  })

  ipcMain.handle('editor:setVisible', async (event, visible: unknown) => {
    if (!ownFrame(event)) return { ok: false }
    const show = visible === true
    if (show !== wantVisible) hiddenSince = Date.now()
    wantVisible = show
    const target = ed
    if (target?.ready && !target.software) {
      if (show) await rpc(target, 'host.set_bounds', { ...physicalBounds() })
      await rpc(target, 'host.set_visible', { visible: show })
    }
    return { ok: true }
  })

  ipcMain.handle('editor:focus', async (event) => {
    if (!ownFrame(event)) return { ok: false }
    const target = ed
    if (target?.ready && wantVisible && !target.software) await rpc(target, 'host.focus')
    return { ok: true }
  })

  ipcMain.handle('editor:blur', async (event) => {
    if (!ownFrame(event)) return { ok: false }
    const target = ed
    if (!target?.ready) return { ok: true }
    // Asked fresh: the last poll can be up to half a second old.
    const live = pub.focused ? null : await rpc(target, 'host.state', {}, 1500)
    if (pub.focused || (live?.result as HostState | undefined)?.focused === true) {
      await rpc(target, 'host.blur')
      const win = deps?.window()
      if (win && !win.isDestroyed()) win.webContents.focus()
    }
    return { ok: true }
  })

  ipcMain.handle('editor:close', async (event) => {
    if (!ownFrame(event)) return { ok: false }
    await closeEditor()
    // Back to the list: a later start is a fresh one, not a restart after an error.
    setPub({ projectId: '', error: '', ended: '', code: null })
    return { ok: true }
  })

  ipcMain.handle('editor:getState', (event) => (ownFrame(event) ? pub : null))

  // The quit question's words in the app's language (the main process has no
  // t(); the page sends them at start and when the language changes).
  ipcMain.handle('editor:words', (event, words: unknown) => {
    if (!ownFrame(event)) return { ok: false }
    const w = words as { message?: unknown; keep?: unknown; quit?: unknown } | null
    const text = (v: unknown, max: number): string | null =>
      typeof v === 'string' && v.trim() && v.length <= max ? v : null
    const message = text(w?.message, 400)
    const keep = text(w?.keep, 80)
    const quit = text(w?.quit, 80)
    if (!message || !keep || !quit) return { ok: false }
    quitWords = { message, keep, quit }
    return { ok: true }
  })

  // Show a recorded export of a project in Explorer. The page names the
  // project and which export; the path comes from the engine's record.
  ipcMain.handle('editor:reveal', async (event, projectId: unknown, index: unknown) => {
    if (!ownFrame(event)) return false
    if (typeof projectId !== 'string' || !PROJECT_ID.test(projectId)) return false
    if (typeof index !== 'number' || !Number.isInteger(index)) return false
    const answer = await engine('GET', `/editor/projects/${projectId}`)
    const exports = answer.body?.exports
    if (!answer.ok || !Array.isArray(exports) || exports.length === 0) return false
    const entry = exports[index < 0 ? exports.length + index : index] as { path?: unknown } | undefined
    const path = entry?.path
    if (typeof path !== 'string' || !win32.isAbsolute(path)) return false
    shell.showItemInFolder(path)
    return true
  })

  // A native question for the Editor page (a native window is drawn above
  // the editor, which an in-page dialog would not be). The page gives the
  // words, already translated.
  ipcMain.handle('editor:ask', async (event, question: unknown) => {
    if (!ownFrame(event)) return -1
    const q = question as { message?: unknown; detail?: unknown; buttons?: unknown; defaultId?: unknown; cancelId?: unknown }
    if (typeof q?.message !== 'string' || !Array.isArray(q.buttons)) return -1
    const buttons = q.buttons.filter((b): b is string => typeof b === 'string').slice(0, 3).map((b) => b.slice(0, 80))
    if (buttons.length === 0) return -1
    const pick = (n: unknown, fallback: number): number =>
      typeof n === 'number' && Number.isInteger(n) && n >= 0 && n < buttons.length ? n : fallback
    const win = deps?.window()
    const options = {
      type: 'question' as const,
      title: 'Clips Kitty',
      message: q.message.slice(0, 400),
      detail: typeof q.detail === 'string' ? q.detail.slice(0, 800) : undefined,
      buttons,
      defaultId: pick(q.defaultId, 0),
      cancelId: pick(q.cancelId, buttons.length - 1),
      noLink: true
    }
    const answer = win && !win.isDestroyed() ? await dialog.showMessageBox(win, options) : await dialog.showMessageBox(options)
    return answer.response
  })
}
