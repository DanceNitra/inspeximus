# inspeximus — launch site

A single-page marketing site for [inspeximus](https://github.com/DanceNitra/inspeximus): a zero-dependency memory
layer and MCP server for AI agents. Dark, precise, with a scroll-driven 3D "memory ledger" narrative.

## Stack
- **Vite** + vanilla **TypeScript**
- **Three.js** — the persistent WebGL ledger scene (hero → correction → attack → erasure beats)
- **GSAP + ScrollTrigger** — scroll choreography (scrubs the scene from scroll position)
- **Lenis** — smooth scrolling

No backend. Builds to a static `dist/` with relative paths (GitHub Pages friendly).

## Develop
```bash
npm install
npm run dev      # http://localhost:5173
```

## Build
```bash
npm run build    # -> dist/
npm run preview  # serve the build locally
```

## Deploy (GitHub Pages)
The build uses `base: './'`, so `dist/` can be served from any path. Either:
- push `dist/` to the `gh-pages` branch / Pages source, or
- add a GitHub Action that runs `npm run build` and publishes `dist/`.

## Accessibility / performance
- Respects `prefers-reduced-motion`: no smooth-scroll or scrub, the WebGL scene holds a static hero frame,
  and every section falls back to a stacked layout with simple fades.
- WebGL is guarded — if the context can't be created the canvas hides and the layout stays intact.
- The Three.js bundle is split into its own chunk; the scroll engine (GSAP + Lenis) is lazy-loaded after
  first paint. Mobile / low-core devices drop bloom, pixel ratio, transmission, and particle count.

## Copy
All product claims are real (measured integrity numbers, tool names, DOIs). Do not invent figures — see the
inspeximus repo `SECURITY.md`, `CHANGELOG.md`, and the open benchmark harness for provenance.
