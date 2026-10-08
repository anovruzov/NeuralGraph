# Requirements of a third field: multi-site IT operations incidents (B4a)

This list describes what a third field needs before any file of its pack exists. The field is the IT operations
of a **fictional group** with six subsidiaries; every subsidiary, service, vendor, host, ticket and person this
gate builds is invented, and no row below states a real-world statistic.

It was written by **the same AI system that wrote the generic code**. That system has read the code, so this list is
not blind: it may leave out what the code cannot express. It is an **internal generality measurement only**. It is
**not X3** (STRATEGY section 11.2: a pack built by someone other than the author of the generic code, with the
engineer-hours recorded), and it is **never used externally**.

Each row says what the field needs and why it matters for detecting a pattern across subsidiaries, for the boundary
of what may leave a subsidiary, or for the follow-up. What the pack could express is recorded after the pack is
built (`docs/collective/x3/effort.json`); this file is not edited after it is committed.

| Id | What the field needs | Why it matters |
|---|---|---|
| R01 | Ticket numbers are INC followed by seven digits. A ticket forwarded to another subsidiary's service desk gets a new number there and names its origin ticket and the origin subsidiary. | Each ticket must count once for the group; a forward counted again at the receiving desk looks like a second, independent report. |
| R02 | Configuration items in each subsidiary's CMDB are named by host names built from a site code, a role and a number, in lower case and joined by hyphens. | The host is where an incident happens and what a resolver checks first, but a host name reveals a subsidiary's internal estate and must stay inside it. |
| R03 | Tickets quote fully qualified host names, IPv4 and IPv6 addresses and host:port pairs (with dots and colons). They are internal and sensitive. | They pin a system down inside a subsidiary but would map its network if they left, so they must be recognised in order to be kept in. |
| R04 | Software versions are written with dots (7.4.2), and vendor release or patch ids with slashes or hyphens. One release is deployed at several subsidiaries. | A defective release is the typical cross-subsidiary cause: each subsidiary sees a few crashes after the same update, and only the group sees the pattern. |
| R05 | The group runs a closed service catalogue (mail, remote access, sign-in, finance ERP, file share, backup, printing, the self-service portal). Users and agents name each service in many ways: phrasings, acronyms and German compounds. | Counts are comparable across subsidiaries only if every phrasing resolves to the same catalogue service. |
| R06 | Services form a chain: a configuration item supports a technical service, which supports a business service (two hops). | A failure on one host surfaces as a business-service outage; joining tickets across subsidiaries needs the whole chain. |
| R07 | Change requests are CHG followed by seven digits and carry planned windows (a start and an end time). Incidents link to the change that caused them. | The same change rolled out at several subsidiaries can fail at each; the window says whether an incident falls inside a change. |
| R08 | Vendors and managed service providers have ids and names, and a vendor ships releases. | Escalation goes to the vendor, and one vendor's failures at several subsidiaries are invisible to each subsidiary alone. |
| R09 | Every ticket carries impact, urgency and a priority from P1 to P4. | Priority decides how fast a pattern must be acted on; a P1 at two subsidiaries matters more than ten P4s. |
| R10 | Timestamps are sub-day and carry a time zone (opened, resolved). Outages unfold within hours. | A group-wide outage shows as tickets within the same hours at several subsidiaries; a day or a week blurs it. |
| R11 | Monitoring tools open alert storms: many tickets from one automated reporter within minutes. | A storm from one monitor is one event, not many independent reports; counting it as many raises false alarms. |
| R12 | Tickets are forwarded between subsidiaries' service desks, either marked with their origin or re-keyed without it. | A forward echoes one report into another subsidiary; marked forwards must be discounted, and unmarked ones are a known source of double counting. |
| R13 | Ticket text is in English and German, often both in one ticket, with negation in both ("no impact on", "kein Ausfall", "nicht reproduzierbar"). | A failure mode named only in a German note, or explicitly negated, must be read correctly, or the counts are wrong in both directions. |
| R14 | Person fields hold the requester's name, e-mail and phone, and the assignee. Names and e-mail addresses also appear in free text. | Personal data never leaves a subsidiary, including where it appears in the text. |
| R15 | A generic "other / unspecified" category rises for unrelated reasons (a new portal, a reorganisation) across many services. | A rising generic category is a realistic background that hides specific failure modes and must not be read as one. |
| R16 | Specific categories (network, authentication, storage, certificates, backup, performance, application errors) each map to a failure mode. | Categories are the structured signal a central team can already see; detection must know which category names which failure mode. |
| R17 | Failure modes are named mainly in text: crash after update, certificate expiry, disk full, memory leak, sign-in failure, backup failure, latency degradation, packet loss, data synchronisation failure, licence exhaustion, failed change. | These are what a problem manager looks for across subsidiaries, and the categories often do not name them. |
| R18 | Tickets created from e-mail carry HTML markup, and agents paste log lines into them. | The reading must cope with markup and log noise without inventing or losing a failure mode. |
| R19 | Boundary policy: free text, person fields, the reporter, host names and addresses never leave a subsidiary. Services, releases, vendors, change ids and categories may leave, and only as suppressed weekly counts. | This is the line the subsidiaries' data owners agree to; everything else must be answered inside the subsidiary. |
| R20 | Each subsidiary's CMDB and vendor list cover only part of the group's releases, vendors and changes. | An id a subsidiary does not know cannot be checked against its own records and must not leave unverified. |
| R21 | The ticketing export nests journal entries (work notes, comments, system entries), of which only the human-written ones are narrative. Categories come as tool labels that must be mapped. | System entries repeat field changes and would inflate counts; tool labels differ and must map to one category set. |
| R22 | Follow-up is an evidence packet at each subsidiary, a problem-record draft for the problem manager and a vendor-escalation draft for the vendor manager. Nothing is written into the tools automatically. | The output must be work its owners recognise, and it stays under their approval. |
| R23 | The group has six subsidiaries in at least two countries, with different volumes and languages. | The pattern is cross-subsidiary by definition; differences in volume and language are what make a per-subsidiary view miss it. |
| R24 | Operations are reviewed weekly. A ticket week is final only after a close lag. | Counts are compared week by week, and a week still being worked must not be counted. |
| R25 | The central team already writes rules (the same certificate expiry at two subsidiaries; one vendor's backup failures at several). | Detection is measured against the rules that already exist, not against nothing. |
| R26 | Exports carry locales ("de-DE", "en-GB") rather than bare language codes. | The reading must choose its German or English cues from the locale the export gives. |
| R27 | Questions sent to a subsidiary to verify a pattern are in operational wording that a resolver understands. | A subsidiary answers a question it understands; wording from another field would be misread. |
