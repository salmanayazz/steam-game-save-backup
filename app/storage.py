"""Content-addressed blob store: each distinct file is kept once under blobs/<aa>/<sha256>."""

import asyncio
import hashlib
import uuid
from pathlib import Path


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class BlobStore:
    def __init__(self, blob_dir: Path, tmp_dir: Path):
        self.blob_dir = blob_dir
        self.tmp_dir = tmp_dir

    def tmp_path(self) -> Path:
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        return self.tmp_dir / uuid.uuid4().hex

    def path(self, sha256: str) -> Path:
        return self.blob_dir / sha256[:2] / sha256

    async def adopt(self, tmp: Path) -> tuple[str, int]:
        """Hash a downloaded temp file and move it into the store. Returns (sha256, size)."""
        sha = await asyncio.to_thread(_hash_file, tmp)
        size = tmp.stat().st_size
        dest = self.path(sha)
        if dest.exists():
            tmp.unlink()
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(dest)
        return sha, size
