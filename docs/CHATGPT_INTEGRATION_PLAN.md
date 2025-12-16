# ChatGPT Integration Plan for MemMachine

## Overview

This plan outlines how to integrate MemMachine's 4D Tesseract memory system with ChatGPT, enabling persistent user memory across conversations.

---

## Integration Options

### Option 1: Custom GPT with Actions (Recommended)

**Best for:** End users who want a memory-enhanced ChatGPT experience

**How it works:**
- Create a Custom GPT in ChatGPT
- Define Actions that call your MemMachine REST API
- GPT automatically stores/retrieves memories during conversation

**Pros:**
- Native ChatGPT experience
- No code required for end users
- Works with ChatGPT Plus/Teams/Enterprise

**Cons:**
- Requires hosting MemMachine server publicly (or via tunnel)
- Limited to ChatGPT's action schema

---

### Option 2: Assistants API with Function Calling

**Best for:** Developers building custom applications

**How it works:**
- Use OpenAI's Assistants API programmatically
- Define functions that map to MemMachine endpoints
- Your backend orchestrates memory operations

**Pros:**
- Full control over memory logic
- Can run MemMachine locally
- Better for production applications

**Cons:**
- Requires custom code
- More complex setup

---

### Option 3: Middleware Proxy

**Best for:** Existing applications that already use ChatGPT

**How it works:**
- Proxy sits between your app and OpenAI API
- Automatically injects memory context into prompts
- Stores conversation turns after each response

**Pros:**
- Transparent to existing code
- Maximum flexibility

**Cons:**
- Additional infrastructure
- Latency overhead

---

## Recommended Implementation: Custom GPT with Actions

### Step 1: Expose MemMachine API Publicly

**Option A: ngrok (Development)**
```bash
# Start MemMachine server
python -m memmachine.server.app --port 8080

# In another terminal, expose via ngrok
ngrok http 8080
```

**Option B: Cloud Deployment (Production)**
- Deploy to AWS/GCP/Azure
- Use HTTPS with valid certificate
- Add authentication (API key in headers)

### Step 2: Create OpenAPI Specification for ChatGPT

Create an OpenAPI spec that ChatGPT can consume:

```yaml
openapi: 3.1.0
info:
  title: MemMachine Memory API
  description: Persistent memory system for ChatGPT
  version: 1.0.0
servers:
  - url: https://your-memmachine-server.com/api/v2
paths:
  /memories:
    post:
      operationId: addMemory
      summary: Store a new memory
      description: Save important information from the conversation
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - org_id
                - project_id
                - messages
              properties:
                org_id:
                  type: string
                  description: Organization identifier (use 'chatgpt')
                project_id:
                  type: string
                  description: User identifier (use conversation_id or user_id)
                messages:
                  type: array
                  items:
                    type: object
                    properties:
                      content:
                        type: string
                        description: The memory content to store
                      producer:
                        type: string
                        enum: [user, assistant]
                      role:
                        type: string
                        enum: [user, assistant]
                      metadata:
                        type: object
                        description: Optional metadata (category, importance, etc.)
      responses:
        '200':
          description: Memory stored successfully

  /memories/search:
    post:
      operationId: searchMemory
      summary: Search stored memories
      description: Retrieve relevant memories based on a query
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - org_id
                - project_id
                - query
              properties:
                org_id:
                  type: string
                project_id:
                  type: string
                query:
                  type: string
                  description: Search query to find relevant memories
                top_k:
                  type: integer
                  default: 5
                  description: Number of results to return
                types:
                  type: array
                  items:
                    type: string
                    enum: [episodic, semantic]
                  default: [episodic, semantic]
      responses:
        '200':
          description: Search results
          content:
            application/json:
              schema:
                type: object
                properties:
                  results:
                    type: array
                    items:
                      type: object
                      properties:
                        content:
                          type: string
                        score:
                          type: number
                        timestamp:
                          type: string

  /projects:
    post:
      operationId: createProject
      summary: Initialize memory for a new user
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required:
                - org_id
                - project_id
              properties:
                org_id:
                  type: string
                project_id:
                  type: string
                config:
                  type: object
                  properties:
                    embedder:
                      type: string
                      default: default
                    reranker:
                      type: string
                      default: default
      responses:
        '200':
          description: Project created
```

### Step 3: Create the Custom GPT

1. Go to https://chat.openai.com/gpts/editor
2. Create new GPT with these settings:

**Name:** MemMachine Assistant (or your preferred name)

**Description:** A ChatGPT with persistent memory that remembers your preferences, facts, and conversation history.

**Instructions (System Prompt):**
```
You are an AI assistant with persistent memory capabilities. You can remember information across conversations.

## Memory Guidelines

### When to Store Memories
Store memories when the user:
- Shares personal preferences (favorite foods, colors, hobbies)
- Mentions important facts (job, location, family members, pets)
- Asks you to remember something explicitly
- Shares goals, plans, or recurring tasks
- Provides context they want you to recall later

### When to Search Memories
Search memories when:
- Starting a new conversation (search for user context)
- The user asks "do you remember..."
- You need context about the user's preferences
- The topic relates to something discussed before
- Making recommendations or suggestions

### Memory Format
When storing memories, use clear, factual statements:
- Good: "User's favorite programming language is Python"
- Bad: "The user mentioned something about Python"

### User ID
Use the conversation_id as the project_id to maintain per-user memory isolation.
Always use "chatgpt" as the org_id.

### Privacy
- Never store sensitive information (passwords, SSN, financial details)
- Inform users when you're storing important information
- Users can ask you to forget specific information
```

**Actions:** Import the OpenAPI spec from Step 2

**Authentication:** Configure API key authentication if your server requires it

### Step 4: Memory Flow Implementation

```
┌─────────────────────────────────────────────────────────┐
│                    User Message                         │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  1. Search Memory (automatic on conversation start)     │
│     POST /memories/search                               │
│     Query: "user context and preferences"               │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  2. Process with Context                                │
│     GPT uses retrieved memories in response             │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  3. Store New Memories (if relevant)                    │
│     POST /memories                                      │
│     Content: extracted facts/preferences                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                    GPT Response                         │
└─────────────────────────────────────────────────────────┘
```

---

## Alternative: Assistants API Integration

For programmatic control, use the Assistants API:

### Function Definitions

```python
tools = [
    {
        "type": "function",
        "function": {
            "name": "store_memory",
            "description": "Store important information to remember",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The fact or information to remember"
                    },
                    "category": {
                        "type": "string",
                        "enum": ["preference", "fact", "goal", "context"],
                        "description": "Category of memory"
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search for relevant stored memories",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to search for in memories"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    }
]
```

### Integration Code

```python
# chatgpt_integration.py
import openai
from memmachine import MemMachineClient

class ChatGPTWithMemory:
    def __init__(self, user_id: str):
        self.openai = openai.OpenAI()
        self.memory = MemMachineClient(base_url="http://localhost:8080")
        self.project = self.memory.get_or_create_project(
            org_id="chatgpt",
            project_id=user_id
        )
        self.user_memory = self.project.memory(user_id=user_id)

    def chat(self, message: str) -> str:
        # 1. Search for relevant context
        context = self.user_memory.search(message, limit=5)

        # 2. Build system prompt with memory context
        memory_context = "\n".join([
            f"- {m['content']}" for m in context.get('results', [])
        ])

        system_prompt = f"""You are a helpful assistant with memory.

Known information about this user:
{memory_context if memory_context else "No prior information stored."}

Use this context to personalize your responses."""

        # 3. Get response from ChatGPT
        response = self.openai.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message}
            ],
            tools=tools,
            tool_choice="auto"
        )

        # 4. Handle function calls (memory operations)
        message_response = response.choices[0].message

        if message_response.tool_calls:
            for tool_call in message_response.tool_calls:
                if tool_call.function.name == "store_memory":
                    args = json.loads(tool_call.function.arguments)
                    self.user_memory.add(
                        content=args["content"],
                        role="user",
                        metadata={"category": args.get("category", "general")}
                    )
                elif tool_call.function.name == "search_memory":
                    args = json.loads(tool_call.function.arguments)
                    results = self.user_memory.search(
                        args["query"],
                        limit=args.get("limit", 5)
                    )
                    # Continue conversation with results...

        return message_response.content

# Usage
assistant = ChatGPTWithMemory(user_id="user_123")
response = assistant.chat("I love hiking in the mountains")
print(response)
```

---

## Implementation Checklist

### Phase 1: Server Setup
- [ ] Deploy MemMachine server
- [ ] Configure HTTPS and authentication
- [ ] Test API endpoints manually
- [ ] Set up monitoring/logging

### Phase 2: ChatGPT Integration
- [ ] Create OpenAPI specification
- [ ] Build Custom GPT
- [ ] Configure Actions with API endpoints
- [ ] Test memory storage/retrieval

### Phase 3: Testing & Refinement
- [ ] Test multi-turn conversations
- [ ] Verify memory persistence across sessions
- [ ] Tune retrieval parameters (top_k, thresholds)
- [ ] Add rate limiting if needed

### Phase 4: Production
- [ ] Add API key authentication
- [ ] Implement user isolation
- [ ] Set up backup/recovery
- [ ] Document for end users

---

## Security Considerations

1. **Authentication**: Always use API keys for production
2. **User Isolation**: Use unique project_id per user
3. **Data Privacy**: Don't store sensitive PII
4. **Rate Limiting**: Prevent abuse
5. **HTTPS**: Always use TLS in production

---

## Files to Create/Modify

| File | Purpose |
|------|---------|
| `integrations/chatgpt/openapi.yaml` | OpenAPI spec for Custom GPT |
| `integrations/chatgpt/assistant.py` | Assistants API integration |
| `integrations/chatgpt/middleware.py` | Optional proxy middleware |
| `docker-compose.chatgpt.yml` | Deployment configuration |
