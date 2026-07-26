import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AppBoundary from './components/AppBoundary'
import './theme.css'
import { applyAppearance, loadAppearance } from './lib/appearance'
import { installBrowserShim } from './lib/browserShim'

// Before anything renders. Under Electron this returns immediately; it only
// does something when the renderer is opened in a browser, which is how the
// Docker `ui` service serves it.
installBrowserShim()

applyAppearance(loadAppearance())

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    {/* Outside App on purpose: a crash in App's own render has to be caught
        too. A blank window is the one failure that tells nobody anything. */}
    <AppBoundary>
      <App />
    </AppBoundary>
  </React.StrictMode>
)
