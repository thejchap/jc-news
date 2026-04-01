# jc news

jc news is a script that summarizes the top 20-ish posts on hacker news,
then prints to a printer on your local network! the hacker news(paper).

<img src="https://github.com/user-attachments/assets/49a454ec-95fb-4ccb-90cc-03d42a6f86b4" alt="jc-news" width="200"/>

## getting started

this can be run easily via `uvx` if you have [uv](https://docs.astral.sh/uv/) installed.

```bash
# make sure we have a printer
uvx --from git+https://github.com/thejchap/jc-news jc-news list-printers

# dry run pdf generation
uvx --from git+https://github.com/thejchap/jc-news jc-news run --dry-run=pdf

# print it!
uvx --from git+https://github.com/thejchap/jc-news jc-news run
```

### summarization

you need to either be logged in to claude code via a pro account,
or have an `ANTHROPIC_API_KEY` environment variable set.
the script uses the anthropic api for summarization.

## contributing

prs welcome

### verification

```bash

# run the tests
uv run tryke test

# run the type checker
uv run ty check

# linting/formatting
uv run ruff check
uv run ruff format --check

# or...
uvx prek -a
```
