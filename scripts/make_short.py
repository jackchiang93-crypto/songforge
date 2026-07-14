#!/usr/bin/env python3
"""
YouTube Shorts maker — turn one song into a vertical 9:16 clip (<=60s) with a
bouncing waveform, your logo, and an optional hook caption. Great for driving
traffic to the full playlist.

Usage:
  python3 make_short.py <song.mp3> <cover.jpg> <out.mp4> \
      [--start 30] [--dur 45] [--logo logo.png] [--hook "this R&B hits different"]

Notes:
  - cover is cropped to vertical 1080x1920 (a portrait image works best).
  - --start/--dur pick the slice of the song (defaults: 30s in, 45s long, capped 60).
  - hook caption needs no special font for Latin text; CJK supported via Pillow PNG.

Needs: ffmpeg, python3, Pillow
"""
import argparse, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
CJK = "/System/Library/Fonts/STHeiti Medium.ttc"
LAT = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
if not os.path.exists(LAT): LAT = CJK


def font(p, s):
    try: return ImageFont.truetype(p, s)
    except Exception: return ImageFont.load_default()


def has_cjk(t): return any(ord(c) > 0x2E80 for c in t)


def render_hook(text, out):
    """Big hook caption near the top, transparent 1080x1920."""
    # the bundled fonts have no emoji glyphs → strip emoji to avoid tofu boxes
    text = "".join(c for c in text if ord(c) < 0x1F000).strip()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = font(CJK if has_cjk(text) else LAT, 78)
    # word-wrap to width ~960
    words = list(text) if has_cjk(text) else text.split()
    lines, cur = [], ""
    for w in words:
        test = cur + w if has_cjk(text) else (cur + " " + w).strip()
        if d.textlength(test, font=f) > 960 and cur:
            lines.append(cur); cur = w
        else:
            cur = test
    if cur: lines.append(cur)
    y = 240
    for ln in lines:
        tw = d.textlength(ln, font=f)
        x = (W - tw) / 2
        d.text((x + 3, y + 3), ln, font=f, fill=(0, 0, 0, 170))      # shadow
        d.text((x, y), ln, font=f, fill=(255, 255, 255, 255))
        y += 96
    img.save(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song"); ap.add_argument("cover"); ap.add_argument("out")
    ap.add_argument("--start", type=float, default=30)
    ap.add_argument("--dur", type=float, default=45)
    ap.add_argument("--logo", default="")
    ap.add_argument("--hook", default="")
    a = ap.parse_args()
    dur = min(a.dur, 60)

    tmp = tempfile.mkdtemp()
    inputs = ["-ss", f"{a.start}", "-t", f"{dur}", "-i", a.song,  # 0: audio slice
              "-loop", "1", "-i", a.cover]                          # 1: cover
    idx = 2
    hook_idx = logo_idx = None
    if a.hook:
        hp = os.path.join(tmp, "hook.png"); render_hook(a.hook, hp)
        inputs += ["-loop", "1", "-i", hp]; hook_idx = idx; idx += 1
    if a.logo and os.path.exists(a.logo):
        inputs += ["-loop", "1", "-i", a.logo]; logo_idx = idx; idx += 1

    fc = [f"[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[bg]",
          f"[0:a]showwaves=s={W-120}x220:mode=cline:colors=white@0.9:rate=25[wave]",
          f"[bg][wave]overlay=(W-w)/2:(H-h)/2+120[v1]"]
    last = "v1"
    if hook_idx is not None:
        fc.append(f"[{last}][{hook_idx}:v]overlay=0:0[v2]"); last = "v2"
    if logo_idx is not None:
        fc.append(f"[{logo_idx}:v]scale=300:-1[lg]")
        fc.append(f"[{last}][lg]overlay=(W-w)/2:H-150[v3]"); last = "v3"
    fc.append(f"[{last}]null[v]")

    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(fc),
           "-map", "[v]", "-map", "0:a", "-r", "25",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k",
           "-t", f"{dur}", a.out]
    print(f"Rendering Short: {dur:.0f}s vertical…")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1200:]); sys.exit("ffmpeg failed")
    print("✅ Done:", a.out)


if __name__ == "__main__":
    main()
