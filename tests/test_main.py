import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner
from tryke import describe, expect, test

from jc_news import (
    PrintingError,
    _find_cups,
    _sanitize_text,
    fetch_hn,
    layout_markdown,
    list_printers,
    main,
    print_content,
    summarize_hn,
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
                check=False,
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

    @test("list_printers returns empty list when lpstat says no destinations")
    def test_list_printers_no_destinations():
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "lpstat: No destinations added."
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


with describe("sanitize_text"):

    @test("removes emojis")
    def test_sanitize_emojis():
        result = _sanitize_text("hello \U0001f600 world \U0001f680")
        expect(result, "emojis removed").to_equal("hello world")

    @test("collapses multiple spaces")
    def test_sanitize_spaces():
        result = _sanitize_text("hello    world\t\tfoo")
        expect(result, "spaces collapsed").to_equal("hello world foo")

    @test("collapses excessive newlines")
    def test_sanitize_newlines():
        result = _sanitize_text("hello\n\n\n\n\nworld")
        expect(result, "newlines collapsed").to_equal("hello\n\nworld")

    @test("strips leading and trailing whitespace")
    def test_sanitize_strip():
        result = _sanitize_text("  hello  ")
        expect(result, "stripped").to_equal("hello")

    @test("handles plain text unchanged")
    def test_sanitize_plain():
        result = _sanitize_text("Just normal text here.")
        expect(result, "unchanged").to_equal("Just normal text here.")


def _make_resp(
    json_data: object = None,
    text_data: str = "",
    *,
    is_json: bool = True,
) -> AsyncMock:
    """Build a single mock aiohttp response."""
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()
    if is_json:
        resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=text_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_mock_session(responses: list[AsyncMock]) -> AsyncMock:
    """Build a mock aiohttp session from ordered responses."""
    session = AsyncMock()
    session.get = MagicMock(side_effect=responses)
    return session


with describe("fetch_hn"):

    @test("returns markdown with top posts, content, and comments")
    def test_fetch_hn_basic():
        now = int(time.time())
        post = {
            "id": 1,
            "title": "Post One",
            "url": "https://example.com/1",
            "score": 100,
            "by": "alice",
            "time": now,
            "kids": [101, 102],
        }
        comment1 = {
            "id": 101,
            "by": "commenter1",
            "text": "Great article!",
        }
        comment2 = {
            "id": 102,
            "by": "commenter2",
            "text": "Interesting read.",
        }
        article_html = "<html><body><p>Article body text.</p></body></html>"
        responses = [
            _make_resp(json_data=[1]),  # topstories
            _make_resp(json_data=post),  # item/1
            _make_resp(text_data=article_html, is_json=False),  # article
            _make_resp(json_data=comment1),  # comment 101
            _make_resp(json_data=comment2),  # comment 102
        ]
        session = _make_mock_session(responses)
        md = asyncio.run(fetch_hn(session))
        expect("Post One" in md, "contains title").to_equal(True)
        expect("100 points by alice" in md, "contains score").to_equal(True)
        expect("Article body text." in md, "contains article").to_equal(True)
        expect("commenter1" in md, "contains commenter1").to_equal(True)
        expect("Great article!" in md, "contains comment1").to_equal(True)
        expect("Interesting read." in md, "contains comment2").to_equal(True)

    @test("includes self-post text when no url")
    def test_fetch_hn_self_post():
        now = int(time.time())
        post = {
            "id": 1,
            "title": "Ask HN: Something",
            "text": "<p>Self post body here.</p>",
            "score": 30,
            "by": "poster",
            "time": now,
        }
        responses = [
            _make_resp(json_data=[1]),
            _make_resp(json_data=post),
        ]
        session = _make_mock_session(responses)
        md = asyncio.run(fetch_hn(session))
        expect("Self post body here." in md, "contains self text").to_equal(True)

    @test("skips posts older than 48 hours")
    def test_fetch_hn_filters_old():
        now = int(time.time())
        old = now - 49 * 60 * 60
        old_post = {
            "id": 1,
            "title": "Old Post",
            "url": "",
            "score": 10,
            "by": "x",
            "time": old,
        }
        new_post = {
            "id": 2,
            "title": "New Post",
            "url": "",
            "score": 20,
            "by": "y",
            "time": now,
        }
        responses = [
            _make_resp(json_data=[1, 2]),
            _make_resp(json_data=old_post),
            _make_resp(json_data=new_post),
        ]
        session = _make_mock_session(responses)
        md = asyncio.run(fetch_hn(session))
        expect("Old Post" in md, "old post excluded").to_equal(False)
        expect("New Post" in md, "new post included").to_equal(True)

    @test("limits to 20 posts")
    def test_fetch_hn_limit_20():
        now = int(time.time())
        ids = list(range(1, 26))
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
        responses = [_make_resp(json_data=ids)]
        responses.extend(_make_resp(json_data=item) for item in items)
        session = _make_mock_session(responses)
        md = asyncio.run(fetch_hn(session))
        expect("Post 20" in md, "has 20th post").to_equal(True)
        expect("Post 21" in md, "no 21st post").to_equal(False)

    @test("skips deleted and dead comments")
    def test_fetch_hn_skips_bad_comments():
        now = int(time.time())
        post = {
            "id": 1,
            "title": "Post",
            "url": "",
            "score": 5,
            "by": "u",
            "time": now,
            "kids": [101, 102],
        }
        deleted = {"id": 101, "deleted": True}
        alive = {
            "id": 102,
            "by": "alive_user",
            "text": "I am alive",
        }
        responses = [
            _make_resp(json_data=[1]),
            _make_resp(json_data=post),
            _make_resp(json_data=deleted),
            _make_resp(json_data=alive),
        ]
        session = _make_mock_session(responses)
        md = asyncio.run(fetch_hn(session))
        expect("alive_user" in md, "alive comment present").to_equal(True)
        expect("I am alive" in md, "alive text present").to_equal(True)


with describe("run"):

    @test("dry-run markdown echoes summary to terminal")
    def test_run_dry_run():
        fake_md = "## 1. Test Post\n100 points by alice\n"
        with (
            patch("jc_news.summarize_hn", new_callable=AsyncMock, return_value=fake_md),
            patch("jc_news.print_content") as mock_print,
            patch("jc_news.layout_markdown") as mock_layout,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["run", "--dry-run", "markdown"])
            expect(result.exit_code, "exit code").to_equal(0)
            expect(
                "Test Post" in result.output,
                "output contains summary",
            ).to_equal(True)
            mock_layout.assert_not_called()
            mock_print.assert_not_called()

    @test("dry-run=markdown echoes summary to terminal without printing")
    def test_run_dry_run_markdown():
        fake_md = "## 1. Test Post\n100 points by alice\n"
        with (
            patch("jc_news.summarize_hn", new_callable=AsyncMock, return_value=fake_md),
            patch("jc_news.print_content") as mock_print,
            patch("jc_news.layout_markdown") as mock_layout,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["run", "--dry-run=markdown"])
            expect(result.exit_code, "exit code").to_equal(0)
            expect(
                "Test Post" in result.output,
                "output contains summary",
            ).to_equal(True)
            mock_layout.assert_not_called()
            mock_print.assert_not_called()

    @test("dry-run=pdf writes PDF to temp file and prints path")
    def test_run_dry_run_pdf():
        fake_md = "## 1. Test Post\n100 points by alice\n"
        fake_pdf = b"%PDF-1.4 fake"
        with (
            patch("jc_news.summarize_hn", new_callable=AsyncMock, return_value=fake_md),
            patch("jc_news.layout_markdown", return_value=fake_pdf) as mock_layout,
            patch("jc_news.print_content") as mock_print,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["run", "--dry-run=pdf"])
            expect(result.exit_code, "exit code").to_equal(0)
            mock_layout.assert_called_once_with(fake_md)
            mock_print.assert_not_called()
            path = result.output.strip()
            expect(path.endswith(".pdf"), "output is a pdf path").to_equal(True)

    @test("non-dry-run generates PDF and sends to printer")
    def test_run_prints():
        fake_md = "## 1. Test Post\n100 points by alice\n"
        fake_pdf = b"%PDF-1.4 fake"
        with (
            patch("jc_news.summarize_hn", new_callable=AsyncMock, return_value=fake_md),
            patch("jc_news.layout_markdown", return_value=fake_pdf) as mock_layout,
            patch("jc_news.print_content") as mock_print,
            patch("jc_news._find_default_printer", return_value="TestPrinter"),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["run"])
            expect(result.exit_code, "exit code").to_equal(0)
            mock_layout.assert_called_once_with(fake_md)
            mock_print.assert_called_once_with(fake_pdf, "TestPrinter")
            expect(
                "Sent to printer 'TestPrinter'." in result.output,
                "confirms print",
            ).to_equal(True)

    @test("explicit --printer skips default lookup")
    def test_run_explicit_printer():
        fake_md = "## Summary\n"
        fake_pdf = b"%PDF-1.4 fake"
        with (
            patch("jc_news.summarize_hn", new_callable=AsyncMock, return_value=fake_md),
            patch("jc_news.layout_markdown", return_value=fake_pdf),
            patch("jc_news.print_content") as mock_print,
            patch("jc_news._find_default_printer") as mock_default,
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["run", "--printer", "MyPrinter"])
            expect(result.exit_code, "exit code").to_equal(0)
            mock_default.assert_not_called()
            mock_print.assert_called_once_with(fake_pdf, "MyPrinter")

    @test("PrintingError becomes ClickException")
    def test_run_printing_error():
        fake_md = "## Summary\n"
        with (
            patch("jc_news.summarize_hn", new_callable=AsyncMock, return_value=fake_md),
            patch(
                "jc_news._find_default_printer",
                side_effect=PrintingError("no printers found."),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(main, ["run"])
            expect(result.exit_code != 0, "nonzero exit").to_equal(True)
            expect(
                "no printers found." in result.output,
                "error message shown",
            ).to_equal(True)


with describe("summarize_hn"):

    @test("fetches posts, summarizes each, and assembles markdown")
    def test_summarize_hn_basic():
        now = int(time.time())
        posts = [
            {
                "id": 1,
                "title": "Post One",
                "url": "",
                "score": 10,
                "by": "a",
                "time": now,
            },
            {
                "id": 2,
                "title": "Post Two",
                "url": "",
                "score": 20,
                "by": "b",
                "time": now,
            },
        ]
        with (
            patch(
                "jc_news._fetch_hn_posts",
                new_callable=AsyncMock,
                return_value=posts,
            ),
            patch(
                "jc_news._format_post",
                new_callable=AsyncMock,
                side_effect=["md1", "md2"],
            ),
            patch(
                "jc_news._summarize_post",
                new_callable=AsyncMock,
                side_effect=["Summary one.", "Summary two."],
            ),
        ):
            session = AsyncMock()
            result = asyncio.run(summarize_hn(session))
            expect("## 1. Post One" in result, "has first heading").to_equal(True)
            expect("## 2. Post Two" in result, "has second heading").to_equal(True)
            expect("Summary one." in result, "has first summary").to_equal(True)
            expect("Summary two." in result, "has second summary").to_equal(True)
            expect("---" in result, "sections separated by hr").to_equal(True)
