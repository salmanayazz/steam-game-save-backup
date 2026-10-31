"""Persist the logged-in Steam web session (cookies only, never the password)."""

import json
import os
from pathlib import Path

import httpx


def save(client: httpx.AsyncClient, path: Path) -> None:
    cookies = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
        for c in client.cookies.jar
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cookies, f)
    os.replace(tmp, path)


def load(path: Path) -> httpx.AsyncClient | None:
    if not path.exists():
        return None
    client = httpx.AsyncClient(follow_redirects=True)
    for c in json.loads(path.read_text()):
        client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
    return client
