"""Debug temporal resolution issues."""
import json
from datetime import datetime, timedelta
from pathlib import Path

data = json.load(open(Path(__file__).parent / "locomo" / "locomo10.json"))

conv = data[0]

# Find question: "When did Caroline give a speech at a school?"
# Gold: "The week before 9 June 2023"

print("DEBUG: Temporal Resolution")
print("=" * 70)

# Look at session 3 which is 9 June 2023
print("\nSession 3 (9 June 2023) messages about school/speech:")
for msg in conv["conversation"]["session_3"]:
    text = msg["text"].lower()
    if "school" in text or "speech" in text:
        print(f"  [{msg['speaker']}]: {msg['text'][:150]}...")

# The message says "last week" - relative to 9 June 2023
# "last week" from 9 June would be week of 29 May - 4 June
# So the gold "The week before 9 June" is correct

print("\n" + "=" * 70)
print("GOLD ANSWER ANALYSIS")
print("=" * 70)

# Look at some temporal questions
qa = conv.get("qa", [])
temporal_qa = [q for q in qa if q.get("category") == 2]

for q in temporal_qa[:10]:
    question = q["question"]
    answer = q["answer"]
    print(f"\nQ: {question}")
    print(f"GOLD: {answer}")

    # Try to parse the gold answer
    if "before" in answer.lower():
        # Format: "The X before DATE"
        # This is relative to DATE in the answer itself!
        print("  -> RELATIVE TO SPECIFIC DATE IN ANSWER")
    elif "2023" in answer:
        print("  -> ABSOLUTE DATE")
    elif "2022" in answer:
        print("  -> ABSOLUTE YEAR")
    else:
        print("  -> RELATIVE (month/general)")
