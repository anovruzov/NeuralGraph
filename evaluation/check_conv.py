import json

# Check locomo10.json directly for conv 3 questions
with open('locomo/locomo10.json', encoding='utf-8') as f:
    data = json.load(f)

conv = data[3]
qa = conv.get('qa', [])

print(f"Conversation 3 has {len(qa)} questions\n")

# Find John and Maria questions
for i, q in enumerate(qa):
    qtext = q.get('question', '')
    if 'John' in qtext or 'Maria' in qtext:
        print(f"#{i}: {qtext}")
        print(f"  Answer: {q.get('answer', '')}")
        print()
