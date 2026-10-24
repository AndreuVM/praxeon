import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// Si un fragmento de la interfaz ya no existe (se actualizó PRAXEON con la ventana abierta), se recarga una vez para coger la versión nueva
window.addEventListener('vite:preloadError', (event) => {
  event.preventDefault()
  try {
    const last = Number(sessionStorage.getItem('praxeon:preload-reload') || 0)
    if (Date.now() - last < 15000) return   // ya se recargó hace un momento: que lo gestione el aviso del panel
    sessionStorage.setItem('praxeon:preload-reload', String(Date.now()))
  } catch { /* sin sessionStorage: se recarga igualmente */ }
  window.location.reload()
})

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
