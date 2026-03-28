"""jc-news: Fetch some social media and prints it on my at-home printer.

The example module supplies one function, factorial().  For example,

>>> 1 + 1
2
"""

import asyncio
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Coroutine
from functools import wraps
from pathlib import Path
from typing import Any

import click
import mistune
import weasyprint

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
@coro
async def async_run(dry_run: bool) -> None:
    """Fetch and summarizes HN/Twitter."""


@main.command("fetch-hn")
@coro
async def async_fetch_hn() -> None:
    """Fetch top HN posts in the last 48 hours. Write to markdown file."""
    raise NotImplementedError


@main.command("fetch-twitter")
@coro
async def async_fetch_twitter() -> None:
    """Fetch Twitter feed, writes contents and comments to a temporary markdown file."""
    raise NotImplementedError


@main.command("summarize-hn")
@coro
async def async_summarize_hn() -> None:
    """Summarizes HN feed in markdown file."""
    raise NotImplementedError


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
    result = subprocess.run([lpstat, "-a"], capture_output=True, text=True, check=True)  # noqa: S603
    if result.returncode != 0:
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
