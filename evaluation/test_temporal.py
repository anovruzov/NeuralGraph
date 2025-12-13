"""Test temporal retrieval for Caroline question."""
import json
import re
from pathlib import Path

# Import from benchmark
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from evaluation.locomo10_parallel_benchmark import (
    is_temporal_query, extract_temporal_markers, compute_temporal_score,
    extract_query_structure, universal_expand
)

# Load data
data = json.load(open(Path(__file__).parent / 'locomo/locomo10.json', encoding='utf-8'))
msgs = []
for i in range(1, 30):
    key = f'session_{i}'
    if key in data[0]['conversation']:
        for m in data[0]['conversation'][key]:
            msgs.append({'speaker': m.get('speaker', 'Unknown'), 'text': m.get('text', '')})

question = 'Where did Caroline move from 4 years ago?'
print(f'Q: {question}')
print(f'is_temporal: {is_temporal_query(question)}')

q_markers, q_scale = extract_temporal_markers(question)
print(f'Q markers: {q_markers}, scale: {q_scale}')

struct = extract_query_structure(question)
print(f'entities: {struct["entities"]}')
print(f'action: {struct["action"]}')
print(f'action_variants: {struct["action_variants"]}')

# Test key messages
for idx in [47, 60]:
    msg = msgs[idx]
    score = compute_temporal_score(msg['text'], question)
    m_markers, m_scale = extract_temporal_markers(msg['text'])

    # Check action match
    text_lower = msg['text'].lower()
    msg_words = set(re.findall(r'\b[a-z]+\b', text_lower))
    action_match = bool(struct['action_variants'] & msg_words) if struct['action_variants'] else False
    speaker_match = any(e in msg['speaker'].lower() for e in struct['entities'])

    print(f'\n[{idx}] {msg["speaker"][:10]}: {msg["text"][:60]}...')
    print(f'  temporal_score={score:.2f}, markers={m_markers}, scale={m_scale}')
    print(f'  speaker_match={speaker_match}, action_match={action_match}')
