import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import main
from app.steam import auth


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "manifest_refresh_hours", 0)
    monkeypatch.setattr(main.settings, "sync_interval_minutes", 0)
    with TestClient(main.app) as c:
        yield c


@respx.mock
def test_begin_qr_parses_challenge():
    respx.post(f"{auth.API}/BeginAuthSessionViaQR/v1").mock(
        return_value=httpx.Response(
            200,
            json={"response": {"client_id": "1", "request_id": "cg==", "challenge_url": "https://s.team/q/1/1", "interval": 5}},
        )
    )

    async def go():
        async with httpx.AsyncClient() as c:
            return await auth.begin_qr(c)

    import asyncio

    login = asyncio.run(go())
    assert login.challenge_url == "https://s.team/q/1/1"
    assert login.interval == 5.0


def test_index_logged_out(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Sign in with QR code" in r.text


def test_unknown_login_panel_404(client):
    assert client.get("/login/nope/panel").status_code == 404
