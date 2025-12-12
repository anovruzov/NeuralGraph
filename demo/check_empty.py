import json

with open('locomo_benchmark.json', encoding='utf-8') as f:
    data = json.load(f)

empty = 0
total = len(data['questions'])

for q in data['questions']:
    ans = q.get('answer', '')
    if not ans or str(ans).strip() == '':
        empty += 1
        print(f"Empty: {q['id']} - {q['question'][:60]}...")

print(f"\n=== SUMMARY ===")
print(f"Total questions: {total}")
print(f"Empty answers: {empty}")
print(f"With answers: {total - empty}")
