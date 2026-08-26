"""Pass 2: turn map.json into a request-flow tour + mermaid diagram.

Usage:
    python tour.py                      # reads map.json
    python tour.py --map other.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

from analyze import call, pick_models

PROMPT = """You are given a structural map of a codebase as JSON.

Produce a guided tour that explains how the system actually works when used.

Return ONLY JSON. No markdown fences, no preamble.

Schema:
{
  "mermaid": "graph TD\\n  A[Name] --> B[Name]",
  "trace": {
    "scenario": "a concrete thing a user does, one sentence",
    "steps": [
      {
        "component": "component name from the map",
        "file": "the most relevant file path",
        "what_happens": "two sentences, concrete, no filler"
      }
    ]
  },
  "start_here": ["3 file paths a newcomer should read first, in order"],
  "gotchas": ["2-3 non-obvious things about this codebase"]
}

Rules for the mermaid field:
- Use graph TD syntax only
- Node ids must be single letters or short alphanumerics
- Node labels in square brackets, no parentheses or quotes inside labels
- Include every component from the map
- Escape newlines as \\n

Pick the single most representative scenario. 5 to 8 steps.

MAP:
{map}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="map.json")
    ap.add_argument("--out", default="tour.json")
    ap.add_argument("--md", default="TOUR.md")
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY first")

    map_path = Path(args.map)
    if not map_path.exists():
        sys.exit(f"{args.map} not found. Run analyze.py first.")

    repo_map = map_path.read_text()
    prompt = PROMPT.replace("{map}", repo_map)

    models = pick_models(key)
    print(f"candidates: {', '.join(models)}", file=sys.stderr)

    raw = call(models, key, prompt).strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```")

    try:
        tour = json.loads(raw)
    except json.JSONDecodeError:
        Path("raw_tour.txt").write_text(raw)
        sys.exit("Invalid JSON. Saved to raw_tour.txt")

    Path(args.out).write_text(json.dumps(tour, indent=2))

    # Render a readable markdown version — this is what you screenshot
    m = json.loads(repo_map)
    lines = [
        f"# {m.get('purpose', 'Repo tour')}",
        "",
        f"**Stack:** {', '.join(m.get('stack', []))}",
        "",
        "## Architecture",
        "",
        "```mermaid",
        tour.get("mermaid", ""),
        "```",
        "",
        "## How it works",
        "",
        f"*{tour['trace']['scenario']}*",
        "",
    ]
    for i, step in enumerate(tour["trace"]["steps"], 1):
        lines.append(f"**{i}. {step['component']}** — `{step['file']}`")
        lines.append("")
        lines.append(step["what_happens"])
        lines.append("")

    lines += ["## Start here", ""]
    lines += [f"{i}. `{f}`" for i, f in enumerate(tour.get("start_here", []), 1)]
    lines += ["", "## Gotchas", ""]
    lines += [f"- {g}" for g in tour.get("gotchas", [])]

    Path(args.md).write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nsaved {args.out} and {args.md}", file=sys.stderr)


if __name__ == "__main__":
    main()