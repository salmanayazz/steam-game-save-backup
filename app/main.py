import asyncio
import io
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import segno
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.steam import auth, session, web
from app.steam.parser import ParseError

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=BASE / "templates")

# In-flight QR logins, keyed by an unguessable id handed to the browser.
logins: dict[str, auth.QrLogin] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for login in logins.values():
        if login.task:
            login.task.cancel()


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
    return templates.TemplateResponse(request, "index.html", {"logged_in": logged_in})


@app.get("/games", response_class=HTMLResponse)
async def games(request: Request):
    client = session.load(settings.session_file)
    if not client:
        return RedirectResponse("/", status_code=303)
    async with client:
        try:
            found = await web.SteamCloud(client).list_games()
        except web.SessionExpired:
            return RedirectResponse("/", status_code=303)
        except (httpx.HTTPError, ParseError) as e:
            raise HTTPException(502, f"Could not read Steam Cloud: {e}")
    return templates.TemplateResponse(request, "games.html", {"games": found})


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
