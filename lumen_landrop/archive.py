"""Archive construction and validation for Lumen LAN Drop."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import tarfile
from typing import BinaryIO, Dict, Iterator, List, Tuple

CHUNK_SIZE = 1024 * 1024


class TransferError(ValueError):
    """An unsafe or invalid transfer."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_source(path: Path) -> None:
    if path.is_symlink():
        raise TransferError(f"refusing symlink: {path}")
    if not path.exists():
        raise TransferError(f"path does not exist: {path}")
    if not (path.is_file() or path.is_dir()):
        raise TransferError(f"not a regular file or directory: {path}")


def _source_members(paths: List[Path]) -> Iterator[Tuple[Path, str, bool]]:
    top_names = set()
    for source in paths:
        _validate_source(source)
        root_name = source.name
        if not root_name or root_name in {".", ".."} or root_name in top_names:
            raise TransferError(f"duplicate or invalid top-level name: {root_name!r}")
        top_names.add(root_name)
        if source.is_file():
            yield source, root_name, False
            continue
        yield source, root_name, True
        for current, dirnames, filenames in os.walk(source, followlinks=False):
            current_path = Path(current)
            for name in list(dirnames):
                child = current_path / name
                if child.is_symlink():
                    raise TransferError(f"refusing symlink: {child}")
            for name in filenames:
                child = current_path / name
                if child.is_symlink():
                    raise TransferError(f"refusing symlink: {child}")
                if not child.is_file():
                    raise TransferError(f"not a regular file: {child}")
            relative = current_path.relative_to(source)
            if relative != Path("."):
                yield current_path, (PurePosixPath(root_name) / relative.as_posix()).as_posix(), True
            for name in filenames:
                child = current_path / name
                archive_name = (PurePosixPath(root_name) / child.relative_to(source).as_posix()).as_posix()
                yield child, archive_name, False


def build_archive(paths: List[Path], destination: Path) -> Dict[str, str]:
    """Create a regular-file-only tar archive and return path->digest metadata."""
    if not paths:
        raise TransferError("at least one input path is required")
    files: Dict[str, str] = {}
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for source, archive_name, is_directory in _source_members(paths):
            if is_directory:
                info = tarfile.TarInfo(archive_name.rstrip("/") + "/")
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = int(source.stat().st_mtime)
                archive.addfile(info)
                continue
            info = tarfile.TarInfo(archive_name)
            stat = source.stat()
            info.size = stat.st_size
            info.mode = 0o644
            info.mtime = int(stat.st_mtime)
            with source.open("rb") as handle:
                archive.addfile(info, handle)
            files[archive_name] = sha256_file(source)
    return files


def safe_member_name(name: str) -> Path:
    """Turn a tar member name into a safe relative local path."""
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise TransferError(f"unsafe archive path: {name!r}")
    if "\\" in name:
        raise TransferError(f"unsafe archive path: {name!r}")
    return Path(*pure.parts)


def extract_archive_safely(stream: BinaryIO, staging: Path) -> Tuple[List[Dict[str, object]], str]:
    """Extract only regular files and directories into an empty staging directory."""
    files: List[Dict[str, object]] = []
    seen = set()
    with tarfile.open(fileobj=stream, mode="r|") as archive:
        for member in archive:
            relative = safe_member_name(member.name.rstrip("/"))
            key = relative.as_posix()
            if key in seen:
                raise TransferError(f"duplicate archive path: {key}")
            seen.add(key)
            target = staging / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=False)
                continue
            if not member.isreg():
                raise TransferError(f"unsupported archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise TransferError(f"cannot read archive member: {member.name}")
            digest = hashlib.sha256()
            written = 0
            with target.open("xb") as output:
                while True:
                    chunk = source.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
            if written != member.size:
                raise TransferError(f"truncated archive member: {member.name}")
            files.append({"path": key, "size": written, "sha256": digest.hexdigest()})
    return files, ""
