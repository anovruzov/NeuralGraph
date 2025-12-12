"""Analyze failed temporal questions to understand patterns."""
import json
from pathlib import Path
from collections import defaultdict

results_dir = Path(__file__).parent / "results"
files = sorted(results_dir.glob("locomo10_TESSERACT_202512*.json"), reverse=True)

failed_temporal = []
for f in files[:100]:
    try:
        data = json.load(open(f))
        if "results" in data:
            for r in data["results"]:
                if r.get("category") == "temporal" and not r.get("correct"):
                    failed_temporal.append({
                        "q": r["question"],
                        "gold": str(r["gold"]),
                        "gen": str(r["generated"]),
                    })
    except:
        pass

print("FAILED TEMPORAL QUESTIONS ANALYSIS")
print("=" * 70)
print(f"Total failed temporal: {len(failed_temporal)}")
print()

# Categorize failure types
failure_types = defaultdict(list)

for f in failed_temporal:
    q = f["q"].lower()
    gold = f["gold"].lower()
    gen = f["gen"].lower()

    # Categorize by question pattern
    if "how long" in q:
        failure_types["duration"].append(f)
    elif "when did" in q or "when was" in q or "when is" in q:
        failure_types["when_event"].append(f)
    elif "last time" in q or "first time" in q:
        failure_types["first_last"].append(f)
    elif "before" in q or "after" in q:
        failure_types["sequence"].append(f)
    elif "how many times" in q:
        failure_types["frequency"].append(f)
    else:
        failure_types["other_temporal"].append(f)

print("FAILURE TYPE BREAKDOWN:")
print("-" * 40)
for ftype, items in sorted(failure_types.items(), key=lambda x: -len(x[1])):
    print(f"{ftype}: {len(items)}")

print("\n" + "=" * 70)
print("SAMPLE FAILURES BY TYPE:")
print("=" * 70)

for ftype, items in sorted(failure_types.items(), key=lambda x: -len(x[1])):
    print(f"\n### {ftype.upper()} ({len(items)} failures) ###")
    for i, f in enumerate(items[:3]):
        print(f"\n[{i+1}]")
        print(f"  Q: {f['q'][:120]}")
        print(f"  GOLD: {f['gold'][:80]}")
        print(f"  GEN:  {f['gen'][:80]}")
