// Checking GitHub Releases for a newer Clips Studio, and installing it.
//
// This exists because of when it has to exist: the moment somebody installs a
// build, you need a way to move them off it. Adding updates after a release
// strands that first group on a stale version permanently, because nothing in
// their copy knows to look.
//
// electron-updater reads the latest.yml that every build already produces and
// publishes beside the installer. Downloads are verified against the SHA512 in
// that file before anything is run.

import { app, ipcMain, type BrowserWindow } from 'electron'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { autoUpdater, type UpdateInfo } from 'electron-updater'

/** Which releases this install is offered. Alpha sees everything, stable
 *  only sees finished releases. Read from the same file the renderer writes,
 *  so switching channel needs no restart of anything but the check. */
export type Channel = 'stable' | 'beta' | 'alpha'

interface UpdatePrefs {
  channel: Channel
  /** A version the user chose to skip; never offered again. */
  skipped?: string
}

const PREFS_FILE = (): string => join(app.getPath('userData'), 'update-prefs.json')

function loadPrefs(): UpdatePrefs {
  try {
    return { channel: 'stable', ...JSON.parse(readFileSync(PREFS_FILE(), 'utf8')) }
  } catch {
    return { channel: 'stable' }
  }
}

function savePrefs(prefs: UpdatePrefs): void {
  try {
    writeFileSync(PREFS_FILE(), JSON.stringify(prefs, null, 2), 'utf8')
  } catch (e) {
    console.warn('could not save update preferences:', e)
  }
}

let mainWindow: BrowserWindow | null = null

function send(channel: string, payload: unknown): void {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload)
}

export function setupUpdater(win: BrowserWindow): void {
  mainWindow = win

  // Never download behind the user's back. A 2 GB payload starting by itself
  // on someone's home connection, mid-render, is hostile.
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = false
  autoUpdater.allowPrerelease = loadPrefs().channel !== 'stable'
  autoUpdater.logger = null

  autoUpdater.on('checking-for-update', () => send('update:state', { state: 'checking' }))

  autoUpdater.on('update-available', (info: UpdateInfo) => {
    const prefs = loadPrefs()
    if (prefs.skipped && prefs.skipped === info.version) {
      send('update:state', { state: 'none' })
      return
    }
    send('update:state', {
      state: 'available',
      version: info.version,
      notes: typeof info.releaseNotes === 'string' ? info.releaseNotes : '',
      date: info.releaseDate
    })
  })

  autoUpdater.on('update-not-available', () => send('update:state', { state: 'none' }))

  autoUpdater.on('download-progress', (p) =>
    send('update:state', {
      state: 'downloading',
      percent: Math.round(p.percent),
      transferred: p.transferred,
      total: p.total,
      bytesPerSecond: p.bytesPerSecond
    })
  )

  autoUpdater.on('update-downloaded', (info: UpdateInfo) =>
    send('update:state', { state: 'ready', version: info.version })
  )

  autoUpdater.on('error', (err) =>
    // Being unable to check is not worth interrupting anyone over — they may
    // simply be offline. The renderer shows this only if a check was asked for.
    send('update:state', { state: 'error', message: String(err?.message ?? err) })
  )

  ipcMain.handle('update:check', async () => {
    if (!app.isPackaged) {
      // In dev there is no app-update.yml and electron-updater throws.
      send('update:state', { state: 'dev' })
      return { ok: false, reason: 'dev' }
    }
    autoUpdater.allowPrerelease = loadPrefs().channel !== 'stable'
    try {
      await autoUpdater.checkForUpdates()
      return { ok: true }
    } catch (e) {
      return { ok: false, reason: String(e) }
    }
  })

  ipcMain.handle('update:download', async () => {
    try {
      await autoUpdater.downloadUpdate()
      return { ok: true }
    } catch (e) {
      send('update:state', { state: 'error', message: String(e) })
      return { ok: false }
    }
  })

  // Quits and runs the installer. Anything mid-render is lost, so the
  // renderer asks first.
  ipcMain.handle('update:install', () => {
    autoUpdater.quitAndInstall(false, true)
    return { ok: true }
  })

  ipcMain.handle('update:skip', (_e, version: unknown) => {
    if (typeof version !== 'string') return { ok: false }
    savePrefs({ ...loadPrefs(), skipped: version })
    send('update:state', { state: 'none' })
    return { ok: true }
  })

  ipcMain.handle('update:prefs', (_e, patch: unknown) => {
    const prefs = loadPrefs()
    if (patch && typeof patch === 'object') {
      const next = { ...prefs, ...(patch as Partial<UpdatePrefs>) }
      // Changing channel clears a skip: someone moving to beta wants to see
      // what beta has, including a version they skipped on stable.
      if (next.channel !== prefs.channel) delete next.skipped
      savePrefs(next)
      return next
    }
    return prefs
  })

  // One quiet check a few seconds after launch, once the app is responsive.
  // Failures here are silent by design.
  if (app.isPackaged) {
    setTimeout(() => {
      autoUpdater.checkForUpdates().catch(() => undefined)
    }, 8000)
  }
}
