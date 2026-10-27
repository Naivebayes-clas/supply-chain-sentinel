"""Unit tests for pipeline transforms."""
import pytest

def test_bronze_to_silver_runs():
    from pipeline.transforms.bronze_to_silver import run_transform
    # For now, just verify it doesn't crash
    run_transform()

def test_silver_to_gold_runs():
    from pipeline.transforms.silver_to_gold import run_aggregate
    run_aggregate()
EOF   
