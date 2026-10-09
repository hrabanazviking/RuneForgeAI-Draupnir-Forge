"""Slice 3 acceptance tests: Forge logging infrastructure.

Logging is global state, so every test resets the ``draupnir_forge``
root logger in setUp/tearDown with unique logger names — no test can
be polluted by another, whatever order unittest chooses.
"""

from __future__ import annotations

import contextlib
import io
import logging
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path

from draupnir_forge import log as forge_log
from draupnir_forge.log import (
    ForgeFormatter,
    bind_role,
    configure_file_logging,
    get_logger,
)


class CapturingHandler(logging.Handler):
    """Handler that keeps emitted records for inspection."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class LoggingIsolationTestCase(unittest.TestCase):
    """Resets the draupnir_forge root logger around each test."""

    _counter = 0

    def setUp(self):
        LoggingIsolationTestCase._counter += 1
        self.tag = f"t{LoggingIsolationTestCase._counter}_{self._testMethodName}"
        self.root = logging.getLogger(forge_log.ROOT_LOGGER_NAME)
        self._saved_handlers = list(self.root.handlers)
        self._saved_level = self.root.level
        for handler in self._saved_handlers:
            self.root.removeHandler(handler)
        ready_attr = forge_log._CONSOLE_READY_ATTR
        self._saved_ready = getattr(self.root, ready_attr, None)
        if hasattr(self.root, ready_attr):
            delattr(self.root, ready_attr)
        self.root.setLevel(logging.WARNING)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        for handler in list(self.root.handlers):
            self.root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
        for handler in self._saved_handlers:
            self.root.addHandler(handler)
        self.root.setLevel(self._saved_level)
        ready_attr = forge_log._CONSOLE_READY_ATTR
        if self._saved_ready is not None:
            setattr(self.root, ready_attr, self._saved_ready)
        elif hasattr(self.root, ready_attr):
            delattr(self.root, ready_attr)

    def unique_name(self, base: str) -> str:
        return f"{base}_{self.tag}"


class TestFormatter(LoggingIsolationTestCase):
    def _record(self, **kwargs):
        record = logging.LogRecord(
            name="draupnir_forge.fmt", level=logging.WARNING,
            pathname=__file__, lineno=1, msg="hello", args=None, exc_info=None,
        )
        for key, value in kwargs.items():
            setattr(record, key, value)
        return record

    def test_exact_spec_format(self):
        out = ForgeFormatter().format(self._record())
        self.assertRegex(
            out,
            r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "
            r"\[WARNING\] draupnir_forge\.fmt: hello$",
        )

    def test_role_shown_in_brackets(self):
        out = ForgeFormatter().format(self._record(role="Skald"))
        self.assertIn("draupnir_forge.fmt [Skald]: hello", out)

    def test_no_role_no_brackets(self):
        out = ForgeFormatter().format(self._record())
        self.assertNotIn("[", out.split("] ", 1)[-1].rsplit(": ", 1)[0])


class TestGetLogger(LoggingIsolationTestCase):
    def test_console_output_format_and_default_level(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            logger = get_logger(self.unique_name("console"))
            logger.info("too quiet")
            logger.warning("loud enough")
        text = buf.getvalue()
        self.assertNotIn("too quiet", text)
        self.assertRegex(text, r"\[WARNING\] draupnir_forge\.\S+: loud enough")

    def test_verbose_enables_debug(self):
        verbose = get_logger(self.unique_name("verbose"), verbose=True)
        quiet = get_logger(self.unique_name("quiet"))
        self.assertTrue(verbose.isEnabledFor(logging.DEBUG))
        self.assertFalse(quiet.isEnabledFor(logging.INFO))
        self.assertTrue(quiet.isEnabledFor(logging.WARNING))

    def test_console_handler_added_exactly_once(self):
        get_logger(self.unique_name("a"))
        get_logger(self.unique_name("a"))
        get_logger(self.unique_name("b"))
        console_handlers = [
            h for h in self.root.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, RotatingFileHandler)
        ]
        self.assertEqual(len(console_handlers), 1)


class TestFileLogging(LoggingIsolationTestCase):
    def test_log_file_created_and_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(configure_file_logging(tmp))
            log_path = Path(tmp) / ".mythis" / "logs" / "forge.log"
            self.assertTrue(log_path.is_file())
            logger = get_logger(self.unique_name("file"))
            logger.warning("written-to-file")
            for handler in self.root.handlers:
                handler.flush()
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("[WARNING]", content)
            self.assertIn("written-to-file", content)

    def test_second_call_does_not_duplicate_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(configure_file_logging(tmp))
            self.assertTrue(configure_file_logging(tmp))
            file_handlers = [
                h for h in self.root.handlers if isinstance(h, RotatingFileHandler)
            ]
            self.assertEqual(len(file_handlers), 1)

    def test_rotation_kicks_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(configure_file_logging(tmp, max_bytes=200, backup_count=1))
            logger = get_logger(self.unique_name("rotate"))
            for i in range(30):
                logger.warning("rotation probe line %02d padding-padding", i)
            for handler in self.root.handlers:
                handler.flush()
            log_dir = Path(tmp) / ".mythis" / "logs"
            self.assertTrue((log_dir / "forge.log.1").is_file())

    def test_unwritable_dir_falls_back_to_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("I am a file, not a directory.", encoding="utf-8")
            # <blocker>/.mythis/logs cannot be created: must not raise.
            self.assertFalse(configure_file_logging(blocker))
            logger = get_logger(self.unique_name("fallback"))
            self.assertTrue(logger.isEnabledFor(logging.WARNING))


class TestBindRole(LoggingIsolationTestCase):
    def test_role_field_present_on_record(self):
        logger = get_logger(self.unique_name("role"))
        capture = CapturingHandler()
        logger.addHandler(capture)
        adapter = bind_role(logger, "Skald")
        adapter.warning("the vision speaks")
        self.assertEqual(len(capture.records), 1)
        self.assertEqual(capture.records[0].role, "Skald")

    def test_role_visible_in_formatted_output(self):
        logger = get_logger(self.unique_name("rolefmt"))
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(ForgeFormatter())
        logger.addHandler(handler)
        bind_role(logger, "Auditor").warning("judgment comes")
        self.assertIn("[Auditor]", buf.getvalue())

    def test_role_name_sanitized(self):
        logger = get_logger(self.unique_name("rolesafe"))
        capture = CapturingHandler()
        logger.addHandler(capture)
        adapter = bind_role(logger, "Sk)ald %(evil)s")
        adapter.warning("x")
        role = capture.records[0].role
        self.assertNotIn(")", role)
        self.assertNotIn("%", role)
        self.assertTrue(role)  # never empty

    def test_bind_role_returns_adapter(self):
        logger = get_logger(self.unique_name("adapter"))
        self.assertIsInstance(bind_role(logger, "Heimdallr"), logging.LoggerAdapter)


class TestLibraryHygiene(unittest.TestCase):
    def test_no_print_in_library_code(self):
        import ast

        package = Path(forge_log.__file__).parent
        for module in ("log.py", "config.py", "_paths.py"):
            source = (package / module).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=module)
            calls = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
            ]
            self.assertEqual(calls, [], f"{module} must not call print()")


if __name__ == "__main__":
    unittest.main()
