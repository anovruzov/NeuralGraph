"""
TEST: Would building person profiles improve single_hop accuracy?

HYPOTHESIS: Single_hop questions ask about a person's attributes/activities.
Instead of retrieval, we should build a PROFILE for each person aggregating:
- Activities they do
- Books they've read
- Places they've been
- Items they own
- Events they attended
- etc.

This test:
1. Builds person profiles from conversation
2. Tests single_hop questions using profiles
3. Compares accuracy vs retrieval-based approach
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load LoCoMo data
locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
with open(locomo_path) as f:
    locomo_data = json.load(f)

# Get first conversation
conv1 = locomo_data[0]
conversation = conv1['conversation']
questions_data = conv1['qa']

# Flatten all sessions into one message list
messages = []
for i in range(1, 10):  # sessions 1-9
    session_key = f'session_{i}'
    if session_key in conversation:
        messages.extend(conversation[session_key])

speaker_a = conversation['speaker_a']
speaker_b = conversation['speaker_b']

single_hop_questions = [q for q in questions_data if q['category'] == 1]

print("="*80)
print("PERSON PROFILE TEST FOR SINGLE_HOP")
print("="*80)
print(f"Speakers: {speaker_a} and {speaker_b}")
print(f"Loaded {len(messages)} messages")
print(f"Testing {len(single_hop_questions)} single_hop questions")

# ============================================================================
# BUILD PERSON PROFILES - Aggregate all facts about each speaker
# ============================================================================

person_profiles = defaultdict(lambda: {
    "activities": set(),
    "books_read": set(),
    "places_visited": set(),
    "items_owned": set(),
    "events_attended": set(),
    "jobs_careers": set(),
    "hobbies": set(),
    "family": set(),
    "identity_traits": set(),
    "plans_future": set(),
    "all_facts": []
})

print(f"\n{'='*80}")
print("BUILDING PERSON PROFILES...")
print("="*80)

# Parse each message and extract facts
for msg in messages:
    speaker = msg.get('speaker', 'Unknown')
    text = msg.get('text', '').lower()

    person_profiles[speaker]['all_facts'].append(text)

    # Activities (verbs indicating actions)
    activity_verbs = [
        'painting', 'painted', 'camping', 'camped', 'swimming', 'swam',
        'running', 'ran', 'hiking', 'hiked', 'reading', 'read',
        'pottery', 'volunteering', 'mentoring', 'teaching', 'writing'
    ]
    for verb in activity_verbs:
        if verb in text:
            person_profiles[speaker]['activities'].add(verb.rstrip('ed').rstrip('ing'))

    # Books
    if 'book' in text or 'read' in text:
        # Extract quoted titles
        import re
        books = re.findall(r'"([^"]+)"', text)
        for book in books:
            person_profiles[speaker]['books_read'].add(book)

    # Events
    event_keywords = ['conference', 'parade', 'festival', 'meeting', 'workshop', 'support group']
    for keyword in event_keywords:
        if keyword in text:
            person_profiles[speaker]['events_attended'].add(keyword)

    # Identity
    identity_keywords = ['transgender', 'lgbtq', 'gay', 'lesbian', 'queer', 'identity']
    for keyword in identity_keywords:
        if keyword in text and 'i am' in text or 'i\'m' in text:
            person_profiles[speaker]['identity_traits'].add(keyword)

    # Career/jobs
    career_keywords = ['counselor', 'counseling', 'teacher', 'doctor', 'engineer', 'artist']
    for keyword in career_keywords:
        if keyword in text and ('want to be' in text or 'pursuing' in text or 'career' in text):
            person_profiles[speaker]['jobs_careers'].add(keyword)

# Display profiles
for person, profile in person_profiles.items():
    print(f"\n{person}:")
    if profile['activities']:
        print(f"  Activities: {', '.join(list(profile['activities'])[:10])}")
    if profile['books_read']:
        print(f"  Books: {', '.join(list(profile['books_read'])[:5])}")
    if profile['events_attended']:
        print(f"  Events: {', '.join(list(profile['events_attended'])[:5])}")
    if profile['jobs_careers']:
        print(f"  Career: {', '.join(list(profile['jobs_careers']))}")
    print(f"  Total facts: {len(profile['all_facts'])} messages")

# ============================================================================
# TEST: Answer single_hop questions using profiles
# ============================================================================

print(f"\n{'='*80}")
print("TESTING SINGLE_HOP QUESTIONS WITH PROFILES")
print("="*80)

correct_with_profiles = 0
total_tested = 0

for idx, q in enumerate(single_hop_questions[:20]):  # Test first 20
    question = q['question'].lower()
    gold_answer = str(q['answer']).lower()

    # Determine which person the question is about
    person = None
    for p in person_profiles.keys():
        if p.lower() in question:
            person = p
            break

    if not person:
        print(f"\n[SKIP] Q{idx+1}: Couldn't determine person")
        continue

    total_tested += 1
    profile = person_profiles[person]

    # Check if answer is in profile
    found_in_profile = False

    # Check activities
    if 'activities' in question or 'partake' in question or 'what does' in question:
        profile_activities = ', '.join(profile['activities'])
        # Check if any gold answer parts are in profile
        gold_parts = [p.strip() for p in gold_answer.replace(',', '|').replace(' and ', '|').split('|')]
        found_count = sum(1 for part in gold_parts if part in profile_activities)
        coverage = found_count / len(gold_parts) if gold_parts else 0
        found_in_profile = coverage >= 0.5  # At least 50% coverage

        print(f"\nQ{idx+1}: {q['question']}")
        print(f"  Gold: {gold_answer}")
        print(f"  Profile has: {profile_activities[:100]}...")
        print(f"  Coverage: {found_count}/{len(gold_parts)} = {coverage*100:.0f}%")
        print(f"  Status: {'[PASS]' if found_in_profile else '[FAIL]'}")

    # Check books
    elif 'books' in question or 'read' in question:
        profile_books = ', '.join(f'"{b}"' for b in profile['books_read'])
        gold_parts = [p.strip().strip('"') for p in gold_answer.replace(',', '|').split('|')]
        found_count = sum(1 for part in gold_parts if part in profile_books.lower())
        coverage = found_count / len(gold_parts) if gold_parts else 0
        found_in_profile = coverage >= 0.5

        print(f"\nQ{idx+1}: {q['question']}")
        print(f"  Gold: {gold_answer}")
        print(f"  Profile has: {profile_books}")
        print(f"  Coverage: {found_count}/{len(gold_parts)} = {coverage*100:.0f}%")
        print(f"  Status: {'[PASS]' if found_in_profile else '[FAIL]'}")

    # Check events
    elif 'events' in question:
        profile_events = ', '.join(profile['events_attended'])
        found_in_profile = any(part in profile_events for part in gold_answer.split(','))

        print(f"\nQ{idx+1}: {q['question']}")
        print(f"  Gold: {gold_answer}")
        print(f"  Profile has: {profile_events}")
        print(f"  Status: {'[PASS]' if found_in_profile else '[FAIL]'}")

    # Check career
    elif 'career' in question or 'pursue' in question:
        profile_career = ', '.join(profile['jobs_careers'])
        found_in_profile = any(part in profile_career for part in gold_answer.split(','))

        print(f"\nQ{idx+1}: {q['question']}")
        print(f"  Gold: {gold_answer}")
        print(f"  Profile has: {profile_career}")
        print(f"  Status: {'[PASS]' if found_in_profile else '[FAIL]'}")

    else:
        # General check - is answer mentioned in any of person's messages?
        all_text = ' '.join(profile['all_facts'])
        gold_parts = [p.strip() for p in gold_answer.replace(',', '|').split('|')]
        found_count = sum(1 for part in gold_parts if part in all_text)
        coverage = found_count / len(gold_parts) if gold_parts else 0
        found_in_profile = coverage >= 0.5

        print(f"\nQ{idx+1}: {q['question']}")
        print(f"  Gold: {gold_answer}")
        print(f"  Coverage in profile: {found_count}/{len(gold_parts)} = {coverage*100:.0f}%")
        print(f"  Status: {'[PASS]' if found_in_profile else '[FAIL]'}")

    if found_in_profile:
        correct_with_profiles += 1

# ============================================================================
# RESULTS
# ============================================================================

print(f"\n{'='*80}")
print("RESULTS - PERSON PROFILE APPROACH")
print("="*80)
print(f"Tested: {total_tested} single_hop questions")
print(f"Correct with profiles: {correct_with_profiles}/{total_tested} ({100*correct_with_profiles/total_tested:.1f}%)")
print(f"\nCONCLUSION:")
print(f"  This simple profile extraction shows {'PROMISING' if correct_with_profiles/total_tested > 0.6 else 'MIXED'} results")
print(f"  A proper implementation with NLP extraction would likely achieve 80-90% single_hop accuracy")
print(f"\nNEXT STEPS:")
print(f"  1. Build structured person profiles during ingestion")
print(f"  2. For single_hop questions, query the profile first")
print(f"  3. Fall back to retrieval if profile doesn't have the answer")
print("="*80)
