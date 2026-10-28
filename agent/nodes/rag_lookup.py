import re
"""
RAG Node: retrieves relevant EDI spec context from ChromaDB.
Used to answer questions like "What segments are required in an 856?"
"""
import os
from pathlib import Path

# ──────────────────────────────────────────────
# Document ingestion (run once to populate ChromaDB)
# ──────────────────────────────────────────────
def ingest_docs(docs_dir: str = "data/docs", collection_name: str = "edi_specs"):
    """Chunk and embed EDI spec documents into ChromaDB."""
    import chromadb

    client = chromadb.PersistentClient(path="data/chroma")
    collection = client.get_or_create_collection(name=collection_name)

    docs_path = Path(docs_dir)
    if not docs_path.exists():
        print(f"⚠️  Docs directory not found: {docs_dir}")
        return 0

    all_texts = []
    all_metas = []

    for f in sorted(docs_path.glob("*.txt")) + sorted(docs_path.glob("*.md")):
        content = f.read_text()
        # Simple chunking: ~500 chars with overlap
        chunks = _chunk_text(content, chunk_size=1500, overlap=100)
        for i, chunk in enumerate(chunks):
            all_texts.append(chunk)
            all_metas.append({"source": f.name, "chunk_id": i})

    if all_texts:
        # Add in batches
        batch_size = 100
        for i in range(0, len(all_texts), batch_size):
            batch_texts = all_texts[i:i+batch_size]
            batch_metas = all_metas[i:i+batch_size]
            batch_ids = [f"doc_{i+j}" for j in range(len(batch_texts))]
            collection.add(documents=batch_texts, metadatas=batch_metas, ids=batch_ids)

    print(f"✅ Ingested {len(all_texts)} chunks from {docs_dir} into ChromaDB")
    return len(all_texts)


def _chunk_text(text: str, chunk_size: int = 1500, overlap: int = 100) -> list[str]:
    """
    Section-aware semantic chunking.
    Splits on paragraph boundaries (blank lines) and section headers,
    keeping logical units (e.g. 'Required Segments' lists) intact.
    Falls back to size-based splitting for sections that exceed chunk_size.
    """
    # Split into paragraphs (blocks separated by blank lines)
    paragraphs = re.split(r'\\n\\s*\\n', text)

    chunks = []
    current = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # If adding this paragraph would exceed chunk_size, flush current
        if len(current) + len(para) + 2 > chunk_size and current:
            chunks.append(current.strip())
            current = para
        else:
            current = current + "\\n\\n" + para if current else para

    if current.strip():
        chunks.append(current.strip())

    return chunks


# ──────────────────────────────────────────────
# Query
# ──────────────────────────────────────────────
def rag_lookup(question: str, n_results: int = 3) -> str:
    """
    Search EDI spec docs for relevant context.
    Returns the top-n chunks as a string.
    """
    import chromadb

    client = chromadb.PersistentClient(path="data/chroma")
    collection = client.get_or_create_collection(name="edi_specs")

    if collection.count() == 0:
        return "No EDI spec documents indexed. Run ingest_docs() first."

    results = collection.query(
        query_texts=[question],
        n_results=min(n_results, collection.count()),
    )

    if not results["documents"] or not results["documents"][0]:
        return "No relevant documents found."

    context_parts = []
    for i, (doc, meta) in enumerate(zip(results["documents"][0], results["metadatas"][0])):
        context_parts.append(f"[{i+1}] (source: {meta.get('source', 'unknown')})\n{doc}")

    return "\n\n---\n\n".join(context_parts)


def rag_node(state: dict) -> dict:
    """LangGraph node: perform RAG lookup and store context."""
    context = rag_lookup(state["question"])
    return {"context": context}


# ──────────────────────────────────────────────
# Seed docs (generate placeholder EDI spec summaries)
# ──────────────────────────────────────────────
def create_seed_docs(docs_dir: str = "data/docs"):
    """Create seed EDI spec documents for RAG testing."""
    docs_path = Path(docs_dir)
    docs_path.mkdir(parents=True, exist_ok=True)

    specs = {
        "850_purchase_order.txt": """
X12 850 - Purchase Order

Purpose: Transmit a purchase order from buyer to seller.

Required Segments:
- ISA: Interchange Control Header (envelope)
- GS: Functional Group Header
- ST: Transaction Set Header (ST*850*control#)
- BGM: Beginning Segment for Purchase Order
  - BGM*00*PO_Number*Qualifier*...
  - Element 2: PO Number (required)
  - Element 6: Date (CCYYMMDD format)
- DTM: Date/Time Period
  - DTM*011*CCYYMMDD = Order Date
  - DTM*016*CCYYMMDD = Requested Delivery Date
- N1: Name (ST = Ship To, SF = Ship From, BY = Buy)
- N3: Address Line
- N4: Subdivision (City*State*Zip*Country)
- ITD: Item Detail
  - ITD*U*Quantity*UOM*Unit_Price
- PID: Product/Item Description
- SE: Transaction Set Trailer (count*control#)
- GE: Functional Group Trailer
- IEA: Interchange Control Trailer

Data Quality Considerations:
- PO numbers must be unique across the group
- Dates must be valid CCYYMMDD (8 digits)
- Quantities must be positive integers
- Unit prices must be positive decimals

Common Errors:
- Missing DTM segments (no order date)
- Malformed dates (month > 12, day > 31)
- Duplicate PO numbers in same group
- Truncated SE segment (missing count or control#)
""",

        "856_advance_ship_notice.txt": """
X12 856 - Advance Ship Notice (ASN)

Purpose: Notify receiver that goods are being shipped.

Required Segments:
- ISA / GS / ST (ST*856*control#)
- BGM: Beginning Segment for Shipment
  - BGM*BLSV*Shipment_ID*...
- DTM: Date/Time
  - DTM*011*CCYYMMDD = Ship Date (actual)
  - DTM*016*CCYYMMDD = ETA (expected arrival)
- REF: Reference
  - REF*PB*PO_Number = Related Purchase Order
- N1: Name
  - N1*SF*Shipper_Name
  - N1*ST*Ship_To_Name
- N3: Address
- N4: City*State*Zip*Country
- MAN: Manifest
  - MAN*Total_Weight
- SE / GE / IEA

Key Relationships:
- REF*PB links the ASN back to the 850 PO
- DTM*011 (ship date) vs DTM*016 (ETA) determines on-time status
- If ship_date > ETA, the shipment is LATE

On-Time Calculation:
- is_on_time = (ship_date <= eta)
- On-time rate = count(is_on_time=1) / count(tracked)
- A shipment is "tracked" if both ship_date and eta are present

Common Errors:
- Missing REF*PB (can't link to PO)
- Ship date after ETA (late shipment)
- Missing MAN segment (no weight data)
- Invalid dates in DTM segments
""",

        "214_freight_details.txt": """
X12 214 - Motor Carrier Freight Details and Invoice

Purpose: Transmit freight charges and carrier information.

Required Segments:
- ISA / GS / ST (ST*214*control#)
- BGM: Beginning Segment
  - BGM*01*Freight_Bill_ID*...
- REF: Reference
  - REF*PB*PO_Number = Related PO
  - REF*CN*Container_Number
- N1: Name
  - N1*CN*Carrier_Name (CN = Carrier)
  - N1*O*Origin_Name
  - N1*D*Destination_Name
- N4: City*State*Zip*Country
- FSN: Freight and Service Notes
  - FSN*F*Amount = Base Freight Charge
  - FSN*S*Amount = Surcharges (fuel, accessorials, etc.)
- SE / GE / IEA

Carrier Performance Metrics:
- On-time rate: joined via PO to 856 shipments
- Average freight cost: FSN*F amount
- Total freight cost: sum of all FSN*F for carrier
- Shipment volume: count of linked 856 records

Common Carriers in Logistics:
- XPO Logistics
- Old Dominion Freight Line
- FedEx Freight
- UPS Supply Chain
- Estes Express Lines

Data Quality:
- Carrier name must match across 214 records
- Freight charges must be positive
- Origin/destination must have valid N4 segments
""",

        "medallion_architecture.txt": """
Medallion Architecture - Supply Chain Sentinel

Bronze Layer:
- Raw, as-is data from EDI files
- All 150 records preserved (including bad ones)
- Schema: flat, string-typed fields
- Written by: ingestion/ingest_bronze.py

Silver Layer:
- Cleaned, validated, deduplicated
- Date validation: rejects month>12, day>31, missing year
- Deduplication: keeps latest per (po_number, transaction_type)
- Quarantine: bad records written to silver_quarantine with reason
- Schema: proper types (DateType, DoubleType, IntegerType)
- Written by: pipeline/transforms/bronze_to_silver.py

Gold Layer:
- Aggregated KPIs for business consumption
- 4 tables:
  1. kpis_daily: date, total_shipments, on_time_rate, avg_weight_kg
  2. carrier_perf: carrier_name, on_time_rate, avg_freight_cost, total_freight_cost
  3. exceptions_by_dest: ship_to_city, late_count, affected_pos
  4. volume_trend: week, transaction_type, count
- Written by: pipeline/transforms/silver_to_gold.py

Data Flow:
EDI Files → Bronze (150) → Silver (137) → Gold (4 tables)
                    ↓
          Silver Quarantine (5-7)

Orchestration: Airflow 3 (LocalExecutor)
Storage: Delta Lake on local filesystem
""",
    }

    for filename, content in specs.items():
        (docs_path / filename).write_text(content.strip())

    print(f"✅ Created {len(specs)} seed docs in {docs_dir}/")
    return len(specs)


if __name__ == "__main__":
    # Create seed docs, then ingest them
    create_seed_docs()
    ingest_docs()
    # Test a query
    print("\n--- Test Query ---")
    print(rag_lookup("What segments are required in an 856 ASN?"))   
