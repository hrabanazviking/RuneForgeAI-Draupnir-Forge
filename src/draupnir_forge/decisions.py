"""decisions.py — Decision ledger (slice 35).

The ledger is the Forge's memory of its own mind: every decision is
written to ``.mythis/DECISIONS.md`` with a UTC timestamp, its reason,
the alternatives weighed, the evidence, the consequences, the role that
decided, and a "revisit if" line drawn from the consequences so future
runs know exactly when the decision may be re-litigated.

Two searches keep the ledger honest:

* ``find(keyword)`` — parses the entries back into dicts and returns
  the ones matching a keyword (case-insensitive substring across the
  decision, reason, and consequences).
* ``check_contradiction(new_decision)`` — warns when the new decision
  shares rare keywords with a prior decision but takes a different
  stance, detected by negation markers (``not``/``instead``/
  ``reverses`` and kin) appearing on only one side.

Entry format (machine-parseable, and compatible with the Scribe role's
DECISIONS.md entries)::

    ## <utc iso timestamp> — <decision>
    - Role: <role>
    - Reason: <reason>
    - Alternatives considered: <alt>; <alt>
    - Evidence: <evidence>
    - Consequences: <consequences>
    - Revisit if: <consequences>
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

_LOG = logging.getLogger(__name__)

MYTHIS_DIR = ".mythis"
DECISIONS_FILE = "DECISIONS.md"
_HEADER = "# Decisions\n\n"

#: Heading line: "## <date> — <decision>".
_HEADER_RE = re.compile(r"^##\s+(?P<date>.+?)\s+—\s+(?P<decision>.+?)\s*$")
#: Bullet field line: "- Field: value".
_FIELD_RE = re.compile(r"^-\s+(?P<field>[A-Za-z ]+):\s*(?P<value>.*)$")

#: Words too common to ever count as keywords.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "will",
    "have", "has", "are", "was", "were", "been", "being", "into",
    "over", "under", "between", "through", "during", "about",
    "which", "what", "when", "where", "how", "why", "should",
    "could", "would", "shall", "than", "then", "them", "they",
    "their", "there", "these", "those", "such", "also", "only",
    "more", "most", "some", "any", "all", "each", "other", "using",
    "used", "use", "make", "made", "decide", "decided", "decision",
})

#: Markers of a "different stance": negation / reversal language.
_NEGATION_MARKERS = frozenset({
    "not", "no", "never", "none", "instead", "reverses", "reverse",
    "reversed", "against", "avoid", "drop", "dropped", "remove",
    "removed", "reject", "rejected", "replace", "replaced",
    "opposite", "contrary", "abandon", "abandoned",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _keywords(text: str) -> List[str]:
    """Lowercased keyword tokens: words of 4+ chars, stopwords removed."""
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _parse_entries(text: str) -> List[Dict[str, str]]:
    """Parse DECISIONS.md text into entry dicts. Tolerant of gaps."""
    entries: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    field_key: str = ""

    def flush() -> None:
        if current.get("decision"):
            for key in ("date", "role", "reason", "alternatives",
                        "evidence", "consequences", "revisit"):
                current.setdefault(key, "")
            entries.append(dict(current))

    for line in text.splitlines():
        heading = _HEADER_RE.match(line)
        if heading:
            flush()
            current = {
                "date": heading.group("date").strip(),
                "decision": heading.group("decision").strip(),
                "role": "",
                "reason": "",
                "alternatives": "",
                "evidence": "",
                "consequences": "",
                "revisit": "",
            }
            field_key = ""
            continue
        field = _FIELD_RE.match(line)
        if field and current:
            raw = field.group("field").strip().lower()
            value = field.group("value").strip()
            field_key = {
                "role": "role",
                "reason": "reason",
                "alternatives considered": "alternatives",
                "alternatives": "alternatives",
                "evidence": "evidence",
                "consequences": "consequences",
                "revisit if": "revisit",
            }.get(raw, "")
            if field_key:
                current[field_key] = value
            continue
        # Continuation lines fold into the current field.
        if current and field_key and line.strip():
            current[field_key] += " " + line.strip()
    flush()
    return entries


class DecisionLedger:
    """Append-only decision ledger backed by ``.mythis/DECISIONS.md``."""

    def __init__(self, project_dir: str) -> None:
        self.project_dir = Path(project_dir)
        self.path = self.project_dir / MYTHIS_DIR / DECISIONS_FILE

    # -- recording -------------------------------------------------------

    def record(
        self,
        decision: str,
        reason: str,
        alternatives: List[str] | None = None,
        evidence: str = "",
        consequences: str = "",
        role: str = "",
    ) -> Path:
        """Append a decision entry; return the ledger path."""
        alt_list = [str(a) for a in (alternatives or []) if str(a)]
        alt_text = "; ".join(alt_list) if alt_list else "none recorded"
        revisit = str(consequences) or "none specified"
        entry = (
            f"## {_utc_now()} — {decision}\n"
            f"- Role: {role}\n"
            f"- Reason: {reason}\n"
            f"- Alternatives considered: {alt_text}\n"
            f"- Evidence: {evidence}\n"
            f"- Consequences: {consequences}\n"
            f"- Revisit if: {revisit}\n"
            "\n"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists() or self.path.stat().st_size == 0:
                self.path.write_text(_HEADER, encoding="utf-8")
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(entry)
        except OSError as exc:
            _LOG.warning("decision ledger write failed: %s", exc)
        _LOG.info("Decision recorded: %s", decision)
        return self.path

    # -- reading ---------------------------------------------------------

    def entries(self) -> List[Dict[str, str]]:
        """All parsed ledger entries, oldest first."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        return _parse_entries(text)

    def find(self, keyword: str) -> List[Dict[str, str]]:
        """Return entries matching ``keyword`` (case-insensitive)."""
        needle = str(keyword).lower()
        return [
            entry for entry in self.entries()
            if needle in entry["decision"].lower()
            or needle in entry["reason"].lower()
            or needle in entry["consequences"].lower()
            or needle in entry["alternatives"].lower()
        ]

    # -- contradiction detection -----------------------------------------

    def check_contradiction(self, new_decision: str) -> List[Dict[str, str]]:
        """Warn about prior decisions the new one may contradict.

        A warning fires when a prior entry shares at least one rare
        keyword with the new decision *and* the two take different
        stances — i.e. negation markers (``not``/``instead``/
        ``reverses`` …) appear in exactly one of the two texts.
        Each warning is a dict with the conflicting ``decision``,
        ``date``, ``shared_keywords``, and the ``stance`` observed.
        """
        new_text = str(new_decision)
        new_words = set(_keywords(new_text))
        new_negated = _has_negation(new_text)
        priors = self.entries()
        # Rare = appears in at most two entries overall (the new text
        # plus at most one prior), so boilerplate never matches.
        counts: Dict[str, int] = {}
        for entry in priors:
            for word in set(_keywords(entry["decision"] + " " + entry["reason"])):
                counts[word] = counts.get(word, 0) + 1
        warnings: List[Dict[str, str]] = []
        for entry in priors:
            prior_words = set(_keywords(entry["decision"] + " " + entry["reason"]))
            shared = sorted(
                w for w in (new_words & prior_words)
                if counts.get(w, 0) <= 1
            )
            if not shared:
                continue
            prior_negated = _has_negation(entry["decision"] + " " + entry["reason"])
            if new_negated == prior_negated:
                continue
            stance = ("new decision negates prior"
                      if new_negated else "prior decision negates new")
            warnings.append({
                "decision": entry["decision"],
                "date": entry["date"],
                "shared_keywords": ", ".join(shared),
                "stance": stance,
                "reason": (
                    f"shares rare keywords [{', '.join(shared)}] with "
                    f"'{entry['decision']}' ({entry['date']}) but takes a "
                    f"different stance: {stance}"
                ),
            })
        return warnings


def _has_negation(text: str) -> bool:
    """True if the text carries a negation / reversal marker."""
    words = set(re.findall(r"[a-zA-Z]+", text.lower()))
    return bool(words & _NEGATION_MARKERS)
