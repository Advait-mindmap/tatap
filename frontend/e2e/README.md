# End-to-end tests for the 2D flow view

Real Chromium against the real golden `SimulationOutput`. The backend tests prove the DATA is
right and `tsc` proves the code compiles; neither can tell you whether 49 nodes actually appear
on a canvas, whether hovering really dims the rest of the graph, or whether the capped-confidence
detail is legible to a reviewer.

```bash
npm run e2e            # headless, starts Vite itself
npm run e2e:headed     # watch it drive the browser
npm run e2e:report     # HTML report after a run
npx playwright test -g "hover"    # one test
```

Vite is started by the config (`webServer`), and an already-running dev server is reused, so
there is no "did you start the server first" step.

## Screenshots

Written to `e2e/screenshots/` on every run — they are as much the point as the assertions,
because they let the rendered flow be reviewed without opening a browser.

| File | Shows |
|---|---|
| `01-full-flow.png` | The whole programme: 49 nodes across 6 stage columns |
| `02-hover-highlight.png` | Hover highlighting the transitive path, everything else dimmed |
| `03-trail-open.png` | Reasoning trail open beside the graph |
| `04-governance-badges.png` | Top bar: export blocked, governance incomplete |
| `05-trail-panel.png` | The trail panel close up, incl. capped confidence |
| `06-decision-point.png` | A resolved fork with why it stopped and the answer |
| `07-readable-detail.png` | Zoomed in far enough to read the cards |
| `08-decision-detail.png` | A decision point close up |

## Two things worth knowing

**Hovering React Flow needs a stepped mouse move.** Playwright's `.hover()` teleports the cursor
in one jump and `onNodeMouseEnter` does not fire — the browser never generates the crossing
sequence React's synthetic `mouseenter` is built on. `hoverNode()` moves from a neutral point in
12 steps. Diagnosed empirically: a single move gave 0 highlighted nodes, a stepped move gave 8.

**Counts that must agree are read in one `evaluate`.** Two separate `count()` calls can straddle a
React re-render, so `dimmed + highlighted === total` flaked because the two halves came from
different frames. One atomic read, retried with `expect.poll`, removes the race rather than
hiding it behind a longer sleep.
