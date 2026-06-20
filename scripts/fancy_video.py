#!/usr/bin/env python3
"""
Fancy playlist video maker for YouTube music channels.

Builds a polished 1080p video from a cover image + a folder of mp3s, with:
  - a "now playing" player bar (song title, prev/pause/next + heart icons,
    and a progress dot that slides across as each track plays)
  - an animated SUBSCRIBE button that pulses in periodically
  - your channel logo in the corner (optional)

No ffmpeg `drawtext` needed — every text/graphic is pre-rendered with Pillow
(supports CJK), then composited by ffmpeg. Fully local, reusable.

Usage:
  python3 fancy_video.py <cover.jpg> <songs_dir> <out.mp4> [--logo logo.png] [--channel "Name"]
Needs: ffmpeg, python3, Pillow  (pip install Pillow)
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
CJK_FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
LAT_FONT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
if not os.path.exists(LAT_FONT):
    LAT_FONT = CJK_FONT


def font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def probe_dur(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def rounded(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


# ── overlay renderers ─────────────────────────────────────────────────────────
PANEL_W, PANEL_H = 1040, 150
PANEL_X = (W - PANEL_W) // 2
PANEL_Y = 880
TRACK_PAD = 70
TRACK_Y = PANEL_Y + PANEL_H - 34
TRACK_X1 = PANEL_X + TRACK_PAD
TRACK_X2 = PANEL_X + PANEL_W - TRACK_PAD


def render_player_bar(title, out):
    """Static player panel (title + transport icons + dotted track), transparent 1920x1080."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded(d, (PANEL_X, PANEL_Y, PANEL_X + PANEL_W, PANEL_Y + PANEL_H), 26, (10, 10, 16, 150))
    # title
    f = font(CJK_FONT, 40)
    tw = d.textlength(title, font=f)
    d.text(((W - tw) / 2, PANEL_Y + 22), title, font=f, fill=(255, 255, 255, 235))
    # transport row (centered)
    cy = PANEL_Y + 92
    cx = W / 2
    white = (255, 255, 255, 235)
    # heart (left)
    d.text((PANEL_X + 60, cy - 16), "♥", font=font(CJK_FONT, 34), fill=(255, 90, 110, 235))
    # prev  |◀
    d.polygon([(cx - 150, cy), (cx - 122, cy - 16), (cx - 122, cy + 16)], fill=white)
    d.rectangle([cx - 156, cy - 16, cx - 150, cy + 16], fill=white)
    # pause (circle + two bars)
    d.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], fill=white)
    d.rectangle([cx - 12, cy - 14, cx - 4, cy + 14], fill=(10, 10, 16, 255))
    d.rectangle([cx + 4, cy - 14, cx + 12, cy + 14], fill=(10, 10, 16, 255))
    # next  ▶|
    d.polygon([(cx + 150, cy), (cx + 122, cy - 16), (cx + 122, cy + 16)], fill=white)
    d.rectangle([cx + 150, cy - 16, cx + 156, cy + 16], fill=white)
    # dotted progress track
    x = TRACK_X1
    while x < TRACK_X2:
        d.ellipse([x, TRACK_Y - 2, x + 4, TRACK_Y + 2], fill=(255, 255, 255, 150))
        x += 16
    img.save(out)


def render_dot(out):
    img = Image.new("RGBA", (28, 28), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([4, 4, 24, 24], fill=(255, 255, 255, 255))
    img.save(out)


SUB_W, SUB_H = 560, 150


def render_subscribe(out):
    img = Image.new("RGBA", (SUB_W, SUB_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded(d, (10, 30, SUB_W - 10, SUB_H - 30), 20, (210, 30, 36, 235))
    d.rounded_rectangle((10, 30, SUB_W - 10, SUB_H - 30), radius=20, outline=(255, 200, 60, 230), width=3)
    # thumbs up (simple)
    d.rectangle([60, 78, 80, 110], fill=(255, 255, 255, 255))
    d.rounded_rectangle([84, 64, 120, 110], radius=6, fill=(255, 255, 255, 255))
    # text
    f = font(LAT_FONT, 46)
    d.text((150, 50), "SUBSCRIBE", font=f, fill=(255, 255, 255, 255))
    # bell
    bx = SUB_W - 70
    d.pieslice([bx - 22, 58, bx + 22, 104], 180, 360, fill=(255, 255, 255, 255))
    d.rectangle([bx - 22, 80, bx + 22, 100], fill=(255, 255, 255, 255))
    d.ellipse([bx - 7, 104, bx + 7, 118], fill=(255, 255, 255, 255))
    img.save(out)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cover")
    ap.add_argument("songs_dir")
    ap.add_argument("out")
    ap.add_argument("--logo", default="")
    ap.add_argument("--channel", default="")
    a = ap.parse_args()

    songs = sorted(f for f in os.listdir(a.songs_dir) if f.lower().endswith(".mp3"))
    if not songs:
        sys.exit("No mp3 files found")
    paths = [os.path.join(a.songs_dir, f) for f in songs]
    durs = [probe_dur(p) for p in paths]
    titles = [re.sub(r"^\d+_", "", os.path.splitext(f)[0]) for f in songs]

    tmp = tempfile.mkdtemp()
    # concat audio
    listf = os.path.join(tmp, "list.txt")
    with open(listf, "w") as fh:
        for p in paths:
            fh.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))
    full = os.path.join(tmp, "full.mp3")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listf,
                    "-c:a", "libmp3lame", "-q:a", "0", full],
                   check=True, capture_output=True)

    # render overlays
    dot = os.path.join(tmp, "dot.png"); render_dot(dot)
    sub = os.path.join(tmp, "sub.png"); render_subscribe(sub)
    bars = []
    starts, t = [], 0.0
    for i, title in enumerate(titles):
        bp = os.path.join(tmp, f"bar{i}.png"); render_player_bar(title, bp)
        bars.append(bp); starts.append(t); t += durs[i]
    total = t

    # build ffmpeg inputs
    inputs = ["-loop", "1", "-i", a.cover, "-i", full,
              "-i", dot, "-i", sub]
    bar_idx0 = 4
    for bp in bars:
        inputs += ["-loop", "1", "-i", bp]
    logo_idx = None
    if a.logo and os.path.exists(a.logo):
        logo_idx = bar_idx0 + len(bars)
        inputs += ["-loop", "1", "-i", a.logo]

    # filtergraph
    fc = ["[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1[bg]"]
    last = "bg"
    # per-song bars (enabled during the song)
    for i, bp in enumerate(bars):
        s, e = starts[i], starts[i] + durs[i]
        idx = bar_idx0 + i
        fc.append(f"[{last}][{idx}:v]overlay=0:0:enable='between(t,{s:.2f},{e:.2f})'[b{i}]")
        last = f"b{i}"
    # moving progress dot per song
    for i in range(len(bars)):
        s, dur = starts[i], max(durs[i], 0.1)
        e = s + dur
        xexpr = f"{TRACK_X1}+(t-{s:.2f})/{dur:.2f}*{TRACK_X2 - TRACK_X1}"
        fc.append(f"[{last}][2:v]overlay=x='{xexpr}':y={TRACK_Y - 12}:enable='between(t,{s:.2f},{e:.2f})'[d{i}]")
        last = f"d{i}"
    # subscribe: pulse in 8s every 200s
    subx = (W - SUB_W) // 2
    fc.append(f"[{last}][3:v]overlay={subx}:430:enable='lt(mod(t,200),8)'[s1]")
    last = "s1"
    # logo top-left
    if logo_idx is not None:
        fc.append(f"[{logo_idx}:v]scale=180:-1[lg]")
        fc.append(f"[{last}][lg]overlay=44:40[v]")
    else:
        fc.append(f"[{last}]copy[v]")

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
           "-map", "[v]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
           "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "320k",
           "-t", f"{total:.2f}", a.out]
    print(f"Rendering {len(songs)} tracks, {total/60:.1f} min…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        sys.exit("ffmpeg failed")
    print("✅ Done:", a.out)


if __name__ == "__main__":
    main()
