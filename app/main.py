import asyncio
import io
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import segno
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.archive import build_zip, safe_name
from app.config import settings
from app.db import Database
from app.manifest import ManifestStore
from app.models import Game, Version
from app.steam import auth, session, web
from app.sync import SyncManager

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["sync_poll_seconds"] = settings.sync_poll_seconds
templates.env.globals["login_poll_seconds"] = settings.login_poll_seconds

# In-flight QR logins, keyed by an unguessable id handed to the browser.
logins: dict[str, auth.QrLogin] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_url)
    await db.create_all()
    app.state.db = db
    app.state.sync = SyncManager(db, settings)
    app.state.manifest = ManifestStore(settings.manifest_dir)
    app.state.manifest.load()
    manifest_task = None
    if settings.manifest_refresh_hours > 0:
        manifest_task = asyncio.create_task(
            app.state.manifest.refresh_forever(settings.manifest_refresh_hours)
        )
    scheduler = None
    if settings.sync_interval_minutes > 0:
        scheduler = asyncio.create_task(app.state.sync.schedule_forever())
    yield
    if scheduler:
        scheduler.cancel()
    if manifest_task:
        manifest_task.cancel()
    await app.state.sync.stop()
    for login in logins.values():
        if login.task:
            login.task.cancel()
    await db.close()


app = FastAPI(lifespan=lifespan)


def qr_svg(url: str) -> str:
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, xmldecl=False, svgns=True)
    return buf.getvalue().decode()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    client = session.load(settings.session_file)
    logged_in = False
    if client:
        async with client:
            try:
                logged_in = await web.SteamCloud(client).is_logged_in()
            except httpx.HTTPError:
                pass
    return templates.TemplateResponse(
        request, "index.html", {"logged_in": logged_in, "sync": request.app.state.sync.status}
    )


@app.post("/sync")
async def start_sync(request: Request):
    request.app.state.sync.start()
    return RedirectResponse("/", status_code=303)


@app.get("/sync/status", response_class=HTMLResponse)
async def sync_status(request: Request):
    return templates.TemplateResponse(
        request, "_sync_status.html", {"sync": request.app.state.sync.status}
    )


@app.get("/games", response_class=HTMLResponse)
async def games(request: Request):
    async with request.app.state.db.session() as db:
        rows = (
            await db.execute(
                select(Game, func.count(Version.id), func.max(Version.created_at))
                .outerjoin(Version)
                .group_by(Game.app_id)
                .order_by(Game.name)
            )
        ).all()
    return templates.TemplateResponse(request, "games.html", {"rows": rows})


@app.get("/games/{app_id}", response_class=HTMLResponse)
async def game_detail(request: Request, app_id: int):
    async with request.app.state.db.session() as db:
        game = await db.scalar(
            select(Game).where(Game.app_id == app_id).options(selectinload(Game.versions).selectinload(Version.files))
        )
    if not game:
        raise HTTPException(404, "Unknown game")
    manifest = request.app.state.manifest
    return templates.TemplateResponse(
        request, "game.html", {"game": game, "entry": manifest.lookup(app_id), "manifest": manifest}
    )


@app.get("/games/{app_id}/versions/{number}/download")
async def download_version(request: Request, app_id: int, number: int, background: BackgroundTasks):
    async with request.app.state.db.session() as db:
        version = await db.scalar(
            select(Version)
            .where(Version.app_id == app_id, Version.number == number)
            .options(selectinload(Version.files), selectinload(Version.game))
        )
    if not version:
        raise HTTPException(404, "Unknown version")
    blobs = request.app.state.sync.blobs
    tmp = blobs.tmp_path()
    try:
        await asyncio.to_thread(build_zip, version, blobs, tmp)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(500, f"Could not build archive: {e}")
    background.add_task(tmp.unlink, missing_ok=True)
    name = f"{safe_name(version.game.name)}-v{number}.zip"
    return FileResponse(tmp, media_type="application/zip", filename=name, background=background)


@app.post("/login", response_class=HTMLResponse)
async def start_login(request: Request):
    client = httpx.AsyncClient(follow_redirects=True)
    try:
        login = await auth.begin_qr(client)
    except (httpx.HTTPError, auth.LoginError) as e:
        await client.aclose()
        raise HTTPException(502, f"Could not start Steam login: {e}")
    login.task = asyncio.create_task(auth.run(login))
    login_id = secrets.token_urlsafe(16)
    logins[login_id] = login
    login_page = {"login_id": login_id, "qr": qr_svg(login.challenge_url), "scanned": False, "error": None}
    return templates.TemplateResponse(request, "login.html", login_page)


@app.get("/login/{login_id}/panel", response_class=HTMLResponse)
async def login_panel(request: Request, login_id: str):
    login = logins.get(login_id)
    if not login:
        raise HTTPException(404, "Unknown login")
    if login.state is auth.State.DONE:
        session.save(login.client, settings.session_file)
        await login.client.aclose()
        del logins[login_id]
        return Response(headers={"HX-Redirect": "/"})
    if login.state is auth.State.FAILED:
        await login.client.aclose()
        del logins[login_id]
        return templates.TemplateResponse(
            request, "_login_panel.html", {"error": login.error, "login_id": None}
        )
    return templates.TemplateResponse(
        request,
        "_login_panel.html",
        {
            "login_id": login_id,
            "qr": qr_svg(login.challenge_url),
            "scanned": login.state is auth.State.SCANNED,
            "error": None,
        },
    )


@app.post("/logout")
async def logout():
    settings.session_file.unlink(missing_ok=True)
    return RedirectResponse("/", status_code=303)
