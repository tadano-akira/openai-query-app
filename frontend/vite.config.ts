import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// コンテナ内では backend サービス名で FastAPI に転送する
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    // Windows のバインドマウントでは変更通知が届かないためポーリングする
    watch: { usePolling: true, interval: 300 },
    proxy: {
      '/api': { target: apiTarget, changeOrigin: false },
    },
  },
})
