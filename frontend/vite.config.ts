import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(['/api', '/health', '/ready'].map((path) => [path, process.env.FORMA_DEV_API ?? 'http://127.0.0.1:8000'])),
  },
  build: { outDir: 'dist', sourcemap: false },
})
