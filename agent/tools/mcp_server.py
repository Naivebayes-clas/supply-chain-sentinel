"""
MCP Server: exposes Delta Lake Gold tables as callable tools for the agent.
"""
from fastmcp import FastMCP
from pyspark.sql import SparkSession
import json

mcp = FastMCP("supply-chain-sentinel")

_spark_instance = None


def _get_spark():
    """Get or recreate the SparkSession. Recreates if the JVM gateway is dead."""
    global _spark_instance

    if _spark_instance is not None:
        # Check if the JVM gateway is still alive
        try:
            _spark_instance.sparkContext._jvm  # noqa: B018
            return _spark_instance
        except Exception:
            _spark_instance = None

    import delta  # noqa: F401
    _spark_instance = SparkSession.builder \
        .appName("SCS-MCP-Tools") \
        .config("spark.jars.packages", "io.delta:delta-spark_2.12:3.0.0") \
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
        .getOrCreate()
    return _spark_instance


def _df_to_json(df, max_rows: int = 50) -> str:
    """Convert a Spark DataFrame to a JSON string."""
    rows = df.limit(max_rows).collect()
    columns = df.columns
    data = [dict(zip(columns, [str(v) for v in row])) for row in rows]
    return json.dumps(data, indent=2, default=str)


def _query_gold_internal(sql: str) -> str:
    """Core SQL execution logic (no decorator)."""
    if not sql.strip().upper().startswith("SELECT"):
        return "Error: Only SELECT queries are allowed."

    try:
        spark = _get_spark()
        gold_path = "data/lake/gold"
        spark.read.format("delta").load(f"{gold_path}/kpis_daily").createOrReplaceTempView("kpis_daily")
        spark.read.format("delta").load(f"{gold_path}/carrier_perf").createOrReplaceTempView("carrier_perf")
        spark.read.format("delta").load(f"{gold_path}/exceptions_by_dest").createOrReplaceTempView("exceptions_by_dest")
        spark.read.format("delta").load(f"{gold_path}/volume_trend").createOrReplaceTempView("volume_trend")

        df = spark.sql(sql.strip().rstrip(';'))
        result = _df_to_json(df)
        return f"Results ({df.count()} rows):\n{result}"
    except Exception as e:
        # If Spark crashed, try once more with a fresh session
        global _spark_instance
        _spark_instance = None
        try:
            spark = _get_spark()
            gold_path = "data/lake/gold"
            spark.read.format("delta").load(f"{gold_path}/kpis_daily").createOrReplaceTempView("kpis_daily")
            spark.read.format("delta").load(f"{gold_path}/carrier_perf").createOrReplaceTempView("carrier_perf")
            spark.read.format("delta").load(f"{gold_path}/exceptions_by_dest").createOrReplaceTempView("exceptions_by_dest")
            spark.read.format("delta").load(f"{gold_path}/volume_trend").createOrReplaceTempView("volume_trend")
            df = spark.sql(sql.strip().rstrip(';'))
            result = _df_to_json(df)
            return f"Results ({df.count()} rows):\n{result}"
        except Exception as e2:
            return f"Query error: {str(e2)}"


@mcp.tool()
def query_gold(sql: str) -> str:
    """Run a read-only SQL query against the Gold Delta tables."""
    return _query_gold_internal(sql)


@mcp.tool()
def get_schema() -> str:
    """Return the schema of all Gold tables."""
    return """
Gold Tables:

1. kpis_daily (daily shipment metrics)
   - date: string (yyyy-MM-dd)
   - total_shipments: int
   - tracked_shipments: int
   - on_time_rate: double (0.0 to 1.0)
   - avg_weight_kg: double
   - computed_at: string

2. carrier_perf (per-carrier performance)
   - carrier_name: string
   - total_shipments: int
   - tracked_shipments: int
   - on_time_rate: double (0.0 to 1.0)
   - avg_freight_cost: double
   - total_freight_cost: double

3. exceptions_by_dest (late shipments by destination)
   - ship_to_city: string
   - late_count: int
   - affected_pos: int

4. volume_trend (monthly volume by transaction type)
   - week: string (yyyy-MM)
   - transaction_type: string (850, 856, 214)
   - count: int
"""


@mcp.tool()
def get_carrier_performance() -> str:
    """Get current carrier performance rankings."""
    spark = _get_spark()
    df = spark.read.format("delta").load("data/lake/gold/carrier_perf")
    return _df_to_json(df)


@mcp.tool()
def get_exceptions() -> str:
    """Get current shipment exceptions by destination."""
    spark = _get_spark()
    df = spark.read.format("delta").load("data/lake/gold/exceptions_by_dest")
    return _df_to_json(df)


if __name__ == "__main__":
    mcp.run()
   
