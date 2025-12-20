"""
Inspect speaker profiles being built during ingestion
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from NeuralGraph.speaker_profiles import UniversalSpeakerProfiler

# Load LoCoMo data
locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
with open(locomo_path) as f:
    locomo_data = json.load(f)

# Get first conversation
conv1 = locomo_data[0]
conversation = conv1['conversation']

# Flatten all sessions into messages
messages = []
for i in range(1, 10):
    session_key = f'session_{i}'
    if session_key in conversation:
        for msg in conversation[session_key]:
            messages.append({
                "speaker": msg.get('speaker', 'Unknown'),
                "text": msg.get('text', '')
            })

speaker_a = conversation['speaker_a']
speaker_b = conversation['speaker_b']

print("="*80)
print("SPEAKER PROFILE INGESTION TEST")
print("="*80)
print(f"Speakers: {speaker_a} and {speaker_b}")
print(f"Total messages: {len(messages)}\n")

# Build profiles incrementally (simulating ingestion)
profiler = UniversalSpeakerProfiler()

print("="*80)
print("BUILDING PROFILES DURING INGESTION")
print("="*80)

for idx, msg in enumerate(messages):
    profiler.add_message(msg['speaker'], msg['text'])

    # Show profile snapshots at key points
    if idx in [10, 50, 100, len(messages)-1]:
        print(f"\n{'='*80}")
        print(f"After {idx+1} messages:")
        print(f"{'='*80}")

        for speaker_name, profile in profiler.profiles.items():
            print(f"\n{speaker_name} Profile:")
            profile_dict = profile.to_dict()

            if profile_dict['activities']:
                print(f"  Activities ({len(profile_dict['activities'])}): {', '.join(list(profile_dict['activities'])[:10])}")

            if profile_dict['books_read']:
                print(f"  Books ({len(profile_dict['books_read'])}): {', '.join(list(profile_dict['books_read'])[:5])}")

            if profile_dict['events_attended']:
                print(f"  Events ({len(profile_dict['events_attended'])}): {', '.join(list(profile_dict['events_attended'])[:5])}")

            if profile_dict['places_visited']:
                print(f"  Places ({len(profile_dict['places_visited'])}): {', '.join(list(profile_dict['places_visited'])[:5])}")

            if profile_dict['career_goals']:
                print(f"  Career: {', '.join(profile_dict['career_goals'])}")

            print(f"  Total messages: {profile_dict['message_count']}")

print(f"\n{'='*80}")
print("FINAL PROFILES")
print(f"{'='*80}")

for speaker_name, profile in profiler.profiles.items():
    print(f"\n{speaker_name}:")
    profile_dict = profile.to_dict()
    print(f"  Activities: {sorted(profile_dict['activities'])}")
    print(f"  Books: {sorted(profile_dict['books_read'])}")
    print(f"  Events: {sorted(profile_dict['events_attended'])}")
    print(f"  Places: {sorted(profile_dict['places_visited'])}")
    print(f"  Career: {sorted(profile_dict['career_goals'])}")
    print(f"  Messages: {profile_dict['message_count']}")

# Test some queries
print(f"\n{'='*80}")
print("TEST QUERIES")
print(f"{'='*80}")

test_questions = [
    ("What activities does Melanie partake in?", "pottery, camping, painting, swimming"),
    ("What did Caroline research?", "adoption agencies"),
    ("What events has Caroline attended?", "pride parade"),
    ("What books has Melanie read?", "Charlotte's Web"),
]

for question, gold in test_questions:
    result = profiler.query_single_hop(question, gold)
    print(f"\nQ: {question}")
    print(f"  Gold: {gold}")
    print(f"  Speaker: {result.get('speaker', 'Unknown')}")
    print(f"  Found: {result['found']}")
    print(f"  Answer: {result['answer']}")
    print(f"  Confidence: {result['confidence']:.1%}")
    print(f"  Source: {result['source']}")
    print(f"  Status: {'[PASS]' if result['found'] and result['confidence'] >= 0.5 else '[FAIL]'}")

print(f"\n{'='*80}")
