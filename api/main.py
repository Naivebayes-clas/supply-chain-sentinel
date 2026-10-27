"""
FastAPI application for Supply Chain Sentinel.
Exposes the LangGraph agent as a REST API.
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os
import sys

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

app = FastAPI(
    title="Supply Chain Sentinel API",
    description="Agentic data platform for logistics ops",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    question: str
    answer: str
    sql: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.2.0"}


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    """Natural language query → agent-powered answer."""
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    try:
        from agent.graph import ask, build_graph
        answer = ask(req.question)
        return QueryResponse(question=req.question, answer=answer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")


@app.get("/anomalies")
def anomalies(window: str = "7d"):
    """Get recent anomalies (from Gold exceptions table)."""
    try:
        from agent.tools.mcp_server import get_exceptions
        return {"anomalies": get_exceptions(), "window": window}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/reports/weekly")
def weekly_report():
    """Get the latest weekly ops summary."""
    try:
        from agent.graph import ask
        report = ask("Give me a summary of this week's shipment performance: on-time rates by carrier, total volume, and any exceptions.")
        return {"report": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/schema")
def schema():
    """Get the Gold table schema."""
    try:
        from agent.tools.mcp_server import get_schema
        return {"schema": get_schema()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
   
