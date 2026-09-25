import {
  app,
  BrowserWindow,
  Menu,
  Notification,
  Tray,
  clipboard,
  dialog,
  ipcMain,
  nativeImage,
  shell
} from 'electron'
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, join } from 'node:path'
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

// ---- keep watching in the tray (opt-in) ------------------------------------
//
// Off by default, and while it is off nothing below changes anything: closing
// the window quits, exactly as it always has. It exists for people who leave
// Clips Kitty on a spare PC to watch channels (server/automation.py), where
// closing the window must not stop the watching.

let mainWindow: BrowserWindow | null = null
let tray: Tray | null = null
/** True once a real quit is under way: from the tray, the updater, Windows
 *  shutting down. Then closing the window closes it instead of hiding it. */
let quitting = false
let keepInTray = false
let toldAboutTray = false

function trayPrefsPath(): string {
  return join(app.getPath('userData'), 'tray.json')
}

function loadKeepInTray(): boolean {
  try {
    return JSON.parse(readFileSync(trayPrefsPath(), 'utf-8')).keepInTray === true
  } catch {
    return false // never set, or unreadable: the default, which is off
  }
}

function showWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow()
    return
  }
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.show()
  mainWindow.focus()
}

async function ensureTray(): Promise<void> {
  if (tray) return
  // The app's own icon, taken from the running executable, so there is no
  // separate image to ship or to fall out of step with the installer's.
  const icon = await app
    .getFileIcon(process.execPath, { size: 'small' })
    .catch(() => nativeImage.createEmpty())
  if (tray) return // a second close raced this one
  tray = new Tray(icon)
  tray.setToolTip('Clips Kitty: watching for new videos')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Open Clips Kitty', click: showWindow },
      { type: 'separator' },
      {
        label: 'Quit Clips Kitty',
        click: () => {
          quitting = true
          app.quit()
        }
      }
    ])
  )
  tray.on('click', showWindow)
}

function dropTray(): void {
  tray?.destroy()
  tray = null
}

ipcMain.handle('tray:get', () => ({ keepInTray }))

ipcMain.handle('tray:set', (_event, on: unknown) => {
  keepInTray = on === true
  try {
    writeFileSync(trayPrefsPath(), JSON.stringify({ keepInTray }))
  } catch {
    // Not saved: it still applies until the app quits.
  }
  if (!keepInTray) dropTray()
  return { keepInTray }
})

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
  // The 1.1.3 rename (d708b34) moved this folder to "Clips Kitty" while
  // core/paths.py rightly kept the data folder at "Clips Studio", so the two
  // stopped matching: models from earlier versions were stranded and fetched
  // again. Keep whichever folder already holds models, so 1.1.3 and 1.1.4
  // installs keep theirs; otherwise use the data folder's own name.
  const kittyModels = join(localAppData, 'Clips Kitty', 'data', 'models')
  const models = existsSync(join(kittyModels, 'manifests'))
    ? kittyModels
    : join(localAppData, 'Clips Studio', 'data', 'models')

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

  mainWindow = win
  setupUpdater(win)

  // With "keep watching" on, closing hides the window and the backend keeps
  // running. A real quit sets `quitting` first, so it is never intercepted.
  win.on('close', (event) => {
    if (quitting || !keepInTray) return
    event.preventDefault()
    win.hide()
    void ensureTray()
    if (!toldAboutTray && Notification.isSupported()) {
      toldAboutTray = true
      new Notification({
        title: 'Clips Kitty is still watching',
        body: 'It keeps running in the system tray. Quit it from the tray icon.'
      }).show()
    }
  })
  // Windows logging off or shutting down skips before-quit, so say it here.
  win.on('session-end', () => {
    quitting = true
  })
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null
  })

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

// YouTube's thumbnail limit, mirrored from publish/images.py so an oversized
// file is refused before it becomes a base64 string.
const THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024

// Thumbnails come back as DATA, not as a path, and that is the point: this
// process ran the dialog, so it already has the file. Handing the renderer a
// path to POST would mean the backend re-opening an arbitrary path from an
// unauthenticated local endpoint, which is a worse trade than a base64 string
// for an image capped at 2 MB.
ipcMain.handle('pick-thumbnail-image', async () => {
  const result = await dialog.showOpenDialog({
    title: 'Choose a thumbnail',
    properties: ['openFile'],
    // YouTube takes JPEG and PNG. WebP is deliberately absent.
    filters: [{ name: 'Image', extensions: ['png', 'jpg', 'jpeg'] }]
  })
  if (result.canceled || !result.filePaths[0]) return null
  const path = result.filePaths[0]
  try {
    const { open } = await import('node:fs/promises')
    // One handle for both the size check and the read. Calling stat(path) and
    // then readFile(path) looks equivalent but resolves the name twice, so
    // what gets measured and what gets read are not guaranteed to be the same
    // file — swap it in between and the size limit measures nothing.
    const handle = await open(path, 'r')
    try {
      // Checked here as well as in the backend, so a 40 MB photo is never
      // turned into a 53 MB base64 string and pushed through IPC just to be
      // rejected at the other end.
      const info = await handle.stat()
      if (info.size > THUMBNAIL_MAX_BYTES) return { error: 'too-large' }
      const bytes = await handle.readFile()
      return { name: basename(path), data: bytes.toString('base64') }
    } finally {
      await handle.close()
    }
  } catch {
    return { error: 'unreadable' }
  }
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
  // Settings → AI, bring your own key: each cloud provider's key and pricing
  // pages (llm/providers/*.py key_url and pricing_url). A provider added
  // there needs its hosts added here, or its "Get a key" link does nothing.
  /^https:\/\/openrouter\.ai\//,
  /^https:\/\/(platform|developers)\.openai\.com\//,
  /^https:\/\/aistudio\.google\.com\//,
  /^https:\/\/ai\.google\.dev\//,
  /^https:\/\/(console|www)\.anthropic\.com\//,
  /^https:\/\/(console|docs)\.x\.ai\//,
  /^https:\/\/dev\.meta\.ai\//,
  /^https:\/\/github\.com\/ColinGPT9\/clips-studio(\/|$)/,
  // YouTube publishing: the setup wizard sends people to Cloud Console and
  // the audit form, and a published clip links to its own watch/Studio page.
  /^https:\/\/console\.cloud\.google\.com\//,
  /^https:\/\/studio\.youtube\.com\//,
  // Google's OAuth consent screen. A provider's "connect YouTube" flow hands
  // back a URL on THIS host, not on the provider's own domain, which is the
  // one thing Google does differently from every other platform here: TikTok,
  // Meta, X, LinkedIn and Pinterest all authorise on a domain already listed
  // below. Missing it made Connect do nothing at all, four clicks in a row,
  // logging "refused to open external url" and showing the user nothing.
  // It must open in the system browser: Google rejects OAuth inside an
  // embedded webview with disallowed_useragent, and offers no way to opt out.
  /^https:\/\/accounts\.google\.com\//,
  /^https:\/\/www\.youtube\.com\/watch\?v=/,
  // A watched channel's videos, opened from the Watch page.
  /^https:\/\/www\.twitch\.tv\/videos\/\d+$/,
  /^https:\/\/kick\.com\/[\w-]+\/videos\/[0-9a-fA-F-]{36}$/,
  /^https:\/\/support\.google\.com\/youtube\//,
  /^https:\/\/developers\.google\.com\/youtube\//,
  // Upload-Post: the hosted page where a user links their social accounts,
  // their dashboard, and the signup link. Subdomains are allowed because the
  // connect flow lives on app.upload-post.com while signup is on www.
  //
  // If an approved affiliate referral URL is ever configured and it points at
  // a DIFFERENT host — Trackdesk hands out links like
  // <name>.trackdesk.com/... — that host has to be added here too, or the
  // button will do nothing at all and log a refusal rather than failing
  // visibly. This is the trap to check first if a referral link seems dead.
  /^https:\/\/([a-z0-9-]+\.)?upload-post\.com(\/|$|\?)/,
  // WoopSocial, the second publishing provider: their site, the dashboard
  // where the API key lives, and the callback each OAuth flow returns to.
  // The consent screen itself is on the platform's own host, not here.
  // Endorsely hosts their affiliate signup and would issue a referral link
  // on that domain, so it is allowed too — otherwise the button is dead.
  /^https:\/\/([a-z0-9-]+\.)?woopsocial\.com(\/|$|\?)/,
  /^https:\/\/([a-z0-9-]+\.)?endorsely\.com(\/|$|\?)/,
  // Where a published clip actually ended up. Upload-Post returns one URL per
  // platform and the publish panel turns each into an "Open" button; without
  // these the buttons are silently inert. Host-restricted, since the path
  // shape differs per platform and changes without notice.
  /^https:\/\/(www\.)?youtu\.be\//,
  /^https:\/\/(www\.)?tiktok\.com\//,
  /^https:\/\/(www\.)?instagram\.com\//,
  /^https:\/\/(www\.|web\.)?facebook\.com\//,
  /^https:\/\/(www\.)?(x|twitter)\.com\//,
  /^https:\/\/(www\.)?threads\.(net|com)\//,
  /^https:\/\/(www\.)?linkedin\.com\//,
  /^https:\/\/([a-z]{2}\.|www\.)?pinterest\.[a-z.]{2,6}\//,
  /^https:\/\/(www\.)?bsky\.app\//,
  /^https:\/\/(www\.)?reddit\.com\//
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

// The one fiddly step of bring-your-own-key setup is copying a key off a
// website and getting it back into here. This closes that gap — but it
// deliberately does NOT hand the renderer whatever happens to be on the
// clipboard.
//
// Only something that looks like an API key comes back: one token, no
// whitespace, long enough to be a credential and short enough not to be a
// paragraph. Anything else returns empty, so a password or a private message
// sitting on the clipboard is never readable from the page.
const KEY_SHAPE = /^[A-Za-z0-9_\-.]{20,200}$/

ipcMain.handle('read-clipboard-key', () => {
  const text = clipboard.readText().trim()
  return KEY_SHAPE.test(text) ? text : ''
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

// One Clips Kitty at a time. A second launch used to start a second engine
// that could not bind port 8765 and a window talking to the first one's; with
// the window hidden in the tray it would look like the app had not started at
// all. Now it brings the running one forward instead.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', showWindow)

  app.whenReady().then(() => {
    // Windows shows the AppUserModelID as the notification's app name; without
    // it a toast is attributed to "electron.app.Electron". Must match
    // electron-builder.yml's appId so dev and packaged builds agree.
    app.setAppUserModelId('com.clipsstudio.app')
    keepInTray = loadKeepInTray()
    // Ollama first: it takes a moment to bind its port, and starting it before
    // the engine means the first preflight is more likely to find it up.
    startOllama()
    startBackend()
    createWindow()
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })
}

app.on('window-all-closed', () => {
  stopChildren()
  app.quit()
})

// Also covers the routes that skip window-all-closed — notably the updater's
// quitAndInstall, which must not leave an Ollama holding the install folder
// open while the installer tries to replace it. It fires before any window is
// asked to close, so marking the quit here is what lets the updater through a
// window that would otherwise hide itself in the tray.
app.on('before-quit', () => {
  quitting = true
  dropTray()
  stopChildren()
})
