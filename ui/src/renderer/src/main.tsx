import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import AppBoundary from './components/AppBoundary'
import './theme.css'
import { applyAppearance, loadAppearance } from './lib/appearance'

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
