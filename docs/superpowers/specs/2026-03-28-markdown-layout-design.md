# Markdown Layout for Print — Design Spec

## Problem

`print_content` currently sends raw text to the printer with no formatting. We need a layout function that converts markdown into a newspaper-style, print-ready PDF for 8.5x11 (letter) paper.

## Design

### Pipeline

```
markdown string → mistune (parse to HTML) → wrap in HTML+CSS template → WeasyPrint (render to PDF bytes) → return bytes
```

### Function Interface

```python
# src/jc_news/layout.py

def layout_markdown(content: str) -> bytes:
    """Convert markdown to newspaper-style PDF bytes for 8.5x11 paper."""
```

- Input: markdown string
- Output: PDF bytes ready for printing
- Called by the print pipeline; not a CLI command itself

### CSS / Layout Rules

- **Page:** `@page { size: letter; margin: 0.75in; }` — 7x9.5in content area
- **Columns:** `column-count: 2; column-gap: 0.3in; column-rule: 1px solid #ccc;`
- **Font:** Monospace (Courier / Courier New), ~10pt body text
- **Line height:** 1.4
- **Headings:** Bold monospace, slightly larger (h1: 16pt, h2: 13pt, h3: 11pt)
- **Lists:** Compact bullet lists with reduced margins for column fit
- **Section breaks:** Horizontal rules between sections
- **No masthead** — content starts directly at top of page

### File Structure

- **New:** `src/jc_news/layout.py` — `layout_markdown()`, HTML template, CSS constants
- **Modified:** `src/jc_news/__init__.py` — update `print_content` to handle `bytes` (PDF) input
- **New:** `tests/test_layout.py` — tests for layout function

### print_content Changes

Currently `print_content` pipes text to `lp` via stdin. For PDF bytes:

1. Write bytes to a temporary file
2. Pass the temp file path to `lp` instead of piping stdin
3. Clean up the temp file after printing

### Dependencies

- `mistune` — lightweight markdown-to-HTML parser (pure Python)
- `weasyprint` — HTML/CSS to PDF renderer (requires system libs: Pango, Cairo)

### Verification

1. Unit tests: `layout_markdown` returns valid PDF bytes (check `%PDF` header)
2. Test with sample markdown containing headings, paragraphs, lists, bold/italic
3. Visual check: render a sample and open the PDF to confirm 2-column monospace layout
4. Integration: pass PDF bytes through `print_content` and verify `lp` receives the file
