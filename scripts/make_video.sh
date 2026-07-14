#!/bin/bash
# YouTube playlist video: cover image fills 1080p, an audio-reactive waveform
# bounces along the bottom, with an optional logo (top-left) and an animated
# SUBSCRIBE button that pulses in periodically. No burned text (so no font /
# CJK problems). Video length = total length of all the mp3s.
#
# Usage:
#   ./scripts/make_video.sh <cover.jpg> <songs_dir> [out.mp4] [logo.png] [subscribe.png]
#
# Example:
#   ./scripts/make_video.sh ~/Downloads/Music/cover.jpg ~/Downloads/Music \
#       ~/Downloads/playlist.mp4 ~/Downloads/logo.png ~/Downloads/subscribe.png
#
# Needs: ffmpeg  (brew install ffmpeg)
set -euo pipefail

COVER="${1:?usage: make_video.sh <cover.jpg> <songs_dir> [out.mp4] [logo.png] [subscribe.png]}"
SONGS_DIR="${2:?songs directory required}"
OUT="${3:-playlist.mp4}"
LOGO="${4:-}"
SUB="${5:-}"

[ -f "$COVER" ] || { echo "Cover not found: $COVER"; exit 1; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# merge all mp3s (sorted by filename)
shopt -s nullglob
files=("$SONGS_DIR"/*.mp3)
[ ${#files[@]} -eq 0 ] && { echo "No .mp3 files in $SONGS_DIR"; exit 1; }
printf '%s\n' "${files[@]}" | sort | while read -r f; do
  printf "file '%s'\n" "$(realpath "$f")" >> "$TMP/list.txt"
done
echo "Merging ${#files[@]} tracks…"
ffmpeg -y -f concat -safe 0 -i "$TMP/list.txt" -c:a libmp3lame -q:a 0 "$TMP/full.mp3"

# inputs: cover, audio, [logo], [subscribe]
INPUTS=(-loop 1 -i "$COVER" -i "$TMP/full.mp3")
idx=2
LOGO_IDX=""; SUB_IDX=""
if [ -n "$LOGO" ] && [ -f "$LOGO" ]; then INPUTS+=(-loop 1 -i "$LOGO"); LOGO_IDX=$idx; idx=$((idx+1)); fi
if [ -n "$SUB" ] && [ -f "$SUB" ]; then INPUTS+=(-loop 1 -i "$SUB"); SUB_IDX=$idx; idx=$((idx+1)); fi

# filtergraph: cover + bouncing waveform, then logo + subscribe
FC="[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,setsar=1[bg];"
FC+="[1:a]showwaves=s=1700x140:mode=cline:colors=white@0.9:rate=25[wave];"
FC+="[bg][wave]overlay=(W-w)/2:H-h-60[v1]"
last="v1"
if [ -n "$LOGO_IDX" ]; then
  FC+=";[${LOGO_IDX}:v]scale=240:-1[lg];[${last}][lg]overlay=46:38[v2]"; last="v2"
fi
if [ -n "$SUB_IDX" ]; then
  FC+=";[${last}][${SUB_IDX}:v]overlay=(W-w)/2:H*0.40:enable='lt(mod(t,200),8)'[v3]"; last="v3"
fi

echo "Rendering video…"
ffmpeg -y "${INPUTS[@]}" -filter_complex "${FC};[${last}]null[v]" \
  -map "[v]" -map 1:a -r 25 \
  -c:v libx264 -preset veryfast -crf 22 -pix_fmt yuv420p \
  -c:a aac -b:a 320k -shortest "$OUT"

echo "✅ Done: $OUT"
