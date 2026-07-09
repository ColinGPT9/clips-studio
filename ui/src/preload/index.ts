// The renderer talks to the local API over HTTP; only narrow native
// affordances are exposed here, so the renderer never gains Node access.
import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('studio', {
  platform: process.platform,
  // Native audio-file picker (editor's background-music field).
  pickAudioFile: (): Promise<string | null> => ipcRenderer.invoke('pick-audio-file'),
  // Native video-file picker (Dashboard "upload a video file").
  pickVideoFile: (): Promise<string | null> => ipcRenderer.invoke('pick-video-file')
})
