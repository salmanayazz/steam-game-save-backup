"""Back up Steam Cloud saves: one new Version per game whenever its file contents change."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db import Database
from app.models import Game, Version, VersionFile
from app.steam import session
from app.steam.parser import Game as RemoteGame
from app.steam.parser import ParseError
from app.steam.web import SessionExpired, SteamCloud
from app.storage import BlobStore

log = logging.getLogger(__name__)


@dataclass
class SyncStatus:
    running: bool = False
    current: str = ""
    done: int = 0
    total: int = 0
    new_versions: int = 0
    errors: int = 0
    error: str = ""  # fatal error that stopped the run
    finished_at: datetime | None = None


async def sync_game(db: AsyncSession, cloud: SteamCloud, blobs: BlobStore, remote: RemoteGame) -> bool:
    """Sync one game. Returns True if a new version was recorded."""
    files = await cloud.list_files(remote.app_id)

    game = await db.get(Game, remote.app_id)
    if game is None:
        game = Game(app_id=remote.app_id, name=remote.name)
        db.add(game)
    game.name = remote.name
    game.last_synced_at = datetime.now(UTC)

    latest = await db.scalar(
        select(Version)
        .where(Version.app_id == remote.app_id)
        .options(selectinload(Version.files))
        .order_by(Version.number.desc())
        .limit(1)
    )
    prev = {(f.folder, f.path): f for f in latest.files} if latest else {}

    entries: list[VersionFile] = []
    for rf in files:
        old = prev.get((rf.folder, rf.path))
        if old and old.modified == rf.modified and old.listed_size == rf.size:
            sha, size = old.sha256, old.size  # unchanged: no download needed
        else:
            tmp = blobs.tmp_path()
            try:
                await cloud.download(rf, tmp)
                sha, size = await blobs.adopt(tmp)
            finally:
                tmp.unlink(missing_ok=True)
        entries.append(
            VersionFile(
                folder=rf.folder,
                path=rf.path,
                size=size,
                listed_size=rf.size,
                modified=rf.modified,
                sha256=sha,
            )
        )

    def fingerprint(items) -> set[tuple[str, str, str]]:
        return {(f.folder, f.path, f.sha256) for f in items}

    if latest and fingerprint(entries) == fingerprint(latest.files):
        return False
    if not entries and not latest:
        return False  # nothing to back up yet

    db.add(
        Version(
            app_id=remote.app_id,
            number=(latest.number + 1) if latest else 1,
            created_at=datetime.now(UTC),
            total_size=sum(e.size for e in entries),
            files=entries,
        )
    )
    return True


class SyncManager:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self.blobs = BlobStore(settings.blob_dir, settings.tmp_dir)
        self.status = SyncStatus()
        self._task: asyncio.Task | None = None

    def start(self) -> bool:
        """Kick off a sync in the background. False if one is already running."""
        if self.status.running:
            return False
        self.status = SyncStatus(running=True)
        self._task = asyncio.create_task(self._run())
        return True

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        st = self.status
        try:
            client = session.load(self.settings.session_file)
            if not client:
                st.error = "Not signed in to Steam."
                return
            async with client:
                delay = (self.settings.request_delay_min, self.settings.request_delay_max)
                download_delay = (self.settings.download_delay_min, self.settings.download_delay_max)
                cloud = SteamCloud(client, delay=delay, download_delay=download_delay)
                games = await cloud.list_games()
                st.total = len(games)
                for g in games:
                    st.current = g.name
                    try:
                        async with self.db.session() as s:
                            if await sync_game(s, cloud, self.blobs, g):
                                st.new_versions += 1
                            await s.commit()
                    except (httpx.HTTPError, ParseError, OSError) as e:
                        log.warning("sync failed for %s (%s): %s", g.name, g.app_id, e)
                        st.errors += 1
                    st.done += 1
        except SessionExpired:
            st.error = "Steam session expired. Sign in again."
        except (httpx.HTTPError, ParseError) as e:
            st.error = f"Could not read Steam Cloud: {e}"
        except Exception:
            log.exception("sync crashed")
            st.error = "Sync crashed; see server log."
        finally:
            st.running = False
            st.current = ""
            st.finished_at = datetime.now(UTC)

    async def schedule_forever(self) -> None:
        interval = self.settings.sync_interval_minutes * 60
        while True:
            await asyncio.sleep(interval)
            self.start()
