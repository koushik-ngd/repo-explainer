"""Step 1: rank a repo's files by explanatory value.

Usage:
    python rank.py https://github.com/pallets/flask
    python rank.py https://github.com/pallets/flask --top 25 --json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

TEXT_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".java", ".kt",
    ".php", ".cs", ".c", ".h", ".cpp", ".hpp", ".swift", ".scala", ".sh",
    ".md", ".yml", ".yaml", ".toml", ".json", ".cfg", ".ini", ".sql",
}

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build",
    "vendor", "target", ".next", ".idea", ".vscode", "coverage", "migrations",
    "__snapshots__", ".mypy_cache", ".pytest_cache",
}

ENTRYPOINTS = ("main.", "app.", "index.", "server.", "cli.", "__main__.")
MANIFESTS = (
    "dockerfile", "docker-compose", "compose.y", "package.json", "pyproject.toml",
    "requirements.txt", "go.mod", "cargo.toml", "gemfile", "makefile", "pom.xml",
)
ARCHITECTURAL = (
    "router", "routes", "schema", "models", "config", "settings", "middleware",
    "handler", "controller", "service", "db.", "database", "auth",
    "core", "engine", "client", "session", "context", "decorators",
)
NOISE = (
    "test", "spec", "mock", "fixture", "snapshot", ".min.", ".lock",
    "conftest", "example", "sample",
)

IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*(?:from\s+([\w./]+)|import\s+([\w./]+)|"""
    r"""(?:import|require)\s*\(?\s*['"]([^'"]+)['"])""",
    re.MULTILINE,
)


def clone(url: str, dest: str) -> None:
    """Shallow clone — we only ever need the current tree, not history."""
    subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", url, dest],
        check=True,
    )


def collect(root: Path):
    """Walk the tree, returning {relative_path: content} for text files."""
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = Path(dirpath) / fn
            rel = str(full.relative_to(root))
            if full.suffix.lower() not in TEXT_EXT and fn.lower() not in (
                "dockerfile", "makefile", "readme"
            ):
                continue
            try:
                if full.stat().st_size > 400_000:
                    continue
                files[rel] = full.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
    return files


def import_graph(files: dict) -> Counter:
    """Count how many files reference each file's module name.

    Regex, not an AST. It misses aliases and dynamic imports, but it is
    fast, language-agnostic, and good enough to surface load-bearing files.
    Upgrade to tree-sitter only if the ranking is visibly wrong.
    """
    CODE = {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".rb", ".java"}
    stems = {}
    for path in files:
        if Path(path).suffix.lower() not in CODE:
            continue
        stem = Path(path).stem
        if stem not in ("index", "__init__", "main"):
            stems.setdefault(stem, []).append(path)

    inbound = Counter()
    for path, content in files.items():
        seen = set()
        for match in IMPORT_RE.finditer(content):
            target = next((g for g in match.groups() if g), "")
            leaf = Path(target.replace(".", "/")).name
            if leaf in stems and leaf not in seen:
                seen.add(leaf)
                for owner in stems[leaf]:
                    if owner != path:
                        inbound[owner] += 1
    return inbound


def score(path: str, content: str, inbound: Counter) -> tuple:
    name = path.lower()
    base = Path(name).name
    s = 0
    reasons = []

    def add(points, why):
        nonlocal s
        s += points
        reasons.append(f"{why} {points:+d}")

    depth = name.count("/")

    in_docs = name.startswith("docs/") or "/docs/" in name
    if base.startswith(ENTRYPOINTS) and not in_docs and Path(name).suffix != ".md":
        add(40, "entrypoint")
    if any(k in base for k in MANIFESTS):
        add(35, "manifest")
    if "readme" in base:
        add(30 if depth == 0 else 10, "readme")
    if any(k in name for k in ARCHITECTURAL):
        add(25, "architectural")

    fan_in = inbound.get(path, 0)
    if fan_in:
        add(min(fan_in * 3, 30), f"fan-in({fan_in})")

    if any(k in name for k in NOISE):
        add(-40, "test/noise")
    if len(content) > 60_000:
        add(-20, "very large")
    if any(p.startswith(".") for p in name.split("/")[:-1]) or base.startswith("."):
        add(-35, "tooling dotfile")
    if depth > 5:
        add(-10, "deeply nested")
    if len(content.strip()) < 50:
        add(-15, "near empty")

    return s, ", ".join(reasons) or "no signal"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="GitHub repo URL")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    tmp = tempfile.mkdtemp()
    try:
        print(f"cloning {args.url} ...", file=sys.stderr)
        clone(args.url, tmp)
        files = collect(Path(tmp))
        print(f"{len(files)} text files found", file=sys.stderr)

        inbound = import_graph(files)
        ranked = sorted(
            ((p, *score(p, c, inbound), len(c)) for p, c in files.items()),
            key=lambda r: -r[1],
        )[: args.top]

        if args.json:
            print(json.dumps(
                [{"path": p, "score": s, "why": w, "bytes": n}
                 for p, s, w, n in ranked],
                indent=2,
            ))
        else:
            for p, s, w, n in ranked:
                print(f"{s:>5}  {p:<50} {w}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()