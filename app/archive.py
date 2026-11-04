"""Build a zip of one stored version."""

import re
import zipfile
from pathlib import Path, PurePosixPath

from app.models import Version
from app.storage import BlobStore


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\|?*\x00-\x1f]', "_", name).strip(" .") or "game"


def entry_name(folder: str, path: str) -> str:
    """Zip entry path from Steam-supplied strings; never absolute or escaping the archive."""
    parts = [p for p in PurePosixPath(f"{folder}/{path}".replace("\\", "/")).parts if p not in ("/", ".", "..")]
    return "/".join(parts)


def build_zip(version: Version, blobs: BlobStore, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in version.files:
            m = f.modified
            info = zipfile.ZipInfo(entry_name(f.folder, f.path), (max(m.year, 1980), m.month, m.day, m.hour, m.minute, m.second))
            info.compress_type = zipfile.ZIP_DEFLATED
            with zf.open(info, "w") as out, open(blobs.path(f.sha256), "rb") as src:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
