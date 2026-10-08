"""The settings registry: every key read is declared, and each kind parses one way."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from app import settings

APP = Path(__file__).resolve().parents[1] / "app"
ACCESSORS = {"flag", "text", "csv", "integer", "number"}
# Tushare was retired on 2026-10-08; its remaining readers are being removed and
# its keys are deliberately not part of the registry.
RETIRED_PREFIXES = ("TUSHARE_",)


def keys_read(path: Path) -> set[str]:
    """Literal environment keys read in one module, directly or through the accessors."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        key = None
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            callee = ast.unparse(node.func)
            if callee in {"os.getenv", "os.environ.get", "env.get", "environ.get"} \
                    or callee.split(".")[-1] in ACCESSORS and callee.startswith(("settings.", "flag", "text", "csv", "integer", "number")):
                key = node.args[0].value
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value) == "os.environ" and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        if isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]+", key):
            found.add(key)
    return found


class RegistryTests(unittest.TestCase):
    def test_every_key_read_in_the_app_is_declared(self) -> None:
        undeclared = {}
        for path in sorted(APP.rglob("*.py")):
            if path.name == "settings.py":
                continue
            for key in keys_read(path):
                if key not in settings.SETTINGS and not key.startswith(RETIRED_PREFIXES):
                    undeclared.setdefault(key, []).append(path.relative_to(APP).as_posix())
        self.assertEqual(undeclared, {}, "declare these in app/settings.py")

    def test_every_declared_key_is_still_read_somewhere(self) -> None:
        read = set().union(*(keys_read(path) for path in APP.rglob("*.py") if path.name != "settings.py"))
        self.assertEqual(sorted(set(settings.SETTINGS) - read), [], "remove settings nothing reads")

    def test_main_reads_the_environment_only_through_settings(self) -> None:
        source = (APP / "main.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bos\.(getenv|environ)\b", source))

    def test_the_inventory_never_shows_a_secret_default(self) -> None:
        rows = {row["name"]: row for row in settings.inventory()}
        self.assertTrue(rows["QUANT_WRITE_API_KEY"]["secret"])
        self.assertIsNone(rows["QUANT_WRITE_API_KEY"]["default"])
        self.assertEqual(rows["INTRADAY_MINUTE_PROFILE_MAX_SYMBOLS"]["default"], "40")


class AccessorTests(unittest.TestCase):
    def test_flags(self) -> None:
        for raw, expected in (("1", True), (" TRUE ", True), ("yes", True), ("On", True),
                              ("0", False), ("false", False), ("", False), ("enabled", False)):
            self.assertIs(settings.flag("AUCTION_PULSE_ENABLED", {"AUCTION_PULSE_ENABLED": raw}), expected, raw)
        self.assertTrue(settings.flag("AUCTION_PULSE_ENABLED", {}))            # declared default true
        self.assertFalse(settings.flag("QUANT_LONGHU_FULL_MARKET_ENABLED", {}))  # declared default false

    def test_integers_clamp_and_fall_back(self) -> None:
        read = lambda raw: settings.integer("INTRADAY_MINUTE_PROFILE_MAX_SYMBOLS", minimum=1, maximum=100,
                                            environ={} if raw is None else {"INTRADAY_MINUTE_PROFILE_MAX_SYMBOLS": raw})
        self.assertEqual([read(None), read("7"), read(" 7 "), read("0"), read("900"), read("many")], [40, 7, 7, 1, 100, 40])
        self.assertEqual(settings.integer("INTRADAY_MINUTE_PROFILE_MAX_SYMBOLS", fallback=3,
                                          environ={"INTRADAY_MINUTE_PROFILE_MAX_SYMBOLS": "x"}), 3)

    def test_numbers_text_and_csv(self) -> None:
        self.assertEqual(settings.number("AUCTION_PULSE_INTERVAL_SECONDS", minimum=1.0, maximum=30.0,
                                         environ={"AUCTION_PULSE_INTERVAL_SECONDS": "0.2"}), 1.0)
        self.assertEqual(settings.number("AUCTION_PULSE_INTERVAL_SECONDS", environ={"AUCTION_PULSE_INTERVAL_SECONDS": "nan?"}), 2.0)
        self.assertEqual(settings.text("QUANT_DASHBOARD_PUBLIC_URL", {"QUANT_DASHBOARD_PUBLIC_URL": " https://x/ "}), "https://x/")
        self.assertEqual(settings.text("QUANT_DASHBOARD_PUBLIC_URL", {}), "")
        self.assertEqual(settings.csv("QUANT_UNIVERSE", {"QUANT_UNIVERSE": " 000001.SZ, ,600000.SH "}), ["000001.SZ", "600000.SH"])

    def test_an_undeclared_key_is_an_error_not_a_silent_default(self) -> None:
        with self.assertRaises(KeyError):
            settings.flag("SOME_NEW_SWITCH", {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
