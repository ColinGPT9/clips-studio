declare module '*.css'

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
  }
}
