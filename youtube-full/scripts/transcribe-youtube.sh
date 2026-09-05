#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<EOF
Usage: $0 [OPTIONS] YOUTUBE_URL [OUTPUT_DIRECTORY]

Options:
  --language CODE              Spoken language (default: auto)
  --model PATH                 whisper.cpp GGML model
  --diarize                    Identify speaker turns locally with pyannote
  --diarization-model ID|PATH  Hugging Face model ID or downloaded local directory
  --diarization-device DEVICE  cpu, cuda, or mps (default: cpu)
  --num-speakers N             Exact number of speakers
  --min-speakers N             Minimum number of speakers
  --max-speakers N             Maximum number of speakers
  -h, --help                   Show this help
EOF
}

language="auto"
model_file=""
verify_default_model="false"
diarize="false"
diarization_model="${YOUTUBE_DIARIZATION_MODEL:-pyannote/speaker-diarization-community-1}"
diarization_device="${YOUTUBE_DIARIZATION_DEVICE:-cpu}"
num_speakers=""
min_speakers=""
max_speakers=""

positive_integer() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "$1" -gt 0 ]
}

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
    --diarize)
      diarize="true"
      shift
      ;;
    --diarization-model)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      diarization_model="$2"
      diarize="true"
      shift 2
      ;;
    --diarization-device)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      diarization_device="$2"
      diarize="true"
      shift 2
      ;;
    --num-speakers)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      positive_integer "$2" || { printf '%s\n' '--num-speakers must be a positive integer.' >&2; exit 2; }
      num_speakers="$2"
      diarize="true"
      shift 2
      ;;
    --min-speakers)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      positive_integer "$2" || { printf '%s\n' '--min-speakers must be a positive integer.' >&2; exit 2; }
      min_speakers="$2"
      diarize="true"
      shift 2
      ;;
    --max-speakers)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      positive_integer "$2" || { printf '%s\n' '--max-speakers must be a positive integer.' >&2; exit 2; }
      max_speakers="$2"
      diarize="true"
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

case "$diarization_device" in
  cpu|cuda|mps) ;;
  *) printf 'Unsupported diarization device: %s\n' "$diarization_device" >&2; exit 2 ;;
esac

if [ -n "$num_speakers" ] && { [ -n "$min_speakers" ] || [ -n "$max_speakers" ]; }; then
  printf '%s\n' '--num-speakers cannot be combined with --min-speakers or --max-speakers.' >&2
  exit 2
fi
if [ -n "$min_speakers" ] && [ -n "$max_speakers" ] && [ "$min_speakers" -gt "$max_speakers" ]; then
  printf '%s\n' '--min-speakers cannot be greater than --max-speakers.' >&2
  exit 2
fi

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
download_audio() {
  local -a yt_dlp_options=(
    --no-playlist
    --format 'bestaudio/best'
    --extract-audio
    --audio-format m4a
    --audio-quality 0
    --output "$output_dir/source.%(ext)s"
    --print after_move:filepath
  )
  if [ -n "${YOUTUBE_YTDLP_EXTRACTOR_ARGS:-}" ]; then
    yt_dlp_options+=(--extractor-args "$YOUTUBE_YTDLP_EXTRACTOR_ARGS")
  elif [ "${1:-default}" = "embedded" ]; then
    yt_dlp_options+=(--force-ipv4 --extractor-args 'youtube:player_client=web_embedded')
  fi
  yt-dlp "${yt_dlp_options[@]}" "$video_url"
}

if ! source_audio="$(download_audio default)"; then
  if [ -n "${YOUTUBE_YTDLP_EXTRACTOR_ARGS:-}" ]; then
    printf '%s\n' 'Audio download failed with the configured YOUTUBE_YTDLP_EXTRACTOR_ARGS.' >&2
    exit 1
  fi
  printf 'Default YouTube client failed; retrying with web_embedded...\n' >&2
  source_audio="$(download_audio embedded)"
fi

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

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$diarize" = "true" ]; then
  cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}"
  default_diarization_python="$cache_root/youtube-full/diarization-venv/bin/python"
  if [ -n "${YOUTUBE_DIARIZATION_PYTHON:-}" ]; then
    diarization_python="$YOUTUBE_DIARIZATION_PYTHON"
  elif [ -x "$default_diarization_python" ]; then
    diarization_python="$default_diarization_python"
  else
    diarization_python="python3"
  fi

  if ! "$diarization_python" -c 'import pyannote.audio' >/dev/null 2>&1; then
    printf 'Speaker diarization dependencies are not installed. Run:\n  %s/setup-diarization.sh\n' "$script_dir" >&2
    exit 1
  fi

  diarization_args=(
    --audio "$output_dir/audio.wav"
    --transcript "$output_dir/transcript.json"
    --output-dir "$output_dir"
    --model "$diarization_model"
    --device "$diarization_device"
  )
  [ -z "$num_speakers" ] || diarization_args+=(--num-speakers "$num_speakers")
  [ -z "$min_speakers" ] || diarization_args+=(--min-speakers "$min_speakers")
  [ -z "$max_speakers" ] || diarization_args+=(--max-speakers "$max_speakers")

  printf 'Identifying speakers locally with pyannote...\n'
  env \
    PYANNOTE_METRICS_ENABLED="${PYANNOTE_METRICS_ENABLED:-0}" \
    HF_HUB_DISABLE_TELEMETRY="${HF_HUB_DISABLE_TELEMETRY:-1}" \
    "$diarization_python" "$script_dir/diarize-transcript.py" "${diarization_args[@]}"
fi

collection_root="$(dirname "$output_dir")"
if [ "$(basename "$collection_root")" = "yt-transcripts" ]; then
  python3 "$script_dir/rebuild-index.py" "$collection_root"
fi
