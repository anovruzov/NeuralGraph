# Talk track: the pack drafter on public data (2 minutes)

Real public records, one operator, two commands. Say only what the screen shows. Read every number from the screen as
it stands: never round it, restate it or add one it does not show. The screen is the demo's console, or `demo.html`
from a recorded run (the `onboard-demo` workflow's artifact). Say which it is: run now, or recorded.

| Time | Beat | Say | On screen |
|---|---|---|---|
| 0:00–0:15 | The field | "This is MSHA's public file of mine accidents. Each record has a filed category and a narrative. We never wrote a pack for mining. Two commands: one downloads the file, one runs the drafter on it, for one operator, c1." | The title, the settings line, the code line and the input line. |
| 0:15–0:30 | (a) The export | "It reads the export: these rows and columns, in this time. A settings file names this source's columns: the record id, the mine, the date, the narrative and the filed category, and the ones it refuses. The drafter's code names none. The operator's names and ids are read only to refuse words. They are never shown." | Step (a): rows, columns, the roles table, the refused columns and the step's time. |
| 0:30–1:00 | (b) The pack | "From the training years alone it drafts a pack. Each filed category with enough records at enough mines becomes a predicate. Its words come from the narratives." Read two or three labels and their first terms as they stand. "Every word passed a floor, shown here, and the privacy check passed. A word the narratives write like a name is not shown." | Step (b): the floor line, the predicates with their first terms, the privacy floor line, the withheld-terms line, the time. |
| 1:00–1:25 | (c) Reading | "Then it reads the later years it never saw. It sees only the narrative. It is scored against the category the operator filed." Read the drafted pack's F1 and interval, then the best control's, as they stand. Then read the last line of the step as it stands. | Step (c): the table with D002's column, the difference line and the line that says whether this reproduces D002's run. |
| 1:25–1:50 | (d) Across the mines | "Now the operator's mines. Each mine keeps its records. Only weekly counts leave, under a mine label, never the mine's id, and a small count leaves as '<3'. From those counts alone, the detectors raised these alerts." Read the first list as it stands: a category, its weeks, how many mines had records of it. Then say how many items are reference only: "Those came from record-level codes counted centrally, which this setup does not send." | Step (d): the per-mine table, the alerts lines, the list from the weekly counts and the reference-only list. |
| 1:50–2:00 | (e) What it means | "The items from the counts are categories whose counts rose at more than one of this operator's mines in the same weeks. None matches an outcome on record, because we gave it none. Only the operator's own people could say which are real." | Step (e) and "What this does not show". |

Presenter notes:

- Say that it is public data, that the operator is c1, and that no name, id or narrative is on screen. If the
  withheld-terms line of step (b) is not zero, say that some learned words were held back because the narratives
  write them like names.
- Read the last line of step (c) as it stands. If it says the numbers differ from D002's run, say that. If it says the
  settings are not D002's, say that. Never call a number D002's unless that line says it reproduces D002's run.
- Do not say "no configuration" or "a field we never configured". A settings file names this source's columns, and
  a download script fetches and splits the file. The drafter's code names no column. Say that if asked.
- The answer key is the filed category, not a checked label. Say "agrees with the filed category", not "correct".
- This is reading and counting. Never say the alerts are early, real or a detection result. Nothing here measures
  early warning, and no one has judged the review list.
- Never present a reference-only item as found from the weekly counts. Those items came from record-level codes
  counted centrally. Give their number only, as the screen does.
- Read the header's input line as it stands: it says whether the file is the one D002 read.
- The interval covers the sampled records of this operator only.
- If the screen says the output was withheld, stop. Show nothing else, and say that the guard withheld it.
