"""Parse the Steam Cloud remote storage pages.

Index page rows:     name | file count | total size | "Show Files" link (?appid=N)
Game file page rows: root folder | path | size | date written | "Download" link
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from lxml import html

# Steam renders "Date Written" in Pacific time (same assumption as the reference project).
STEAM_TZ = ZoneInfo("America/Los_Angeles")
UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class Game:
    app_id: int
    name: str
    file_count: int
    size: int
    url: str


@dataclass(frozen=True)
class RemoteFile:
    folder: str
    path: str
    size: int
    modified: datetime  # UTC
    url: str


def _rows(content: str) -> tuple[html.HtmlElement, list[html.HtmlElement]]:
    tree = html.fromstring(content)
    if not tree.xpath('//*[@id="main_content"]'):
        raise ParseError("remote storage content not found (logged out or layout changed)")
    rows = tree.xpath('//*[@id="main_content"]//table//tbody//tr')
    return tree, [r for r in rows if r.xpath("./td")]


def parse_size(text: str) -> int:
    m = re.fullmatch(r"([\d.,]+)\s*([KMG]?B)", text.strip())
    if not m:
        raise ParseError(f"unrecognised size: {text!r}")
    return int(float(m.group(1).replace(",", "")) * UNITS[m.group(2)])


def parse_date(text: str, now: datetime | None = None) -> datetime:
    """Parse '23 Oct @ 8:10pm', '23 Oct, 2023 @ 8:10pm' or the month-first equivalents."""
    now = now or datetime.now(UTC)
    m = re.fullmatch(
        r"(?:(\d{1,2}) ([A-Za-z]{3})|([A-Za-z]{3}) (\d{1,2}))(?:,? (\d{4}))? @ (\d{1,2}:\d{2}[ap]m)",
        text.strip(),
    )
    if not m:
        raise ParseError(f"unrecognised date: {text!r}")
    day = m.group(1) or m.group(4)
    mon = m.group(2) or m.group(3)
    year, clock = m.group(5), m.group(6)

    def build(y: int) -> datetime:
        naive = datetime.strptime(f"{day} {mon} {y} {clock}", "%d %b %Y %I:%M%p")
        return naive.replace(tzinfo=STEAM_TZ).astimezone(UTC)

    if year:
        return build(int(year))
    # No year shown means within the last year: roll back if this year's date is in the future.
    # (Leap-day files from a non-leap current year fall through to the previous year.)
    for y in (now.year, now.year - 1, now.year - 2):
        try:
            dt = build(y)
        except ValueError:
            continue
        if dt <= now:
            return dt
    raise ParseError(f"cannot resolve year for date: {text!r}")


def _app_id(url: str) -> int:
    try:
        return int(parse_qs(urlparse(url).query)["appid"][0])
    except (KeyError, ValueError, IndexError):
        raise ParseError(f"no appid in link: {url!r}") from None


def parse_games(content: str) -> list[Game]:
    _, rows = _rows(content)
    games = []
    for tr in rows:
        cols = tr.xpath("./td")
        links = tr.xpath(".//a/@href")
        if len(cols) < 4 or not links:
            continue
        games.append(
            Game(
                app_id=_app_id(links[0]),
                name=cols[0].text_content().strip(),
                file_count=int(cols[1].text_content().strip()),
                size=parse_size(cols[2].text_content()),
                url=links[0],
            )
        )
    return games


def parse_files(content: str, now: datetime | None = None) -> tuple[list[RemoteFile], str | None]:
    """Returns the files on this page and the link to the next page, if any."""
    tree, rows = _rows(content)
    files = []
    for tr in rows:
        cols = tr.xpath("./td")
        links = tr.xpath(".//a/@href")
        if len(cols) < 5 or not links:
            continue
        files.append(
            RemoteFile(
                folder=cols[0].text_content().strip(),
                path=cols[1].text_content().strip(),
                size=parse_size(cols[2].text_content()),
                modified=parse_date(cols[3].text_content(), now),
                url=links[0],
            )
        )
    nxt = tree.xpath('//a[contains(normalize-space(text()), "next")]/@href')
    return files, (nxt[0] if nxt else None)
