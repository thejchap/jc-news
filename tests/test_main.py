import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from tryke import describe, expect, test

from jc_news import (
    PrintingError,
    _find_cups,
    fetch_hn,
    layout_markdown,
    list_printers,
    print_content,
)

with describe("printing"):

    @test("check_cups_installed raises when lp not found")
    def test_check_cups_no_lp():
        with patch("jc_news.shutil.which", return_value=None):
            try:
                _find_cups()
                expect(True, "should have raised").to_equal(False)
            except PrintingError:
                expect(True, "raised PrintingError").to_equal(True)

    @test("check_cups_installed passes when lp found")
    def test_check_cups_found():
        with patch("jc_news.shutil.which", return_value="/usr/bin/lp"):
            result = _find_cups()
            expect(result, "returns lp path").to_equal("/usr/bin/lp")

    @test("list_printers parses lpstat output")
    def test_list_printers():
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "HP_LaserJet accepting requests since Mon 01 Jan 2024\n"
            "Brother_HL accepting requests since Mon 01 Jan 2024\n"
        )
        with (
            patch("jc_news.shutil.which", return_value="/usr/bin/lp"),
            patch("jc_news.subprocess.run", return_value=mock_result) as mock_run,
        ):
            printers = list_printers()
            expect(printers, "printer list").to_equal(["HP_LaserJet", "Brother_HL"])
            mock_run.assert_called_once_with(
                ["/usr/bin/lpstat", "-a"],
                capture_output=True,
                text=True,
                check=True,
            )

    @test("list_printers returns empty list when no printers")
    def test_list_printers_empty():
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with (
            patch("jc_news.shutil.which", return_value="/usr/bin/lp"),
            patch("jc_news.subprocess.run", return_value=mock_result),
        ):
            printers = list_printers()
            expect(printers, "printer list").to_equal([])

    @test("print_content calls lp with correct args")
    def test_print_content():
        mock_result = MagicMock()
        mock_result.returncode = 0
        with (
            patch("jc_news.shutil.which", return_value="/usr/bin/lp"),
            patch("jc_news.subprocess.run", return_value=mock_result) as mock_run,
        ):
            print_content("hello world", "MyPrinter")
            mock_run.assert_called_once_with(
                ["/usr/bin/lp", "-d", "MyPrinter"],
                input="hello world",
                capture_output=True,
                text=True,
                check=True,
            )

    @test("print_content raises on lp failure")
    def test_print_content_failure():
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "lp: error"
        with (
            patch("jc_news.shutil.which", return_value="/usr/bin/lp"),
            patch("jc_news.subprocess.run", return_value=mock_result),
        ):
            try:
                print_content("hello", "BadPrinter")
                expect(True, "should have raised").to_equal(False)
            except PrintingError:
                expect(True, "raised PrintingError").to_equal(True)

    @test("print_content sends PDF bytes via temp file")
    def test_print_content_bytes():
        mock_result = MagicMock()
        mock_result.returncode = 0
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        with (
            patch("jc_news.shutil.which", return_value="/usr/bin/lp"),
            patch("jc_news.subprocess.run", return_value=mock_result) as mock_run,
        ):
            print_content(pdf_bytes, "MyPrinter")
            args = mock_run.call_args[0][0]
            expect(args[0], "lp binary").to_equal("/usr/bin/lp")
            expect(args[1], "-d flag").to_equal("-d")
            expect(args[2], "printer name").to_equal("MyPrinter")
            expect(args[3].endswith(".pdf"), "temp file is .pdf").to_equal(True)


with describe("layout"):

    @test("layout_markdown returns valid PDF bytes")
    def test_layout_returns_pdf():
        result = layout_markdown("# Hello\n\nWorld")
        expect(result[:5], "PDF header").to_equal(b"%PDF-")

    @test("layout_markdown handles rich markdown")
    def test_layout_rich_markdown():
        md = (
            "# Main Heading\n\n"
            "## Subheading\n\n"
            "Some **bold** and *italic* text.\n\n"
            "- item one\n"
            "- item two\n"
            "- item three\n\n"
            "---\n\n"
            "Another paragraph here.\n"
        )
        result = layout_markdown(md)
        expect(result[:5], "PDF header").to_equal(b"%PDF-")
        expect(len(result) > 0, "non-empty PDF").to_equal(True)

    @test("layout_markdown handles empty string")
    def test_layout_empty():
        result = layout_markdown("")
        expect(result[:5], "PDF header").to_equal(b"%PDF-")


def _make_mock_session(story_ids: list[int], items: list[dict]) -> AsyncMock:
    """Build a mock aiohttp session that returns given data."""
    session = AsyncMock()
    responses = []
    # First call is topstories
    top_resp = AsyncMock()
    top_resp.raise_for_status = MagicMock()
    top_resp.json = AsyncMock(return_value=story_ids)
    top_resp.__aenter__ = AsyncMock(return_value=top_resp)
    top_resp.__aexit__ = AsyncMock(return_value=False)
    responses.append(top_resp)
    # Subsequent calls are individual items
    for item in items:
        item_resp = AsyncMock()
        item_resp.raise_for_status = MagicMock()
        item_resp.json = AsyncMock(return_value=item)
        item_resp.__aenter__ = AsyncMock(return_value=item_resp)
        item_resp.__aexit__ = AsyncMock(return_value=False)
        responses.append(item_resp)
    session.get = MagicMock(side_effect=responses)
    return session


with describe("fetch_hn"):

    @test("returns markdown with top posts")
    def test_fetch_hn_basic():
        now = int(time.time())
        items = [
            {
                "id": 1,
                "title": "Post One",
                "url": "https://example.com/1",
                "score": 100,
                "by": "alice",
                "time": now,
            },
            {
                "id": 2,
                "title": "Post Two",
                "url": "https://example.com/2",
                "score": 50,
                "by": "bob",
                "time": now,
            },
        ]
        session = _make_mock_session([1, 2], items)
        md = asyncio.run(fetch_hn(session))
        expect("Post One" in md, "contains first title").to_equal(True)
        expect("Post Two" in md, "contains second title").to_equal(True)
        expect("https://example.com/1" in md, "contains first url").to_equal(True)
        expect("100 points by alice" in md, "contains score/author").to_equal(True)

    @test("skips posts older than 48 hours")
    def test_fetch_hn_filters_old():
        now = int(time.time())
        old = now - 49 * 60 * 60
        items = [
            {
                "id": 1,
                "title": "Old Post",
                "url": "",
                "score": 10,
                "by": "x",
                "time": old,
            },
            {
                "id": 2,
                "title": "New Post",
                "url": "",
                "score": 20,
                "by": "y",
                "time": now,
            },
        ]
        session = _make_mock_session([1, 2], items)
        md = asyncio.run(fetch_hn(session))
        expect("Old Post" in md, "old post excluded").to_equal(False)
        expect("New Post" in md, "new post included").to_equal(True)

    @test("limits to 10 posts")
    def test_fetch_hn_limit_10():
        now = int(time.time())
        ids = list(range(1, 16))
        items = [
            {
                "id": i,
                "title": f"Post {i}",
                "url": "",
                "score": i,
                "by": "u",
                "time": now,
            }
            for i in ids
        ]
        session = _make_mock_session(ids, items)
        md = asyncio.run(fetch_hn(session))
        expect("Post 10" in md, "has 10th post").to_equal(True)
        expect("Post 11" in md, "no 11th post").to_equal(False)
