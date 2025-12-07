"""
Pre-classify all LoCoMo MC10 messages with domain labels using Ollama.

This creates a new enriched dataset file with domain labels for each message,
eliminating the need for runtime classification.

Domains:
- personal: Identity, beliefs, emotions, personality
- work: Career, job, professional life
- hobbies: Activities, interests, leisure
- relationships: Family, friends, social connections
- health: Medical, wellness, fitness
- finance: Money, purchases, financial matters
- events: Specific occurrences, dated activities
- location: Places, travel, geography
- general: Greetings, meta-conversation
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime
import requests

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Domain definitions
DOMAINS = [
    "personal",     # Identity, beliefs, emotions, personality
    "work",         # Career, job, professional life
    "hobbies",      # Activities, interests, leisure
    "relationships",# Family, friends, social connections
    "health",       # Medical, wellness, fitness
    "finance",      # Money, purchases, financial matters
    "events",       # Specific occurrences, dated activities
    "location",     # Places, travel, geography
    "general",      # Greetings, meta-conversation
]

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5:7b-instruct"


def ollama_chat(prompt: str) -> str:
    """Call Ollama API."""
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.0}
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    except Exception as e:
        print(f"  Ollama error: {e}")
        return "general"


def classify_batch_with_ollama(messages: list[str]) -> list[str]:
    """Classify a batch of messages (up to 20) in a single Ollama call."""
    if not messages:
        return []

    batch_prompt = """Classify each of the following messages into ONE primary domain.

Domains:
- personal: Identity, beliefs, emotions, personality
- work: Career, job, professional life
- hobbies: Activities, interests, leisure, sports, arts
- relationships: Family, friends, romantic, social
- health: Medical, wellness, fitness, mental health
- finance: Money, purchases, financial
- events: Specific occurrences, dated activities
- location: Places, travel, geography
- general: Greetings, small talk, meta-conversation

Messages:
"""
    for i, msg in enumerate(messages):
        batch_prompt += f"{i+1}. {msg[:200]}\n"

    batch_prompt += "\nReply with ONLY domain names, one per line (e.g.):\npersonal\nhobbies\nevents\n..."

    try:
        response = ollama_chat(batch_prompt)
        domains = response.strip().lower().split('\n')
        result = []

        for i, d in enumerate(domains):
            d = d.strip().replace('.', '').replace(',', '')
            # Remove any numbering
            for prefix in ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '-', ' ']:
                d = d.lstrip(prefix)
            d = d.strip()

            if d in DOMAINS:
                result.append(d)
            else:
                # Try partial match
                matched = False
                for domain in DOMAINS:
                    if domain in d:
                        result.append(domain)
                        matched = True
                        break
                if not matched:
                    result.append("general")

        # Pad with "general" if needed
        while len(result) < len(messages):
            result.append("general")

        return result[:len(messages)]
    except Exception as e:
        print(f"  Batch error: {e}")
        return ["general"] * len(messages)


def preprocess_locomo_with_domains(input_path: str, output_path: str):
    """Process LoCoMo dataset and add domain labels to all messages."""

    print(f"Loading LoCoMo dataset from: {input_path}")
    with open(input_path, 'r', encoding='utf-8') as f:
        dataset = json.load(f)

    print(f"Found {len(dataset)} conversations")

    total_messages = 0
    domain_counts = {d: 0 for d in DOMAINS}

    for conv_idx, item in enumerate(dataset):
        conversation = item.get("conversation", item)
        speakers = [
            conversation.get('speaker_a', 'Person A'),
            conversation.get('speaker_b', 'Person B')
        ]

        print(f"\n--- Conversation {conv_idx + 1}: {speakers[0]} & {speakers[1]} ---")

        session_idx = 1
        while True:
            session_key = f"session_{session_idx}"
            datetime_key = f"session_{session_idx}_date_time"

            if session_key not in conversation:
                break

            session_messages = conversation[session_key]
            session_time = conversation.get(datetime_key, "Unknown time")

            print(f"  Session {session_idx}: {len(session_messages)} messages")

            # Collect messages for batch classification
            batch_texts = []
            batch_indices = []

            for msg_idx, msg in enumerate(session_messages):
                if isinstance(msg, dict) and 'text' in msg:
                    batch_texts.append(msg['text'])
                    batch_indices.append(msg_idx)

            # Classify in batches of 20
            batch_size = 20
            for batch_start in range(0, len(batch_texts), batch_size):
                batch_end = min(batch_start + batch_size, len(batch_texts))
                batch = batch_texts[batch_start:batch_end]
                indices = batch_indices[batch_start:batch_end]

                domains = classify_batch_with_ollama(batch)

                for i, (msg_idx, domain) in enumerate(zip(indices, domains)):
                    session_messages[msg_idx]['domain'] = domain
                    domain_counts[domain] += 1
                    total_messages += 1

            session_idx += 1

        # Save progress every 10 conversations
        if (conv_idx + 1) % 1 == 0:
            print(f"  Saving progress... ({total_messages} messages processed)")
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(dataset, f, indent=2)

    # Final save
    print(f"\n{'='*60}")
    print(f"PREPROCESSING COMPLETE")
    print(f"{'='*60}")
    print(f"Total messages: {total_messages}")
    print(f"\nDomain distribution:")
    for domain, count in sorted(domain_counts.items(), key=lambda x: -x[1]):
        pct = count / total_messages * 100 if total_messages > 0 else 0
        print(f"  {domain}: {count} ({pct:.1f}%)")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, indent=2)

    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    # Paths
    input_path = Path(__file__).parent.parent / "locomo" / "locomo10.json"
    output_path = Path(__file__).parent.parent / "locomo" / "locomo10_with_domains.json"

    preprocess_locomo_with_domains(str(input_path), str(output_path))
