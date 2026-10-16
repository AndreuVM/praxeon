import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Pantalla de arranque (definida en index.html): se deja ver la animación completa y se desvanece
const splash = document.getElementById('splash')
if (splash) {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  const minMs = reduced ? 300 : 1900
  const wait = Math.max(0, minMs - performance.now())
  setTimeout(() => {
    splash.classList.add('is-hiding')
    setTimeout(() => splash.remove(), 500)
  }, wait)
}
