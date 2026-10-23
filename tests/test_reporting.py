import json
from pathlib import Path

import pytest

from scripts.benchmark import MATRIX, config_for
from scripts.report import summarize


def test_experiments_change_one_conceptual_variable():
    for name, experiment in MATRIX.items():
        if "parent" not in experiment:
            continue
        parent = config_for(experiment["parent"])
        child = config_for(name)
        differences = {key for key in parent if parent[key] != child[key]}
        assert differences == set(experiment["changes"])
        assert len(differences) == 1 or differences == {"DB_HOST", "DB_PORT"}


def test_summary_uses_median_not_best_case_and_preserves_missing_metrics():
    runs = []
    for p95 in [40, 200, 90]:
        runs.append(
            {
                "experiment": "baseline",
                "users": 1000,
                "profile_sha256": "test",
                "total": {"rps": 100, "p50_ms": 10, "p95_ms": p95, "p99_ms": 300},
                "error_rate": 0,
                "cache_hit_rate": None,
                "endpoints": [],
                "samples": [],
            }
        )
    summary = summarize(runs)["baseline"]
    assert summary["p95_ms"] == 90
    assert summary["cache_hit_rate"] is None
    assert summary["read_p95_ms"] is None
    assert summary["client_connections_min"] is None


def test_observability_files_parse():
    import yaml

    root = Path(__file__).resolve().parents[1]
    for path in (root / "infra").rglob("*.yml"):
        assert yaml.safe_load(path.read_text()) is not None
    for path in (root / "infra/grafana/dashboards").glob("*.json"):
        assert json.loads(path.read_text())["panels"]


def test_report_rejects_incomparable_workloads():
    with pytest.raises(ValueError, match="different workload"):
        summarize([{"experiment": "baseline", "users": 10}, {"experiment": "tuned", "users": 1000}])


def test_successful_throughput_is_computed_per_trial_before_taking_median():
    runs = [
        {
            "experiment": "baseline",
            "users": 1000,
            "profile_sha256": "test",
            "total": {"rps": rate, "p50_ms": 10, "p95_ms": 20, "p99_ms": 30},
            "error_rate": errors,
            "endpoints": [],
        }
        for rate, errors in [(100, 0), (200, 0.2), (300, 0.9)]
    ]
    assert summarize(runs)["baseline"]["successful_rps"] == 100
