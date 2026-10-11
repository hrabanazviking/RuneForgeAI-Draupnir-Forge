"""Dawn-forge Track C tests (slices 11-15): decisions validation/dedupe,
reground dry-run, cli roadmap --validate, verifier GateResult serialize."""

from __future__ import annotations

import json

import pytest

from draupnir_forge.cli import main
from draupnir_forge.decisions import DecisionLedger
from draupnir_forge.reground import reground
from draupnir_forge.roles.verifier import GateResult


# -- slice 11: decisions/record-validation --------------------------------


def test_record_rejects_empty_and_whitespace_before_touching_file(tmp_path):
    ledger = DecisionLedger(str(tmp_path))
    with pytest.raises(ValueError):
        ledger.record("", "reason")
    with pytest.raises(ValueError):
        ledger.record("   ", "reason")
    with pytest.raises(ValueError):
        ledger.record(" \t\n ", "reason")
    # The ledger file was never touched.
    assert not ledger.path.exists()


def test_record_blank_leaves_existing_ledger_untouched(tmp_path):
    ledger = DecisionLedger(str(tmp_path))
    ledger.record("Adopt the raven post", "omens were clear")
    before = ledger.path.read_bytes()
    with pytest.raises(ValueError):
        ledger.record("  ", "reason")
    assert ledger.path.read_bytes() == before
    assert len(ledger.entries()) == 1


def test_record_still_accepts_normal_decision(tmp_path):
    ledger = DecisionLedger(str(tmp_path))
    path = ledger.record("Chart the northern isles", "a skald asked")
    assert path == ledger.path
    assert len(ledger.entries()) == 1
    assert ledger.entries()[0]["decision"] == "Chart the northern isles"


# -- slice 12: decisions/dedupe -------------------------------------------


def test_record_dedupes_identical_decision(tmp_path):
    ledger = DecisionLedger(str(tmp_path))
    first = ledger.record("Ship the rune-carver", "first telling")
    assert len(ledger.entries()) == 1
    second = ledger.record("Ship the rune-carver", "retry by another role")
    assert second == first
    assert len(ledger.entries()) == 1  # grew by exactly 1 from zero
    # Whitespace variants of the same text also dedupe.
    ledger.record("  Ship the rune-carver\n", "third telling")
    assert len(ledger.entries()) == 1
    # A different decision still appends normally.
    ledger.record("Bless the longship", "new tidings")
    assert len(ledger.entries()) == 2


# -- slice 13: reground/dry-run --------------------------------------------


def _stale_project(root):
    """Project whose T-001 is done but every file it references is gone."""
    mythis = root / ".mythis"
    mythis.mkdir()
    roadmap = [
        {
            "task_id": "T-001",
            "title": "Carve the rune-stones",
            "status": "done",
            "notes": "shaped src/carver.py into src/rune_carver.py",
        },
        {"task_id": "T-002", "title": "Polish the keel", "status": "ready"},
    ]
    (mythis / "roadmap.json").write_text(
        json.dumps(roadmap), encoding="utf-8")
    return root


def test_dry_run_returns_stale_plan_without_writing(tmp_path):
    root = _stale_project(tmp_path)
    roadmap_path = root / ".mythis" / "roadmap.json"
    before = roadmap_path.read_bytes()
    plan = reground(root, dry_run=True)
    # The plan identifies the stale task ...
    assert plan["dry_run"] is True
    assert plan["stale"] == ["T-001"]
    assert plan["changed"] is True
    # ... and nothing was written or emitted.
    assert roadmap_path.read_bytes() == before
    assert not (root / ".mythis" / "events.jsonl").exists()


def test_dry_run_clean_project_reports_no_change(tmp_path):
    root = tmp_path / "clean"
    root.mkdir()
    plan = reground(root, dry_run=True)
    assert plan["dry_run"] is True
    assert plan["stale"] == []
    assert plan["changed"] is False
    # No .mythis tree was created by the dry run.
    assert not (root / ".mythis").exists()


def test_dry_run_does_not_mark_stale_but_real_run_would(tmp_path):
    root = _stale_project(tmp_path)
    reground(root, dry_run=True)
    assert "re-grounding" not in (root / ".mythis" / "roadmap.json").read_text(
        encoding="utf-8")


# -- slice 14: cli/roadmap-validate -----------------------------------------


def _write_roadmap(root, tasks):
    mythis = root / ".mythis"
    mythis.mkdir()
    (mythis / "roadmap.json").write_text(
        json.dumps(tasks), encoding="utf-8")


def test_roadmap_validate_ok(tmp_path, capsys):
    _write_roadmap(tmp_path, [
        {"task_id": "T-001", "title": "Raise the mast", "status": "done"},
        {"task_id": "T-002", "title": "Hoist the sail", "status": "ready",
         "depends_on": ["T-001"]},
    ])
    rc = main(["--project-dir", str(tmp_path), "roadmap", "--validate"])
    out = capsys.readouterr()
    assert rc == 0
    assert "roadmap valid (2 tasks)" in out.out


def test_roadmap_validate_cycle(tmp_path, capsys):
    _write_roadmap(tmp_path, [
        {"task_id": "T-001", "title": "A", "status": "ready",
         "depends_on": ["T-002"]},
        {"task_id": "T-002", "title": "B", "status": "ready",
         "depends_on": ["T-001"]},
    ])
    rc = main(["--project-dir", str(tmp_path), "roadmap", "--validate"])
    out = capsys.readouterr()
    assert rc == 2
    assert "cycle" in (out.out + out.err).lower()


def test_roadmap_validate_unknown_dependency(tmp_path, capsys):
    _write_roadmap(tmp_path, [
        {"task_id": "T-001", "title": "A", "status": "ready",
         "depends_on": ["T-999"]},
    ])
    rc = main(["--project-dir", str(tmp_path), "roadmap", "--validate"])
    out = capsys.readouterr()
    assert rc == 2
    assert "unknown" in (out.out + out.err).lower()


# -- slice 15: verifier/gateresult-serialize --------------------------------


def test_gateresult_round_trip():
    gate = GateResult(
        gate="code", passed=True, evidence="all files exist", skipped=False)
    restored = GateResult.from_dict(gate.to_dict())
    assert (restored.gate, restored.passed, restored.evidence,
            restored.skipped) == (
        gate.gate, gate.passed, gate.evidence, gate.skipped)


def test_gateresult_from_dict_tolerates_unknown_keys():
    gate = GateResult.from_dict({
        "gate": "test",
        "passed": False,
        "evidence": "no test result",
        "skipped": True,
        "duration_s": 1.5,   # unknown key: ignored
        "name": "legacy",    # unknown key: ignored
    })
    assert gate.gate == "test"
    assert gate.passed is False
    assert gate.evidence == "no test result"
    assert gate.skipped is True


def test_gateresult_from_dict_defaults_for_missing_keys():
    gate = GateResult.from_dict({})
    assert gate.gate == ""
    assert gate.passed is False
    assert gate.evidence == ""
    assert gate.skipped is False
