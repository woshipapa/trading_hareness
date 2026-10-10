import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("generate-tdx-bj-codes.py")
# The shape of the server file: a count row, then pipe rows of market, old code, new code, name, date (GBK).
ADDEDCODE = "000000,0,2,20261009,\r\n44|832000|920000|安徽凤凰(已转板)|20251009\r\n44|874709|920288|示例股份|20251009\r\n".encode("gb18030")


class GenerateTdxBjCodesTests(unittest.TestCase):
    def _generate(self, root, data):
        source = Path(root) / "addedcode_bj.cfg"
        source.write_bytes(data)
        output = Path(root) / "tdx_bj_codes.py"
        result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--output", str(output)],
                                capture_output=True, text=True)
        return result, output

    def test_the_module_holds_the_pairs_and_names_its_source_md5_and_time(self):
        with tempfile.TemporaryDirectory() as root:
            result, output = self._generate(root, ADDEDCODE)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = output.read_text(encoding="utf-8")
        namespace = {}
        exec(text, namespace)
        self.assertEqual(namespace["OLD_TO_NEW"], {"832000": "920000", "874709": "920288"})
        self.assertIn("# source=addedcode_bj.cfg\n", text)
        self.assertIn(f"# md5={hashlib.md5(ADDEDCODE).hexdigest()}\n", text)
        self.assertRegex(text, r"# generated_at_utc=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00\n")

    def test_an_input_without_a_pair_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            result, output = self._generate(root, b"000000,0,0,20261009,\r\n")
            self.assertEqual(result.returncode, 2)
            self.assertIn("holds no old-to-new BJ code", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
