import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server on 5173; API base is read from VITE_API_BASE_URL (see .env.example).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
})
