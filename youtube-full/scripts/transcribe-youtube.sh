#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--language CODE] [--model PATH] YOUTUBE_URL [OUTPUT_DIRECTORY]\n' "$0" >&2
}

language="auto"
model_file=""
verify_default_model="false"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --language)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      language="$2"
      shift 2
      ;;
    --model)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      model_file="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      printf 'Unknown option: %s\n' "$1" >&2
      usage
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || { usage; exit 2; }

video_url="$1"
requested_output_dir="${2:-}"

for executable in yt-dlp ffmpeg curl; do
  command -v "$executable" >/dev/null 2>&1 || {
    printf 'Missing dependency: %s\n' "$executable" >&2
    exit 1
  }
done

whisper_command=""
for candidate in whisper-cli whisper-cpp; do
  if command -v "$candidate" >/dev/null 2>&1; then
    whisper_command="$candidate"
    break
  fi
done
[ -n "$whisper_command" ] || {
  printf 'Missing dependency: whisper-cli (install whisper-cpp)\n' >&2
  exit 1
}

if [ -n "$requested_output_dir" ]; then
  output_dir="$requested_output_dir"
else
  video_id="$(yt-dlp --no-playlist --skip-download --print '%(id)s' "$video_url")"
  transcripts_root="${YOUTUBE_TRANSCRIPTS_DIR:-$PWD/yt-transcripts}"
  output_dir="$transcripts_root/$video_id"
fi

if [ -z "$model_file" ]; then
  cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}"
  model_file="$cache_root/whisper/ggml-large-v3-turbo-q5_0.bin"
  verify_default_model="true"
fi

model_url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
model_sha256="394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
mkdir -p "$output_dir" "$(dirname "$model_file")"

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    printf 'Missing SHA-256 utility: shasum or sha256sum\n' >&2
    return 1
  fi
}

if [ ! -s "$model_file" ]; then
  partial_model="${model_file}.partial"
  printf 'Downloading local Whisper model to %s\n' "$model_file"
  curl --fail --location --retry 3 --continue-at - --output "$partial_model" "$model_url"
  actual_sha256="$(sha256_file "$partial_model")"
  if [ "$actual_sha256" != "$model_sha256" ]; then
    printf 'Whisper model checksum mismatch; refusing to use the download.\n' >&2
    exit 1
  fi
  mv "$partial_model" "$model_file"
fi

if [ "$verify_default_model" = "true" ]; then
  actual_sha256="$(sha256_file "$model_file")"
  if [ "$actual_sha256" != "$model_sha256" ]; then
    printf 'Cached Whisper model checksum mismatch: %s\n' "$model_file" >&2
    exit 1
  fi
fi

printf 'Reading video metadata...\n'
yt-dlp --no-playlist --skip-download --dump-single-json "$video_url" > "$output_dir/video.json"

printf 'Downloading audio...\n'
source_audio="$(yt-dlp \
  --no-playlist \
  --format 'bestaudio/best' \
  --extract-audio \
  --audio-format m4a \
  --audio-quality 0 \
  --output "$output_dir/source.%(ext)s" \
  --print after_move:filepath \
  "$video_url")"

[ -s "$source_audio" ] || {
  printf 'Audio download did not produce a file.\n' >&2
  exit 1
}

printf 'Converting audio for Whisper...\n'
ffmpeg -hide_banner -loglevel error -y \
  -i "$source_audio" \
  -ar 16000 -ac 1 -c:a pcm_s16le \
  "$output_dir/audio.wav"

printf 'Transcribing complete video with local Whisper...\n'
"$whisper_command" \
  --model "$model_file" \
  --file "$output_dir/audio.wav" \
  --language "$language" \
  --output-txt \
  --output-srt \
  --output-json \
  --output-file "$output_dir/transcript"

printf 'Done. Outputs:\n'
printf '%s\n' \
  "$output_dir/transcript.txt" \
  "$output_dir/transcript.srt" \
  "$output_dir/transcript.json" \
  "$output_dir/video.json"

collection_root="$(dirname "$output_dir")"
if [ "$(basename "$collection_root")" = "yt-transcripts" ]; then
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  python3 "$script_dir/rebuild-index.py" "$collection_root"
fi
