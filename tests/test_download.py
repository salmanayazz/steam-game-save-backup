import io
import zipfile
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import main
from app.archive import entry_name
from app.models import Game, Version, VersionFile


def test_entry_name_blocks_traversal():
    assert entry_name("GameInstall", "../../etc/passwd") == "GameInstall/etc/passwd"
    assert entry_name("GameInstall", "/abs/x.sav") == "GameInstall/abs/x.sav"
    assert entry_name("GameInstall", "a" + chr(92) + "b.sav") == "GameInstall/a/b.sav"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "manifest_refresh_hours", 0)
    monkeypatch.setattr(main.settings, "sync_interval_minutes", 0)
    with TestClient(main.app) as c:
        yield c


async def seed(app):
    blobs = app.state.sync.blobs
    tmp = blobs.tmp_path()
    tmp.write_bytes(b"save-data")
    sha, size = await blobs.adopt(tmp)
    async with app.state.db.session() as s:
        s.add(Game(app_id=1, name="Half: Life/2"))
        s.add(
            Version(
                app_id=1, number=1, created_at=datetime.now(UTC), total_size=size,
                files=[VersionFile(folder="GameInstall", path="s/a.sav", size=size, listed_size=size,
                                   modified=datetime(2026, 10, 1, tzinfo=UTC), sha256=sha)],
            )
        )
        await s.commit()


def test_download_zip(client):
    client.portal.call(seed, main.app)
    r = client.get("/games/1/versions/1/download")
    assert r.status_code == 200
    assert "Half_%20Life_2-v1.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.read("GameInstall/s/a.sav") == b"save-data"


def test_download_unknown_version_404(client):
    assert client.get("/games/1/versions/9/download").status_code == 404
