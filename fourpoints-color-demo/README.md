# Four Points Color — concept demo

A single-file redesign concept for [fourpointscolor.com](https://fourpointscolor.com), a Houston print shop
making custom shirts, signs and decals with nationwide shipping.

Open `index.html` in any browser. No build step, no dependencies beyond Google Fonts.

## The idea: "In Register"

"Four points" is what a printer calls the registration marks used to line up each ink pass, so the whole page
is a press sheet coming into register: crop marks on every section, a sheet-edge rail with four registration
marks, spec ledgers written in the shop's own units, and a hero where a shirt is screen-printed in front of
the visitor, pass by pass, landing slightly off and then snapping into register.

## What is on the page

- **The pull** — an animated four-color screen print (SVG, vanilla JS) with garment swap and a rubber stamp.
- **Ledger** — shirts, signs and decals with real minimums, materials and imprint sizes.
- **The Houston year** — eight yard-sign tiles naming the jobs Houston actually orders, each deep-linking into the builder.
- **Separations** — a live halftone viewer (Canvas) with plate toggles, misregistration slider and mesh count.
- **Job ticket builder** — product, quantity, inks, garment, placement, substrate, vinyl and cut choices drive a
  true-scale mockup, a print-method decision (screen vs DTF, mesh, underbase) and a living ticket; proof stamp,
  mobile ticket bar, and hand-off into the contact form.
- **Exclusives** — a Pantone-style chip wall of the shop's own designs with garment swatches.
- **Shipping, questions, send** — sample job board, FAQ in "request → shop's call" format, and a demo ticket form.

Light and dark themes, reduced-motion fallbacks, keyboard operable, no horizontal scroll at 390px.

## Demo boundaries

This is a pitch, not the live site. The phone number is a 555 number, hours and prices are omitted or marked
as demo, sample jobs are illustrative, and the exclusive designs are placeholders for the shop's real line.
