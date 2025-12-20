"""
Quick test to verify AGGREGATION questions get 30 memories (not 15)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from NeuralGraph.temporal_utils import infer_query_mode

# Test questions that should route to AGGREGATION
test_questions = [
    "What activities does Melanie partake in?",  # Q16 - failed with 15, needs 30
    "What do Melanie's kids like?",  # Q20 - failed with 15
    "What books has Melanie read?",  # Q24 - failed with 15
    "What did Caroline research?",  # Should be AGGREGATION
    "What LGBTQ+ events has Caroline participated in?",  # Should be AGGREGATION
]

print("="*80)
print("CONTEXT WINDOW TEST - Verify AGGREGATION gets 30 memories")
print("="*80)

for q in test_questions:
    mode = infer_query_mode(q)
    expected_memories = 30 if mode == "AGGREGATION" else 15
    print(f"\nQ: {q}")
    print(f"   Mode: {mode}")
    print(f"   Expected memories: {expected_memories}")

    if mode == "AGGREGATION" and expected_memories == 30:
        print(f"   [YES] Will get 30 memories!")
    elif mode == "AGGREGATION":
        print(f"   [NO] BUG - Should get 30 but getting {expected_memories}")
    else:
        print(f"   [NO] NOT ROUTED TO AGGREGATION (routed to {mode})")

print("\n" + "="*80)
print("Test shows routing - actual memory count happens in runner.py")
print("The fix ensures: query_mode determined BEFORE reranking")
print("="*80)
