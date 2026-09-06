# rw / ai · Transcript catalog

A local library with video thumbnails, full-text search, categories, and method analyses. Includes a queue for transcribing YouTube videos and playlists within Docker, with no paid transcription service. The interface supports **Portuguese, English, and Spanish**.

## Install, open, and stop

Requires Docker Desktop or Docker Engine with Compose. The first build downloads dependencies and compiles the local transcription engine.

```bash
# From the youtube-catalog directory, for a new installation:
cp .env.example .env
docker compose up -d --build
```

Open [localhost:8765](http://localhost:8765). On macOS, you can also open [Start.command](Start.command) and [Stop.command](Stop.command). Keep any existing `.env` configuration. The **youtube-catalog** project appears in Docker Desktop and can be started or stopped with its usual controls; it does not start automatically.

```bash
docker compose stop       # stop while preserving the queue and library
docker compose start      # start again
docker compose ps         # check status
```

The port is bound only to `127.0.0.1`, with no login required. To change the port, update `CATALOG_PORT` in `.env` and run `docker compose up -d`.

## Add videos or a playlist

1. Open **Add videos** and paste an HTTPS URL for a YouTube video or playlist.
2. Use **Detect automatically**, or explicitly choose **Video** or **Entire playlist**. A video link containing `list=` selects the playlist in automatic mode; choosing Video processes only that video.
3. Select the spoken language: **Detect automatically**, **Portuguese**, **English**, or **Spanish**.
4. Add it to the queue. The app enumerates the entire playlist and transcribes its videos one at a time. Results appear in the catalog automatically.

Follow the current stage, counts, and results for each video. Private, removed, or failed items are flagged while processing continues for the others. Existing videos, including those in Trash, are not overwritten. You can cancel a job or retry its failed or cancelled items. Ongoing live streams are not processed.

Stopping Docker stops processing. The queue stays in the database and resumes on startup; an incomplete stage may restart, preserving completed videos. The library remains available during processing. Long transcriptions can take time because they use the local CPU. Set `WHISPER_THREADS` in `.env` to adjust the number of threads.

The engine uses yt-dlp, ffmpeg, and Whisper.cpp with the multilingual `large-v3-turbo-q5_0` model. The first job downloads approximately 547 MiB of model data and verifies its SHA-256; the file is cached in the volume. No API token is required. The selected language specifies the audio language: it does not translate the transcript or change the original content.

## Language and navigation

The language selector switches the entire interface between PT, EN, and ES and saves the preference in this browser. Titles, transcripts, user-created categories, and analyses retain their original content. The labels below refer to the English interface; Portuguese is the default.

- **Home:** recent additions and shelves grouped by channel or category.
- **All videos:** search combined with channel, category, language, and sorting options. Results lead to the matching passage.
- **Transcript:** text with timestamps, internal search, speaker labels when available, and progressive loading.
- **Method:** an existing analysis from `METODO.md`, when available. New transcriptions do not generate analyses automatically.
- **Your files:** TXT, SRT, and JSON, including existing versions with speaker labels.
- **Categories:** create, rename, and assign multiple topics; local suggestions are applied only when selected.
- **Trash:** Remove from catalog preserves the files; Restore video brings the video back with its categories. Delete permanently requires typing `DELETE` in English (`EXCLUIR` in Portuguese or `ELIMINAR` in Spanish) and erases that video's files from Docker storage. After a partial failure, Trash lets you confirm again to finish; physical deletion never resumes automatically.

## Database and content inside Docker

The named volume **youtube-catalog-storage** holds the entire library. Docker manages it, and it remains on the computer when the container is stopped, updated, or recreated:

| Inside the container | Content |
| --- | --- |
| `/storage/catalog/catalog.sqlite3` | SQLite database: metadata, transcript segments, FTS5 index, methods, categories, Trash, and queue |
| `/storage/transcripts/` | Files for each video: `video.json`, TXT, SRT, JSON, and other imported originals |
| `/storage/catalog/thumbnails/` | Local thumbnails |
| `/storage/models/` | Verified transcription model |
| `/storage/work/` | Unfinished work and temporary queue files |

A new installation does not depend on a transcript folder on the host computer. The `CATALOG_VOLUME` variable selects a different volume; changing its name opens a different library. **Do not remove the volume or run `docker compose down -v` simply to stop the app**, as this deletes persistent data.

## Migrate a collection from the previous version

Run the migration before starting a new library. It copies the source files, thumbnails, and previous database, preserving categories, methods, and Trash. The old folders remain intact as a backup and become independent of the library in Docker. Later changes to those folders are not imported automatically.

The default paths are `../yt-transcripts` and `./data`. For other folders, set `LEGACY_TRANSCRIPTS_DIR` and `LEGACY_DATA_DIR` in `.env`. Both folders must exist.

```bash
docker compose stop
docker compose build
docker compose -f compose.yaml -f compose.migrate.yaml run --rm --no-deps --user 0:0 --entrypoint python catalog /app/scripts/storage.py import-legacy --source /legacy/transcripts --data /legacy/data
docker compose up -d
```

If a permanent deletion is incomplete, finish it in the previous version's Trash before migrating. The process verifies the copies and database integrity. It does not overwrite a volume that already contains a library. Once complete, repeating the migration does not reimport deleted videos. An interrupted copy requires explicitly running the command again; the app will not start while a migration is incomplete. To import a collection without a previous database, use an empty folder as `LEGACY_DATA_DIR`.

## Backup

Stop the app before copying the library. A backup includes the database, content, queue, and model:

```bash
docker compose stop
mkdir -p backups
docker compose run --rm --no-deps --user 0:0 --cap-add DAC_OVERRIDE --entrypoint python -v "$PWD/backups:/backup" catalog /app/scripts/storage.py backup /backup/library.tar.gz
```

Use a new filename for each backup. The command does not overwrite earlier backups. The `backups/` folder is outside the volume. To restore, with the app stopped, extract the archive into a **new volume** and set `CATALOG_VOLUME` to that volume. Keep file ownership at `1000:1000`, as in the original library. Remove the previous library only after verifying the restoration.

## Internet and privacy

Installation downloads dependencies. A requested job accesses YouTube for metadata and audio; first use also downloads the model from Hugging Face. Thumbnails may download in the background. Audio and text are processed locally, with no uploads to a transcription API or telemetry service.

Without internet, you can browse, search, read, edit categories, and download files already stored in the library. New jobs may fail and can be retried when the connection returns. Interface fonts are local. To prevent new thumbnail downloads, set `DOWNLOAD_THUMBNAILS=false` in `.env` and recreate the container.

## Update and verify

```bash
docker compose up -d --build
```

The volume is reused. The database, models, and content are not part of the repository. To investigate startup issues, use `docker compose logs --tail=80`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/python -m pytest backend/tests tests/test_storage.py -q
```

The API contract is in [CONTRACT.md](CONTRACT.md). Test procedures, including language, playlists, deletion, and persistence checks, are in [tests/README.md](tests/README.md). Destructive checks use disposable data.

References: [Whisper.cpp](https://github.com/ggml-org/whisper.cpp), [yt-dlp](https://github.com/yt-dlp/yt-dlp), [Docker volumes](https://docs.docker.com/engine/storage/volumes/).
