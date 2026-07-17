import { app, BrowserWindow, Menu, dialog, ipcMain, shell } from 'electron'
import { spawn, type ChildProcess } from 'node:child_process'
import { join } from 'node:path'

const API_PORT = 8765
let backend: ChildProcess | null = null

function startBackend(): void {
  // Dev: run the repo's Python directly (repo root is one level up from ui/).
  // Packaged: run the frozen backend exe shipped in resources/backend/.
  if (app.isPackaged) {
    const exe = join(process.resourcesPath, 'backend', 'api.exe')
    backend = spawn(exe, ['serve', '--port', String(API_PORT)], { stdio: 'ignore' })
  } else if (process.env.BACKEND_EXTERNAL !== '1') {
    const repoRoot = join(app.getAppPath(), '..')
    backend = spawn('python', ['main.py', 'serve', '--port', String(API_PORT)], {
      cwd: repoRoot,
      stdio: 'inherit'
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
  void win.loadURL('https://paypal.me/clipsstudio')
})

// The OS Downloads folder — the default export destination, like other
// video editors.
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
  startBackend()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  backend?.kill()
  app.quit()
})
