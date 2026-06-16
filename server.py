"""
Suno Song Studio — local backend.

Generates complete, original ENGLISH songs (title + style + lyrics) ready to
paste into Suno. Two LLM backends, auto-selected at runtime:

  1. Anthropic API   — used if env var ANTHROPIC_API_KEY is set.
  2. Claude Code CLI — fallback: calls `claude -p` (uses your local login).

No secret is ever stored in this file. Nothing is uploaded anywhere except
your chosen Anthropic backend.

Run:
    pip install -r requirements.txt
    uvicorn server:app --port 5001
Then open web/index.html in a browser.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

MODEL = os.environ.get("SUNO_MODEL", "claude-opus-4-8")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
CLAUDE_TIMEOUT = int(os.environ.get("SUNO_TIMEOUT", "90"))

app = FastAPI(title="Suno Song Studio", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ──────────────────────────────────────────────────────────────────
class SunoRequest(BaseModel):
    idea: str = Field(..., description="Free-form description of the song you want")
    base_style: str = Field("", description="Optional base style string to build on")
    instrumental: bool = Field(False, description="True = no vocals")
    language: str = Field("english", description="Lyrics language")


class SunoResponse(BaseModel):
    title: str
    style: str
    lyrics: str


class BatchRequest(SunoRequest):
    count: int = Field(5, ge=1, le=20, description="How many songs to generate")


class BatchResponse(BaseModel):
    songs: list[SunoResponse]


# ─── Prompt ──────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a professional songwriter and Suno AI prompt engineer.
You write polished, original songs ready to paste straight into Suno.

TASK: Given the user's idea (mood, scene, genre, theme, or extra elements),
write ONE complete original song.

OUTPUT FIELDS:
1. title  — short evocative song title (1-4 words).
2. style  — a single Suno "Style of Music" line: comma-separated descriptors
   covering genre, vocal type, instruments, mood, bpm, and production. If the
   user gave a base_style, keep its core genre/feel but blend in their new idea.
   ONE line, no line breaks. For instrumental songs prefix "[Instrumental]" and
   add "no vocals".
3. lyrics — full lyrics using Suno section tags:
   [Verse] [Pre-Chorus] [Chorus] [Verse 2] [Chorus] [Bridge] [Outro]
   One line per line, 4-6 sections, repeating chorus, singable and emotional,
   not cliche. If instrumental: "[Instrumental]" then only structure tags
   ([Intro][Verse][Build][Drop][Outro]) with no words.

RULES:
- Write lyrics and style in the requested LANGUAGE (default English; Suno
  handles English best). The style line stays English regardless.
- Weave in every concrete element the user named. Match the vibe precisely.
- Original wording only. Never copy existing song lyrics.
- bpm and genre must be mutually consistent.

OUTPUT: pure JSON ONLY. No markdown fences. No prose.

{"title":"...","style":"...","lyrics":"[Verse]\\nline\\n\\n[Chorus]\\nline"}
"""


def _build_user_msg(req: SunoRequest) -> str:
    parts = [f"Song idea: {req.idea.strip()}"]
    if req.base_style:
        parts.append(f"Base style to build on: {req.base_style}")
    if req.language and req.language.lower() != "english":
        parts.append(f"Write the lyrics in: {req.language}")
    if req.instrumental:
        parts.append("This must be INSTRUMENTAL: no vocals, no lyric words.")
    return "\n".join(parts)


# ─── LLM backends ────────────────────────────────────────────────────────────
def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise HTTPException(500, f"LLM did not return valid JSON. Raw: {raw[:200]}")


def _call_api(user_msg: str) -> dict:
    try:
        import anthropic
    except ImportError:
        raise HTTPException(500, "anthropic package not installed. `pip install anthropic`")
    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _parse_json(resp.content[0].text)


def _call_cli(user_msg: str) -> dict:
    prompt = f"{SYSTEM_PROMPT}\n\n{user_msg}\n\nReturn JSON:"
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"claude CLI timed out ({CLAUDE_TIMEOUT}s)")
    except FileNotFoundError:
        raise HTTPException(
            500,
            "No backend available: set ANTHROPIC_API_KEY, or install Claude Code "
            "(claude CLI) and log in.",
        )
    if result.returncode != 0:
        raise HTTPException(500, f"claude CLI failed: {result.stderr[:300]}")
    return _parse_json(result.stdout)


def generate_song(req: SunoRequest) -> SunoResponse:
    user_msg = _build_user_msg(req)
    data = _call_api(user_msg) if API_KEY else _call_cli(user_msg)
    missing = [f for f in ("title", "style", "lyrics") if not data.get(f)]
    if missing:
        raise HTTPException(500, f"LLM response missing fields: {missing}")
    return SunoResponse(
        title=str(data["title"]).strip(),
        style=str(data["style"]).strip(),
        lyrics=str(data["lyrics"]).strip(),
    )


# ─── Endpoints ───────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    backend = "anthropic-api" if API_KEY else "claude-cli"
    return {"ok": True, "backend": backend, "model": MODEL}


@app.post("/api/suno", response_model=SunoResponse)
def suno(req: SunoRequest):
    if not req.idea.strip():
        raise HTTPException(400, "idea must not be empty")
    return generate_song(req)


@app.post("/api/suno/batch", response_model=BatchResponse)
def suno_batch(req: BatchRequest):
    if not req.idea.strip():
        raise HTTPException(400, "idea must not be empty")
    songs = []
    for i in range(req.count):
        sub = SunoRequest(
            idea=f"{req.idea} (song {i+1} of {req.count}; make it distinct "
                 f"from the others in this set)",
            base_style=req.base_style,
            instrumental=req.instrumental,
            language=req.language,
        )
        songs.append(generate_song(sub))
    return BatchResponse(songs=songs)


# Serve the web UI at /
app.mount("/", StaticFiles(directory="web", html=True), name="web")
