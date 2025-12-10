"""
Hybrid checkpointer implementation: PostgreSQL + Elasticsearch.
PostgreSQL: Primary durable storage (source of truth)
Elasticsearch: Secondary searchable index (analytics, search)
"""
import os
import logging
from typing import Optional
from datetime import datetime
import json

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.memory import MemorySaver

# Elasticsearch imports (optional)
try:
    from elasticsearch import AsyncElasticsearch
    ES_AVAILABLE = True
except ImportError:
    ES_AVAILABLE = False
    logging.warning("Elasticsearch not installed. Search functionality disabled.")

from state import AgentState

logger = logging.getLogger(__name__)

# Global singleton instances
_checkpointer = None
_checkpointer_context = None
_es_client = None


async def init_checkpointer():
    """
    Initialize PostgreSQL checkpointer with graceful fallback.
    
    Returns:
        AsyncPostgresSaver or MemorySaver
    """
    global _checkpointer, _checkpointer_context
    
    if _checkpointer is not None:
        return _checkpointer
    
    try:
        # Build PostgreSQL connection string
        pg_user = os.getenv("POSTGRES_USER", "agent_app")
        pg_password = os.getenv("POSTGRES_PASSWORD", "secure_password")
        pg_host = os.getenv("POSTGRES_HOST", "localhost")
        pg_port = os.getenv("POSTGRES_PORT", "5432")
        pg_db = os.getenv("POSTGRES_DB", "agent_state")
        
        connection_string = (
            f"postgresql://{pg_user}:{pg_password}@"
            f"{pg_host}:{pg_port}/{pg_db}"
        )
        
        logger.info(f"Initializing PostgreSQL checkpointer: {pg_host}:{pg_port}/{pg_db}")
        
        # Create context manager and enter it
        _checkpointer_context = AsyncPostgresSaver.from_conn_string(
            connection_string
        )
        _checkpointer = await _checkpointer_context.__aenter__()
        
        # Create tables if they don't exist
        await _checkpointer.setup()
        
        logger.info("✅ PostgreSQL checkpointer initialized successfully")
        return _checkpointer
        
    except Exception as e:
        logger.error(f"❌ PostgreSQL initialization failed: {e}")
        logger.warning("⚠️  Falling back to in-memory checkpointer (MemorySaver)")
        
        # Graceful fallback for development
        _checkpointer = MemorySaver()
        _checkpointer_context = None
        return _checkpointer


async def init_elasticsearch():
    """
    Initialize Elasticsearch client (optional).
    
    Returns:
        AsyncElasticsearch or None
    """
    global _es_client
    
    if not ES_AVAILABLE:
        logger.warning("Elasticsearch not available - skipping initialization")
        return None
    
    if _es_client is not None:
        return _es_client
    
    try:
        es_url = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
        _es_client = AsyncElasticsearch([es_url])
        
        # Test connection
        info = await _es_client.info()
        logger.info(f"✅ Elasticsearch connected: {info['version']['number']}")
        
        # Create index if it doesn't exist
        await create_conversation_index()
        
        return _es_client
        
    except Exception as e:
        logger.warning(f"⚠️  Elasticsearch initialization failed: {e}")
        logger.warning("Search functionality will be disabled")
        _es_client = None
        return None


async def create_conversation_index():
    """Create Elasticsearch index for conversations with proper mapping."""
    if not _es_client:
        return
    
    index_name = "agent_conversations"
    
    try:
        # Check if index exists
        exists = await _es_client.indices.exists(index=index_name)
        if exists:
            logger.info(f"Index '{index_name}' already exists")
            return
        
        # Create index with mapping
        mapping = {
            "mappings": {
                "properties": {
                    "session_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "intent": {"type": "keyword"},
                    "order_id": {"type": "keyword"},
                    "resolved": {"type": "boolean"},
                    "awaiting_human_input": {"type": "boolean"},
                    "messages": {"type": "text", "analyzer": "standard"},
                    "conversation_history": {"type": "object"},
                    "timestamp": {"type": "date"},
                    "trace_id": {"type": "keyword"}
                }
            }
        }
        
        await _es_client.indices.create(index=index_name, body=mapping)
        logger.info(f"✅ Created Elasticsearch index: {index_name}")
        
    except Exception as e:
        logger.error(f"Failed to create Elasticsearch index: {e}")


async def get_checkpointer():
    """Get or initialize the checkpointer instance."""
    if _checkpointer is None:
        return await init_checkpointer()
    return _checkpointer


async def get_elasticsearch():
    """Get or initialize Elasticsearch client."""
    if _es_client is None and ES_AVAILABLE:
        return await init_elasticsearch()
    return _es_client


async def cleanup_checkpointer():
    """Cleanup resources on shutdown."""
    global _checkpointer, _checkpointer_context, _es_client
    
    # Close PostgreSQL connections
    if _checkpointer_context:
        logger.info("Closing PostgreSQL connections...")
        try:
            await _checkpointer_context.__aexit__(None, None, None)
            logger.info("✅ PostgreSQL cleanup complete")
        except Exception as e:
            logger.error(f"Error during PostgreSQL cleanup: {e}")
    
    # Close Elasticsearch connections
    if _es_client:
        logger.info("Closing Elasticsearch connections...")
        try:
            await _es_client.close()
            logger.info("✅ Elasticsearch cleanup complete")
        except Exception as e:
            logger.error(f"Error during Elasticsearch cleanup: {e}")
    
    _checkpointer = None
    _checkpointer_context = None
    _es_client = None


async def save_to_elasticsearch(state: AgentState):
    """
    Index conversation to Elasticsearch for searchability.
    Non-blocking - failures don't affect primary storage.
    """
    es_client = await get_elasticsearch()
    if not es_client:
        return  # Elasticsearch not available
    
    try:
        document = {
            "session_id": state.get("session_id"),
            "user_id": state.get("user_id"),
            "intent": state.get("intent"),
            "order_id": state.get("order_id"),
            "resolved": state.get("resolved", False),
            "awaiting_human_input": state.get("awaiting_human_input", False),
            "messages": " ".join([
                msg.get("content", "") 
                for msg in state.get("conversation_history", [])
            ]),
            "conversation_history": state.get("conversation_history", []),
            "timestamp": datetime.utcnow().isoformat(),
            "trace_id": state.get("trace_id")
        }
        
        await es_client.index(
            index="agent_conversations",
            id=state.get("session_id"),
            document=document
        )
        
        logger.debug(f"📊 Indexed to Elasticsearch: {state.get('session_id')}")
        
    except Exception as e:
        # Don't fail the request if Elasticsearch indexing fails
        logger.warning(f"Elasticsearch indexing failed: {e}")


async def search_conversations(
    query: str,
    user_id: Optional[str] = None,
    intent: Optional[str] = None,
    resolved: Optional[bool] = None,
    limit: int = 10
):
    """
    Search across historical conversations.
    
    Args:
        query: Search text (searches messages)
        user_id: Filter by user (optional)
        intent: Filter by intent (optional)
        resolved: Filter by resolution status (optional)
        limit: Max results to return
    
    Returns:
        List of matching conversations
    """
    es_client = await get_elasticsearch()
    if not es_client:
        logger.warning("Elasticsearch not available - search disabled")
        return []
    
    try:
        # Build query
        must_clauses = []
        
        if query:
            must_clauses.append({
                "match": {
                    "messages": {
                        "query": query,
                        "operator": "and"
                    }
                }
            })
        
        filter_clauses = []
        if user_id:
            filter_clauses.append({"term": {"user_id": user_id}})
        if intent:
            filter_clauses.append({"term": {"intent": intent}})
        if resolved is not None:
            filter_clauses.append({"term": {"resolved": resolved}})
        
        search_body = {
            "query": {
                "bool": {
                    "must": must_clauses if must_clauses else [{"match_all": {}}],
                    "filter": filter_clauses
                }
            },
            "size": limit,
            "sort": [{"timestamp": {"order": "desc"}}]
        }
        
        response = await es_client.search(
            index="agent_conversations",
            body=search_body
        )
        
        results = [hit["_source"] for hit in response["hits"]["hits"]]
        logger.info(f"🔍 Search found {len(results)} results for query: {query}")
        
        return results
        
    except Exception as e:
        logger.error(f"Elasticsearch search failed: {e}")
        return []


async def get_user_statistics(user_id: str):
    """
    Get conversation statistics for a user.
    
    Returns:
        dict with total_conversations, resolved_count, avg_messages, etc.
    """
    es_client = await get_elasticsearch()
    if not es_client:
        return {}
    
    try:
        # Aggregation query
        agg_body = {
            "query": {"term": {"user_id": user_id}},
            "size": 0,
            "aggs": {
                "total_conversations": {"value_count": {"field": "session_id"}},
                "resolved_count": {
                    "filter": {"term": {"resolved": True}}
                },
                "intents": {
                    "terms": {"field": "intent"}
                }
            }
        }
        
        response = await es_client.search(
            index="agent_conversations",
            body=agg_body
        )
        
        aggs = response["aggregations"]
        
        return {
            "total_conversations": aggs["total_conversations"]["value"],
            "resolved_count": aggs["resolved_count"]["doc_count"],
            "intents": [
                {"intent": bucket["key"], "count": bucket["doc_count"]}
                for bucket in aggs["intents"]["buckets"]
            ]
        }
        
    except Exception as e:
        logger.error(f"Failed to get user statistics: {e}")
        return {}
