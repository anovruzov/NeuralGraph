import json
data = json.load(open("C:/Users/anovr/Desktop/MemMachine-main/evaluation/locomo/locomo10.json"))
msgs = []
for i in range(1, 30):
    key = f"session_{i}"
    if key in data[0]["conversation"]:
        for m in data[0]["conversation"][key]:
            msgs.append(m)

# Find all messages about Sweden or moving
for i, m in enumerate(msgs):
    text = m['text'].lower()
    if 'sweden' in text or 'move' in text or 'home country' in text or '4 years' in text or 'four years' in text:
        print(f"[{i}] {m['speaker']}: {m['text'][:200]}...")
        print()
