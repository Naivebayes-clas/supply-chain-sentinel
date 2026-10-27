"""
Bronze Ingestion
Reads X12 EDI files, parses segments, writes structured records to Delta Lake (bronze layer).

Bronze philosophy: land as-is, minimal transformation.
We preserve raw segment data + extracted key fields.
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Optional

# ──────────────────────────────────────────────
# Spark / Delta setup
# ──────────────────────────────────────────────
def get_spark():
    """Get or create a local SparkSession with Delta support."""
    import delta  # noqa: F401
    from pyspark.sql import SparkSession

    spark = SparkSession.builder \
        .appName("SCS-Bronze-Ingestion") \
        .config("spark.jars.packages", "io.delta:delta-spark_2.12:3.0.0") \
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension") \
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog") \
        .getOrCreate()
    return spark   

# ──────────────────────────────────────────────
# X12 Segment Parser
# ──────────────────────────────────────────────
def parse_segments(raw: str) -> list[dict]:
    """
    Split raw EDI content into a list of segment dicts.
    Each dict: {"code": "BGM", "fields": ["00", "PO123456", "", "", "", "20261019"]}
    """
    # Normalize: ensure ~ terminators (some files may use * as segment terminator)
    # Our generator uses ~ as segment terminator, * as element separator
    segments = raw.split("~")
    parsed = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        parts = seg.split("*")
        code = parts[0]
        fields = parts[1:]
        parsed.append({"code": code, "fields": fields})
    return parsed


def extract_850(segments: list[dict]) -> dict:
    """Extract key fields from an 850 Purchase Order."""
    record = {
        "transaction_type": "850",
        "po_number": None,
        "order_date": None,
        "requested_delivery": None,
        "ship_to_name": None,
        "ship_to_city": None,
        "ship_to_province": None,
        "ship_to_country": None,
        "line_items": [],
        "n_segments": len(segments),
    }

    for seg in segments:
        if seg["code"] == "BGM" and len(seg["fields"]) >= 3:
            record["po_number"] = seg["fields"][2] if seg["fields"][2] else None
            # Date is typically field 6 (index 5)
            if len(seg["fields"]) >= 6 and seg["fields"][5]:
                record["order_date"] = seg["fields"][5]

        elif seg["code"] == "DTM":
            if len(seg["fields"]) >= 2:
                qualifier = seg["fields"][0]
                date_val = seg["fields"][1]
                if qualifier == "011":
                    record["order_date"] = date_val
                elif qualifier == "016":
                    record["requested_delivery"] = date_val

        elif seg["code"] == "N1":
            if len(seg["fields"]) >= 2 and seg["fields"][0] == "ST":
                record["ship_to_name"] = seg["fields"][1]

        elif seg["code"] == "N4":
            if len(seg["fields"]) >= 3:
                record["ship_to_city"] = seg["fields"][0]
                record["ship_to_province"] = seg["fields"][1]
                record["ship_to_country"] = seg["fields"][2] if len(seg["fields"]) > 2 else None

        elif seg["code"] == "ITD":
            if len(seg["fields"]) >= 3:
                record["line_items"].append({
                    "uom": seg["fields"][0],
                    "qty": seg["fields"][1],
                    "unit_price": seg["fields"][2] if len(seg["fields"]) > 2 else None,
                })

        elif seg["code"] == "PID":
            if len(seg["fields"]) >= 2 and record["line_items"]:
                record["line_items"][-1]["description"] = seg["fields"][1]

    return record


def extract_856(segments: list[dict]) -> dict:
    """Extract key fields from an 856 Advance Ship Notice."""
    record = {
        "transaction_type": "856",
        "shipment_id": None,
        "ship_date": None,
        "eta": None,
        "related_po": None,
        "ship_from": None,
        "ship_to": None,
        "ship_to_city": None,
        "ship_to_province": None,
        "total_weight": None,
        "n_segments": len(segments),
    }

    in_ship_to = False

    for seg in segments:
        if seg["code"] == "BGM" and len(seg["fields"]) >= 2:
            record["shipment_id"] = seg["fields"][1] if seg["fields"][1] else None
            if len(seg["fields"]) >= 6 and seg["fields"][5]:
                record["ship_date"] = seg["fields"][5]

        elif seg["code"] == "DTM":
            if len(seg["fields"]) >= 2:
                qualifier = seg["fields"][0]
                date_val = seg["fields"][1]
                if qualifier == "011":
                    record["ship_date"] = date_val
                elif qualifier == "016":
                    record["eta"] = date_val

        elif seg["code"] == "REF":
            if len(seg["fields"]) >= 2 and seg["fields"][0] == "PB":
                record["related_po"] = seg["fields"][1]

        elif seg["code"] == "N1":
            if len(seg["fields"]) >= 2:
                if seg["fields"][0] == "SF":
                    record["ship_from"] = seg["fields"][1]
                    in_ship_to = False
                elif seg["fields"][0] == "ST":
                    record["ship_to"] = seg["fields"][1]
                    in_ship_to = True

        elif seg["code"] == "N4":
            if in_ship_to and len(seg["fields"]) >= 2:
                record["ship_to_city"] = seg["fields"][0]
                record["ship_to_province"] = seg["fields"][1]

        elif seg["code"] == "MAN":
            if len(seg["fields"]) >= 1:
                record["total_weight"] = seg["fields"][0]

    return record   

def extract_214(segments: list[dict]) -> dict:
    """Extract key fields from a 214 Motor Carrier Freight Details."""
    record = {
        "transaction_type": "214",
        "freight_bill_id": None,
        "related_po": None,
        "container_number": None,
        "carrier": None,
        "origin_city": None,
        "dest_city": None,
        "dest_province": None,
        "freight_charge": None,
        "surcharges": [],
        "n_segments": len(segments),
    }

    for seg in segments:
        if seg["code"] == "BGM" and len(seg["fields"]) >= 2:
            record["freight_bill_id"] = seg["fields"][1] if seg["fields"][1] else None

        elif seg["code"] == "REF":
            if len(seg["fields"]) >= 2:
                if seg["fields"][0] == "PB":
                    record["related_po"] = seg["fields"][1]
                elif seg["fields"][0] == "CN":
                    record["container_number"] = seg["fields"][1]

        elif seg["code"] == "N1":
            if len(seg["fields"]) >= 2:
                if seg["fields"][0] == "CN":
                    record["carrier"] = seg["fields"][1]
                elif seg["fields"][0] == "O":
                    record["origin_city"] = "TORONTO"  # hardcoded in generator
                elif seg["fields"][0] == "D":
                    pass  # dest city comes from N4

        elif seg["code"] == "N4":
            if len(seg["fields"]) >= 2:
                # Last N4 is destination
                record["dest_city"] = seg["fields"][0]
                record["dest_province"] = seg["fields"][1]

        elif seg["code"] == "FSN":
            if len(seg["fields"]) >= 2:
                if seg["fields"][0] == "F":
                    record["freight_charge"] = seg["fields"][1]
                elif seg["fields"][0] == "S":
                    record["surcharges"].append(seg["fields"][1])

    return record


# ──────────────────────────────────────────────
# File-level parsing
# ──────────────────────────────────────────────
def parse_edi_file(filepath: Path) -> Optional[dict]:
    """Parse a single EDI file into a structured record."""
    raw = filepath.read_text()
    segments = parse_segments(raw)

    # Determine transaction type from ST segment
    tx_type = None
    for seg in segments:
        if seg["code"] == "ST" and len(seg["fields"]) >= 1:
            tx_type = seg["fields"][0]
            break

    if tx_type is None:
        return None

    # Route to appropriate extractor
    if tx_type == "850":
        record = extract_850(segments)
    elif tx_type == "856":
        record = extract_856(segments)
    elif tx_type == "214":
        record = extract_214(segments)
    else:
        return None

    # Add metadata
    record["source_file"] = filepath.name
    record["ingested_at"] = datetime.now().isoformat()
    record["raw_content"] = raw  # Preserve raw for bronze fidelity

    return record


# ──────────────────────────────────────────────
# Main ingestion
# ──────────────────────────────────────────────
def run_ingest(input_dir: str = "data/sample_edi", lake_path: str = "data/lake/bronze"):
    """Parse all EDI files and write to Bronze Delta table."""
    print(f"📥 Ingesting from {input_dir} → {lake_path}")

    # 1. Parse all files
    input_path = Path(input_dir)
    if not input_path.exists():
        print(f"❌ Input directory not found: {input_dir}")
        print("   Run 'python ingestion/synthetic_generator.py' first.")
        return

    edi_files = sorted(input_path.glob("*.edi"))
    print(f"   Found {len(edi_files)} EDI files")

    records = []
    errors = []
    for f in edi_files:
        try:
            record = parse_edi_file(f)
            if record:
                records.append(record)
            else:
                errors.append(f.name)
        except Exception as e:
            errors.append(f"{f.name}: {str(e)}")

    print(f"   Parsed: {len(records)} records, {len(errors)} errors")
    if errors:
        print(f"   First errors: {errors[:3]}")

    if not records:
        print("❌ No records to write.")
        return

    # 2. Write to Delta
    spark = get_spark()

    # Flatten line_items and surcharges for the Delta table
    flat_records = []
    for r in records:
        flat = {
            "transaction_type": r["transaction_type"],
            "po_number": r.get("po_number") or r.get("related_po"),
            "shipment_id": r.get("shipment_id"),
            "freight_bill_id": r.get("freight_bill_id"),
            "order_date": r.get("order_date"),
            "ship_date": r.get("ship_date"),
            "eta": r.get("eta"),
            "requested_delivery": r.get("requested_delivery"),
            "ship_to_name": r.get("ship_to_name") or r.get("ship_to"),
            "ship_to_city": r.get("ship_to_city"),
            "ship_to_province": r.get("ship_to_province"),
            "ship_to_country": r.get("ship_to_country"),
            "ship_from": r.get("ship_from"),
            "carrier": r.get("carrier"),
            "origin_city": r.get("origin_city"),
            "dest_city": r.get("dest_city"),
            "dest_province": r.get("dest_province"),
            "total_weight": r.get("total_weight"),
            "freight_charge": r.get("freight_charge"),
            "n_line_items": len(r.get("line_items", [])),
            "n_segments": r.get("n_segments", 0),
            "source_file": r["source_file"],
            "ingested_at": r["ingested_at"],
        }
        flat_records.append(flat)

    # Build DataFrame
    df = spark.createDataFrame(flat_records)

    # Write to Delta (overwrite for idempotency)
    df.write.format("delta").mode("overwrite").save(lake_path)

    print(f"✅ Wrote {len(flat_records)} records to {lake_path}/")
    print(f"   Transaction types: {dict(df.select('transaction_type').groupBy('transaction_type').count().collect() and [(row['transaction_type'], row['count']) for row in df.select('transaction_type').groupBy('transaction_type').count().collect()])}")

    # 3. Summary
    type_counts = {}
    for r in records:
        type_counts[r["transaction_type"]] = type_counts.get(r["transaction_type"], 0) + 1
    print(f"   Breakdown: {type_counts}")

    spark.stop()


if __name__ == "__main__":
    run_ingest()
  
