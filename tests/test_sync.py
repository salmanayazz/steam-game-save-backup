from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import Database
from app.models import Version
from app.steam.parser import Game as RemoteGame
from app.steam.parser import RemoteFile
from app.storage import BlobStore
from app.sync import sync_game

GAME = RemoteGame(app_id=220, name="Half-Life 2", file_count=0, size=0, url="")


class FakeCloud:
    """Stands in for SteamCloud: `remote` maps path -> (content, modified minute)."""

    def __init__(self):
        self.remote: dict[str, tuple[bytes, int]] = {}
        self.downloads: list[str] = []

    async def list_files(self, app_id):
        return [
            RemoteFile("GameInstall", p, len(c) * 1000, datetime(2026, 10, 1, 12, m, tzinfo=UTC), p)
            for p, (c, m) in self.remote.items()
        ]

    async def download(self, file, dest: Path):
        self.downloads.append(file.path)
        dest.write_bytes(self.remote[file.path][0])


@pytest.fixture
async def env(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await db.create_all()
    yield db, FakeCloud(), BlobStore(tmp_path / "blobs", tmp_path / "tmp")
    await db.close()


async def run(env) -> bool:
    db, cloud, blobs = env
    async with db.session() as s:
        created = await sync_game(s, cloud, blobs, GAME)
        await s.commit()
    return created


async def versions(db):
    async with db.session() as s:
        return (await s.scalars(select(Version).order_by(Version.number))).all()


async def test_first_sync_creates_version_and_blobs(env):
    db, cloud, blobs = env
    cloud.remote = {"a.sav": (b"aaa", 1), "b.sav": (b"bbb", 1)}
    assert await run(env)
    (v,) = await versions(db)
    assert v.number == 1 and v.total_size == 6
    assert sorted(cloud.downloads) == ["a.sav", "b.sav"]
    assert len([p for p in blobs.blob_dir.rglob("*") if p.is_file()]) == 2


async def test_unchanged_sync_is_a_noop(env):
    db, cloud, _ = env
    cloud.remote = {"a.sav": (b"aaa", 1)}
    await run(env)
    cloud.downloads.clear()
    assert not await run(env)
    assert cloud.downloads == []
    assert len(await versions(db)) == 1


async def test_changed_file_creates_version_and_only_downloads_that_file(env):
    db, cloud, _ = env
    cloud.remote = {"a.sav": (b"aaa", 1), "b.sav": (b"bbb", 1)}
    await run(env)
    cloud.downloads.clear()
    cloud.remote["a.sav"] = (b"AAAA", 2)
    assert await run(env)
    assert cloud.downloads == ["a.sav"]
    v1, v2 = await versions(db)
    assert (v2.number, v2.total_size) == (2, 7)


async def test_removed_file_creates_version(env):
    db, cloud, _ = env
    cloud.remote = {"a.sav": (b"aaa", 1), "b.sav": (b"bbb", 1)}
    await run(env)
    del cloud.remote["b.sav"]
    assert await run(env)
    assert len(await versions(db)) == 2


async def test_touched_but_identical_content_is_not_a_new_version(env):
    db, cloud, _ = env
    cloud.remote = {"a.sav": (b"aaa", 1)}
    await run(env)
    cloud.remote["a.sav"] = (b"aaa", 2)  # newer timestamp, same bytes
    assert not await run(env)
    assert len(await versions(db)) == 1


async def test_identical_content_across_files_stored_once(env):
    _, cloud, blobs = env
    cloud.remote = {"a.sav": (b"same", 1), "b.sav": (b"same", 1)}
    await run(env)
    assert len([p for p in blobs.blob_dir.rglob("*") if p.is_file()]) == 1
