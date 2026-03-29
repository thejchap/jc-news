"""jc-news: Fetch some social media and prints it on my at-home printer.

The example module supplies one function, factorial().  For example,

>>> 1 + 1
2
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Coroutine
from functools import wraps
from pathlib import Path
from typing import Any

import aiohttp
import click
import mistune
import weasyprint
from bs4 import BeautifulSoup
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

_HN_API = "https://hacker-news.firebaseio.com/v0"
_HN_TOP_N = 10
_HN_TOP_COMMENTS = 5
_ARTICLE_MAX_CHARS = 2000

_EMOJI_RE = re.compile(
    "["
    "\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff"
    "\U00002702-\U000027b0"
    "\U000024c2-\U0001f251"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "\u200d"
    "\ufe0f"
    "]+",
)
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def _sanitize_text(text: str) -> str:
    """Remove emojis and clean up whitespace."""
    text = _EMOJI_RE.sub("", text)
    text = _MULTI_SPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


_LAYOUT_CSS = """\
@page {
    size: letter;
    margin: 0.75in;
}
body {
    column-count: 2;
    column-gap: 0.3in;
    column-rule: 1px solid #ccc;
    font-family: 'Courier New', Courier, monospace;
    font-size: 10pt;
    line-height: 1.4;
    margin: 0;
    padding: 0;
}
h1 { font-size: 16pt; font-weight: bold; margin: 0.3em 0; }
h2 { font-size: 13pt; font-weight: bold; margin: 0.3em 0; }
h3 { font-size: 11pt; font-weight: bold; margin: 0.3em 0; }
ul, ol { margin: 0.2em 0; padding-left: 1.2em; }
li { margin: 0.1em 0; }
hr { border: none; border-top: 1px solid #999; margin: 0.5em 0; }
p { margin: 0.3em 0; }
"""

_LAYOUT_HTML = """\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>{css}</style></head>
<body>{content}</body>
</html>
"""


def layout_markdown(content: str) -> bytes:
    """Convert markdown to newspaper-style PDF bytes for 8.5x11 paper."""
    html_body = mistune.html(content)
    full_html = _LAYOUT_HTML.format(css=_LAYOUT_CSS, content=html_body)
    return weasyprint.HTML(string=full_html).write_pdf()


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
    except (aiohttp.ClientError, TimeoutError):
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
    i: int,
    post: dict[str, Any],
) -> list[str]:
    """Render a single HN post as markdown lines."""
    title = post.get("title", "Untitled")
    url = post.get("url", "")
    score = post.get("score", 0)
    author = post.get("by", "unknown")

    lines: list[str] = [f"## {i}. {title}\n"]
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

    lines.append("\n---\n")
    return lines


_SUMMARIZE_SYSTEM = (
    "You are a news summarizer. For each Hacker News post, write a 1-2 sentence "
    "summary of the post and a brief note on the sentiment of the comments. "
    "Use markdown formatting with the post title as a heading."
)


async def summarize_hn(session: aiohttp.ClientSession) -> str:
    """Fetch top HN posts, then summarize each with comment sentiment via Claude."""
    log.info("Fetching HN posts for summarization")
    md = await fetch_hn(session)
    log.info("Sending %d chars to Claude for summarization", len(md))
    result: str = ""
    async for message in query(
        prompt=md,
        options=ClaudeAgentOptions(
            system_prompt=_SUMMARIZE_SYSTEM,
            model="claude-haiku-4-5",
            allowed_tools=[],
            max_turns=1,
        ),
    ):
        if isinstance(message, ResultMessage) and message.result:
            result = message.result
    return result


async def fetch_hn(session: aiohttp.ClientSession) -> str:
    """Fetch top 10 HN posts from the last 48 hours, return as markdown."""
    cutoff = time.time() - 48 * 60 * 60
    log.info("Fetching top story IDs from HN")
    async with session.get(f"{_HN_API}/topstories.json") as resp:
        resp.raise_for_status()
        story_ids: list[int] = await resp.json()
    log.info("Got %d story IDs, filtering to top %d", len(story_ids), _HN_TOP_N)

    posts: list[dict[str, Any]] = []
    for story_id in story_ids:
        if len(posts) >= _HN_TOP_N:
            break
        async with session.get(f"{_HN_API}/item/{story_id}.json") as resp:
            resp.raise_for_status()
            item: dict[str, Any] = await resp.json()
        if item and item.get("time", 0) >= cutoff:
            posts.append(item)
            log.debug("Accepted story %d (%d/%d)", story_id, len(posts), _HN_TOP_N)
    log.info("Collected %d posts", len(posts))

    lines: list[str] = []
    for i, post in enumerate(posts, 1):
        log.info("Processing post %d/%d: %s", i, len(posts), post.get("title", ""))
        lines.extend(await _format_post(session, i, post))
    return "\n".join(lines)


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
@click.option("--dry-run", is_flag=True, help="Fetch and summarize, without printing.")
@click.option(
    "--printer",
    default=None,
    help="Name of the printer. Defaults to the only printer if just one is available.",
)
@coro
async def async_run(dry_run: bool, printer: str | None) -> None:
    """Fetch and summarizes HN/Twitter."""
    log.info("Starting run (dry_run=%s)", dry_run)
    async with aiohttp.ClientSession() as session:
        md = await summarize_hn(session)
    log.info("Summarization complete (%d chars)", len(md))
    if dry_run:
        click.echo(md)
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


@main.command("fetch-twitter")
@coro
async def async_fetch_twitter() -> None:
    """Fetch Twitter feed, writes contents and comments to a temporary markdown file."""
    raise NotImplementedError


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


@main.command("summarize-twitter")
@coro
async def async_summarize_twitter() -> None:
    """Summarizes Twitter feed in markdown file."""
    raise NotImplementedError


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
