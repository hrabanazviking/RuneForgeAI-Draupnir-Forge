"""Slice 9 tests: Role base contract and registry."""

from __future__ import annotations

import unittest

from draupnir_forge.roles.base import (
    Role,
    RoleContext,
    RoleRegistry,
    RoleResult,
    register_role,
    registry,
)


class _DummyRole(Role):
    name = "dummy"
    purpose = "test double"

    def run(self, ctx: RoleContext) -> RoleResult:
        return RoleResult(ok=True, summary="dummy ran")


class TestRoleResult(unittest.TestCase):
    def test_empty_summary_rejected(self):
        with self.assertRaises(ValueError):
            RoleResult(ok=True, summary="  ")


class TestRoleRegistry(unittest.TestCase):
    def test_register_and_create(self):
        reg = RoleRegistry()
        reg.register(_DummyRole)
        role = reg.create("dummy")
        self.assertIsInstance(role, _DummyRole)
        self.assertEqual(reg.names(), ["dummy"])

    def test_duplicate_rejected(self):
        reg = RoleRegistry()
        reg.register(_DummyRole)
        with self.assertRaises(ValueError):
            reg.register(_DummyRole)

    def test_unknown_role(self):
        reg = RoleRegistry()
        with self.assertRaises(KeyError):
            reg.create("nope")

    def test_non_role_rejected(self):
        reg = RoleRegistry()
        with self.assertRaises(ValueError):
            reg.register(object)  # type: ignore[arg-type]

    def test_decorator_registers(self):
        before = len(registry)

        @register_role
        class _Deco(Role):
            name = "deco_test_role_xyz"
            purpose = "decorator test"

            def run(self, ctx):
                return RoleResult(ok=True, summary="x")

        try:
            self.assertEqual(len(registry), before + 1)
            self.assertIsInstance(registry.create("deco_test_role_xyz"), _Deco)
        finally:
            # Keep global registry clean for other tests.
            del registry._roles["deco_test_role_xyz"]

    def test_role_run_contract(self):
        ctx = RoleContext(project_dir="/tmp")
        result = _DummyRole().run(ctx)
        self.assertTrue(result.ok)
        self.assertIsInstance(result.artifacts, dict)

    def test_emit_never_raises(self):
        ctx = RoleContext(project_dir="/tmp", event_log=None)
        _DummyRole().emit(ctx, "ANYTHING", {})  # must not raise


if __name__ == "__main__":
    unittest.main()
