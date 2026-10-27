"""FastAPI application for Supply Chain Sentinel."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Supply Chain Sentinel API",
    description="Agentic data platform for logistics ops",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}

@app.post("/query")
def query(question: str):
    """Natural language query → answer (agent-powered)."""
    # TODO: Week 3 - route to LangGraph agent
    return {"answer": "Agent not yet wired up. See Week 3 tasks.", "question": question}

@app.get("/anomalies")
def anomalies(window: str = "7d"):
    """Get recent anomalies."""
    return {"anomalies": [], "window": window}

@app.get("/reports/weekly")
def weekly_report():
    """Get the latest weekly ops report."""
    return {"report": "Report generation not yet implemented."}
