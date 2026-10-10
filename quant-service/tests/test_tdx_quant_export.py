import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "tdx-quant-export.py"
spec = importlib.util.spec_from_file_location("tdx_quant_export", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


class FakeTq:
    def __init__(self):
        self.initialized = None
        self.calls = []

    def initialize(self, path):
        self.initialized = path

    def get_market_data(self, **kwargs):
        self.calls.append(kwargs)
        return {name: {"600000.SH": {"2026-10-09": value}} for name, value in {
            "Time": "2026-10-09", "Open": 10, "High": 11, "Low": 9, "Close": 10.5,
            "Volume": 12, "Amount": 34,
        }.items()}


class TdxQuantExportTests(unittest.TestCase):
    def test_export_writes_contract_and_converts_tdx_units(self):
        with tempfile.TemporaryDirectory() as root:
            args = module.argparse.Namespace(out=Path(root), symbols="600000.SH", kinds="daily",
                                             start="", end="")
            fake = FakeTq()
            outputs = module.export(fake, args)
            self.assertEqual(len(outputs), 1)
            text = outputs[0].read_text()
            self.assertIn("volume_shares,amount_yuan", text)
            self.assertIn("1200.0,340000.0", text)
            self.assertEqual(fake.calls[0]["period"], "1d")

    def test_dry_run_does_not_import_or_initialize(self):
        self.assertEqual(module.main(["--out", "/tmp/tdx-test", "--symbols", "600000.SH", "--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
