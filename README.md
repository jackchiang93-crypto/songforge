# 🎛️ SONGFORGE — Suno Song Studio

**An AI songwriting workbench for songwriters & hobbyists.** Describe a song → get a complete, original work (**title + style + lyrics**) ready to paste straight into [Suno](https://suno.com). Shape it with pro controls (tempo, key, vocal, structure, reference), rewrite any section, batch a whole playlist, and render a video.

Sleek dark "black-tech" UI. Runs entirely on your machine.

![lyrics](https://img.shields.io/badge/lyrics-AI%20generated-7b5cff) ![ui](https://img.shields.io/badge/ui-black--tech-27e0d8) ![license](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Features

- **Two writing modes:**
  - **Forge from scratch** — type a mood, scene, or story; an LLM writes a full song with proper Suno section tags (`[Verse]`, `[Chorus]`, `[Bridge]`…).
  - **Co-write** — paste the few good lines you already wrote; the AI keeps them **verbatim** and builds the rest of the song around them, matching tone, rhyme, and meter. Tell it whether your lines are the chorus, a verse, the opening, etc.
- **Target length** — ask for "2-3 min", "short 90s", "radio edit"; section count is chosen to fit.
- **Pro songwriting controls** — set **tempo**, **musical key**, **vocal** (range/gender/duet), **song structure**, **stylistic reference** ("in the style of…"), and **lyrical subject**. All optional — leave blank for AI's choice.
- **Section rework** — not happy with the chorus? Rewrite just that section with a note ("more hopeful, add a metaphor") while the rest stays intact.
- **14 base genres** — Midnight R&B, Groove Pop, Rainy Jazz, Synthwave, City Pop, Dream Pop, Trap Soul, Bossa Nova, Lo-fi, Piano Ballad, Indie Rock, Chill EDM, Epic Cinematic, Soft Acoustic.
- **Playlist batch** — generate up to 20 distinct songs in one click.
- **Multi-language lyrics** — English, Korean, Japanese, Mandarin, Spanish, French.
- **Instrumental & explicit toggles.**
- **Copy & export** — one-click copy of Style / Lyrics, or export the batch as `.txt` / `.json`.
- **Video maker** — `make_video.sh` stitches a cover + mp3s into a 1080p waveform video for YouTube playlists.
- **Two LLM backends, zero secrets in code** (see below).

---

## 🚀 Quick start

```bash
git clone https://github.com/<you>/suno-song-studio.git
cd suno-song-studio
pip install -r requirements.txt
uvicorn server:app --port 5001
```

Open **http://localhost:5001** in your browser. Pick a genre, describe what you want, hit **Generate**.

---

## 🔌 LLM backend (pick one — no API key is stored in the code)

The server auto-selects a backend at runtime:

### Option A — Anthropic API key
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
uvicorn server:app --port 5001
```
Reads the key **from the environment only**. Never hard-code it. Use a `.env` file if you like — it is git-ignored.

### Option B — Claude Code CLI (no key needed)
Install [Claude Code](https://claude.com/claude-code) and log in, then just run the server. It shells out to `claude -p` using your existing login. The health badge shows which backend is active.

Optional env vars: `SUNO_MODEL` (default `claude-opus-4-8`), `SUNO_TIMEOUT` (CLI timeout, seconds).

---

## 🎬 From songs to a YouTube video

1. Generate a playlist in the web UI, copy each **Style** + **Lyrics** into Suno, and download the mp3s into a folder (e.g. `songs/midnight-rnb/`).
2. Make a cover image (1920×1080 recommended).
3. Render the video:

```bash
brew install ffmpeg          # one time
./scripts/make_video.sh cover.jpg songs/midnight-rnb/ midnight-rnb-1hr.mp4
```

This merges every mp3 in the folder and overlays an animated waveform — ready to upload.

---

## 📡 API

| Method | Path               | Body                                                                                                   |
|--------|--------------------|--------------------------------------------------------------------------------------------------------|
| GET    | `/api/health`      | —                                                                                                      |
| POST   | `/api/suno`        | `{idea, base_style?, instrumental?, explicit?, language?, tempo?, musical_key?, vocal?, structure?, reference?, theme_topic?}` |
| POST   | `/api/suno/batch`  | same + `{count: 1-20}`                                                                                  |
| POST   | `/api/suno/complete` | `{seed_lyrics, seed_role?, ...same controls as /api/suno}` — keeps your lines, writes the rest        |
| POST   | `/api/suno/rework` | `{lyrics, section, note?, title?, style?, language?}`                                                   |

Response: `{title, style, lyrics}` (batch wraps them in `{songs: [...]}`).

---

## 🔒 Privacy & safety

- **No accounts, tokens, or personal links are bundled.** The only outbound call is to your chosen Anthropic backend.
- API keys are read from environment variables; `.env`, `*.key`, and generated audio/video are all git-ignored.
- Lyrics are generated original — the prompt explicitly forbids copying existing songs. **You** are responsible for Suno's commercial-use terms (a paid Suno plan is required to monetize generated tracks on YouTube).

---

## 📁 Layout

```
suno-song-studio/
├─ server.py            # FastAPI backend (serves the UI + /api/suno)
├─ web/index.html       # Single-file web UI (no build step)
├─ scripts/make_video.sh# Cover + mp3s → 1080p waveform video
├─ requirements.txt
├─ .gitignore
└─ LICENSE              # MIT
```

## License

MIT — see [LICENSE](LICENSE).
