/// <reference types="vitest" />
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: [],
    include: ['src/**/*.{spec,test}.{ts,tsx}'],
    // Isolate each test file's module registry so store state doesn't leak
    isolate: true,
    // Use fake timers by default — tests that don't need them still pass
    fakeTimers: {
      // Do NOT install globally; tests opt in with vi.useFakeTimers()
    },
  },
});
