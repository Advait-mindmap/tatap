import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end config for the 2D flow view.
 *
 * `webServer` starts Vite itself, so `npx playwright test` is self-contained — no "did you
 * remember to start the dev server" step, which is the usual way a browser suite rots.
 * `reuseExistingServer` keeps a dev server you already have running.
 *
 * A wide viewport is deliberate: the screenshots are the deliverable here, not a side effect,
 * and a narrow window would crop the stage columns the test exists to show.
 */
export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.artifacts',
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  // A test drives a whole simulation: intake, thirteen stages, a stop at every genuine fork.
  // The 45s this used to allow was shorter than the waits inside the tests themselves
  // (support.ts waits up to 120s for extraction and 300s for a stage), so those waits could
  // never be reached — the test timeout always fired first. On a CI runner with no GPU, where
  // everything is slower than a developer machine, that gap is the difference between green
  // and red. The internal waits are the intended limits; this just has to be larger than them.
  timeout: 360_000,
  expect: { timeout: 10_000 },

  use: {
    // E2E_BASE_URL points the whole suite at a deployment. Unset, it drives the Vite dev server.
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    viewport: { width: 1680, height: 1000 },
    deviceScaleFactor: 2, // legible screenshots on a normal display
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        // Headless Chromium has no GPU, so WebGL falls back to SwiftShader — which newer
        // builds refuse to use unless asked. Without these the 3D canvas renders nothing and
        // the test would be "proving" a black screen.
        launchOptions: {
          args: [
            '--enable-unsafe-swiftshader',
            '--use-gl=angle',
            '--use-angle=swiftshader',
            '--ignore-gpu-blocklist',
          ],
        },
      },
    },
  ],

  // No dev server when testing a deployment - there is nothing local to start.
  webServer: process.env.E2E_BASE_URL ? undefined : {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
