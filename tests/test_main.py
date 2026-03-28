from unittest.mock import MagicMock, patch

from tryke import describe, expect, test

from jc_news import (
    PrintingError,
    _find_cups,
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
