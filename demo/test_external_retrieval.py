"""
Quick test for the external retrieval implementation.
Tests the open-domain detection and Wikipedia retrieval.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from NeuralGraph.external_retriever import (
    WikipediaRetriever,
    WikipediaConfig,
    is_open_domain_query,
    get_open_domain_tracer,
)
from NeuralGraph.query_router import QueryRouter
from NeuralGraph.tesseract import detect_query_type, QueryType


def test_open_domain_detection():
    """Test that open-domain queries are properly detected."""
    print("\n" + "="*60)
    print("Testing Open-Domain Query Detection")
    print("="*60)

    test_queries = [
        # Should be open-domain (encyclopedic/world knowledge)
        ("What causes rain?", True),
        ("What is photosynthesis?", True),
        ("How does gravity work?", True),
        ("What is the capital of France?", True),
        ("When was the Eiffel Tower built?", True),
        ("Define entropy", True),
        ("Why do leaves change color?", True),

        # Should NOT be open-domain (session-specific)
        ("What did she say yesterday?", False),
        ("When did he mention the meeting?", False),
        ("What is Caroline's job?", False),
        ("Where did they go last week?", False),
    ]

    router = QueryRouter()
    passed = 0
    failed = 0

    for query, expected_open in test_queries:
        # Test external_retriever detection
        is_open, confidence = is_open_domain_query(query)

        # Test query_router detection
        analysis = router.analyze(query)

        # Test tesseract detection
        type_scores = detect_query_type(query)
        tesseract_open_score = type_scores.get(QueryType.OPEN, 0)

        status = "[PASS]" if is_open == expected_open else "[FAIL]"
        if is_open == expected_open:
            passed += 1
        else:
            failed += 1

        print(f"{status} Query: {query[:50]:<50}")
        print(f"    Expected: {'open-domain' if expected_open else 'session-specific'}")
        print(f"    External detector: open={is_open}, confidence={confidence:.2f}")
        print(f"    Router: is_open={analysis.is_open_domain}, confidence={analysis.open_domain_confidence:.2f}")
        print(f"    Tesseract OPEN score: {tesseract_open_score:.2f}")
        print()

    print(f"Results: {passed}/{passed+failed} passed")
    return failed == 0


async def test_wikipedia_retrieval():
    """Test Wikipedia API retrieval."""
    print("\n" + "="*60)
    print("Testing Wikipedia Retrieval")
    print("="*60)

    try:
        import aiohttp
    except ImportError:
        print("[ERROR] aiohttp not installed. Run: pip install aiohttp")
        return False

    config = WikipediaConfig(
        search_limit=3,
        extract_chars=500,
        timeout_seconds=5.0,
    )
    retriever = WikipediaRetriever(config)

    test_queries = [
        "What causes rain?",
        "photosynthesis process",
        "capital of France",
    ]

    all_passed = True

    for query in test_queries:
        print(f"\nQuery: {query}")
        print("-" * 40)

        try:
            results = await retriever.retrieve(
                query=query,
                limit=3,
                timeout_ms=5000,
            )

            if results:
                print(f"[OK] Retrieved {len(results)} results")
                for i, r in enumerate(results, 1):
                    print(f"  {i}. {r.title} (relevance: {r.relevance_score:.2f})")
                    print(f"     {r.content[:100]}...")
            else:
                print("[FAIL] No results returned")
                all_passed = False

        except Exception as e:
            print(f"[ERROR] {e}")
            all_passed = False

    return all_passed


async def test_rare_term_detection():
    """Test rare term extraction for keyword retrieval."""
    print("\n" + "="*60)
    print("Testing Rare Term Detection")
    print("="*60)

    router = QueryRouter()

    test_queries = [
        "What is the role of mitochondria in cellular respiration?",
        "How does electromagnetic radiation work?",
        "Explain the photoelectric effect",
        "What is the capital?",  # No rare terms expected
    ]

    for query in test_queries:
        analysis = router.analyze(query)
        print(f"\nQuery: {query}")
        print(f"  Rare terms: {analysis.rare_terms}")
        print(f"  Strategy: {analysis.strategy}")

    return True


def test_tracer():
    """Test the open-domain tracer."""
    print("\n" + "="*60)
    print("Testing Open-Domain Tracer")
    print("="*60)

    from NeuralGraph.external_retriever import OpenDomainTrace, OpenDomainTracer

    tracer = OpenDomainTracer()

    # Simulate some traces
    traces = [
        OpenDomainTrace(
            query_text="What is photosynthesis?",
            is_open_domain=True,
            open_domain_confidence=0.8,
            session_candidates_count=5,
            session_hits_at_threshold=1,
            external_retriever_called=True,
            external_results_count=3,
        ),
        OpenDomainTrace(
            query_text="What did she say?",
            is_open_domain=False,
            open_domain_confidence=0.2,
            session_candidates_count=10,
            session_hits_at_threshold=5,
        ),
    ]

    for trace in traces:
        tracer.record_trace(trace)

    stats = tracer.get_stats()
    print(f"Total queries: {stats['total_queries']}")
    print(f"Open-domain queries: {stats['open_domain_queries']}")
    print(f"Zero session hits: {stats['zero_session_hits']}")
    print(f"External called: {stats['external_called']}")

    return True


async def main():
    print("="*60)
    print("OPEN-DOMAIN RECOVERY PLAN - IMPLEMENTATION TEST")
    print("="*60)

    all_passed = True

    # Test 1: Open-domain detection
    if not test_open_domain_detection():
        all_passed = False

    # Test 2: Rare term detection
    if not await test_rare_term_detection():
        all_passed = False

    # Test 3: Tracer
    if not test_tracer():
        all_passed = False

    # Test 4: Wikipedia retrieval (requires network)
    print("\nAttempting Wikipedia API test (requires internet)...")
    try:
        if not await test_wikipedia_retrieval():
            print("[WARN] Wikipedia test had issues (may be network-related)")
    except Exception as e:
        print(f"[WARN] Wikipedia test skipped: {e}")

    print("\n" + "="*60)
    if all_passed:
        print("[SUCCESS] All core tests passed!")
    else:
        print("[FAILED] Some tests failed")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
