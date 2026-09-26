import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import itougu_video_worker as worker


class VideoWorkerTests(unittest.TestCase):
    def test_url_is_allowlisted(self):
        self.assertEqual(worker.validate_url("https://voss.itougu.com/a.m3u8"), "https://voss.itougu.com/a.m3u8")
        with self.assertRaises(ValueError):
            worker.validate_url("https://example.com/a.m3u8")
        with self.assertRaises(ValueError):
            worker.validate_url("http://voss.itougu.com/a.m3u8")

    def test_playlist_duration(self):
        response = type("Response", (), {"__enter__": lambda s: s, "__exit__": lambda *a: None, "read": lambda s, n: b"#EXTM3U\n#EXTINF:10.5,\na.ts\n#EXTINF:9.5,\nb.ts\n"})()
        with patch.object(worker.urllib.request, "urlopen", return_value=response):
            self.assertEqual(worker.playlist_info("https://voss.itougu.com/a.m3u8"), {"url": "https://voss.itougu.com/a.m3u8", "segments": 2, "duration_seconds": 20.0})


if __name__ == "__main__":
    unittest.main()
