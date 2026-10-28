"""
Anomaly Detection Node
Statistical anomaly detection on daily KPIs using z-score.
Flags days where on_time_rate deviates > 2σ from the mean.
"""
import json
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, mean, stddev, min, max, abs as spark_abs, lit


def _get_spark():
    import delta  # noqa: F401
    return SparkSession.builder \
        .appName("SCS-Anomaly") \
        .config("spark.jars.packages", "io.delta:delta-spark_2.12:3.0.0") \
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
        .getOrCreate()


def detect_anomalies(threshold: float = 1.0) -> dict:
    """
    Detect anomalies in daily on-time rate using z-score.
    Returns a dict with:
      - anomalies: list of flagged days
      - stats: overall mean/std
      - summary: natural language summary
    """
    spark = _get_spark()
    try:
        df = spark.read.format("delta").load("data/lake/gold/kpis_daily")

        # Compute z-score for on_time_rate
        mean_rate = df.agg(mean(col("on_time_rate"))).first()[0]
        std_rate = df.agg(stddev(col("on_time_rate"))).first()[0]

        if std_rate is None or std_rate == 0:
            return {
                "anomalies": [],
                "stats": {"mean": mean_rate, "std": 0},
                "summary": "No variance in on-time rates. No anomalies detected."
            }

        df = df.withColumn("z_score", (spark_abs(col("on_time_rate") - mean_rate) / std_rate))
        df = df.withColumn("is_anomaly", col("z_score") > lit(threshold))

        anomalies_df = df.filter(col("is_anomaly"))
        all_df = df.select("date", "on_time_rate", "total_shipments", "z_score")

        anomalies = []
        for row in anomalies_df.collect():
            anomalies.append({
                "date": str(row["date"]),
                "on_time_rate": round(float(row["on_time_rate"]), 4),
                "total_shipments": int(row["total_shipments"]),
                "z_score": round(float(row["z_score"]), 2),
            })

        # Build summary
        if anomalies:
            dates_str = ", ".join(a["date"] for a in anomalies)
            summary = (
                f"Detected {len(anomalies)} anomalous day(s) where on-time rate "
                f"deviated more than {threshold}σ from the mean ({mean_rate:.2%}). "
                f"Flagged dates: {dates_str}. "
                f"Mean on-time rate: {mean_rate:.1%}, Std: {std_rate:.1%}."
            )
        else:
            summary = (
                f"No anomalies detected. All daily on-time rates are within "
                f"{threshold}σ of the mean ({mean_rate:.2%}). "
                f"Range: {df.agg(min(col('on_time_rate'))).first()[0]:.1%} – "
                f"{df.agg(max(col('on_time_rate'))).first()[0]:.1%}."
            )

        return {
            "anomalies": anomalies,
            "stats": {"mean": round(mean_rate, 4), "std": round(std_rate, 4)},
            "summary": summary,
        }

    except Exception as e:
        return {
            "anomalies": [],
            "stats": {},
            "summary": f"Error detecting anomalies: {str(e)}",
        }
    finally:
        spark.stop()


def anomaly_check() -> str:
    """MCP-compatible wrapper: returns JSON string."""
    result = detect_anomalies()
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    print(anomaly_check())   
