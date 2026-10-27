"""
Bronze → Silver Transform
Reads Bronze Delta, applies cleaning rules, writes Silver Delta.

Cleaning rules:
  1. Date validation: reject month-13, day-32, missing-year (MMDD only)
  2. Deduplication: keep latest record per PO number (by ingested_at)
  3. Type casting: dates → DateType, quantities → IntegerType, prices → DoubleType
  4. Quarantine: failed records go to silver_quarantine with a reason
"""

import re
from datetime import datetime
from typing import Optional


# ──────────────────────────────────────────────
# Spark setup (same as ingestion)
# ──────────────────────────────────────────────
def get_spark():
    import delta  # noqa: F401
    from pyspark.sql import SparkSession

    spark = SparkSession.builder \
        .appName("SCS-Silver-Transform") \
        .config("spark.jars.packages", "io.delta:delta-spark_2.12:3.0.0") \
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
        .getOrCreate()
    return spark


# ──────────────────────────────────────────────
# Date validation
# ──────────────────────────────────────────────
def validate_date(date_str: Optional[str]) -> Optional[str]:
    """
    Validate an X12 date string (CCYYMMDD).
    Returns the valid date string or None if invalid.
    """
    if not date_str:
        return None

    # Must be 8 digits (CCYYMMDD)
    if not re.match(r'^\d{8}$', date_str):
        return None

    year = int(date_str[0:4])
    month = int(date_str[4:6])
    day = int(date_str[6:8])

    # Range checks
    if not (2000 <= year <= 2099):
        return None
    if not (1 <= month <= 12):
        return None
    if not (1 <= day <= 31):
        return None

    # Verify it's a real date
    try:
        datetime(int(year), int(month), int(day))
    except ValueError:
        return None

    return date_str


# ──────────────────────────────────────────────
# Main transform
# ──────────────────────────────────────────────
def run_transform(bronze_path: str = "data/lake/bronze", silver_path: str = "data/lake/silver"):
    """Read Bronze, clean, dedup, write Silver + Quarantine."""
    print("🔄 Bronze → Silver transform running...")

    from pyspark.sql.functions import col, when, concat_ws, lit, row_number, to_date, udf
    from pyspark.sql.window import Window
    from pyspark.sql.types import StringType, DateType, DoubleType

    spark = get_spark()

    # 1. Read Bronze
    bronze = spark.read.format("delta").load(bronze_path)
    print(f"   Read {bronze.count()} records from Bronze")

    # 2. Validate dates (UDF for complex logic)
    from pyspark.sql.functions import udf
    from pyspark.sql.types import StringType

    validate_date_udf = udf(validate_date, StringType())

    # Apply validation to all date fields
    date_fields = ["order_date", "ship_date", "eta", "requested_delivery"]
    for field in date_fields:
        bronze = bronze.withColumn(f"{field}_valid", validate_date_udf(col(field)))

    # 3. Flag records with ANY invalid date
    from pyspark.sql.functions import col, when, concat_ws, lit, row_number
    from pyspark.sql.window import Window

    # Build quarantine reason
    invalid_date_reasons = []
    for field in date_fields:
        invalid_date_reasons.append(
            when(
                (col(field).isNotNull()) & (col(f"{field}_valid").isNull()),
                f"invalid_{field}"
            )
        )

    bronze = bronze.withColumn(
        "date_issue",
        concat_ws(",", *invalid_date_reasons)
    )

    # 4. Split: clean vs quarantine
    # A record is "clean" if no date issues
    clean = bronze.filter(col("date_issue") == "")
    quarantined = bronze.filter(col("date_issue") != "")

    print(f"   Clean: {clean.count()}, Quarantined: {quarantined.count()}")

    # 5. Deduplicate: keep latest per PO (by ingested_at)
    # Only dedup on records that have a po_number
    has_po = clean.filter(col("po_number").isNotNull())
    no_po = clean.filter(col("po_number").isNull())

    if has_po.count() > 0:
        window = Window.partitionBy("po_number", "transaction_type").orderBy(col("ingested_at").desc())
        has_po_deduped = has_po.withColumn("row_num", row_number().over(window)).filter(col("row_num") == 1).drop("row_num")
    else:
        has_po_deduped = has_po

    # 6. Cast types
    from pyspark.sql.types import DateType, IntegerType, DoubleType, StructType, StructField

    silver = has_po_deduped.unionByName(no_po)

    # Cast date fields to proper DateType (CCYYMMDD → YYYY-MM-DD)
    from pyspark.sql.functions import to_date, col as c

    for field in date_fields:
        # Convert CCYYMMDD format to date
        silver = silver.withColumn(
            field,
            to_date(c(field), "yyyyMMdd")
        )

    # Cast numeric fields
    if silver.columns:
        for field in ["total_weight", "freight_charge"]:
            if field in silver.columns:
                silver = silver.withColumn(field, c(field).cast(DoubleType()))

    # 7. Drop helper columns
    for field in date_fields:
        silver = silver.drop(f"{field}_valid")
    silver = silver.drop("date_issue")

    # 8. Write Silver
    silver.write.format("delta").mode("overwrite").save(silver_path)
    print(f"   ✅ Wrote {silver.count()} clean records to {silver_path}/")

    # 9. Write Quarantine (with reason)
    quarantine_path = "data/lake/silver_quarantine"
    quarantined_out = quarantined.withColumnRenamed("date_issue", "quarantine_reason")
    for field in date_fields:
        quarantined_out = quarantined_out.drop(f"{field}_valid")

    quarantined_out.write.format("delta").mode("overwrite").save(quarantine_path)
    print(f"   ⚠️  Wrote {quarantined_out.count()} quarantined records to {quarantine_path}/")

    # 10. Summary
    print("\n   ─── Summary ───")
    print(f"   Bronze in:       {bronze.count()}")
    print(f"   Silver out:      {silver.count()}")
    print(f"   Quarantined:     {quarantined_out.count()}")
    print(f"   Duplicates removed: {clean.count() - silver.count()}")

    # Show quarantine reasons
    if quarantined_out.count() > 0:
        print("\n   Quarantine reasons:")
        quarantined_out.select("quarantine_reason").groupBy("quarantine_reason").count().show(truncate=False)

    spark.stop()


if __name__ == "__main__":
    run_transform()   
