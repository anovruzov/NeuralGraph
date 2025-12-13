import json
data = json.load(open('C:/Users/anovr/Desktop/MemMachine-main/evaluation/locomo/locomo10.json'))
conv = data[0]['conversation']

# Count messages per session
total = 0
for i in range(1, 30):
    key = f'session_{i}'
    if key in conv:
        count = len(conv[key])
        print(f'Session {i}: {count} messages (cumulative: {total} - {total + count - 1})')
        total += count

print(f'\nTotal messages: {total}')

# What index is session_6 message 5?
flat_idx = 0
for i in range(1, 7):
    key = f'session_{i}'
    if key in conv:
        if i == 6:
            print(f'\nSession 6 message 5 is at flat index: {flat_idx + 5}')
        flat_idx += len(conv[key])
