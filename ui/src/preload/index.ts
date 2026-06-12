// The renderer talks to the local API over HTTP only; nothing from Node
// needs to be exposed yet. The bridge stays for future needs (e.g. native
// folder pickers) so the renderer never gains Node access directly.
import { contextBridge } from 'electron'

contextBridge.exposeInMainWorld('studio', {
  platform: process.platform
})
