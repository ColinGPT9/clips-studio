import { resolve } from 'path'
import { defineConfig } from 'electron-vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    plugins: [react(), tailwindcss()],
    resolve: {
      // JASSUB's requestVideoFrameCallback polyfill is never needed here
      // (Chromium has it) and is GPL-3.0: left out (lib/rvfcNone.ts).
      alias: { 'rvfc-polyfill': resolve(__dirname, 'src/renderer/src/lib/rvfcNone.ts') }
    },
    // JASSUB's worker is an ES module with imports of its own.
    worker: { format: 'es' },
    // Served as it is in development, so its worker and WebAssembly are
    // found beside it (pre-bundling would move it away from them); its
    // one CommonJS import is pre-bundled.
    optimizeDeps: { exclude: ['jassub'], include: ['jassub > throughput'] }
  }
})
