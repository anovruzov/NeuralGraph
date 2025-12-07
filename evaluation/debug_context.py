import json
from pathlib import Path

data_path = Path("evaluation/locomo/locomo10.json")
with open(data_path, "r", encoding="utf-8") as f:
    dataset = json.load(f)

item = dataset[0]
conv = item.get("conversation", {})

# Count sessions
session_count = 0
total_messages = 0
for key in conv:
    if key.startswith("session_") and not key.endswith("_date_time"):
        session_count += 1
        total_messages += len(conv[key])

print(f"Sessions: {session_count}")
print(f"Total messages: {total_messages}")

# Build context
text_parts = []
for i in range(1, session_count + 1):
    session_key = f"session_{i}"
    datetime_key = f"session_{i}_date_time"
    session_time = conv.get(datetime_key, "")
    text_parts.append(f"=== Session {i} ({session_time}) ===")
    for msg in conv[session_key]:
        speaker = msg["speaker"]
        text = msg["text"]
        text_parts.append(f"{speaker}: {text}")

context = "\n".join(text_parts)
print(f"Context length: {len(context)} chars")
print(f"Context words: ~{len(context.split())} words")
print("\n--- First 1000 chars ---")
print(context[:1000])
