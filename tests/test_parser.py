from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.steam import parser

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def test_parse_games():
    games = parser.parse_games((FIXTURES / "index.html").read_text(encoding="utf-8"))
    assert len(games) == 5
    hl2 = next(g for g in games if g.app_id == 220)
    assert hl2.name == "Half-Life 2"
    assert hl2.file_count == 52
    assert hl2.size == int(7.95 * 1024**2)


def test_parse_files_and_next_page():
    files, nxt = parser.parse_files((FIXTURES / "app.html").read_text(encoding="utf-8"), NOW)
    assert len(files) == 4
    assert files[0].folder == "GameInstall"
    assert files[0].path == "hl2/save/autosave.sav"
    assert files[0].url.startswith("https://cdn.steamusercontent.com/filedownload/")
    assert nxt and "appid=220" in nxt and "index=50" in nxt


def test_logged_out_page_raises():
    with pytest.raises(parser.ParseError):
        parser.parse_games("<html><body>Sign in</body></html>")


@pytest.mark.parametrize(
    "text,expected",
    [
        ("23 Oct @ 8:10pm", datetime(2026, 10, 24, 3, 10, tzinfo=UTC)),
        ("Oct 23 @ 8:10pm", datetime(2026, 10, 24, 3, 10, tzinfo=UTC)),
        ("23 Oct, 2023 @ 8:10pm", datetime(2023, 10, 24, 3, 10, tzinfo=UTC)),
        ("12 Dec @ 9:00am", datetime(2025, 12, 12, 17, 0, tzinfo=UTC)),  # future this year -> last year
    ],
)
def test_parse_date(text, expected):
    assert parser.parse_date(text, NOW) == expected


def test_parse_date_rejects_garbage():
    with pytest.raises(parser.ParseError):
        parser.parse_date("yesterday", NOW)


def test_parse_size():
    assert parser.parse_size("39 B") == 39
    assert parser.parse_size("1.93 KB") == int(1.93 * 1024)
