"""Web API for repo-explainer.

Run:
    pip install -r requirements.txt
    export GEMINI_API_KEY=...
    uvicorn server:app --reload --port 8000
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from analyze import build_prompt, call, pick_models
from rank import collect, import_graph, score
from tour import PROMPT as TOUR_PROMPT

DB = os.environ.get("CACHE_DB", "cache.db")
MAX_FILES = 5000

app = FastAPI(title="repo-explainer")


def db():
    conn = sqlite3.connect(DB)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cache ("
        "  sha TEXT PRIMARY KEY,"
        "  url TEXT,"
        "  result TEXT,"
        "  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    return conn


class AnalyzeReq(BaseModel):
    url: str


def normalise(url: str) -> str:
    url = url.strip().rstrip("/").removesuffix(".git")
    if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", url):
        raise HTTPException(400, "Expected https://github.com/owner/repo")
    return url


def head_sha(url: str) -> str:
    """Cheap remote lookup — no clone needed to check the cache."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", url, "HEAD"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
        return out.split()[0]
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError):
        raise HTTPException(404, "Repo not found or not public")


def clean_json(raw: str) -> dict:
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(502, "Model returned malformed JSON. Try again.")


@app.post("/api/analyze")
def analyze(req: AnalyzeReq):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise HTTPException(500, "GEMINI_API_KEY not set on the server")

    url = normalise(req.url)
    sha = head_sha(url)

    conn = db()
    row = conn.execute("SELECT result FROM cache WHERE sha = ?", (sha,)).fetchone()
    if row:
        conn.close()
        return {"cached": True, "sha": sha, **json.loads(row[0])}

    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", url, tmp],
            check=True, timeout=120,
        )
        files = collect(Path(tmp))
        if not files:
            raise HTTPException(422, "No readable source files found")
        if len(files) > MAX_FILES:
            raise HTTPException(413, f"Repo too large ({len(files)} files)")

        inbound = import_graph(files)
        ranked = sorted(
            ((p, *score(p, c, inbound), len(c)) for p, c in files.items()),
            key=lambda r: -r[1],
        )[:25]

        models = pick_models(key)
        repo_map = clean_json(call(models, key, build_prompt(files, ranked)))
        tour = clean_json(
            call(models, key, TOUR_PROMPT.replace("{map}", json.dumps(repo_map)))
        )

        result = {"map": repo_map, "tour": tour, "url": url}
        conn.execute(
            "INSERT OR REPLACE INTO cache (sha, url, result) VALUES (?, ?, ?)",
            (sha, url, json.dumps(result)),
        )
        conn.commit()
        return {"cached": False, "sha": sha, **result}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Clone timed out")
    except subprocess.CalledProcessError:
        raise HTTPException(404, "Clone failed")
    finally:
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/api/recent")
def recent():
    conn = db()
    rows = conn.execute(
        "SELECT url, sha FROM cache ORDER BY created_at DESC LIMIT 8"
    ).fetchall()
    conn.close()
    return [{"url": u, "sha": s[:7]} for u, s in rows]


app.mount("/", StaticFiles(directory="static", html=True), name="static")