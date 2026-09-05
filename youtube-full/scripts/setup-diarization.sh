#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_command="${YOUTUBE_DIARIZATION_BASE_PYTHON:-python3}"
cache_root="${XDG_CACHE_HOME:-${HOME}/.cache}"
venv_dir="${YOUTUBE_DIARIZATION_VENV:-$cache_root/youtube-full/diarization-venv}"

command -v "$python_command" >/dev/null 2>&1 || {
  printf 'Missing dependency: %s\n' "$python_command" >&2
  exit 1
}

if ! "$python_command" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  printf 'Speaker diarization requires Python 3.10 or newer.\n' >&2
  exit 1
fi

printf 'Creating diarization environment at %s\n' "$venv_dir"
"$python_command" -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --upgrade pip
"$venv_dir/bin/python" -m pip install \
  --requirement "$script_dir/../requirements-diarization.txt"

printf '\nDiarization is ready. The first model download also requires:\n'
printf '1. Accept the model terms at https://huggingface.co/pyannote/speaker-diarization-community-1\n'
printf '2. Set HF_TOKEN to a Hugging Face read token, or run:\n'
printf '   %s auth login\n' "$venv_dir/bin/hf"
printf '\nPython environment: %s\n' "$venv_dir/bin/python"
