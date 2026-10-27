"""
Silver → Gold Transform
Reads Silver Delta, computes operational KPIs, writes Gold Delta.

Gold tables:
  - kpis_daily:      daily shipment volume, on-time %, avg freight cost
  - carrier_perf:    per-carrier on-time rate, avg cost, total shipments
  - exception_log:   exceptions by destination, by type, by week
"""

from pyspark.sql.functions import (
    sum as spark_sum,
    col, to_date, date_format, lit, count, avg, when,
    round as spark_round, countDistinct, coalesce
)
from pyspark.sql.window import Window


def get_spark():
    import delta  # noqa: F401
    from pyspark.sql import SparkSession

    spark = SparkSession.builder \
        .appName("SCS-Gold-Aggregation") \
        .config("spark.jars.packages", "io.delta:delta-spark_2.12:3.0.0") \
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
        .getOrCreate()
    return spark


def run_aggregate(silver_path: str = "data/lake/silver", gold_path: str = "data/lake/gold"):
    """Read Silver, compute KPIs, write Gold."""
    print("📊 Silver → Gold aggregation running...")

    spark = get_spark()

    # 1. Read Silver
    silver = spark.read.format("delta").load(silver_path)
    print(f"   Read {silver.count()} records from Silver")

    # 2. Filter to shipment records (856) for delivery KPIs
    shipments = silver.filter(col("transaction_type") == "856")
    print(f"   Shipment records (856): {shipments.count()}")

    # 3. Compute on-time flag
    # On-time = ship_date <= eta (actual arrival before expected)
    # We use ship_date as proxy for "actual" and eta as "expected"
    on_time_expr = when(
        col("ship_date").isNotNull() & col("eta").isNotNull(),
        (col("ship_date") <= col("eta")).cast("int")
    ).otherwise(None)

    shipments = shipments.withColumn("is_on_time", on_time_expr)

    # 4. ─── GOLD TABLE 1: kpis_daily ───
    kpis_daily = shipments \
        .groupBy(date_format(col("ship_date"), "yyyy-MM-dd").alias("date")) \
        .agg(
            count("*").alias("total_shipments"),
            count(col("is_on_time")).alias("tracked_shipments"),
            (spark_sum(col("is_on_time")) / count(col("is_on_time"))).alias("on_time_rate"),
            avg(col("total_weight").cast("double")).alias("avg_weight_kg"),
        ) \
        .withColumn("on_time_rate", spark_round(col("on_time_rate"), 4)) \
        .withColumn("avg_weight_kg", spark_round(col("avg_weight_kg"), 2)) \
        .withColumn("computed_at", lit(spark.sparkContext._jvm.java.time.Instant.now().toString()))

    kpis_daily_path = f"{gold_path}/kpis_daily"
    kpis_daily.write.format("delta").mode("overwrite").save(kpis_daily_path)
    print(f"   ✅ kpis_daily: {kpis_daily.count()} rows → {kpis_daily_path}/")

    # 5. ─── GOLD TABLE 2: carrier_perf ───
    # Join with 214 (freight) to get carrier info
    freight = silver.filter(col("transaction_type") == "214")

    # Get carrier per PO from freight records
    carrier_map = freight \
        .filter(col("carrier").isNotNull()) \
        .select(col("po_number"), col("carrier").alias("carrier_name"), col("freight_charge").cast("double").alias("freight_cost")) \
        .distinct()

    # Join shipments with carrier info
    shipments_with_carrier = shipments \
        .join(carrier_map, shipments["po_number"] == carrier_map["po_number"], "left") \
        .withColumn("carrier", coalesce(col("carrier").alias("carrier_name"), lit("UNKNOWN")))

    carrier_perf = shipments_with_carrier \
        .groupBy(col("carrier_name")) \
        .agg(
            count("*").alias("total_shipments"),
            count(col("is_on_time")).alias("tracked_shipments"),
            (spark_sum(col("is_on_time")) / count(col("is_on_time"))).alias("on_time_rate"),
            avg(col("freight_cost")).alias("avg_freight_cost"),
            spark_sum(col("freight_cost")).alias("total_freight_cost"),
        ) \
        .withColumn("on_time_rate", spark_round(col("on_time_rate"), 4)) \
        .withColumn("avg_freight_cost", spark_round(col("avg_freight_cost"), 2)) \
        .withColumn("total_freight_cost", spark_round(col("total_freight_cost"), 2)) \
        .orderBy(col("total_shipments").desc())

    carrier_perf_path = f"{gold_path}/carrier_perf"
    carrier_perf.write.format("delta").mode("overwrite").save(carrier_perf_path)
    print(f"   ✅ carrier_perf: {carrier_perf.count()} rows → {carrier_perf_path}/")

    # 6. ─── GOLD TABLE 3: exception_log ───
    # Late shipments (is_on_time = 0)
    late = shipments.filter(col("is_on_time") == 0)

    # Exceptions by destination
    exceptions_by_dest = late \
        .groupBy(col("ship_to_city")) \
        .agg(
            count("*").alias("late_count"),
            countDistinct(col("po_number")).alias("affected_pos"),
        ) \
        .orderBy(col("late_count").desc())

    exceptions_by_dest_path = f"{gold_path}/exceptions_by_dest"
    exceptions_by_dest.write.format("delta").mode("overwrite").save(exceptions_by_dest_path)
    print(f"   ✅ exceptions_by_dest: {exceptions_by_dest.count()} rows → {exceptions_by_dest_path}/")

    # 7. ─── GOLD TABLE 4: volume_trend ───
    # Weekly volume across ALL transaction types
    volume_trend = silver \
        .groupBy(
            date_format(col("ingested_at"), "yyyy-MM").alias("week"),
            col("transaction_type"),
        ) \
        .agg(count("*").alias("count")) \
        .orderBy("week", "transaction_type")

    volume_trend_path = f"{gold_path}/volume_trend"
    volume_trend.write.format("delta").mode("overwrite").save(volume_trend_path)
    print(f"   ✅ volume_trend: {volume_trend.count()} rows → {volume_trend_path}/")

    # 8. Summary
    print("\n   ─── Gold Summary ───")
    print(f"   kpis_daily:        {kpis_daily.count()} rows")
    print(f"   carrier_perf:      {carrier_perf.count()} rows")
    print(f"   exceptions_by_dest:{exceptions_by_dest.count()} rows")
    print(f"   volume_trend:      {volume_trend.count()} rows")

    # Show carrier performance
    print("\n   Carrier Performance:")
    carrier_perf.show(truncate=False)

    # Show top exceptions
    if exceptions_by_dest.count() > 0:
        print("   Top Exception Destinations:")
        exceptions_by_dest.show(5, truncate=False)

    spark.stop()


# Helper: coalesce (import at module level to avoid shadowing)
from pyspark.sql.functions import coalesce


if __name__ == "__main__":
    run_aggregate()
 
