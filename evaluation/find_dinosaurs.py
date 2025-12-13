import json
data = json.load(open('C:/Users/anovr/Desktop/MemMachine-main/evaluation/locomo/locomo10.json'))
conv = data[0]['conversation']

# D6:6 means session 6, message 6 (or index 5 if 0-based)
# D4:8 means session 4, message 8

for session_num in [4, 6]:
    key = f'session_{session_num}'
    if key in conv:
        print(f'Session {session_num}:')
        for i, msg in enumerate(conv[key]):
            text = msg.get('text', '')
            speaker = msg.get('speaker', '?')
            if 'dinosaur' in text.lower() or 'nature' in text.lower() or 'kids like' in text.lower():
                print(f'  [{i}] {speaker}: {text[:300]}...')
        print()

# Search ALL sessions for dinosaur
print("\n\nSearching ALL sessions for 'dinosaur':")
for key, val in conv.items():
    if key.startswith('session_') and isinstance(val, list):
        for i, msg in enumerate(val):
            text = msg.get('text', '')
            if 'dinosaur' in text.lower():
                print(f'{key}[{i}]: {text[:300]}')
