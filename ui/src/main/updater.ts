// Checking for a newer Clips Studio, and installing it.
//
// This exists because of when it has to exist: the moment somebody installs a
// build, you need a way to move them off it. Adding updates after a release
// strands that first group on a stale version permanently, because nothing in
// their copy knows to look.
//
// electron-updater reads the latest.yml that every build already produces and
// publishes beside the installer. Downloads are verified against the SHA512 in
// that file before anything is run.
//
// The feed is a plain HTTPS directory, not the GitHub releases API — the
// payload is too big for a release asset, so it lives on Hugging Face. See the
// publish block in electron-builder.yml.

import { app, ipcMain, type BrowserWindow } from 'electron'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { autoUpdater, type UpdateInfo } from 'electron-updater'
import { isMicrosoftStore } from './distribution'

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

/** Point the updater at the feed file for a channel.
 *
 *  The GitHub provider had a boolean for this — `allowPrerelease`, which let a
 *  release's pre-release flag do the work. A plain HTTPS feed has no flags:
 *  each channel is its own file sitting beside the installer (`latest.yml`,
 *  `beta.yml`, `alpha.yml`) and the updater fetches exactly the one named. */
function applyChannel(channel: Channel): void {
  autoUpdater.channel = channel === 'stable' ? 'latest' : channel
}

/** True while a pre-release check is in flight that a stable check will follow.
 *  Between pre-releases there is no alpha.yml to fetch and the 404 is normal,
 *  so it must not reach the user as an error. */
let willRetryOnStable = false

/** One update check, honouring the user's channel.
 *
 *  Alpha and beta are documented as seeing finished releases as well as
 *  pre-releases. One feed file cannot express that, so a pre-release channel
 *  that comes up empty falls back to the stable feed.
 */
async function check(): Promise<{ ok: boolean; reason?: string }> {
  const { channel } = loadPrefs()
  applyChannel(channel)
  willRetryOnStable = channel !== 'stable'
  try {
    await autoUpdater.checkForUpdates()
    return { ok: true }
  } catch (e) {
    if (!willRetryOnStable) return { ok: false, reason: String(e) }
    willRetryOnStable = false
    applyChannel('stable')
    try {
      await autoUpdater.checkForUpdates()
      return { ok: true }
    } catch (stableError) {
      return { ok: false, reason: String(stableError) }
    }
  } finally {
    willRetryOnStable = false
  }
}

export function setupUpdater(win: BrowserWindow): void {
  mainWindow = win

  // A Store copy is updated by the Store. electron-updater is not merely
  // unnecessary there, it is wrong: it would fetch the NSIS installer and try
  // to run it over a package Windows itself manages. So it is never wired up
  // at all — no listeners, no startup check — and the handlers below answer
  // honestly instead of pretending to check.
  if (isMicrosoftStore()) {
    const storeState = (): void => send('update:state', { state: 'store' })
    ipcMain.handle('update:check', async () => {
      storeState()
      return { ok: false, reason: 'store' }
    })
    ipcMain.handle('update:download', async () => ({ ok: false }))
    ipcMain.handle('update:install', () => ({ ok: false }))
    ipcMain.handle('update:skip', () => ({ ok: false }))
    // Channel is still readable and writable so the screen renders, but it
    // decides nothing here.
    ipcMain.handle('update:prefs', () => loadPrefs())
    storeState()
    return
  }

  // Never download behind the user's back. A 2 GB payload starting by itself
  // on someone's home connection, mid-render, is hostile.
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = false
  applyChannel(loadPrefs().channel)
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

  autoUpdater.on('error', (err) => {
    // An empty pre-release feed is about to be retried against stable, and
    // the retry reports the real outcome.
    if (willRetryOnStable) return
    // Being unable to check is not worth interrupting anyone over — they may
    // simply be offline. The renderer shows this only if a check was asked for.
    send('update:state', { state: 'error', message: String(err?.message ?? err) })
  })

  ipcMain.handle('update:check', async () => {
    if (!app.isPackaged) {
      // In dev there is no app-update.yml and electron-updater throws.
      send('update:state', { state: 'dev' })
      return { ok: false, reason: 'dev' }
    }
    return check()
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
      check().catch(() => undefined)
    }, 8000)
  }
}
