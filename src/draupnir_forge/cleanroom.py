"""cleanroom.py — Clean-room research mode (slice 40).

A völva may study the songs of other tribes, but she must sing her own:
the clean room lets the Forge *learn from* external sources without ever
*copying* them. The discipline has four rites:

    1. **study()** — record a source's provenance (id, URL, license,
       date) and the researcher's own *notes* to
       ``<project>/.mythis/provenance.md``. The source's full text is
       NEVER stored — only what the researcher wrote down in their own
       words. A machine-readable sidecar
       (``.mythis/cleanroom_sources.json``) keeps the notes across
       sessions.
    2. **derive()** — produce requirements, compatibility notes, and an
       architecture sketch as *templates* from the studied sources.
       Every line is stamped ``"derived, not copied"`` and cites the
       sources that informed it.
    3. **check_overlap()** — slide 25-character windows over a candidate
       text and compare against the studied notes. Any verbatim match
       raises the alarm: that is copying, not deriving, and the
       candidate must be rewritten.
    4. **report()** — a provenance summary in markdown: what was
       studied, under which licenses, and the guarantees above.

Like the rest of the Forge, nothing here may sink the ship: every file
operation is guarded, and failures are logged as warnings.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Union

log = logging.getLogger("draupnir_forge.cleanroom")

# Canonical verbatim-copy detection window (spec §40).
OVERLAP_WINDOW = 25

# Stamp marking every derived line: learned from, never lifted.
DERIVED_STAMP = "derived, not copied"

# Relative paths under the project dir.
PROVENANCE_RELATIVE = "provenance.md"
REGISTRY_RELATIVE = "cleanroom_sources.json"
_MYTHIS_DIR = ".mythis"


def _windows(text: str, size: int = OVERLAP_WINDOW) -> List[str]:
    """Return every *size*-character window of *text* (lowercased)."""
    lowered = text.lower()
    if len(lowered) < size:
        return []
    return [lowered[i : i + size] for i in range(len(lowered) - size + 1)]


def _quote_block(text: str) -> str:
    """Render *text* as a markdown blockquote, line by line."""
    lines = str(text).strip().splitlines() or [""]
    return "\n".join("> " + line for line in lines)


class CleanRoom:
    """Clean-room research discipline for one project.

    Args:
        project_dir: Project root; provenance and registry live under
            ``<project>/.mythis/``.
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._mythis = self.project_dir / _MYTHIS_DIR
        self._provenance_path = self._mythis / PROVENANCE_RELATIVE
        self._registry_path = self._mythis / REGISTRY_RELATIVE
        # source_id -> {"source_id", "url", "license", "date", "notes"}.
        # Only notes are kept here; full source text is never stored.
        self._sources: Dict[str, Dict[str, str]] = {}
        # Lowercased 25-char windows over all notes, for overlap checks.
        self._note_windows: set = set()
        self._load_registry()

    # -- persistence ---------------------------------------------------
    def _load_registry(self) -> None:
        """Rebuild the source registry (and window index) from disk.

        Never raises: a missing or corrupt registry simply starts the
        clean room empty.
        """
        try:
            if not self._registry_path.is_file():
                return
            with open(self._registry_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception as exc:
            log.warning(
                "Ignoring unreadable clean-room registry %s: %s",
                self._registry_path,
                exc,
            )
            return
        entries = data.get("sources", []) if isinstance(data, dict) else []
        if not isinstance(entries, list):
            log.warning(
                "Ignoring malformed clean-room registry %s.", self._registry_path
            )
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            source_id = str(entry.get("source_id", "")).strip()
            if not source_id:
                continue
            clean = {
                "source_id": source_id,
                "url": str(entry.get("url", "")),
                "license": str(entry.get("license", "")),
                "date": str(entry.get("date", "")),
                "notes": str(entry.get("notes", "")),
            }
            self._sources[source_id] = clean
            self._note_windows.update(_windows(clean["notes"]))

    def _save_registry(self) -> None:
        """Persist the registry sidecar. Never raises."""
        try:
            self._mythis.mkdir(parents=True, exist_ok=True)
            payload = {"sources": list(self._sources.values())}
            with open(self._registry_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
        except Exception as exc:
            log.warning(
                "Could not write clean-room registry %s: %s",
                self._registry_path,
                exc,
            )

    def _append_provenance(self, entry: Dict[str, str]) -> None:
        """Append one human-readable provenance entry. Never raises."""
        try:
            self._mythis.mkdir(parents=True, exist_ok=True)
            new_file = not self._provenance_path.is_file()
            with open(self._provenance_path, "a", encoding="utf-8") as handle:
                if new_file:
                    handle.write(
                        "# Clean-Room Provenance\n\n"
                        "Every external source studied in the clean room, "
                        "in the order it was studied. Only the researcher's "
                        "own notes are kept here — the sources' full text "
                        "is never stored. All derived work is marked "
                        f'"{DERIVED_STAMP}".\n'
                    )
                handle.write(f"\n## {entry['source_id']}\n\n")
                handle.write(f"- **URL:** {entry['url']}\n")
                handle.write(f"- **License:** {entry['license']}\n")
                handle.write(f"- **Date studied:** {entry['date']}\n")
                handle.write("- **Notes:**\n")
                handle.write(_quote_block(entry["notes"]) + "\n")
        except Exception as exc:
            log.warning(
                "Could not append to provenance file %s: %s",
                self._provenance_path,
                exc,
            )

    # -- rites ---------------------------------------------------------
    def study(
        self,
        source_id: str,
        url: str,
        license: str,
        notes: str,
    ) -> Dict[str, str]:
        """Study one external source and record its provenance.

        Args:
            source_id: Short stable identifier, e.g. ``"oauth-rfc"``.
            url: Where the source was found.
            license: The source's license, e.g. ``"MIT"``.
            notes: The researcher's own notes **in their own words**.
                This is what the overlap check scans; never paste the
                source's text here.

        Returns:
            The recorded entry (source_id, url, license, date, notes).

        Raises:
            ValueError: When *source_id* or *notes* is empty.
        """
        source_id = str(source_id).strip()
        notes = str(notes).strip()
        if not source_id:
            raise ValueError("study() requires a non-empty source_id.")
        if not notes:
            raise ValueError(
                "study() requires non-empty notes written in your own words; "
                "an empty study teaches the clean room nothing."
            )
        entry = {
            "source_id": source_id,
            "url": str(url).strip(),
            "license": str(license).strip() or "unknown",
            "date": datetime.now(timezone.utc).date().isoformat(),
            "notes": notes,
        }
        # Re-studying refreshes the entry (additive: history stays in the
        # provenance file, the registry keeps the latest notes).
        self._sources[source_id] = entry
        self._note_windows.update(_windows(notes))
        self._append_provenance(entry)
        self._save_registry()
        return dict(entry)

    def derive(self, requirements: List[str]) -> Dict[str, List[str]]:
        """Derive template docs from the studied sources.

        Produces fresh scaffolding — requirements, license-compatibility
        notes, and an architecture sketch — informed by what was studied
        but written anew. Every line carries the ``"derived, not
        copied"`` stamp and cites its informing sources.

        Args:
            requirements: The capability statements to scaffold around.

        Returns:
            ``{"requirements": [...], "compat_notes": [...],
            "architecture_sketch": [...]}`` — lists of template lines.

        Raises:
            ValueError: When *requirements* is empty.
        """
        clean: List[str] = [
            str(req).strip() for req in requirements if str(req).strip()
        ]
        if not clean:
            raise ValueError(
                "derive() requires at least one non-empty requirement."
            )
        studied = sorted(self._sources)
        informed_by = ", ".join(studied) if studied else "no sources studied yet"

        derived_requirements = [
            f"REQ-{index + 1:03d}: {req} "
            f"[{DERIVED_STAMP}; informed by: {informed_by}]"
            for index, req in enumerate(clean)
        ]

        compat_notes = self._compat_notes(studied)

        sketch_sections = [
            "Components — name each part and its responsibility; "
            "fill in from the requirements above",
            "Data flow — how information travels between components; "
            "draw the rivers before building the bridges",
            "Interfaces — the public surface each component offers; "
            "contracts, not implementations",
            "Error handling — what happens when a thread snaps; "
            "every failure named, every failure answered",
            "Security boundaries — which authority each component needs; "
            "see the security model, least power first",
        ]
        architecture_sketch = [
            f"{section} [{DERIVED_STAMP}; informed by: {informed_by}]"
            for section in sketch_sections
        ]

        return {
            "requirements": derived_requirements,
            "compat_notes": compat_notes,
            "architecture_sketch": architecture_sketch,
        }

    def _compat_notes(self, studied: List[str]) -> List[str]:
        """Build license-compatibility template notes for studied sources."""
        by_license: Dict[str, List[str]] = {}
        for source_id in studied:
            lic = self._sources[source_id]["license"]
            by_license.setdefault(lic, []).append(source_id)

        notes: List[str] = []
        for lic in sorted(by_license):
            ids = ", ".join(by_license[lic])
            lowered = lic.lower()
            if any(tag in lowered for tag in ("gpl", "agpl", "copyleft")):
                guidance = (
                    f"Copyleft license ({lic}) on: {ids} — treat as "
                    "read-only inspiration. Derived expression must be "
                    "written anew; do not adapt or relicense their text. "
                    "Keep the clean-room boundary intact."
                )
            elif any(
                tag in lowered
                for tag in ("mit", "apache", "bsd", "isc", "public domain", "cc0")
            ):
                guidance = (
                    f"Permissive license ({lic}) on: {ids} — ideas and "
                    "patterns may inform the design freely, but the "
                    "implementation must still be written anew, not copied."
                )
            else:
                guidance = (
                    f"License {lic!r} on: {ids} — terms unclear or "
                    "unknown; treat conservatively as read-only "
                    "inspiration until the license is confirmed."
                )
            notes.append(f"{guidance} [{DERIVED_STAMP}]")
        if not notes:
            notes.append(
                "No sources studied yet — study sources before deriving, "
                f"so compatibility can be judged. [{DERIVED_STAMP}]"
            )
        return notes

    def check_overlap(self, candidate_text: str) -> List[str]:
        """Scan *candidate_text* for verbatim copying from studied notes.

        Slides 25-character windows over the candidate and reports every
        window that also appears in a studied source's notes — the
        canonical verbatim-copy alarm. Comparison is case-insensitive,
        but the returned snippets keep the candidate's own casing.

        Args:
            candidate_text: Draft text to vet before it enters the forge.

        Returns:
            The candidate's own matching 25-character snippets, in order
            of first appearance, deduplicated. Empty when clean.
        """
        candidate = str(candidate_text or "")
        if len(candidate) < OVERLAP_WINDOW or not self._note_windows:
            return []
        lowered = candidate.lower()
        hits: List[str] = []
        seen: set = set()
        for index in range(len(lowered) - OVERLAP_WINDOW + 1):
            window = lowered[index : index + OVERLAP_WINDOW]
            if window in self._note_windows and window not in seen:
                seen.add(window)
                hits.append(candidate[index : index + OVERLAP_WINDOW])
        return hits

    def report(self) -> str:
        """Return a markdown summary of the clean room's provenance."""
        lines = [
            "# Clean-Room Provenance Report",
            "",
            f"_Generated {datetime.now(timezone.utc).date().isoformat()} "
            "by the Draupnir Forge clean room._",
            "",
            "## Policy",
            "",
            "- External sources are studied for understanding, never copied.",
            f'- Every derived line is marked "{DERIVED_STAMP}".',
            "- Only the researcher's own notes are stored; "
            "the sources' full text is never kept.",
            f"- Verbatim copying is policed by the {OVERLAP_WINDOW}-character "
            "overlap check.",
            "",
            f"## Sources studied ({len(self._sources)})",
            "",
        ]
        if self._sources:
            lines.append("| source_id | url | license | date | notes (chars) |")
            lines.append("| --- | --- | --- | --- | ---: |")
            for source_id in sorted(self._sources):
                entry = self._sources[source_id]
                lines.append(
                    f"| {entry['source_id']} | {entry['url']} | "
                    f"{entry['license']} | {entry['date']} | "
                    f"{len(entry['notes'])} |"
                )
        else:
            lines.append("_No sources studied yet._")
        lines.append("")
        lines.append("## License summary")
        lines.append("")
        tally: Dict[str, int] = {}
        for entry in self._sources.values():
            tally[entry["license"]] = tally.get(entry["license"], 0) + 1
        if tally:
            for lic in sorted(tally):
                lines.append(f"- {lic}: {tally[lic]} source(s)")
        else:
            lines.append("- none")
        lines.append("")
        return "\n".join(lines)

    @property
    def sources(self) -> Dict[str, Dict[str, str]]:
        """A copy of the studied-source registry (notes only, never text)."""
        return {key: dict(value) for key, value in self._sources.items()}


__all__ = [
    "CleanRoom",
    "OVERLAP_WINDOW",
    "DERIVED_STAMP",
    "PROVENANCE_RELATIVE",
    "REGISTRY_RELATIVE",
]
