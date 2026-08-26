# Repo Explainer

Paste a public GitHub URL, get an architecture diagram and a walkthrough of what
happens when someone actually uses the project.

## Why it isn't just "send the repo to an LLM"

Most repos are far too large to fit in a context window, and dumping what does fit
produces generic summaries. The work is in **file selection**: picking the ~25 files
that explain the system out of thousands.

`rank.py` scores every file by explanatory value:

| Signal | Weight |
|---|---|
| Entrypoint (`main.`, `app.`, `server.`) | +40 |
| Manifest (`package.json`, `pyproject.toml`, Dockerfile) | +35 |
| Root README | +30 |
| Architectural name (`router`, `core`, `models`, `auth`) | +25 |
| Fan-in — how many files import it | +3 each, capped at +30 |
| Tests, fixtures, mocks | −40 |
| Tooling dotfiles | −35 |
| Very large (likely generated) | −20 |

Fan-in is computed with a regex import graph rather than an AST — language-agnostic
and fast, and accurate enough that the top 15 files are usually the ones you'd hand
a new joiner.

## Then two model passes

1. **Map** — the ranked files go to the model, which returns components, their
   responsibilities, and the edges between them as JSON.
2. **Trace** — that map goes back in (without the source), and the model narrates one
   concrete scenario end to end.

Splitting the passes matters. A single prompt asking for both produces mush; the
second pass stays coherent because it reasons over the map instead of raw code.

## Running it

```bash
pip install -r requirements.txt
export GEMINI_API_KEY=your_key      # aistudio.google.com/apikey
uvicorn server:app --reload --port 8000
```

CLI, if you'd rather skip the web UI:

```bash
python rank.py https://github.com/psf/requests --top 25   # just the ranking
python analyze.py https://github.com/psf/requests         # → map.json
python tour.py                                            # → TOUR.md
```

## Caching

Results are keyed by commit SHA. Before cloning, the server runs `git ls-remote` to
get HEAD — so a repeat request for an unchanged repo returns from SQLite with no
clone and no API call.

## Known limits

- The import-graph regex misses dynamic and aliased imports.
- Tuned on Python; ranking is weaker on repos with unusual layouts.
- Monorepos hit the file cap and get analysed shallowly.
- Diagram rendering occasionally fails when the model emits invalid mermaid syntax.

## Stack

Python, FastAPI, SQLite, Gemini API, mermaid.js. No build step on the frontend.