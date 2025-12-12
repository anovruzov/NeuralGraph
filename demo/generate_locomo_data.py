"""
Generate LoCoMo benchmark data for the demo UI.
Extracts all 1986 questions from locomo10.json and formats them for the web demo.
"""

import json
from pathlib import Path

# Category mapping from LoCoMo numeric categories to named categories
CATEGORY_MAP = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial"
}

CATEGORY_CONFIG = {
    "single_hop": {"name": "Single-hop", "icon": "🎯"},
    "multi_hop": {"name": "Multi-hop", "icon": "🔗"},
    "temporal": {"name": "Temporal", "icon": "⏰"},
    "open_domain": {"name": "Open Domain", "icon": "🌐"},
    "adversarial": {"name": "Adversarial", "icon": "⚔️"}
}

def main():
    # Load LoCoMo data
    locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"

    with open(locomo_path, 'r', encoding='utf-8') as f:
        locomo_data = json.load(f)

    # Extract all questions
    all_questions = []
    question_id = 1

    for conv_idx, conversation in enumerate(locomo_data):
        conv_id = conv_idx + 1

        for qa_idx, qa in enumerate(conversation['qa']):
            category_num = qa['category']
            category_name = CATEGORY_MAP.get(category_num, "unknown")

            question_data = {
                "id": f"Q{str(question_id).zfill(4)}",
                "conversation_id": conv_id,
                "question_index": qa_idx + 1,
                "category": category_name,
                "category_num": category_num,
                "question": qa.get('question', ''),
                "answer": str(qa.get('answer', '')),
                "evidence": qa.get('evidence', [])
            }

            all_questions.append(question_data)
            question_id += 1

    # Calculate category counts
    category_counts = {}
    for q in all_questions:
        cat = q['category']
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Build output structure
    output = {
        "metadata": {
            "total_questions": len(all_questions),
            "total_conversations": len(locomo_data),
            "category_counts": category_counts,
            "category_config": CATEGORY_CONFIG
        },
        "questions": all_questions
    }

    # Write to demo folder
    output_path = Path(__file__).parent / "locomo_benchmark.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Generated {output_path}")
    print(f"Total questions: {len(all_questions)}")
    print(f"Category counts: {category_counts}")
    print(f"Conversations: {len(locomo_data)}")

if __name__ == "__main__":
    main()
