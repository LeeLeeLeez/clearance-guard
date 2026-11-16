import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发时前端 5178，API 代理到本地后端 8770；
// 生产构建产物输出到 web/dist，由 server.py 托管 / 打进 Tauri。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5178,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8770',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1400,
  },
})
