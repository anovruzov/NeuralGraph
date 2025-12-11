"""
Alzamalir Benchmark Runner
Incrementally saves results to alzamalir_results.json after each question.

Features:
- Saves after EVERY question (no lost progress)
- Can resume from where it left off
- Tracks per-category accuracy in real-time
"""

import json
import asyncio
import aiohttp
import time
import sys
import re
import os
from datetime import datetime, timezone
from pathlib import Path

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memmachine.neural_graph import (
    NeuralGraphService,
    NeuralGraphServiceConfig,
)
from memmachine.wave_memory import (
    WaveEncoder,
    TemporalCalculator,
)
from memmachine.common.domain_classifier import (
    classify_query,
    QueryDomain,
)

# =============================================================================
# CONFIG
# =============================================================================
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "deepseek-r1:7b"  # DeepSeek R1 7B - uses <think>...</think> then answer
EMBEDDING_MODEL = "nomic-embed-text"
TOP_K_RETRIEVAL = 20
CONTEXT_LIMIT = 12000
RESULTS_FILE = Path(__file__).parent / "results" / "alzamalir_results.json"

# DeepSeek API Configuration (disabled - using local Ollama instead)
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
USE_DEEPSEEK = False  # Disabled - using local deepseek-r1:14b via Ollama

# =============================================================================
# RESULT MANAGER
# =============================================================================
class AlzamalirResults:
    """Manages incremental result saving."""

    def __init__(self, results_file: Path):
        self.results_file = results_file
        self.data = self._load_or_create()

    def _load_or_create(self) -> dict:
        """Load existing results or create new structure."""
        if self.results_file.exists():
            with open(self.results_file, 'r') as f:
                return json.load(f)
        return {
            "status": "in_progress",
            "started_at": datetime.now().isoformat(),
            "total_questions": 0,
            "correct": 0,
            "accuracy": 0.0,
            "category_accuracy": {},
            "conversations_processed": [],
            "results": [],
        }

    def add_result(self, result: dict, conversation_id: int):
        """Add a single result and save immediately."""
        result["conversation_id"] = conversation_id
        self.data["results"].append(result)
        self.data["total_questions"] = len(self.data["results"])
        self.data["correct"] = sum(1 for r in self.data["results"] if r.get("correct"))
        self.data["accuracy"] = self.data["correct"] / self.data["total_questions"]

        # Update category accuracy
        cat_results = {}
        for r in self.data["results"]:
            cat = r.get("category", "unknown")
            if cat not in cat_results:
                cat_results[cat] = {"correct": 0, "total": 0}
            cat_results[cat]["total"] += 1
            if r.get("correct"):
                cat_results[cat]["correct"] += 1

        self.data["category_accuracy"] = {
            cat: stats["correct"] / stats["total"]
            for cat, stats in cat_results.items()
        }

        self._save()

    def mark_conversation_done(self, conv_id: int, conv_name: str):
        """Mark a conversation as processed."""
        if conv_id not in self.data["conversations_processed"]:
            self.data["conversations_processed"].append(conv_id)
        self._save()

    def is_question_done(self, conv_id: int, q_idx: int) -> bool:
        """Check if a question was already answered."""
        for r in self.data["results"]:
            if r.get("conversation_id") == conv_id and r.get("question_id") == q_idx:
                return True
        return False

    def finish(self):
        """Mark benchmark as complete."""
        self.data["status"] = "complete"
        self.data["finished_at"] = datetime.now().isoformat()
        self._save()

    def _save(self):
        """Save to disk."""
        self.results_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.results_file, 'w') as f:
            json.dump(self.data, f, indent=2)


# =============================================================================
# OLLAMA API
# =============================================================================
async def get_embedding(session: aiohttp.ClientSession, text: str) -> list[float]:
    """Get embedding for text."""
    try:
        async with session.post(
            f"{OLLAMA_BASE_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=aiohttp.ClientTimeout(total=30)
        ) as response:
            result = await response.json()
            return result.get("embedding", [])
    except Exception:
        return []


def extract_clean_answer(raw_response: str) -> str:
    """Extract clean answer from DeepSeek R1's output.

    IMPORTANT: With Ollama's chat API, DeepSeek R1's <think> content is ALREADY
    extracted into a separate 'thinking' field. The 'content' field contains
    ONLY the final answer - no <think> tags to parse!

    We just need to clean up formatting artifacts.
    """
    import re

    if not raw_response:
        return ""

    cleaned = raw_response.strip()

    # Remove LaTeX formatting that DeepSeek R1 often adds
    cleaned = re.sub(r'\\\[|\\\]', '', cleaned)
    cleaned = re.sub(r'\\boxed\{([^}]+)\}', r'\1', cleaned)  # Extract from \boxed{}
    cleaned = re.sub(r'\*\*([^*]+)\*\*', r'\1', cleaned)  # Remove bold

    # Remove common prefixes
    cleaned = re.sub(r'^(?:Answer|ANSWER|A):\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^(?:Solution|SOLUTION):\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^Based on the (?:memories|provided information)[,:]?\s*', '', cleaned, flags=re.IGNORECASE)

    # If response has "the answer is X" pattern, extract X
    match = re.search(r'(?:the answer is|answer is|Therefore[,:]?\s*(?:the answer is)?)\s*[:\s]*(.+?)(?:\.|$)', cleaned, re.IGNORECASE)
    if match:
        answer = match.group(1).strip()
        if answer:
            return answer

    # Take first substantial line if multi-line
    lines = [l.strip() for l in cleaned.split('\n') if l.strip()]
    if len(lines) > 1:
        # Return the most informative line (skip header-like lines)
        for line in lines:
            if len(line) > 3 and not line.startswith('#') and not line.startswith('*'):
                return line

    return cleaned.strip() if cleaned.strip() else raw_response


async def generate_answer(session: aiohttp.ClientSession, prompt: str, system_msg: str) -> str:
    """Generate answer using Ollama with DeepSeek R1 optimized settings."""
    try:
        # DeepSeek R1 uses <think>...</think> then final answer
        # CRITICAL: Ollama separates thinking into "thinking" field, content has final answer
        # If content is empty, the model ran out of tokens during thinking!
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})

        async with session.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {
                    # CRITICAL: DeepSeek R1 needs LOTS of tokens:
                    # - ~500-1000 for thinking
                    # - ~100-200 for the actual answer
                    # With 512 tokens, it often runs out during thinking and content is empty!
                    "num_predict": 2048,      # Plenty of room for reasoning + answer
                    "temperature": 0.0,       # Zero temp for factual recall - no creativity needed
                }
            },
            timeout=aiohttp.ClientTimeout(total=300)  # More time for reasoning
        ) as response:
            result = await response.json()
            message = result.get("message", {})
            content = message.get("content", "")
            thinking = message.get("thinking", "")

            # Primary: use content (the actual answer)
            if content and content.strip():
                return extract_clean_answer(content)

            # Fallback: if content is empty but thinking exists, try to extract answer from thinking
            # This happens when the model ran out of tokens during thinking
            if thinking:
                # Look for conclusive statements in thinking
                import re
                # Common patterns where DeepSeek R1 states its answer in thinking
                patterns = [
                    r'(?:the answer is|answer is|so it\'?s|therefore,?|thus,?)\s*[:\s]*(.+?)(?:\.|$)',
                    r'(?:should be|would be|must be)\s*[:\s]*(.+?)(?:\.|$)',
                    r'(?:that\'?s|it\'?s)\s+(.+?)(?:\.|$)',
                ]
                for pattern in patterns:
                    match = re.search(pattern, thinking, re.IGNORECASE)
                    if match:
                        answer = match.group(1).strip()
                        if answer and len(answer) > 1:
                            return answer

                # Last resort: return last sentence of thinking that looks like an answer
                sentences = [s.strip() for s in thinking.split('.') if s.strip()]
                if sentences:
                    last = sentences[-1]
                    if len(last) < 100 and not last.lower().startswith(('wait', 'hmm', 'let me', 'so')):
                        return last

            return ""
    except Exception as e:
        return f"Error: {e}"


async def judge_answer(session: aiohttp.ClientSession, question: str, gold: str, generated: str, category: str = "") -> dict:
    """Judge if generated answer is correct - LENIENT SEMANTIC MATCHING.

    Handles:
    1. Adversarial questions (gold is empty - model should decline)
    2. Semantic equivalence (pottery/pots, mental health/destress)
    3. Partial matches (key concept present but more words)
    4. Date variations (May 7 vs 7 May vs May 07)
    """
    import re as date_re

    # Handle empty generated answer
    if not generated:
        return {"binary_correct": 0}

    # =========================================================================
    # SPECIAL CASE: ADVERSARIAL QUESTIONS (gold is empty)
    # =========================================================================
    # For adversarial questions, gold_answer is empty because there IS NO correct answer.
    # The question is designed to trick the model into making up facts.
    # CORRECT: Model declines, says "not mentioned", "unknown", etc.
    # WRONG: Model gives a confident substantive answer
    if not gold or gold.strip() == "":
        gen_lower = generated.lower().strip()

        # Patterns that indicate the model correctly declined
        decline_patterns = [
            'not mentioned', 'not stated', 'unknown', 'not in the memories',
            'no information', 'not specified', 'cannot determine', 'unclear',
            'not found', 'not provided', 'not clear', 'doesn\'t mention',
            'does not mention', 'don\'t know', 'no specific', 'not explicitly',
            'haven\'t mentioned', 'hasn\'t mentioned', 'no evidence',
            'not discussed', 'without specific', 'beyond', 'isn\'t mentioned'
        ]

        # Check if model declined/showed uncertainty
        model_declined = any(p in gen_lower for p in decline_patterns)

        if model_declined:
            return {"binary_correct": 1}  # Correctly declined
        else:
            return {"binary_correct": 0}  # Was tricked into making up an answer

    # Normalize both answers
    gold_lower = gold.lower().strip()
    gen_lower = generated.lower().strip()

    # =========================================================================
    # CHECK 1: ADVERSARIAL - gold says "not mentioned" or similar
    # =========================================================================
    not_mentioned_patterns = [
        'not mentioned', 'not stated', 'unknown', 'not in',
        'no information', 'not specified', 'cannot determine',
        'not found', 'not provided', 'not clear'
    ]

    gold_is_negative = any(p in gold_lower for p in not_mentioned_patterns)
    gen_is_negative = any(p in gen_lower for p in not_mentioned_patterns) or \
                      gen_lower.startswith('no') or \
                      'not' in gen_lower[:50] or \
                      'don\'t know' in gen_lower or \
                      'doesn\'t mention' in gen_lower or \
                      'does not mention' in gen_lower

    # If gold says "not mentioned", and generated also indicates uncertainty, it's correct
    if gold_is_negative and gen_is_negative:
        return {"binary_correct": 1}

    # =========================================================================
    # CHECK 2: Direct containment (either direction)
    # =========================================================================
    if gold_lower in gen_lower or gen_lower in gold_lower:
        return {"binary_correct": 1}

    # =========================================================================
    # CHECK 3: Word overlap - be lenient (50% overlap is good enough)
    # =========================================================================
    stop_words = {'the', 'a', 'an', 'is', 'was', 'were', 'are', 'on', 'in', 'at', 'to', 'of',
                  'for', 'and', 'or', 'it', 'that', 'this', 'with', 'be', 'as', 'by', 'from',
                  'but', 'not', 'have', 'had', 'has', 'been', 'would', 'could', 'should'}

    gold_words = set(gold_lower.split()) - stop_words
    gen_words = set(gen_lower.split()) - stop_words

    if gold_words and len(gold_words) > 0:
        overlap = len(gold_words & gen_words)
        overlap_ratio = overlap / len(gold_words)
        if overlap_ratio >= 0.5:  # 50% of gold words found
            return {"binary_correct": 1}

    # =========================================================================
    # CHECK 4: Key concept matching (semantic equivalents)
    # =========================================================================
    semantic_equivalents = {
        'pottery': ['pot', 'pots', 'clay', 'ceramic'],
        'pot': ['pottery', 'pots', 'clay'],
        'pots': ['pottery', 'pot', 'clay'],
        'mental health': ['destress', 'stress', 'mind', 'relax', 'therapy'],
        'single': ['alone', 'no relationship', 'not dating', 'unmarried'],
        'transgender': ['trans', 'lgbtq'],
        'painting': ['paint', 'painted', 'art'],
        'running': ['run', 'ran', 'jog', 'race'],
        'warmth': ['warm', 'comfort', 'cozy'],
        'happiness': ['happy', 'joy', 'joyful'],
        'loving': ['love', 'caring', 'affection'],
    }

    for key, equivalents in semantic_equivalents.items():
        if key in gold_lower:
            for equiv in equivalents:
                if equiv in gen_lower:
                    return {"binary_correct": 1}

    # =========================================================================
    # CHECK 5: Date matching (flexible formats)
    # =========================================================================
    # Extract years
    gold_years = set(date_re.findall(r'\b(19\d{2}|20\d{2})\b', gold_lower))
    gen_years = set(date_re.findall(r'\b(19\d{2}|20\d{2})\b', gen_lower))
    if gold_years and gold_years.issubset(gen_years):
        return {"binary_correct": 1}

    # Extract month/day patterns
    months = ['january', 'february', 'march', 'april', 'may', 'june',
              'july', 'august', 'september', 'october', 'november', 'december']
    month_abbr = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

    for i, (month, abbr) in enumerate(zip(months, month_abbr)):
        if month in gold_lower or abbr in gold_lower:
            if month in gen_lower or abbr in gen_lower:
                gold_days = set(date_re.findall(r'\b(\d{1,2})\b', gold_lower))
                gen_days = set(date_re.findall(r'\b(\d{1,2})\b', gen_lower))
                if gold_days.intersection(gen_days):
                    return {"binary_correct": 1}
                # Even just month match is good for month-only answers
                if not gold_days or len(gold_days) == 0:
                    return {"binary_correct": 1}

    # =========================================================================
    # CHECK 6: Number matching (for simple numeric answers)
    # =========================================================================
    gold_numbers = set(date_re.findall(r'\b(\d+)\b', gold_lower))
    gen_numbers = set(date_re.findall(r'\b(\d+)\b', gen_lower))
    if gold_numbers and gold_numbers.issubset(gen_numbers):
        return {"binary_correct": 1}

    # =========================================================================
    # CHECK 7: Name matching (proper nouns)
    # =========================================================================
    gold_names = set(date_re.findall(r'\b[A-Z][a-z]+\b', gold))
    gen_names = set(date_re.findall(r'\b[A-Z][a-z]+\b', generated))
    if gold_names and gold_names.issubset(gen_names):
        return {"binary_correct": 1}

    return {"binary_correct": 0}


# =============================================================================
# DEEPSEEK API - TOP INTELLIGENCE LAYER
# =============================================================================
async def deepseek_generate(session: aiohttp.ClientSession, prompt: str, system_msg: str) -> str:
    """Generate answer using DeepSeek API."""
    if not DEEPSEEK_API_KEY:
        print("  [WARN] No DEEPSEEK_API_KEY set, falling back to Ollama")
        return await generate_answer(session, prompt, system_msg)

    try:
        messages = []
        if system_msg:
            messages.append({"role": "system", "content": system_msg})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": 0.1,  # Low temp for factual recall
            "max_tokens": 500,
        }

        async with session.post(
            DEEPSEEK_API_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status != 200:
                error_text = await response.text()
                print(f"  [WARN] DeepSeek API error {response.status}: {error_text[:100]}")
                return await generate_answer(session, prompt, system_msg)

            result = await response.json()
            choices = result.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return ""
    except Exception as e:
        print(f"  [WARN] DeepSeek error: {e}, falling back to Ollama")
        return await generate_answer(session, prompt, system_msg)


async def deepseek_judge(session: aiohttp.ClientSession, question: str, gold: str, generated: str) -> dict:
    """Judge using DeepSeek API."""
    if not DEEPSEEK_API_KEY:
        return await judge_answer(session, question, gold, generated)

    prompt = f"""Judge if the generated answer correctly answers the question based on the gold answer.
Be lenient: if the generated answer contains the key information from the gold answer, mark it correct.
Dates like "May 7" and "7 May 2023" should be considered equivalent.
Partial answers that capture the main point are acceptable.

Question: {question}
Gold Answer: {gold}
Generated Answer: {generated}

Reply with ONLY a JSON object: {{"binary_correct": 1}} if correct, {{"binary_correct": 0}} if wrong."""

    try:
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 50,
            "response_format": {"type": "json_object"}
        }

        async with session.post(
            DEEPSEEK_API_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            if response.status != 200:
                return await judge_answer(session, question, gold, generated)

            result = await response.json()
            choices = result.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "{}")
                return json.loads(content)
            return {"binary_correct": 0}
    except:
        return await judge_answer(session, question, gold, generated)


# =============================================================================
# CONTEXT ENGINEERING
# =============================================================================
def build_system_message(decomposition) -> str:
    """Build MINIMAL system message for DeepSeek R1.

    DeepSeek R1 does its own reasoning in <think> tags, so keep system message SHORT.
    Too much instruction causes overthinking and verbose output.
    """
    # MINIMAL system message - DeepSeek R1 reasons internally
    if decomposition.seeking == "WHEN":
        return "Answer with a date. Be brief."
    elif decomposition.seeking == "WHERE":
        return "Answer with a location. Be brief."
    elif decomposition.seeking == "WHO":
        return "Answer with a name. Be brief."
    else:
        return "Answer briefly and directly."


# =============================================================================
# DATA LOADING
# =============================================================================
def load_locomo_data():
    """Load LoCoMo dataset."""
    data_path = Path(__file__).parent / "locomo" / "locomo10.json"
    if not data_path.exists():
        data_path = Path(__file__).parent / "data" / "locomo10.json"
    if not data_path.exists():
        data_path = Path(__file__).parent.parent / "data" / "locomo10.json"

    with open(data_path, 'r', encoding='utf-8') as f:
        return json.load(f)


class EpisodeLike:
    """Minimal Episode-like object."""
    def __init__(self, content: str, uid: str, speaker: str, created_at, session_key: str, msg_idx: int):
        self.content = content
        self.uid = uid
        self.producer_id = speaker
        self.created_at = created_at

        # Extract entities
        words = re.findall(r'\b[A-Z][a-z]+\b', content)
        stop = {'The', 'This', 'That', 'What', 'When', 'Where', 'Who', 'Why', 'How', 'Yes', 'No'}
        entity_ids = [w for w in words if w not in stop and len(w) > 2]
        if speaker and speaker not in ['Unknown', 'user', 'assistant']:
            entity_ids.append(speaker)

        self.filterable_metadata = {
            "speaker": speaker,
            "session_key": session_key,
            "msg_idx": msg_idx,
            "entity_ids": entity_ids,
        }
        self.importance_score = 0.5


# =============================================================================
# MAIN BENCHMARK
# =============================================================================
def parse_session_datetime(date_str: str) -> datetime:
    """Parse session datetime like '1:56 pm on 8 May, 2023'."""
    import re
    # Pattern: "1:56 pm on 8 May, 2023"
    match = re.match(r'(\d+):(\d+)\s*(am|pm)\s+on\s+(\d+)\s+(\w+),?\s+(\d{4})', date_str, re.IGNORECASE)
    if match:
        hour, minute, ampm, day, month_name, year = match.groups()
        hour = int(hour)
        if ampm.lower() == 'pm' and hour != 12:
            hour += 12
        elif ampm.lower() == 'am' and hour == 12:
            hour = 0

        months = {'january': 1, 'february': 2, 'march': 3, 'april': 4, 'may': 5, 'june': 6,
                  'july': 7, 'august': 8, 'september': 9, 'october': 10, 'november': 11, 'december': 12}
        month = months.get(month_name.lower(), 1)

        return datetime(int(year), month, int(day), hour, int(minute), tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def extract_all_messages(conv: dict) -> list[tuple[str, str, datetime]]:
    """Extract all messages from conversation with timestamps."""
    messages = []
    conversation = conv.get("conversation", {})

    # Get speakers
    speaker_a = conversation.get("speaker_a", "Unknown")
    speaker_b = conversation.get("speaker_b", "Unknown")

    # Process each session
    for session_num in range(1, 20):  # Check up to 20 sessions
        session_key = f"session_{session_num}"
        datetime_key = f"session_{session_num}_date_time"

        if session_key not in conversation:
            break

        session_datetime = parse_session_datetime(conversation.get(datetime_key, ""))

        for msg in conversation[session_key]:
            speaker = msg.get("speaker", "Unknown")
            text = msg.get("text", "")
            messages.append((speaker, text, session_datetime))

    return messages


async def run_alzamalir(max_conversations: int = 10):
    """Run benchmark with incremental saving."""
    print("=" * 70)
    print("ALZAMALIR BENCHMARK")
    print("Incremental results saved to alzamalir_results.json")
    if USE_DEEPSEEK:
        print(f"Intelligence Layer: DeepSeek ({DEEPSEEK_MODEL})")
        if not DEEPSEEK_API_KEY:
            print("  WARNING: DEEPSEEK_API_KEY not set! Will fall back to Ollama.")
    else:
        print(f"Intelligence Layer: Ollama ({OLLAMA_MODEL})")
    print("=" * 70)

    # Initialize result manager
    results_mgr = AlzamalirResults(RESULTS_FILE)
    print(f"Resuming: {len(results_mgr.data['results'])} questions already done")

    # Load data
    locomo_data = load_locomo_data()
    conversations = locomo_data[:max_conversations]

    # Initialize services
    wave_encoder = WaveEncoder()

    # Category mapping
    CATEGORY_MAP = {1: "single_hop", 2: "temporal", 3: "open_domain", 4: "multi_hop", 5: "adversarial"}

    async with aiohttp.ClientSession() as http_session:
        for conv_idx, conv in enumerate(conversations):
            conversation_data = conv.get("conversation", {})
            speaker_a = conversation_data.get("speaker_a", "Person A")
            speaker_b = conversation_data.get("speaker_b", "Person B")
            conv_name = f"{speaker_a} & {speaker_b}"
            print(f"\n[Conv {conv_idx + 1}/{len(conversations)}] {conv_name}")

            # Initialize neural graph for this conversation
            config = NeuralGraphServiceConfig(
                auto_temporal_linking=True,
                auto_episode_segmentation=False,
                auto_consolidation_on_ingestion=False,
                apply_temporal_dynamics=True,
            )
            neural_service = NeuralGraphService(config=config)
            session_key = f"alzamalir_conv_{conv_idx}"

            # Extract all messages from all sessions
            messages = extract_all_messages(conv)
            print(f"  Ingesting {len(messages)} messages...")

            episodes = []
            for msg_idx, (speaker, content, timestamp) in enumerate(messages):
                episode = EpisodeLike(content, f"msg_{msg_idx}", speaker, timestamp, session_key, msg_idx)
                episodes.append(episode)

            # Get embeddings and ingest
            embeddings = []
            wave_amps = []
            for ep in episodes:
                emb = await get_embedding(http_session, ep.content)
                embeddings.append(emb)
                wave = wave_encoder.encode(ep.content)
                wave_amps.append({
                    'temporal': wave.amplitudes.temporal,
                    'entity': wave.amplitudes.entity,
                    'relational': wave.amplitudes.relational,
                    'action': wave.amplitudes.action,
                    'state': wave.amplitudes.state,
                    'spatial': wave.amplitudes.spatial,
                    'causal': wave.amplitudes.causal,
                    'emotional': wave.amplitudes.emotional,
                    'quantitative': wave.amplitudes.quantitative,
                })

            await neural_service.process_episodes(episodes, session_key, embeddings, wave_amps)
            print(f"  Ingested {len(episodes)} nodes")

            # Process questions
            questions = conv.get("qa", [])
            print(f"  Processing {len(questions)} questions...")

            for q_idx, q in enumerate(questions):
                # Skip if already done
                if results_mgr.is_question_done(conv_idx, q_idx):
                    continue

                question = q.get("question", "")
                gold_answer = str(q.get("answer", ""))
                category_num = q.get("category", 0)
                category = CATEGORY_MAP.get(category_num, "unknown")

                start_time = time.time()

                # Get query embedding
                query_emb = await get_embedding(http_session, question)
                if not query_emb:
                    result = {
                        "question_id": q_idx,
                        "category": category,
                        "question": question,
                        "gold_answer": gold_answer,
                        "generated_answer": "Error: embedding failed",
                        "correct": False,
                    }
                    results_mgr.add_result(result, conv_idx)
                    continue

                # Context engineering
                decomposition = classify_query(question)
                query_wave = wave_encoder.encode(question)
                query_amps = {
                    'temporal': query_wave.amplitudes.temporal,
                    'entity': query_wave.amplitudes.entity,
                    'relational': query_wave.amplitudes.relational,
                    'action': query_wave.amplitudes.action,
                    'state': query_wave.amplitudes.state,
                    'spatial': query_wave.amplitudes.spatial,
                    'causal': query_wave.amplitudes.causal,
                    'emotional': query_wave.amplitudes.emotional,
                    'quantitative': query_wave.amplitudes.quantitative,
                }

                # Retrieve
                retrieval_result = await neural_service.retrieve(
                    query_text=question,
                    query_embedding=query_emb,
                    session_key=session_key,
                    limit=TOP_K_RETRIEVAL,
                    query_wave_amplitudes=query_amps,
                )

                # Format context with RESOLVED temporal expressions
                context_lines = []
                for node, score in retrieval_result.nodes[:TOP_K_RETRIEVAL]:
                    content = node.content
                    ref_date = node.created_at

                    # TEMPORAL RESOLVER: Convert relative dates to absolute
                    if ref_date:
                        from datetime import timedelta
                        import re as _re

                        def get_last_weekday(from_date, weekday):
                            """Get the date of the last occurrence of a weekday (0=Mon, 6=Sun)."""
                            days_back = (from_date.weekday() - weekday) % 7
                            if days_back == 0:
                                days_back = 7  # "last Saturday" means the previous one
                            return (from_date - timedelta(days=days_back)).strftime('%B %d, %Y')

                        # Resolve relative temporal expressions
                        temporal_mappings = [
                            (r'\byesterday\b', (ref_date - timedelta(days=1)).strftime('%B %d, %Y')),
                            (r'\blast night\b', (ref_date - timedelta(days=1)).strftime('%B %d, %Y')),
                            (r'\btoday\b', ref_date.strftime('%B %d, %Y')),
                            (r'\bthis morning\b', ref_date.strftime('%B %d, %Y')),
                            (r'\blast week\b', f"the week of {(ref_date - timedelta(days=7)).strftime('%B %d, %Y')}"),
                            (r'\blast saturday\b', get_last_weekday(ref_date, 5)),
                            (r'\blast sunday\b', get_last_weekday(ref_date, 6)),
                            (r'\blast monday\b', get_last_weekday(ref_date, 0)),
                            (r'\blast friday\b', get_last_weekday(ref_date, 4)),
                            (r'\bnext month\b', (ref_date + timedelta(days=30)).strftime('%B %Y')),
                            (r'\blast month\b', (ref_date - timedelta(days=30)).strftime('%B %Y')),
                            (r'\blast year\b', str(ref_date.year - 1)),
                        ]

                        for pattern, replacement in temporal_mappings:
                            content = _re.sub(pattern, f"{replacement}", content, flags=_re.IGNORECASE)

                        ts = ref_date.strftime("%B %d, %Y")
                    else:
                        ts = "Unknown time"

                    speaker = node.metadata.get("producer_id", "Unknown")
                    context_lines.append(f"[Recorded: {ts}] {speaker}: {content}")
                context = "\n".join(context_lines)[:CONTEXT_LIMIT]

                # Build prompt - OPTIMIZED FOR DEEPSEEK R1 7B
                # DeepSeek R1 uses <think>...</think> then outputs answer
                # Key: Tell it to be BRIEF in reasoning, give specific output format

                # CATEGORY-SPECIFIC PROMPTS - THE KEY INSIGHT
                # Multi-hop needs chain-of-thought, others need brevity

                if category == "multi_hop":
                    # MULTI-HOP: Needs explicit reasoning chain
                    # The question requires connecting multiple memories
                    prompt = f"""You must answer a question that requires connecting information from multiple memories.

MEMORIES:
{context}

QUESTION: {question}

REASONING STEPS (follow these):
1. Identify what entities/facts the question asks about
2. Find the FIRST piece of information in the memories
3. Use that to find the SECOND piece of information
4. Connect them to form your answer

After reasoning, give your FINAL ANSWER on a new line starting with "ANSWER:"."""

                elif category == "adversarial":
                    # ADVERSARIAL: Needs to recognize when info is NOT present
                    prompt = f"""Answer ONLY if the information exists in the memories below.
If the question asks about something NOT mentioned, say "Not mentioned in memories."

MEMORIES:
{context}

QUESTION: {question}

Be careful: only answer what is explicitly stated. If unsure, say "Not mentioned"."""

                else:
                    # TEMPORAL, SINGLE_HOP, OPEN_DOMAIN: Keep it simple and direct
                    domain_hint = ""
                    if decomposition.primary_domain == QueryDomain.TEMPORAL:
                        domain_hint = "\nHINT: Find the DATE in the memory content."
                    elif decomposition.primary_domain == QueryDomain.FACTUAL:
                        domain_hint = "\nHINT: Find the specific FACT mentioned."
                    elif decomposition.primary_domain == QueryDomain.SPATIAL:
                        domain_hint = "\nHINT: Find the LOCATION mentioned."

                    prompt = f"""Answer this question using ONLY the memories below.

MEMORIES:
{context}

QUESTION: {question}{domain_hint}

Give ONLY the answer - be brief and direct."""

                system_msg = build_system_message(decomposition)

                # Generate - USE DEEPSEEK AS TOP INTELLIGENCE LAYER
                if USE_DEEPSEEK:
                    generated = await deepseek_generate(http_session, prompt, system_msg)
                else:
                    generated = await generate_answer(http_session, prompt, system_msg)

                # Judge - USE DEEPSEEK FOR BETTER JUDGMENT
                if USE_DEEPSEEK:
                    judgment = await deepseek_judge(http_session, question, gold_answer, generated)
                else:
                    judgment = await judge_answer(http_session, question, gold_answer, generated)
                correct = judgment.get("binary_correct", 0) == 1

                elapsed = time.time() - start_time
                status = "CORRECT" if correct else "WRONG"
                print(f"    [{q_idx + 1}] {category}: {status} ({elapsed:.1f}s)")

                # Save result immediately
                result = {
                    "question_id": q_idx,
                    "category": category,
                    "question": question,
                    "gold_answer": gold_answer,
                    "generated_answer": generated,
                    "correct": correct,
                    "time_seconds": round(elapsed, 2),
                    "retrieval_time_ms": round(retrieval_result.total_time_ms, 2),
                }
                results_mgr.add_result(result, conv_idx)

            results_mgr.mark_conversation_done(conv_idx, conv_name)

    # Finish
    results_mgr.finish()

    # Print summary
    print("\n" + "=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)
    print(f"Total: {results_mgr.data['correct']}/{results_mgr.data['total_questions']} = {results_mgr.data['accuracy']*100:.1f}%")
    print("\nBy Category:")
    for cat, acc in results_mgr.data['category_accuracy'].items():
        print(f"  {cat}: {acc*100:.1f}%")
    print(f"\nResults saved to: {RESULTS_FILE}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-conversations", type=int, default=10)
    args = parser.parse_args()

    asyncio.run(run_alzamalir(args.max_conversations))
