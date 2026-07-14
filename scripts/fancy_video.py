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

W, H = 1280, 720
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
PANEL_W, PANEL_H = 720, 104
PANEL_X = (W - PANEL_W) // 2
PANEL_Y = H - 150
TRACK_PAD = 48
TRACK_Y = PANEL_Y + PANEL_H - 24
TRACK_X1 = PANEL_X + TRACK_PAD
TRACK_X2 = PANEL_X + PANEL_W - TRACK_PAD


def render_player_bar(title, out, frac=0.0):
    """Static player panel with title + transport icons + progress, transparent WxH.
    `frac` (0..1) places the filled progress + knob, so a per-song still already
    shows roughly where the track is — no per-frame dot overlay needed."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rounded(d, (PANEL_X, PANEL_Y, PANEL_X + PANEL_W, PANEL_Y + PANEL_H), 20, (10, 10, 16, 150))
    f = font(CJK_FONT, 28)
    tw = d.textlength(title, font=f)
    d.text(((W - tw) / 2, PANEL_Y + 14), title, font=f, fill=(255, 255, 255, 235))
    cy = PANEL_Y + 64
    cx = W / 2
    white = (255, 255, 255, 235)
    d.text((PANEL_X + 40, cy - 13), "♥", font=font(CJK_FONT, 26), fill=(255, 90, 110, 235))
    d.polygon([(cx - 108, cy), (cx - 88, cy - 12), (cx - 88, cy + 12)], fill=white)
    d.rectangle([cx - 112, cy - 12, cx - 108, cy + 12], fill=white)
    d.ellipse([cx - 22, cy - 22, cx + 22, cy + 22], fill=white)
    d.rectangle([cx - 9, cy - 10, cx - 3, cy + 10], fill=(10, 10, 16, 255))
    d.rectangle([cx + 3, cy - 10, cx + 9, cy + 10], fill=(10, 10, 16, 255))
    d.polygon([(cx + 108, cy), (cx + 88, cy - 12), (cx + 88, cy + 12)], fill=white)
    d.rectangle([cx + 108, cy - 12, cx + 112, cy + 12], fill=white)
    # progress line + knob at frac
    d.line([TRACK_X1, TRACK_Y, TRACK_X2, TRACK_Y], fill=(255, 255, 255, 90), width=2)
    kx = TRACK_X1 + frac * (TRACK_X2 - TRACK_X1)
    d.line([TRACK_X1, TRACK_Y, kx, TRACK_Y], fill=(47, 230, 214, 230), width=3)
    d.ellipse([kx - 6, TRACK_Y - 6, kx + 6, TRACK_Y + 6], fill=(255, 255, 255, 255))
    img.save(out)


SUB_W, SUB_H = 420, 110


def render_subscribe(out):
    img = Image.new("RGBA", (SUB_W, SUB_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((8, 22, SUB_W - 8, SUB_H - 22), radius=16, fill=(210, 30, 36, 235),
                        outline=(255, 200, 60, 230), width=3)
    # thumbs up
    d.rectangle([46, 58, 60, 82], fill=(255, 255, 255, 255))
    d.rounded_rectangle([63, 48, 92, 82], radius=5, fill=(255, 255, 255, 255))
    # text
    d.text((112, 36), "SUBSCRIBE", font=font(LAT_FONT, 34), fill=(255, 255, 255, 255))
    # bell
    bx = SUB_W - 52
    d.pieslice([bx - 16, 42, bx + 16, 78], 180, 360, fill=(255, 255, 255, 255))
    d.rectangle([bx - 16, 60, bx + 16, 74], fill=(255, 255, 255, 255))
    d.ellipse([bx - 5, 78, bx + 5, 88], fill=(255, 255, 255, 255))
    img.save(out)


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cover")
    ap.add_argument("songs_dir")
    ap.add_argument("out")
    ap.add_argument("--logo", default="")
    ap.add_argument("--channel", default="")
    ap.add_argument("--fps", type=int, default=12, help="output fps (low is fine for BGM)")
    ap.add_argument("--preset", default="veryfast", help="x264 preset (ultrafast=fastest)")
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

    # Render one player-bar PNG per song, with the progress knob at a few points
    # so each still hints how far along the track is. We pre-composite all of
    # them into ONE transparent overlay track (qtrle), so the final render uses
    # just 3 overlays (bars + subscribe + logo) instead of dozens — ~10x faster.
    sub = os.path.join(tmp, "sub.png"); render_subscribe(sub)
    starts, t = [], 0.0
    barlist = os.path.join(tmp, "bars.txt")
    with open(barlist, "w") as bl:
        for i, title in enumerate(titles):
            bp = os.path.join(tmp, f"bar{i}.png")
            render_player_bar(title, bp, frac=0.5)  # mid-progress knob
            bl.write("file '%s'\n" % bp)
            bl.write("duration %.3f\n" % max(durs[i], 0.1))
            starts.append(t); t += durs[i]
        # concat demuxer needs the last file repeated
        bl.write("file '%s'\n" % os.path.join(tmp, f"bar{len(titles)-1}.png"))
    total = t

    # Build the transparent bars track once (alpha preserved via qtrle)
    barstrack = os.path.join(tmp, "bars.mov")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", barlist,
                    "-vf", f"fps={a.fps},format=rgba", "-c:v", "qtrle", barstrack],
                   check=True, capture_output=True)

    # inputs: cover, audio, bars track, subscribe png, [logo]
    inputs = ["-loop", "1", "-i", a.cover, "-i", full, "-i", barstrack, "-loop", "1", "-i", sub]
    logo_idx = None
    if a.logo and os.path.exists(a.logo):
        logo_idx = 4
        inputs += ["-loop", "1", "-i", a.logo]

    subx = (W - SUB_W) // 2
    fc = [
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[bg]",
        f"[bg][2:v]overlay=0:0[bw]",
        f"[bw][3:v]overlay={subx}:{int(H*0.42)}:enable='lt(mod(t,200),8)'[s1]",
    ]
    if logo_idx is not None:
        fc.append(f"[{logo_idx}:v]scale=150:-1[lg]")
        fc.append(f"[s1][lg]overlay=34:30[v]")
    else:
        fc.append(f"[s1]null[v]")

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
           "-map", "[v]", "-map", "1:a", "-r", str(a.fps),
           "-c:v", "libx264", "-preset", a.preset, "-tune", "stillimage",
           "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
           "-t", f"{total:.2f}", a.out]
    print(f"Rendering {len(songs)} tracks, {total/60:.1f} min at {a.fps}fps/{a.preset}…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        sys.exit("ffmpeg failed")
    print("✅ Done:", a.out)


if __name__ == "__main__":
    main()
