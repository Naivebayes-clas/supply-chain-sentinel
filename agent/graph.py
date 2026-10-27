"""
LangGraph Agent: routes user questions to the appropriate tool/node.

Graph structure:
    [START] → [planner] → [nl_query] → [respond] → [END]
                         → [carrier_check] → [respond] → [END]
                         → [exception_check] → [respond] → [END]
                         → [schema_info] → [respond] → [END]
"""
import json
import re
from typing import TypedDict, Literal, Optional
from langgraph.graph import StateGraph, END
from langchain_openai import ChatOpenAI
import os


# ──────────────────────────────────────────────
# State
# ──────────────────────────────────────────────
class AgentState(TypedDict):
    question: str
    plan: str                    # Which tool to use
    sql: Optional[str]           # Generated SQL (for nl_query)
    result: Optional[str]        # Raw tool result
    answer: Optional[str]        # Final natural language answer
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
# Node: Planner (decides which tool to use)
# ──────────────────────────────────────────────
def planner(state: AgentState) -> dict:
    """Decide which tool handles the question."""
    q = state["question"].lower()

    # Simple keyword-based routing (no LLM needed for this)
    if any(kw in q for kw in ["schema", "table", "column", "field", "structure"]):
        plan = "schema_info"
    elif any(kw in q for kw in ["carrier", "on-time rate", "freight cost", "performance", "which carrier"]):
        plan = "carrier_check"
    elif any(kw in q for kw in ["exception", "late", "mississauga", "burlington", "delay", "problem"]):
        plan = "exception_check"
    else:
        plan = "nl_query"  # Default: generate SQL

    return {"plan": plan}


# ──────────────────────────────────────────────
# Node: NL → SQL
# ──────────────────────────────────────────────
def nl_query(state: AgentState) -> dict:
    """Generate SQL from natural language using the LLM."""
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

    # Clean: remove markdown code fences if present
    sql = re.sub(r'^```sql?\n?', '', sql)
    sql = re.sub(r'\n?```$', '', sql)
    sql = sql.strip()

    return {"sql": sql}


# ──────────────────────────────────────────────
# Node: Execute SQL (calls the MCP tool)
# ──────────────────────────────────────────────
def execute_query(state: AgentState) -> dict:
    """Execute the generated SQL against Gold tables."""
    from agent.tools.mcp_server import query_gold
    result = query_gold(state["sql"])
    return {"result": result}


# ──────────────────────────────────────────────
# Node: Carrier Performance (direct tool call)
# ──────────────────────────────────────────────
def carrier_check(state: AgentState) -> dict:
    """Get carrier performance data."""
    from agent.tools.mcp_server import get_carrier_performance
    result = get_carrier_performance()
    return {"result": result}


# ──────────────────────────────────────────────
# Node: Exceptions (direct tool call)
# ──────────────────────────────────────────────
def exception_check(state: AgentState) -> dict:
    """Get exception data."""
    from agent.tools.mcp_server import get_exceptions
    result = get_exceptions()
    return {"result": result}


# ──────────────────────────────────────────────
# Node: Schema Info
# ──────────────────────────────────────────────
def schema_info(state: AgentState) -> dict:
    """Return schema information."""
    from agent.tools.mcp_server import get_schema
    result = get_schema()
    return {"result": result}


# ──────────────────────────────────────────────
# Node: Respond (generate natural language answer)
# ──────────────────────────────────────────────
def respond(state: AgentState) -> dict:
    """Generate a natural language answer from the raw result."""
    llm = get_llm()

    prompt = f"""You are a logistics operations analyst. Answer the user's question
using the data provided. Be concise and specific. Use numbers. If the data shows
a clear pattern or outlier, highlight it.

User question: {state['question']}

Data:
{state['result']}

Answer:"""

    response = llm.invoke(prompt)
    return {"answer": response.content.strip()}


# ──────────────────────────────────────────────
# Routing function
# ──────────────────────────────────────────────
def route_by_plan(state: AgentState) -> str:
    """Route to the appropriate node based on the plan."""
    plan = state["plan"]
    if plan == "schema_info":
        return "schema_info"
    elif plan == "carrier_check":
        return "carrier_check"
    elif plan == "exception_check":
        return "exception_check"
    else:
        return "nl_query"


# ──────────────────────────────────────────────
# Build the graph
# ──────────────────────────────────────────────
def build_graph():
    """Construct and compile the LangGraph state machine."""
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("planner", planner)
    graph.add_node("nl_query", nl_query)
    graph.add_node("execute_query", execute_query)
    graph.add_node("carrier_check", carrier_check)
    graph.add_node("exception_check", exception_check)
    graph.add_node("schema_info", schema_info)
    graph.add_node("respond", respond)

    # Edges
    graph.set_entry_point("planner")

    # Planner routes to the appropriate tool
    graph.add_conditional_edges(
        "planner",
        route_by_plan,
        {
            "nl_query": "nl_query",
            "carrier_check": "carrier_check",
            "exception_check": "exception_check",
            "schema_info": "schema_info",
        }
    )

    # nl_query → execute_query → respond
    graph.add_edge("nl_query", "execute_query")
    graph.add_edge("execute_query", "respond")

    # Direct tools → respond
    graph.add_edge("carrier_check", "respond")
    graph.add_edge("exception_check", "respond")
    graph.add_edge("schema_info", "respond")

    # respond → END
    graph.add_edge("respond", END)

    return graph.compile()


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
_app = None

def ask(question: str) -> str:
    """Main entry point: ask the agent a question, get an answer."""
    global _app
    if _app is None:
        _app = build_graph()

    result = _app.invoke({"question": question})
    return result.get("answer", "I couldn't generate an answer.")


if __name__ == "__main__":
    # Quick test
    test_questions = [
        "Which carrier has the worst on-time rate?",
        "Where are the most late shipments going?",
        "What's the average freight cost per carrier?",
        "Show me the schema",
    ]
    for q in test_questions:
        print(f"\nQ: {q}")
        print(f"A: {ask(q)}")
   
