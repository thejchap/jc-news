import shutil
import subprocess


class PrintingError(Exception):
    """Raised when a printing operation fails."""


def check_cups_installed() -> None:
    """Raise PrintingError if lp is not on PATH."""
    if shutil.which("lp") is None:
        raise PrintingError(
            "CUPS is not installed. Install it with: sudo apt install cups"
        )


def list_printers() -> list[str]:
    """Return list of available printer names via lpstat -a."""
    check_cups_installed()
    result = subprocess.run(
        ["lpstat", "-a"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PrintingError(f"Failed to list printers: {result.stderr.strip()}")
    printers = []
    for line in result.stdout.strip().splitlines():
        if line:
            printers.append(line.split()[0])
    return printers


def print_content(content: str, printer: str) -> None:
    """Send content to the named printer via lp."""
    check_cups_installed()
    result = subprocess.run(
        ["lp", "-d", printer],
        input=content,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PrintingError(f"Failed to print: {result.stderr.strip()}")
