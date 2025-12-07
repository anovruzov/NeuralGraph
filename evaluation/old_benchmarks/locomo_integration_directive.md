# Operational Directive: Automated Integration and Evaluation of Custom Memory Architectures via the LoCoMo Benchmark

## 1. Executive Summary and Strategic Context

The evolution of Artificial Intelligence from static query-response models to persistent, autonomous agents has necessitated a fundamental shift in evaluation methodologies. Traditional benchmarks, which measure static knowledge retrieval or needle-in-a-haystack context retention within a single turn, fail to capture the temporal and causal complexities of long-term agent interactions. The LoCoMo (Long-Context Memory) benchmark, developed by Snap Research, has emerged as the definitive standard for assessing "episodic memory"—the ability of an AI system to maintain coherence, recall specific facts, and synthesize events across distinct sessions separated by simulated time.

For engineering teams seeking to deploy autonomous coding agents—specifically Claude Code—to "identify and discover" how to test a proprietary system against this benchmark, the challenge is multi-dimensional. It requires not only a mastery of the LoCoMo codebase and data schema but also a nuanced understanding of the "Benchmark Wars" currently shaping the industry. The discrepancies in reported performance between leading systems like Mem0, Zep, Memobase, and Backboard 3 highlight the critical importance of implementation details, particularly in how retrieval limits, judge models, and ingestion pipelines are configured.

This report serves as a comprehensive architectural guide and operational directive. It synthesizes technical documentation, repository analysis, and comparative performance data to construct an exhaustive set of instructions for Claude Code. The objective is to enable the autonomous agent to navigate the snap-research/locomo repository, architect a custom adapter for the proprietary system, and execute a rigorous evaluation that yields legally and technically defensible metrics. The analysis reveals that successful integration requires bypassing standard shell scripts in favor of a custom ingestion loop, strictly managing the "LLM-as-a-Judge" configuration to ensure reproducibility, and accurately handling multimodal artifacts to prevent data leakage or loss.

## 2. The LoCoMo Ecosystem: Architectural Analysis

To instruct an autonomous agent to "test our system," one must first map the terrain. The LoCoMo benchmark is not merely a dataset; it is a complex ecosystem comprising data structures, evaluation scripts, and specific python dependencies. An exhaustive analysis of the repository structure is required to identify the integration points.

### 2.1 Repository Structure and Critical Paths

The snap-research/locomo repository organizes its resources into distinct modules for data storage, agent generation, and task evaluation. Identifying the correct entry points is the first step in the prompt engineering process for Claude Code. The directory structure, as revealed by the research material, indicates a clear separation of concerns that the agent must respect.

| Directory / File | Functional Role | Operational Instruction for Agent |
|------------------|-----------------|-----------------------------------|
| `data/` | Storage Core: Contains the locomo10.json benchmark dataset. | Verify: The agent must confirm the existence of this file. If missing, it must trigger a download protocol. |
| `scripts/` | Execution Layer: Shell scripts for running evaluations (e.g., evaluate_hf_llm.sh). | Adapt: These scripts serve as templates. The agent must read them to understand the CLI arguments but essentially rewrite the logic for the custom system. |
| `task_eval/` | Logic Core: Python modules calculating metrics (ROUGE, F1, BLEU). | Import: The agent must import these modules to score the custom system's outputs, ensuring metric standardization. |
| `requirements.txt` | Dependency Manifest: Lists essential libraries (torch, transformers, openai). | Install: Immediate execution of pip install -r requirements.txt is mandatory to prevent runtime import errors. |
| `scripts/env.sh` | Configuration Interface: Stores API keys and directory paths. | Inject: The agent must populate this file with the Judge Model API keys (e.g., OpenAI) and the output paths. |

The analysis of the `scripts/` directory is particularly illuminating. Files such as `evaluate_gpts.sh` and `evaluate_hf_llm.sh` provide the standard harness for OpenAI and Hugging Face models, respectively. However, for a custom system—which may be a vector database, a graph database, or a hybrid architecture—these scripts are insufficient as-is. They assume a standard model interface. Therefore, the prompt for Claude Code must explicitly instruct it to analyze the logic within these scripts to replicate the evaluation loop, rather than simply trying to run them. The existence of `locomo_ingest_eval.py` in the Backboard implementation suggests that top-tier implementations create a dedicated python script that handles the "ingest-then-eval" workflow, bypassing the rigid shell scripts of the original repository.

### 2.2 The LoCoMo10 Dataset Schema

The heart of the benchmark is the `locomo10.json` file. This dataset contains 10 high-quality, long-term conversations. Unlike synthetic datasets, these conversations are designed to test the limits of context retention, averaging 300 turns and 9,000 tokens per conversation, spanning up to 35 distinct sessions.

The schema of this JSON file is intricate, and the autonomous agent must be programmed to parse it correctly to avoid data ingestion errors. The structure is as follows:

- **Root Level**: A list of conversation objects, each identified by a `sample_id`.
- **Conversation Object**: Contains a key `conversation` which is a list of sessions.
- **Session Structure**: Keyed by `session_<num>` (e.g., `session_1`, `session_2`). This chronological ordering is vital. The benchmark tests the system's ability to recall information from `session_1` while interacting in `session_10`.
- **Temporal Metadata**: `session_<num>_date_time` provides the timestamp. This is critical for the "Temporal Reasoning" task. If the system does not ingest this timestamp, it will fail questions related to "when did X happen relative to Y?".
- **Turn Structure**: Each session is a list of turns.
  - `speaker`: Identifies the interlocutor.
  - `content`: The primary text payload.
  - `img_url`: A link to visual context (multimodal).
  - `blip_caption`: A generated caption for the image.
  - `dia_id`: Unique dialog ID, used for evidence retrieval citations.
- **Annotation Objects**:
  - `qa`: The ground truth for Question Answering. Contains `question`, `answer`, `category` (reasoning type), and `evidence`.
  - `event_summary`: Ground truth for the summarization task.

The presence of `blip_caption` alongside `img_url` is a significant architectural detail. It implies that the benchmark supports both native multimodal systems (which process the image directly) and text-only systems (which rely on the caption). The prompt to Claude Code must force a decision: does the custom system have vision capabilities? If not, the agent must be instructed to fallback to `blip_caption` to ensure the system is not blinded to visual context.

### 2.3 Task Domains and Evaluation Metrics

LoCoMo is not a single test; it is a suite of three principal task domains. The autonomous agent must identify which task is being targeted to select the correct evaluation script.

1. **Question Answering (QA)**: This is the primary focus for memory systems. It evaluates the ability to recall specific facts across long durations. The metric used is typically the LLM-as-a-Judge Score, alongside F1 and Exact Match. The `qa` field in the JSON drives this task.

2. **Event Summarization**: This tests the system's ability to understand cause-and-effect relationships over time. It requires generating a summary that aligns with the `event_summary` ground truth. Metrics include ROUGE and BERTScore.

3. **Multimodal Dialogue Generation**: This probes the ability to generate persona-consistent responses given image inputs. It uses MMRelevance and BLEU scores.

For the specific purpose of testing a "system on the locomo10 benchmark" as requested, the industry standard is to focus on the QA task, as this provides the most granular data on memory retention (Single-hop vs. Multi-hop). The prompt instructions will prioritize the QA pipeline while acknowledging the existence of the others.

## 3. Comparative Landscape: The "Benchmark Wars"

To "test our system" implies a need for comparison. A raw score is meaningless without context. The research material reveals a fierce competitive landscape, often referred to as the "Benchmark Wars," where different memory architectures claim supremacy on LoCoMo. Understanding this context is vital for the agent to generate a meaningful report.

### 3.1 Performance Baselines and Controversies

Several systems have published LoCoMo results, but the numbers are often contested. The agent must be aware of these baselines to properly frame the custom system's performance.

| System | Reported Overall Score (%) | Strengths | Weaknesses | Source |
|--------|---------------------------|-----------|------------|--------|
| Backboard | 90.00 | Exceptional temporal reasoning (91.90%) and Open Domain (91.20%). | - | 5 |
| Memobase (v0.0.37) | 75.78 | Strong temporal reasoning (85.05%). Uses user modeling. | Multi-hop reasoning (46.88%) is relatively weak. | 4 |
| Zep | 75.14 | Balanced performance. Strong temporal (79.79%). | Open Domain (67.71%) is lower than Memobase. | 4 |
| Mem0 | 66.88 | Good Open Domain (72.93%). | Poor Temporal (55.51%) and Multi-hop (51.15%). | 4 |
| OpenAI Memory | 52.90 | Decent single-hop. | Fails significantly at Temporal (21.71%) and Multi-hop (42.92%). | 4 |

**Insight into Discrepancies**: There is a notable conflict in the data. Mem0 initially claimed SOTA status with a 26% improvement over OpenAI. However, Zep and Memobase published rebuttals showing Mem0 underperforming, particularly in Temporal Reasoning. This discrepancy often arises from how the benchmark is run. Some implementations hardcode a retrieval limit of `k=42` chunks. If a system relies on retrieving more context to answer complex questions, this artificial limit cripples it.

Furthermore, the choice of the "Judge" model matters. Using GPT-4o typically yields higher and more accurate scores than GPT-3.5 or smaller models. The autonomous agent must be instructed to document these configuration parameters (Judge model, k-value) to ensure the comparison is "apples to apples."

### 3.2 The Impact of Architecture

The variance in scores across categories (Single-hop vs. Temporal) suggests that architecture dictates performance.

- **Graph-based systems** (like Zep and Mem0-Graph) tend to perform better on Multi-hop reasoning because they map relationships between entities explicitly.
- **Vector-based RAG systems** often struggle with Temporal reasoning because vector similarity searches do not inherently respect chronological order. Retrieving the "most relevant" chunks might return contradictory facts from different times without the timestamp context.
- **User Modeling systems** (Memobase) excel at Temporal tasks by maintaining a structured profile of the user that evolves, rather than just a bag of vectors.

The prompt for Claude Code should encourage it to hypothesize about the custom system's architecture based on which specific categories it excels or fails in during the test.

## 4. Technical Implementation Directives

This section details the specific engineering steps the agent must take. These form the "how-to" core of the report.

### 4.1 Dependency Management and Environment

The environment setup is the first point of failure. The `requirements.txt` file generally requires standard ML libraries, but specific issues have been documented.

**The `datasets` Library Issue**: A `ValueError` has been documented when generating the dataset using the standard Hugging Face datasets library. The error `Failed to convert pandas DataFrame to Arrow Table` suggests a schema mismatch in the Parquet files.

**Directive**: The agent must not rely on `datasets.load_dataset()`. Instead, it must be instructed to use Python's native `json` module to load `data/locomo10.json` directly from the local file system. This bypasses the Arrow conversion layer entirely.

**SentencePiece**: `sentencepiece` is often required for tokenizers but might be missing from the base environment. The agent should explicitly check for this.

**API Keys**: The scripts rely on `scripts/env.sh`. The agent must be instructed to inspect this file and inject valid keys for `OPENAI_API_KEY` (for the judge) and potentially `ANTHROPIC_API_KEY` if the system under test or the judge uses Claude.

### 4.2 The "Adapter Pattern" for System Integration

The default scripts (`evaluate_hf_llm.sh`) are designed for models that conform to the Hugging Face `AutoModel` interface. A custom system (e.g., a REST API, a local class, or a database client) will not fit this mold. The autonomous agent must implement an Adapter Pattern.

**The Adapter Strategy**:

1. **Identify the Interface**: The agent must analyze `task_eval` to see how the evaluation script calls the model. It likely looks for a `.generate()` or `.chat()` method.

2. **Create the Wrapper**: The agent must write a Python class `CustomSystemAdapter` that wraps the custom system.
   - `__init__`: Initialize the connection to the custom system.
   - `ingest(text, metadata)`: A method to feed conversation turns into the system. This corresponds to the "Memory Storage" phase.
   - `query(question)`: A method that calls the system's retrieval/answer function and returns a string.

3. **The Ingestion Loop (Crucial)**: Unlike standard RAG benchmarks which might bulk-ingest documents, LoCoMo requires sequential ingestion to simulate time.
   - The agent must iterate through `conversation -> sessions -> turns`.
   - It must pass the `session_date_time` to the system if the system supports temporal metadata.
   - It must pause ingestion when it encounters a `qa` annotation, ask the question, record the answer, and then resume ingestion. This "online" evaluation is critical to testing the evolution of memory. If the agent were to ingest the whole conversation and then ask all questions, it would invalidate the temporal aspect of the test (i.e., asking a question from Session 2 while having memory of Session 10 is data leakage).

### 4.3 Handling Multimodality

If the custom system supports images, the agent faces a choice.

- **Option A (Visual)**: Use the `img_url`. The agent needs to verify if the URL is accessible or if the images are stored locally in `data/images` (if the repo was cloned recursively). The system adapter must be able to download/read the image bytes.
- **Option B (Text-Fallback)**: Use `blip_caption`. If the system is text-only, the agent must extract this field. The prompt must explicitly tell the agent to look for this key in the JSON turn object, otherwise, the system will miss context essential for answering questions about the images.

### 4.4 The "LLM-as-a-Judge" Configuration

The scoring mechanism is not a simple string match. It uses an LLM to evaluate the semantic equivalence of the generated answer to the ground truth.

**The Judge Script**: The agent must locate the scoring script, likely in `task_eval/eval_qa.py` or similar.

**Model Selection**: Local reproduction scores often drop because users run the judge on weaker models (like GPT-3.5 or local Llamas) to save money/time, whereas the paper results use GPT-4.

**Directive**: The agent must be instructed to configure the judge to use `gpt-4` or `claude-3-5-sonnet` to ensure the scores are comparable to the baselines. Using a weaker judge will result in false negatives (the system answers correctly, but the judge fails to recognize it).

## 5. Evaluation Workflow and Reproducibility

To ensure the test is robust and the results are valid, a strict workflow must be adhered to.

### 5.1 The `locomo_ingest_eval.py` Approach

While the repository provides shell scripts, the most robust way to test a custom system is to create a dedicated Python entry point, similar to the `locomo_ingest_eval.py` script referenced in the Backboard benchmark. This script encapsulates the entire lifecycle.

**Workflow Logic**:

1. **Setup**: Load JSON, init metrics containers.
2. **Sample Loop**: Iterate through each `sample_id` (conversation).
3. **Reset**: **Critical Step**. The system's memory must be wiped or a new "Agent ID" must be generated for each sample. If memory bleeds from Conversation 1 to Conversation 2, the results are invalid.
4. **Session Loop**: Iterate sessions.
5. **Turn Loop**: Ingest content.
6. **Trigger Point**: Check if current `turn_id` or `session_id` matches a `qa` entry.
7. **Eval**: Execute query, store result.
8. **Post-Process**: Calculate aggregate metrics.

### 5.2 Latency and Token Usage Tracking

Beyond accuracy, efficiency is a key competitive differentiator. Mem0 highlights a "90% reduction in token usage" compared to full-context.

**Directive**: The agent should be instructed to instrument the adapter to track:

- **Input Tokens**: How many tokens are sent to the LLM during the query (context size).
- **Latency**: Time (ms) from request to first token or full response.
- **Storage Size**: If possible, measure the growth of the memory store over the 35 sessions.

This data allows for a multi-axis comparison (e.g., "Our system is 5% less accurate but 50% faster and cheaper than Zep").

## 6. Detailed Prompt for Claude Code

The following section aggregates all the analysis above into the specific, executable prompt requested by the user. This prompt is designed to be pasted directly into Claude Code. It utilizes the "Persona" pattern and strict "Step-by-Step" instructions to ensure compliance with the complex requirements of the LoCoMo benchmark.

---

### PROMPT START

**Role**: You are a Senior AI Validation Engineer and Systems Architect.

**Objective**: You are tasked with "identifying and discovering" the precise procedure to benchmark a proprietary, custom memory system (referred to as "The System") using the LoCoMo (Long-Context Memory) benchmark framework.

**Target System**: Assume "The System" exposes a Python interface with two primary methods: `add_memory(content, metadata)` and `retrieve_answer(query)`.

**Constraint**: You must operate autonomously within the snap-research/locomo repository context. You must prioritize accuracy, reproducibility, and rigorous adherence to the temporal constraints of the benchmark.

---

#### Phase 1: Reconnaissance and Environment Validation

**Repository Mapping**:
1. Clone or navigate to the root of `snap-research/locomo`.
2. **Critical Check**: Verify the existence of `data/locomo10.json`. This is the dataset. If it is missing, identify the download URL from the `README.MD` or `scripts/download_data.sh` and fetch it.
3. **Schema Analysis**: Read the first 100 lines of `locomo10.json` to understand the structure. Specifically identify the fields: `session_<num>_date_time`, `blip_caption`, and the `qa` list structure.

**Dependency Audit**: Inspect `requirements.txt`.
- **Action**: Run `pip install -r requirements.txt`.
- **Alert**: Be vigilant for `ValueError` related to the `datasets` library. If this occurs, note that you must load the JSON using Python's native `json` library, not Hugging Face's `load_dataset`.

**Baseline Understanding**:
1. Examine `scripts/evaluate_hf_llm.sh` and `task_eval/eval_qa.py`.
2. **Discovery**: Identify the function that computes the metrics (F1, ROUGE, LLM-Judge). You will need to import this function later to score "The System."
3. **Configuration**: Open `scripts/env.sh`. You must set `OPENAI_API_KEY` here, as the benchmark uses GPT-4 as a judge to evaluate the answers. Do not use a weaker model for the judge, as this will artificially lower our scores compared to the paper's baselines.

---

#### Phase 2: The Adapter Implementation Strategy

You cannot use the provided shell scripts directly because "The System" is not a standard Hugging Face model. You must discover how to write a custom execution script.

**Script Architecture**: Create a plan for a Python script named `run_custom_locomo.py`.

**Ingestion Logic (The "Streaming" Requirement)**:
- The benchmark requires sequential processing. You cannot bulk-ingest the file.
- **Instruction**: The script must iterate through the dataset: `Conversation -> Session -> Turn`.
- **Multimodality**: Check if "The System" supports images.
  - If YES: Extract `img_url` from the turn and pass it to `add_memory`.
  - If NO: Extract `blip_caption` and pass it as text context (e.g., `"[Visual Context: <caption_text>]"`) to `add_memory`.
- **Temporal Context**: Extract `session_<num>_date_time` and pass it in the metadata dictionary to `add_memory`. This is required for the "Temporal Reasoning" category tasks.

---

#### Phase 3: The Evaluation Loop

**Interleaved Testing**:
1. The script must pause ingestion when it finds a `qa` annotation associated with the current session/turn.
2. **Action**: Pass the `question` to `retrieve_answer(query)`.
3. **Limit Check**: Ensure "The System" does not use a hardcoded retrieval limit (like `k=42`) that might truncate necessary context. Use dynamic retrieval if possible.
4. **Capture**: Store the system's response, the ground truth answer, the `category` (e.g., "Multi-hop", "Temporal"), and the `evidence`.

---

#### Phase 4: Scoring and Reporting

**Metric Calculation**:
1. Use the imported scoring functions from `task_eval` to compare the system's response against the ground truth.
2. **Breakdown**: You must report scores by category. Global average is insufficient.
   - Temporal Score: (Baseline to beat: Zep ~79%)
   - Multi-hop Score: (Baseline to beat: Backboard ~75%)
   - Single-hop Score: (Baseline to beat: Memobase ~70%)

**Efficiency Metrics**:
1. Measure and report the average **Latency** (seconds per query).
2. Measure **Token Usage** (input tokens to the LLM per query).

---

#### Execution Command

"Claude, execute Phase 1 immediately. Once the environment is validated and the schema is parsed, generate the Python code for `run_custom_locomo.py` as described in Phase 2 and 3. Do not run the shell scripts; build the custom harness."

---

## 7. Troubleshooting and Failure Mode Analysis

Even with a perfect prompt, execution may fail due to specific technical pitfalls identified in the research.

### 7.1 The `ValueError` in Dataset Loading

The `locomo-mc10` dataset on Hugging Face has a known corruption or schema issue that causes `datasets.load_dataset` to fail with a `ValueError`. The autonomous agent might encounter this if it tries to use the standard loading method.

**Mitigation**: The prompt explicitly instructs the agent to use `json.load()` on the local file `data/locomo10.json`. This bypasses the Arrow/Parquet serialization layer that causes the crash.

### 7.2 Reproducibility and the "Judge" Variance

A scenario where a researcher successfully ran the code but got "significantly lower" scores than the paper. This is almost always due to the "Judge" model. The LoCoMo paper uses GPT-4. If the agent defaults to GPT-3.5-Turbo or a local model to save costs, the semantic evaluation will differ.

**Mitigation**: The prompt mandates checking `scripts/env.sh` and ensuring a high-fidelity model is set for the evaluator.

### 7.3 State Persistence Issues

A common implementation error is resetting the memory system after every session instead of every conversation.

**Impact**: If memory is cleared after Session 1, the system will fail all questions in Session 2 that reference Session 1.

**Mitigation**: The prompt clarifies the "Conversation -> Session" hierarchy and explicitly states that memory must persist across sessions within the same conversation ID.

## 8. Conclusion

The task of benchmarking a custom system on LoCoMo is a rigorous exercise in systems engineering. It requires navigating a complex repository, handling distinct failure modes related to dependency management and data schemas, and implementing a sophisticated "online" evaluation loop that respects the temporal nature of the data. By following the directives outlined in this report, the Claude Code agent will be equipped not just to run a script, but to "discover" the optimal integration path, bypassing the limitations of the default repository tools and delivering a high-fidelity assessment of the system's capabilities against the industry's leading baselines. The resulting data will provide a definitive answer to where the proprietary system stands in the competitive landscape of AI memory.
