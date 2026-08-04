/** Lets the interface run in a plain browser, for contributors.
 *
 *  `window.studio` is injected by Electron's preload. Open the Vite dev
 *  server directly — which is what the Docker `ui` service serves — and it
 *  does not exist, so the first click on anything native throws.
 *
 *  This installs a stand-in that says what it cannot do instead of failing
 *  silently. Everything that matters for interface work still functions,
 *  because that all goes over HTTP to the engine, not through Electron.
 *
 *  Never active under Electron: the real preload sets window.studio before
 *  any of this runs, and it is left strictly alone.
 */

function unavailable(what: string): void {
  console.warn(
    `[browser] ${what} needs the desktop app — it lives in Electron's preload. ` +
      'Run the app on your host for the full thing; see docs/DOCKER.md.'
  )
}

export function installBrowserShim(): void {
  if (window.studio) return // running under Electron; leave it alone

  console.info(
    '[browser] Running outside Electron. Native features are stubbed; ' +
      'everything that talks to the engine works normally.'
  )

  window.studio = {
    platform: 'browser',
    pickAudioFile: async () => {
      unavailable('Choosing an audio file')
      return null
    },
    pickVideoFile: async () => {
      unavailable('Choosing a video file')
      return null
    },
    pickVideoFiles: async () => {
      unavailable('Choosing video files')
      return []
    },
    pickImageFile: async () => {
      unavailable('Choosing an image')
      return null
    },
    pickFolder: async () => {
      unavailable('Choosing a folder')
      return null
    },
    // Somewhere plausible, so export flows can be walked through end to end.
    getDownloadsPath: async () => '/downloads',
    openDonateWindow: async () => {
      window.open('https://paypal.me/clipsstudio', '_blank', 'noopener')
    },
    openExternal: async (url: string) => {
      window.open(url, '_blank', 'noopener')
      return true
    },
    // The browser has its own Notification API, but it needs a permission
    // prompt this shim has no business triggering. The queue treats a false
    // return as "no notification happened", which is the truth here.
    notify: async () => {
      unavailable('Desktop notifications')
      return false
    },
    update: {
      check: async () => ({ ok: false, reason: 'browser' }),
      download: async () => ({ ok: false }),
      install: async () => ({ ok: false }),
      skip: async () => ({ ok: false }),
      prefs: async () => ({ channel: 'stable' }),
      // No updates in a browser, and no listener to clean up.
      onState: () => () => undefined
    }
  }
}
