"""
Suno Song Studio — local backend.

Generates multilingual song briefs and lyrics with Codex CLI, Claude CLI or
Anthropic API. Durable production jobs and independent text reviews live in
production.py. Suno audio submission is not connected.

Run:
    pip install -r requirements.txt
    uvicorn server:app --port 5001
Then open http://127.0.0.1:5001/ in a browser.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

MODEL = os.environ.get("SUNO_MODEL", "claude-opus-4-8")
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
CLAUDE_TIMEOUT = int(os.environ.get("SUNO_TIMEOUT", "90"))

app = FastAPI(title="Suno Song Studio", version="1.0")
# The browser UI is served by this localhost process. Other websites must not
# be able to spend model or Suno credits through cross-origin requests.
_origins = os.environ.get("SONGFORGE_CORS_ORIGINS", "http://127.0.0.1:5001,http://localhost:5001").strip()
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
    duration_mode: Literal["auto", "custom"] = "auto"
    duration_seconds: Optional[int] = Field(None, ge=30, le=480)
    max_mode: bool = False
    variety: Literal["low", "normal", "high"] = "normal"
    personalize: bool = False
    audio_reference: str = ""
    voice_reference: str = ""
    inspo_reference: str = ""
    provider: Literal["auto", "codex", "claude", "anthropic"] = "auto"


class SunoResponse(BaseModel):
    title: str
    style: str            # → Suno "Styles"
    lyrics: str           # → Suno "Lyrics"
    exclude_styles: str = ""   # → Suno "Exclude styles"
    vocal_gender: str = "any"  # → Suno "Vocal Gender"
    weirdness: int = 50        # → Suno "Weirdness" %
    style_influence: int = 50  # → Suno "Style Influence" %
    # ── musician / DJ layer ──
    musical_key: str = ""      # e.g. "C minor" (resolved key of the song)
    bpm: str = ""              # e.g. "92"
    camelot: str = ""          # harmonic-mixing code, e.g. "5A"
    chords: str = ""           # chord progression per section
    duration_mode: str = "auto"
    duration_seconds: Optional[int] = None
    max_mode: bool = False
    variety: str = "normal"
    personalize: bool = False
    audio_reference: str = ""
    voice_reference: str = ""
    inspo_reference: str = ""
    quality_warnings: list[str] = Field(default_factory=list)


class BatchRequest(SunoRequest):
    count: int = Field(5, ge=1, le=20, description="How many songs to generate")


class BatchResponse(BaseModel):
    songs: list[SunoResponse]


class ProjectRequest(BaseModel):
    direction: str = Field(..., min_length=3, description="One-sentence creative direction")
    style: str = Field(..., min_length=2, description="Any genre or cultural style, not limited to presets")
    languages: list[str] = Field(..., min_length=1, max_length=6)
    count: int = Field(10, ge=1, le=15)
    reference_traits: str = Field("", description="Observed musical traits; a URL alone is not audio analysis")
    instrumental: bool = False
    provider: Literal["auto", "codex", "claude", "anthropic"] = "auto"


class ProjectBrief(BaseModel):
    concept: str
    scene: str
    emotional_turn: str
    hook_idea: str
    arrangement: str
    language: str


class ProjectPlan(BaseModel):
    songs: list[ProjectBrief]


PROJECT_SYSTEM = """You are an album creative director. Plan a cohesive but genuinely varied collection of original songs.
Return pure JSON only: {"songs":[{"concept":"...","scene":"...","emotional_turn":"...","hook_idea":"...","arrangement":"...","language":"..."}]}.
Each song must have a distinct story premise, concrete scene, emotional change, hook concept and arrangement.
Avoid formulaic substitutions, recycled metaphors, repeated hook phrases, and references to existing song lyrics, melodies or named artists.
Do not claim to have listened to a linked song. Reference traits are user-provided observations only.
The provided language list must be distributed across the collection, and language names must match the supplied list exactly.
Keep every value concise and specific. Instrumental projects still need unique musical motifs instead of lyric hooks."""


def _normalized(text: str) -> str:
    return re.sub(r"[^\w]+", "", text.casefold())


def _validate_plan(data: dict, req: ProjectRequest, languages: list[str]) -> ProjectPlan:
    try:
        plan = ProjectPlan.model_validate(data)
    except Exception as exc:
        raise ValueError(f"Invalid project plan: {exc}") from exc
    if len(plan.songs) != req.count:
        raise ValueError(f"Project plan returned {len(plan.songs)} songs; expected {req.count}")
    if any(song.language not in languages for song in plan.songs):
        raise ValueError("Project plan returned a language outside the requested list")
    if req.count >= len(languages) and set(languages) - {song.language for song in plan.songs}:
        raise ValueError("Project plan did not cover every requested language")
    for field in ("concept", "scene", "hook_idea"):
        values = [_normalized(getattr(song, field)) for song in plan.songs]
        if any(not value for value in values) or len(set(values)) != len(values):
            raise ValueError(f"Project plan contains duplicate or empty {field} values")
    return plan


@app.post("/api/suno/plan", response_model=ProjectPlan)
def plan_project(req: ProjectRequest):
    languages = [language.strip() for language in req.languages if language.strip()]
    if not languages:
        raise HTTPException(400, "At least one language is required")
    user_msg = (
        f"Plan exactly {req.count} songs.\nCreative direction: {req.direction}\n"
        f"Genre / cultural style: {req.style}\nLanguages: {', '.join(languages)}\n"
        f"Instrumental: {req.instrumental}\n"
        f"Reference traits supplied by user: {req.reference_traits or 'none'}\n"
        "Use each language label exactly as provided in a song's language field. "
        "A label with '+' means a single bilingual song, not two separate songs.\n"
        "Make every concept, scene and hook distinct. Output one JSON object only."
    )
    for attempt in range(2):
        data = _call_model(user_msg, PROJECT_SYSTEM, req.provider)
        try:
            return _validate_plan(data, req, languages)
        except ValueError as exc:
            if attempt == 1:
                raise HTTPException(502, str(exc)) from exc
            user_msg += f"\nPrevious output was invalid: {exc}. Regenerate the entire plan correctly."


# ─── Prompt ──────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a professional songwriter and Suno AI prompt engineer.
You write polished, original songs and configure every Suno Advanced-mode field.

TASK: Given the user's idea (mood, scene, genre, theme, or extra elements),
write ONE complete original song AND set the matching Suno parameters.

OUTPUT FIELDS (these map 1:1 to Suno's Advanced create panel):
1. title  — short evocative song title (1-4 words).
2. style  — Suno "Styles" line: one concise comma-separated line covering
   primary genre, vocal character, 2-3 KEY INSTRUMENTS, mood, BPM, and production.
   Prioritize concrete acoustic descriptors over adjectives. Avoid named artists,
   contradictory directions and repeated tempo tags. For instrumental: prefix
   "[Instrumental]" and add "no vocals".
3. lyrics — full lyrics using Suno section tags:
   [Intro] [Verse] [Pre-Chorus] [Chorus] [Verse 2] [Chorus] [Bridge] [Outro]
   One line per line. Give the chorus a memorable hook, repeat its wording exactly,
   use concrete imagery, natural phrasing and a consistent point of view.
   Keep lines singable, usually 5-12 words in English; fit sections to duration.
   If instrumental: "[Instrumental]" then only structure tags, no words.
4. exclude_styles — Suno "Exclude styles": comma-separated things to keep OUT
   (genres/elements that would clash with this song). 3-6 items.
5. vocal_gender — "male", "female", or "any" (honor the user's request if given).
6. weirdness — integer 0-100. Suno "Weirdness": higher = more experimental/odd.
   Pick a sensible value for the genre (pop ~25, experimental ~70).
7. style_influence — integer 0-100. Suno "Style Influence": how strongly the
   style tags steer it. Default ~50; higher for strong genre identity.
8. musical_key — the song's key, e.g. "C minor", "A major" (honor user's key if given).
9. bpm — the tempo as a number string, e.g. "92" (consistent with the style/tempo).
10. chords — a chord progression for the main sections, written as plain text,
   e.g. "Verse: Am - F - C - G | Chorus: F - C - G - Am". Use chords that fit the key.

RULES:
- Write lyrics in the requested LANGUAGE (default English). style/exclude stay English.
- Weave in every concrete element and instrument the user named. Match the vibe.
- Original wording only. Never copy existing song lyrics.
- bpm, key, genre, and instruments must be mutually consistent.
- Keep Styles focused. Do not promise a specific melody, exact duration, or audio quality.

OUTPUT: pure JSON ONLY. No markdown fences. No prose.

{"title":"...","style":"...","lyrics":"[Verse]\\nline\\n\\n[Chorus]\\nline","exclude_styles":"...","vocal_gender":"any","weirdness":50,"style_influence":50,"musical_key":"C minor","bpm":"92","chords":"Verse: Cm - Ab - Eb - Bb | Chorus: Ab - Eb - Bb - Cm"}
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
    if req.duration_mode == "custom" and req.duration_seconds:
        parts.append(f"Target length: approximately {req.duration_seconds} seconds; plan an appropriate number of sections and line lengths. Suno may vary the final duration.")
    elif req.duration:
        parts.append(f"Target length: {req.duration} (choose a suitable section count; final duration may vary).")
    if req.variety == "low":
        parts.append("Favor a direct, cohesive arrangement and a clear repeated hook.")
    elif req.variety == "high":
        parts.append("Vary sections and texture while keeping one coherent genre and hook.")
    if req.audio_reference:
        parts.append(f"User audio reference note (descriptive only; no audio is attached here): {req.audio_reference}")
    if req.voice_reference:
        parts.append(f"User voice reference note (descriptive only): {req.voice_reference}")
    if req.inspo_reference:
        parts.append(f"User inspiration note (descriptive only): {req.inspo_reference}")
    lang = (req.language or "english").strip()
    mixed = [part.strip() for part in re.split(r"[+/／＋]", lang) if part.strip()]
    if len(mixed) >= 2:
        parts.append(
            f"IMPORTANT: This is ONE mixed-language song using {mixed[0]} and {mixed[1]}. "
            f"Write verses mainly in {mixed[0]} and the repeated chorus mainly in {mixed[1]}; "
            "use a natural bilingual bridge. Both languages must actually appear in the lyrics. "
            "Do not translate each line twice or mix languages word-by-word unnaturally. "
            "Keep the hook identical each time the chorus repeats. Styles and exclude_styles stay in English."
        )
    else:
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
        max_tokens=5000 if system == PROJECT_SYSTEM else 2000,
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


def _call_codex(user_msg: str, system: str = SYSTEM_PROMPT) -> dict:
    if not shutil.which("codex"):
        raise HTTPException(503, "codex CLI is not installed")
    prompt = (
        "You are a creative writer. Do not use tools, read files, or perform actions. "
        "Return only the requested JSON.\n\n" + system + "\n\n" + user_msg
    )
    with tempfile.TemporaryDirectory(prefix="songforge-codex-") as workdir:
        try:
            result = subprocess.run(
                ["codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                 "--skip-git-repo-check", "--sandbox", "read-only", "-C", workdir, "-"],
                input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT * 2,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "codex CLI timed out") from exc
    if result.returncode != 0:
        raise HTTPException(503, f"codex CLI failed: {(result.stderr or result.stdout)[-300:]}")
    return _parse_json(result.stdout)


def _call_model(user_msg: str, system: str, provider: str) -> dict:
    if provider == "codex":
        return _call_codex(user_msg, system)
    if provider == "anthropic":
        if not API_KEY:
            raise HTTPException(503, "ANTHROPIC_API_KEY is not configured")
        return _call_api(user_msg, system)
    if provider == "claude":
        return _call_cli(user_msg, system)
    return _call_api(user_msg, system) if API_KEY else _call_cli(user_msg, system)


def generate_song(req: SunoRequest) -> SunoResponse:
    user_msg = _build_user_msg(req)
    data = _call_model(user_msg, SYSTEM_PROMPT, req.provider)
    missing = [f for f in ("title", "style", "lyrics") if not data.get(f)]
    if missing:
        raise HTTPException(500, f"LLM response missing fields: {missing}")
    return _to_response(data, req)


def _clamp_pct(v, default: int) -> int:
    try:
        return max(0, min(100, int(v)))
    except (TypeError, ValueError):
        return default


# Camelot wheel (harmonic-mixing codes used by Rekordbox/Serato/Mixed In Key)
_CAMELOT = {
    # major (B side)
    ("b", "major"): "1B", ("f#", "major"): "2B", ("gb", "major"): "2B", ("db", "major"): "3B",
    ("c#", "major"): "3B", ("ab", "major"): "4B", ("g#", "major"): "4B", ("eb", "major"): "5B",
    ("d#", "major"): "5B", ("bb", "major"): "6B", ("a#", "major"): "6B", ("f", "major"): "7B",
    ("c", "major"): "8B", ("g", "major"): "9B", ("d", "major"): "10B", ("a", "major"): "11B",
    ("e", "major"): "12B",
    # minor (A side)
    ("ab", "minor"): "1A", ("g#", "minor"): "1A", ("eb", "minor"): "2A", ("d#", "minor"): "2A",
    ("bb", "minor"): "3A", ("a#", "minor"): "3A", ("f", "minor"): "4A", ("c", "minor"): "5A",
    ("g", "minor"): "6A", ("d", "minor"): "7A", ("a", "minor"): "8A", ("e", "minor"): "9A",
    ("b", "minor"): "10A", ("f#", "minor"): "11A", ("gb", "minor"): "11A", ("c#", "minor"): "12A",
    ("db", "minor"): "12A",
}


def to_camelot(key: str) -> str:
    """Convert a key like 'C minor' / 'F# Major' to a Camelot code, or '' if unknown."""
    if not key:
        return ""
    k = key.strip().lower().replace("♯", "#").replace("♭", "b")
    quality = "minor" if ("min" in k or k.endswith("m")) else ("major" if "maj" in k or "major" in k else None)
    # extract the root note (first token)
    import re as _re
    m = _re.match(r"\s*([a-g][#b]?)", k)
    if not m:
        return ""
    root = m.group(1)
    if quality is None:
        quality = "minor" if k.rstrip().endswith("m") else "major"
    return _CAMELOT.get((root, quality), "")


def _to_response(data: dict, req: SunoRequest) -> SunoResponse:
    """Build SunoResponse, letting explicit user overrides win over the LLM."""
    vg = "any" if req.instrumental else (req.vocal_gender or data.get("vocal_gender") or "any").lower()
    if vg not in ("male", "female", "any"):
        vg = "any"
    weird = req.weirdness if req.weirdness is not None else _clamp_pct(data.get("weirdness"), 50)
    infl = req.style_influence if req.style_influence is not None else _clamp_pct(data.get("style_influence"), 50)
    excl = req.exclude_styles.strip() or str(data.get("exclude_styles", "")).strip()
    key = (req.musical_key.strip() or str(data.get("musical_key", "")).strip())
    lyrics = str(data["lyrics"]).strip()
    style = str(data["style"]).strip()
    warnings = []
    if not req.instrumental:
        if "[Chorus]" not in lyrics:
            warnings.append("歌詞缺少 [Chorus]；請檢查歌曲 hook。")
        if req.language.lower() == "english" and len(lyrics.split()) < 60:
            warnings.append("歌詞偏短；請確認長度符合預期。")
    elif re.search(r"(?im)^\s*(?!\[|$)[^\[]+\w", lyrics):
        warnings.append("純音樂歌詞含文字；請確認 Suno 歌詞欄留空或只用結構標記。")
    if len(style) > 500:
        warnings.append("Styles 超過 500 字元；建議精簡，避免方向互相稀釋。")
    if not req.instrumental and vg in ("male", "female"):
        opposite = "female" if vg == "male" else "male"
        if re.search(rf"\b{opposite}\s+(?:lead\s+)?(?:vocals?|voice|singer)\b", style, re.I):
            warnings.append("Styles 的人聲性別與 Vocal Gender 設定衝突。")
    if req.tempo:
        requested_bpm = re.search(r"\b(\d{2,3})\s*bpm\b", req.tempo, re.I)
        style_bpm = re.search(r"\b(\d{2,3})\s*bpm\b", style, re.I)
        if requested_bpm and style_bpm and requested_bpm.group(1) != style_bpm.group(1):
            warnings.append("Styles 的 BPM 與指定速度不同；請手動核對。")
    if req.duration_mode == "custom" and req.duration_seconds is None:
        warnings.append("已選 Custom 時長，但未設定秒數。")
    mixed = [part.strip().lower() for part in re.split(r"[+/／＋]", req.language) if part.strip()]
    if len(mixed) >= 2 and not req.instrumental:
        lyric_lines = re.sub(r"(?m)^\s*\[[^\]]+\]\s*$", "", lyrics)
        if any(part in ("中文", "chinese", "mandarin") for part in mixed) and not re.search(r"[\u4e00-\u9fff]", lyric_lines):
            warnings.append("混合語言指定中文，但歌詞未見中文字。")
        if any(part in ("日文", "日本語", "japanese") for part in mixed) and not re.search(r"[\u3040-\u30ff]", lyric_lines):
            warnings.append("混合語言指定日文，但歌詞未見日文假名；請人工確認。")
        if any(part in ("英文", "english") for part in mixed) and not re.search(r"\b[a-zA-Z]{2,}\b", lyric_lines):
            warnings.append("混合語言指定英文，但歌詞未見英文單字。")
    return SunoResponse(
        title=str(data["title"]).strip(),
        style=style,
        lyrics=lyrics,
        exclude_styles=excl,
        vocal_gender=vg,
        weirdness=weird,
        style_influence=infl,
        musical_key=key,
        bpm=str(data.get("bpm", "")).strip(),
        camelot=to_camelot(key),
        chords=str(data.get("chords", "")).strip(),
        duration_mode=req.duration_mode,
        duration_seconds=req.duration_seconds if req.duration_mode == "custom" else None,
        max_mode=req.max_mode,
        variety=req.variety,
        personalize=req.personalize,
        audio_reference=req.audio_reference.strip(),
        voice_reference=req.voice_reference.strip(),
        inspo_reference=req.inspo_reference.strip(),
        quality_warnings=warnings,
    )


# ─── Endpoints ───────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    backend = "anthropic-api" if API_KEY else "claude-cli"
    return {"ok": True, "backend": backend, "model": MODEL,
            "providers": {"codex": bool(shutil.which("codex")),
                          "claude": bool(shutil.which("claude")), "anthropic": bool(API_KEY)}}


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
    provider: Literal["auto", "codex", "claude", "anthropic"] = "auto"


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
    data = _call_model(msg, REWORK_SYSTEM, req.provider)
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
8. musical_key, bpm, chords — the key, tempo number, and a chord progression
   for the main sections (e.g. "Verse: Am - F - C - G | Chorus: F - C - G - Am").

{"title":"...","style":"...","lyrics":"[Verse]\\n...\\n\\n[Chorus]\\n...","exclude_styles":"...","vocal_gender":"any","weirdness":50,"style_influence":50,"musical_key":"A minor","bpm":"90","chords":"Verse: Am - F - C - G"}
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
    data = _call_model(msg, COMPLETE_SYSTEM, req.provider)
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
            tempo=req.tempo, musical_key=req.musical_key, vocal=req.vocal,
            structure=req.structure, reference=req.reference, theme_topic=req.theme_topic,
            explicit=req.explicit, duration=req.duration, instruments=req.instruments,
            exclude_styles=req.exclude_styles, vocal_gender=req.vocal_gender,
            weirdness=req.weirdness, style_influence=req.style_influence,
            duration_mode=req.duration_mode, duration_seconds=req.duration_seconds,
            max_mode=req.max_mode, variety=req.variety, personalize=req.personalize,
            audio_reference=req.audio_reference, voice_reference=req.voice_reference,
            inspo_reference=req.inspo_reference,
            provider=req.provider,
        )
        songs.append(generate_song(sub))
    return BatchResponse(songs=songs)


class ProductionRequest(BaseModel):
    direction: str = Field(min_length=3, max_length=6000)
    style: str = Field(min_length=2, max_length=2000)
    languages: list[str] = Field(min_length=1, max_length=6)
    count: int = Field(default=10, ge=1, le=15)
    reference_traits: str = Field(default='', max_length=6000)
    channel_brief: str = Field(default='', max_length=6000)
    settings: SunoRequest


def production_validate(data):
    from pydantic import ValidationError
    try:
        req = ProductionRequest.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not req.direction.strip() or not any(s.strip() for s in req.languages):
        raise HTTPException(422, '請填主題與語言')
    return req.model_dump()


def production_plan(data):
    settings = data['settings']
    request = ProjectRequest(direction=data['direction'] + '\nChannel identity: ' + data['channel_brief'],
                             style=data['style'], languages=data['languages'], count=data['count'],
                             reference_traits=data['reference_traits'], instrumental=settings['instrumental'],
                             provider=settings['provider'])
    return [song.model_dump() for song in plan_project(request).songs]


def production_generate(data, brief, history):
    from difflib import SequenceMatcher
    recent = [{'title': s['title'], 'hook': re.search(r'\[Chorus\]([^[]*)', s.get('lyrics', ''))[1][:300]
               if re.search(r'\[Chorus\]([^[]*)', s.get('lyrics', '')) else ''} for s in history[:30]]
    settings = dict(data['settings'])
    settings.update(language=brief['language'], base_style=data['style'] + ', ' + brief['arrangement'])
    idea = (data['direction'] + '\nChannel identity: ' + data['channel_brief'] +
            '\nCreative brief: ' + json.dumps(brief, ensure_ascii=False) +
            '\nAvoid these existing titles, hook phrases and stories: ' + json.dumps(recent, ensure_ascii=False))
    for attempt in range(2):
        settings['idea'] = idea + ('\nPrevious draft was too similar. Choose a completely new hook and wording.' if attempt else '')
        request = SunoRequest.model_validate(settings)
        song = generate_song(request).model_dump()
        def hook(s):
            match = re.search(r'\[Chorus\]([^[]*)', s.get('lyrics', ''))
            return _normalized(match[1] if match else s.get('lyrics', ''))
        duplicate = any(_normalized(song['title']) == _normalized(old['title']) or
                        (not request.instrumental and len(hook(song)) > 12 and
                         SequenceMatcher(None, hook(song), hook(old)).ratio() > .78) for old in history)
        if not duplicate:
            song.update(_payload=settings, _genre=data['style'])
            return song
    raise HTTPException(422, '副歌或標題與作品庫過於相近，請調整企劃後重試。')


from pathlib import Path
from production import Studio, router as studio_router
from mureka_api import MurekaApi
from mureka_pipeline import MurekaPipeline, router as mureka_pipeline_router

def production_revise(song):
    request = SunoRequest.model_validate(song.get('_payload') or {'idea': song['title'], 'provider': 'codex'})
    data = _call_model(json.dumps({'original': {k: song.get(k) for k in ('title', 'style', 'lyrics')},
                                   'editor_feedback': song['_review'], 'requirements': request.model_dump()}, ensure_ascii=False),
                       SYSTEM_PROMPT + '\nRevise the supplied song using the editor feedback. Preserve its identity, language and musical requirements. Return the full revised song.', request.provider)
    if not all(data.get(k) for k in ('title', 'style', 'lyrics')):
        raise HTTPException(502, '改稿缺少必要欄位，原作保持不變。')
    version = dict(song)
    version.update(title=str(data['title']), lyrics=str(data['lyrics']), style=str(data['style']))
    # Editing lyrics must not silently reset the producer's existing controls.
    version['quality_warnings'] = _to_response(version, request).quality_warnings
    return version

studio = Studio(os.environ.get('SONGFORGE_DB', str(Path(__file__).parent / 'data' / 'studio.sqlite3')),
                production_plan, production_generate, _call_model,
                lambda song: _to_response(song, SunoRequest.model_validate(song.get('_payload') or {'idea': song['title']})).quality_warnings,
                production_revise)
mureka_pipeline = MurekaPipeline(studio, MurekaApi())
app.include_router(studio_router(studio, production_validate))
app.include_router(mureka_pipeline_router(mureka_pipeline))

# Serve the web UI at /
app.mount("/", StaticFiles(directory="web", html=True), name="web")
