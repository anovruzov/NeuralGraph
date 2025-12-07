"""
LoCoMo10 Full Pipeline Cognitive Benchmark

Tests the complete MemMachine cognitive architecture with all 6 phases:
1. Privacy Sanitization - PII detection & redaction
2. Emotional Encoding - Plutchik emotions & mood tracking
3. Mid-term Memory - Heat-based consolidation
4. Knowledge Graph - Entity extraction & reasoning
5. Granularity Router - Entropy-based adaptive retrieval
6. Domain Classifier - Semantic memory sharding

This benchmark ingests conversations through the ACTUAL EpisodicMemory pipeline,
unlike the parallel benchmark which uses simple embedding-based retrieval.

Usage:
    python locomo10_full_pipeline_benchmark.py [--max-conversations N] [--run-baseline]

Options:
    --max-conversations N    Number of conversations to process (default: 10)
    --run-baseline          Also run parallel benchmark for comparison
    --config CONFIG_FILE    Path to custom configuration JSON
"""

import asyncio
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from cognitive_pipeline import (
    CognitivePipelineConfig,
    CognitiveEpisodicMemory,
    MetricsCollector,
    ResultAnalyzer,
)
from cognitive_pipeline.pipeline_config import EmotionDetectorType

# ============================================================================
# CONSTANTS
# ============================================================================

BENCHMARK_VERSION = "1.0.0"
BENCHMARK_NAME = "FULL_PIPELINE"

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
JUDGE_MODEL = "deepseek-r1:14b"

CONCURRENT_QUESTIONS = 4
CONTEXT_LIMIT = 12000
CORRECT_THRESHOLD = 8
PARTIAL_THRESHOLD = 5

CATEGORIES = {
    1: "single_hop",
    2: "temporal",
    3: "open_domain",
    4: "multi_hop",
    5: "adversarial",
}

# Category-specific prompts
PROMPTS = {
    "standard": """Based on the conversation memories below, answer the question.

{context}

QUESTION: {question}

INSTRUCTIONS:
- Answer based ONLY on information in the context above
- If the answer is not present, say "The answer is not in the provided context"
- Be specific and concise

ANSWER:""",

    "temporal": """Based on the conversation memories below, answer WHEN something happened.

{context}

QUESTION: {question}

=== DATE CALCULATION STEPS ===
1. Find the SESSION DATE in format "DATE: [time] on [day] [month], [year]"
2. Find RELATIVE TIME words in the text: "yesterday", "last week", "last year"
3. CALCULATE the absolute date

ANSWER (specific date only, NO relative terms):""",

    "multi_hop": """Based on the conversation memories below, answer the question that requires connecting multiple pieces of information.

{context}

QUESTION: {question}

INSTRUCTIONS:
- This question requires finding and connecting 2+ pieces of information
- Look across multiple sessions if needed

ANSWER:""",

    "adversarial": """Based on the conversation memories below, carefully answer the question.

{context}

QUESTION: {question}

CRITICAL VERIFICATION:
1. Verify that entities in the question MATCH the context
2. If the question attributes something to the WRONG PERSON, correct it
3. Only answer as asked if all entities are correct

ANSWER (correct any errors first):""",

    "open_domain": """Based on the conversation memories below, answer this inference question.

{context}

QUESTION: {question}

=== INFERENCE STEPS ===
This question requires reading between the lines. Follow these steps:

1. **Extract Evidence**: What explicit facts relate to this question?
2. **Find Patterns**: What themes, preferences, or goals appear across conversations?
3. **Synthesize**: What reasonable conclusion can you draw?

ANSWER (state your inference with brief reasoning):""",
}

JUDGE_PROMPT = """Score this answer 0-10.

QUESTION: {question}
REFERENCE: {gold_answer}
GENERATED: {generated_answer}

SCORING:
- 8-10: Correct, matches reference meaning
- 5-7: Partially correct
- 0-4: Wrong or missing key info

Output ONLY JSON: {{"total": 0-10}}"""


# ============================================================================
# DATA EXTRACTION
# ============================================================================

def load_locomo10_dataset(data_path: Path | None = None) -> list[dict]:
    """Load the LoCoMo10 dataset.

    Args:
        data_path: Path to dataset. Uses default if None.

    Returns:
        List of conversation items with qa pairs.
    """
    if data_path is None:
        data_path = Path(__file__).parent / "locomo" / "locomo10.json"

    with open(data_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    return dataset


def extract_messages_from_conversation(item: dict) -> list[dict]:
    """Extract messages with timestamps from a conversation item.

    Args:
        item: Conversation item from dataset.

    Returns:
        List of message dictionaries.
    """
    conversation = item.get("conversation", item)
    messages = []
    session_idx = 1

    while True:
        session_key = f"session_{session_idx}"
        datetime_key = f"session_{session_idx}_date_time"

        if session_key not in conversation:
            break

        session_time_str = conversation.get(datetime_key, "Unknown")
        session_messages = conversation[session_key]

        # Parse session datetime
        session_datetime = None
        try:
            # Try common formats
            for fmt in [
                "%I:%M %p on %A %B %d, %Y",
                "%H:%M on %A %B %d, %Y",
                "%B %d, %Y",
            ]:
                try:
                    session_datetime = datetime.strptime(session_time_str, fmt)
                    session_datetime = session_datetime.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
        except Exception:
            pass

        for msg_idx, msg in enumerate(session_messages):
            if isinstance(msg, dict) and "text" in msg:
                messages.append({
                    "content": msg.get("text", ""),
                    "speaker": msg.get("speaker", "Unknown"),
                    "session": session_idx,
                    "session_time": session_time_str,
                    "timestamp": session_datetime,
                    "msg_idx": msg_idx,
                    "dia_id": msg.get("dia_id", f"D{session_idx}:{msg_idx}"),
                })

        session_idx += 1

    return messages


# ============================================================================
# LLM FUNCTIONS
# ============================================================================

async def generate_answer_async(
    session: aiohttp.ClientSession,
    prompt: str,
    model: str = OLLAMA_MODEL,
    timeout: int = 60,
) -> str:
    """Generate answer using Ollama.

    Args:
        session: HTTP session.
        prompt: Prompt text.
        model: Model name.
        timeout: Request timeout.

    Returns:
        Generated answer text.
    """
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as response:
            result = await response.json()
            return result["message"]["content"].strip()
    except Exception as e:
        return f"Error: {str(e)}"


async def judge_answer_async(
    session: aiohttp.ClientSession,
    question: str,
    gold_answer: str,
    generated_answer: str,
) -> dict:
    """Judge an answer using the judge model.

    Args:
        session: HTTP session.
        question: Original question.
        gold_answer: Reference answer.
        generated_answer: Generated answer to judge.

    Returns:
        Dict with 'total' score.
    """
    # Quick match check
    gold_lower = gold_answer.lower().strip()
    gen_lower = generated_answer.lower().strip()

    if gold_lower in gen_lower or gen_lower in gold_lower:
        return {"total": 10}

    prompt = JUDGE_PROMPT.format(
        question=question,
        gold_answer=gold_answer,
        generated_answer=generated_answer,
    )

    try:
        result_text = await generate_answer_async(
            session, prompt, model=JUDGE_MODEL, timeout=120
        )

        # Parse JSON from response
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0]
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0]

        return json.loads(result_text.strip())
    except Exception:
        return {"total": 0}


def get_prompt_for_category(category: str) -> str:
    """Get the appropriate prompt template for a category.

    Args:
        category: Question category name.

    Returns:
        Prompt template string.
    """
    return PROMPTS.get(category, PROMPTS["standard"])


# ============================================================================
# QUESTION PROCESSING
# ============================================================================

async def process_question(
    http_session: aiohttp.ClientSession,
    q_idx: int,
    qa: dict,
    memory: CognitiveEpisodicMemory,
) -> dict:
    """Process a single question through the cognitive pipeline.

    Args:
        http_session: HTTP session for LLM calls.
        q_idx: Question index.
        qa: QA item with question, answer, category.
        memory: Cognitive episodic memory instance.

    Returns:
        Result dictionary with scores and metadata.
    """
    question = qa.get("question", "")
    gold_answer = str(qa.get("answer", ""))
    category_id = qa.get("category", 0)
    category_name = CATEGORIES.get(category_id, "unknown")

    start_time = time.time()

    # Query memory through cognitive pipeline
    query_result = await memory.query(question, limit=30)

    # Format context (truncate if needed)
    context = query_result.context
    if len(context) > CONTEXT_LIMIT:
        context = context[:CONTEXT_LIMIT] + "\n... [truncated]"

    # Generate answer with category-specific prompt
    prompt_template = get_prompt_for_category(category_name)
    prompt = prompt_template.format(context=context, question=question)
    generated = await generate_answer_async(http_session, prompt)

    # Judge the answer
    judgment = await judge_answer_async(http_session, question, gold_answer, generated)

    gen_time = time.time() - start_time
    total_score = judgment.get("total", 0)

    if total_score >= CORRECT_THRESHOLD:
        status = "CORRECT"
    elif total_score >= PARTIAL_THRESHOLD:
        status = "PARTIAL"
    else:
        status = "WRONG"

    print(
        f"  [{q_idx + 1:3d}] {category_name:12s}: {question[:40]}... "
        f"Score: {total_score:2d}/10 [{status:7s}] ({gen_time:.1f}s)",
        flush=True,
    )

    return {
        "question_id": q_idx,
        "category": category_id,
        "category_name": category_name,
        "question": question,
        "gold_answer": gold_answer,
        "generated_answer": generated,
        "score": total_score,
        "correct": total_score >= CORRECT_THRESHOLD,
        "time_seconds": round(gen_time, 2),
        "query_metrics": {
            "stm_results": query_result.metrics.stm_results,
            "ltm_results": query_result.metrics.ltm_results,
            "entropy_score": query_result.metrics.entropy_score,
            "granularity_level": query_result.metrics.granularity_level,
            "kg_paths_found": query_result.metrics.kg_paths_found,
        },
        "kg_reasoning": query_result.kg_reasoning,
    }


# ============================================================================
# MAIN BENCHMARK
# ============================================================================

async def run_full_pipeline_benchmark(
    max_conversations: int = 10,
    config: CognitivePipelineConfig | None = None,
    run_baseline: bool = False,
) -> dict:
    """Run the full pipeline cognitive benchmark.

    Args:
        max_conversations: Maximum conversations to process.
        config: Pipeline configuration.
        run_baseline: Whether to run parallel benchmark first.

    Returns:
        Benchmark results dictionary.
    """
    # Load dataset
    dataset = load_locomo10_dataset()

    print("=" * 70)
    print(f"LoCoMo10 Full Pipeline Cognitive Benchmark v{BENCHMARK_VERSION}")
    print("=" * 70)
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Judge: {JUDGE_MODEL}")
    print(f"Conversations: {min(max_conversations, len(dataset))}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 70)

    # Initialize configuration
    if config is None:
        config = CognitivePipelineConfig()
        config.emotional.detector_type = EmotionDetectorType.HYBRID

    print(str(config))
    print("=" * 70)

    all_results = []
    total_episodes = 0

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, item in enumerate(dataset[:max_conversations]):
            conversation = item.get("conversation", item)
            qa_list = item.get("qa", [])

            speaker_a = conversation.get("speaker_a", "Person A")
            speaker_b = conversation.get("speaker_b", "Person B")

            print(f"\n{'='*60}")
            print(f"Conversation {conv_idx + 1}: {speaker_a} & {speaker_b}")
            print(f"Questions: {len(qa_list)}")
            print("=" * 60)

            # Create session-specific memory
            session_key = f"benchmark_conv_{conv_idx}"
            memory = CognitiveEpisodicMemory(session_key=session_key, config=config)

            # Extract and ingest messages
            messages = extract_messages_from_conversation(item)
            print(f"Ingesting {len(messages)} messages through cognitive pipeline...")

            ingest_start = time.time()
            for msg in messages:
                result = await memory.ingest_episode(
                    content=msg["content"],
                    speaker=msg["speaker"],
                    timestamp=msg.get("timestamp"),
                    metadata={
                        "session": msg["session"],
                        "session_time": msg["session_time"],
                        "dia_id": msg["dia_id"],
                    },
                )
                if not result.success:
                    print(f"    Warning: Episode blocked - {result.error}")

            ingest_time = time.time() - ingest_start
            total_episodes += len(messages)

            # Get metrics summary
            metrics = memory.get_metrics()
            summary = metrics.get_summary()
            print(f"  Ingestion complete in {ingest_time:.1f}s")
            print(f"    PII detected: {summary['privacy']['pii_detection_rate']:.1f}%")
            print(f"    Top emotions: {list(summary['emotional']['dominant_emotions'].keys())[:3]}")
            print(f"    Entities extracted: {summary['knowledge_graph']['entities_extracted']}")

            # Process questions
            print(f"\nProcessing {len(qa_list)} questions...")

            semaphore = asyncio.Semaphore(CONCURRENT_QUESTIONS)

            async def process_with_semaphore(q_idx: int, qa: dict) -> dict:
                async with semaphore:
                    return await process_question(http_session, q_idx, qa, memory)

            tasks = [
                process_with_semaphore(q_idx, qa)
                for q_idx, qa in enumerate(qa_list)
            ]
            results = await asyncio.gather(*tasks)

            for result in results:
                result["conversation_id"] = conv_idx
                all_results.append(result)

            # Get pipeline statistics
            pipeline_stats = await memory.get_pipeline_statistics()

            # Close memory
            await memory.close()

    # Analyze results
    print("\n" + "=" * 70)
    print("ANALYZING RESULTS")
    print("=" * 70)

    analyzer = ResultAnalyzer()
    analysis = analyzer.analyze_results(
        results=all_results,
        pipeline_stats=pipeline_stats,
        benchmark_name=BENCHMARK_NAME,
        model=OLLAMA_MODEL,
    )

    # Try to compare with baseline
    baseline_path = analyzer.load_baseline()
    if baseline_path:
        print(f"Comparing with baseline: {baseline_path.name}")
        analysis = analyzer.compare_with_baseline(analysis, baseline_path)

    # Calculate module impact
    analysis = analyzer.calculate_module_impact(analysis)

    # Print report
    report = analyzer.format_report(analysis)
    print(report)

    # Save results
    output_path = analyzer.save_results(analysis, all_results)

    return {
        "benchmark": BENCHMARK_NAME,
        "version": BENCHMARK_VERSION,
        "analysis": analysis,
        "results": all_results,
        "output_path": str(output_path),
    }


# ============================================================================
# CLI
# ============================================================================

def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="LoCoMo10 Full Pipeline Cognitive Benchmark"
    )
    parser.add_argument(
        "--max-conversations",
        type=int,
        default=10,
        help="Maximum conversations to process (default: 10)",
    )
    parser.add_argument(
        "--run-baseline",
        action="store_true",
        help="Also run parallel benchmark for comparison",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to custom configuration JSON",
    )
    parser.add_argument(
        "--disable-privacy",
        action="store_true",
        help="Disable privacy sanitization",
    )
    parser.add_argument(
        "--disable-emotions",
        action="store_true",
        help="Disable emotional encoding",
    )
    parser.add_argument(
        "--disable-kg",
        action="store_true",
        help="Disable knowledge graph",
    )
    parser.add_argument(
        "--disable-granularity",
        action="store_true",
        help="Disable granularity routing",
    )
    parser.add_argument(
        "--disable-domain",
        action="store_true",
        help="Disable domain classification",
    )

    args = parser.parse_args()

    # Load or create config
    config = CognitivePipelineConfig()

    if args.config:
        with open(args.config, "r") as f:
            config_data = json.load(f)
        # Apply config values (simplified)
        if "privacy" in config_data:
            config.privacy.enabled = config_data["privacy"].get("enabled", True)
        if "emotional" in config_data:
            config.emotional.enabled = config_data["emotional"].get("enabled", True)
        if "knowledge_graph" in config_data:
            config.knowledge_graph.enabled = config_data["knowledge_graph"].get("enabled", True)

    # Apply CLI overrides
    if args.disable_privacy:
        config.privacy.enabled = False
    if args.disable_emotions:
        config.emotional.enabled = False
    if args.disable_kg:
        config.knowledge_graph.enabled = False
    if args.disable_granularity:
        config.granularity.enabled = False
    if args.disable_domain:
        config.domain.enabled = False

    # Run benchmark
    result = asyncio.run(
        run_full_pipeline_benchmark(
            max_conversations=args.max_conversations,
            config=config,
            run_baseline=args.run_baseline,
        )
    )

    print(f"\nBenchmark complete! Results saved to: {result['output_path']}")


if __name__ == "__main__":
    main()
