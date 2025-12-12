"""Deep debug: Why is retrieval finding the WRONG events?"""
import json
from datetime import datetime
from pathlib import Path

data = json.load(open(Path(__file__).parent / "locomo" / "locomo10.json"))
conv = data[0]["conversation"]

# Failed question: "When did Caroline go to a pride parade during the summer?"
# Gold: "The week before 3 July 2023"
# Generated: "11 August 2023" (wrong event - August parade instead of June/July parade)

print("DEBUG: Pride Parade Temporal Search")
print("=" * 70)
print("Question: When did Caroline go to a pride parade during the summer?")
print("Gold Answer: The week before 3 July 2023")
print()

# Find ALL mentions of pride parade
print("ALL 'pride parade' mentions in conversations:")
print("-" * 50)

for session_num in range(1, 25):
    session_key = f"session_{session_num}"
    if session_key not in conv:
        continue

    session_date = conv.get(f"{session_key}_date_time", "Unknown")

    for msg in conv[session_key]:
        text = msg["text"].lower()
        if "pride" in text and "parade" in text:
            print(f"\nSession {session_num} ({session_date}):")
            print(f"  [{msg['speaker']}]: {msg['text'][:200]}...")

print("\n" + "=" * 70)
print("INSIGHT: There are MULTIPLE pride parades mentioned!")
print("The question asks about 'during the summer' - we need to find the RIGHT one.")
print()

# Now check: What is the temporal constraint?
# "during the summer" typically means June-August
# Gold answer is "week before 3 July" so we need the JUNE parade

print("=" * 70)
print("DEBUGGING: When did Caroline join a mentorship program?")
print("Gold: The weekend before 17 July 2023")
print("-" * 50)

for session_num in range(1, 25):
    session_key = f"session_{session_num}"
    if session_key not in conv:
        continue

    session_date = conv.get(f"{session_key}_date_time", "Unknown")

    for msg in conv[session_key]:
        text = msg["text"].lower()
        if "mentorship" in text:
            print(f"\nSession {session_num} ({session_date}):")
            print(f"  [{msg['speaker']}]: {msg['text'][:200]}...")

print("\n" + "=" * 70)
print("DEBUGGING: When did Caroline join a new activist group?")
print("Gold: The Tuesday before 20 July 2023")
print("-" * 50)

for session_num in range(1, 25):
    session_key = f"session_{session_num}"
    if session_key not in conv:
        continue

    session_date = conv.get(f"{session_key}_date_time", "Unknown")

    for msg in conv[session_key]:
        text = msg["text"].lower()
        if "activist" in text:
            print(f"\nSession {session_num} ({session_date}):")
            print(f"  [{msg['speaker']}]: {msg['text'][:200]}...")
