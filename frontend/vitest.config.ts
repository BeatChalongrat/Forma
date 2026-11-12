import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'

const output = path.resolve(process.env.FORMA_QA_OUTPUT ?? '../test-results/frontend')

export default defineConfig({
  plugins: [react()],
  test: {
    include: ['tests/**/*.test.{mjs,ts,tsx}'],
    environment: 'node',
    reporters: ['default', 'junit'],
    outputFile: { junit: path.join(output, 'frontend.xml') },
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.d.ts'],
      reportsDirectory: path.join(output, 'coverage'),
      reporter: ['text-summary', 'json', 'json-summary', 'lcov', 'html'],
    },
  },
})
