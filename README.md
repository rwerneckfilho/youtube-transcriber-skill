# YouTube Transcriber Skill

A Codex skill that downloads YouTube audio and creates complete local transcripts with `yt-dlp`, `ffmpeg`, and Whisper.cpp. Optional speaker diarization uses `pyannote.audio` to separate voices.

No paid API is required. On its first run, the script downloads the multilingual `large-v3-turbo-q5_0` model to the local cache and verifies its SHA-256 checksum. Audio processing, transcription, and diarization all run on your computer.

This repository also includes **[rw / ai · Transcript Catalog](youtube-catalog/README.md)**: a local Docker application for browsing transcripts by thumbnail, channel, and category, searching their text, reading method analyses, downloading files, and managing Trash. The **Add videos** tab transcribes individual videos and entire playlists in Docker. **Follow playlists** checks saved playlists periodically and sends new videos to the local transcription queue. The interface supports English, Portuguese, and Spanish.

The catalog's **Generate skill** workspace suggests related videos and combines your selection into one downloadable Agent Skills package. Synthesis uses an already installed local Ollama text model, with timestamped source references and a preview. No cloud inference or API key is required. See [local skill generation](youtube-catalog/README.md#generate-a-skill-from-several-videos) for setup and validation limits.

## Docker catalog

Requires Docker Desktop or Docker Engine with Compose. If you already use the previous version, follow the [library migration instructions](youtube-catalog/README.md#migrate-a-collection-from-the-previous-version) first. For a new installation on macOS or Linux, after cloning this repository:

```bash
cd youtube-transcriber-skill/youtube-catalog
cp .env.example .env
docker compose up -d --build
```

Open [localhost:8765](http://localhost:8765). These steps are for a new installation; preserve an existing `.env` configuration. On macOS, you can also use [Start.command](youtube-catalog/Start.command) and [Stop.command](youtube-catalog/Stop.command). See the [catalog instructions](youtube-catalog/README.md) for configuration and operation details.

The **youtube-catalog-storage** Docker volume stores the SQLite database, content, thumbnails, queue, and model. To import an existing library, follow the [migration instructions before first startup](youtube-catalog/README.md#migrate-a-collection-from-the-previous-version). Migration copies the old folders without modifying them; the Docker library then operates independently. The command-line skill remains available separately.

Start and stop the **youtube-catalog** project in Docker. The port is bound to your computer, and the volume persists when the container is recreated. Reading and searching work offline. New jobs access YouTube and download the model on first use; thumbnails may also download in the background. Transcription runs locally without a paid API. Unfinished jobs resume when the application starts again.

**Remove from catalog** moves a video to Trash while preserving its files. **Delete permanently**, available in Trash, requires confirmation and deletes that video's folder from Docker storage, including transcripts, analyses, and imported media. Personal libraries, thumbnails, databases, backups, and configuration are excluded from this repository.

## Skill requirements

- Codex with skill support
- Python 3
- `curl`
- `yt-dlp`
- `ffmpeg`
- `whisper-cli` (provided by the `whisper-cpp` package)

For optional speaker diarization:

- Python 3.10 or later
- `pyannote.audio` 4.x, installed by the included setup script
- Accepted access terms for the free `pyannote/speaker-diarization-community-1` model

On macOS with Homebrew:

```bash
brew install yt-dlp ffmpeg whisper-cpp
```

## Skill installation

Clone this repository and copy the skill into the Codex skills directory:

```bash
git clone https://github.com/rwerneckfilho/youtube-transcriber-skill.git
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R youtube-transcriber-skill/youtube-full "${CODEX_HOME:-$HOME/.codex}/skills/youtube-full"
```

Restart Codex to discover the skill.

To update an existing installation:

```bash
git -C youtube-transcriber-skill pull
rm -rf "${CODEX_HOME:-$HOME/.codex}/skills/youtube-full"
cp -R youtube-transcriber-skill/youtube-full "${CODEX_HOME:-$HOME/.codex}/skills/youtube-full"
```

## Usage

Ask Codex, for example:

> Use $youtube-full to transcribe this entire YouTube video: https://www.youtube.com/watch?v=VIDEO_ID

Or run the script directly:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --language en \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

### Speaker diarization

Set up the isolated diarization environment once:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/setup-diarization.sh"
```

Before downloading the model for the first time:

1. Accept the terms for [pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1).
2. Create a Hugging Face read token and export it as `HF_TOKEN`, or authenticate in the isolated environment:

```bash
"${XDG_CACHE_HOME:-$HOME/.cache}/youtube-full/diarization-venv/bin/hf" auth login
```

The token only authorizes the initial download of the free model weights. Once the model is cached, diarization runs locally. Optional pyannote and Hugging Face telemetry is disabled by default.

To transcribe a video and separate voices:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --diarize \
  --language en \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

When the number of speakers is known, specify it to improve diarization:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --diarize \
  --num-speakers 2 \
  "https://www.youtube.com/watch?v=VIDEO_ID"
```

Other options include `--min-speakers`, `--max-speakers`, `--diarization-device cpu|cuda|mps`, and `--diarization-model ID|PATH`. Pass a local model directory with `--diarization-model` for fully offline operation after downloading the model manually.

If the default YouTube client returns HTTP 403, the script automatically retries with the `web_embedded` client. Advanced users can override extractor arguments with `YOUTUBE_YTDLP_EXTRACTOR_ARGS`.

Transcripts are saved to `./yt-transcripts/<video-id>/` by default. To choose another root directory:

```bash
export YOUTUBE_TRANSCRIPTS_DIR="$HOME/Documents/yt-transcripts"
```

You can also specify an output directory directly:

```bash
"${CODEX_HOME:-$HOME/.codex}/skills/youtube-full/scripts/transcribe-youtube.sh" \
  --language en \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  "$HOME/Documents/my-transcript"
```

Generated files:

- `transcript.txt`
- `transcript.srt`
- `transcript.json`
- `video.json`
- `source.m4a`
- `audio.wav`

With `--diarize`, the script also generates:

- `transcript-speakers.txt`
- `transcript-speakers.srt`
- `transcript-speakers.json`
- `diarization.json`

Speakers receive stable identifiers within each run, such as `SPEAKER_00` and `SPEAKER_01`. Diarization distinguishes voices but does not automatically identify people's names.

When the output is inside a folder named `yt-transcripts`, the skill also updates the library's `INDEX.md` automatically.

## Notes

- The default model requires substantial disk space and is downloaded only on its first use.
- Diarization models also require substantial disk space and are downloaded only when `--diarize` is used.
- Use `--model /path/to/model.bin` to select another Whisper.cpp-compatible GGML model.
- Use `--language auto` when the spoken language is unknown.
- Overlapping voices, music, noise, and very short speech segments can reduce diarization accuracy.
- Respect copyright and platform terms. Transcribe content you are authorized to process.
