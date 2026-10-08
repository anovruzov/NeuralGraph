# Mycelic website

One static page, `site/index.html`. Plain HTML and CSS, with no build step and no JavaScript.

## Run it locally

```sh
cd site && python3 -m http.server 8000
```

Then open http://localhost:8000. Without a network connection the Google Fonts fall back to system fonts.

## Deploy to any static host

Upload `index.html` as the site root on any web server or static host (for example Netlify, Cloudflare Pages, or S3 behind a CDN). There is nothing to build.

## Deploy to GitHub Pages

Pages serves only from the root or the `/docs` folder of a branch, so publish `site/` as its own branch. Run this from a branch that contains `site/` (today that is `mycelic-site`, not `main`):

```sh
git push origin "$(git subtree split --prefix site HEAD)":refs/heads/gh-pages
```

The same command publishes the first time and every later update. Add `--force` only if the push is rejected.

Then, on GitHub:

1. Go to Settings, then Pages, then Build and deployment.
2. Set Source to "Deploy from a branch".
3. Choose Branch `gh-pages` with folder `/ (root)`, and Save.

The site appears at https://anovruzov.github.io/NeuralGraph/. No workflow file is used.

## Change the contact link

In `index.html`, search for the `CONTACT LINK` comment. It marks the only place the pilot-request link is set. Replace the `href`, and update the note under it if requests are no longer public GitHub issues.

If issue templates that disable blank issues are added later, GitHub sends this link to the template chooser and drops the title.

## Before you share the page

The pilot link opens a public GitHub issue, and visitors need a GitHub account to file it. Switch to a private contact before you send the page to buyers.

When the code for the four quality steps is public, delete the paragraph after the `PUBLIC CODE NOTE` comment.

## Themes

The page follows the system light or dark setting. A host can force one with `data-theme="light"` or `data-theme="dark"` on `<html>`.
