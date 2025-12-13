import json
data = json.load(open("C:/Users/anovr/Desktop/MemMachine-main/evaluation/locomo/locomo10.json"))
msgs = []
for i in range(1, 30):
    key = f"session_{i}"
    if key in data[0]["conversation"]:
        for m in data[0]["conversation"][key]:
            msgs.append(m)

for idx in [365, 300, 367, 263]:
    print(f"[{idx}] {msgs[idx]['speaker']}: {msgs[idx]['text']}")
    print()
