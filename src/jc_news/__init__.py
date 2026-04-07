"""jc-news: A CLI tool to fetch top Hacker News posts, summarize them and print them."""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiohttp
import anthropic
import click
import mistune
import pdfun
from bs4 import BeautifulSoup
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

_HN_API = "https://hacker-news.firebaseio.com/v0"
_HN_TOP_N = 20
_HN_TOP_COMMENTS = 5
_ARTICLE_MAX_CHARS = 2000
_SUMMARIZE_CONCURRENCY = 5

_EMOJI_CATEGORIES = {"So", "Sk"}
_EMOJI_FORMAT_CHARS = frozenset("\u200d\ufe0f")
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _strip_emojis(text: str) -> str:
    """Remove emoji and emoji-related format characters via unicode category."""
    return "".join(
        c
        for c in text
        if c not in _EMOJI_FORMAT_CHARS
        and unicodedata.category(c) not in _EMOJI_CATEGORIES
    )


def _sanitize_text(text: str) -> str:
    """Remove emojis and clean up whitespace."""
    text = _strip_emojis(text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def _extract_domain(url: str) -> str:
    """Extract the domain from a URL, stripping www. prefix."""
    host = urlparse(url).hostname or ""
    return host.removeprefix("www.")


_LAYOUT_CSS = """\
@page {
    size: letter;
    margin: 0.25in 0.3in;
}
body {
    column-count: 2;
    column-gap: 0.2in;
    column-rule: 1px solid #999;
    font-family: 'Courier New', Courier, monospace;
    font-size: 7.5pt;
    line-height: 1.1;
    margin: 0;
    padding: 0;
}
h1 { display: none; }
h2 { font-size: 8.5pt; font-weight: bold; margin: 0.05em 0; }
h3 { font-size: 8pt; font-weight: bold; margin: 0.05em 0; }
ul, ol { margin: 0.1em 0; padding-left: 1em; }
li { margin: 0; }
hr { border: none; border-top: 0.5px solid #999; margin: 0.15em 0; }
p { margin: 0.1em 0; }
.date { column-span: all; font-size: 6pt; text-align: right; margin: 0; color: #666; }
"""

_LAYOUT_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{css}</style></head>
<body>{content}</body>
</html>
"""


def _pdf_page_count(pdf_bytes: bytes) -> int:
    """Count pages in a PDF by matching /Type /Page (not /Pages) markers."""
    return len(re.findall(rb"/Type\s*/Page(?!\w)", pdf_bytes))


def layout_markdown(content: str) -> bytes:
    """Convert markdown to newspaper-style PDF bytes for 8.5x11 paper.

    Truncates articles so the output fits on a single page.
    """
    # split on horizontal rules that separate articles
    sections = re.split(r"(?m)^---$", content)
    header = sections[0] if sections else ""
    articles = sections[1:] if len(sections) > 1 else []

    now = datetime.now(tz=UTC)
    date_html = f'<div class="date">{now.strftime("%B")} {now.day}, {now.year}</div>'

    while True:
        md = ("---".join([header, *articles])).strip()
        html_body = str(mistune.html(md))
        full_html = _LAYOUT_HTML.format(
            css=_LAYOUT_CSS,
            content=date_html + html_body,
        )
        pdf_bytes = pdfun.HtmlDocument(string=full_html).to_bytes()
        if _pdf_page_count(pdf_bytes) <= 2 or not articles:  # noqa: PLR2004
            return pdf_bytes
        articles.pop()


async def _fetch_article_text(
    session: aiohttp.ClientSession,
    url: str,
) -> str:
    """Fetch a URL and extract readable text content."""
    try:
        log.debug("Fetching article: %s", url)
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            html = await resp.text()
    except (aiohttp.ClientError, TimeoutError, UnicodeDecodeError):
        log.debug("Failed to fetch article: %s", url)
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    article = soup.find("article") or soup.find("main") or soup.body
    if not article:
        return ""
    text = _sanitize_text(article.get_text(separator="\n", strip=True))
    if len(text) > _ARTICLE_MAX_CHARS:
        text = text[:_ARTICLE_MAX_CHARS] + "..."
    return text


async def _fetch_comments(
    session: aiohttp.ClientSession,
    kid_ids: list[int],
) -> list[str]:
    """Fetch top comments by id, return list of comment texts."""
    comments: list[str] = []
    log.debug("Fetching up to %d comments", min(len(kid_ids), _HN_TOP_COMMENTS))
    for kid_id in kid_ids[:_HN_TOP_COMMENTS]:
        async with session.get(f"{_HN_API}/item/{kid_id}.json") as resp:
            resp.raise_for_status()
            item: dict[str, Any] = await resp.json()
        if not item or item.get("deleted") or item.get("dead"):
            continue
        text = item.get("text", "")
        if text:
            soup = BeautifulSoup(text, "html.parser")
            clean = _sanitize_text(soup.get_text(separator=" ", strip=True))
            author = item.get("by", "unknown")
            comments.append(f"**{author}:** {clean}")
    return comments


async def _format_post(
    session: aiohttp.ClientSession,
    post: dict[str, Any],
) -> str:
    """Render a single HN post as markdown (unnumbered, no trailing separator)."""
    title = post.get("title", "Untitled")
    url = post.get("url", "")
    score = post.get("score", 0)
    author = post.get("by", "unknown")

    lines: list[str] = [f"## {title}\n"]
    if url:
        lines.append(f"{url}\n")
    lines.append(f"{score} points by {author}\n")

    if url:
        article_text = await _fetch_article_text(session, url)
        if article_text:
            lines.append(f"\n{article_text}\n")
    elif post.get("text"):
        soup = BeautifulSoup(post["text"], "html.parser")
        lines.append(f"\n{_sanitize_text(soup.get_text(separator=' ', strip=True))}\n")

    kid_ids: list[int] = post.get("kids", [])
    if kid_ids:
        comments = await _fetch_comments(session, kid_ids)
        if comments:
            lines.append("\n### Comments\n")
            lines.extend(f"- {c}\n" for c in comments)

    return "\n".join(lines)


_SUMMARIZE_SYSTEM = (
    "You are a news summarizer. Given a single Hacker News post with its article "
    "text and comments, write a 1-2 sentence summary of the article and a brief "
    "note on the comment sentiment. Output plain text only, no headings or markdown "
    "formatting."
)


async def _summarize_post_api(title: str, post_md: str) -> str:
    """Summarize via the Anthropic API using ANTHROPIC_API_KEY."""
    log.info("Summarizing (api): %s", title)
    client = anthropic.AsyncAnthropic()
    message = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=_SUMMARIZE_SYSTEM,
        messages=[{"role": "user", "content": post_md}],
    )
    block = message.content[0] if message.content else None
    result: str = (
        block.text if block and isinstance(block, anthropic.types.TextBlock) else ""
    )
    log.info("Summarized: %s (%d chars)", title, len(result))
    return result.strip()


async def _summarize_post_sdk(title: str, post_md: str) -> str:
    """Summarize via the Claude Agent SDK (requires Claude Code CLI)."""
    log.info("Summarizing (sdk): %s", title)
    result: str = ""
    async for message in query(
        prompt=post_md,
        options=ClaudeAgentOptions(
            system_prompt=_SUMMARIZE_SYSTEM,
            model="claude-haiku-4-5",
            max_turns=2,
            allowed_tools=["WebFetch"],
            permission_mode="bypassPermissions",
        ),
    ):
        if isinstance(message, ResultMessage) and message.result:
            result = message.result
    log.info("Summarized: %s (%d chars)", title, len(result))
    return result.strip()


_summarize_sem = asyncio.Semaphore(_SUMMARIZE_CONCURRENCY)


async def _summarize_post(title: str, post_md: str) -> str:
    """Summarize a single post's markdown via Claude."""
    async with _summarize_sem:
        try:
            if os.getenv("ANTHROPIC_API_KEY"):
                return await _summarize_post_api(title, post_md)
            return await _summarize_post_sdk(title, post_md)
        except Exception:  # noqa: BLE001 - sdk raises bare Exception
            log.warning("failed to summarize: %s", title, exc_info=True)
            return ""


async def _fetch_hn_posts(
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """Fetch top HN posts from the last 48 hours."""
    cutoff = time.time() - 48 * 60 * 60
    log.info("Fetching top story IDs from HN")
    async with session.get(f"{_HN_API}/topstories.json") as resp:
        resp.raise_for_status()
        story_ids: list[int] = await resp.json()
    log.info("Got %d story IDs, filtering to top %d", len(story_ids), _HN_TOP_N)

    # fetch a batch in parallel, then filter by recency
    batch = story_ids[: _HN_TOP_N * 3]

    async def _fetch_item(story_id: int) -> dict[str, Any] | None:
        async with session.get(f"{_HN_API}/item/{story_id}.json") as resp:
            resp.raise_for_status()
            item: dict[str, Any] = await resp.json()
        if item and item.get("time", 0) >= cutoff:
            return item
        return None

    results = await asyncio.gather(*(_fetch_item(sid) for sid in batch))
    posts = [r for r in results if r is not None][:_HN_TOP_N]
    log.info("Collected %d posts", len(posts))
    return posts


async def summarize_hn(session: aiohttp.ClientSession) -> str:
    """Fetch top HN posts, summarize each individually, assemble deterministically."""
    posts = await _fetch_hn_posts(session)
    log.info("Formatting %d posts in parallel", len(posts))
    post_mds = list(
        await asyncio.gather(*(_format_post(session, post) for post in posts)),
    )
    log.info("Summarizing %d posts via Claude", len(post_mds))
    summaries = await asyncio.gather(
        *[
            _summarize_post(p.get("title", "Untitled"), md)
            for p, md in zip(posts, post_mds, strict=False)
        ],
    )
    sections: list[str] = []
    for i, (post, summary) in enumerate(zip(posts, summaries, strict=False), 1):
        title = post.get("title", "Untitled")
        url = post.get("url", "")
        domain = _extract_domain(url) if url else ""
        if domain:
            heading = f"## {i}. {title}\n\n{domain}\n\n"
        else:
            heading = f"## {i}. {title}\n\n"
        sections.append(f"{heading}{summary}")
    return "\n\n---\n\n".join(sections)


async def fetch_hn(session: aiohttp.ClientSession) -> str:
    """Fetch top 10 HN posts from the last 48 hours, return as markdown."""
    posts = await _fetch_hn_posts(session)
    log.info("Formatting %d posts in parallel", len(posts))
    sections = list(
        await asyncio.gather(*(_format_post(session, post) for post in posts)),
    )
    return "\n\n---\n\n".join(sections)


class PrintingError(Exception):
    """Raised when a printing operation fails."""


def coro[**P](
    f: Callable[P, Coroutine[Any, Any, Any]],
) -> Callable[P, Coroutine[Any, Any, Any]]:
    """Wrap to allow async click commands.

    See https://github.com/pallets/click/issues/85#issuecomment-503464628 .
    """

    @wraps(f)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Coroutine[Any, Any, Any]:
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.group
def main() -> None:
    """Fetch some social media and prints it on my at-home printer."""


@main.command("run")
@click.option(
    "--dry-run",
    type=click.Choice(["markdown", "pdf"], case_sensitive=False),
    default=None,
    help=(
        "Fetch and summarize, without printing."
        " 'markdown' echoes to terminal;"
        " 'pdf' writes to a temp file."
    ),
)
@click.option(
    "--printer",
    default=None,
    help="Name of the printer. Defaults to the only printer if just one is available.",
)
@coro
async def async_run(dry_run: str | None, printer: str | None) -> None:
    """Fetch and summarize HN."""
    log.info("Starting run (dry_run=%s)", dry_run)
    async with aiohttp.ClientSession() as session:
        md = await summarize_hn(session)
    log.info("Summarization complete (%d chars)", len(md))
    if dry_run == "markdown":
        click.echo(md)
        return
    if dry_run == "pdf":
        pdf = layout_markdown(md)
        with tempfile.NamedTemporaryFile(
            suffix=".pdf",
            delete=False,
            prefix="jc-news-",
        ) as f:
            f.write(pdf)
        click.echo(f.name)
        return
    try:
        printer = printer or _find_default_printer()
        pdf = layout_markdown(md)
        log.info("PDF generated (%d bytes), sending to printer '%s'", len(pdf), printer)
        print_content(pdf, printer)
        click.echo(f"Sent to printer '{printer}'.")
    except PrintingError as e:
        raise click.ClickException(str(e)) from e


@main.command("fetch-hn")
@coro
async def async_fetch_hn() -> None:
    """Fetch top HN posts in the last 48 hours. Write to markdown file."""
    async with aiohttp.ClientSession() as session:
        md = await fetch_hn(session)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        delete=False,
        prefix="hn-",
    ) as f:
        f.write(md)
        click.echo(f"Wrote {f.name}")


@main.command("summarize-hn")
@coro
async def async_summarize_hn() -> None:
    """Summarizes HN feed in markdown file."""
    async with aiohttp.ClientSession() as session:
        md = await summarize_hn(session)
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        delete=False,
        prefix="hn-summary-",
    ) as f:
        f.write(md)
        click.echo(f"Wrote {f.name}")


@main.command("print")
@click.option(
    "--content",
    required=True,
    help="Content to print (plain text or markdown).",
)
@click.option(
    "--printer",
    default=None,
    help="Name of the printer. Defaults to the only printer if just one is available.",
)
@coro
async def async_print(content: str, printer: str | None) -> None:
    """Print content to the specified printer."""
    try:
        printer = printer or _find_default_printer()
        print_content(content, printer)
        click.echo(f"Sent to printer '{printer}'.")
    except PrintingError as e:
        raise click.ClickException(str(e)) from e


@main.command("list-printers")
@coro
async def async_list_printers() -> None:
    """List available printers on the local network."""
    try:
        printers = list_printers()
        if not printers:
            click.echo("No printers found.")
        else:
            for p in printers:
                click.echo(p)
    except PrintingError as e:
        raise click.ClickException(str(e)) from e


def _find_cups() -> str:
    """Return the path to lp, or raise PrintingError if not on PATH."""
    lp = shutil.which("lp")
    if lp is None:
        msg = "CUPS is not installed. Install it with: sudo apt install cups"
        raise PrintingError(msg)
    return lp


def _find_default_printer() -> str:
    printers = list_printers()
    if len(printers) == 1:
        return printers[0]
    if not printers:
        msg = "no printers found."
        raise PrintingError(msg)
    raise PrintingError(
        "multiple printers available, specify one with --printer: "
        + ", ".join(printers),
    )


def list_printers() -> list[str]:
    """Return list of available printer names via lpstat -a."""
    lp = _find_cups()
    lpstat = str(Path(lp).parent / "lpstat")
    result = subprocess.run([lpstat, "-a"], capture_output=True, text=True, check=False)  # noqa: S603
    if result.returncode != 0:
        if "No destinations" in result.stderr:
            return []
        msg = f"Failed to list printers: {result.stderr.strip()}"
        raise PrintingError(msg)
    printers: list[str] = [
        line.split()[0] for line in result.stdout.strip().splitlines() if line
    ]
    return printers


def print_content(content: str | bytes, printer: str) -> None:
    """Send content to the named printer via lp.

    Accepts plain text (str) or PDF bytes.
    """
    lp = _find_cups()
    if isinstance(content, bytes):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(content)
            tmp_path = f.name
        try:
            result = subprocess.run(  # noqa: S603
                [lp, "-d", printer, tmp_path],
                capture_output=True,
                text=True,
                check=True,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    else:
        result = subprocess.run(  # noqa: S603
            [lp, "-d", printer],
            input=content,
            capture_output=True,
            text=True,
            check=True,
        )
    if result.returncode != 0:
        msg = f"Failed to print: {result.stderr.strip()}"
        raise PrintingError(msg)
