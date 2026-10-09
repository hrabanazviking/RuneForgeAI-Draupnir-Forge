"""memory.py — Slice 26: the project's long memory, the .mythis/ archive.

:class:`ProjectMemory` tends the eleven canonical documents (the
Scribe's flock), plus the ``evidence/``, ``sessions/`` and ``logs/``
halls where the other roles leave their workings. It can seed a fresh
skeleton — never overwriting what already exists — read any document,
write documents with the Scribe's quill-guard (canonical docs demand
``by_scribe=True``; evidence and sessions are free ground), and compile
a full snapshot for the context compiler.

The skeleton's titles and one-line descriptions live in
``data/memory_skeleton.yaml`` — data, never hardcoded.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Union

from draupnir_forge import _paths

log = logging.getLogger("draupnir_forge.memory")

SKELETON_DATA_FILE = "memory_skeleton.yaml"

_MYTHIS = ".mythis"
_FREE_ZONES = ("evidence", "sessions", "logs")

# Emergency fallback if the data file cannot be found — the memory
# must never fail to form, so it carries its own seed in its pocket.
_FALLBACK_DOCS: List[Dict[str, str]] = [
    {"name": name, "title": name.replace(".md", "").replace("_", " ").title(),
     "description": "Canonical forge document."}
    for name in ("SYSTEM_VISION.md", "ARCHITECTURE.md", "DOMAIN_MAP.md",
                 "INTERFACES.md", "INVARIANTS.md", "CONSTRAINTS.md",
                 "ROADMAP.md", "DECISIONS.md", "KNOWN_ISSUES.md",
                 "CAPABILITY_LEDGER.md")
] + [{"name": "PROJECT_STATE.json", "title": "Project State",
      "description": "The machine-readable heart of the project."}]


class ProjectMemory:
    """Keeper of the project's canonical documents and working halls.

    Args:
        project_dir: Root of the forge project holding ``.mythis/``.
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self.project_dir = Path(project_dir)
        self._mythis = self.project_dir / _MYTHIS
        self._docs: List[Dict[str, str]] = self._load_skeleton_data()

    # -- skeleton --------------------------------------------------------

    @staticmethod
    def _load_skeleton_data() -> List[Dict[str, str]]:
        """Load the canonical doc list from the data file (with fallback)."""
        try:
            data = _paths.load_data_yaml(SKELETON_DATA_FILE)
            docs = data.get("documents", [])
            if isinstance(docs, list) and all(
                    isinstance(d, dict) and "name" in d for d in docs):
                return [{"name": str(d["name"]),
                         "title": str(d.get("title", d["name"])),
                         "description": str(d.get("description", ""))}
                        for d in docs]
            log.warning("Skeleton data file malformed; using fallback docs")
        except ValueError as exc:
            log.warning("Skeleton data file unavailable (%s); using fallback",
                        exc)
        return [dict(doc) for doc in _FALLBACK_DOCS]

    def canonical_docs(self) -> List[str]:
        """Names of the eleven canonical documents, in order."""
        return [doc["name"] for doc in self._docs]

    def ensure_skeleton(self) -> List[str]:
        """Create ``.mythis/`` with all canonical docs and working halls.

        Existing files are never overwritten — the Scribe's older words
        are sacred. Idempotent: safe to call on every forge start.

        Returns:
            Names of the documents created by this call.
        """
        self._mythis.mkdir(parents=True, exist_ok=True)
        for zone in _FREE_ZONES:
            (self._mythis / zone).mkdir(parents=True, exist_ok=True)
        created: List[str] = []
        for doc in self._docs:
            path = self._mythis / doc["name"]
            if path.exists():
                continue
            try:
                path.write_text(self._skeleton_content(doc), encoding="utf-8")
                created.append(doc["name"])
            except OSError as exc:
                log.warning("Could not seed %s: %s", path, exc)
        return created

    @staticmethod
    def _skeleton_content(doc: Dict[str, str]) -> str:
        """Seed content for a missing canonical document."""
        if doc["name"].endswith(".json"):
            # PROJECT_STATE.json must stay machine-readable: no markdown.
            state = {
                "phase": "intake",
                "goal": "",
                "task_id": None,
                "updated_ts": datetime.now(timezone.utc).isoformat(),
                "counters": {},
            }
            return json.dumps(state, indent=2) + "\n"
        return f"# {doc['title']}\n\n{doc['description']}\n"

    # -- path safety ------------------------------------------------------

    def _resolve(self, name: str) -> Path:
        """Resolve ``name`` under ``.mythis/``; reject escape attempts."""
        candidate = (self._mythis / name).resolve()
        try:
            candidate.relative_to(self._mythis.resolve())
        except ValueError:
            raise ValueError(
                f"Refusing to leave .mythis/: {name!r}") from None
        return candidate

    @staticmethod
    def _is_canonical(name: str, canonical: List[str]) -> bool:
        return Path(name).name in canonical

    # -- reading / writing -------------------------------------------------

    def read_doc(self, name: str) -> str:
        """Read a document (canonical, evidence, or session).

        Missing or unreadable documents return ``""`` — the memory
        answers with silence rather than crashing.
        """
        try:
            path = self._resolve(name)
        except ValueError as exc:
            log.warning("read_doc refused: %s", exc)
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            log.warning("Could not read %s: %s", path, exc)
            return ""

    def write_doc(self, name: str, content: str,
                  by_scribe: bool = False) -> Path:
        """Write a document into ``.mythis/``.

        The Scribe owns the canonical documents: writing one without
        ``by_scribe=True`` raises :class:`PermissionError`. The
        ``evidence/``, ``sessions/`` and ``logs/`` halls are free ground
        for every role.

        Raises:
            PermissionError: On a non-Scribe write to a canonical doc or
                to any other reserved ``.mythis/`` file.
            ValueError: If ``name`` tries to escape ``.mythis/``.
        """
        path = self._resolve(name)
        rel = path.relative_to(self._mythis.resolve())
        canonical = self.canonical_docs()
        if self._is_canonical(name, canonical):
            zone = "canonical"
        elif rel.parts and rel.parts[0] in _FREE_ZONES:
            zone = "free"
        else:
            zone = "reserved"
        if zone != "free" and not by_scribe:
            raise PermissionError(
                f"Only the Scribe may write {rel} (pass by_scribe=True)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    # -- context compilation ------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Compile the whole memory for the context compiler.

        Returns:
            ``{"docs": {canonical name: content},
            "evidence": {relpath: content}, "sessions": {relpath: content}}``.
            Unreadable files are skipped with a warning, never a crash.
        """
        docs = {name: self.read_doc(name) for name in self.canonical_docs()}
        collected: Dict[str, Dict[str, str]] = {"evidence": {}, "sessions": {}}
        for zone in ("evidence", "sessions"):
            zone_dir = self._mythis / zone
            if not zone_dir.is_dir():
                continue
            for path in sorted(zone_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(self._mythis))
                try:
                    collected[zone][rel] = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    log.warning("Skipping unreadable %s: %s", rel, exc)
        return {"docs": docs, **collected}
