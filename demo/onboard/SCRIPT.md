# Talk track: the pack drafter on public data (2 minutes)

Real public records, one operator, two commands. Say only what the screen shows. Read every number from the screen as
it stands: never round it, restate it or add one it does not show. The screen is the demo's console, or `demo.html`
from a recorded run (the `onboard-demo` workflow's artifact). Say which it is: run now, or recorded.

| Time | Beat | Say | On screen |
|---|---|---|---|
| 0:00–0:15 | The field | "This is MSHA's public file of mine accidents. Each record has a filed category and a narrative. We never wrote a pack for mining. Two commands: one downloads the file, one runs the drafter on it, for one operator, c1." | The title, the settings line, the code line and the input line. |
| 0:15–0:30 | (a) The export | "It reads the export: these rows and columns, in this time. A settings file names this source's columns: the record id, the mine, the date, the narrative and the filed category, and the ones it refuses. The drafter's code names none. The operators' names and ids are read only to split the file, to refuse and hold back words, and to check the output. The guard looked on screen for each of them, whole, and found none." | Step (a): rows, columns, the roles table, the refused columns and the step's time; the guard line at the foot. |
| 0:30–1:00 | (b) The pack | "From the training years alone it drafts a pack. Each filed category with enough records at enough mines, and a specific label, becomes a predicate. Its words come from the narratives." Read two or three labels and their first terms as they stand. "Every word passed the term floor, and the privacy check passed. A learned word that is part of an operator's name or id, or that the narratives write like a name, is held back from the screen, and this line counts them." | Step (b): the floor line, the predicates with their first terms, the privacy floor line, the withheld-terms line, the time. |
| 1:00–1:25 | (c) Reading | "Then it reads the later years it never saw. It sees only the narrative. It is scored against the category the operator filed." Read the drafted pack's F1 and interval, then the best control's, as they stand. Then read the last line of the step as it stands. | Step (c): the table with D002's column, the difference line and the line that says whether this reproduces D002's run. |
| 1:25–1:50 | (d) Across the mines | "Now the operator's mines. In the pipeline, each mine runs as its own site and keeps its records; here they all run in this one process. Only weekly cells leave a mine, under a mine label, never the mine's id. A cell counts one category in one week, and a count under 3 leaves as '<k'. From those cells alone, the detectors raised these alerts." Read the first list as it stands: a category, its weeks, how many mines sent a cell of it. Then say how many items are reference only: "Those came from record-level codes counted centrally, which this setup does not send." | Step (d): the per-mine table, the alerts lines, the list from the weekly cells and the reference-only list. |
| 1:50–2:00 | (e) What it means | "The items from the cells are categories whose counts rose at more than one of this operator's mines in the same weeks. None matches an outcome on record, because we gave it none. Only the operator's own people could say which are real." | Step (e) and "What this does not show". |

Presenter notes:

- Say that it is public data, that the operator is c1, and that no id or narrative is on screen. Read the
  withheld-terms line of step (b) as it stands: learned words that are part of an operator's name or id, or that the
  narratives write like names, are held back. Do not say that no name is on screen: a name the narratives also write
  in lower case is not caught, as "What this does not show" says.
- The mines are not separate machines here. Each runs as its own site inside one process, from the operator's export,
  and nothing goes over a network. Say "in the pipeline" when you say what a mine keeps or sends.
- Read the last line of step (c) as it stands. If it says the numbers differ from D002's run, say that. If it says the
  settings are not D002's, say that. Never call a number D002's unless that line says it reproduces D002's run.
- Do not say "no configuration" or "a field we never configured". A settings file names this source's columns, and
  a download script fetches and splits the file. The drafter's code names no column. Say that if asked.
- The answer key is the filed category, not a checked label. Say "agrees with the filed category", not "correct".
- This is reading and counting. Never say the alerts are early, real or a detection result. Nothing here measures
  early warning, and no one has judged the review list.
- Never present a reference-only item as found from the weekly cells. Those items came from record-level codes
  counted centrally. Give their number only, as the screen does.
- Read the header's input line as it stands: it says whether the file is the one D002 read.
- The interval covers the sampled records of this operator only.
- If the screen says the output was withheld, stop. Show nothing else, and say that the guard withheld it.
