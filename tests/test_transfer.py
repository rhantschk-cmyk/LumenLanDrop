import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from lumen_landrop.cli import DropHandler, DropServer, send


class LanDropTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name) / "received"
        self.server = DropServer(("127.0.0.1", 0), DropHandler, "test-token", self.output)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary.cleanup()

    def test_health_and_directory_transfer(self):
        self.assertEqual(json.loads(urlopen(self.url + "/health").read())["status"], "ok")
        source = Path(self.temporary.name) / "exports"
        source.mkdir()
        (source / "video.txt").write_text("ready", encoding="utf-8")
        result = send(self.url, "test-token", [str(source)])
        self.assertIn("sha256", result)
        received = Path(result["path"]) / "exports" / "video.txt"
        self.assertEqual(received.read_text(encoding="utf-8"), "ready")


if __name__ == "__main__":
    unittest.main()
