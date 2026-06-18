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
# CORS: defaults to "*" for easy local use. Set SUNO_CORS_ORIGINS (comma-separated)
# to lock it down before exposing the server beyond localhost.
_origins = os.environ.get("SUNO_CORS_ORIGINS", "*").strip()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _origins == "*" else [o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Models ──────────────────────────────────────────────────────────────────
class SunoRequest(BaseModel):
    idea: str = Field(..., description="Free-form description of the song you want")
    base_style: str = Field("", description="Optional base style string to build on")
    instrumental: bool = Field(False, description="True = no vocals")
    language: str = Field("english", description="Lyrics language")
    # ── Pro songwriting controls (all optional) ──
    tempo: str = Field("", description="e.g. '90 bpm' or 'slow ballad'")
    musical_key: str = Field("", description="e.g. 'C minor', 'A major'")
    vocal: str = Field("", description="e.g. 'female alto', 'male tenor', 'duet'")
    structure: str = Field("", description="e.g. 'Verse-Chorus-Verse-Chorus-Bridge-Chorus'")
    reference: str = Field("", description="Stylistic reference, e.g. 'in the style of 90s neo-soul'")
    theme_topic: str = Field("", description="Lyrical subject/story to write about")
    explicit: bool = Field(False, description="Allow mature/explicit language")
    duration: str = Field("", description="Target song length, e.g. '2-3 min', 'short 90s'")
    # ── Suno Advanced parameters (all optional; blank = let AI choose) ──
    instruments: str = Field("", description="Instruments to feature, e.g. 'nylon guitar, rhodes, sax'")
    exclude_styles: str = Field("", description="Styles/elements to exclude (Suno 'Exclude styles')")
    vocal_gender: str = Field("", description="'male' | 'female' | 'any'")
    weirdness: Optional[int] = Field(None, ge=0, le=100, description="Suno Weirdness % (0-100)")
    style_influence: Optional[int] = Field(None, ge=0, le=100, description="Suno Style Influence % (0-100)")


class SunoResponse(BaseModel):
    title: str
    style: str            # → Suno "Styles"
    lyrics: str           # → Suno "Lyrics"
    exclude_styles: str = ""   # → Suno "Exclude styles"
    vocal_gender: str = "any"  # → Suno "Vocal Gender"
    weirdness: int = 50        # → Suno "Weirdness" %
    style_influence: int = 50  # → Suno "Style Influence" %


class BatchRequest(SunoRequest):
    count: int = Field(5, ge=1, le=20, description="How many songs to generate")


class BatchResponse(BaseModel):
    songs: list[SunoResponse]


# ─── Prompt ──────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a professional songwriter and Suno AI prompt engineer.
You write polished, original songs and configure every Suno Advanced-mode field.

TASK: Given the user's idea (mood, scene, genre, theme, or extra elements),
write ONE complete original song AND set the matching Suno parameters.

OUTPUT FIELDS (these map 1:1 to Suno's Advanced create panel):
1. title  — short evocative song title (1-4 words).
2. style  — Suno "Styles" line: comma-separated descriptors covering genre,
   vocal character, KEY INSTRUMENTS, mood, bpm, and production. ONE line, no
   line breaks. Keep the user's base_style core, blend their new idea, and
   include any instruments the user named. For instrumental: prefix
   "[Instrumental]" and add "no vocals".
3. lyrics — full lyrics using Suno section tags:
   [Intro] [Verse] [Pre-Chorus] [Chorus] [Verse 2] [Chorus] [Bridge] [Outro]
   One line per line, repeating chorus, singable, emotional, not cliche.
   Fit the section count to the requested length (~30s per section).
   If instrumental: "[Instrumental]" then only structure tags, no words.
4. exclude_styles — Suno "Exclude styles": comma-separated things to keep OUT
   (genres/elements that would clash with this song). 3-6 items.
5. vocal_gender — "male", "female", or "any" (honor the user's request if given).
6. weirdness — integer 0-100. Suno "Weirdness": higher = more experimental/odd.
   Pick a sensible value for the genre (pop ~25, experimental ~70).
7. style_influence — integer 0-100. Suno "Style Influence": how strongly the
   style tags steer it. Default ~50; higher for strong genre identity.

RULES:
- Write lyrics in the requested LANGUAGE (default English). style/exclude stay English.
- Weave in every concrete element and instrument the user named. Match the vibe.
- Original wording only. Never copy existing song lyrics.
- bpm, key, genre, and instruments must be mutually consistent.

OUTPUT: pure JSON ONLY. No markdown fences. No prose.

{"title":"...","style":"...","lyrics":"[Verse]\\nline\\n\\n[Chorus]\\nline","exclude_styles":"...","vocal_gender":"any","weirdness":50,"style_influence":50}
"""


def _build_user_msg(req: SunoRequest) -> str:
    parts = []
    if req.idea.strip():
        parts.append(f"Song idea: {req.idea.strip()}")
    if req.base_style:
        parts.append(f"Base style to build on: {req.base_style}")
    if req.theme_topic:
        parts.append(f"Lyrical subject / story: {req.theme_topic}")
    if req.reference:
        parts.append(f"Stylistic reference: {req.reference}")
    if req.tempo:
        parts.append(f"Tempo: {req.tempo}")
    if req.musical_key:
        parts.append(f"Musical key: {req.musical_key}")
    if req.vocal:
        parts.append(f"Vocal: {req.vocal}")
    if req.vocal_gender and req.vocal_gender.lower() != "any":
        parts.append(f"Vocal gender: {req.vocal_gender}")
    if req.instruments:
        parts.append(f"Feature these instruments: {req.instruments}")
    if req.exclude_styles:
        parts.append(f"Exclude these styles/elements: {req.exclude_styles}")
    if req.weirdness is not None:
        parts.append(f"Use weirdness = {req.weirdness}")
    if req.style_influence is not None:
        parts.append(f"Use style_influence = {req.style_influence}")
    if req.structure:
        parts.append(f"Song structure (use exactly these sections in order): {req.structure}")
    if req.duration:
        parts.append(f"Target length: {req.duration} (choose section count to fit; "
                     "~30s per section as a rough guide).")
    lang = (req.language or "english").strip()
    parts.append(
        f"IMPORTANT: Write ALL lyrics in {lang.upper()}, regardless of the "
        f"language used in the description above. The 'style' and 'exclude_styles' "
        f"fields stay in English."
    )
    if req.explicit:
        parts.append("Mature/explicit language is allowed if it fits the song.")
    else:
        parts.append("Keep lyrics clean (no explicit profanity).")
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


def _call_api(user_msg: str, system: str = SYSTEM_PROMPT) -> dict:
    try:
        import anthropic
    except ImportError:
        raise HTTPException(500, "anthropic package not installed. `pip install anthropic`")
    client = anthropic.Anthropic(api_key=API_KEY)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _parse_json(resp.content[0].text)


def _call_cli(user_msg: str, system: str = SYSTEM_PROMPT) -> dict:
    prompt = f"{system}\n\n{user_msg}\n\nReturn JSON:"
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
    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    if result.returncode != 0:
        raise HTTPException(500, f"claude CLI failed (rc={result.returncode}): {err[:300] or out[:300] or 'no output'}")
    # CLI sometimes returns rc=0 with a non-JSON notice (e.g. session/usage limit)
    if not out.lstrip().startswith(("{", "```")):
        raise HTTPException(503, f"claude CLI returned no song (likely usage limit or notice): {out[:200]}")
    return _parse_json(out)


def generate_song(req: SunoRequest) -> SunoResponse:
    user_msg = _build_user_msg(req)
    data = _call_api(user_msg) if API_KEY else _call_cli(user_msg)
    missing = [f for f in ("title", "style", "lyrics") if not data.get(f)]
    if missing:
        raise HTTPException(500, f"LLM response missing fields: {missing}")
    return _to_response(data, req)


def _clamp_pct(v, default: int) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return default


def _to_response(data: dict, req: SunoRequest) -> SunoResponse:
    """Build SunoResponse, letting explicit user overrides win over the LLM."""
    vg = (req.vocal_gender or data.get("vocal_gender") or "any").lower()
    if vg not in ("male", "female", "any"):
        vg = "any"
    weird = req.weirdness if req.weirdness is not None else _clamp_pct(data.get("weirdness"), 50)
    infl = req.style_influence if req.style_influence is not None else _clamp_pct(data.get("style_influence"), 50)
    excl = req.exclude_styles.strip() or str(data.get("exclude_styles", "")).strip()
    return SunoResponse(
        title=str(data["title"]).strip(),
        style=str(data["style"]).strip(),
        lyrics=str(data["lyrics"]).strip(),
        exclude_styles=excl,
        vocal_gender=vg,
        weirdness=weird,
        style_influence=infl,
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


class ReworkRequest(BaseModel):
    title: str = ""
    style: str = ""
    lyrics: str = Field(..., description="Current full lyrics")
    section: str = Field(..., description="Which section to rewrite, e.g. 'Chorus', 'Verse 2'")
    note: str = Field("", description="What to change, e.g. 'more hopeful, add a metaphor'")
    language: str = "english"


REWORK_SYSTEM = """You are a professional lyric editor. Rewrite ONLY the requested
section of a song, keeping it consistent with the rest of the lyrics, the title,
and the style. Preserve rhyme feel and syllable count so it stays singable.
Return the FULL updated lyrics (all sections), using the same Suno section tags.
OUTPUT: pure JSON ONLY: {"title":"...","style":"...","lyrics":"..."}"""


@app.post("/api/suno/rework", response_model=SunoResponse)
def rework(req: ReworkRequest):
    """Regenerate a single section while keeping the rest of the song intact."""
    if not req.lyrics.strip() or not req.section.strip():
        raise HTTPException(400, "lyrics and section are required")
    msg = (
        f"Title: {req.title}\nStyle: {req.style}\n\nCurrent lyrics:\n{req.lyrics}\n\n"
        f"Rewrite the [{req.section}] section. Change: {req.note or 'improve it'}.\n"
        f"Language: {req.language}"
    )
    data = _call_api(msg, REWORK_SYSTEM) if API_KEY else _call_cli(msg, REWORK_SYSTEM)
    if not data.get("lyrics"):
        raise HTTPException(500, "LLM response missing field: lyrics")
    return SunoResponse(
        title=str(data.get("title", req.title)).strip(),
        style=str(data.get("style", req.style)).strip(),
        lyrics=str(data["lyrics"]).strip(),
    )


class CompleteRequest(SunoRequest):
    idea: str = Field("", description="Optional extra direction (seed_lyrics is the main input)")
    seed_lyrics: str = Field(..., description="The user's own lyric lines/sections to build around")
    seed_role: str = Field("auto", description="Where the seed goes: 'chorus', 'verse', 'hook', 'auto'")


COMPLETE_SYSTEM = """You are a professional co-writer. The songwriter gives you their
OWN lyric fragments (a few lines or a section). Build a COMPLETE song around them.

ABSOLUTE RULE: Preserve the user's given lines VERBATIM — same words, same order.
Do not paraphrase, censor, or "improve" their lines. You may only:
  - place them in the right section (their stated role, or the most natural one),
  - write the MISSING sections around them (intro/verses/pre-chorus/bridge/outro),
  - match their tone, rhyme feel, meter, and theme so the whole song is cohesive,
  - repeat their lines where a chorus/hook naturally repeats.

Mark the user's original lines so they can see them — wrap each preserved line as is,
but DO NOT add brackets inside the lyric text other than standard [Section] tags.

Honor any tempo/key/vocal/structure/length/genre constraints provided.

OUTPUT FIELDS (pure JSON ONLY, no markdown, no prose):
1. title  — short evocative title (you may draw it from their lines).
2. style  — Suno "Styles" line (genre, vocal character, key instruments, mood, bpm).
3. lyrics — full song with Suno section tags, the user's lines kept exactly.
4. exclude_styles — comma-separated styles/elements to keep out (3-6 items).
5. vocal_gender — "male" | "female" | "any".
6. weirdness — integer 0-100 (experimental-ness).
7. style_influence — integer 0-100 (how strongly style tags steer it).

{"title":"...","style":"...","lyrics":"[Verse]\\n...\\n\\n[Chorus]\\n...","exclude_styles":"...","vocal_gender":"any","weirdness":50,"style_influence":50}
"""


def _complete_user_msg(req: CompleteRequest) -> str:
    base = _build_user_msg(req)
    role = "" if req.seed_role in ("", "auto") else f"\nUse these as the [{req.seed_role}]."
    return (
        f"{base}\n\n=== MY OWN LYRICS (preserve verbatim) ==={role}\n"
        f"{req.seed_lyrics.strip()}\n=== END ===\n"
        "Build the complete song around the lines above."
    )


@app.post("/api/suno/complete", response_model=SunoResponse)
def complete(req: CompleteRequest):
    """Take the user's own lyric fragments and finish the whole song around them."""
    if not req.seed_lyrics.strip():
        raise HTTPException(400, "seed_lyrics must not be empty")
    msg = _complete_user_msg(req)
    data = _call_api(msg, COMPLETE_SYSTEM) if API_KEY else _call_cli(msg, COMPLETE_SYSTEM)
    missing = [f for f in ("title", "style", "lyrics") if not data.get(f)]
    if missing:
        raise HTTPException(500, f"LLM response missing fields: {missing}")
    return _to_response(data, req)


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
