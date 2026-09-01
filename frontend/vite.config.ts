import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// The flow view reads the backend's golden SimulationOutput directly rather than through a
// copy. A copy would drift from the fixture the backend tests pin, and then the view would be
// rendering something no test guards. `fs.allow` lets Vite serve it in dev; the production
// build inlines it at bundle time.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    fs: { allow: [path.resolve(__dirname), path.resolve(__dirname, '..', 'backend')] },
  },
})
