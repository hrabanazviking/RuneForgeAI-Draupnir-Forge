"""security.py — Security model: authority levels, policy, audit (slice 39).

The Forge's shield-wall: every tool action is judged against an
:func:`Authority` level before it touches the world. The model has three
rings, like the layers of config:

    1. **Authority levels** — READ < WRITE < EXEC < INSTALL < NETWORK <
       DESTRUCTIVE, ordered by power. The enum values carry the order, so
       ``Authority.READ < Authority.DESTRUCTIVE`` holds by construction.
    2. **Config gates** — each level maps to a ``tools.allow_*`` config
       flag (mirroring the map in :mod:`draupnir_forge.tools`). READ and
       WRITE are the forge's bread and water and need no flag;
       INSTALL/NETWORK/DESTRUCTIVE are denied by default and open only
       when the config explicitly allows them.
    3. **Role grants** — ``data/role_authority.yaml`` lists the extra
       authorities each role may wield beyond the universal READ+WRITE
       baseline. A role grant never overrides a config denial: an action
       passes only when *both* the config and the role permit it.

Every decision is audit-logged (unless ``tools.audit`` is false) to
``<project>/.mythis/security_audit.jsonl`` — one JSON object per line,
recording who asked, what they asked for, whether it was granted, and
why. The audit trail never sinks the forge: write failures are logged
as warnings, never raised.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Tuple, Union

from . import _paths
from .tools import AuthorityDenied

log = logging.getLogger("draupnir_forge.security")

# Name of the data file holding role -> authority grants.
ROLE_AUTHORITY_FILE = "role_authority.yaml"

# Relative path (under the project dir) of the audit log.
AUDIT_LOG_RELATIVE = os.path.join(".mythis", "security_audit.jsonl")


class Authority(IntEnum):
    """Authority levels, ordered by power (spec §31).

    The integer values encode the ordering: higher means more powerful,
    so ``Authority.READ < Authority.EXEC < Authority.DESTRUCTIVE``.
    """

    READ = 1
    WRITE = 2
    EXEC = 3
    INSTALL = 4
    NETWORK = 5
    DESTRUCTIVE = 6


# Authority -> config flag that gates it (None = always allowed).
_AUTHORITY_TO_FLAG: Dict[Authority, Optional[str]] = {
    Authority.READ: None,
    Authority.WRITE: None,
    Authority.EXEC: "tools.allow_exec",
    Authority.INSTALL: "tools.allow_install",
    Authority.NETWORK: "tools.allow_network",
    Authority.DESTRUCTIVE: "tools.allow_destructive",
}

# Every role holds these without them being listed in the data file.
_BASELINE_AUTHORITIES: FrozenSet[Authority] = frozenset(
    {Authority.READ, Authority.WRITE}
)


def _coerce_authority(action: Union[Authority, str]) -> Authority:
    """Normalize *action* to an :class:`Authority`.

    Accepts an ``Authority`` member or its name (case-insensitive,
    e.g. ``"destructive"``). Unknown names raise ``ValueError`` — the
    authority set is closed, like the gates of Valhalla.
    """
    if isinstance(action, Authority):
        return action
    if isinstance(action, str):
        try:
            return Authority[action.strip().upper()]
        except KeyError:
            pass
    raise ValueError(
        f"Unknown authority {action!r}; expected one of "
        f"{[a.name for a in Authority]}."
    )


def _load_role_grants() -> Dict[str, FrozenSet[Authority]]:
    """Load role -> extra-authority grants from the data file.

    Returns a mapping of lowercase role name to the additional
    authorities beyond the universal baseline. Never raises: a missing
    or malformed file yields an empty map (baseline only for every
    role), because the forge must not fall over its own shield.
    """
    grants: Dict[str, FrozenSet[Authority]] = {}
    try:
        data = _paths.load_data_yaml(ROLE_AUTHORITY_FILE)
    except Exception as exc:
        log.warning(
            "Could not load %s; all roles fall back to baseline: %s",
            ROLE_AUTHORITY_FILE,
            exc,
        )
        return grants
    if not isinstance(data, dict):
        log.warning(
            "Ignoring %s: top level must be a mapping.", ROLE_AUTHORITY_FILE
        )
        return grants
    roles = data.get("roles", {})
    if not isinstance(roles, dict):
        log.warning(
            "Ignoring %s: 'roles' must be a mapping.", ROLE_AUTHORITY_FILE
        )
        return grants
    for role, authorities in roles.items():
        role_name = str(role).strip().lower()
        if not role_name:
            continue
        extra = set()
        items = authorities if isinstance(authorities, (list, tuple)) else []
        for item in items:
            try:
                extra.add(_coerce_authority(str(item)))
            except ValueError:
                log.warning(
                    "Ignoring unknown authority %r for role %r in %s.",
                    item,
                    role,
                    ROLE_AUTHORITY_FILE,
                )
        grants[role_name] = frozenset(extra)
    return grants


class SecurityPolicy:
    """Judges tool actions against config gates and role grants.

    Args:
        config: A :class:`ForgeConfig` (or any object with a
            ``get(dotted_key, default)`` method) carrying the
            ``tools.allow_*`` and ``tools.audit`` flags.
        project_dir: Project root; the audit log lands in
            ``<project>/.mythis/security_audit.jsonl``. ``None`` uses the
            current working directory.
    """

    def __init__(
        self,
        config: Any,
        project_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.config = config
        if project_dir is None:
            self.project_dir = Path(os.getcwd())
        else:
            self.project_dir = Path(project_dir).resolve()
        # Heimdallr reads the roster of who may carry which weapon.
        self._role_grants = _load_role_grants()

    # -- decisions -----------------------------------------------------
    def _config_allows(self, action: Authority) -> Tuple[bool, str]:
        """Check the config flag gate for *action*.

        Returns (allowed, reason). Unreadable config degrades to deny —
        a gate that cannot be read stays shut.
        """
        flag = _AUTHORITY_TO_FLAG[action]
        if flag is None:
            return True, (
                f"{action.name} needs no config flag "
                "(read/write are the forge's bread and water)."
            )
        allowed = False
        try:
            allowed = bool(self.config.get(flag, False))
        except Exception as exc:
            log.warning(
                "Could not read config %r; denying %s: %s",
                flag,
                action.name,
                exc,
            )
            return False, (
                f"config {flag!r} unreadable; denying {action.name}."
            )
        if allowed:
            return True, f"config {flag!r} is true."
        return False, (
            f"config {flag!r} is false; {action.name} denied by default."
        )

    def _role_allows(
        self, action: Authority, role: Optional[str]
    ) -> Tuple[bool, str]:
        """Check the role-grant gate for *action*.

        Returns (allowed, reason). No role in context means the role
        gate passes silently (config alone decides). Unknown roles fall
        back to the universal READ+WRITE baseline with a warning.
        """
        if role is None:
            return True, "no role in context; config alone decides."
        role_name = str(role).strip().lower()
        if role_name not in self._role_grants:
            log.warning(
                "Unknown role %r; applying baseline authorities only.", role
            )
            grants = self._role_grants.get(role_name, frozenset())
            effective = _BASELINE_AUTHORITIES | grants
        else:
            effective = _BASELINE_AUTHORITIES | self._role_grants[role_name]
        if action in effective:
            return True, f"role {role_name!r} is granted {action.name}."
        return False, (
            f"role {role_name!r} is not granted {action.name} "
            f"(holds: {sorted(a.name for a in effective)})."
        )

    def _decide(
        self, action: Union[Authority, str], context: Optional[Dict[str, Any]]
    ) -> Tuple[bool, str, str]:
        """Decide an authorization request.

        Returns (granted, actor, reason). Both the config gate and the
        role gate must pass.
        """
        authority = _coerce_authority(action)
        ctx = dict(context) if isinstance(context, dict) else {}
        actor = str(ctx.get("actor", "unknown"))
        role = ctx.get("role")

        config_ok, config_reason = self._config_allows(authority)
        role_ok, role_reason = self._role_allows(authority, role)
        granted = config_ok and role_ok
        if granted:
            reason = f"GRANTED {authority.name}: {config_reason} {role_reason}"
        else:
            failed = "; ".join(
                part
                for part, ok in (
                    (config_reason, config_ok),
                    (role_reason, role_ok),
                )
                if not ok
            )
            reason = f"DENIED {authority.name}: {failed}"
        return granted, actor, reason

    def authorize(
        self, action: Union[Authority, str], context: Dict[str, Any]
    ) -> bool:
        """Return True when *action* is authorized for this *context*.

        Args:
            action: An :class:`Authority` member or its name.
            context: May carry ``"role"`` (role name, checked against
                ``data/role_authority.yaml``) and ``"actor"`` (who is
                asking; used in the audit log).

        The decision is audit-logged unless ``tools.audit`` is false.
        """
        granted, actor, reason = self._decide(action, context)
        authority = _coerce_authority(action)
        self._audit(authority, granted, actor, reason)
        return granted

    def check_or_raise(
        self,
        action: Union[Authority, str],
        actor: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Authorize *action*, raising :class:`AuthorityDenied` on denial.

        Args:
            action: An :class:`Authority` member or its name.
            actor: Who is asking (recorded in the audit log).
            context: Optional extra context (e.g. ``{"role": "tester"}``);
                ``actor`` is added to it when absent.

        Raises:
            AuthorityDenied: When the policy denies the action.
        """
        ctx = dict(context) if isinstance(context, dict) else {}
        ctx.setdefault("actor", actor)
        granted, _, reason = self._decide(action, ctx)
        authority = _coerce_authority(action)
        self._audit(authority, granted, actor, reason)
        if not granted:
            raise AuthorityDenied(
                f"Security policy denied {authority.name} for actor "
                f"{actor!r}: {reason}"
            )

    # -- audit ---------------------------------------------------------
    def _audit_enabled(self) -> bool:
        """True unless the config explicitly disables auditing."""
        try:
            return bool(self.config.get("tools.audit", True))
        except Exception as exc:
            log.warning("Could not read tools.audit; auditing anyway: %s", exc)
            return True

    def _audit(
        self, action: Authority, granted: bool, actor: str, reason: str
    ) -> None:
        """Append one decision record to the audit log, if enabled."""
        if not self._audit_enabled():
            return
        self.audit(action, granted, actor, reason)

    def audit(
        self,
        action: Union[Authority, str],
        granted: bool,
        actor: str,
        reason: str = "",
    ) -> None:
        """Append one record to ``.mythis/security_audit.jsonl``.

        Each line is a JSON object with ``ts`` (UTC ISO-8601), ``action``,
        ``actor``, ``granted``, and ``reason``. Never raises: a forge
        that cannot write its saga must still be able to fight.
        """
        authority = _coerce_authority(action)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": authority.name,
            "actor": str(actor),
            "granted": bool(granted),
            "reason": str(reason),
        }
        try:
            log_path = self.project_dir / AUDIT_LOG_RELATIVE
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except Exception as exc:
            log.warning("Could not write security audit log: %s", exc)


__all__ = [
    "Authority",
    "SecurityPolicy",
    "ROLE_AUTHORITY_FILE",
    "AUDIT_LOG_RELATIVE",
]
