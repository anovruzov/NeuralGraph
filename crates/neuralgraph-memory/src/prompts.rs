//! The extraction prompts, byte-identical to the ones the Python pipeline sent (generated from its source and
//! pinned by `tests/golden/prompts.json`). Templates use Python `str.format` syntax: `{name}` placeholders and
//! `{{`/`}}` for literal braces; see [`crate::extraction::py_format`].

pub const MEMORY_PROMPT: &str = r##"You maintain the long-term memory of an AI assistant across many separate chats.
Read the NEW MESSAGES and extract only what is worth remembering in FUTURE conversations. Use CONTEXT only to resolve references.

KEEP (durable, specific, useful later): identity details (name, location, job, family, age), stable facts, preferences and habits, dislikes, important events with dates, plans and commitments, relationships between people, health, goals, decisions, projects, possessions, strong opinions.
SKIP: pure greetings, thanks, jokes, {assistant_skip}, hypotheticals, momentary states ("I'm tired right now"), anything already in KNOWN MEMORIES unless the new message adds a concrete detail.
A reaction, compliment or question still COUNTS as a source when it restates or implies a fact about a participant: "That charity race for mental health sounds great" -> the race raised awareness for mental health; "For walking or running?" answered "Running" -> the shoes are for running; "You'll be an awesome mom" -> Melanie thinks Caroline will be a great mother. Read replies together with the message they answer.
If a message lists several items (books, hobbies, places, people), keep them all: one memory per item, or one memory that names every item verbatim. Never collapse a list into a vague summary.
Never store facts about the assistant itself.

RULES:
1. One memory = ONE self-contained sentence in the third person with full names. Never use "I", "you", "he", "she", "they" as the subject. Resolve nicknames and pronouns with KNOWN ENTITIES and the speaker names.
2. "subject" = the person (or thing) the memory is about, as a full name. "kind" = one of {kinds}.
3. Resolve relative time with the message date: "yesterday" said on 2026-09-17 is 2026-09-16. "when" = the date the memory is ABOUT as YYYY-MM-DD, YYYY-MM or YYYY; null when the memory is not tied to a date.
4. importance 0-1: 0.9 identity and major life events, 0.7 preferences, plans, relationships, health; 0.5 minor facts; 0.3 trivia.
5. confidence 0-1: 1.0 stated explicitly, 0.6 clearly implied, lower if uncertain. Do not invent details.
6. "sources" = indices of the NEW MESSAGES that support the memory.
7. "entities" = people, places, organisations, pets, products, projects, events named in the memory: short canonical names with a type from {{person, place, org, pet, product, project, event, other}}.
8. {assistant_rule}
9. Up to {max_memories} memories. Keep every specific detail about a participant as its own memory rather than merging several facts into one sentence. If nothing qualifies return {{"memories": []}}.

CHAT PARTICIPANTS: {speakers}
KNOWN ENTITIES: {registry}
KNOWN MEMORIES (already stored, do not repeat):
{known_memories}

CONTEXT (earlier messages, for reference only):
{context}

NEW MESSAGES (date: {date}):
{messages}

Return ONLY JSON:
{{"memories": [{{"text": "...", "kind": "fact", "subject": "Full Name", "when": null, "importance": 0.7, "confidence": 0.9, "sources": [1], "entities": [{{"name": "...", "type": "..."}}]}}]}}"##;

pub const ASSISTANT_RULE_OFF: &str = r##"Messages marked (assistant) are reference only and can never be a source; only what the human participants say counts."##;

pub const ASSISTANT_RULE_ON: &str = r##"Messages marked (assistant) ARE valid sources for what the assistant told the user: recommendations, names, numbers, ratios, instructions, places, titles. Phrase them from the user's side with the user as subject ("The user was recommended Miss Bee Providore in Bandung", "The user was told to dilute tea tree oil 1:10 with a carrier oil"). Never make the assistant itself the subject."##;

pub const ASSISTANT_SKIP_OFF: &str = r##"generic advice or explanations from the assistant"##;

pub const ASSISTANT_SKIP_ON: &str =
    r##"assistant explanations that carry no specific name, number, place, title or instruction"##;

pub const RELATION_PROMPT: &str = r##"You are building a knowledge graph from a chat between {speakers}.
From the NEW MESSAGES extract entities and [subject, relation, object] facts that are stated or clearly implied. Use CONTEXT only to resolve references.

RULES:
1. Entities: people, places, organisations, pets, products, projects, events, activities, media titles, jobs, health conditions. Short canonical names (lowercase is fine), singular. Resolve "I", "my", "me" to the speaker of that message. Reuse KNOWN ENTITIES names exactly when they refer to the same thing (nicknames, first names, pronouns).
2. Relation: a short snake_case verb phrase such as lives_in, works_at, works_as, has_pet, married_to, sibling_of, parent_of, friend_of, likes, dislikes, owns, uses, studied_at, plans_to_visit, moved_from, diagnosed_with, member_of, born_in, plays.
3. Skip greetings, small talk and generic assistant advice. Never invent relations. Never make the assistant a subject.
4. "source" = index of the NEW MESSAGE that supports the triple; "confidence" 0-1.
5. At most {max_triples} triples.

KNOWN ENTITIES: {registry}

CONTEXT (earlier messages):
{context}

NEW MESSAGES (date: {date}):
{messages}

Return ONLY JSON:
{{"entities": [{{"name": "...", "type": "person"}}], "triples": [{{"s": "...", "r": "...", "o": "...", "source": 1, "confidence": 0.9}}]}}"##;

pub const RECONCILE_PROMPT: &str = r##"You maintain a memory store about people. A NEW memory was just extracted. Compare it with the EXISTING memories about the same subject and decide what to do.

NEW (observed {new_date}): "{new_text}"

EXISTING:
{existing}

Decide exactly one:
- DUPLICATE: NEW adds nothing beyond one existing memory. target = its letter.
- UPDATE: NEW refines, extends or corrects the same fact as one existing memory. target = its letter; "text" = ONE merged sentence in third person that keeps every detail that is still true.
- CONTRADICT: NEW conflicts with one existing memory (the old fact is no longer true, e.g. moved, changed job, broke up). target = its letter.
- ADD: NEW is about something different from all existing memories. target = null.

Return ONLY JSON: {{"decision": "ADD", "target": null, "text": null, "reason": "short"}}"##;

pub const REPAIR_SUFFIX: &str = r##"

IMPORTANT: your previous answer to this request was not valid JSON. Return ONLY the JSON object, starting with {{ and ending with }}, with no prose before or after it."##;
