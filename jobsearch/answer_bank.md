# Application Answer Bank — Ali Novruzov

Every answer below is supported by the resume. Rephrasing is allowed; invention is not.
Fields marked **[ASK ALI]** are left blank on purpose — the resume does not support them
and answering them would be fabrication.

---

## 1. Standard identity fields (safe to autofill)

| Field | Value |
|---|---|
| Full name | Ali Novruzov |
| Email | anovruzov0708@gmail.com |
| Phone | 713-572-7711 |
| Location | Katy, TX, United States |
| LinkedIn | https://linkedin.com/in/ali-novruzov |
| GitHub | https://github.com/anovruzov |
| School | The University of Texas at Austin |
| Degree | Bachelor of Science, Computer Science (Minor: Business) |
| Expected graduation | May 2027 |
| Currently enrolled? | Yes |
| GPA | **[ASK ALI]** — not on resume. Leave blank when optional; never estimate. |

## 2. Fields that must NOT be answered without Ali's explicit go-ahead

These appear on nearly every application and each one is a hard stop:

1. **Work authorization** — "Are you legally authorized to work in the US?" **[ASK ALI]**
2. **Sponsorship** — "Will you now or in the future require sponsorship?" **[ASK ALI]**
3. **Citizenship / national origin** — **[ASK ALI]**
4. **Security clearance held or eligibility** — **[ASK ALI]** (blocks Anduril and all defense reqs)
5. **Desired salary / compensation expectation** — **[ASK ALI]**
6. **Willingness to relocate** — **[ASK ALI]**
7. **Race, ethnicity, gender, veteran status, disability status** — do not answer.
   Select **"Decline to self-identify"** where offered. Ali has not authorized any value.
8. **Start-date availability** — depends on #6 and on whether he is targeting an internship
   during the school year vs. a post-May-2027 full-time start. **[ASK ALI]**

## 3. "Why this company / role?" — building blocks, not boilerplate

Compose from these true specifics. Never send the same paragraph to two companies.

**Evaluation / post-training companies (Arize, Scale, Surge, Snorkel, LLM-eval teams):**
> I spend my working hours doing the thing this team builds tooling for. At Mercor I write
> adversarial math problems — combinatorics, number theory, linear algebra, analysis — whose
> whole purpose is to break frontier model reasoning, each with an exact machine-verifiable
> answer and validated against live models before it ships. The part I find most interesting
> is the layer above the problems: the grading rubrics and structured error analyses that
> sort failures into logic vs. computation vs. consistency, because that taxonomy is what
> makes the data usable for post-training instead of just being a pile of hard questions.

**Agent-infrastructure / memory / RAG companies:**
> My project Mycelic (provisional patent 10479PPA) is a lineage-aware distributed memory
> system for agents. The premise is that agent memory fails silently: a fact stays in the
> store after the evidence that justified it has been invalidated. Mycelic tracks
> independent derivation paths per fact, detects when a fact has lost all its support, and
> selectively rebuilds those paths from independent sources rather than replicating
> everything. It is built on temporal and entity memory graphs with query-aware multi-hop
> retrieval, event sourcing, and NATS JetStream for replayable recovery.

**Applied-AI / forward-deployed / platform roles:**
> At Fujitsu I shipped an agent that ingests AT&T configuration and operational JSON and
> auto-detects mismatches, which took network troubleshooting from 48 hours to about 20
> minutes across thousands of devices a day. I also built a RAG pipeline that reads
> Statements of Work and drafts the corresponding Operational Level Agreements. The part
> that mattered as much as the code was adoption — I ran live demos and built reusable
> prompt libraries that got other engineering teams actually using the internal AI platform.

## 4. "Tell us about a technical challenge" — one true answer

> Silent forgetting in agent memory. If an agent stores a conclusion and later the evidence
> behind it is invalidated, nothing in a normal vector store notices — the conclusion just
> sits there and keeps getting retrieved. Universal replication is the brute-force answer
> and it is too expensive. What I built instead tracks each fact's independent derivation
> paths as first-class structure, so the system can tell the difference between a fact with
> three independent supports and a fact with three copies of one support. When supports
> drop below threshold, only that fact's missing paths get reconstructed. Getting the
> lineage bookkeeping to survive replay was the hard part, which is why it sits on event
> sourcing and NATS JetStream rather than mutable state.

## 5. Years-of-experience questions

State only what the dated entries support: **roughly 1.25 years of cumulative professional
AI/ML work** (Fujitsu Jun–Aug 2025; Mercor May 2026–present), plus the Mycelic project.
Where a form demands an integer and the true answer is 1, enter **1**. Do not round up.
If the req asks for 3–5+ years, apply anyway when the work matches (per Ali's instruction),
and let the cover note carry the argument — do not misstate the number to clear a filter.

## 6. Technologies — claim vs. do not claim

**Claim (on resume):** Python, C, C++, Java, Go, SQL, JavaScript, TypeScript, Bash, PyTorch,
LangChain, scikit-learn, Pandas, NumPy, Docker, Kubernetes, Linux, Git, AWS, Flask, Milvus,
Pinecone, NATS JetStream, RAG, knowledge graphs, embeddings, vector/semantic search,
multi-hop retrieval, RLHF, benchmark design, red teaming, multi-agent systems, event sourcing.

**Do NOT claim** (absent from resume, regardless of what the JD asks for): JAX, TensorFlow,
CUDA, Triton, Ray, Spark, Airflow, Terraform, GCP, Azure, vLLM, DeepSpeed, Kubeflow,
Snowflake, dbt, React, or any named internal framework.

## 7. Resume versions (same facts, reordered emphasis — all truthful)

- **v1-eval** — leads with Mercor eval/post-training; for Arize, Scale, Surge, Snorkel, OpenAI.
- **v1-agents** — leads with Mycelic + the Fujitsu agent; for Cohere, agent-infra startups.
- **v1-swe** — leads with distributed systems / NATS / K8s coursework; for generalist SWE reqs.

No version may add, remove, or alter a fact. Reordering and emphasis only.
