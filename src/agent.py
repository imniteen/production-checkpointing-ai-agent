"""
LangGraph-based customer service agent with PostgreSQL checkpointing.
Demonstrates durability, persistence, and HITL (Human-in-the-Loop) patterns.
"""
import os
import uuid
import logging
from datetime import datetime
from typing import Optional, Tuple

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from openai import AsyncOpenAI

from state import AgentState
from checkpointer import (
    get_checkpointer,
    save_to_elasticsearch,
    cleanup_checkpointer
)

load_dotenv()

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# ============================================================================
# Agent Nodes
# ============================================================================

async def triage_agent(state: AgentState) -> AgentState:
    """
    Classify user intent: faq, order, or human escalation.
    Detects anger, complexity, and routes accordingly.
    """
    user_message = state["user_message"]
    
    # Simple intent detection (use LLM in production)
    user_lower = user_message.lower()
    
    # Detect anger/escalation
    anger_keywords = ["angry", "furious", "unacceptable", "refund now", "cancel immediately"]
    if any(keyword in user_lower for keyword in anger_keywords):
        state["intent"] = "human"
        state["pending_action"] = "escalate"
        logger.info("🔥 Anger detected - escalating to human")
        return state
    
    # Detect order queries
    if "order" in user_lower or "#" in user_message:
        state["intent"] = "order"
        # Extract order ID (simple regex)
        import re
        match = re.search(r'#?(\d{5})', user_message)
        if match:
            state["order_id"] = match.group(1)
        logger.info(f"📦 Order query detected: {state.get('order_id')}")
        return state
    
    # Default to FAQ
    state["intent"] = "faq"
    logger.info("❓ FAQ query detected")
    return state


async def faq_agent(state: AgentState) -> AgentState:
    """Handle frequently asked questions using LLM."""
    user_message = state["user_message"]
    
    # Simple FAQ responses
    faq_db = {
        "return": "Our return policy allows returns within 30 days of purchase. Items must be unused and in original packaging.",
        "shipping": "Standard shipping takes 5-7 business days. Express shipping is available for 2-3 day delivery.",
        "payment": "We accept all major credit cards, PayPal, and Apple Pay.",
        "contact": "You can reach us at support@example.com or call 1-800-SUPPORT."
    }
    
    # Simple keyword matching (use LLM/RAG in production)
    reply = None
    for keyword, response in faq_db.items():
        if keyword in user_message.lower():
            reply = response
            break
    
    if not reply:
        reply = "I'd be happy to help! Could you please provide more details about your question?"
    
    state["draft_reply"] = reply
    logger.info(f"💬 FAQ response generated: {reply[:50]}...")
    return state


async def order_agent(state: AgentState) -> AgentState:
    """Handle order-related queries (check status, modify, cancel)."""
    order_id = state.get("order_id")
    user_message = state["user_message"]
    
    if not order_id:
        state["draft_reply"] = "I'd be happy to help with your order. Could you provide your order number? (Format: #12345)"
        return state
    
    # Simulate order lookup (use real database in production)
    mock_orders = {
        "12345": {"status": "In Transit", "delivery": "Thursday, Dec 12"},
        "67890": {"status": "Delivered", "delivery": "Dec 8"},
        "11111": {"status": "Processing", "delivery": "Dec 15"}
    }
    
    order_info = mock_orders.get(order_id)
    
    if not order_info:
        state["draft_reply"] = f"I couldn't find order #{order_id}. Please check the order number and try again."
    else:
        state["draft_reply"] = (
            f"Order #{order_id} status: **{order_info['status']}**\n"
            f"Expected delivery: {order_info['delivery']}\n\n"
            f"Is there anything else I can help you with?"
        )
    
    logger.info(f"📦 Order lookup complete: {order_id}")
    return state


async def human_agent(state: AgentState) -> AgentState:
    """
    Human escalation node (HITL).
    This node triggers interrupt_before, pausing execution.
    """
    state["draft_reply"] = (
        "🔔 **Your request has been escalated to a support engineer.**\n\n"
        "A human specialist will review your case and respond shortly.\n\n"
        "_Please provide any additional details that may help resolve your issue._"
    )
    
    state["awaiting_human_input"] = True
    
    logger.info(f"👨‍💼 Human escalation triggered for session: {state['session_id']}")
    return state


async def tone_agent(state: AgentState) -> AgentState:
    """
    Format final reply with appropriate tone using LLM.
    Adds empathy, professionalism, and clarity.
    """
    draft_reply = state.get("draft_reply", "I'm here to help!")
    user_message = state["user_message"]
    
    # Use LLM to improve tone (optional - can skip for demo)
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a friendly customer service agent. "
                        "Rewrite the draft reply with empathy and professionalism. "
                        "Keep it concise (2-3 sentences)."
                    )
                },
                {
                    "role": "user",
                    "content": f"Customer: {user_message}\n\nDraft reply: {draft_reply}"
                }
            ],
            max_tokens=150,
            temperature=0.7
        )
        
        final_reply = response.choices[0].message.content.strip()
        state["final_reply"] = final_reply
        logger.info("✨ Tone-adjusted reply generated")
        
    except Exception as e:
        logger.warning(f"Tone adjustment failed: {e}, using draft")
        state["final_reply"] = draft_reply
    
    return state


# ============================================================================
# Graph Building
# ============================================================================

def route_by_intent(state: AgentState) -> str:
    """Conditional edge: route based on triage intent."""
    intent = state.get("intent", "faq")
    logger.info(f"🔀 Routing to: {intent}")
    return intent


async def compile_graph():
    """
    Compile LangGraph workflow with PostgreSQL checkpointing.
    
    Returns:
        Compiled graph with interrupt_before human node
    """
    # Create workflow
    workflow = StateGraph(state_schema=AgentState)
    
    # Add nodes
    workflow.add_node("triage", triage_agent)
    workflow.add_node("faq", faq_agent)
    workflow.add_node("order", order_agent)
    workflow.add_node("human", human_agent)
    workflow.add_node("tone", tone_agent)
    
    # Add edges
    workflow.add_conditional_edges(
        source="triage",
        path=route_by_intent,
        path_map={
            "faq": "faq",
            "order": "order",
            "human": "human"
        }
    )
    workflow.add_edge("faq", "tone")
    workflow.add_edge("order", "tone")
    workflow.add_edge("tone", END)
    workflow.add_edge("human", END)  # Human node ends (awaits input)
    
    # Start node
    workflow.add_edge(START, "triage")
    
    # Get checkpointer
    checkpointer = await get_checkpointer()
    
    # Compile with checkpointer and HITL interrupt
    graph = workflow.compile(
        checkpointer=checkpointer,
        interrupt_before=["human"]  # CRITICAL: Pauses before human node
    )
    
    logger.info("✅ Graph compiled with PostgreSQL checkpointing")
    return graph


# ============================================================================
# Main Execution Function
# ============================================================================

async def run_conversation(
    user_id: str,
    user_message: str,
    session_id: Optional[str] = None
) -> Tuple[AgentState, str]:
    """
    Execute agent workflow with persistent state.
    
    Args:
        user_id: User identifier
        user_message: User's message
        session_id: Optional session ID for conversation continuity
    
    Returns:
        Tuple of (final_state, session_id)
    
    Example:
        # New conversation
        state, session_id = await run_conversation(
            user_id="customer-123",
            message="What's your return policy?",
            session_id=None
        )
        
        # Resume conversation
        state, _ = await run_conversation(
            user_id="customer-123",
            message="How long do I have?",
            session_id=session_id  # Same session
        )
    """
    # Generate session_id if not provided (new conversation)
    if session_id is None:
        session_id = str(uuid.uuid4())
        logger.info(f"🆕 New session: {session_id}")
    else:
        logger.info(f"🔄 Resuming session: {session_id}")
    
    # Create thread_id for checkpointing
    thread_id = f"{user_id}:{session_id}"
    
    config = {
        "configurable": {
            "thread_id": thread_id,
            "checkpoint_ns": "customer_service"
        }
    }
    
    # Get compiled graph
    graph = await compile_graph()
    
    # Check if we're resuming from a checkpoint
    checkpointer = await get_checkpointer()
    existing_state = await checkpointer.aget(config)
    
    if existing_state:
        logger.info(f"📂 Loaded checkpoint for session: {session_id}")
        
        # Resume: Only pass new user message (rest restored from DB)
        initial_state: AgentState = {
            "user_message": user_message,
            "user_id": user_id,
            "session_id": session_id,
            "messages": None,
            "intent": None,
            "order_id": None,
            "pending_action": None,
            "draft_reply": None,
            "final_reply": None,
            "awaiting_human_input": False,
            "resolved": None,
            "trace_id": None,
            "conversation_history": [],
            "created_at": None,
            "updated_at": None
        }
        
        # Append to conversation history
        if existing_state.get("values"):
            history = existing_state["values"].get("conversation_history", [])
            history.append({"role": "user", "content": user_message, "timestamp": datetime.utcnow().isoformat()})
            initial_state["conversation_history"] = history
            
    else:
        logger.info(f"🌱 Starting new conversation: {session_id}")
        
        # New: Initialize complete state
        initial_state: AgentState = {
            "user_id": user_id,
            "user_message": user_message,
            "session_id": session_id,
            "messages": None,
            "intent": None,
            "order_id": None,
            "pending_action": None,
            "draft_reply": None,
            "final_reply": None,
            "awaiting_human_input": False,
            "resolved": False,
            "trace_id": str(uuid.uuid4())[:8],
            "conversation_history": [
                {"role": "user", "content": user_message, "timestamp": datetime.utcnow().isoformat()}
            ],
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
    
    # Execute graph (checkpointed automatically after each node)
    try:
        result: AgentState = await graph.ainvoke(initial_state, config=config)
        
        # Add assistant response to history
        if result.get("final_reply"):
            result["conversation_history"].append({
                "role": "assistant",
                "content": result["final_reply"],
                "timestamp": datetime.utcnow().isoformat()
            })
        
        result["updated_at"] = datetime.utcnow().isoformat()
        
        # Check if we hit an interrupt (human node)
        if result.get("intent") == "human" and not result.get("final_reply"):
            result["awaiting_human_input"] = True
            result["final_reply"] = result.get("draft_reply", "Escalated to human support.")
            logger.info(f"⏸️  Conversation paused for HITL: {session_id}")
        else:
            result["awaiting_human_input"] = False
            result["resolved"] = True
            logger.info(f"✅ Conversation completed: {session_id}")
        
        # Index to Elasticsearch (async, non-blocking)
        await save_to_elasticsearch(result)
        
        return result, session_id
        
    except Exception as e:
        logger.error(f"❌ Graph execution failed: {e}", exc_info=True)
        
        # Return error state
        error_state: AgentState = {
            **initial_state,
            "final_reply": "I apologize, but I encountered an error. Please try again or contact support.",
            "resolved": False
        }
        return error_state, session_id


# ============================================================================
# Initialization & Cleanup
# ============================================================================

async def initialize():
    """Initialize all services (checkpointer, Elasticsearch)."""
    from checkpointer import init_checkpointer, init_elasticsearch
    
    logger.info("🚀 Initializing services...")
    await init_checkpointer()
    await init_elasticsearch()
    logger.info("✅ All services initialized")


async def shutdown():
    """Cleanup all services on shutdown."""
    logger.info("🛑 Shutting down services...")
    await cleanup_checkpointer()
    logger.info("✅ Shutdown complete")
