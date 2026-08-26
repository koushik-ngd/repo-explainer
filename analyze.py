"""Pass 1: ask a model to map the repo's components.

Usage:
    export GEMINI_API_KEY=your_key_here
    python analyze.py https://github.com/pallets/click
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from rank import clone, collect, import_graph, score

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
PREFERRED = ("3.6-flash", "2.5-flash", "flash-latest", "flash")
MAX_FILE_CHARS = 12_000
MAX_TOTAL_CHARS = 350_000

PROMPT = """You are analysing a codebase. Below is the file tree and the \
content of the most important files.

Return ONLY a JSON object. No markdown fences, no preamble, no explanation.

Schema:
{
  "purpose": "one sentence on what this project does",
  "stack": ["language", "framework", "key libraries"],
  "components": [
    {
      "name": "short name",
      "responsibility": "one sentence",
      "files": ["path/one.py"]
    }
  ],
  "edges": [
    {"from": "component name", "to": "component name", "what": "what flows"}
  ]
}

Aim for 4 to 8 components. Use exact file paths from the tree.

FILE TREE:
{tree}

FILES:
{files}
"""


def pick_models(key: str) -> list:
    """Ask the API which models exist, then pick the best free-tier flash one."""
    req = urllib.request.Request(
        f"{API_ROOT}/models", headers={"x-goog-api-key": key}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)

    usable = [
        m["name"].split("/")[-1]
        for m in data.get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    if not usable:
        sys.exit("No usable models returned. Check your API key.")

    picks = []
    for want in PREFERRED:
        for name in usable:
            if want in name and "image" not in name and "tts" not in name:
                if name not in picks:
                    picks.append(name)
    return picks[:3] or usable[:1]


def call(models: list, key: str, prompt: str) -> str:
    """Try each model with exponential backoff. 503 means overloaded, not broken."""
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }).encode()

    last = ""
    for model in models:
        for attempt in range(4):
            req = urllib.request.Request(
                f"{API_ROOT}/models/{model}:generateContent",
                data=body,
                headers={"Content-Type": "application/json", "x-goog-api-key": key},
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.load(r)
                print(f"ok via {model}", file=sys.stderr)
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                last = f"{e.code} {e.read().decode()[:300]}"
                if e.code in (429, 500, 503):
                    wait = 2 ** attempt
                    print(f"{model}: {e.code}, retry in {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                sys.exit(f"API error {last}")
        print(f"{model} exhausted, trying next model", file=sys.stderr)

    sys.exit(f"All models overloaded. Last error: {last}")


def build_prompt(files: dict, ranked: list) -> str:
    tree = "\n".join(sorted(files)[:400])

    chunks, total = [], 0
    for path, _, _, _ in ranked:
        body = files[path][:MAX_FILE_CHARS]
        block = f"\n--- {path} ---\n{body}\n"
        if total + len(block) > MAX_TOTAL_CHARS:
            break
        chunks.append(block)
        total += len(block)

    print(f"sending {len(chunks)} files, ~{total // 4} tokens", file=sys.stderr)
    return PROMPT.replace("{tree}", tree).replace("{files}", "".join(chunks))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--out", default="map.json")
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        sys.exit("Set GEMINI_API_KEY first:  export GEMINI_API_KEY=your_key")

    tmp = tempfile.mkdtemp()
    try:
        print(f"cloning {args.url} ...", file=sys.stderr)
        clone(args.url, tmp)
        files = collect(Path(tmp))
        inbound = import_graph(files)
        ranked = sorted(
            ((p, *score(p, c, inbound), len(c)) for p, c in files.items()),
            key=lambda r: -r[1],
        )[: args.top]

        models = pick_models(key)
        print(f"candidates: {', '.join(models)}", file=sys.stderr)

        raw = call(models, key, build_prompt(files, ranked))
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            Path("raw_response.txt").write_text(raw)
            sys.exit("Model returned invalid JSON. Saved to raw_response.txt")

        Path(args.out).write_text(json.dumps(parsed, indent=2))
        print(json.dumps(parsed, indent=2))
        print(f"\nsaved to {args.out}", file=sys.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()