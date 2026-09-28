# Mycelic frontend

React 19 + TypeScript + Vite application for Mycelic. It is built into `frontend/dist` and served as static
files by the aiohttp API process; every request to `/api/*` (and the SSE stream at `/api/events/stream`) goes to
the same origin, so no CORS configuration is needed in production.

The API contract is `docs/mycelic/API.md`. `src/api/types.ts` mirrors every JSON shape in it and
`src/api/client.ts` has one typed function per endpoint.

## Development

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

The dev server proxies `/api`, `/healthz` and `/readyz` to `http://127.0.0.1:8780`, so start the API first:

```bash
python -m mycelic serve   # from the repository root (port 8780)
```

The proxy keeps the browser's `Host` header (`changeOrigin: false`) so the API's Origin/Host CSRF check passes.
In demo mode (`MYCELIC_DEMO_MODE=1`) the login page lists the seeded personas and the top bar has a persona switcher.

## Build

```bash
npm run typecheck    # tsc --noEmit (strict)
npm run build        # writes frontend/dist (served by the API; also built by the Docker image)
npm run preview      # serve the built bundle locally
```

`dist/` and `node_modules/` are git-ignored; only sources are committed.

## Layout

```
src/
  api/        types.ts (contract types), client.ts (fetch wrapper + endpoints), events.ts (SSE hook)
  auth/       AuthProvider / useAuth, RequireAuth, DemoBanner
  shell/      AppShell (navigation, top bar, notifications, persona switcher), breadcrumbs, glyphs
  components/ design-system primitives (ui.tsx), evidence panel, lists, loop panel, network graph, forms
  pages/      one file per route; pages/admin/* for /app/admin/*
  styles/     global.css — tokens, layout, components, responsive rules
```

Routes follow the "Frontend routes" section of `API.md`:
`/login`, `/register`, `/invite/:token`, `/onboarding`, `/app`, `/app/memory`, `/app/chat` (`/app/chat/:chatId`),
`/app/goals`, `/app/goals/:id`, `/app/questions/:id`, `/app/discoveries/:id`, `/app/claims/:id`,
`/app/unit/:unitId`, `/app/executive`, `/app/network`, `/app/admin` (+ `/members /hierarchy /policies /models
/budgets /integrations /workers /audit /deployment`).

## Behaviour notes

* A `401` from any call clears the session and redirects to `/login?next=…`; a `403` renders a "Not authorized" panel.
* Pages subscribe to the relevant SSE kinds and refresh; the stream reconnects with `since=<last id>`.
* The loop badge shows **Active** only when `active_indicator.active` is true; otherwise the observed state is shown
  with its explanation. Goal progress shows **Unknown** when `progress.known` is false. Priority breakdowns are
  labelled as heuristic estimates.
* The demo banner ("Demonstration data — simulated organization") is shown whenever `tenant.is_demo` is true.
