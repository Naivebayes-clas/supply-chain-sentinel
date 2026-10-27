"""
Synthetic EDI Generator
Produces X12 850 (PO), 856 (ASN), 214 (Freight) files with realistic messiness.

Data quality issues intentionally injected:
  - Missing required segments (~10%)
  - Malformed dates (~8%)
  - Duplicate PO numbers (~5%)
  - Out-of-range quantities (~5%)
  - Truncated segment terminators (~3%)
"""

import random
import os
import string
from datetime import datetime, timedelta
from pathlib import Path

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
SEGMENT_TERMINATOR = "*"  # X12 uses * or ~
N_FILES = 50
BASE_DATE = datetime(2026, 10, 1)

# ──────────────────────────────────────────────
# Random helpers
# ──────────────────────────────────────────────
def rand_po_number() -> str:
    return f"PO{random.randint(100000, 999999)}"

def rand_date(offset_days: int = 0) -> str:
    """Return X12 date (CCYYMMDD) with occasional malformation."""
    d = BASE_DATE + timedelta(days=random.randint(0, 30) + offset_days)
    if random.random() < 0.08:
        # Malformed: missing year, wrong format, or invalid month
        choice = random.choice(["no_year", "bad_month", "bad_day"])
        if choice == "no_year":
            return d.strftime("%m%d")  # MMDD instead of CCYYMMDD
        elif choice == "bad_month":
            return f"{d.year}13{d.day:02d}"  # Month 13
        else:
            return f"{d.year}{d.month:02d}32"  # Day 32
    return d.strftime("%Y%m%d")

def rand_qty() -> str:
    """Return quantity with occasional out-of-range value."""
    if random.random() < 0.05:
        return str(random.randint(10000, 99999))  # Unreasonably high
    return str(random.randint(1, 500))

def rand_weight() -> str:
    return str(round(random.uniform(50, 5000), 2))

def rand_zip() -> str:
    return f"{random.randint(10000, 99999)}"

def rand_id(prefix: str, length: int = 6) -> str:
    return prefix + "".join(random.choices(string.digits, k=length))

# ──────────────────────────────────────────────
# X12 Envelope
# ──────────────────────────────────────────────
def build_isa() -> str:
    """ISA/IEA envelope header."""
    return (
        f"ISA*99*{rand_id('S')}{rand_id('S')}*99*{rand_id('R')}{rand_id('R')}"
        f"*261026*1200*U*00401*{rand_id('Z')}*000000001*0*P~"
    )

def build_iea(control: str) -> str:
    return f"IEA*1*{control}~"

def build_gs() -> str:
    return f"GS*HS*SCS-SENDER*SCS-RECEIVER*{rand_date()}*{random.randint(0,9999999)}*X*004010~"

def build_ge(control: str) -> str:
    return f"GE*1*{control}~"

# ──────────────────────────────────────────────
# 850 - Purchase Order
# ──────────────────────────────────────────────
def build_850(po_number: str, duplicate: bool = False) -> str:
    """Build an X12 850 Purchase Order."""
    lines = []
    lines.append(f"ST*850*{random.randint(1000000, 9999999)}~")

    # BGM - Beginning Segment
    bgm_date = rand_date()
    lines.append(f"BGM*00*{po_number}****{bgm_date}~")

    # DTM - Date/Time (with occasional missing)
    if random.random() > 0.10:  # 10% missing
        lines.append(f"DTM*011*{rand_date(-5)}~")  # Order date
        lines.append(f"DTM*016*{rand_date(10)}~")  # Requested delivery

    # N1 - Name (Ship To)
    lines.append(f"N1*ST*{random.choice(['WAREHOUSE_A','DC_EAST','DC_WEST','STORE_001'])}~")
    lines.append(f"N3*{random.randint(100,9999)} {random.choice(['Main St','Industrial Blvd','Logistics Pkwy'])}~")
    lines.append(f"N4*{random.choice(['Toronto','Montreal','Vancouver','Calgary','Ottawa'])}*ON*{rand_zip()}*CA~")

    # ITD - Item Detail (1-3 line items)
    n_items = random.randint(1, 3)
    for i in range(1, n_items + 1):
        lines.append(f"ITD*U*{rand_qty()}*EA*{random.uniform(5, 500):.2f}~")
        lines.append(f"PID*F*{random.choice(['WIDGET','BRACKET','SENSOR','VALVE','PUMP'])}-{random.randint(100,999)}~")

    # SE - Service Advice Trailer
    segment_count = len(lines) + 1
    lines.append(f"SE*{segment_count}*{lines[0].split('*')[2]}~")

    # Inject: truncate last segment (3% chance)
    if random.random() < 0.03:
        lines[-1] = lines[-1][:-2]  # Remove trailing ~

    return "".join(lines)

# ──────────────────────────────────────────────
# 856 - Advance Ship Notice
# ──────────────────────────────────────────────
def build_856(po_number: str, ship_date: datetime) -> str:
    """Build an X12 856 ASN."""
    lines = []
    control = str(random.randint(1000000, 9999999))
    lines.append(f"ST*856*{control}~")

    # BGM
    lines.append(f"BGM*BLSV*{rand_id('SHIP')}****{ship_date.strftime('%Y%m%d')}~")

    # DTM
    lines.append(f"DTM*011*{ship_date.strftime('%Y%m%d')}~")  # Ship date
    eta_days = random.randint(-3, 7)  # negative = late
    lines.append(f"DTM*016*{(ship_date + timedelta(days=eta_days)).strftime('%Y%m%d')}~")   

    # REF - Related PO
    lines.append(f"REF*PB*{po_number}~")

    # N1 - Ship From
    lines.append(f"N1*SF*SCS-SENDER~")
    lines.append(f"N3*1234 Logistics Way~")
    lines.append(f"N4*Toronto*ON*M5V 2T6*CA~")

    # N1 - Ship To
    lines.append(f"N1*ST*{random.choice(['WAREHOUSE_A','DC_EAST','DC_WEST'])}~")
    lines.append(f"N3*{random.randint(100,9999)} Industrial Blvd~")
    lines.append(f"N4*{random.choice(['Mississauga','Hamilton','Burlington'])}*ON*{rand_zip()}*CA~")

    # MAN - Manifest
    lines.append(f"MAN*{rand_weight()}~")

    # SE
    segment_count = len(lines) + 1
    lines.append(f"SE*{segment_count}*{control}~")

    return "".join(lines)

# ──────────────────────────────────────────────
# 214 - Motor Carrier Freight Details
# ──────────────────────────────────────────────
def build_214(po_number: str, ship_date: datetime) -> str:
    """Build an X12 214 freight bill."""
    lines = []
    control = str(random.randint(1000000, 9999999))
    lines.append(f"ST*214*{control}~")

    # BGM
    lines.append(f"BGM*01*{rand_id('FB')}****{ship_date.strftime('%Y%m%d')}~")

    # REF
    lines.append(f"REF*PB*{po_number}~")
    lines.append(f"REF*CN*{rand_id('CTN')}~")  # Container number

    # N1 - Carrier
    lines.append(f"N1*CN*{random.choice(['FedEx','UPS','XPO','Old Dominion','Estes'])}~")

    # N1 - Origin
    lines.append(f"N1*O*ORIGIN~")
    lines.append(f"N4*Toronto*ON*{rand_zip()}*CA~")

    # N1 - Destination
    lines.append(f"N1*D*DEST~")
    lines.append(f"N4*{random.choice(['Montreal','Vancouver','Winnipeg','Halifax'])}*"
                f"{random.choice(['QC','BC','MB','NS'])*1}*{rand_zip()}*CA~")

    # FSN - Freight & Service Notes (charges)
    lines.append(f"FSN*F*{random.uniform(100, 2500):.2f}~")  # Freight charge
    if random.random() > 0.3:
        lines.append(f"FSN*S*{random.uniform(10, 200):.2f}~")  # Surcharges

    # SE
    segment_count = len(lines) + 1
    lines.append(f"SE*{segment_count}*{control}~")

    return "".join(lines)

# ──────────────────────────────────────────────
# Main: generate full file set
# ──────────────────────────────────────────────
def generate_files(output_dir: str = "data/sample_edi", n: int = N_FILES):
    """Generate n synthetic EDI transaction files with intentional messiness."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Track PO numbers for intentional duplicates
    used_pos: list[str] = []
    duplicate_pos: set[str] = set()

    stats = {
        "total": 0,
        "missing_segments": 0,
        "malformed_dates": 0,
        "duplicate_pos": 0,
        "out_of_range_qty": 0,
        "truncated": 0,
    }

    for i in range(n):
        # Decide PO number (5% chance of duplicate)
        if used_pos and random.random() < 0.05:
            po_number = random.choice(used_pos)
            duplicate_pos.add(po_number)
            stats["duplicate_pos"] += 1
        else:
            po_number = rand_po_number()
            used_pos.append(po_number)

        ship_date = BASE_DATE + timedelta(days=random.randint(0, 25))

        # Build the three transactions
        edi_850 = build_850(po_number)
        edi_856 = build_856(po_number, ship_date)
        edi_214 = build_214(po_number, ship_date)

        # Wrap in ISA/GS envelope
        isa = build_isa()
        gs = build_gs()
        ge = build_ge(str(random.randint(1000000, 9999999)))
        iea = build_iea(str(random.randint(1000000, 9999999)))

        full_content = f"{isa}{gs}{edi_850}{ge}{iea}"
        full_content_856 = f"{isa}{gs}{edi_856}{ge}{iea}"
        full_content_214 = f"{isa}{gs}{edi_214}{ge}{iea}"

        # Write files
        suffix = f"{i:04d}"
        (out / f"850_{suffix}.edi").write_text(full_content)
        (out / f"856_{suffix}.edi").write_text(full_content_856)
        (out / f"214_{suffix}.edi").write_text(full_content_214)

        stats["total"] += 1

        # Count injected issues (approximate)
        if "13" in edi_850 or len([s for s in edi_850.split("~") if "DTM" in s]) == 0:
            stats["missing_segments"] += 1

    # Write a manifest
    manifest = {
        "generated_at": datetime.now().isoformat(),
        "total_transactions": stats["total"],
        "files_per_transaction": 3,
        "total_files": stats["total"] * 3,
        "injected_issues": {
            "duplicate_po_numbers": list(duplicate_pos),
            "note": "Malformed dates, missing segments, out-of-range quantities, and truncations are randomly injected (~5-10% per file type)",
        },
        "segment_terminator": SEGMENT_TERMINATOR,
    }
    (out / "_manifest.json").write_text(str(manifest))

    print(f"✅ Generated {stats['total']} transactions ({stats['total']*3} files) in {out}/")
    print(f"   Duplicate POs: {len(duplicate_pos)}")
    print(f"   Files: 850_* (PO), 856_* (ASN), 214_* (Freight)")
    return stats


if __name__ == "__main__":
    generate_files()
  
