import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Loopback only. Never 0.0.0.0: health data must not be reachable from the LAN.
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', port: 5174, strictPort: true },
  preview: { host: '127.0.0.1', port: 5175, strictPort: true },
  base: './',
});
