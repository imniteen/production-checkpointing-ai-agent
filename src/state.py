"""
State schema for customer service agent.
Defines the complete state structure persisted in PostgreSQL.
"""
from typing import TypedDict, Optional, Annotated
from langgraph.graph import add_messages


class AgentState(TypedDict):
    """
    Complete state schema for customer service agent.
    
    Every field is automatically persisted to PostgreSQL as JSONB.
    """
    
    # Core conversation data
    messages: Optional[Annotated[list[str], add_messages]]
    user_message: str
    user_id: str
    session_id: str
    
    # Business context
    intent: Optional[str]           # "faq" | "order" | "human"
    order_id: Optional[str]
    pending_action: Optional[str]   # "refund" | "cancel" | "escalate"
    
    # Agent workflow state
    draft_reply: Optional[str]
    final_reply: Optional[str]
    awaiting_human_input: bool
    resolved: Optional[bool]
    
    # Observability
    trace_id: Optional[str]
    conversation_history: list[dict]
    created_at: Optional[str]
    updated_at: Optional[str]
