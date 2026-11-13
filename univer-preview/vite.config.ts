import { defineConfig } from 'vite';

// 产物直接吐到网关静态目录；容器里 /app/static 已挂载宿主机
export default defineConfig({
  base: '/univer/',
  build: {
    outDir: '../gateway/static/univer',
    emptyOutDir: true,
    chunkSizeWarningLimit: 8192,
    rollupOptions: {
      output: {
        manualChunks: {
          univer: ['@univerjs/presets', '@univerjs/preset-sheets-core'],
        },
      },
    },
  },
});
