declare module '*.css'

/** Importing an image gives you its URL — Vite copies the file into the
 *  build and hands back a hashed path. Bundled rather than referenced from
 *  disk so it survives being packed into the installer, and so the renderer's
 *  strict CSP treats it as same-origin. */
declare module '*.png' {
  const src: string
  export default src
}

/** What the main process reports about updates. */
interface UpdateState {
  state: 'checking' | 'available' | 'none' | 'downloading' | 'ready' | 'error' | 'dev'
  version?: string
  notes?: string
  date?: string
  percent?: number
  transferred?: number
  total?: number
  bytesPerSecond?: number
  message?: string
}

interface Window {
  studio: {
    platform: string
    pickAudioFile: () => Promise<string | null>
    pickVideoFile: () => Promise<string | null>
    pickImageFile: () => Promise<string | null>
    getDownloadsPath: () => Promise<string>
    pickFolder: () => Promise<string | null>
    openDonateWindow: () => Promise<void>
    /** Opens an allow-listed URL in the user's browser. Resolves false if
     *  the main process refused it. */
    openExternal: (url: string) => Promise<boolean>
    update: {
      check: () => Promise<{ ok: boolean; reason?: string }>
      download: () => Promise<{ ok: boolean }>
      install: () => Promise<{ ok: boolean }>
      skip: (version: string) => Promise<{ ok: boolean }>
      prefs: (patch?: { channel?: string }) => Promise<{ channel: string; skipped?: string }>
      onState: (fn: (s: UpdateState) => void) => () => void
    }
  }
}
