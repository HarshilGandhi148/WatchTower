import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { fileURLToPath } from 'node:url';
import { recommendationReviews } from './server/reviews';

export default defineConfig({
  plugins: [
    react(),
    recommendationReviews(fileURLToPath(new URL('../.env', import.meta.url))),
  ],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
  },
});
