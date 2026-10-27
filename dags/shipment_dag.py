"""
Shipment Pipeline DAG
Bronze → Silver → Gold
"""
from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator


def ingest_bronze(**kwargs):
    """Land raw EDI files into Bronze layer."""
    import sys
    sys.path.insert(0, '/opt/airflow')
    from ingestion.ingest_bronze import run_ingest
    run_ingest()
    print("✅ Bronze ingestion complete")


def bronze_to_silver(**kwargs):
    """Clean, dedup, schema-enforce → Silver."""
    import sys
    sys.path.insert(0, '/opt/airflow')
    from pipeline.transforms.bronze_to_silver import run_transform
    run_transform()
    print("✅ Silver transform complete")


def silver_to_gold(**kwargs):
    """Aggregate KPIs → Gold."""
    import sys
    sys.path.insert(0, '/opt/airflow')
    from pipeline.transforms.silver_to_gold import run_aggregate
    run_aggregate()
    print("✅ Gold aggregation complete")


default_args = {
    "owner": "data-engineer",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="shipment_dag",
    default_args=default_args,
    description="Shipment data pipeline: Bronze → Silver → Gold",
    schedule="@daily",
    start_date=datetime(2026, 10, 1),
    catchup=False,
    tags=["logistics", "edi", "medallion"],
) as dag:

    t_ingest = PythonOperator(
        task_id="ingest_bronze",
        python_callable=ingest_bronze,
    )

    t_silver = PythonOperator(
        task_id="bronze_to_silver",
        python_callable=bronze_to_silver,
    )

    t_gold = PythonOperator(
        task_id="silver_to_gold",
        python_callable=silver_to_gold,
    )

    t_ingest >> t_silver >> t_gold   
