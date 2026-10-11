"""Dawn-forge 2026-10-11 Track A tests (slices 1-5): roadmap graph + discovery."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

import draupnir_forge.discovery as discovery
from draupnir_forge.discovery import DiscoveryReport
from draupnir_forge.roadmap import TaskGraph


def _seed(project_dir: Path, tasks) -> Path:
    mythis = project_dir / ".mythis"
    mythis.mkdir(parents=True, exist_ok=True)
    (mythis / "roadmap.json").write_text(json.dumps(tasks), encoding="utf-8")
    return mythis


def _task(task_id, depends_on=(), status="ready"):
    return {
        "task_id": task_id, "title": f"Task {task_id}", "domain": "test",
        "status": status, "depends_on": list(depends_on),
        "goal": "g", "constraints": [], "acceptance": [], "verification": [],
    }


def _diamond():
    """A -> B -> D, A -> C -> D (A=T-001, B=T-002, C=T-003, D=T-004)."""
    return [
        _task("T-001"),
        _task("T-002", ["T-001"]),
        _task("T-003", ["T-001"]),
        _task("T-004", ["T-002", "T-003"]),
    ]


def _three_cycle():
    return [
        _task("T-101", ["T-103"]),
        _task("T-102", ["T-101"]),
        _task("T-103", ["T-102"]),
    ]


# -- slice 1: roadmap/find-cycles -------------------------------------------


class TestFindCycles:
    def test_acyclic_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), _diamond())
            graph = TaskGraph(tmp)
            assert graph.find_cycles() == []

    def test_single_task_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            assert graph.find_cycles() == []

    def test_three_cycle_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), _diamond() + _three_cycle())
            graph = TaskGraph(tmp)
            cycles = graph.find_cycles()
            assert len(cycles) == 1
            cycle = cycles[0]
            assert set(cycle) == {"T-101", "T-102", "T-103"}
            # Closed path: starts and ends at the same id, matching
            # the private _find_cycle convention.
            assert cycle[0] == cycle[-1]
            assert len(cycle) == 4

    def test_each_cycle_edge_is_a_dep_edge(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), _three_cycle())
            graph = TaskGraph(tmp)
            cycles = graph.find_cycles()
            for cycle in cycles:
                for a, b in zip(cycle, cycle[1:]):
                    assert b in graph.get(a).depends_on

    def test_two_disjoint_cycles_both_found(self):
        other = [
            _task("T-201", ["T-202"]),
            _task("T-202", ["T-201"]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), _three_cycle() + other)
            graph = TaskGraph(tmp)
            cycles = graph.find_cycles()
            assert len(cycles) == 2
            covered = {tid for cycle in cycles for tid in cycle}
            assert {"T-101", "T-102", "T-103", "T-201", "T-202"} <= covered

    def test_self_loop_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-301", ["T-301"])])
            graph = TaskGraph(tmp)
            cycles = graph.find_cycles()
            assert cycles == [["T-301", "T-301"]]

    def test_unknown_deps_ignored_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-401", ["T-404"])])
            graph = TaskGraph(tmp)
            assert graph.find_cycles() == []


# -- slice 2: roadmap/critical-path ------------------------------------------


class TestCriticalPath:
    def test_empty_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            assert graph.critical_path() == []

    def test_diamond_picks_longest_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), _diamond())
            graph = TaskGraph(tmp)
            path = graph.critical_path()
            assert len(path) == 3
            # Tie between [T-001, T-002, T-004] and [T-001, T-003, T-004]:
            # first in insertion order wins.
            assert path == ["T-001", "T-002", "T-004"]

    def test_chain_runs_root_to_leaf(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [
                _task("T-001"),
                _task("T-002", ["T-001"]),
                _task("T-003", ["T-002"]),
                _task("T-004", ["T-003"]),
            ])
            graph = TaskGraph(tmp)
            assert graph.critical_path() == ["T-001", "T-002", "T-003", "T-004"]

    def test_independent_tasks_longest_single(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001"), _task("T-002")])
            graph = TaskGraph(tmp)
            assert graph.critical_path() == ["T-001"]

    def test_longest_wins_over_branches(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), _diamond() + [
                _task("T-005", ["T-004"]),
                _task("T-006", ["T-005"]),
            ])
            graph = TaskGraph(tmp)
            assert graph.critical_path() == [
                "T-001", "T-002", "T-004", "T-005", "T-006"]

    def test_cyclic_graph_terminates(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), _three_cycle())
            graph = TaskGraph(tmp)
            path = graph.critical_path()
            # No infinite recursion; a simple (non-repeating) path is returned.
            assert len(path) == len(set(path))


# -- slice 3: roadmap/add-task-title-required ---------------------------------


class TestAddTaskTitleRequired:
    def test_missing_title_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            with pytest.raises(ValueError, match="title"):
                graph.add_task({"domain": "x"})

    def test_blank_title_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            with pytest.raises(ValueError, match="title"):
                graph.add_task({"title": "   ", "domain": "x"})

    def test_empty_title_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            with pytest.raises(ValueError, match="title"):
                graph.add_task({"title": "", "domain": "x"})

    def test_valid_title_adds_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            added = graph.add_task({"title": "Carve the rune", "domain": "x"})
            assert added.title == "Carve the rune"
            assert added.task_id in graph.task_ids()


# -- slice 4: discovery/file-size-cap ------------------------------------------


class TestFileSizeCap:
    def test_default_cap_is_5_mib(self):
        assert discovery.max_file_bytes == 5 * 1024 * 1024

    def test_oversized_file_skipped_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            big = Path(tmp) / "blob.bin"
            big.write_bytes(b"")
            os.truncate(big, 10 * 1024 * 1024)  # 10 MiB sparse
            Path(tmp, "small.py").write_text("x = 1\n", encoding="utf-8")
            report = DiscoveryReport(tmp, {})
            assert report.skipped_files() == ["blob.bin"]
            assert "blob.bin" not in [r for r, _, _ in report._inventory()]
            assert "small.py" in [r for r, _, _ in report._inventory()]

    def test_file_at_cap_is_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            edge = Path(tmp) / "edge.bin"
            edge.write_bytes(b"")
            os.truncate(edge, discovery.max_file_bytes)  # exactly 5 MiB
            report = DiscoveryReport(tmp, {})
            assert report.skipped_files() == []
            assert "edge.bin" in [r for r, _, _ in report._inventory()]

    def test_skipped_files_never_get_lines_counted(self, monkeypatch):
        monkeypatch.setattr(discovery, "max_file_bytes", 1024)
        with tempfile.TemporaryDirectory() as tmp:
            # 6000 lines (~120 KiB): big enough to be a "huge" file risk,
            # small enough to build fast, but over the patched cap.
            Path(tmp, "huge.py").write_text(
                "x = 1\n" * 6000, encoding="utf-8")
            report = DiscoveryReport(tmp, {})
            assert report.skipped_files() == ["huge.py"]
            assert "huge.py" not in [r for r, _, _ in report._inventory()]
            risks = report.risks()
            assert not any("huge.py" in risk for risk in risks)


# -- slice 5: discovery/write-atomic ---------------------------------------------


class TestWriteAtomic:
    def test_write_returns_same_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "main.py").write_text("print('hi')\n", encoding="utf-8")
            report = DiscoveryReport(tmp, {})
            result = report.write()
            assert set(result) == {"report_path", "risks"}
            assert result["report_path"].endswith("DISCOVERY_REPORT.md")
            assert isinstance(result["risks"], list)

    def test_report_parses_and_no_tmp_residue(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "main.py").write_text("print('hi')\n", encoding="utf-8")
            report = DiscoveryReport(tmp, {})
            result = report.write()
            report_path = Path(result["report_path"])
            assert report_path.is_file()
            text = report_path.read_text(encoding="utf-8")
            assert text.startswith("# Discovery Report")
            leftover = list((Path(tmp) / ".mythis").glob("*.tmp"))
            assert leftover == []
