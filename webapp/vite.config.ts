import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/static/app-dist/',
  build: {
    outDir: '../static/app-dist',
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      output: {
        entryFileNames: 'index.js',
        chunkFileNames: '[name]-[hash].js',
        assetFileNames: (info) =>
          info.name?.endsWith('.css') ? 'index.css' : '[name]-[hash][extname]',
        manualChunks: {
          vendor: ['react', 'react-dom', 'zustand', 'immer'],
          charts: ['echarts', 'echarts-for-react'],
          flow: ['@xyflow/react', '@xyflow/system'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:5070',
      '/auth': 'http://localhost:5070',
    },
  },
});
