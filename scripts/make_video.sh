#!/bin/bash
# Turn a cover image + a folder of mp3s into a 1080p playlist video with a
# waveform animation at the bottom. Perfect for YouTube "X-hour playlist" videos.
#
# Usage:  ./scripts/make_video.sh <cover.jpg> <songs_dir> [output.mp4]
# Needs:  ffmpeg  (brew install ffmpeg)
set -euo pipefail

COVER="${1:?usage: make_video.sh <cover.jpg> <songs_dir> [output.mp4]}"
SONGS_DIR="${2:?songs directory required}"
OUT="${3:-playlist.mp4}"

TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# Build a sorted concat list of all mp3s
shopt -s nullglob
files=("$SONGS_DIR"/*.mp3)
[ ${#files[@]} -eq 0 ] && { echo "No .mp3 files in $SONGS_DIR"; exit 1; }
for f in "${files[@]}"; do echo "file '$(realpath "$f")'" >> "$TMP/list.txt"; done

echo "Merging ${#files[@]} tracks…"
ffmpeg -y -f concat -safe 0 -i "$TMP/list.txt" -c:a libmp3lame -q:a 0 "$TMP/full.mp3"

echo "Rendering video…"
ffmpeg -y -loop 1 -i "$COVER" -i "$TMP/full.mp3" \
  -filter_complex "\
    [0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080[bg]; \
    [1:a]showwaves=s=1600x120:mode=cline:colors=white@0.8[wave]; \
    [bg][wave]overlay=(W-w)/2:H-h-60[v]" \
  -map "[v]" -map 1:a \
  -c:v libx264 -preset medium -tune stillimage -crf 20 -pix_fmt yuv420p \
  -c:a aac -b:a 320k -shortest "$OUT"

echo "✅ Done: $OUT"
