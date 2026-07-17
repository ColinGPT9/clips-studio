// The renderer talks to the local API over HTTP; only narrow native
// affordances are exposed here, so the renderer never gains Node access.
import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('studio', {
  platform: process.platform,
  // Native audio-file picker (editor's background-music field).
  pickAudioFile: (): Promise<string | null> => ipcRenderer.invoke('pick-audio-file'),
  // Native video-file picker (Dashboard "upload a video file").
  pickVideoFile: (): Promise<string | null> => ipcRenderer.invoke('pick-video-file'),
  // Native image-file picker (watermark logo upload).
  pickImageFile: (): Promise<string | null> => ipcRenderer.invoke('pick-image-file'),
  // Export destination: the OS Downloads folder + a folder picker.
  getDownloadsPath: (): Promise<string> => ipcRenderer.invoke('get-downloads-path'),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('pick-folder'),
  // Donation popup: PayPal in a small in-app window (no external browser).
  openDonateWindow: (): Promise<void> => ipcRenderer.invoke('open-donate-window')
})
