import { defineConfig } from 'vitest/config';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    environmentOptions: {
      jsdom: { url: 'http://localhost/' },
    },
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
    globals: true,
    setupFiles: ['./test-setup.ts'],
  },
});
