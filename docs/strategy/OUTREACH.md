# Outreach: from first email to a signed pilot

`DISCOVERY.md` fixes who to call and what to ask. `docs/collective/PILOT.md` is what a partner runs. This file holds
what the founder sends in between: the first email, the one-page pilot offer for after a call, and a non-binding letter
of intent whose success test is agreed before any data is read. Nothing here has been sent; no company has seen it.

Every claim in these texts is one the repository supports today. Do not add a result we do not have. The five public
replays found no early warning beyond chance (`MARKET.md` 5.1), and the texts say so: a buyer who later finds out we
hid it will not sign.

## 1. The first email (to a VP or director of quality, or the head of post-market surveillance)

> **Subject:** Complaint trends that span several of your sites
>
> Dear [name],
>
> I am building software for one narrow problem: a failure that shows up at several of a company's sites, where each
> site sees too little of it to act, and the narratives cannot be pooled centrally. That may be because of privacy
> law, works councils, separate systems or language.
>
> I am not selling anything yet. I would value 30 minutes to hear how [company] handles this today: the last time a
> trend turned out to involve more than one site, who saw it first, and what stood in the way. I will share what
> twenty such conversations teach me, in aggregate and without naming anyone.
>
> [founder name], [company], [one line on background]

Send it only to the people `DISCOVERY.md` section 1 lists, at most one account per company, and log each send in the
call sheet (`DISCOVERY.md` section 3) with its date.

## 2. The pilot offer (one page, only after a call where the audit question was answered yes)

**What we ask for**
- One export of two years of complaint, nonconformance or service records from at least three sites, in whatever
  format your system produces. We map it in the first week (`PILOT.md`, "What you export").
- The list of issues you acted on in those two years (CAPAs or investigations): id, date opened, what it concerned.
- Two hours of a quality engineer's time at the end, to go through the review list.

**What runs, and where**
- `pilot.audit` runs inside your environment, on your machine, with Python's standard library only. It sends nothing
  anywhere, and we never see a record.
- It reports which of your past issues the cross-site detectors would have flagged, how much earlier, and what the
  same alerts at random times would have found.
- It also lists cross-site patterns that match no issue on record, with the record ids behind each. Only your
  engineers can tell whether those are missed problems or noise.

**What we know today, stated plainly**
- On public data, the same detectors gave no early warning beyond chance: FDA device reports for one manufacturer,
  and NHTSA complaints for six car makes. Public reports lack what your own records carry: your sites, your lot and
  part numbers, your internal narratives.
- So the honest answer to "does it work" is that we do not know for a company's own records. This audit is the
  cheapest way to find out, and a "no" costs you two weeks of one export.

**Terms:** free; four weeks; either side may stop at any time. The success test in section 3 is agreed before any
record is read, and it is the only thing that decides whether we propose a paid phase.

## 3. Letter of intent (template, non-binding)

> **Letter of intent: signal audit pilot**
>
> Between [company] ("the Company") and [our company] ("the Provider"), dated [date].
>
> 1. **Purpose.** To learn whether cross-site detection of complaint or nonconformance patterns would have flagged
>    issues the Company acted on earlier than it did.
> 2. **Scope.** Records from [sites], received [from] to [to]; issues opened in the same period.
> 3. **Data.** The Company runs the audit in its own environment. No record, narrative or count leaves the Company.
>    The Provider receives only what the Company chooses to share from the report, and by default that is nothing.
> 4. **Success test, fixed before any record is read** (the Company picks one row; both sides initial it):
>    - [ ] at least [N] of the Company's issues flagged before they were opened, more than the chance column of the
>      same report gives, with p below [0.05];
>    - [ ] at least [M] patterns on the review list that the Company's reviewers rate worth an investigation they
>      had not opened;
>    - [ ] [another test the Company names, in writing, before the run].
> 5. **If the test is met,** the parties intend to agree a paid phase: [scope, sites, price range]. **If it is not
>    met,** neither party owes the other anything, and the Provider may report the result only as "a pilot did not
>    meet its pre-agreed test", without naming the Company.
> 6. **Not binding.** This letter states intent and creates no obligation, except the confidentiality in item 3.
>
> Signed for the Company: [name, role] Signed for the Provider: [name, role]

## 4. Variants for the other fields

The offer and the letter change only in their nouns. Use these only after a discovery call in that field: `MARKET.md`
ranks them below the beachhead, and none has evidence of its own yet.

| Field (pack) | "Sites" | "Records" | "Issues you acted on" |
|---|---|---|---|
| Automotive field quality (the vehicle pack's structure) | plants, regions or dealer groups | warranty claims, technician notes | field actions, recalls, supplier claims |
| Insurance (`claims_integrity`) | lines of business or subsidiaries | claims and adjuster notes | special investigations opened |
| IT operations (`it_incidents`) | subsidiaries or managed clients | incidents and problem tickets | problem records opened |

## 5. What counts as evidence for the YC application

- A **sent** count and a **call** count, from the call sheet.
- A **signed letter of intent**, quoted by role and company size only, unless the company agrees to be named.
- A **pilot result**, whatever it is, reported against the test the letter fixed.

Do not count interest, replies or meetings as letters of intent.
