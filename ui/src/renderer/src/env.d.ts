declare module '*.css'

interface Window {
  studio: {
    platform: string
    pickAudioFile: () => Promise<string | null>
    pickVideoFile: () => Promise<string | null>
    getDownloadsPath: () => Promise<string>
    pickFolder: () => Promise<string | null>
  }
}
