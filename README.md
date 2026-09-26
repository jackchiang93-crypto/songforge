# 🎛️ SONGFORGE — Mureka Music Studio

**AI 歌曲生產工作台。** 描述方向後產出原創歌名、曲風與歌詞；審稿及人工選歌後，可送往 [Mureka 官方 API](https://platform.mureka.ai/docs/en/quickstart.html)生成音樂。YouTube 上傳仍暫緩。

Sleek dark "black-tech" UI. Runs entirely on your machine.

![lyrics](https://img.shields.io/badge/lyrics-AI%20generated-7b5cff) ![ui](https://img.shields.io/badge/ui-black--tech-27e0d8) ![license](https://img.shields.io/badge/license-MIT-green)

---

## ✨ Features

### 生產工作台（新版）

- **頻道方向**：記下聽眾、使用情境、聲音特色和避免的套路，批次企劃與創作使用同一份方向。
- **本機作品庫**：SQLite 保存歌曲、創作設定、版本、審稿結果和生產工作，位置為 `data/studio.sqlite3`。首次開啟會遷入舊瀏覽器草稿；保留舊資料備份。
- **背景批次**：設定 1–15 首，提交後設定鎖定。關閉或重新整理網頁不會停止伺服器上的工作。電腦與本機服務須持續運行。
- **恢復與重試**：伺服器重啟後標記中斷工作，可按「續作未完成項目」；成功作品不會重新生成。停止會讓目前處理中的歌曲完成後停止下一首。
- **獨立審稿**：每首批次歌曲另做一次模型審查，自然度、可唱性、hook、故事、風格各自評分並給出修改理由。80 分以上、各項至少 65 分且無基本檢查警告，標示可進人工選歌；不是音訊品質保證。
- **依建議改稿並複審**：一鍵呼叫改稿和審稿（兩次模型請求），另存新版本，原作與設定保留。審稿失敗仍保存草稿。
- **人工選歌**：入選後可匯出製作資料。「Mureka 待製作」支援官方 API 送件、進度查詢及試聽連結；未設定 key 時按鈕停用。
- **精準製作提示詞**：送件前逐首預覽實際 API 提示詞與可貼進 Mureka Co 的完整版本；包含曲風、歌曲專屬 hook／編曲、語言咬字、人聲定位、排除元素與混音方向。可整批套用固定主唱聲線描述，或在官方 API 中使用已有授權的 Vocal ID（O2 不支援）。文字描述只能增加一致性，不能保證同一個聲音。
- **本機 MP3 與批量準備**：完成的第一個 Mureka 版本會嘗試保存於 `data/audio/`，進度卡可直接試聽或下載。批量歌詞可產出 1–15 首、每首獨立審稿；可匯出整批 Mureka Co 提示詞。API 有額度時可送 1–15 首生成；桌面版 Gold 不等於 API 額度。桌面版目前仍須逐首送出，但成音後可用「匯入桌面版 MP3」直接把 `歌名-0.mp3` 複製進作品庫，無須 API 查詢或重複生成。

啟動時請只使用一個服務程序：`uvicorn server:app --host 127.0.0.1 --port 5001`。這版尚不支援多程序 worker 或跨機多人編輯。備份時先停止服務，再保存整個 `data/` 資料夾；也可從歌曲庫匯出 JSON。

生成歌詞仍使用選定模型額度：每批一次企劃，每首一次創作和一次審稿；副歌／標題相近會額外重試一次。Mureka 音樂生成另依其 API 帳號計費；審稿判斷是模型建議，音樂成品仍須人工試聽。

- **Two writing modes:**
  - **Forge from scratch** — type a mood, scene, or story; an LLM writes a full song with proper Suno section tags (`[Verse]`, `[Chorus]`, `[Bridge]`…).
  - **Co-write** — paste the few good lines you already wrote; the AI keeps them **verbatim** and builds the rest of the song around them, matching tone, rhyme, and meter. Tell it whether your lines are the chorus, a verse, the opening, etc.
- **Target length** — ask for "2-3 min", "short 90s", "radio edit"; section count is chosen to fit.
- **Maps to Suno Advanced mode** — output is split into the fields Suno's Advanced create panel expects: **Styles**, **Exclude styles**, **Vocal Gender**, **Weirdness %**, **Style Influence %**, **Lyrics**, **Title**. Copy each across and check the settings in Suno.
- **Suno Advanced checklist** — each song now shows Lyrics, Styles, Exclude styles, Vocal Gender, Duration Auto/Custom, Max Mode, Weirdness, Style Influence, Variety and Personalize. Audio, Voice and Inspo notes remind you which assets to select using Suno's `+` controls. Those assets and toggles are not transferred automatically. Generated song length and sound are approximate until you audition the result.
- **Quality review** — the songwriter prompt favors a focused style, consistent musical direction, singable lines and a repeated hook. Short lyrics, missing chorus, long Styles and incomplete custom duration raise review warnings without blocking your work.

- **Pro songwriting controls** — set **instruments**, **tempo**, **musical key**, **vocal** (range/gender/duet), **song structure**, **target length**, **stylistic reference** ("in the style of…"), and **lyrical subject**. All optional — leave blank for AI's choice.
- **Section rework** — not happy with the chorus? Rewrite just that section with a note ("more hopeful, add a metaphor") while the rest stays intact.
- **Genre presets and free-form styles** — start with a preset or describe any Japanese, Chinese, mixed or other sound in the custom style field.
- **AI creative direction** — the idea button asks the model for a new concept; it no longer rotates through a fixed idea list.
- **Smart playlist** — one click plans and drafts up to 15 songs with different stories, scenes, hooks and arrangements. It checks for repeated titles and highly similar choruses, then retries. This reduces repetition but cannot guarantee artistic uniqueness.
- **Playlist languages** — enter up to six language names separated by commas; the planner distributes them across the songs. A `+` joins two languages within each song (for example, `中文+English`).
- **Multi-language lyrics** — English, Korean, Japanese, Mandarin, Spanish, French, plus Chinese–English and Japanese–English songs with different languages across sections.
- **Instrumental & explicit toggles.**
- **Persistent library** — songs are saved to the local SQLite library with browser recovery copies; ⭐ favorite, 🔄 regenerate as a new version, ✕ remove, 📋 copy-all.
- **Live batch progress** — durable background jobs show per-song completion and recover failed work without regenerating successful songs.
- **Mobile-friendly** — sticky bottom create bar on small screens.
- **Copy & export** — one-click copy of Style / Lyrics (or the whole song), export the batch as `.txt` / `.json`.
- **Video maker** — `make_video.sh` stitches a cover + mp3s into a 1080p waveform video for YouTube playlists.
- **Selectable writing providers** — Codex CLI (ChatGPT account), Claude CLI, or Anthropic API key (see below).

### Suno Advanced 欄位對照

| Suno 畫面 | SONGFORGE 操作 |
|---|---|
| Lyrics、Styles、Exclude styles | 從生成歌曲卡各自複製貼上；純音樂可在 Suno 留空歌詞欄 |
| Vocal Gender、Weirdness、Style Influence | 在歌曲卡看建議值，再於 Suno More Options 設定 |
| Duration Auto / Custom | 專業微調中選 Auto 或輸入秒數；實際時長以 Suno 生成結果為準 |
| Max Mode、Variety、Personalize | 在專業微調設定偏好，於 Suno 對照製作卡手動點選；Variety 的 Low / Normal / High 是操作提示，不代表精確滑桿刻度 |
| Audio、Voice、Inspo | 記下參考素材名稱／描述，並在 Suno 頂部的 `+` 手動選取素材 |

生成後先檢查品質提醒，再檢查 Styles 中的 BPM、人聲與 Lyrics 是否一致。建議對同一首歌試聽多個 Suno 版本，記錄評分並保留最佳版本；SONGFORGE 無法保證 Suno 一定遵循每項文字指令。

### 10–15 首歌單與 Mureka 自動化

在「想做什麼歌」填一個企劃，在「自訂曲風」輸入任意風格（例如和風 city pop 或國風 R&B），於「批次製作歌單」填語言與數量，按「智能生成歌單」。逗號分隔不同歌曲的語言；`中文+English` 或 `日本語+English` 表示同一首混合雙語。系統先讓 AI 規劃每首的場景、情緒轉折、hook 與編曲，再逐首寫歌詞，檢查相似內容並重試。若你參考熱門音樂，請在「參考熱門歌曲的聲音特徵」填你觀察到的節奏、樂器與段落；單貼網址不會讓系統聽到音訊，也不應照抄歌詞或旋律。生成後在「歌曲庫」選擇入選歌曲，它們會出現在「Mureka 待製作」清單。

工作台已依 [Mureka 官方文件](https://platform.mureka.ai/docs/api/operations/post-v1-song-generate.html)備好送件佇列、狀態查詢和音檔連結回存。桌面版完成的音樂可直接從 Mureka Co 本機音檔匯入；API 任務 ID 匯入仍保留給官方 API 生成的工作。`MUREKA_API_KEY` 已可驗證，但 API 帳號若餘額為 $0，生成仍會收到 429；桌面版 Pro Gold 與 API 餘額是不同使用路徑。詳見 [接通與測試紀錄](docs/MUREKA_API_INTEGRATION.md)。舊版 Suno Advanced 控制項仍保留在歌曲卡供辨識，但不會經 Mureka API 傳送。

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

The UI lets you select a provider per request. The default is **GPT / Codex CLI**. `auto` in a direct API request preserves the older behavior (Anthropic API key if configured, otherwise Claude CLI).

### Option A — Codex CLI with your ChatGPT login

Install and sign in to [Codex](https://developers.openai.com/codex/cli/), then select **GPT / Codex CLI** in the page. The backend invokes `codex exec` using the locally signed-in account. It uses that account's Codex usage allowance; it does not use the Claude account or an OpenAI API key. Check locally with `codex login status`. Provider availability appears in the status badge; `codex exec` still requires a successful login to work.

### Option B — Anthropic API key
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
uvicorn server:app --port 5001
```
Reads the key **from the environment only**. Never hard-code it. Use a `.env` file if you like — it is git-ignored.

### Option C — Claude Code CLI (no key needed)
Install [Claude Code](https://claude.com/claude-code) and log in, then select **Claude CLI**. It shells out to `claude -p` using your existing login.

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

### YouTube 發布工作流

SONGFORGE 的歌曲卡可記錄 Suno 連結、品質評分、影片狀態、YouTube 連結和觀看數。資料保存在本機作品庫；用 `.json` 定期備份。每首歌可下載 `metadata.txt`，內含標題、說明與標籤。發布文案是初稿，請按實際歌曲和影片修改。此階段先完成音樂製作，上傳功能維持暫緩。

1. 在 SONGFORGE 產生歌曲，複製 Styles / Lyrics 到 Suno，試聽後下載音檔並填上 Suno 連結、評分。
2. 用 `scripts/make_video.sh` 產生長影片；Shorts 可用 `scripts/make_short.py`。在卡片下載 metadata，檢查標題、說明、標籤與影片內容。
3. 可手動登入 YouTube Studio 上傳；或使用下列工具。工具預設只預覽，加入 `--upload` 才會以 **private** 上傳，公開發布仍由你在 YouTube Studio 決定。

```bash
pip install google-api-python-client google-auth-oauthlib
python3 scripts/youtube_upload.py output/playlist.mp4 output/metadata.txt \
  --client-secrets client_secrets.json
python3 scripts/youtube_upload.py output/playlist.mp4 output/metadata.txt \
  --client-secrets client_secrets.json --upload
```

把歌曲卡下載的 `metadata.txt` 放到上述位置，或在命令中改用下載檔的實際路徑。影片也請改成實際輸出的 MP4 路徑。

第一次上傳須在 Google Cloud 啟用 YouTube Data API v3，建立「電腦應用程式」OAuth 用戶端，將下載的 JSON 放在 `client_secrets.json`。登入時確認選到正確頻道。憑證與本機 token 已加入 `.gitignore`。上傳後將影片連結貼回歌曲卡，設為「已發布」，並定期填入觀看數。API 配額與應用驗證限制以 Google Cloud 專案顯示為準。

Suno 的商業使用權、YouTube 的營利資格及內容政策都需以你實際帳戶狀態確認；此工具不保證流量或營利。

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

- **No accounts, tokens, or personal links are bundled.** Song requests go to the writing provider you select (Codex, Claude, or Anthropic API); the library is stored locally in SQLite with browser recovery copies.
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
