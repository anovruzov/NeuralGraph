"""Inspect temporal structure of LoCoMo data."""
import json
from pathlib import Path

data = json.load(open(Path(__file__).parent / "locomo" / "locomo10.json"))

conv = data[0]["conversation"]

print("CONVERSATION 1 - TEMPORAL STRUCTURE")
print("=" * 70)

# Get all session dates
session_dates = []
for i in range(1, 30):
    if f"session_{i}" in conv:
        dt = conv.get(f"session_{i}_date_time", "N/A")
        session_dates.append((i, dt))
        print(f"Session {i}: {dt}")

print("\n" + "=" * 70)
print("SAMPLE MESSAGES WITH TEMPORAL REFERENCES")
print("=" * 70)

# Look at messages with temporal words
temporal_words = ["yesterday", "today", "tomorrow", "last week", "next week",
                  "last month", "this month", "last friday", "next friday"]

for i in range(1, 6):
    if f"session_{i}" in conv:
        print(f"\n### SESSION {i} ({conv.get(f'session_{i}_date_time', 'N/A')}) ###")
        for msg in conv[f"session_{i}"][:5]:
            text = msg.get("text", "")
            text_lower = text.lower()
            for tw in temporal_words:
                if tw in text_lower:
                    print(f"  [{msg['speaker']}]: {text[:150]}...")
                    break

print("\n" + "=" * 70)
print("SAMPLE TEMPORAL QUESTIONS AND GOLD ANSWERS")
print("=" * 70)

qa = data[0].get("qa", [])
temporal_qa = [q for q in qa if q.get("category") == 2]  # category 2 = temporal

for q in temporal_qa[:10]:
    print(f"\nQ: {q['question']}")
    print(f"A: {q['answer']}")
