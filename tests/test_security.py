"""Tests for the security model (slice 39)."""

import json
import os
import shutil
import tempfile
import unittest

from draupnir_forge.config import ForgeConfig
from draupnir_forge.security import Authority, SecurityPolicy
from draupnir_forge.tools import AuthorityDenied


def _config(**extra):
    """Hermetic config: temp HOME + no DRAUPNIR_* env leakage."""
    overrides = {
        "tools.allow_exec": True,
        "tools.allow_install": False,
        "tools.allow_network": False,
        "tools.allow_destructive": False,
        "tools.audit": True,
        "tools.default_timeout_s": 60,
    }
    overrides.update(extra)
    return ForgeConfig.load(overrides=overrides)


class _EnvGuard:
    """Isolate HOME and DRAUPNIR_* env vars so tests are hermetic."""

    def __init__(self):
        self._saved = dict(os.environ)
        self.home = tempfile.mkdtemp(prefix="forge-home-")

    def __enter__(self):
        os.environ["HOME"] = self.home
        for var in [v for v in os.environ if v.startswith("DRAUPNIR_")]:
            del os.environ[var]
        return self

    def __exit__(self, *exc):
        os.environ.clear()
        os.environ.update(self._saved)
        shutil.rmtree(self.home, ignore_errors=True)


class AuthorityOrderTest(unittest.TestCase):
    def test_ordered_by_power(self):
        self.assertLess(Authority.READ, Authority.WRITE)
        self.assertLess(Authority.WRITE, Authority.EXEC)
        self.assertLess(Authority.EXEC, Authority.INSTALL)
        self.assertLess(Authority.INSTALL, Authority.NETWORK)
        self.assertLess(Authority.NETWORK, Authority.DESTRUCTIVE)

    def test_all_six_levels_present(self):
        names = [a.name for a in Authority]
        self.assertEqual(
            names,
            ["READ", "WRITE", "EXEC", "INSTALL", "NETWORK", "DESTRUCTIVE"],
        )


class SecurityPolicyConfigGateTest(unittest.TestCase):
    def setUp(self):
        self.guard = _EnvGuard()
        self.guard.__enter__()
        self.root = tempfile.mkdtemp(prefix="forge-security-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.guard.__exit__, None, None, None)

    def _policy(self, **extra):
        return SecurityPolicy(_config(**extra), project_dir=self.root)

    def test_read_write_always_allowed(self):
        policy = self._policy()
        self.assertTrue(policy.authorize(Authority.READ, {"actor": "w"}))
        self.assertTrue(policy.authorize(Authority.WRITE, {"actor": "w"}))

    def test_exec_allowed_when_config_allows(self):
        policy = self._policy()
        self.assertTrue(policy.authorize(Authority.EXEC, {"actor": "w"}))

    def test_destructive_denied_by_default(self):
        policy = self._policy()
        self.assertFalse(
            policy.authorize(Authority.DESTRUCTIVE, {"actor": "w"})
        )

    def test_network_denied_by_default(self):
        policy = self._policy()
        self.assertFalse(policy.authorize(Authority.NETWORK, {"actor": "w"}))

    def test_install_denied_by_default(self):
        policy = self._policy()
        self.assertFalse(policy.authorize(Authority.INSTALL, {"actor": "w"}))

    def test_destructive_allowed_when_config_allows(self):
        policy = self._policy(**{"tools.allow_destructive": True})
        self.assertTrue(
            policy.authorize(Authority.DESTRUCTIVE, {"actor": "w"})
        )

    def test_network_allowed_when_config_allows(self):
        policy = self._policy(**{"tools.allow_network": True})
        self.assertTrue(policy.authorize(Authority.NETWORK, {"actor": "w"}))

    def test_install_allowed_when_config_allows(self):
        policy = self._policy(**{"tools.allow_install": True})
        self.assertTrue(policy.authorize(Authority.INSTALL, {"actor": "w"}))

    def test_action_accepts_string_names(self):
        policy = self._policy()
        self.assertTrue(policy.authorize("read", {"actor": "w"}))
        self.assertFalse(policy.authorize("destructive", {"actor": "w"}))

    def test_unknown_action_raises_value_error(self):
        policy = self._policy()
        with self.assertRaises(ValueError):
            policy.authorize("teleport", {"actor": "w"})


class SecurityPolicyRoleGateTest(unittest.TestCase):
    def setUp(self):
        self.guard = _EnvGuard()
        self.guard.__enter__()
        self.root = tempfile.mkdtemp(prefix="forge-security-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.guard.__exit__, None, None, None)
        self.policy = SecurityPolicy(_config(), project_dir=self.root)

    def test_tester_gets_exec(self):
        self.assertTrue(
            self.policy.authorize(
                Authority.EXEC, {"actor": "t", "role": "tester"}
            )
        )

    def test_tester_denied_network(self):
        self.assertFalse(
            self.policy.authorize(
                Authority.NETWORK, {"actor": "t", "role": "tester"}
            )
        )

    def test_worker_gets_write_and_exec(self):
        self.assertTrue(
            self.policy.authorize(
                Authority.WRITE, {"actor": "w", "role": "worker"}
            )
        )
        self.assertTrue(
            self.policy.authorize(
                Authority.EXEC, {"actor": "w", "role": "worker"}
            )
        )

    def test_worker_denied_destructive(self):
        self.assertFalse(
            self.policy.authorize(
                Authority.DESTRUCTIVE, {"actor": "w", "role": "worker"}
            )
        )

    def test_auditor_is_read_only(self):
        # Auditor has no extra grants: baseline READ/WRITE only.
        self.assertTrue(
            self.policy.authorize(
                Authority.READ, {"actor": "a", "role": "auditor"}
            )
        )
        self.assertFalse(
            self.policy.authorize(
                Authority.EXEC, {"actor": "a", "role": "auditor"}
            )
        )

    def test_role_cannot_override_config_deny(self):
        # Even with allow_destructive=true, no role grants DESTRUCTIVE
        # by default — the role gate holds the line.
        permissive = SecurityPolicy(
            _config(**{"tools.allow_destructive": True}),
            project_dir=self.root,
        )
        self.assertFalse(
            permissive.authorize(
                Authority.DESTRUCTIVE, {"actor": "w", "role": "worker"}
            )
        )

    def test_unknown_role_falls_back_to_baseline(self):
        self.assertTrue(
            self.policy.authorize(
                Authority.READ, {"actor": "x", "role": "no-such-role"}
            )
        )
        self.assertFalse(
            self.policy.authorize(
                Authority.EXEC, {"actor": "x", "role": "no-such-role"}
            )
        )

    def test_no_role_means_config_only(self):
        self.assertTrue(
            self.policy.authorize(Authority.EXEC, {"actor": "anon"})
        )


class SecurityPolicyAuditTest(unittest.TestCase):
    def setUp(self):
        self.guard = _EnvGuard()
        self.guard.__enter__()
        self.root = tempfile.mkdtemp(prefix="forge-security-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(self.guard.__exit__, None, None, None)

    def _audit_lines(self):
        path = os.path.join(self.root, ".mythis", "security_audit.jsonl")
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_authorize_writes_audit_log(self):
        policy = SecurityPolicy(_config(), project_dir=self.root)
        policy.authorize(Authority.EXEC, {"actor": "skald"})
        policy.authorize(Authority.DESTRUCTIVE, {"actor": "skald"})
        records = self._audit_lines()
        self.assertEqual(len(records), 2)
        granted, denied = records
        for record in records:
            for key in ("ts", "action", "actor", "granted", "reason"):
                self.assertIn(key, record)
        self.assertEqual(granted["action"], "EXEC")
        self.assertTrue(granted["granted"])
        self.assertEqual(granted["actor"], "skald")
        self.assertEqual(denied["action"], "DESTRUCTIVE")
        self.assertFalse(denied["granted"])
        self.assertIn("denied", denied["reason"].lower())

    def test_audit_disabled_writes_nothing(self):
        policy = SecurityPolicy(
            _config(**{"tools.audit": False}), project_dir=self.root
        )
        policy.authorize(Authority.READ, {"actor": "w"})
        path = os.path.join(self.root, ".mythis", "security_audit.jsonl")
        self.assertFalse(os.path.exists(path))

    def test_audit_never_raises(self):
        policy = SecurityPolicy(_config(), project_dir=self.root)
        # Direct audit call with an unwritable path must not raise.
        policy.project_dir = os.path.join(self.root, "nope", "x")
        policy.audit(Authority.READ, True, "w", "test")
        # And a read-only project dir degrades gracefully too.
        ro = tempfile.mkdtemp(prefix="forge-ro-")
        self.addCleanup(shutil.rmtree, ro, True)
        os.chmod(ro, 0o500)
        ro_policy = SecurityPolicy(_config(), project_dir=ro)
        ro_policy.audit(Authority.READ, True, "w", "test")
        os.chmod(ro, 0o700)

    def test_check_or_raise_passes_on_grant(self):
        policy = SecurityPolicy(_config(), project_dir=self.root)
        self.assertIsNone(policy.check_or_raise(Authority.READ, "w"))

    def test_check_or_raise_raises_authority_denied(self):
        policy = SecurityPolicy(_config(), project_dir=self.root)
        with self.assertRaises(AuthorityDenied):
            policy.check_or_raise(Authority.DESTRUCTIVE, "w")

    def test_check_or_raise_audits_denial(self):
        policy = SecurityPolicy(_config(), project_dir=self.root)
        with self.assertRaises(AuthorityDenied):
            policy.check_or_raise(
                Authority.NETWORK, "w", {"role": "tester"}
            )
        records = self._audit_lines()
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["granted"])
        self.assertEqual(records[0]["actor"], "w")


if __name__ == "__main__":
    unittest.main()
