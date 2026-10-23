import json

import pytest

from scripts import drill


def test_cleanup_keeps_recovery_journal_when_netem_remains(tmp_path, monkeypatch):
    monkeypatch.setattr(drill, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(drill, "ACTIVE", tmp_path / "active-drill.json")
    state = {"id": "test", "fault": "db-latency", "status": "active", "cleanup_network": True}
    drill.save(state)
    monkeypatch.setattr(drill, "net", lambda *args, **kwargs: "qdisc netem delay 200ms")
    with pytest.raises(RuntimeError, match="cleanup failed"):
        drill.heal(state)
    assert json.loads(drill.ACTIVE.read_text())["status"] == "active"


def test_old_drill_cannot_change_another_active_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(drill, "ACTIVE", tmp_path / "active-drill.json")
    drill.ACTIVE.write_text(json.dumps({"id": "current"}))
    with pytest.raises(RuntimeError, match="different drill"):
        drill.heal({"id": "old", "fault": "redis-outage", "status": "healed"})


def test_healing_a_completed_drill_does_not_restart_services(tmp_path, monkeypatch):
    monkeypatch.setattr(drill, "ACTIVE", tmp_path / "active-drill.json")

    def unexpected(*args, **kwargs):
        pytest.fail("An already-healed drill must not mutate Docker")

    monkeypatch.setattr(drill, "docker", unexpected)
    drill.heal({"id": "done", "fault": "redis-outage", "status": "healed"})


def test_diagnosis_comparison_excludes_validation_and_unpaired_faults(tmp_path, monkeypatch):
    monkeypatch.setattr(drill, "ARTIFACTS", tmp_path)
    records = [
        {"round": "logs-only", "fault": "redis-outage", "diagnosis_seconds": 200},
        {"round": "observability", "fault": "redis-outage", "diagnosis_seconds": 100},
        {"round": "validation", "fault": "redis-outage", "diagnosis_seconds": 1},
        {"round": "logs-only", "fault": "app-crash", "diagnosis_seconds": 500},
    ]
    for i, record in enumerate(records):
        (tmp_path / f"{i}.json").write_text(json.dumps(record))
    (tmp_path / "validation-summary.json").write_text("[]")
    drill.compare()
    report = json.loads((tmp_path / "diagnosis-comparison.json").read_text())
    assert report["paired_faults"] == ["redis-outage"]
    assert report["reduction_percent"] == 50
