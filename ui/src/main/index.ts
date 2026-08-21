import { app, BrowserWindow, Menu, Notification, dialog, ipcMain, shell } from 'electron'
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import { join } from 'node:path'
import { setupUpdater } from './updater'
import { isMicrosoftStore } from './distribution'

const API_PORT = 8765

// One definition for both routes below: the in-app popup and, for Store
// copies, the system browser. They must not be able to drift apart.
const DONATE_URL = 'https://paypal.me/clipsstudio'

// The bundled Ollama listens here instead of on 11434, its default. A creator
// who already runs Ollama owns that port, and two servers fighting over it
// fails confusingly for both. On a port of our own the two simply coexist.
const OLLAMA_PORT = 11435
const OLLAMA_HOST = `127.0.0.1:${OLLAMA_PORT}`

let backend: ChildProcess | null = null
let ollama: ChildProcess | null = null

/** Start the Ollama runtime that ships inside the app.
 *
 *  Packaged builds carry their own copy (see scripts/fetch_ollama.py) so that
 *  installing Clips Kitty installs everything Clips Kitty needs. In a
 *  checkout there is nothing to start: a developer already has Ollama on its
 *  default port, and the engine falls back to that because startBackend only
 *  overrides the host when packaged.
 */
function startOllama(): void {
  if (!app.isPackaged) return

  // PyInstaller puts bundled data under _internal/, which is where the spec
  // places the runtime — the same shape as _internal/ffmpeg.
  const exe = join(process.resourcesPath, 'backend', '_internal', 'ollama', 'ollama.exe')

  // Models are gigabytes, so they live with the creator's other big files
  // rather than in their user profile. This must match how data_dir resolves
  // in core/paths.py, or the engine and the runtime disagree about what is
  // downloaded.
  const localAppData = process.env.LOCALAPPDATA ?? join(app.getPath('home'), 'AppData', 'Local')
  const models = join(localAppData, 'Clips Kitty', 'data', 'models')

  ollama = spawn(exe, ['serve'], {
    stdio: 'ignore',
    env: { ...process.env, OLLAMA_HOST, OLLAMA_MODELS: models },
    windowsHide: true
  })
  ollama.on('error', (e) => console.error(`bundled Ollama could not start: ${e.message}`))
  ollama.on('exit', (code) => {
    if (code !== 0 && code !== null) console.error(`Ollama exited with code ${code}`)
  })
}

/** Stop a child process and everything it spawned.
 *
 *  Both children have grandchildren that matter: the engine runs FFmpeg, and
 *  Ollama runs model inference in separate runner processes. Killing only the
 *  parent leaves those behind — a stranded FFmpeg writing to a clip nobody is
 *  waiting for, or a runner still holding VRAM, which reads to the creator as
 *  the app leaking their GPU. Windows has no process group to signal, so the
 *  tree has to be taken down by hand.
 */
function killTree(child: ChildProcess | null): void {
  if (!child?.pid) return
  if (process.platform === 'win32') {
    try {
      execFileSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
      return
    } catch {
      // Already exited, or taskkill is unavailable — fall through to a signal.
    }
  }
  child.kill()
}

/** Shut both children down. Safe to call twice: quitting can arrive by more
 *  than one route, and the updater's quitAndInstall is one of them. */
function stopChildren(): void {
  killTree(backend)
  killTree(ollama)
  backend = null
  ollama = null
}

function startBackend(): void {
  // Windows gives a spawned process the system locale's encoding, which is
  // cp1252 on most Western installs — and printing a title with an emoji in
  // it then throws UnicodeEncodeError and kills the backend. A developer's
  // own terminal usually has UTF-8 configured, so this only shows up once
  // someone else installs the app. The backend forces UTF-8 itself too;
  // this covers it before a single line of Python runs.
  const backendEnv: NodeJS.ProcessEnv = {
    ...process.env,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1'
  }

  // Packaged builds run their own Ollama on a private port, so the engine has
  // to be told where it is — settings.yaml's default 11434 would send it to a
  // system install the creator may not have.
  if (app.isPackaged) backendEnv.CLIPS_STUDIO_OLLAMA_HOST = `http://${OLLAMA_HOST}`

  // Dev: run the repo's Python directly (repo root is one level up from ui/).
  // Packaged: run the frozen backend exe shipped in resources/backend/.
  if (app.isPackaged) {
    const exe = join(process.resourcesPath, 'backend', 'api.exe')
    // The backend is built as a console app so its prints have somewhere to
    // go (a windowed build gives it no stdout, and every print() then
    // throws). windowsHide keeps that console from flashing up at the user.
    backend = spawn(exe, ['serve', '--port', String(API_PORT)], {
      stdio: 'ignore',
      env: backendEnv,
      windowsHide: true
    })
  } else if (process.env.BACKEND_EXTERNAL !== '1') {
    const repoRoot = join(app.getAppPath(), '..')
    backend = spawn('python', ['main.py', 'serve', '--port', String(API_PORT)], {
      cwd: repoRoot,
      stdio: 'inherit',
      env: backendEnv
    })
  }
  backend?.on('exit', (code) => {
    if (code !== 0 && code !== null) console.error(`backend exited with code ${code}`)
  })
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: '#0A1628',
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  })

  setupUpdater(win)

  // External links open in the system browser, never inside the app.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  // Electron ships no right-click menu at all — text boxes need one.
  win.webContents.on('context-menu', (_event, params) => {
    if (params.isEditable) {
      Menu.buildFromTemplate([
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { type: 'separator' },
        { role: 'selectAll' }
      ]).popup()
    } else if (params.selectionText) {
      Menu.buildFromTemplate([{ role: 'copy' }]).popup()
    }
  })

  if (process.env.ELECTRON_RENDERER_URL) {
    win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    win.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// Native file picker for the editor's music field: returns the real path of
// a local audio file (renderer stays sandboxed, no Node access needed).
ipcMain.handle('pick-audio-file', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Choose background music',
    properties: ['openFile'],
    filters: [
      { name: 'Audio', extensions: ['mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac'] },
      { name: 'All files', extensions: ['*'] }
    ]
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('pick-video-file', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Choose a video to make clips from',
    properties: ['openFile'],
    filters: [
      { name: 'Video', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'm4v', 'ts', 'flv'] },
      { name: 'All files', extensions: ['*'] }
    ]
  })
  return result.canceled ? null : result.filePaths[0]
})

ipcMain.handle('pick-video-files', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Choose videos to add to the queue',
    properties: ['openFile', 'multiSelections'],
    filters: [
      { name: 'Video', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'm4v', 'ts', 'flv'] },
      { name: 'All files', extensions: ['*'] }
    ]
  })
  return result.canceled ? [] : result.filePaths
})

// Desktop notification for a finished video / finished queue. Text only: no
// path, no URL, no action. The renderer already has this text on screen — this
// puts it somewhere visible while the window is behind something else, which
// is the entire point of being able to walk away from a long batch.
ipcMain.handle('notify', (event, payload: unknown) => {
  if (!Notification.isSupported()) return false
  const { title, body } = (payload ?? {}) as { title?: unknown; body?: unknown }
  if (typeof title !== 'string' || typeof body !== 'string') return false
  const n = new Notification({ title: title.slice(0, 120), body: body.slice(0, 300) })
  n.on('click', () => {
    const w = BrowserWindow.fromWebContents(event.sender)
    w?.show()
    w?.focus()
  })
  n.show()
  return true
})

ipcMain.handle('pick-image-file', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Choose a logo image',
    properties: ['openFile'],
    filters: [
      { name: 'Image', extensions: ['png', 'jpg', 'jpeg', 'webp'] },
      { name: 'All files', extensions: ['*'] }
    ]
  })
  return result.canceled ? null : result.filePaths[0]
})

// Donation popup: PayPal opens in a small in-app window instead of the
// external browser. It is a locked-down Chromium window showing the REAL
// paypal.me page — no Node access, no preload, and any attempt by the page
// to open further windows goes to the system browser instead.
ipcMain.handle('open-donate-window', (event) => {
  // Store policy 10.8.2 permits a third-party payment API and says plainly
  // that "users may be directed to a browser to complete registration or
  // transactions". Taking that route means a Store copy hands PayPal to the
  // system browser rather than hosting a payment page itself, so there is no
  // in-app payment experience for certification to assess.
  if (isMicrosoftStore()) {
    void shell.openExternal(DONATE_URL)
    return
  }

  const parent = BrowserWindow.fromWebContents(event.sender) ?? undefined
  const win = new BrowserWindow({
    width: 480,
    height: 720,
    parent,
    modal: false,
    autoHideMenuBar: true,
    title: 'Donate — paypal.me/clipsstudio',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  })
  // Keep the popup pinned to PayPal: external links (terms, help, …) go to
  // the system browser rather than navigating the popup somewhere else.
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (!/^https:\/\/([\w-]+\.)*paypal\.(com|me)\//.test(url)) {
      e.preventDefault()
      shell.openExternal(url)
    }
  })
  // The page title always shows where the user really is.
  win.on('page-title-updated', (e) => e.preventDefault())
  void win.loadURL(DONATE_URL)
})

// The OS Downloads folder — the default export destination, like other
// video editors.
// Open a link in the user's own browser. Allow-listed rather than open:
// the renderer must never be able to hand an arbitrary URL — or a file://
// or other scheme — to the OS. The setup wizard uses this to send people to
// Ollama, which is deliberately not bundled.
const EXTERNAL_ALLOWED = [
  /^https:\/\/ollama\.com\//,
  /^https:\/\/github\.com\/ColinGPT9\/clips-studio(\/|$)/
]

ipcMain.handle('open-external', (_event, url: unknown) => {
  if (typeof url !== 'string') return false
  if (!EXTERNAL_ALLOWED.some((re) => re.test(url))) {
    console.warn(`refused to open external url: ${url}`)
    return false
  }
  void shell.openExternal(url)
  return true
})

ipcMain.handle('get-downloads-path', () => app.getPath('downloads'))

// Folder picker for choosing where exported clips are saved.
ipcMain.handle('pick-folder', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Choose where to save exported clips',
    defaultPath: app.getPath('downloads'),
    properties: ['openDirectory', 'createDirectory']
  })
  return result.canceled ? null : result.filePaths[0]
})

app.whenReady().then(() => {
  // Windows shows the AppUserModelID as the notification's app name; without
  // it a toast is attributed to "electron.app.Electron". Must match
  // electron-builder.yml's appId so dev and packaged builds agree.
  app.setAppUserModelId('com.clipsstudio.app')
  // Ollama first: it takes a moment to bind its port, and starting it before
  // the engine means the first preflight is more likely to find it up.
  startOllama()
  startBackend()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  stopChildren()
  app.quit()
})

// Also covers the routes that skip window-all-closed — notably the updater's
// quitAndInstall, which must not leave an Ollama holding the install folder
// open while the installer tries to replace it.
app.on('before-quit', stopChildren)
