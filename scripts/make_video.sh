#!/bin/bash
# Turn a cover image + a folder of mp3s into a YouTube playlist video:
# the cover fills 1920x1080, an optional title is burned on, and a waveform
# animates along the bottom. Video length = total length of all the mp3s.
#
# Usage:
#   ./scripts/make_video.sh <cover.jpg> <songs_dir> [output.mp4] ["Title text"]
#
# Examples:
#   ./scripts/make_video.sh ~/Downloads/cover.jpg ~/Downloads/Music
#   ./scripts/make_video.sh ~/Downloads/cover.jpg ~/Downloads/Music out.mp4 "R&B Playlist 133 | Chill Work BGM"
#
# Needs: ffmpeg  (brew install ffmpeg)
set -euo pipefail

COVER="${1:?usage: make_video.sh <cover.jpg> <songs_dir> [output.mp4] [title]}"
SONGS_DIR="${2:?songs directory required}"
OUT="${3:-playlist.mp4}"
TITLE="${4:-}"

[ -f "$COVER" ] || { echo "Cover not found: $COVER"; exit 1; }

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# Build a sorted concat list of all mp3s
shopt -s nullglob
files=("$SONGS_DIR"/*.mp3)
[ ${#files[@]} -eq 0 ] && { echo "No .mp3 files in $SONGS_DIR"; exit 1; }
printf '%s\n' "${files[@]}" | sort | while read -r f; do
  printf "file '%s'\n" "$(realpath "$f")" >> "$TMP/list.txt"
done
echo "Merging ${#files[@]} tracks…"
ffmpeg -y -f concat -safe 0 -i "$TMP/list.txt" -c:a libmp3lame -q:a 0 "$TMP/full.mp3"

# Copy the CJK font to a space-free path so drawtext is happy
FONT_SRC="/System/Library/Fonts/STHeiti Medium.ttc"
[ -f "$FONT_SRC" ] || FONT_SRC="/System/Library/Fonts/AppleSDGothicNeo.ttc"
cp "$FONT_SRC" "$TMP/font.ttc"

# Burning text needs ffmpeg built with libfreetype (drawtext). If unavailable,
# skip the title — bake it into the cover image instead (Canva/Preview).
if [ -n "$TITLE" ] && ! ffmpeg -hide_banner -filters 2>/dev/null | grep -q ' drawtext '; then
  echo "⚠ This ffmpeg has no drawtext filter — skipping burned title."
  echo "  Put the title text into the cover image instead."
  TITLE=""
fi

# Build the video filter
BASE="[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1[bg];[1:a]showwaves=s=1700x120:mode=cline:colors=white@0.85[wave];[bg][wave]overlay=(W-w)/2:H-h-50"
if [ -n "$TITLE" ]; then
  SAFE=$(printf '%s' "$TITLE" | sed "s/:/\\\\:/g")   # escape colons for drawtext
  FILTER="${BASE}[bgw];[bgw]drawtext=fontfile=${TMP}/font.ttc:text='${SAFE}':fontcolor=white:fontsize=56:borderw=3:bordercolor=black@0.85:x=(w-text_w)/2:y=h-235[v]"
else
  FILTER="${BASE}[v]"
fi

echo "Rendering video…"
ffmpeg -y -loop 1 -i "$COVER" -i "$TMP/full.mp3" \
  -filter_complex "$FILTER" \
  -map "[v]" -map 1:a \
  -c:v libx264 -preset medium -tune stillimage -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 320k -shortest "$OUT"

echo "✅ Done: $OUT"
