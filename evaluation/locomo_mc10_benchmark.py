"""
LoCoMo-MC10 Benchmark Script
Tests AI Memory System evaluation using Qwen via Ollama

This script implements the Multi-Criteria 10-Point evaluation framework:
1. Retrieval Precision (1-10)
2. Faithfulness & Hallucination Check (1-10)
3. Answer Relevance (1-10)
"""

import json
import csv
import time
from datetime import datetime
from pathlib import Path
from openai import OpenAI

# Ollama Configuration
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_MODEL = "qwen2.5:7b-instruct"

client = OpenAI(
    base_url=OLLAMA_BASE_URL,
    api_key="ollama",
)

MC10_JUDGE_PROMPT = """You are the **LoCoMo-MC10 Evaluation Judge**. Your task is to audit the performance of an AI Memory System. You will be provided with a User Query, the Raw Context retrieved by the system, and the System's Final Answer.

You must evaluate the system on three strict dimensions and calculate a weighted final score.

### INPUT DATA:
**User Query:**
"{user_query}"

**Retrieved Context (from Vector/SQL Memory):**
"{retrieved_context}"

**System Final Answer:**
"{system_answer}"

---

### EVALUATION CRITERIA (LoCoMo-MC10):

**1. Retrieval Precision (Score 1-10):**
* **10:** The retrieved context contains *all* necessary facts to answer the query accurately with no irrelevant noise that confuses the model.
* **5:** The retrieved context contains the partial answer but misses key details or includes significant irrelevant data.
* **1:** The retrieved context is completely irrelevant to the query.

**2. Faithfulness & Hallucination Check (Score 1-10):**
* **10:** The Final Answer is derived *exclusively* from the Retrieved Context. No outside knowledge was hallucinated.
* **5:** The answer is mostly grounded but includes minor assumptions not present in the source text.
* **1:** The answer contradicts the context or hallucinates facts entirely.

**3. Answer Relevance (Score 1-10):**
* **10:** The answer directly addresses the User Query using the context provided.
* **5:** The answer is vague or dances around the specific question asked.
* **1:** The answer ignores the user query.

---

### OUTPUT FORMAT:
You must output a strictly valid JSON object. Do not output markdown code blocks. Do not output conversational text.

{{"retrieval_score": <int>, "faithfulness_score": <int>, "relevance_score": <int>, "average_mc10_score": <float>, "reasoning": "<concise explanation of the deduction>"}}
"""

# Sample test cases for the 4 categories
TEST_CASES = [
    # Category 1: Direct Fact Retrieval (3 questions)
    {
        "category": "direct_fact",
        "query": "What was the API key format mentioned in the documentation?",
        "context": "The API documentation states that API keys must follow the format: 'sk-xxxx-yyyy-zzzz' where x is alphanumeric, y is numeric, and z is a checksum. Keys expire after 90 days.",
        "answer": "The API key format is 'sk-xxxx-yyyy-zzzz' where x is alphanumeric, y is numeric, and z is a checksum.",
    },
    {
        "category": "direct_fact",
        "query": "What is Sarah's favorite restaurant?",
        "context": "In our conversation on March 15, Sarah mentioned she loves Italian food and her favorite restaurant is 'Bella Italia' on Main Street. She goes there every Friday.",
        "answer": "Sarah's favorite restaurant is Bella Italia on Main Street.",
    },
    {
        "category": "direct_fact",
        "query": "What programming language does the project use?",
        "context": "Project README: This is a Python 3.12+ project using FastAPI for the backend and React for the frontend. Database: PostgreSQL.",
        "answer": "The project uses Python 3.12+ with FastAPI for backend and React for frontend.",
    },

    # Category 2: Multi-Hop Reasoning (3 questions)
    {
        "category": "multi_hop",
        "query": "Based on the meeting notes and the follow-up email, what is the final project deadline?",
        "context": "Meeting notes (Tuesday): Discussed timeline, initial deadline set for end of Q1. Email from Friday: 'After reviewing resources, we need to push the deadline to April 15th to ensure quality.'",
        "answer": "The final project deadline is April 15th, as confirmed in the Friday follow-up email after the initial Q1 deadline was revised.",
    },
    {
        "category": "multi_hop",
        "query": "Who should I contact about the budget issue mentioned by John?",
        "context": "John's message: 'The marketing budget is overspent, talk to finance.' Company directory: Finance department head is Maria Chen (maria@company.com). Marketing lead is Tom Smith.",
        "answer": "You should contact Maria Chen from the finance department at maria@company.com about the budget issue.",
    },
    {
        "category": "multi_hop",
        "query": "What hotel did we book for the conference that Mike recommended?",
        "context": "Mike's recommendation (Jan 5): 'Stay at the Grand Hotel, it's closest to the venue.' Booking confirmation (Jan 10): 'Your reservation at Grand Hotel is confirmed for March 20-22.'",
        "answer": "We booked the Grand Hotel for March 20-22, which was recommended by Mike for being closest to the conference venue.",
    },

    # Category 3: Negative Constraints (2 questions) - System should say "I don't know"
    {
        "category": "negative",
        "query": "What is the price of the X-200 model?",
        "context": "Product catalog: X-100 costs $299, X-150 costs $449, X-300 costs $699. All models come with 1-year warranty.",
        "answer": "I don't have information about the X-200 model pricing. The catalog only lists X-100 ($299), X-150 ($449), and X-300 ($699).",
    },
    {
        "category": "negative",
        "query": "When is Lisa's birthday?",
        "context": "Team birthdays: John - March 15, Sarah - July 22, Mike - December 3. Lisa joined the team last month.",
        "answer": "I don't know Lisa's birthday. The available records only show birthdays for John (March 15), Sarah (July 22), and Mike (December 3).",
    },

    # Category 4: Temporal Sequencing (2 questions)
    {
        "category": "temporal",
        "query": "What was the last update made to the project architecture?",
        "context": "Architecture log: [2024-01-10] Added caching layer. [2024-02-15] Migrated to microservices. [2024-03-20] Added rate limiting. [2024-04-01] Implemented OAuth2.",
        "answer": "The last update to the project architecture was implementing OAuth2 on April 1, 2024.",
    },
    {
        "category": "temporal",
        "query": "What was the first feature request from the client?",
        "context": "Client requests timeline: [Week 1] Dark mode support requested. [Week 2] Export to PDF needed. [Week 3] Mobile app version. [Week 4] API integration.",
        "answer": "The first feature request from the client was dark mode support in Week 1.",
    },
]


def evaluate_with_mc10(query: str, context: str, answer: str) -> dict:
    """Evaluate a single test case using the MC10 framework."""
    prompt = MC10_JUDGE_PROMPT.format(
        user_query=query,
        retrieved_context=context,
        system_answer=answer,
    )

    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        content = response.choices[0].message.content.strip()

        # Try to parse JSON
        # Handle potential markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]

        result = json.loads(content)
        return result
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")
        print(f"Raw response: {content}")
        return {
            "retrieval_score": 0,
            "faithfulness_score": 0,
            "relevance_score": 0,
            "average_mc10_score": 0.0,
            "reasoning": f"Parse error: {str(e)}",
        }
    except Exception as e:
        print(f"Error: {e}")
        return {
            "retrieval_score": 0,
            "faithfulness_score": 0,
            "relevance_score": 0,
            "average_mc10_score": 0.0,
            "reasoning": f"Error: {str(e)}",
        }


def run_benchmark():
    """Run the complete MC10 benchmark."""
    print("=" * 60)
    print("LoCoMo-MC10 Benchmark")
    print(f"Model: {OLLAMA_MODEL}")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)

    results = []
    category_scores = {
        "direct_fact": [],
        "multi_hop": [],
        "negative": [],
        "temporal": [],
    }

    for i, test in enumerate(TEST_CASES, 1):
        print(f"\n[{i}/{len(TEST_CASES)}] Category: {test['category']}")
        print(f"Query: {test['query'][:50]}...")

        start_time = time.time()
        eval_result = evaluate_with_mc10(
            test["query"],
            test["context"],
            test["answer"],
        )
        elapsed = time.time() - start_time

        result = {
            "test_id": i,
            "category": test["category"],
            "query": test["query"],
            "retrieval_score": eval_result.get("retrieval_score", 0),
            "faithfulness_score": eval_result.get("faithfulness_score", 0),
            "relevance_score": eval_result.get("relevance_score", 0),
            "average_mc10_score": eval_result.get("average_mc10_score", 0.0),
            "reasoning": eval_result.get("reasoning", ""),
            "time_seconds": round(elapsed, 2),
        }
        results.append(result)
        category_scores[test["category"]].append(result["average_mc10_score"])

        print(f"  Retrieval: {result['retrieval_score']}/10")
        print(f"  Faithfulness: {result['faithfulness_score']}/10")
        print(f"  Relevance: {result['relevance_score']}/10")
        print(f"  Average MC10: {result['average_mc10_score']:.2f}")
        print(f"  Time: {elapsed:.2f}s")

    # Save results to CSV
    output_dir = Path(__file__).parent / "results"
    output_dir.mkdir(exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"mc10_benchmark_{timestamp}.csv"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    # Print summary
    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)

    for category, scores in category_scores.items():
        if scores:
            avg = sum(scores) / len(scores)
            print(f"{category:15} | Avg MC10: {avg:.2f} | Count: {len(scores)}")

    all_scores = [r["average_mc10_score"] for r in results]
    overall_avg = sum(all_scores) / len(all_scores) if all_scores else 0

    print("-" * 60)
    print(f"{'OVERALL':15} | Avg MC10: {overall_avg:.2f} | Total: {len(results)}")
    print(f"\nResults saved to: {csv_path}")

    # Save JSON results too
    json_path = output_dir / f"mc10_benchmark_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "model": OLLAMA_MODEL,
            "timestamp": timestamp,
            "overall_average": overall_avg,
            "category_averages": {k: sum(v)/len(v) if v else 0 for k, v in category_scores.items()},
            "results": results,
        }, f, indent=2)

    print(f"JSON saved to: {json_path}")

    return results


if __name__ == "__main__":
    run_benchmark()
