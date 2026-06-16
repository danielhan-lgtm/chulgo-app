import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8081',
        changeOrigin: true,
        proxyTimeout: 660000,   // 11분 — 전체수량 매칭 첫 계산(BH 전체 조회) 대비
        timeout: 660000,
      },
    },
  },
})
