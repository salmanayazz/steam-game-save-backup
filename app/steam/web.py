"""Steam Cloud remote storage pages (store.steampowered.com/account/remotestorage)."""

import httpx

REMOTE_STORAGE_URL = "https://store.steampowered.com/account/remotestorage/?l=english"


async def is_logged_in(client: httpx.AsyncClient) -> bool:
    """Steam redirects to the login page when the session is missing or expired."""
    resp = await client.get(REMOTE_STORAGE_URL, follow_redirects=False)
    return resp.status_code == 200
