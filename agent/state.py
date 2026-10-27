"""Typed state for the LangGraph agent (kept for reference / future use)."""
from typing import TypedDict, Optional

class AgentState(TypedDict):
    question: str
    plan: str
    sql: Optional[str]
    result: Optional[str]
    answer: Optional[str]
    error: Optional[str]
   
