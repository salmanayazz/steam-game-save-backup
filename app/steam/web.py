"""Steam Cloud remote storage pages (store.steampowered.com/account/remotestorage)."""

import asyncio
import random
from pathlib import Path

import httpx

from app.steam import parser

REMOTE_STORAGE_URL = "https://store.steampowered.com/account/remotestorage/?l=english"
APP_URL = "https://store.steampowered.com/account/remotestorageapp/?appid={app_id}&l=english"
MAX_ATTEMPTS = 3


class SessionExpired(Exception):
    pass


class SteamCloud:
    """Polite client for the remote storage pages: jittered delay between requests, retries."""

    def __init__(self, client: httpx.AsyncClient, delay: tuple[float, float] = (3.0, 5.0)):
        self.client = client
        self.delay = delay

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        for attempt in range(MAX_ATTEMPTS):
            await asyncio.sleep(random.uniform(*self.delay))
            try:
                resp = await self.client.get(url, follow_redirects=False, **kwargs)
                if resp.is_redirect:
                    raise SessionExpired("Steam redirected to login; sign in again")
                resp.raise_for_status()
                return resp
            except httpx.HTTPError:
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(2**attempt)
        raise AssertionError("unreachable")

    async def is_logged_in(self) -> bool:
        resp = await self.client.get(REMOTE_STORAGE_URL, follow_redirects=False)
        return resp.status_code == 200

    async def list_games(self) -> list[parser.Game]:
        return parser.parse_games((await self._get(REMOTE_STORAGE_URL)).text)

    async def list_files(self, app_id: int) -> list[parser.RemoteFile]:
        files: list[parser.RemoteFile] = []
        url: str | None = APP_URL.format(app_id=app_id)
        while url:
            page, url = parser.parse_files((await self._get(url)).text)
            files += page
            if url and "l=english" not in url:
                url += "&l=english"
        return files

    async def download(self, file: parser.RemoteFile, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        for attempt in range(MAX_ATTEMPTS):
            await asyncio.sleep(random.uniform(*self.delay))
            try:
                async with self.client.stream("GET", file.url) as resp:
                    resp.raise_for_status()
                    with open(tmp, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
                tmp.replace(dest)
                return
            except httpx.HTTPError:
                tmp.unlink(missing_ok=True)
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(2**attempt)
