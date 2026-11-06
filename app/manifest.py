"""Typical save locations per Steam game, from the Ludusavi manifest (compiled from PCGamingWiki).

The ~18 MB YAML is parsed one game at a time and reduced to a small JSON index keyed by
Steam app ID. Refreshes use a conditional GET (ETag), so an unchanged manifest costs one tiny request.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

log = logging.getLogger(__name__)

MANIFEST_URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/master/data/manifest.yaml"
MAX_BYTES = 100 * 1024 * 1024
HEADERS = {"User-Agent": "steam-game-save-backup"}
Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


@dataclass(frozen=True)
class Location:
    path: str
    os: tuple[str, ...]  # empty means any OS


@dataclass(frozen=True)
class Entry:
    name: str
    locations: list[Location]


def _locations(files: dict | None) -> list[Location]:
    found: dict[tuple[str, tuple[str, ...]], Location] = {}
    for path, spec in (files or {}).items():
        spec = spec or {}
        tags = spec.get("tags")
        if tags and "save" not in tags:
            continue  # config-only and similar
        oses: set[str] = set()
        for cond in spec.get("when") or [{}]:
            if cond.get("store") not in (None, "steam"):
                continue  # applies only to other stores
            oses.add(cond.get("os") or "")
        if not oses:
            continue
        os_ = () if "" in oses else tuple(sorted(oses))
        found[(path, os_)] = Location(path, os_)
    return list(found.values())


def parse_manifest(path: Path) -> dict[int, Entry]:
    """Index the manifest by Steam app ID (including `steamExtra` IDs)."""
    games: dict[int, Entry] = {}

    def flush(chunk: list[str]) -> None:
        text = "".join(chunk)
        if "steam" not in text:
            return
        try:
            data = yaml.load(text, Loader=Loader)
        except yaml.YAMLError as e:
            log.warning("skipping unparsable manifest entry: %s", e)
            return
        for name, body in (data or {}).items():
            if not isinstance(body, dict):
                continue
            ids = []
            steam = body.get("steam")
            if isinstance(steam, dict) and isinstance(steam.get("id"), int):
                ids.append(steam["id"])
            extra = (body.get("id") or {}).get("steamExtra") or []
            ids += [i for i in extra if isinstance(i, int)]
            locs = _locations(body.get("files"))
            if ids and locs:
                for i in ids:
                    games.setdefault(i, Entry(str(name), locs))

    chunk: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            # A non-indented, non-comment line starts the next game's entry.
            if chunk and line[:1] not in ("", " ", "\t", "\n", "\r", "#"):
                flush(chunk)
                chunk = []
            chunk.append(line)
    flush(chunk)
    return games


class ManifestStore:
    def __init__(self, directory: Path, url: str = MANIFEST_URL):
        self.dir = directory
        self.url = url
        self.games: dict[int, Entry] = {}
        self.etag: str | None = None
        self.checked_at: datetime | None = None
        self.updated_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def index_file(self) -> Path:
        return self.dir / "index.json"

    def load(self) -> None:
        """Load the saved index, if any."""
        try:
            raw = json.loads(self.index_file.read_text(encoding="utf-8"))
            self.etag = raw["etag"]
            self.checked_at = datetime.fromisoformat(raw["checked_at"])
            self.updated_at = datetime.fromisoformat(raw["updated_at"])
            self.games = {
                int(k): Entry(v["name"], [Location(loc["path"], tuple(loc["os"])) for loc in v["locations"]])
                for k, v in raw["games"].items()
            }
        except (OSError, KeyError, ValueError):
            self.games = {}

    def _save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        raw = {
            "etag": self.etag,
            "checked_at": self.checked_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "games": {
                str(k): {
                    "name": e.name,
                    "locations": [{"path": loc.path, "os": list(loc.os)} for loc in e.locations],
                }
                for k, e in self.games.items()
            },
        }
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        os.replace(tmp, self.index_file)

    def lookup(self, app_id: int) -> Entry | None:
        return self.games.get(app_id)

    def is_stale(self, max_age_hours: float) -> bool:
        if not self.checked_at:
            return True
        return (datetime.now(UTC) - self.checked_at).total_seconds() > max_age_hours * 3600

    async def refresh(self, client: httpx.AsyncClient | None = None) -> bool:
        """Download the manifest if it changed. Returns True if the index was updated."""
        async with self._lock:
            own = client is None
            client = client or httpx.AsyncClient(follow_redirects=True, timeout=60)
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / "manifest.yaml.part"
            try:
                headers = dict(HEADERS)
                if self.etag and self.games:
                    headers["If-None-Match"] = self.etag
                async with client.stream("GET", self.url, headers=headers) as resp:
                    if resp.status_code == 304:
                        self.checked_at = datetime.now(UTC)
                        self._save()
                        return False
                    resp.raise_for_status()
                    size = 0
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise ValueError("manifest larger than expected")
                            f.write(chunk)
                    etag = resp.headers.get("etag")
                games = await asyncio.to_thread(parse_manifest, tmp)
                if not games:
                    raise ValueError("manifest parsed to zero games; keeping previous index")
                now = datetime.now(UTC)
                self.games, self.etag = games, etag
                self.checked_at = self.updated_at = now
                self._save()
                return True
            finally:
                tmp.unlink(missing_ok=True)
                if own:
                    await client.aclose()

    async def refresh_forever(self, interval_hours: float) -> None:
        """Refresh now if stale, then once per interval. Failures keep the previous index."""
        while True:
            if self.is_stale(interval_hours):
                try:
                    changed = await self.refresh()
                    log.info("save-location manifest %s", "updated" if changed else "unchanged")
                except (httpx.HTTPError, ValueError, OSError) as e:
                    log.warning("manifest refresh failed: %s", e)
            await asyncio.sleep(interval_hours * 3600)
