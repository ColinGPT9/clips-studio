declare module '*.css'

interface Window {
  studio: {
    platform: string
    pickAudioFile: () => Promise<string | null>
    pickVideoFile: () => Promise<string | null>
  }
}
