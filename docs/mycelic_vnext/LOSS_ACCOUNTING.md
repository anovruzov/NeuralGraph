# Mycelic vNext — loss accounting (old vs new)

_The full per-pattern accounting, with the stage definitions, the sketch-failure taxonomy, the witness curve and the rank tables, is `docs/MYCELIC_LOSS_ACCOUNTING.md` (regenerated on the new artifacts). This file puts the old and new funnels side by side for the hierarchy._

Stages (each row is a gold pattern; a stage is passed only if every earlier stage was): extracted → sketch_visible → in_triage → questioned → descent_reached → in_pool → candidate → matched_any → matched_tau → in_register. `loss_stage` is the terminal loss (first failing stage after the last passing one).

## 10,000

_(funnel rows missing for one side)_

### Where they died

_(funnel rows missing for one side)_

## 50,000

_(funnel rows missing for one side)_

### Where they died

_(funnel rows missing for one side)_

## Reading

The pack's eight loss categories map onto the columns as: never represented in sketches = ¬sketch_visible; filtered from knowledge objects = extracted but ¬in_pool after reach; not selected for questioning = ¬questioned; descent routing miss = ¬descent_reached; local extraction miss = ¬extracted; evidence at kernel but no candidate = in_pool ∧ ¬candidate; candidate rejected by verification = candidate ∧ ¬matched_tau; ranking buried it = matched_tau ∧ ¬in_register.
