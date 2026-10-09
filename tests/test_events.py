"""Slice 5 acceptance tests: EventType, ForgeEvent, EventLog."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime

from draupnir_forge.events import EventLog, EventType, ForgeEvent

# The closed set from spec §29 — the test must match it exactly.
EXPECTED_EVENT_TYPES = (
    "PROJECT_CREATED",
    "VISION_UPDATED",
    "DOMAIN_DISCOVERED",
    "ARCHITECTURE_UPDATED",
    "ROADMAP_REVISED",
    "TASK_STARTED",
    "TASK_FAILED",
    "TASK_REPAIRED",
    "TEST_FAILED",
    "TEST_PASSED",
    "INVARIANT_VIOLATED",
    "HUMAN_DECISION_REQUESTED",
    "HUMAN_DECISION_RECEIVED",
    "MODEL_SWITCHED",
    "CHECKPOINT_CREATED",
    "TASK_COMPLETED",
    "PROJECT_REGROUNDED",
    "PROJECT_COMPLETED",
)


class TestEventType(unittest.TestCase):
    def test_exactly_the_spec_set(self):
        actual = [e.value for e in EventType]
        self.assertEqual(len(EventType), 18)
        self.assertEqual(set(actual), set(EXPECTED_EVENT_TYPES))

    def test_values_match_names(self):
        for member in EventType:
            self.assertEqual(member.value, member.name)


class TestForgeEvent(unittest.TestCase):
    def _event(self) -> ForgeEvent:
        return ForgeEvent(
            seq=7,
            ts="2026-10-09T08:31:00+00:00",
            type=EventType.TASK_STARTED,
            actor_role="Architect",
            project="demo",
            payload={"task": "slice-5"},
            caused_by=3,
        )

    def test_round_trip_dict(self):
        event = self._event()
        data = event.to_dict()
        self.assertEqual(data["type"], "TASK_STARTED")
        rebuilt = ForgeEvent.from_dict(data)
        self.assertEqual(rebuilt, event)

    def test_round_trip_json(self):
        event = self._event()
        rebuilt = ForgeEvent.from_dict(json.loads(json.dumps(event.to_dict())))
        self.assertEqual(rebuilt, event)

    def test_from_dict_rejects_unknown_type(self):
        data = self._event().to_dict()
        data["type"] = "NOT_A_REAL_EVENT"
        with self.assertRaises(ValueError):
            ForgeEvent.from_dict(data)

    def test_from_dict_rejects_bad_shapes(self):
        data = self._event().to_dict()
        bad = dict(data, seq="seven")
        with self.assertRaises(TypeError):
            ForgeEvent.from_dict(bad)
        bad = dict(data, payload=["not", "a", "dict"])
        with self.assertRaises(TypeError):
            ForgeEvent.from_dict(bad)
        with self.assertRaises(TypeError):
            ForgeEvent.from_dict("not a dict")


class TestEventLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-events-")
        self.project_dir = os.path.join(self.tmp, "myproject")
        self.log = EventLog(self.project_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_mythis_dir_and_file_on_emit(self):
        self.log.emit(EventType.PROJECT_CREATED, "Skald", {"goal": "x"})
        self.assertTrue(os.path.isfile(self.log.path))
        self.assertTrue(str(self.log.path).endswith(
            os.path.join(".mythis", "events.jsonl")))

    def test_seq_starts_at_one_and_increments(self):
        e1 = self.log.emit(EventType.PROJECT_CREATED, "Skald", {})
        e2 = self.log.emit(EventType.VISION_UPDATED, "Skald", {})
        self.assertEqual((e1.seq, e2.seq), (1, 2))
        self.assertEqual(e1.project, "myproject")
        self.assertEqual(e2.caused_by, None)

    def test_ts_is_utc_iso8601(self):
        event = self.log.emit(EventType.TASK_STARTED, "Architect", {})
        parsed = datetime.fromisoformat(event.ts)
        self.assertIsNotNone(parsed.tzinfo)

    def test_caused_by_recorded(self):
        e1 = self.log.emit(EventType.TASK_STARTED, "Architect", {})
        e2 = self.log.emit(EventType.TASK_FAILED, "Auditor", {}, caused_by=e1.seq)
        self.assertEqual(e2.caused_by, e1.seq)

    def test_accepts_type_as_string(self):
        event = self.log.emit("TEST_PASSED", "Auditor", {})
        self.assertEqual(event.type, EventType.TEST_PASSED)

    def test_rejects_unknown_type_string(self):
        with self.assertRaises(ValueError):
            self.log.emit("BOGUS", "Skald", {})

    def test_rejects_empty_actor_role(self):
        with self.assertRaises(ValueError):
            self.log.emit(EventType.TASK_STARTED, "  ", {})

    def test_non_serializable_payload_raises_typeerror_early(self):
        with self.assertRaises(TypeError) as ctx:
            self.log.emit(EventType.TASK_STARTED, "Architect",
                          {"bad": object()})
        self.assertIn("JSON-serializable", str(ctx.exception))
        # Nothing was written and no seq was consumed.
        self.assertEqual(len(self.log), 0)

    def test_non_dict_payload_raises_typeerror(self):
        with self.assertRaises(TypeError):
            self.log.emit(EventType.TASK_STARTED, "Architect", ["nope"])

    def test_query_returns_all_newest_last(self):
        for i in range(5):
            self.log.emit(EventType.TASK_STARTED, "Architect", {"i": i})
        events = self.log.query()
        self.assertEqual([e.seq for e in events], [1, 2, 3, 4, 5])

    def test_query_filters_by_type(self):
        self.log.emit(EventType.TASK_STARTED, "Architect", {})
        self.log.emit(EventType.TEST_FAILED, "Auditor", {})
        self.log.emit(EventType.TASK_STARTED, "Architect", {})
        got = self.log.query(type=EventType.TASK_STARTED)
        self.assertEqual(len(got), 2)
        self.assertTrue(all(e.type is EventType.TASK_STARTED for e in got))
        # String form works too.
        got = self.log.query(type="TEST_FAILED")
        self.assertEqual(len(got), 1)

    def test_query_since_seq(self):
        for _ in range(10):
            self.log.emit(EventType.TASK_STARTED, "Architect", {})
        got = self.log.query(since_seq=7)
        self.assertEqual([e.seq for e in got], [8, 9, 10])

    def test_query_limit_keeps_most_recent(self):
        for _ in range(10):
            self.log.emit(EventType.TASK_STARTED, "Architect", {})
        got = self.log.query(limit=3)
        self.assertEqual([e.seq for e in got], [8, 9, 10])

    def test_query_combined_filters(self):
        for i in range(6):
            kind = EventType.TASK_STARTED if i % 2 == 0 else EventType.TEST_PASSED
            self.log.emit(kind, "Architect", {"i": i})
        got = self.log.query(type=EventType.TEST_PASSED, since_seq=2, limit=1)
        self.assertEqual([e.seq for e in got], [6])

    def test_thousand_event_round_trip(self):
        for i in range(1000):
            self.log.emit(EventType.TASK_STARTED, "Architect", {"i": i})
        events = self.log.query()
        self.assertEqual(len(events), 1000)
        self.assertEqual([e.seq for e in events], list(range(1, 1001)))
        payloads = [e.payload["i"] for e in events]
        self.assertEqual(payloads, list(range(1000)))

    def test_seq_monotonic_after_reopen(self):
        for _ in range(25):
            self.log.emit(EventType.TASK_STARTED, "Architect", {})
        reopened = EventLog(self.project_dir)
        e = reopened.emit(EventType.TASK_COMPLETED, "Auditor", {})
        self.assertEqual(e.seq, 26)
        e2 = reopened.emit(EventType.PROJECT_COMPLETED, "Skald", {})
        self.assertEqual(e2.seq, 27)
        self.assertEqual(len(reopened), 27)

    def test_jsonl_lines_parse_independently(self):
        self.log.emit(EventType.TASK_STARTED, "Architect", {"a": 1})
        with open(self.log.path, "r", encoding="utf-8") as handle:
            lines = [ln for ln in handle if ln.strip()]
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["seq"], 1)
        self.assertEqual(parsed["type"], "TASK_STARTED")

    def test_corrupt_lines_skipped_and_counted(self):
        self.log.emit(EventType.TASK_STARTED, "Architect", {})
        self.log.emit(EventType.TASK_STARTED, "Architect", {})
        with open(self.log.path, "a", encoding="utf-8") as handle:
            handle.write("this is not json\n")
            handle.write('{"seq": 99, "type": "BOGUS"}\n')  # bad shape/type
            handle.write("\n")
        events = self.log.query()
        self.assertEqual([e.seq for e in events], [1, 2])
        self.assertEqual(self.log.skipped_corrupt, 2)

    def test_corrupt_lines_counted_on_open_scan(self):
        self.log.emit(EventType.TASK_STARTED, "Architect", {})
        with open(self.log.path, "a", encoding="utf-8") as handle:
            handle.write("garbage\n")
        reopened = EventLog(self.project_dir)
        self.assertEqual(reopened.skipped_corrupt, 1)
        # Highest valid seq was 1, so numbering continues at 2.
        self.assertEqual(reopened.emit(
            EventType.TASK_STARTED, "Architect", {}).seq, 2)

    def test_len_counts_valid_events(self):
        self.assertEqual(len(self.log), 0)
        self.log.emit(EventType.TASK_STARTED, "Architect", {})
        self.log.emit(EventType.TASK_STARTED, "Architect", {})
        self.assertEqual(len(self.log), 2)


if __name__ == "__main__":
    unittest.main()
