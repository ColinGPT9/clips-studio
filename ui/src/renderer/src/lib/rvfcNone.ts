/** Stands in for "rvfc-polyfill", which JASSUB imports to add
 *  requestVideoFrameCallback to browsers that lack it. Electron's Chromium
 *  and Edge have it built in, and the viewer draws captions by program time
 *  (JASSUB's canvas mode, not a <video>), so the polyfill would never run.
 *  It is GPL-3.0, so it is left out of the app altogether: electron.vite.
 *  config.ts and vite.config.mts point the name here. */
export {}
