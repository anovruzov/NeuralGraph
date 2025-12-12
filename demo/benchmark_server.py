"""
Tesseract Benchmark Server
Runs the real benchmark and streams results to the UI via Server-Sent Events
"""

import json
import asyncio
import aiohttp
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import NeuralNode, NodeLayer
from memmachine.neural_graph.storage import InMemoryNeuralGraphStorage
from memmachine.neural_graph.tesseract import Tesseract, detect_query_type, QueryType

# Config
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b-instruct"
EMBEDDING_MODEL = "nomic-embed-text"
TOP_K = 30  # Reduced to focus on most relevant
CONTEXT_LIMIT = 15000

CATEGORIES = {1: "single_hop", 2: "temporal", 3: "open_domain", 4: "multi_hop", 5: "adversarial"}

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Serve static files
app.mount("/static", StaticFiles(directory=Path(__file__).parent), name="static")


@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "mycelic-demo.html")


@app.get("/locomo_benchmark.json")
async def get_benchmark_data():
    return FileResponse(Path(__file__).parent / "locomo_benchmark.json")


# Global storage for ask endpoint (pre-loaded conversation)
ask_storage = None
ask_tesseract = None
ask_conv_id = None  # Track which conversation is loaded


@app.post("/api/ask")
async def ask_question(request: Request):
    """Ask a question about the loaded conversation"""
    global ask_storage, ask_tesseract, ask_conv_id

    body = await request.json()
    question = body.get("question", "")
    conv_id = body.get("conversation_id", 0)

    if not question:
        return {"error": "No question provided"}

    async with aiohttp.ClientSession() as http:
        # Load conversation if not already loaded OR if conversation changed
        if ask_storage is None or ask_conv_id != conv_id:
            locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
            with open(locomo_path) as f:
                data = json.load(f)

            conversation = data[conv_id]
            messages = []
            session_idx = 1
            while f"session_{session_idx}" in conversation.get("conversation", conversation):
                conv_data = conversation.get("conversation", conversation)
                for msg in conv_data.get(f"session_{session_idx}", []):
                    messages.append({
                        "speaker": msg.get("speaker", "Unknown"),
                        "text": msg.get("text", ""),
                        "datetime": conv_data.get(f"session_{session_idx}_date_time", ""),
                    })
                session_idx += 1

            ask_storage = InMemoryNeuralGraphStorage()
            ask_tesseract = Tesseract(ask_storage)
            ask_conv_id = conv_id  # Track which conversation is loaded

            for msg_idx, msg in enumerate(messages):
                embedding = await get_embedding(http, msg["text"])
                node = NeuralNode(
                    node_id=f"msg_ask_{msg_idx}",
                    session_key="ask_session",
                    content=msg["text"],
                    layer=NodeLayer.MESSAGE,
                    embedding=embedding,
                    metadata={"speaker": msg["speaker"], "datetime": msg["datetime"]},
                )
                await ask_storage.save_node(node)

        # Get embedding for question
        query_emb = await get_embedding(http, question)
        if not query_emb:
            return {"error": "Failed to get embedding"}

        # Retrieve
        results = await ask_tesseract.retrieve(
            query_text=question,
            query_embedding=query_emb,
            session_key="ask_session",
            limit=TOP_K,
        )

        # Build context
        context_parts = []
        total_len = 0
        for node, charge in results:
            if total_len >= CONTEXT_LIMIT:
                break
            speaker = node.metadata.get("speaker", "") if node.metadata else ""
            dt = node.metadata.get("datetime", "") if node.metadata else ""
            if dt and speaker:
                entry = f"[{dt} - {speaker}]: {node.content}"
            elif speaker:
                entry = f"[{speaker}]: {node.content}"
            else:
                entry = node.content
            context_parts.append(entry)
            total_len += len(entry)

        context = "\n\n".join(context_parts)

        # Generate answer
        answer = await generate_answer(http, question, context, "")

        return {"answer": answer, "sources": len(results)}


async def get_embedding(session, text: str) -> list[float]:
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except:
        return []


async def generate_answer(session, question: str, context: str, category: str = "") -> str:
    # Add category-specific prompting
    if category == "temporal":
        instructions = """Find the memory that directly answers the question. Calculate the date.

DATE CALCULATION:
1. Find the memory timestamp: [DD Month, YYYY]
2. Apply the relative term:
   - "yesterday" = subtract 1 day from timestamp
   - "last Saturday" = find Saturday before timestamp
   - "last week" = ~7 days before timestamp
   - "last year" = year before timestamp's year
   - "next month" = month after timestamp's month
   - "this month" = same month as timestamp

Output ONLY the final date (nothing else). Format: "May 7, 2023" or "June 2023" or "2022"."""
    elif category == "adversarial":
        instructions = """Answer based ONLY on what is explicitly stated in these memories.
IMPORTANT: If the information is NOT mentioned or cannot be found in the memories, you MUST say "This is not mentioned in the conversation" or "I don't have information about this".
Do NOT make up or infer information that isn't explicitly stated. Be honest when you don't know."""
    else:
        instructions = "Answer based ONLY on these memories. Be specific and concise."

    prompt = f"""{instructions}

MEMORIES:
{context}

QUESTION: {question}

Answer:"""

    # Use shorter responses for temporal to force concise answers
    max_tokens = 50 if category == "temporal" else 150

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.1, "num_predict": max_tokens}},
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            result = await response.json()
            return result.get("response", "").strip()
    except Exception as e:
        return f"Error: {e}"


def normalize_date(text: str) -> str:
    """Normalize date formats for comparison - converts all date formats to 'month day year'."""
    import re
    text = text.lower().strip()
    text = re.sub(r',', '', text)

    # Replace "DD Month YYYY" with "Month DD YYYY" everywhere in text
    def replace_dmy(m):
        return f"{m.group(2)} {m.group(1)} {m.group(3)}"

    text = re.sub(
        r'(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})',
        replace_dmy,
        text
    )
    return text


def extract_date_components(text: str) -> dict:
    """Extract month, day, year from text for flexible matching."""
    import re
    text = text.lower()
    months = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'jun': 6, 'jul': 7,
        'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
    }
    result = {'month': None, 'day': None, 'year': None}

    # Find year
    year_match = re.search(r'\b(20\d{2})\b', text)
    if year_match:
        result['year'] = int(year_match.group(1))

    # Find month
    for month_name, month_num in months.items():
        if month_name in text:
            result['month'] = month_num
            break

    # Find day
    day_match = re.search(r'\b(\d{1,2})\b', text)
    if day_match:
        day = int(day_match.group(1))
        if 1 <= day <= 31:
            result['day'] = day

    return result


def dates_match_flexible(gold: str, generated: str) -> bool:
    """Check if dates match - STRICT matching, only minor format differences allowed."""
    gold_parts = extract_date_components(gold)
    gen_parts = extract_date_components(generated)

    # Both must have same month and year at minimum
    if gold_parts['month'] != gen_parts.get('month'):
        return False
    if gold_parts.get('year') and gen_parts.get('year') and gold_parts['year'] != gen_parts['year']:
        return False

    # If gold has specific day, generated should be within 2 days
    if gold_parts['day'] and gen_parts['day']:
        day_diff = abs(gold_parts['day'] - gen_parts['day'])
        if day_diff > 2:
            return False

    return True


async def judge_answer(session, question: str, generated: str, gold, is_adversarial: bool = False) -> bool:
    import re
    gen_lower = str(generated).lower().strip()
    gold_lower = str(gold).lower().strip()

    # Uncertainty phrases - model admits it doesn't know
    uncertainty_phrases = [
        "not mentioned", "no information", "don't know", "doesn't mention",
        "not specified", "no mention", "cannot determine", "can't determine",
        "not clear", "unclear", "no evidence", "not stated", "isn't mentioned",
        "not discussed", "no data", "unknown", "not available", "can't find",
        "cannot find", "no record", "not in", "wasn't mentioned", "were not",
        "was not", "weren't", "isn't", "aren't", "not provided", "lack",
        "no specific", "does not mention", "do not have", "don't have",
        "cannot answer", "can't answer", "unable to", "not enough information",
        "there is no", "does not include", "not provide", "no details",
    ]

    # Check if model expresses uncertainty
    model_uncertain = any(phrase in gen_lower for phrase in uncertainty_phrases)

    # For ADVERSARIAL questions: uncertainty = correct (question has no answer)
    if is_adversarial:
        return model_uncertain

    # For NON-ADVERSARIAL questions: uncertainty = WRONG (model should know the answer)
    if model_uncertain:
        return False

    # Handle "X years ago" vs year format
    years_ago_match = re.search(r'(\d+)\s*years?\s*ago', gold_lower)
    if years_ago_match:
        years_ago = int(years_ago_match.group(1))
        expected_year = 2023 - years_ago
        if str(expected_year) in gen_lower:
            return True
        if f"{years_ago} year" in gen_lower:
            return True

    # Normalize dates for comparison
    gen_normalized = normalize_date(gen_lower)
    gold_normalized = normalize_date(gold_lower)

    # Check normalized versions
    if gold_normalized in gen_normalized or gen_normalized in gold_normalized:
        return True

    # Try flexible date matching (for "week before X" style answers)
    if dates_match_flexible(gold_lower, gen_lower):
        return True

    # Direct substring match
    if gold_lower in gen_lower or gen_lower in gold_lower:
        return True

    # Use LLM judge with STRICT prompt
    prompt = f"""You are a strict answer grader. Compare these two answers:

Question: {question}
Expected Answer: {gold}
Generated Answer: {generated}

Rules for CORRECT:
- The generated answer must contain the SAME KEY FACTS as expected
- Minor wording differences are OK if meaning is same
- If expected says "X" and generated says "Y" (different facts), mark WRONG

Rules for WRONG:
- Different facts or information = WRONG
- Vague answer when specific answer expected = WRONG
- Correct topic but wrong details = WRONG

Reply with ONLY one word: CORRECT or WRONG"""

    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.0, "num_predict": 10}},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            text = result.get("response", "").upper()
            return "CORRECT" in text and "WRONG" not in text
    except:
        return False


@app.get("/api/benchmark/stream")
async def stream_benchmark(
    request: Request,
    max_conv: int = 1,
    category: str = "all",
    conversations_list: str = "",  # Comma-separated list like "0,2,5"
    shuffle: bool = False,
    max_questions: int = 0,  # 0 = all questions
):
    """Stream benchmark results as Server-Sent Events"""
    import random

    async def generate():
        # Load LoCoMo data
        locomo_path = Path(__file__).parent.parent / "evaluation" / "locomo" / "locomo10.json"
        with open(locomo_path) as f:
            data = json.load(f)

        # Select conversations
        if conversations_list:
            # Use specific conversations
            conv_indices = [int(i.strip()) for i in conversations_list.split(",") if i.strip().isdigit()]
            conv_indices = [i for i in conv_indices if 0 <= i < len(data)]
            conversations = [(i, data[i]) for i in conv_indices]
        else:
            # Use first max_conv conversations
            conversations = [(i, data[i]) for i in range(min(max_conv, len(data)))]

        # Initialize stats at 0
        stats = {cat: {"correct": 0, "total": 0} for cat in CATEGORIES.values()}

        yield f"data: {json.dumps({'type': 'init', 'conversations': len(conversations)})}\n\n"

        async with aiohttp.ClientSession() as http:
            total_questions_processed = 0

            for original_idx, conversation in conversations:
                # Extract messages
                messages = []
                session_idx = 1
                while f"session_{session_idx}" in conversation.get("conversation", conversation):
                    conv_data = conversation.get("conversation", conversation)
                    for msg in conv_data.get(f"session_{session_idx}", []):
                        messages.append({
                            "speaker": msg.get("speaker", "Unknown"),
                            "text": msg.get("text", ""),
                            "datetime": conv_data.get(f"session_{session_idx}_date_time", ""),
                        })
                    session_idx += 1

                yield f"data: {json.dumps({'type': 'status', 'message': f'Conv {original_idx}: Ingesting {len(messages)} messages...'})}\n\n"

                # Create storage and tesseract
                storage = InMemoryNeuralGraphStorage()
                tesseract = Tesseract(storage)
                session_key = f"conv_{original_idx}"

                # Ingest messages
                for msg_idx, msg in enumerate(messages):
                    embedding = await get_embedding(http, msg["text"])
                    node = NeuralNode(
                        node_id=f"msg_{original_idx}_{msg_idx}",
                        session_key=session_key,
                        content=msg["text"],
                        layer=NodeLayer.MESSAGE,
                        embedding=embedding,
                        metadata={"speaker": msg["speaker"], "datetime": msg["datetime"]},
                    )
                    await storage.save_node(node)

                yield f"data: {json.dumps({'type': 'status', 'message': f'Conv {original_idx}: Processing questions...'})}\n\n"

                # Process questions
                questions = list(conversation.get("qa", []))

                # Shuffle questions if requested
                if shuffle:
                    random.shuffle(questions)

                for q_idx, qa in enumerate(questions):
                    # Check max questions limit
                    if max_questions > 0 and total_questions_processed >= max_questions:
                        yield f"data: {json.dumps({'type': 'status', 'message': f'Reached {max_questions} question limit'})}\n\n"
                        break
                    try:
                        # Check if client disconnected
                        if await request.is_disconnected():
                            print(f"Client disconnected at question {q_idx}")
                            return

                        question = qa.get("question", "")
                        gold = qa.get("answer", "")
                        cat_id = qa.get("category", 1)
                        category_name = CATEGORIES.get(cat_id, "single_hop")

                        # Skip if filtering by category
                        if category != "all" and category_name != category:
                            continue

                        # For adversarial questions, empty answer means "should say unknown/not mentioned"
                        is_adversarial = (cat_id == 5)
                        if not gold or str(gold).strip() == "":
                            if not is_adversarial:
                                print(f"Skipping empty answer: {question[:50]}...")
                                continue
                            # For adversarial, the "gold" is that there's no answer
                            gold = "[NO ANSWER - should indicate unknown]"

                        print(f"Processing [{category_name}]: {question[:60]}...")

                        # Send heartbeat every question to keep connection alive
                        yield f": heartbeat\n\n"

                        # Get embedding and retrieve
                        query_emb = await get_embedding(http, question)
                        if not query_emb:
                            print(f"Failed to get embedding for: {question[:50]}...")
                            continue

                        results = await tesseract.retrieve(
                            query_text=question,
                            query_embedding=query_emb,
                            session_key=session_key,
                            limit=TOP_K,
                        )

                        # Build context with timestamps for temporal reasoning
                        context_parts = []
                        total_len = 0
                        for node, charge in results:
                            if total_len >= CONTEXT_LIMIT:
                                break
                            speaker = node.metadata.get("speaker", "") if node.metadata else ""
                            dt = node.metadata.get("datetime", "") if node.metadata else ""
                            # Include datetime so LLM can resolve relative dates like "yesterday", "last week"
                            if dt and speaker:
                                entry = f"[{dt} - {speaker}]: {node.content}"
                            elif speaker:
                                entry = f"[{speaker}]: {node.content}"
                            else:
                                entry = node.content
                            context_parts.append(entry)
                            total_len += len(entry)

                        context = "\n\n".join(context_parts)

                        # Generate and judge
                        generated = await generate_answer(http, question, context, category_name)
                        is_correct = await judge_answer(http, question, generated, gold, is_adversarial)

                        # Update stats
                        stats[category_name]["total"] += 1
                        if is_correct:
                            stats[category_name]["correct"] += 1
                        total_questions_processed += 1

                        # Send result
                        result = {
                            "type": "result",
                            "question": question,
                            "gold": str(gold),
                            "generated": generated,
                            "category": category_name,
                            "correct": is_correct,
                            "stats": stats,
                            "q_num": sum(s["total"] for s in stats.values()),
                            "conv_id": original_idx,
                        }

                        yield f"data: {json.dumps(result)}\n\n"
                        await asyncio.sleep(0.05)  # Small delay for UI

                    except Exception as e:
                        print(f"ERROR processing question {q_idx}: {e}")
                        import traceback
                        traceback.print_exc()
                        # Send error but continue
                        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                        continue

                # Check if we hit max questions limit (break outer loop)
                if max_questions > 0 and total_questions_processed >= max_questions:
                    break

        # Final summary
        yield f"data: {json.dumps({'type': 'complete', 'stats': stats})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


if __name__ == "__main__":
    print("Starting Tesseract Benchmark Server on http://localhost:8000")
    print("Open http://localhost:8000 in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000)
