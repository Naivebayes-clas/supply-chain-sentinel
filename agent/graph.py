"""
LangGraph Agent: routes user questions to the appropriate tool/node.
6 routing paths: nl_query, carrier_check, exception_check, anomaly_check, rag_lookup, schema_info
"""
import re
import os
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI


# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
class AgentState(TypedDict):
    question: str
    plan: str
    sql: Optional[str]
    result: Optional[str]
    context: Optional[str]
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

    def has_word(words: list[str]) -> bool:
        for w in words:
            if len(w) <= 4:
                if re.search(r'\b' + re.escape(w) + r'\b', q):
                    return True
            else:
                if w in q:
                    return True
        return False

    # 1. Schema
    if has_word([
        "schema", "what tables", "what columns", "data type",
        "table structure", "what tables are available",
    ]):
        return {"plan": "schema_info"}

    # 2. Anomaly (check before RAG to avoid "spike" matching RAG)
    if has_word([
        "anomaly", "anomalies", "outlier", "outliers", "spike",
        "deviation", "z-score", "z score", "unusual", "statistically",
    ]):
        return {"plan": "anomaly_check"}

    # 3. RAG: EDI specs, formats, architecture
    if has_word([
        "segment", "segments", "format", "spec", "specification",
        "required field", "required segment",
        "what is an 85", "what is a 214", "edi spec", "how to parse",
        "medallion", "architecture", "pipeline",
        "bronze", "silver", "gold layer",
        "dtm", "bgm", "fsn", "isa", "iea",
        "qualifier", "link an 85", "link back", "how do you link",
        "data quality", "common errors", "date format",
        "how does", "what is the medallion", "what is the pipeline",
    ]):
        if re.search(r'\bman\b', q) or re.search(r'\bref\b', q):
            return {"plan": "rag_lookup"}
        return {"plan": "rag_lookup"}

    # 4. Exception check
    if has_word([
        "late shipment", "late shipments", "delivery exception",
        "delivery exceptions", "delivery problem", "delivery problems",
        "where are the most late", "most late",
        "destinations have delivery", "affected by late",
        "late shipments to", "show exceptions", "list exceptions",
        "show me the late",
    ]):
        return {"plan": "exception_check"}

    # 5. Carrier check: ONLY raw table dump
    if has_word([
        "show carrier performance", "carrier performance data",
        "show me the carriers", "list all carriers",
    ]):
        return {"plan": "carrier_check"}

    # 6. Default: nl_query
    return {"plan": "nl_query"}


# ──────────────────────────────────────────────
# Node: NL → SQL
# ──────────────────────────────────────────────
def nl_query(state: AgentState) -> dict:
    """Generate SQL from natural language."""
    llm = get_llm()

    schema = """
You have access to EXACTLY these 4 SQL tables (use these exact names in FROM clauses, no prefix):

TABLE carrier_perf:
  - carrier_name (string): 'XPO', 'FedEx', 'UPS', 'Old Dominion', 'Estes'
  - total_shipments (int)
  - tracked_shipments (int)
  - on_time_rate (double): 0.0 to 1.0
  - avg_freight_cost (double): dollars
  - total_freight_cost (double): dollars

TABLE kpis_daily:
  - date (string): 'yyyy-MM-dd'
  - total_shipments (int)
  - tracked_shipments (int)
  - on_time_rate (double): 0.0 to 1.0
  - avg_weight_kg (double)

TABLE exceptions_by_dest:
  - ship_to_city (string): 'Mississauga', 'Burlington', 'Hamilton'
  - late_count (int)
  - affected_pos (int)

TABLE volume_trend:
  - week (string): 'yyyy-MM'
  - transaction_type (string): '850', '856', '214'
  - count (int)

RULES:
- ALWAYS include the metric being asked about in your SELECT
- If asked for a rate/percentage, SELECT the rate column
- If asked for a count, SELECT the count or use COUNT(*)
- If asked to rank/compare, SELECT both the identifier AND the metric
- Use EXACT table and column names above
- Only SELECT queries, no JOINs needed
- Return ONLY the SQL, no explanation, no markdown fences
"""

    prompt = f"{schema}\n\nUser question: {state['question']}\n\nSQL:"
    response = llm.invoke(prompt)
    sql = response.content.strip()
    sql = re.sub(r'^```sql?\n?', '', sql)
    sql = re.sub(r'\n?```$', '', sql)
    return {"sql": sql.strip()}


# ──────────────────────────────────────────────
# Node: Execute SQL
# ──────────────────────────────────────────────
def execute_query(state: AgentState) -> dict:
    from agent.tools.mcp_server import _query_gold_internal
    result = _query_gold_internal(state["sql"])
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
# Node: Anomaly Detection
# ──────────────────────────────────────────────
def anomaly_check(state: AgentState) -> dict:
    from agent.nodes.anomaly import anomaly_check as detect
    return {"result": detect()}


# ──────────────────────────────────────────────
# Node: RAG Lookup
# ──────────────────────────────────────────────
def rag_lookup_node(state: AgentState) -> dict:
    from agent.nodes.rag_lookup import rag_lookup
    context = rag_lookup(state["question"])
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
    """Generate a natural language answer."""
    llm = get_llm()

    if state.get("context") and state["plan"] == "rag_lookup":
        prompt = f"""You are a logistics data engineer. The user is asking about
EDI specifications. Below is the relevant reference document. Extract the answer
directly from it. Quote exact segment codes, qualifiers, and field positions
verbatim (e.g. "DTM*011", "BGM*BLSV", "REF*PB", "CCYYMMDD").

User question: {state['question']}

Reference document:
{state['context']}

Your answer:"""
    else:
        prompt = f"""You are a logistics operations analyst. Answer the user's
question using the data below. Be specific. Use the exact numbers from the data.
If the data shows an error or is empty, say "I could not retrieve that data."

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
    graph.add_node("rag_lookup", rag_lookup_node)
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
   
