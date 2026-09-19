# MSE website — build brief (read fully before touching any file)

## Objective
A corporate website for MSE that stands next to bp.com and aramco.com without embarrassment:
editorial typography, generous whitespace, disciplined grid, full-bleed sections, restrained motion,
dark/light rhythm between sections, a mega-menu header, and a rich, useful footer. It must feel like
a major energy-sector engineering company, not a template.

MSE (working assumption until the client copy arrives): an engineering and project services company
serving energy and industrial clients — from concept and front-end engineering through project
controls, procurement, construction support and commissioning, with a digital-innovation practice.

## Site map (fixed — these routes exist, nothing else)
| Route | Page |
|---|---|
| `/` | Home |
| `/about` | About Us |
| `/services` | Services |
| `/project-services` | Project Services |
| `/industries` | Industries |
| `/methodology` | Methodology |
| `/case-studies` | Case Studies |
| `/digital-innovation` | Digital Innovation |
| `/leadership` | Leadership |
| `/resources` | Resources |
| `/contact` | Contact |

## Content rules (non-negotiable)
The client's real copy is arriving separately. Write finished, confident draft copy that reads as final —
never "lorem ipsum", never "TODO", never "placeholder", never bracketed hints in rendered output.
BUT do not invent verifiable facts:
- no client names, project names, contract values, headcounts, founding years, revenue, office addresses,
  phone numbers, certifications, awards, or statistics with numbers;
- no named people. Leadership cards use role titles (e.g. "Chief Executive Officer") with initials-free
  monogram-less avatars (an abstract mark), and a role description;
- case studies are labelled "Representative engagements" and described by sector, scope, challenge and
  approach — qualitatively, no numeric outcomes;
- "proof points" are qualitative (e.g. "Lifecycle coverage: concept to commissioning"), never numbers.
- contact details come only from `content/site.ts` (`CONTACT`), which holds obvious configuration values.
All copy lives in `content/*.ts` files so it can be swapped without touching components.

## Technical rules
- Next.js 15 App Router, TypeScript strict, Tailwind 3, `output: "export"` (static). No API routes,
  no server actions, no `next/image` remote sources, no runtime data fetching.
- Do NOT run `npm install` or add dependencies. Available: react 19, next 15, tailwindcss 3,
  framer-motion 11, lucide-react.
- Fonts are self-hosted: `public/fonts/fonts.css` defines `Manrope` (400–800) and `Inter` (400–600).
  Manrope for headings/display, Inter for body/UI.
- No external images can be fetched in this environment. All visuals are procedural: inline SVG,
  CSS gradients/masks, canvas. Design them to look intentional and premium (topographic lines,
  isometric facility line-art, gradient meshes, grid overlays, subtle noise). Every art component
  accepts a `className` and is decorative (`aria-hidden`).
- Real photography slots: components that would normally hold a photo take an optional `src` and
  render procedural art when it's absent. Document expected filenames in `public/images/README.md`.
- Respect `prefers-reduced-motion`. Lighthouse-level accessibility: semantic landmarks, one `h1`
  per page, logical heading order, focus states, 4.5:1 contrast for text, alt text or `aria-hidden`.
- Verification you may run concurrently with other agents: `npx tsc --noEmit`, `npx next lint`.
  `npm run build` writes to `.next/` and `out/` and must ONLY be run by an agent told it owns the build.
- Ownership: edit only the files your task names. If a shared component lacks something you need,
  compose within your own page file rather than editing shared components.

## Quality bar (what reviewers will reject)
Default Tailwind look, centred-everything layouts, uniform card grids on every page, generic hero
gradient blobs, tiny type, cramped sections, emoji, stock-photo-shaped empty boxes, inconsistent
spacing, orphaned headings, nav that wraps, anything that looks like a SaaS landing page.
