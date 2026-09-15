# MSE website

Static site, no build step. Pages are generated from `site/build.py`; edit the
`PAGES` table there and run `python3 site/build.py` to regenerate the HTML.

## Content still needed

Every `TODO` line on the site maps to one forwarded email from Samir Novruzov:

| Page | Source email |
|---|---|
| index.html | Fwd: MSE Home |
| about.html | Fwd: MSE About Us |
| services.html | Fwd: Mse Services |
| project-services.html | Fwd: MSE project services |
| industries.html | Fwd: Mse Industries |
| methodology.html | Fwd: MSE Methodology |
| case-studies.html | Fwd: MSE case Studies |
| digital-innovation.html | Fwd: MSE Digital Innovation |
| leadership.html | Fwd: MSE leadership |
| resources.html | Fwd: MSE Resources |
| contact.html | Fwd: MSE Contact |
| navigation and colours | Fwd: MSE website map (overview), Fwd: MSE website and brand |

## Deploy to Vercel

1. In Vercel, **Add New → Project** and import `anovruzov/NeuralGraph`.
2. Framework preset: **Other**. Leave build command empty. The root
   `vercel.json` already points the output directory at `site/`.
3. Deploy. Every push to the connected branch redeploys.

Or from a terminal with the Vercel CLI: `vercel --prod` in the repo root.
