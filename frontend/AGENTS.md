# TraceMind Frontend Agent Instructions

## Stack
- **Vue 3** (Composition API, `<script setup>`)
- **TypeScript**
- **Element Plus** (on-demand imports)
- **Vite** (build)
- **Vitest** (test)

Do NOT introduce another framework, Tailwind, shadcn, or external font CDN without explicit approval.

## Design Direction

**TraceMind — Minimal Technical Workspace**

The UI design specification is the single source of truth:
→ `../docs/design/TraceMind-UI-Design.md`

**Read it before modifying any frontend presentation.**

## Architecture Rules

- Preserve existing architecture (Vue 3 + TS + Element Plus + Vite) unless explicitly required.
- Prefer minimal compatible changes.
- Reuse existing components and patterns before creating new ones.
- For presentation-only tasks, normally do NOT modify `src/services/` or `src/types/` unless necessary and explicitly justified.

## Before Adding New UI

1. Classify new information as L1 / L2 / L3 (see design spec).
2. Inspect analogous existing TraceMind components first.
3. Reuse current TraceMind patterns (resource rows, provenance rows, evidence items).
4. Keep Evidence/Sources first-class (L1).
5. Keep execution/debug secondary (L3, progressive disclosure).
6. Preserve the two-level App Shell.
7. Avoid: duplicated navigation, admin CRUD tables, card-heavy SaaS layouts, ChatGPT bubble UI, generic Element Plus appearance.

## Git

- Do NOT `git commit` unless explicitly requested.
- Do NOT `git push` unless explicitly requested.

## Testing

For relevant frontend changes, run:
```bash
npx vue-tsc --noEmit
npx eslint src/ --max-warnings 100
npx vitest run
npx vite build
```

Do NOT remove meaningful tests just to make a task pass.
