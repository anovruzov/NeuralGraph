# Power check

_synthetic power check: generated records and planted signals written by the same author as the detectors; it measures whether the channels could see a signal of this size, not whether real data hold one._

- Pack `vehicle_complaints`; background `docs/collective/power/vehicle-background.json` (sha256 `20c7401b93cb9149`): 262.35 records a week over 60 sites and 1409 entities.
- Worlds: 6 (2, 4 years x seeds 1, 2, 3); per world 10 ramps per rate and 10 sextuplings, each with a control.
- Ramps rise linearly to the rate over 104 weeks; sextuplings last 13 weeks; outcomes open in the last 26 weeks.
- Gate (look-back 26 weeks): the best channel with a control share of at most 0.2 must find at least 0.8 of the plants. **Fails.**

## Ramps, look-back 26 weeks

| Rate a week | Years | Plants | Planted records (mean) | X | S | R_mf | P | PRR | Best | Passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.1 | 2 | 30 | 5.0 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.13 (4/30); control 1/30 | 0.20 (6/30); control 1/30 | PRR | no |
| 0.1 | 4 | 30 | 5.6 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.20 (6/30); control 1/30 | 0.23 (7/30); control 0/30 | PRR | no |
| 0.5 | 2 | 30 | 25.9 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.03 (1/30); control 0/30 | 0.10 (3/30); control 0/30 | 0.07 (2/30); control 0/30 | P | no |
| 0.5 | 4 | 30 | 26.1 | 0.03 (1/30); control 0/30 | 0.03 (1/30); control 0/30 | 0.13 (4/30); control 0/30 | 0.07 (2/30); control 0/30 | 0.07 (2/30); control 0/30 | R_mf | no |
| 2 | 2 | 30 | 104.1 | 0.43 (13/30); control 0/30 | 0.43 (13/30); control 0/30 | 0.67 (20/30); control 0/30 | 0.10 (3/30); control 1/30 | 0.00 (0/30); control 0/30 | R_mf | no |
| 2 | 4 | 30 | 102.7 | 0.30 (9/30); control 0/30 | 0.30 (9/30); control 0/30 | 0.50 (15/30); control 0/30 | 0.07 (2/30); control 0/30 | 0.00 (0/30); control 0/30 | R_mf | no |

## Sextuplings, look-back 26 weeks

| Rate a week | Years | Plants | Planted records (mean) | X | S | R_mf | P | PRR | Best | Passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 6x | 2 | 30 | 6.6 | 0.03 (1/30); control 0/30 | 0.03 (1/30); control 0/30 | 0.10 (3/30); control 0/30 | 0.60 (18/30); control 3/30 | 0.60 (18/30); control 0/30 | P | no |
| 6x | 4 | 30 | 7.1 | 0.23 (7/30); control 0/30 | 0.23 (7/30); control 0/30 | 0.23 (7/30); control 0/30 | 0.60 (18/30); control 0/30 | 0.53 (16/30); control 0/30 | P | no |

## Ramps, look-back 104 weeks

| Rate a week | Years | Plants | Planted records (mean) | X | S | R_mf | P | PRR | Best | Passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 0.1 | 2 | 30 | 5.0 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.17 (5/30); control 3/30 | 0.30 (9/30); control 2/30 | PRR | no |
| 0.1 | 4 | 30 | 5.6 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.00 (0/30); control 0/30 | 0.37 (11/30); control 4/30 | 0.50 (15/30); control 3/30 | PRR | no |
| 0.5 | 2 | 30 | 25.9 | 0.03 (1/30); control 0/30 | 0.03 (1/30); control 0/30 | 0.07 (2/30); control 0/30 | 0.27 (8/30); control 0/30 | 1.00 (30/30); control 2/30 | PRR | yes |
| 0.5 | 4 | 30 | 26.1 | 0.07 (2/30); control 0/30 | 0.07 (2/30); control 0/30 | 0.17 (5/30); control 0/30 | 0.60 (18/30); control 1/30 | 1.00 (30/30); control 1/30 | PRR | yes |
| 2 | 2 | 30 | 104.1 | 0.70 (21/30); control 0/30 | 0.70 (21/30); control 0/30 | 0.90 (27/30); control 0/30 | 0.37 (11/30); control 2/30 | 1.00 (30/30); control 0/30 | PRR | yes |
| 2 | 4 | 30 | 102.7 | 0.63 (19/30); control 0/30 | 0.63 (19/30); control 0/30 | 0.90 (27/30); control 0/30 | 0.90 (27/30); control 0/30 | 1.00 (30/30); control 1/30 | PRR | yes |

## Sextuplings, look-back 104 weeks

| Rate a week | Years | Plants | Planted records (mean) | X | S | R_mf | P | PRR | Best | Passes |
|---|---|---|---|---|---|---|---|---|---|---|
| 6x | 2 | 30 | 6.6 | 0.03 (1/30); control 0/30 | 0.03 (1/30); control 0/30 | 0.10 (3/30); control 0/30 | 0.67 (20/30); control 7/30 | 0.63 (19/30); control 1/30 | PRR | no |
| 6x | 4 | 30 | 7.1 | 0.23 (7/30); control 0/30 | 0.23 (7/30); control 0/30 | 0.23 (7/30); control 0/30 | 0.63 (19/30); control 9/30 | 0.53 (16/30); control 3/30 | PRR | no |

Each channel cell: power (plants found of plants); control: found where nothing was planted.
