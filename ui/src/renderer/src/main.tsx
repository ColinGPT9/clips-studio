import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './theme.css'
import { applyAppearance, loadAppearance } from './lib/appearance'

applyAppearance(loadAppearance())

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
