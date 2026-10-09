"""Slice 34 tests: archdocs renderers (pure functions)."""

from __future__ import annotations

import unittest

from draupnir_forge.archdocs import (
    render_architecture,
    render_interfaces,
    render_invariants,
)

SAMPLE_ARCH = {
    "domains": [
        {
            "name": "roles",
            "owner": "architect",
            "files": ["src/draupnir_forge/roles/cartographer.py"],
        },
        {
            "name": "tools",
            "owner": "worker",
            "files": ["src/draupnir_forge/tools.py"],
        },
    ],
    "interfaces": [
        {
            "module": "draupnir_forge.roles.cartographer",
            "functions": ["map_repo", "DomainMap"],
        },
        {"module": "draupnir_forge.tools", "functions": []},
    ],
    "invariants": [
        "domain 'roles' owns its files — no other domain may claim them.",
        "no hardcoding of secrets in code.",
    ],
    "dependency_direction": {"roles": ["tools"], "tools": []},
}


class TestRenderArchitecture(unittest.TestCase):
    def test_mermaid_block_present_with_edges(self):
        text = render_architecture(SAMPLE_ARCH)
        self.assertIn("```mermaid", text)
        self.assertIn("flowchart LR", text)
        self.assertIn("roles", text)
        self.assertIn("tools", text)
        self.assertIn("-->", text)

    def test_domain_tables_well_formed(self):
        text = render_architecture(SAMPLE_ARCH)
        self.assertIn("| Domain | Owner | Files | Dependencies |", text)
        self.assertIn("| roles | architect | 1 | tools |", text)
        self.assertIn("| tools | worker | 1 | _none_ |", text)
        self.assertIn("### roles (owner: architect)", text)
        self.assertIn("- `src/draupnir_forge/roles/cartographer.py`", text)

    def test_interface_summary_table(self):
        text = render_architecture(SAMPLE_ARCH)
        self.assertIn("| `draupnir_forge.roles.cartographer` | `map_repo`, `DomainMap` |", text)

    def test_empty_arch_renders(self):
        text = render_architecture({})
        self.assertIn("```mermaid", text)
        self.assertIn("no inter-domain dependencies", text)
        self.assertIn("| _none_ | _none_ | _none_ | _none_ |", text)

    def test_no_external_dependency_edges_leak(self):
        arch = dict(SAMPLE_ARCH, dependency_direction={"roles": ["ghost"]})
        text = render_architecture(arch)
        self.assertNotIn("ghost", text)


class TestRenderInterfaces(unittest.TestCase):
    def test_per_module_tables(self):
        text = render_interfaces(SAMPLE_ARCH)
        self.assertIn("## `draupnir_forge.roles.cartographer`", text)
        self.assertIn("| Symbol | Kind |", text)
        self.assertIn("| `map_repo` | function |", text)
        self.assertIn("| `DomainMap` | class |", text)
        self.assertIn("## `draupnir_forge.tools`", text)

    def test_empty_interfaces(self):
        text = render_interfaces({})
        self.assertIn("_No interfaces recorded._", text)


class TestRenderInvariants(unittest.TestCase):
    def test_checklist_format(self):
        text = render_invariants(SAMPLE_ARCH["invariants"])
        self.assertIn(
            "- [ ] domain 'roles' owns its files — no other domain may claim them.",
            text,
        )
        self.assertIn("- [ ] no hardcoding of secrets in code.", text)

    def test_empty_invariants(self):
        text = render_invariants([])
        self.assertIn("_No invariants recorded._", text)


if __name__ == "__main__":
    unittest.main()
