// The renderer talks to the local API over HTTP; only narrow native
// affordances are exposed here, so the renderer never gains Node access.
import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('studio', {
  platform: process.platform,
  // Native audio-file picker (editor's background-music field).
  pickAudioFile: (): Promise<string | null> => ipcRenderer.invoke('pick-audio-file'),
  // Native video-file picker (Dashboard "upload a video file").
  pickVideoFile: (): Promise<string | null> => ipcRenderer.invoke('pick-video-file'),
  // Multi-select variant, for queueing a batch of local files at once.
  pickVideoFiles: (): Promise<string[]> => ipcRenderer.invoke('pick-video-files'),
  // Native image-file picker (watermark logo upload).
  pickImageFile: (): Promise<string | null> => ipcRenderer.invoke('pick-image-file'),
  // Export destination: the OS Downloads folder + a folder picker.
  getDownloadsPath: (): Promise<string> => ipcRenderer.invoke('get-downloads-path'),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('pick-folder'),
  // Donation popup: PayPal in a small in-app window (no external browser).
  openDonateWindow: (): Promise<void> => ipcRenderer.invoke('open-donate-window'),
  // Desktop notification when a queued video finishes. Text only — the main
  // process builds the toast, so the renderer cannot attach actions or links.
  notify: (title: string, body: string): Promise<boolean> =>
    ipcRenderer.invoke('notify', { title, body }),
  // Open a link in the real browser. The main process allow-lists which
  // hosts are permitted, so this cannot be used to launch arbitrary URLs.
  openExternal: (url: string): Promise<boolean> =>
    ipcRenderer.invoke('open-external', url),

  // Updates. The renderer never touches electron-updater directly; it asks
  // the main process and listens for state.
  update: {
    check: (): Promise<{ ok: boolean; reason?: string }> => ipcRenderer.invoke('update:check'),
    download: (): Promise<{ ok: boolean }> => ipcRenderer.invoke('update:download'),
    install: (): Promise<{ ok: boolean }> => ipcRenderer.invoke('update:install'),
    skip: (version: string): Promise<{ ok: boolean }> =>
      ipcRenderer.invoke('update:skip', version),
    prefs: (patch?: { channel?: string }): Promise<{ channel: string; skipped?: string }> =>
      ipcRenderer.invoke('update:prefs', patch),
    /** Subscribe to update state. Returns an unsubscribe function. */
    onState: (fn: (s: Record<string, unknown>) => void): (() => void) => {
      const handler = (_e: unknown, s: Record<string, unknown>): void => fn(s)
      ipcRenderer.on('update:state', handler)
      return () => ipcRenderer.removeListener('update:state', handler)
    }
  }
})
