[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## How It Works

### The Story

Imagine you run a logistics company. Every day, your customers send you
electronic shipping documents — purchase orders, "we shipped your stuff"
notices, and freight bills. They arrive as ugly, machine-readable text files
called **EDI files** (X12 format). No human can read them. No spreadsheet
can open them.

**Supply Chain Sentinel** is the system that turns that chaos into answers.

---

### Step 1: The Data Arrives (Ingestion)

Your system generates (or receives) 150 EDI files. Each file is a wall of
text like this:

ISA99S216615S03138899R569791R9590882610261200U00401Z7642790000000010P~
GSHSSCS-SENDERSCS-RECEIVER202610228624394X004010~
ST8507990823~
BGM00PO211098**20261019~
N1ST*DC_EAST~
...

In this, **X12 parser** reads each file, splits it into segments (the parts
between `~`), and extracts the fields that matter: PO number, ship date,
destination city, carrier name, freight cost. It writes everything — raw and
uncleaned — into a **Bronze** table. Think of Bronze as a mailroom: everything
lands here, sorted but not yet read.

> **Real-world twist:** Not all files are clean. About 8% have malformed dates
> (month 13, day 32), 5% have duplicate PO numbers, and 10% are missing
> required segments. The parser doesn't care — it records everything as-is.
> That's the job of the next step.

---

### Step 2: Cleaning (Bronze → Silver)

The **Silver** transform is the quality control team. It:

1. **Validates dates** — "2026-13-45" is not a real date. Flag it.
2. **Deduplicates** — Two records with the same PO number? Keep the latest.
3. **Quarantines** — Bad records don't get deleted. They go to a
   `silver_quarantine` table with a reason ("invalid_order_date"). A human
   can review them later.
4. **Types the data** — "20261022" becomes an actual date. "1234.56" becomes
   a number, not a string.

Result: 150 raw records → 137 clean records + 5 quarantined.

---

### Step 3: Making It Useful (Silver → Gold)

The **Gold** layer is what a VP actually looks at. It computes:

- **kpis_daily** — "On Oct 14, we shipped 1 package and it was late. On Oct 8,
  we shipped 3 and all were on time."
- **carrier_perf** — "XPO is 54% on-time. Old Dominion is 85%. FedEx is 78%."
- **exceptions_by_dest** — "Mississauga has 7 late shipments. Burlington has 6."
- **volume_trend** — "We processed 49 purchase orders, 43 ASNs, 49 freight bills."

These four small tables are what the AI agent queries. No one is reading
150 raw EDI files anymore.

---

### Step 4: The Agent Answers Questions (LangGraph)

Now a logistics manager opens the chat and types:

> *"Which carrier should I fire?"*

Here's what happens in ~6 seconds:

1. **Planner** (keyword router, zero cost): "This is a carrier question.
   Route to SQL."
2. **NL→SQL node** (GPT-4o): Generates
   `SELECT carrier_name, on_time_rate FROM carrier_perf ORDER BY on_time_rate ASC LIMIT 1`
3. **Execute node** (PySpark + Delta Lake): Runs the query, gets back
   `{"carrier_name": "XPO", "on_time_rate": "0.5455"}`
4. **Respond node** (GPT-4o): "XPO has the worst on-time rate at 54.5%.
   This is significantly below the fleet average of 69%."

That's the full pipeline. No human touched a spreadsheet.

**Six routing paths** handle different question types:

| You ask...                        | It does...                                    |
| --------------------------------- | --------------------------------------------- |
| "Which carrier is worst?"         | Generates SQL, queries Delta, answers         |
| "Any anomalies?"                  | Runs z-score on daily KPIs, flags outliers    |
| "What's in an 856 ASN?"           | Searches EDI spec docs (RAG), quotes segments |
| "Show me the schema"              | Returns table definitions directly            |
| "Where are late shipments going?" | Queries exceptions table                      |
| "Show carrier performance"        | Dumps the full carrier table                  |

---

### Step 5: You Can Verify It's Not Lying (Evals)

Wrote 40 questions with known-correct answers. An LLM judge (GPT-4o-mini)
scores each response on correctness and checks for hallucination.

**Result: 95% pass rate. 0% hallucination.** The agent doesn't make up
carriers or cities. If it doesn't know, it says so.

---

This project demonstrates **both sides** of the modern data+AI stack:

- **Data Engineering:** Pipelines, data modeling, SQL/Spark, orchestration,
  data quality handling, medallion architecture
- **Agentic AI:** LLM orchestration, tool calling, state management,
  RAG, eval design, observability, production thinking (Docker, CI, API)

It's a working system with a `docker compose up` that
starts everything, a committed eval report, and a UI you can demo in 2 minutes.

## Debugging Log

| #   | Error                                                                | Root Cause                                                                                           | Fix                                                                |
| --- | -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 1   | `python-edi-parser` not found on PyPI                                | Hallucinated package name                                                                            | Replaced with `pyedi`                                              |
| 2   | Airflow init: `exit 127` (command not found)                         | `airflow users create` doesn't exist in Airflow 3 (SimpleAuthManager)                                | Removed the command; admin/admin is auto-generated                 |
| 3   | `schedule_interval` TypeError                                        | Renamed to `schedule` in Airflow 3                                                                   | One-word fix                                                       |
| 4   | DAG not appearing in UI                                              | Airflow 3 requires a separate `dag-processor` service                                                | Added the service to compose                                       |
| 5   | `DeltaSparkSession` ImportError                                      | That class doesn't exist; Delta works via data source registration                                   | Removed the import; added `spark.sql.extensions` config            |
| 6   | `JAVA_HOME is not set`                                               | `apache/airflow` image doesn't include Java                                                          | Added `default-jre-headless` to Dockerfile                         |
| 7   | `pip install` as root blocked                                        | Airflow image's pip wrapper forbids root                                                             | Split USER: root for apt-get, airflow for pip                      |
| 8   | `scala/collection/IterableOnce` NoClassDefFoundError                 | Delta jar `_2.13` vs PySpark's Scala 2.12                                                            | Changed to `delta-spark_2.12:3.0.0`                                |
| 9   | `Mkdirs failed` on Delta write                                       | Nested directory didn't exist on host bind mount                                                     | `mkdir -p data/lake/bronze` on host                                |
| 10  | `sum(col(...))` → "Column is not iterable"                           | Python's built-in `sum` shadows PySpark's `sum`                                                      | Aliased: `from pyspark.sql.functions import sum as spark_sum`      |
| 11  | `UNRESOLVED_COLUMN: related_po`                                      | Column was already renamed to `po_number` during Bronze ingestion                                    | Used the correct column name                                       |
| 12  | `AMBIGUOUS_REFERENCE: carrier`                                       | Both sides of a LEFT JOIN had a `carrier` column                                                     | Renamed to `carrier_name` before the join                          |
| 13  | `yyyy-Www` invalid DateTimeFormatter                                 | Not a valid Java pattern                                                                             | Replaced with `yyyy-MM`                                            |
| 14  | Spark dies after ~15 queries in one process                          | JVM gateway crashes silently; next call gets a dead session                                          | Added health check + auto-recreate in `_get_spark()`               |
| 15  | RAG returning wrong chunks                                           | Fixed-size char chunking split the 856 "Required Segments" section across two chunks                 | Section-aware chunking (split on blank lines)                      |
| 16  | `httpx.ConnectError: Name or service not known`                      | ChromaDB's default embedding function tried a remote API                                             | Pinned to local `ONNXMiniLM_L6_V2`                                 |
| 17  | Agent saying "I do not have that information" for every SQL question | SQL only selected the identifier, not the metric (e.g. `SELECT carrier_name` without `on_time_rate`) | Added rule to NL→SQL prompt: "ALWAYS include the metric in SELECT" |
| 18  | RAG LLM refusing to answer despite context being present             | Prompt included an escape hatch ("If context doesn't contain the answer, say...") and GPT-4o took it | Removed the bail-out; told LLM to extract from the doc             |
| 19  | `docker compose run --profile agent` → "unknown flag"                | `--profile` is for `up`, not `run`                                                                   | Use `docker compose run --rm app` directly                         |
| 20  | Port 8501 already allocated                                          | Previous Streamlit instance still bound                                                              | Use a different port or `kill` the old process                     |

## Core Stack

| Tech           | Version                     | Purpose                                                     |
| -------------- | --------------------------- | ----------------------------------------------------------- |
| **Python**     | 3.11 (app) / 3.12 (airflow) | Everything is written in Python                             |
| **Java (JRE)** | default-jre-headless        | Spark runtime (PySpark needs a JVM)                         |
| **PySpark**    | 3.5.1 (Scala 2.12)          | All data transforms (Bronze→Silver→Gold)                    |
| **Delta Lake** | 3.0.0                       | Storage: ACID transactions, time-travel, schema enforcement |
| **Airflow**    | 3.0.0                       | Orchestration: schedules and runs the 3-task DAG            |

## Agent / AI Layer

| Tech                               | Purpose                                                        |
| ---------------------------------- | -------------------------------------------------------------- |
| **LangGraph**                      | State machine: 6 routing paths, conditional edges, typed state |
| **LangChain** + `langchain-openai` | LLM integration (prompt → response)                            |
| **GPT-4o**                         | The "brain": NL→SQL generation + natural language answers      |
| **GPT-4o-mini**                    | Eval judge: scores 40 answers on correctness + hallucination   |
| **FastMCP**                        | MCP server: exposes 4 tools (`query_gold`, `get_schema`, etc.) |
| **ChromaDB**                       | Vector store for RAG (stores EDI spec chunks + embeddings)     |
| **ONNX MiniLM-L6-v2**              | Local embedding model (no API cost, no network call)           |

## API / UI Layer

| Tech          | Purpose                                                        |
| ------------- | -------------------------------------------------------------- |
| **FastAPI**   | REST API: `/query`, `/anomalies`, `/reports/weekly`, `/schema` |
| **Streamlit** | Web UI: Chat + Dashboard + Anomalies + EDI Docs tabs           |
| **Pydantic**  | Request/response validation in FastAPI                         |

## Infrastructure

| Tech               | Purpose                                                         |
| ------------------ | --------------------------------------------------------------- |
| **Docker Compose** | 7 services in one `docker compose up`                           |
| **Postgres 16**    | Airflow metadata DB + Langfuse DB                               |
| **Langfuse**       | Observability: traces every agent call (traces, spans, latency) |
| **Makefile**       | `make up`, `make ingest`, `make eval`, `make test`              |

## Dev / Quality

| Tech               | Purpose                             |
| ------------------ | ----------------------------------- |
| **pytest**         | Unit tests (API, transforms, agent) |
| **ruff**           | Linter                              |
| **mypy**           | Type checker                        |
| **GitHub Actions** | CI: lint + test on every push       |

https://github.com/user-attachments/assets/6d07137d-1f1f-4a2e-af44-36689dce2965

https://github.com/user-attachments/assets/5158ee74-6158-43dd-b55d-40dcf6915a63

<img width="1294" height="642" alt="carrier_performance_dashboard" src="https://github.com/user-attachments/assets/e17db5f7-334c-46ad-8091-9f0795fd0dd5" />

