# @studio/web

The studio UI (Next.js 16, React 19, Tailwind 4). Run it from the repo root with `pnpm dev`; it talks to the
API at `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8400`).

- `src/app/` routes: `/create/video`, `/create/image`, `/library`, `/settings`, `/soon/[studio]`.
- `src/components/create/` the control panel; forms are generated from the model registry.
- `src/components/takes/TakeCard.tsx` the take card (slate while rendering, player when done).
- `src/lib/realtime.tsx` one EventSource that patches cached queries as jobs progress.
- API types come from `@studio/api-client`; regenerate with `pnpm gen` after API changes.
- `pnpm e2e` runs Playwright against the mock provider.
