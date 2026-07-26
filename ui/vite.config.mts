/** Serves the renderer on its own, in a browser — `npm run dev:web`.
 *
 *  The app is built by electron-vite, which reads `electron.vite.config.ts`
 *  and knows about three bundles (main, preload, renderer). That config
 *  cannot be used to serve the renderer alone, and plain `vite` will not
 *  read it, so the web-only case needs this file.
 *
 *  Used by the Docker `ui` service, and by anyone who wants to work on the
 *  interface without launching Electron. Everything that talks to the engine
 *  works; the Electron-only calls are stubbed by `lib/browserShim.ts`.
 *
 *  Keep the plugin list in step with the `renderer` block of
 *  `electron.vite.config.ts` — if they drift, the browser and the real app
 *  stop rendering the same thing, which is the one failure this file could
 *  cause.
 *
 *  `.mts`, not `.ts`, and that is load-bearing. package.json has no
 *  `"type": "module"` — it cannot, because Electron's main process is CJS —
 *  so Vite would load a `.ts` config with `require`, and `@tailwindcss/vite`
 *  is ESM-only. The extension is what makes it ESM without touching the rest
 *  of the build.
 */

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/** Relax the page's CSP, for the browser dev server only.
 *
 *  `index.html` ships `default-src 'self'` with no `script-src`, which means
 *  inline scripts are refused — and Vite's React Fast Refresh preamble is an
 *  inline script. In a browser that leaves you editing components and seeing
 *  nothing change.
 *
 *  This rewrites the header in memory as the dev server serves the page. The
 *  file on disk is untouched, so the packaged app keeps the strict policy it
 *  is entitled to. Nothing here runs in a build.
 */
function relaxCspForBrowserDev(): Plugin {
  return {
    name: 'relax-csp-for-browser-dev',
    apply: 'serve',
    transformIndexHtml: {
      order: 'pre',
      handler: (html) =>
        html.replace(
          /<meta\s+http-equiv="Content-Security-Policy"[\s\S]*?\/>/,
          '<meta http-equiv="Content-Security-Policy" content="' +
            "default-src 'self'; " +
            // Fast Refresh preamble.
            "script-src 'self' 'unsafe-inline'; " +
            // The engine, plus Vite's own HMR socket on this origin.
            "connect-src 'self' ws: http://127.0.0.1:8765 ws://127.0.0.1:8765; " +
            'media-src http://127.0.0.1:8765; ' +
            "style-src 'self' 'unsafe-inline'\" />"
        )
    }
  }
}

export default defineConfig({
  root: 'src/renderer',
  plugins: [react(), tailwindcss(), relaxCspForBrowserDev()],
  server: {
    // 0.0.0.0 so the port is reachable from outside the container. The
    // published port is bound to 127.0.0.1 in docker-compose.yml, so this
    // does not put the dev server on the network.
    host: '0.0.0.0',
    port: 5173
  }
})
