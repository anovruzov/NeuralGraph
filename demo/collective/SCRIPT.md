# Talk track: the collective demo

Fictional company, synthetic data, an illustration, not a measured result. Internal use only: the YC demo's own rules
in STRATEGY put synthetic-fixture results of any kind off its screen, so this cut rehearses the shape of the YC cut on
fictional data and is not the YC cut itself; whether it may be shown to YC is the founder's decision (`README.md`).

The screen shows only values read from the run files. Name the values ("the X rank", "the R column", "the gate
status"); never say a value that the screen does not show, and never round or restate one.

Before you start, say whether the run is LIVE or RECORDED: the badge at the top says which. It says LIVE only while
the console is driving the engine now (`--serve`), and you act as the named owner when you approve. A replay or an
exported page always says RECORDED, also for a run that was once driven live: nothing runs behind it. The footer says
how the run was made (a scripted run, or one driven live in the console).

## The 60-second cut

Give it live with `--serve --cut` (the engine walks only the cut's beats; the follow-ups run with scripted approval
when you press Next after the check), or from a recording. The "Show the 60-second cut" button of a full live run only
filters the view: the engine still stops at the follow-up beat until you approve both follow-ups.

| Time | Beat | Say | On screen |
|---|---|---|---|
| 0:00–0:08 | The problem | "Several plants each log complaints under the same generic code. The codes do not say what failed." | The company, the plants with the case, the complaint count, the code label and the illustration line. |
| 0:08–0:22 | The alert | "Each plant counts inside its own boundary, and only suppressed weekly counts leave it. The detectors flag this lot, and no rule was written for it. Here is the X rank for this failure mode, next to the S column and the R column: the same detectors over the codes only, and over the fields allowed to leave. They flag this lot under the generic code; they do not say what failed." | The failure-mode caption; the X row with its rank, score and week; the S and R rows, each with the first other key of this case it flagged; the by-construction caption; every related-key line. |
| 0:22–0:45 | Check with the sites | "HQ asks the plants one narrow question. Each plant answers from its own records: confirm, refute or unknown, with count buckets and a reference that only that plant can open. The sibling plant holds the same lot without the failure, and it refutes. No narrative text crossed." | The question, one card per plant, the gate status and its reasons, and the text-overlap and canary counter. |
| 0:45–0:60 | Real data | "This is a constructed illustration on a fictional company. The real-data result is not measured yet." | The real-data line. |

Presenter notes:

- Say LIVE or RECORDED out loud, and say that the company and the data are fictional.
- If the R column or the S column caught the case, say so: the screen then says "also caught this case".
- Read the each-site-alone reference with its week. In the committed run one plant alone, running the same detectors
  on its own records, flags this lot in the same week as X, and the screen says so ("One plant alone also caught this
  failure mode ... no later than X"): on this case the cross-site view is not earlier than a single site, so there is
  no collective lift to claim. Say that; never present the case as one only the collective could see. Say "the
  detectors flag this lot", not that only the cross-site view does.
- Each row's rank is for the failure mode the caption names. The S and R rows also name the first other key of this
  case they flagged, with its rank and week (the lot or the product under the generic code): read it as it stands.
  Never say that the baselines missed the lot.
- If R or S flagged a related key, read that line as it stands. A related key is a different key on the same case,
  shown with its own rank and week. Never call a related flag a miss.
- The by-construction caption says why S and R cannot see this key in these records. It is a property of the
  constructed case, not a result.
- Say what is simulated: the plants run in one process, and the models are the ones the footer names.
- Never quote a benchmark figure, and never show this run to a buyer.

## Extended internal beat (not in the 60-second cut)

| Time | Beat | Say | On screen |
|---|---|---|---|
| after the cut | Approval-routed follow-up | "Only a supported conclusion can propose a follow-up. The evidence packet is assembled inside each plant, and only buckets leave. The CAPA draft is written at HQ from structured inputs only, for a named owner, who approves it. Each approved step runs once, however often it is requested. This layer is not measured: X4 has not run." | The X4 caption, the packet cards, the owner and the acknowledgement date, the draft fields, the approval mode, the execute requests and executor runs, and the outcome line. |
