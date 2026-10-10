"""Slice 2 acceptance tests: layered ForgeConfig.

Every test isolates HOME and DRAUPNIR_* so the developer's real
environment can never leak into (or out of) the assertions.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import yaml

from draupnir_forge.config import ForgeConfig


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle)


class IsolatedEnvTestCase(unittest.TestCase):
    """Redirects HOME to a temp dir and clears DRAUPNIR_* variables."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.project = Path(self.tmp.name) / "project"
        self.project.mkdir()

        self._saved = {}
        for var in ("HOME", "DRAUPNIR_DATA_DIR"):
            self._saved[var] = os.environ.get(var)
        os.environ["HOME"] = str(self.home)
        os.environ.pop("DRAUPNIR_DATA_DIR", None)
        self._saved_draupnir = {
            k: v for k, v in os.environ.items() if k.startswith("DRAUPNIR_")
        }
        for k in self._saved_draupnir:
            del os.environ[k]
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in [k for k in os.environ if k.startswith("DRAUPNIR_")]:
            del os.environ[k]
        os.environ.update(self._saved_draupnir)

    def home_config_path(self) -> Path:
        return self.home / ".draupnir" / "config.yaml"

    def project_config_path(self) -> Path:
        return self.project / ".mythis" / "config.yaml"


class TestDefaults(IsolatedEnvTestCase):
    def test_builtin_defaults_load(self):
        cfg = ForgeConfig.load()
        self.assertEqual(cfg.get("model.provider"), "openai")
        self.assertEqual(cfg.get("model.name"), "gpt-4o-mini")
        self.assertEqual(cfg.get("model.api_key_env"), "OPENAI_API_KEY")
        self.assertEqual(cfg.get("model.timeout_s"), 120)
        self.assertEqual(cfg.get("model.max_retries"), 3)
        self.assertEqual(cfg.get("budget.max_tokens"), 2000000)
        self.assertAlmostEqual(cfg.get("budget.max_cost_usd"), 25.00)
        self.assertEqual(cfg.get("autonomy"), "trusted")
        self.assertIs(cfg.get("git.auto_checkpoint"), True)
        self.assertIs(cfg.get("tools.allow_exec"), True)
        self.assertIs(cfg.get("tools.allow_install"), False)
        self.assertIs(cfg.get("tools.allow_network"), False)
        self.assertIs(cfg.get("tools.allow_destructive"), False)
        self.assertEqual(cfg.get("tools.default_timeout_s"), 300)


class TestMissingFiles(IsolatedEnvTestCase):
    def test_no_user_no_project_files(self):
        # Neither ~/.draupnir/config.yaml nor <project>/.mythis/config.yaml
        # exists: loading must succeed on defaults alone.
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.get("autonomy"), "trusted")

    def test_empty_project_dir_ok(self):
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.get("model.provider"), "openai")

    def test_corrupt_home_yaml_skipped(self):
        self.home_config_path().parent.mkdir(parents=True, exist_ok=True)
        self.home_config_path().write_text(":\n: bad: [unclosed\n", encoding="utf-8")
        cfg = ForgeConfig.load()  # must not raise
        self.assertEqual(cfg.get("model.name"), "gpt-4o-mini")

    def test_non_mapping_yaml_skipped(self):
        _write_yaml(self.home_config_path(), ["not", "a", "mapping"])  # type: ignore[arg-type]
        cfg = ForgeConfig.load()
        self.assertEqual(cfg.get("autonomy"), "trusted")


class TestLayeredOverrides(IsolatedEnvTestCase):
    def test_full_precedence_chain(self):
        _write_yaml(self.home_config_path(), {"model": {"name": "home-model"},
                                              "autonomy": "guided"})
        _write_yaml(self.project_config_path(), {"model": {"name": "project-model"}})
        os.environ["DRAUPNIR_MODEL__NAME"] = "env-model"

        # Explicit dict beats everything.
        cfg = ForgeConfig.load(project_dir=self.project,
                               overrides={"model": {"name": "explicit-model"}})
        self.assertEqual(cfg.get("model.name"), "explicit-model")
        # Sibling keys from weaker layers survive the merge.
        self.assertEqual(cfg.get("model.provider"), "openai")
        self.assertEqual(cfg.get("autonomy"), "guided")  # home beats default

        # Without overrides, env beats project beats home beats defaults.
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.get("model.name"), "env-model")

        del os.environ["DRAUPNIR_MODEL__NAME"]
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.get("model.name"), "project-model")

        self.project_config_path().unlink()
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.get("model.name"), "home-model")

        self.home_config_path().unlink()
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.get("model.name"), "gpt-4o-mini")

    def test_env_double_underscore_nesting(self):
        os.environ["DRAUPNIR_MODEL__NAME"] = "gpt-4o"
        os.environ["DRAUPNIR_GIT__AUTO_CHECKPOINT"] = "false"
        os.environ["DRAUPNIR_BUDGET__MAX_COST_USD"] = "99.5"
        os.environ["DRAUPNIR_AUTONOMY"] = "deep"
        cfg = ForgeConfig.load()
        self.assertEqual(cfg.get("model.name"), "gpt-4o")
        self.assertIs(cfg.get("git.auto_checkpoint"), False)
        self.assertAlmostEqual(cfg.get("budget.max_cost_usd"), 99.5)
        self.assertEqual(cfg.get("autonomy"), "deep")

    def test_env_bool_variants(self):
        for truthy in ("true", "True", "1", "yes", "ON"):
            os.environ["DRAUPNIR_TOOLS__ALLOW_INSTALL"] = truthy
            self.assertIs(ForgeConfig.load().get("tools.allow_install"), True,
                          f"env value {truthy!r} should coerce to True")
        for falsy in ("false", "False", "0", "no", "off"):
            os.environ["DRAUPNIR_TOOLS__ALLOW_INSTALL"] = falsy
            self.assertIs(ForgeConfig.load().get("tools.allow_install"), False,
                          f"env value {falsy!r} should coerce to False")

    def test_explicit_dotted_keys(self):
        cfg = ForgeConfig.load(overrides={"tools.allow_install": True,
                                          "budget.max_tokens": 500})
        self.assertIs(cfg.get("tools.allow_install"), True)
        self.assertEqual(cfg.get("budget.max_tokens"), 500)
        # Untouched siblings keep their defaults.
        self.assertIs(cfg.get("tools.allow_network"), False)

    def test_project_layer_without_home(self):
        _write_yaml(self.project_config_path(), {"autonomy": "deep"})
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.get("autonomy"), "deep")


class TestValidation(IsolatedEnvTestCase):
    def test_invalid_autonomy_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            ForgeConfig.load(overrides={"autonomy": "reckless"})
        self.assertIn("autonomy", str(ctx.exception))

    def test_invalid_provider_rejected(self):
        with self.assertRaises(ValueError):
            ForgeConfig.load(overrides={"model": {"provider": "smoke-signals"}})

    def test_wrong_type_rejected(self):
        with self.assertRaises(ValueError):
            ForgeConfig.load(overrides={"budget": {"max_tokens": "a lot"}})

    def test_bool_rejects_non_bool_string(self):
        with self.assertRaises(ValueError):
            ForgeConfig.load(overrides={"git": {"auto_checkpoint": "maybe"}})

    def test_below_minimum_rejected(self):
        with self.assertRaises(ValueError):
            ForgeConfig.load(overrides={"model": {"timeout_s": 0}})
        with self.assertRaises(ValueError):
            ForgeConfig.load(overrides={"budget": {"max_cost_usd": -1}})

    def test_empty_string_rejected_where_forbidden(self):
        with self.assertRaises(ValueError):
            ForgeConfig.load(overrides={"model": {"name": ""}})

    def test_invalid_home_file_value_rejected(self):
        _write_yaml(self.home_config_path(), {"autonomy": "anarchy"})
        with self.assertRaises(ValueError) as ctx:
            ForgeConfig.load()
        self.assertIn("autonomy", str(ctx.exception))

    def test_invalid_env_value_rejected(self):
        os.environ["DRAUPNIR_AUTONOMY"] = "bogus"
        with self.assertRaises(ValueError):
            ForgeConfig.load()

    def test_non_mapping_overrides_rejected(self):
        with self.assertRaises(ValueError):
            ForgeConfig.load(overrides=["not", "a", "mapping"])  # type: ignore[arg-type]


class TestAccessors(IsolatedEnvTestCase):
    def test_get_dotted(self):
        cfg = ForgeConfig.load()
        self.assertEqual(cfg.get("model.provider"), "openai")

    def test_get_missing_raises_keyerror(self):
        cfg = ForgeConfig.load()
        with self.assertRaises(KeyError):
            cfg.get("model.nonexistent")

    def test_get_with_default(self):
        cfg = ForgeConfig.load()
        self.assertEqual(cfg.get("model.nonexistent", "fallback"), "fallback")

    def test_to_dict_round_trip(self):
        cfg = ForgeConfig.load(overrides={"autonomy": "deep"})
        data = cfg.to_dict()
        self.assertEqual(data["autonomy"], "deep")
        self.assertEqual(data["model"]["provider"], "openai")
        self.assertEqual(data["tools"]["allow_destructive"], False)

    def test_to_dict_returns_copy(self):
        cfg = ForgeConfig.load()
        data = cfg.to_dict()
        data["model"]["name"] = "mutated"
        self.assertEqual(cfg.get("model.name"), "gpt-4o-mini")


class TestSkippedFiles(IsolatedEnvTestCase):
    """Skipped config files are recorded with path, layer, and reason."""

    def test_malformed_user_config_recorded(self):
        self.home_config_path().parent.mkdir(parents=True, exist_ok=True)
        self.home_config_path().write_text(":\n: bad: [unclosed\n",
                                           encoding="utf-8")
        cfg = ForgeConfig.load()  # must not raise
        self.assertEqual(len(cfg.skipped_files), 1)
        entry = cfg.skipped_files[0]
        self.assertEqual(entry["layer"], "user")
        self.assertEqual(entry["reason"], "yaml_error")
        self.assertIn(".draupnir", entry["path"])
        # The summary surfaces the skip.
        summary = cfg.summary()
        self.assertIn("Skipped config files (1)", summary)
        self.assertIn("yaml_error", summary)
        self.assertIn("user", summary)

    def test_not_a_mapping_project_config_recorded(self):
        _write_yaml(self.project_config_path(), ["not", "a", "mapping"])  # type: ignore[arg-type]
        cfg = ForgeConfig.load(project_dir=self.project)
        by_layer = {e["layer"]: e["reason"] for e in cfg.skipped_files}
        self.assertEqual(by_layer.get("project"), "not_a_mapping")

    def test_valid_config_leaves_skipped_files_empty(self):
        _write_yaml(self.home_config_path(), {"autonomy": "deep"})
        _write_yaml(self.project_config_path(), {"autonomy": "trusted"})
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.skipped_files, [])
        self.assertIn("No config files were skipped.", cfg.summary())

    def test_missing_files_are_not_skips(self):
        # Absent files are silently skipped without a skipped_files entry.
        cfg = ForgeConfig.load(project_dir=self.project)
        self.assertEqual(cfg.skipped_files, [])


if __name__ == "__main__":
    unittest.main()
