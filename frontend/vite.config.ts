import fs from 'node:fs'
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// PDF.js が実行時に読む資源（文字コード対応表・標準フォント・画像デコーダー）を /pdfjs/ で配信する。
// 外部 CDN に頼らない。開発時は node_modules から返し、ビルド時は成果物へコピーする
const PDFJS_DIRS = ['cmaps', 'standard_fonts', 'wasm', 'iccs']
const pdfjsRoot = path.resolve(__dirname, 'node_modules/pdfjs-dist')

function pdfjsAssets(): Plugin {
  return {
    name: 'pdfjs-assets',
    configureServer(server) {
      server.middlewares.use('/pdfjs', (req, res, next) => {
        const rel = decodeURIComponent((req.url ?? '').split('?')[0]).replace(/^\/+/, '')
        const file = path.resolve(pdfjsRoot, rel)
        if (!PDFJS_DIRS.includes(rel.split('/')[0]) || !file.startsWith(pdfjsRoot) || !fs.existsSync(file)) return next()
        res.setHeader('Cache-Control', 'public, max-age=86400')
        fs.createReadStream(file).pipe(res)
      })
    },
    generateBundle() {
      for (const dir of PDFJS_DIRS) {
        const base = path.join(pdfjsRoot, dir)
        if (!fs.existsSync(base)) continue
        for (const name of fs.readdirSync(base)) {
          const file = path.join(base, name)
          if (fs.statSync(file).isFile()) {
            this.emitFile({ type: 'asset', fileName: `pdfjs/${dir}/${name}`, source: fs.readFileSync(file) })
          }
        }
      }
    },
  }
}

// コンテナ内では backend サービス名で FastAPI に転送する
const apiTarget = process.env.API_PROXY_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss(), pdfjsAssets()],
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
