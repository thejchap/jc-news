"""
This is the "example" module.

The example module supplies one function, factorial().  For example,

>>> 1 + 1
2
"""

import asyncio
from functools import wraps

import click


def coro(f):
    """https://github.com/pallets/click/issues/85#issuecomment-503464628"""

    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))

    return wrapper


@click.group
def main():
    """Fetches some social media and prints it on my at-home printer."""


@main.command("run")
@click.option("--dry-run", is_flag=True, help="Fetch and summarize, without printing.")
@coro
async def async_run(dry_run: bool):
    """Fetches and summarizes HN/Twitter"""
    # TODO: implement
    _check_path()


@main.command("fetch-hn")
@coro
async def async_fetch_hn():
    """Fetches top HN posts in the last 48 hours, writes contents and comments to a temporary markdown file."""
    # TODO: implement


@main.command("fetch-twitter")
@coro
async def async_fetch_twitter():
    """Fetches Twitter feed, writes contents and comments to a temporary markdown file."""
    # TODO: implement


@main.command("summarize-hn")
@coro
async def async_summarize_hn():
    """Summarizes HN feed in markdown file."""
    # TODO: implement


@main.command("summarize-twitter")
@coro
async def async_summarize_twitter():
    """Summarizes Twitter feed in markdown file."""
    # TODO: implement


@main.command("print")
@click.option(
    "--content", required=True, help="Content to print (plain text or markdown)."
)
@click.option(
    "--printer",
    required=True,
    help="Name of the printer. Use list-printers to see available printers.",
)
@coro
async def async_print(content: str, printer: str):
    """Prints content to the specified printer."""
    from jc_news.printing import PrintingError, print_content

    try:
        print_content(content, printer)
        click.echo(f"Sent to printer '{printer}'.")
    except PrintingError as e:
        raise click.ClickException(str(e))


@main.command("list-printers")
@coro
async def async_list_printers():
    """Lists available printers on the local network."""
    from jc_news.printing import PrintingError, list_printers

    try:
        printers = list_printers()
        if not printers:
            click.echo("No printers found.")
        else:
            for p in printers:
                click.echo(p)
    except PrintingError as e:
        raise click.ClickException(str(e))


def _check_path():
    """Confirms required CLIs are in the path.

    - https://github.com/sferik/x-cli

    >>> 1 + 1
    2
    """

    # TODO: implement
