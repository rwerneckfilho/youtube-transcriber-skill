# Acceptance testing

With the catalog running, execute this command from the `youtube-catalog` directory:

```sh
python3 tests/acceptance.py --url http://127.0.0.1:8765 --source ../yt-transcripts
```

The runner uses only the Python standard library and read-only requests. It waits up to 90 seconds for the initial import (`--wait` changes this timeout). It never adds test files, changes categories, or writes other test data to the original collection.

Expected totals are calculated directly from the available metadata and transcripts; the video count is not hardcoded to 82. The test queries Trash separately and excludes its videos from checks of the active collection. Active and trashed videos together should match the source inventory, so records of previously removed sources can produce a legitimate difference. Additional user-created categories are allowed. Filters are checked against the effective categories returned for each video, preserving customizations.

The acceptance runner checks:

- Statistics, every video ID and title, pagination without duplicates, and filters by channel, category, and language.
- Search with and without accents and in uppercase, transcript content search, and navigation to the matching segment.
- Every segment of the longest video in pages of 100, comparing millisecond offsets with the original transcript.
- Method analyses without YAML front matter, missing analyses, speaker labels, and TXT/SRT/JSON downloads identical to the originals.
- JSON errors for unknown routes, rejection of paths outside the allowed downloads, local thumbnails, and loading the interface through a direct address.
- SHA-256 hashes of metadata, method analyses, and transcripts before and after testing. Audio and video media are neither read nor modified.

Failures include the scenario name. The process exits with code 1 if any scenario fails, or 0 if all pass. Scenarios that require specific examples, such as accented titles, speaker labels, and classifications, report when no active sample is available. Downloads are checked even when all videos with speaker labels are in Trash.

## Browser checks

`browser-smoke.cjs` uses Playwright and an installed Google Chrome browser. With Playwright available to Node, run:

```sh
node tests/browser-smoke.cjs
```

The default address is `http://127.0.0.1:8765`; `CATALOG_URL` accepts another local address. Make `playwright` and `pngjs` available in a Node test environment, for example with `npm install --no-save playwright pngjs`, or point `NODE_PATH` to an existing installation of these packages. Chrome must be installed.

The runner checks the home page, filters, pagination, search, navigation to a matching passage, method analyses, speaker labels, progressive loading, the channel directory, keyboard navigation, and 375 px screens. It opens and closes the category dialog without saving. It blocks API write requests and all external HTTP traffic throughout the run, then reloads the interface and repeats reading checks under these conditions. It records JavaScript errors and any attempted external requests.

Screenshots are saved to `/tmp/youtube-catalog-home.png`, `/tmp/youtube-catalog-reader.png`, and `/tmp/youtube-catalog-mobile.png`. The test uses classified videos, method analyses, and accented titles from the initial collection. Speaker and missing-method checks report when no active samples are available.

## Permanent deletion: disposable data only

`browser-permanent.cjs` actually deletes a synthetic video and its files. It refuses to run outside local port `8766` or against a directory other than the marked temporary fixture created by the setup script:

```sh
python3 tests/create-permanent-fixture.py
```

The setup script prints the `source` and `data` paths within a new temporary directory containing exactly two synthetic videos, transcripts, metadata, method analyses, and small audio files. Start an isolated server at `127.0.0.1:8766`, mounting this source with write access and using its dedicated data directory. Never connect this server to the original collection. Then run:

```sh
TEST_SOURCE_DIR="<source reported by the setup script>" node tests/browser-permanent.cjs
```

The runner checks warnings, the video name, initial focus on the cancel button, Escape, the exact confirmation token `EXCLUIR` used by the Portuguese interface, the mobile dialog, and deletion of the entire video directory. It compares hashes of the other video, checks its reader and downloads, and refreshes the catalog to prove that the removed video does not reappear. Moving videos to Trash and restoring them remain covered separately by `browser-update.cjs` and must preserve every original file.

Each successful run requires a new fixture because the previous one has been partially deleted. Screenshots are saved to `/tmp/youtube-catalog-permanent-dialog.png` and `/tmp/youtube-catalog-permanent-mobile.png`; the report is saved to `/tmp/youtube-catalog-permanent-report.json`.

## Queue and interface languages with a mocked API

`browser-jobs.cjs` tests only the compiled interface of an isolated server at `127.0.0.1:8766`. Playwright intercepts every `/api/*` route and responds using in-memory fixtures within the test. No real jobs are queued, and no videos are downloaded or transcribed.

```sh
node tests/browser-jobs.cjs
```

The runner covers video and playlist submission, transcription language selection, queue states, logs, cancellation, and retries. It also checks PT/EN/ES across routes and dialogs, persistence of the interface language preference after a reload, and the mobile layout. Fixtures use neutral content to detect interface text that remains in Portuguese in other translations. The report explicitly identifies this mocked coverage; real processing, migration, and volume persistence must be tested separately.

The report is saved to `/tmp/youtube-catalog-jobs-report.json`, with screenshots for each language. The preceding runners explicitly initialize their isolated browser contexts in Portuguese without changing the user's personal browser preference.

## Named volume: entirely synthetic integration test

With the image already built and port `8767` available, run:

```sh
python3 tests/docker-volume.py
```

The runner pins the ID of the local `youtube-catalog:local` image (`--image` accepts another existing image) without pulling images. It creates a dedicated named volume and internal network, generates two synthetic videos inside the volume, and reserves only `127.0.0.1:8767` on the host. It queries the HTTP API through the container's loopback interface because some Docker Desktop versions block published ports on internal networks. It neither mounts nor reads the original collection and does not use the servers on ports `8765` and `8766`. When finished, it removes only the resources it created.

The test checks edited categories, Trash, source files, a cached thumbnail, model storage, and working files after reimporting, stopping, starting, and recreating the container. Restoring a video must recover its original addition date, transcript, method analysis, and selected categories. The runner also creates a queued job and cancels another to verify their persistence. The worker remains disabled: this does not test processing, downloads, or real transcription.

The final stage uses `--network none` and the FastAPI test client inside the container to check the interface, local fonts, accent-insensitive search, segments, downloads, thumbnails, Trash, and the queue. The internal network blocks external access during the other stages. Ten synthetic files are compared using SHA-256; the test also confirms that no model was downloaded and no transcription job started processing. The report is saved to `/tmp/youtube-catalog-volume-report.json`.

## Resilience checks

`browser-update.cjs` checks rw / ai branding, local fonts, contrast, six viewport sizes, and the deletion, cancellation, and restoration flows. Changes are allowed only on a second, disposable server on port `8766`, using the same image and read-only source mounts but a temporary data directory. The main catalog on port `8765` receives only read requests.

```sh
CATALOG_URL=http://127.0.0.1:8765 TEST_CATALOG_URL=http://127.0.0.1:8766 TEST_SOURCE_DIR="$(cd ../yt-transcripts && pwd)" node tests/browser-update.cjs
```

Use `UPDATE_TEST_MODE=brand` to check only the main catalog's appearance without requiring the second server. The report is saved to `/tmp/youtube-catalog-update-report.json`. The full runner also confirms that source files remain identical after deleting and restoring a video.

`python3 tests/docker-lifecycle.py` creates its own temporary container on port `8766`. Run it with that port available. It checks categories and deletions after a refresh, stop, restart, and recreation; restores the video with its preferences; and exercises reading with the container disconnected from the network.

Isolated backend tests should use separate temporary directories for sources and data. In this disposable collection, check incremental imports, incomplete sources, invalid JSON, video removal and return, category persistence after reimporting, overlapping scans, and thumbnail download failures. These scenarios must not create or modify files in `yt-transcripts`.

For Docker integration, stop and start the service and repeat the acceptance checks, then recreate the container without removing persistent storage and repeat them again. Once the application is loaded and thumbnails are cached, block external access and verify navigation, filters, search, reading, and local downloads. HTTP acceptance alone does not prove that the external network is unavailable or replace visual inspection and keyboard navigation checks.
