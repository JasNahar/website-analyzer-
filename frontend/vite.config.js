import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // 127.0.0.1 rather than localhost: Node may resolve localhost to ::1, which uvicorn doesn't listen on.
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
