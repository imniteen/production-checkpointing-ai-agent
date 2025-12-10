# Production-Grade State Management Demo

A standalone implementation demonstrating durability, persistence, and state management for LangGraph-based AI agents.

## 🎯 What This Demonstrates

This is a **complete, production-ready** implementation showing:

- ✅ **PostgreSQL checkpointing** with LangGraph for durable state
- ✅ **Human-in-the-Loop (HITL)** interrupt/resume patterns
- ✅ **Hybrid architecture** with Elasticsearch for searchability
- ✅ **Connection pooling** and lifecycle management
- ✅ **OpenTelemetry observability** with trace correlation
- ✅ **Graceful fallbacks** and error handling
- ✅ **Multi-turn conversations** with session continuity

**Read the full article:** [article.md](./article.md)

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.10+**
- **Docker Desktop** (for PostgreSQL and Elasticsearch)
- **OpenAI API Key**

### 1. Start Infrastructure Services

```powershell
# Start PostgreSQL and Elasticsearch
docker-compose up -d

# Verify services are running
docker-compose ps
```

**Services:**
- PostgreSQL: `localhost:5432` (state storage)
- Elasticsearch: `localhost:9200` (conversation search)

### 2. Install Python Dependencies

```powershell
# Create virtual environment
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Environment

```powershell
# Copy environment template
copy .env.example .env

# Edit .env and add your OpenAI API key
notepad .env
```

**Required variables:**
```env
OPENAI_API_KEY=sk-...
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=agent_state
POSTGRES_USER=agent_app
POSTGRES_PASSWORD=secure_password
ELASTICSEARCH_URL=http://localhost:9200
```

### 4. Run the Demo

```powershell
# Interactive CLI mode
python main.py

# Or run specific test
python main.py --test-hitl
```

---

## 📋 Usage Examples

### Example 1: Basic Conversation

```python
from agent import run_conversation

# New conversation
state, session_id = await run_conversation(
    user_id="customer-123",
    message="What's your return policy?",
    session_id=None  # New session
)

print(f"Agent: {state['final_reply']}")
print(f"Session ID: {session_id}")
```

### Example 2: HITL Workflow (Interrupt & Resume)

```python
# Step 1: Trigger HITL escalation
state1, session_id = await run_conversation(
    user_id="customer-123",
    message="I'm extremely angry! Refund my order NOW!",
    session_id=None
)

print(f"Awaiting human: {state1['awaiting_human_input']}")  # True
print(f"Session ID: {session_id}")

# Simulate backend restart (state persists in PostgreSQL)
# ...

# Step 2: Human provides approval, resume conversation
state2, _ = await run_conversation(
    user_id="customer-123",
    message="Approved: Issue full refund for order #12345",
    session_id=session_id  # Same session - resumes from checkpoint
)

print(f"Agent: {state2['final_reply']}")
print(f"Resolved: {state2.get('resolved', False)}")
```

### Example 3: Multi-Turn Conversation

```python
session_id = None

# Turn 1
state, session_id = await run_conversation(
    user_id="customer-123",
    message="I need help with order #12345",
    session_id=session_id
)

# Turn 2 (context preserved)
state, session_id = await run_conversation(
    user_id="customer-123",
    message="When will it arrive?",  # Agent knows order_id from Turn 1
    session_id=session_id
)

# Turn 3 (full history available)
state, session_id = await run_conversation(
    user_id="customer-123",
    message="Can I change the delivery address?",
    session_id=session_id
)

print(f"Conversation history: {len(state['conversation_history'])} messages")
```

### Example 4: Search Conversations (Elasticsearch)

```python
from checkpointer import search_conversations

# Search for similar issues
results = await search_conversations(
    query="refund delayed order",
    user_id="customer-123"
)

for hit in results:
    print(f"Session: {hit['session_id']}")
    print(f"Intent: {hit['intent']}")
    print(f"Resolved: {hit['resolved']}")
```

---

## 🏗️ Architecture

```
┌──────────────┐
│   main.py    │  ← CLI interface
└──────┬───────┘
       │
       ▼
┌──────────────┐      ┌────────────────┐
│   agent.py   │─────▶│ checkpointer.py│
│  (LangGraph) │      │  (Hybrid Store)│
└──────────────┘      └────────┬───────┘
                               │
                    ┌──────────┴──────────┐
                    ▼                     ▼
           ┌─────────────────┐   ┌──────────────┐
           │   PostgreSQL    │   │Elasticsearch │
           │  (Durability)   │   │(Searchability)│
           └─────────────────┘   └──────────────┘
```

---

## 📁 File Structure

```
technical-article/
├── README.md                 # This file
├── article.md               # Technical blog post
├── .env.example            # Environment template
├── .gitignore              # Git ignore patterns
├── requirements.txt        # Python dependencies
├── docker-compose.yml      # PostgreSQL + Elasticsearch
│
├── main.py                 # CLI entry point
├── agent.py                # LangGraph workflow implementation
├── checkpointer.py         # Hybrid storage (PostgreSQL + ES)
├── state.py                # State schema definition
│
└── DEPLOYMENT.md           # Production deployment guide
```

---

## 🔍 Key Implementation Details

### 1. PostgreSQL Checkpointer (Durability)

```python
# From checkpointer.py
async def init_checkpointer():
    """Initialize with graceful fallback."""
    try:
        connection_string = f"postgresql://{user}:{password}@{host}:{port}/{db}"
        context = AsyncPostgresSaver.from_conn_string(connection_string)
        checkpointer = await context.__aenter__()
        await checkpointer.setup()  # Auto-create tables
        return checkpointer
    except Exception as e:
        logger.warning("Falling back to MemorySaver")
        return MemorySaver()
```

### 2. HITL Interrupt Pattern

```python
# From agent.py
graph = workflow.compile(
    checkpointer=checkpointer,
    interrupt_before=["human"]  # Pauses here, saves to PostgreSQL
)
```

### 3. Session Management

```python
# From agent.py
thread_id = f"{user_id}:{session_id}"
config = {"configurable": {"thread_id": thread_id}}

# Check if resuming
existing_state = await checkpointer.aget(config)
if existing_state:
    # Resume: minimal state (rest auto-loaded)
    initial = {"user_message": message}
else:
    # New: full initialization
    initial = {"user_id": user_id, "session_id": session_id, ...}
```

### 4. Hybrid Storage

```python
# From checkpointer.py
async def save_state_hybrid(state, config):
    # 1. Primary: PostgreSQL (must succeed)
    await checkpointer.aput(config, state)
    
    # 2. Secondary: Elasticsearch (can fail)
    try:
        await es_client.index(index="conversations", document=state)
    except Exception as e:
        logger.warning(f"ES indexing failed: {e}")
        # Don't fail the request
```

---

## 🧪 Testing

### Run All Tests

```powershell
python main.py --test-all
```

### Individual Tests

```powershell
# Test basic conversation
python main.py --test-basic

# Test HITL workflow
python main.py --test-hitl

# Test durability (restart simulation)
python main.py --test-durability

# Test search functionality
python main.py --test-search
```

---

## 📊 Database Queries

### Inspect PostgreSQL State

```powershell
# Connect to PostgreSQL
docker exec -it agent-postgres psql -U agent_app -d agent_state

# List all sessions
SELECT thread_id, checkpoint_id, created_at 
FROM checkpoints 
ORDER BY created_at DESC 
LIMIT 10;

# View specific session state
SELECT checkpoint 
FROM checkpoints 
WHERE thread_id = 'customer-123:your-session-id' 
ORDER BY created_at DESC 
LIMIT 1;

# Find interrupted sessions (HITL)
SELECT 
    thread_id,
    checkpoint->>'intent' as intent,
    checkpoint->>'awaiting_human_input' as awaiting_human,
    created_at
FROM checkpoints
WHERE checkpoint->>'awaiting_human_input' = 'true'
ORDER BY created_at DESC;
```

### Query Elasticsearch

```powershell
# Search conversations
curl -X GET "localhost:9200/conversations/_search?pretty" -H 'Content-Type: application/json' -d'
{
  "query": {
    "match": {
      "messages": "refund"
    }
  }
}
'

# Get conversation by session_id
curl -X GET "localhost:9200/conversations/_search?pretty" -H 'Content-Type: application/json' -d'
{
  "query": {
    "term": {
      "session_id": "your-session-id"
    }
  }
}
'
```

---

## 🔧 Troubleshooting

### PostgreSQL Connection Failed

```powershell
# Check if container is running
docker-compose ps

# View logs
docker-compose logs postgres

# Restart services
docker-compose restart postgres

# Test connection
docker exec -it agent-postgres psql -U agent_app -d agent_state -c "SELECT 1;"
```

### Elasticsearch Not Indexing

```powershell
# Check Elasticsearch health
curl http://localhost:9200/_cluster/health?pretty

# View indices
curl http://localhost:9200/_cat/indices?v

# Check logs
docker-compose logs elasticsearch
```

### Session Not Persisting

1. Verify PostgreSQL checkpointer initialized successfully (check logs)
2. Ensure session_id is being passed correctly
3. Check database for checkpoint entries: `SELECT * FROM checkpoints;`

### HITL Not Interrupting

1. Verify `interrupt_before=["human"]` in graph compilation
2. Check intent routing logic in `agent.py`
3. Ensure state includes `intent: "human"` before Human node

---

## 🌐 Production Deployment

See [DEPLOYMENT.md](./DEPLOYMENT.md) for:
- Cloud deployment (AWS, Azure, GCP)
- Managed PostgreSQL setup
- Elasticsearch cluster configuration
- Security hardening
- Monitoring and alerting
- Scaling strategies

---

## 📚 Learn More

### Documentation

- **LangGraph Checkpointers:** https://langchain-ai.github.io/langgraph/reference/checkpoints/
- **AsyncPG (PostgreSQL driver):** https://magicstack.github.io/asyncpg/
- **Elasticsearch Python Client:** https://elasticsearch-py.readthedocs.io/

### Related Articles

- [Building Production AI Agents](./article.md) - Full technical deep dive
- [State Management Patterns](#) - Design patterns
- [HITL Best Practices](#) - Human-in-the-loop workflows

---

## 🤝 Contributing

This is a reference implementation for educational purposes. Feel free to:
- Fork and adapt for your use case
- Submit issues for bugs or questions
- Share improvements via pull requests

---

## 📄 License

MIT License - See LICENSE file for details

---

## 🙋 Support

Having issues? Check:
1. [Troubleshooting section](#-troubleshooting) in this README
2. Docker logs: `docker-compose logs`
3. Application logs: Look for ERROR/WARNING messages
4. [GitHub Issues](#) for known problems

---

**Built with ❤️ for production AI systems**
