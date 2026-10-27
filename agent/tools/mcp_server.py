"""MCP Server: exposes Delta queries, anomaly checks, schema info as tools."""
from fastmcp import FastMCP

mcp = FastMCP("supply-chain-sentinel")

@mcp.tool()
def query_delta(sql: str) -> str:
    """Run a read-only SQL query against the Gold Delta layer."""
    # TODO: Week 3 - connect to Delta, execute, return results
    return f"Query not yet implemented: {sql[:80]}"

@mcp.tool()
def get_schema() -> str:
    """Return the schema of the Gold layer tables."""
    return "Tables: shipments_gold, kpis_gold, anomalies_gold (schema TBD)"

@mcp.tool()
def run_anomaly_check(window: str = "7d") -> str:
    """Run statistical anomaly detection on shipment KPIs."""
    return f"Anomaly check for {window} not yet implemented."

if __name__ == "__main__":
    mcp.run()
