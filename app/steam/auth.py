"""Steam web login via QR code (approved from the Steam mobile app).

Flow: BeginAuthSessionViaQR -> poll PollAuthSessionStatus until a refresh token
is issued -> finalizelogin to turn it into web cookies.
"""

import asyncio
import enum
from dataclasses import dataclass, field

import httpx

API = "https://api.steampowered.com/IAuthenticationService"
FINALIZE_URL = "https://login.steampowered.com/jwt/finalizelogin"
COMMUNITY_URL = "https://steamcommunity.com"
HEADERS = {"Origin": COMMUNITY_URL, "Referer": f"{COMMUNITY_URL}/"}
PLATFORM_WEB_BROWSER = 2
LOGIN_TIMEOUT = 5 * 60


class LoginError(Exception):
    pass


class State(enum.Enum):
    PENDING = "pending"
    SCANNED = "scanned"
    DONE = "done"
    FAILED = "failed"


@dataclass
class QrLogin:
    client: httpx.AsyncClient
    client_id: str = ""
    request_id: str = ""
    challenge_url: str = ""
    interval: float = 5.0
    state: State = State.PENDING
    error: str = ""
    task: asyncio.Task | None = field(default=None, repr=False)


async def _api(client: httpx.AsyncClient, method: str, data: dict) -> dict:
    resp = await client.post(f"{API}/{method}/v1", data=data, headers=HEADERS)
    resp.raise_for_status()
    return resp.json().get("response", {})


async def begin_qr(client: httpx.AsyncClient) -> QrLogin:
    r = await _api(
        client,
        "BeginAuthSessionViaQR",
        {"device_friendly_name": "Steam Save Backup", "platform_type": PLATFORM_WEB_BROWSER},
    )
    if "challenge_url" not in r:
        raise LoginError("Steam did not return a QR challenge")
    return QrLogin(
        client=client,
        client_id=r["client_id"],
        request_id=r["request_id"],
        challenge_url=r["challenge_url"],
        interval=float(r.get("interval", 5)),
    )


async def _poll_until_done(login: QrLogin) -> str:
    """Poll Steam until the QR is approved. Returns the refresh token."""
    while True:
        await asyncio.sleep(login.interval)
        r = await _api(
            login.client,
            "PollAuthSessionStatus",
            {"client_id": login.client_id, "request_id": login.request_id},
        )
        # Steam rotates the QR challenge periodically.
        if r.get("new_client_id"):
            login.client_id = r["new_client_id"]
        if r.get("new_challenge_url"):
            login.challenge_url = r["new_challenge_url"]
        if r.get("had_remote_interaction"):
            login.state = State.SCANNED
        if r.get("refresh_token"):
            return r["refresh_token"]


async def finalize(client: httpx.AsyncClient, refresh_token: str) -> None:
    """Exchange a refresh token for web cookies on every Steam domain."""
    await client.get(COMMUNITY_URL)  # sets the sessionid cookie
    sessionid = client.cookies.get("sessionid", domain="steamcommunity.com")
    if not sessionid:
        raise LoginError("No sessionid cookie from Steam")
    resp = await client.post(
        FINALIZE_URL,
        files={
            "nonce": (None, refresh_token),
            "sessionid": (None, sessionid),
            "redirect_uri": (None, f"{COMMUNITY_URL}/login/home/?goto="),
        },
        headers=HEADERS,
    )
    resp.raise_for_status()
    body = resp.json()
    if "transfer_info" not in body:
        raise LoginError(f"finalizelogin failed: {body}")
    for t in body["transfer_info"]:
        fields = {k: (None, str(v)) for k, v in t["params"].items()}
        fields["steamID"] = (None, str(body["steamID"]))
        r = await client.post(t["url"], files=fields, headers=HEADERS)
        r.raise_for_status()


async def run(login: QrLogin) -> None:
    """Background task: drive the login to completion, recording the outcome."""
    try:
        token = await asyncio.wait_for(_poll_until_done(login), LOGIN_TIMEOUT)
        await finalize(login.client, token)
        login.state = State.DONE
    except TimeoutError:
        login.state, login.error = State.FAILED, "QR code expired. Try again."
    except (httpx.HTTPError, LoginError) as e:
        login.state, login.error = State.FAILED, str(e)
