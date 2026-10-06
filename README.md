# AI Media Studio

A personal AI video studio that matches Higgsfield's video features (Create Video, Edit Video, Motion Control,
Motion Transfer, Object Swap, Character Creation, World Creation, Faceless Studio, Cinema Studio, 3D Jutsu,
Lipsync). It gives access to the same model families through commercial APIs, open models on RunPod, and an
optional local ComfyUI.

**Status:** P0 (foundations) is done: the full create → live progress → library loop runs on a free mock
provider. P1 adds the live providers (fal.ai, Google, Runway, Replicate). See [docs/PLAN.md](docs/PLAN.md).

## Run it

Prerequisites: Docker Desktop, Node 22+ with pnpm, Python 3.12 with uv, and ffmpeg on PATH.

```bash
pnpm db:up
```

```bash
uv sync && pnpm i && pnpm db:migrate
```

```bash
pnpm dev
```

Open http://localhost:3000. Everything runs in mock mode by default (`PROVIDER_MODE=mock` in `.env`), so
generations are labelled test clips and nothing is billed.

| Doc | What's inside |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Approved plan: feature-to-model matrix, architecture, phases, verification |
| [docs/research/06-architecture-blueprint.md](docs/research/06-architecture-blueprint.md) | Detailed design: data model, registry schema, job lifecycle, pipeline engine, director, frontend |
| [docs/research/](docs/research/) | Research reports (Higgsfield features, commercial APIs, open models and RunPod, Higgsfield live catalog) |
| [CLAUDE.md](CLAUDE.md) | Conventions and rules for AI-assisted development |
