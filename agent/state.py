"""Typed state for the LangGraph agent."""
from pydantic import BaseModel
from typing import Optional
from enum import Enum

class AgentMode(str, Enum):
    NL_QUERY = "nl_query"
    ANOMALY = "anomaly"
    REPORT = "report"
    RAG = "rag"

class AgentState(BaseModel):
    question: str
    mode: AgentMode = AgentMode.NL_QUERY
    generated_sql: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    context: Optional[str] = None  # RAG context
