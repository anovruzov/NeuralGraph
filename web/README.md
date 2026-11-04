# Mycelic website

Public site for Mycelic (myceliclabs.com). Next.js 15, TypeScript, no CSS framework.
It is self-contained: nothing in the rest of the repository is imported, and nothing here is imported by the runtime or the research code.

```bash
cd web
npm install
npm run dev      # http://localhost:3000
npm run build    # production build
```

Pages: `/`, `/technology`, `/research`, `/company`.

Every number on the Research page is traced to a repository artifact in `app/research/metrics.ts`.
