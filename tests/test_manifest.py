import httpx
import pytest
import respx

from app.manifest import ManifestStore, parse_manifest

SAMPLE = """\
Half-Life 2:
  files:
    "<base>/hl2/save":
      tags:
        - save
      when:
        - store: steam
    "<base>/hl2/cfg":
      tags:
        - config
    "<winLocalAppData>/HL2/save":
      when:
        - os: windows
        - os: linux
  id:
    steamExtra:
      - 219
  steam:
    id: 220
Other Store Game:
  files:
    "<base>/saves":
      when:
        - store: gog
  steam:
    id: 5
No Steam Game:
  files:
    "<base>/saves": {}
"""


@pytest.fixture
def sample(tmp_path):
    f = tmp_path / "manifest.yaml"
    f.write_text(SAMPLE, encoding="utf-8")
    return f


def test_parse_manifest(sample):
    games = parse_manifest(sample)
    assert set(games) == {220, 219}  # extra id indexed; other-store-only and non-Steam skipped
    locs = {(loc.path, loc.os) for loc in games[220].locations}
    assert locs == {
        ("<base>/hl2/save", ()),
        ("<winLocalAppData>/HL2/save", ("linux", "windows")),
    }
    assert games[219].name == "Half-Life 2"


@respx.mock
async def test_refresh_uses_etag(tmp_path):
    store = ManifestStore(tmp_path / "m", url="https://example.test/manifest.yaml")
    route = respx.get("https://example.test/manifest.yaml").mock(
        return_value=httpx.Response(200, text=SAMPLE, headers={"etag": '"v1"'})
    )
    assert await store.refresh()
    assert store.lookup(220)

    route.mock(return_value=httpx.Response(304))
    assert not await store.refresh()
    assert route.calls.last.request.headers["if-none-match"] == '"v1"'
    assert store.lookup(220)  # still available after a 304

    reloaded = ManifestStore(tmp_path / "m")
    reloaded.load()
    assert reloaded.lookup(220).name == "Half-Life 2"
    assert reloaded.etag == '"v1"'


@respx.mock
async def test_failed_refresh_keeps_previous_index(tmp_path):
    store = ManifestStore(tmp_path / "m", url="https://example.test/manifest.yaml")
    respx.get("https://example.test/manifest.yaml").mock(
        return_value=httpx.Response(200, text=SAMPLE, headers={"etag": '"v1"'})
    )
    await store.refresh()
    respx.get("https://example.test/manifest.yaml").mock(return_value=httpx.Response(500))
    with pytest.raises(httpx.HTTPStatusError):
        await store.refresh()
    assert store.lookup(220)
    assert not list((tmp_path / "m").glob("*.part"))


def test_is_stale(tmp_path):
    store = ManifestStore(tmp_path)
    assert store.is_stale(24)
