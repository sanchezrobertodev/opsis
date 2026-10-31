# @opsis/web

The Opsis UI (Next.js 16, React 19, Tailwind 4). Run it from the repo root with `pnpm dev`; it talks to the
API at `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8400`).

- `src/app/` routes: one folder per studio (`create/video`, `create/image`, `edit`, `motion`, `swap`, `cinema`,
  `faceless`, `jutsu3d`, `lipsync`, `worlds`, `characters`, `templates`…) plus `library`, `runs` and `settings`.
- `src/lib/studios.ts` the studios in the side rail.
- `src/components/create/` the control panel; forms are generated from the model registry.
- `src/components/takes/TakeCard.tsx` the take card (slate while rendering, player when done).
- `src/components/ui/controls.tsx` the hand-built UI primitives; design tokens are in `src/app/globals.css`.
- `src/lib/realtime.tsx` one EventSource that patches cached queries as jobs progress.
- API types come from `@opsis/api-client`; regenerate with `pnpm gen` after API changes.
- `pnpm e2e` runs Playwright against the mock provider.
