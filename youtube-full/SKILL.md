---
name: youtube-full
description: "Handle YouTube videos, links, IDs, channels, and playlists. Use for downloading permitted media, extracting complete transcripts, captions, speaker-separated transcripts, timestamps, summaries, translations, quotes, or research from YouTube. For transcription, use the bundled local-first yt-dlp + Whisper workflow; do not require TranscriptAPI or another paid transcript service."
---

# YouTube Full

Use local tools for YouTube work. Do not request a TranscriptAPI account or key.

## Full-video transcription

Run the bundled script:

```bash
scripts/transcribe-youtube.sh "YOUTUBE_URL"
```

The script:

1. Downloads only the video's audio with `yt-dlp`.
2. Converts it to mono 16 kHz WAV with `ffmpeg`.
3. Runs a local Whisper model through `whisper-cli`.
4. Produces `transcript.txt`, `transcript.srt`, `transcript.json`, and `video.json`.
5. Rebuilds the Markdown index when the collection root is named `yt-transcripts`.

By default, save transcripts under `./yt-transcripts/<video-id>/`. Honor `YOUTUBE_TRANSCRIPTS_DIR` when set. Pass an explicit output directory only when the user requests another location.

The first run downloads the multilingual `large-v3-turbo-q5_0` model to the local cache. It requires no API key and incurs no per-request fee.

Use `--language pt`, `--language en`, or another ISO language code when known. Keep `auto` when uncertain. Use `--model PATH` to select another whisper.cpp GGML model.

## Local speaker diarization

When the user asks to identify or separate speakers, add `--diarize`:

```bash
scripts/transcribe-youtube.sh --diarize "YOUTUBE_URL"
```

The optional diarization stage runs locally with `pyannote.audio` and the open-source `pyannote/speaker-diarization-community-1` model. It never sends audio to a transcription or diarization API, and disables pyannote and Hugging Face telemetry by default. The first use needs internet access only to download model files.

Before the first diarized transcription, run `scripts/setup-diarization.sh`. Then accept the Community-1 model terms on Hugging Face and provide a read token with `HF_TOKEN`, or authenticate with the Hugging Face CLI. This token authorizes the model download; it is not a paid inference API key. Once cached, inference is local. A fully downloaded local model directory can be selected with `--diarization-model PATH`.

Use `--num-speakers N` when the exact number is known. Otherwise, optional `--min-speakers N` and `--max-speakers N` bounds can improve detection. CPU is the safe default; `--diarization-device cuda` and `--diarization-device mps` are available when supported.

Diarization produces `transcript-speakers.txt`, `transcript-speakers.srt`, `transcript-speakers.json`, and `diarization.json`. Speaker labels such as `SPEAKER_00` distinguish voices but do not infer people's real names.

## Dependencies

Check for `yt-dlp`, `ffmpeg`, `whisper-cli`, and `curl`. Speaker diarization additionally needs Python 3.10+ and the environment created by `scripts/setup-diarization.sh`. On macOS, `curl` is built in; install the other missing base tools with Homebrew:

```bash
brew install yt-dlp ffmpeg whisper-cpp
```

Do not install packages unless the user requested transcription or setup. Explain that OpenAI's hosted audio transcription API is paid; this skill uses the free, open-source Whisper model locally.

## Output handling

- Preserve the complete transcript artifact; do not truncate it in chat.
- Run `scripts/rebuild-index.py PATH_TO_YT_TRANSCRIPTS` after adding, moving, or removing a transcript folder manually.
- Give the user clickable links to the TXT and SRT files.
- For diarized runs, also give clickable links to `transcript-speakers.txt` and `transcript-speakers.srt`.
- Summarize only when requested or when a short orientation is clearly useful.
- Respect copyright and platform terms. Transcribe content the user supplied for personal analysis; do not redistribute the source video.
