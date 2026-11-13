import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from lumen_landrop.archive import TransferError
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

    def test_sender_streams_archive_without_loading_it_into_memory(self):
        source = Path(self.temporary.name) / "clip.txt"
        source.write_text("stream me", encoding="utf-8")
        with patch.object(Path, "read_bytes", side_effect=AssertionError("archive must be streamed")):
            result = send(self.url, "test-token", [str(source)])
        received = Path(result["path"]) / "clip.txt"
        self.assertEqual(received.read_text(encoding="utf-8"), "stream me")

    def test_sender_reports_receiver_rejection(self):
        limited_output = Path(self.temporary.name) / "sender-limited-received"
        limited_server = DropServer(
            ("127.0.0.1", 0),
            DropHandler,
            "test-token",
            limited_output,
            max_upload_bytes=1,
        )
        limited_thread = threading.Thread(target=limited_server.serve_forever, daemon=True)
        limited_thread.start()
        source = Path(self.temporary.name) / "too-large.txt"
        source.write_text("too large", encoding="utf-8")
        try:
            with self.assertRaisesRegex(TransferError, r"receiver rejected transfer \(413\)"):
                send(f"http://127.0.0.1:{limited_server.server_port}", "test-token", [str(source)])
            self.assertFalse(limited_output.exists())
        finally:
            limited_server.shutdown()
            limited_server.server_close()
            limited_thread.join(timeout=2)

    def test_rejects_oversized_upload_before_creating_output(self):
        limited_output = Path(self.temporary.name) / "limited-received"
        limited_server = DropServer(
            ("127.0.0.1", 0),
            DropHandler,
            "test-token",
            limited_output,
            max_upload_bytes=1,
        )
        limited_thread = threading.Thread(target=limited_server.serve_forever, daemon=True)
        limited_thread.start()
        try:
            request = Request(
                f"http://127.0.0.1:{limited_server.server_port}/upload",
                data=b"ab",
                method="POST",
                headers={
                    "X-LumenLanDrop-Token": "test-token",
                    "X-LumenLanDrop-SHA256": "not-needed-for-size-rejection",
                },
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request)
            self.assertEqual(raised.exception.code, 413)
            self.assertFalse(limited_output.exists())
        finally:
            limited_server.shutdown()
            limited_server.server_close()
            limited_thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
