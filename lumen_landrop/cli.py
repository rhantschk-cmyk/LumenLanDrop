"""HTTP protocol and CLI for Lumen LAN Drop."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import secrets
import sys
import tempfile
import threading
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .archive import TransferError, build_archive, extract_archive_safely, sha256_file


DEFAULT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024 * 1024


class DropServer(ThreadingHTTPServer):
    def __init__(
        self,
        address,
        handler,
        token: str,
        output: Path,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    ):
        if not token.strip():
            raise TransferError("receiver token must not be blank")
        super().__init__(address, handler)
        self.token = token
        self.output = output
        self.max_upload_bytes = max_upload_bytes


class DropHandler(BaseHTTPRequestHandler):
    server: DropServer

    def log_message(self, format: str, *args) -> None:
        return

    def _reply(self, code: int, payload: dict) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        self._reply(HTTPStatus.OK, {"service": "lumen-landrop", "status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/upload":
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not secrets.compare_digest(self.headers.get("X-LumenLanDrop-Token", ""), self.server.token):
            self._reply(HTTPStatus.UNAUTHORIZED, {"error": "invalid token"})
            return
        length = self.headers.get("Content-Length")
        if not length or not length.isdigit() or int(length) <= 0:
            self._reply(HTTPStatus.BAD_REQUEST, {"error": "a positive Content-Length is required"})
            return
        content_length = int(length)
        if content_length > self.server.max_upload_bytes:
            self._reply(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": "upload exceeds receiver size limit"},
            )
            return
        self.server.output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".lumen-landrop-", dir=self.server.output) as temporary:
            staging = Path(temporary) / "payload"
            staging.mkdir()
            archive_path = Path(temporary) / "upload.tar"
            remaining = content_length
            with archive_path.open("xb") as archive:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        self._reply(HTTPStatus.BAD_REQUEST, {"error": "truncated upload"})
                        return
                    archive.write(chunk)
                    remaining -= len(chunk)
            actual_sha256 = sha256_file(archive_path)
            expected_sha256 = self.headers.get("X-LumenLanDrop-SHA256", "")
            if not expected_sha256 or not secrets.compare_digest(actual_sha256, expected_sha256):
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "archive checksum mismatch"})
                return
            try:
                with archive_path.open("rb") as archive:
                    files, _ = extract_archive_safely(archive, staging)
            except (OSError, TransferError) as exc:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": f"unsafe upload: {exc}"})
                return
            transfer_name = datetime.now(timezone.utc).strftime("drop-%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
            final = self.server.output / transfer_name
            os.replace(staging, final)
            self._reply(HTTPStatus.CREATED, {"sha256": actual_sha256, "files": files, "path": str(final)})


def serve(host: str, port: int, token: str, output: Path, max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES) -> None:
    if max_upload_bytes <= 0:
        raise TransferError("max upload size must be positive")
    server = DropServer((host, port), DropHandler, token, output, max_upload_bytes)
    print(f"Lumen LAN Drop listening on http://{host}:{port}; output: {output}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLumen LAN Drop stopped.", flush=True)
    finally:
        server.server_close()


def send(target: str, token: str, paths: list[str]) -> dict:
    sources = [Path(path).expanduser().resolve() for path in paths]
    target_parts = urllib.parse.urlsplit(target)
    if target_parts.scheme not in {"http", "https"} or not target_parts.hostname:
        raise TransferError("receiver URL must include an http:// or https:// host")
    if target_parts.query or target_parts.fragment:
        raise TransferError("receiver URL must not include a query or fragment")
    try:
        connection_class = http.client.HTTPSConnection if target_parts.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(target_parts.hostname, target_parts.port, timeout=60)
    except ValueError as exc:
        raise TransferError(f"invalid receiver URL: {exc}") from exc
    endpoint = (target_parts.path.rstrip("/") + "/upload") or "/upload"
    with tempfile.TemporaryDirectory(prefix="lumen-landrop-") as temporary:
        archive = Path(temporary) / "payload.tar"
        build_archive(sources, archive)
        digest = sha256_file(archive)
        try:
            connection.putrequest("POST", endpoint)
            connection.putheader("Content-Type", "application/x-tar")
            connection.putheader("Content-Length", str(archive.stat().st_size))
            connection.putheader("X-LumenLanDrop-Token", token)
            connection.putheader("X-LumenLanDrop-SHA256", digest)
            connection.endheaders()
            with archive.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read().decode("utf-8", errors="replace")
        except (OSError, http.client.HTTPException) as exc:
            raise TransferError(f"cannot reach receiver: {exc}") from exc
        finally:
            connection.close()
    if not 200 <= response.status < 300:
        raise TransferError(f"receiver rejected transfer ({response.status}): {body}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TransferError("receiver returned invalid JSON") from exc
    if result.get("sha256") != digest:
        raise TransferError("receiver acknowledgement checksum mismatch")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lumen-landrop", description="Token-protected local LAN/Tailnet file drop.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("token", help="generate a secure receiver token")
    serve_parser = sub.add_parser("serve", help="receive files")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--token", required=True)
    serve_parser.add_argument("--output", required=True, type=Path)
    serve_parser.add_argument(
        "--max-upload-bytes",
        type=int,
        default=DEFAULT_MAX_UPLOAD_BYTES,
        help="maximum accepted archive size in bytes (default: 10 GiB)",
    )
    send_parser = sub.add_parser("send", help="send files or directories")
    send_parser.add_argument("--to", required=True)
    send_parser.add_argument("--token", required=True)
    send_parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)
    try:
        if args.command == "token":
            print(secrets.token_urlsafe(32))
            return 0
        if args.command == "serve":
            serve(
                args.host,
                args.port,
                args.token,
                args.output.expanduser().resolve(),
                args.max_upload_bytes,
            )
            return 0
        result = send(args.to, args.token, args.paths)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, TransferError) as exc:
        print(f"lumen-landrop: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
