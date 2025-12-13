"""Standalone temporal test - copies functions directly."""
import json
import re
from pathlib import Path

# ============= COPIED FUNCTIONS =============

def is_temporal_query(question: str) -> bool:
    """Detect if question is asking about time/when."""
    q = question.lower()
    if q.startswith('when'):
        return True
    if re.search(r'\bhow\s+long\b', q):
        return True
    if re.search(r'\b(yesterday|today|last\s+week|last\s+month|last\s+year|ago|recently)\b', q):
        return True
    return False


def extract_temporal_markers(text: str) -> tuple[set[str], str]:
    """Extract temporal markers from text."""
    markers = set()
    text_lower = text.lower()
    time_scale = None

    patterns = [
        r'\byesterday\b', r'\btoday\b', r'\btomorrow\b',
        r'\blast\s+week\b', r'\bthis\s+week\b', r'\bnext\s+week\b',
        r'\blast\s+month\b', r'\bthis\s+month\b',
        r'\blast\s+year\b', r'\bthis\s+year\b',
        r'\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b',
        r'\brecently\b', r'\bjust\b',
        r'\ba\s+few\s+(?:days?|weeks?|months?|years?)\s+ago\b',
        r'\b(?:several|many|some|few)\s+(?:days?|weeks?|months?|years?)\s+ago\b',
        r'\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:days?|weeks?|months?|years?)\s+ago\b',
        r'\bback\s+when\b', r'\bwhen\s+i\s+first\b', r'\boriginally\b', r'\binitially\b',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, text_lower)
        markers.update(matches)

    if re.search(r'\byear', text_lower):
        time_scale = 'years'
    elif re.search(r'\bmonth', text_lower):
        time_scale = 'months'
    elif re.search(r'\bweek', text_lower):
        time_scale = 'weeks'
    elif re.search(r'\bday', text_lower):
        time_scale = 'days'

    return markers, time_scale


def compute_temporal_score(msg_text: str, question: str) -> float:
    """Score message based on temporal relevance."""
    q_markers, q_scale = extract_temporal_markers(question)
    msg_markers, msg_scale = extract_temporal_markers(msg_text)
    score = 1.0

    if not q_markers and not q_scale:
        if msg_markers:
            return 1.5
        if re.search(r'\b(when|then|after|before|during|while|ago|later|earlier)\b', msg_text.lower()):
            return 1.2
        return 1.0

    overlap = q_markers & msg_markers
    if overlap:
        score = 2.5
    elif q_scale and msg_scale and q_scale == msg_scale:
        score = 2.0
    elif msg_markers:
        score = 1.3

    msg_lower = msg_text.lower()
    if re.search(r'\b(?:moved?|came|left|from|originally|back\s+home)\b', msg_lower):
        if re.search(r'\b(?:moved?|from|came)\b', question.lower()):
            score *= 1.5

    return score


def universal_expand(word: str) -> set[str]:
    """Generate morphological variants."""
    if not word:
        return set()
    variants = {word}
    w = word.lower()
    variants.add(w)
    base = w
    if w.endswith('ed'):
        base = w[:-2]
        if base.endswith('i'):
            base = base[:-1] + 'y'
        elif len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
    elif w.endswith('ing'):
        base = w[:-3]
        if len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
    elif w.endswith('ies'):
        base = w[:-3] + 'y'
    elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
        base = w[:-1]
    variants.add(base)
    if len(base) >= 2:
        variants.add(base + 's')
        variants.add(base + 'ed')
        variants.add(base + 'ing')
        if base.endswith('e'):
            variants.add(base[:-1] + 'ing')
            variants.add(base + 'd')
    return {v for v in variants if len(v) >= 2 and v.isalpha() and not v.endswith('inging')}


def extract_query_structure(question: str) -> dict:
    """Extract WHO + ACTION + ATTRIBUTE."""
    SKIP = {'What', 'When', 'Where', 'Who', 'Why', 'How', 'Did', 'Does', 'Do',
            'Is', 'Are', 'Was', 'Were', 'Has', 'Have', 'Had', 'The', 'Many'}
    entities = {e.lower() for e in re.findall(r'\b[A-Z][a-z]+\b', question) if e not in SKIP}

    action_match = re.search(r'\b(?:has|have|did|does|do)\s+[A-Z][a-z]+\s+(\w+)', question)
    if not action_match:
        action_match = re.search(r'[A-Z][a-z]+\s+(\w+ed|\w+ing)\b', question)
    action = action_match.group(1).lower() if action_match else None

    return {
        'entities': entities,
        'action': action,
        'action_variants': universal_expand(action) if action else set(),
    }


# ============= TEST =============
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

    text_lower = msg['text'].lower()
    msg_words = set(re.findall(r'\b[a-z]+\b', text_lower))
    action_match = bool(struct['action_variants'] & msg_words) if struct['action_variants'] else False
    speaker_match = any(e in msg['speaker'].lower() for e in struct['entities'])

    print(f'\n[{idx}] {msg["speaker"][:10]}: {msg["text"][:80]}...')
    print(f'  temporal_score={score:.2f}, markers={m_markers}, scale={m_scale}')
    print(f'  speaker_match={speaker_match}, action_match={action_match}')
    if action_match:
        matched = struct['action_variants'] & msg_words
        print(f'  action_matched_words: {matched}')
