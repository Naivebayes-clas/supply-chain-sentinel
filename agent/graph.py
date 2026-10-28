"""
LangGraph Agent: routes user questions to the appropriate tool/node.

Graph structure:
    [START] → [planner] → [nl_query] → [execute_query] → [respond] → [END]
                         → [carrier_check] → [respond] → [END]
                         → [exception_check] → [respond] → [END]
                         → [anomaly_check] → [respond] → [END]
                         → [rag_lookup] → [respond] → [END]
                         → [schema_info] → [respond] → [END]
"""
import json
import re
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
import os


# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
class AgentState(TypedDict):
    question: str
    plan: str
    sql: Optional[str]
    result: Optional[str]
    context: Optional[str]       # RAG context
    answer: Optional[str]
    error: Optional[str]


# ──────────────────────────────────────────────
# LLM
# ──────────────────────────────────────────────
def get_llm():
    return ChatOpenAI(
        model="gpt-4o",
        temperature=0,
        api_key=os.environ.get("OPENAI_API_KEY"),
    )


# ──────────────────────────────────────────────
# Node: Planner
# ──────────────────────────────────────────────
def planner(state: AgentState) -> dict:
    """Decide which tool handles the question."""
    q = state["question"].lower()

    if any(kw in q for kw in ["schema", "table", "column", "field", "structure", "what tables"]):
        plan = "schema_info"
    elif any(kw in q for kw in ["anomaly", "anomal", "outlier", "spike", "deviation", "z-score", "unusual"]):
        plan = "anomaly_check"
    elif any(kw in q for kw in ["segment", "format", "spec", "required field", "what is an 85", "what is a 214", "edi spec", "how to parse"]):
        plan = "rag_lookup"
    elif any(kw in q for kw in ["carrier", "on-time rate", "freight cost", "performance", "which carrier"]):
        plan = "carrier_check"
    elif any(kw in q for kw in ["exception", "late", "mississauga", "burlington", "delay", "problem", "where are"]):
        plan = "exception_check"
    else:
        plan = "nl_query"

    return {"plan": plan}


# ──────────────────────────────────────────────
# Node: NL → SQL
# ──────────────────────────────────────────────
def nl_query(state: AgentState) -> dict:
    """Generate SQL from natural language."""
    llm = get_llm()
    schema = """
Available tables:
- kpis_daily: date, total_shipments, tracked_shipments, on_time_rate, avg_weight_kg
- carrier_perf: carrier_name, total_shipments, tracked_shipments, on_time_rate, avg_freight_cost, total_freight_cost
- exceptions_by_dest: ship_to_city, late_count, affected_pos
- volume_trend: week, transaction_type, count
"""
    prompt = f"""You are a SQL expert for a logistics data platform.
Given the schema below, write a single SELECT query that answers the user's question.
Return ONLY the SQL, no explanation.

Schema:
{schema}

User question: {state['question']}

SQL:"""
    response = llm.invoke(prompt)
    sql = response.content.strip()
    sql = re.sub(r'^```sql?\n?', '', sql)
    sql = re.sub(r'\n?```$', '', sql)
    return {"sql": sql.strip()}


# ──────────────────────────────────────────────
# Node: Execute SQL
# ──────────────────────────────────────────────
def execute_query(state: AgentState) -> dict:
    from agent.tools.mcp_server import query_gold
    result = query_gold(state["sql"])
    return {"result": result}


# ──────────────────────────────────────────────
# Node: Carrier Performance
# ──────────────────────────────────────────────
def carrier_check(state: AgentState) -> dict:
    from agent.tools.mcp_server import get_carrier_performance
    return {"result": get_carrier_performance()}


# ──────────────────────────────────────────────
# Node: Exceptions
# ──────────────────────────────────────────────
def exception_check(state: AgentState) -> dict:
    from agent.tools.mcp_server import get_exceptions
    return {"result": get_exceptions()}


# ──────────────────────────────────────────────
# Node: Anomaly Detection (NEW)
# ──────────────────────────────────────────────
def anomaly_check(state: AgentState) -> dict:
    from agent.nodes.anomaly import anomaly_check as detect
    return {"result": detect()}


# ──────────────────────────────────────────────
# Node: RAG Lookup (NEW)
# ──────────────────────────────────────────────
def rag_lookup(state: AgentState) -> dict:
    from agent.nodes.rag_lookup import rag_lookup as lookup
    context = lookup(state["question"])
    return {"context": context, "result": context}


# ──────────────────────────────────────────────
# Node: Schema Info
# ──────────────────────────────────────────────
def schema_info(state: AgentState) -> dict:
    from agent.tools.mcp_server import get_schema
    return {"result": get_schema()}


# ──────────────────────────────────────────────
# Node: Respond
# ──────────────────────────────────────────────
def respond(state: AgentState) -> dict:
    llm = get_llm()

    # For RAG, include context in the prompt
    if state.get("context") and state["plan"] == "rag_lookup":
        prompt = f"""You are a logistics data engineer. Answer the user's question
using the retrieved EDI specification context below. Be specific and cite the
segment names. If the context doesn't fully answer the question, say so.

User question: {state['question']}

Retrieved context:
{state['context']}

Answer:"""
    else:
        prompt = f"""You are a logistics operations analyst. Answer the user's question
using the data provided. Be concise and specific. Use numbers. If the data shows
a clear pattern or outlier, highlight it.

User question: {state['question']}

Data:
{state.get('result', 'No data available')}

Answer:"""

    response = llm.invoke(prompt)
    return {"answer": response.content.strip()}


# ──────────────────────────────────────────────
# Routing
# ──────────────────────────────────────────────
def route_by_plan(state: AgentState) -> str:
    plan = state["plan"]
    routes = {
        "schema_info": "schema_info",
        "anomaly_check": "anomaly_check",
        "rag_lookup": "rag_lookup",
        "carrier_check": "carrier_check",
        "exception_check": "exception_check",
    }
    return routes.get(plan, "nl_query")


# ──────────────────────────────────────────────
# Build graph
# ──────────────────────────────────────────────
def build_graph():
    graph = StateGraph(AgentState)

    graph.add_node("planner", planner)
    graph.add_node("nl_query", nl_query)
    graph.add_node("execute_query", execute_query)
    graph.add_node("carrier_check", carrier_check)
    graph.add_node("exception_check", exception_check)
    graph.add_node("anomaly_check", anomaly_check)
    graph.add_node("rag_lookup", rag_lookup)
    graph.add_node("schema_info", schema_info)
    graph.add_node("respond", respond)

    graph.set_entry_point("planner")

    graph.add_conditional_edges(
        "planner",
        route_by_plan,
        {
            "nl_query": "nl_query",
            "carrier_check": "carrier_check",
            "exception_check": "exception_check",
            "anomaly_check": "anomaly_check",
            "rag_lookup": "rag_lookup",
            "schema_info": "schema_info",
        }
    )

    graph.add_edge("nl_query", "execute_query")
    graph.add_edge("execute_query", "respond")
    graph.add_edge("carrier_check", "respond")
    graph.add_edge("exception_check", "respond")
    graph.add_edge("anomaly_check", "respond")
    graph.add_edge("rag_lookup", "respond")
    graph.add_edge("schema_info", "respond")
    graph.add_edge("respond", END)

    return graph.compile()


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
_app = None

def ask(question: str) -> str:
    global _app
    if _app is None:
        _app = build_graph()
    result = _app.invoke({"question": question})
    return result.get("answer", "I couldn't generate an answer.")


if __name__ == "__main__":
    test_questions = [
        "Which carrier has the worst on-time rate?",
        "Where are the most late shipments going?",
        "Are there any anomalies in the daily on-time rate?",
        "What segments are required in an 856 ASN?",
        "What's the format for the BGM segment in a purchase order?",
        "What's the average freight cost per carrier?",
        "Show me the schema",
    ]
    for q in test_questions:
        print(f"\nQ: {q}")
        print(f"A: {ask(q)}")   
