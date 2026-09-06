# Local catalog interface contract

All API endpoints use the same origin under `/api`. The JSON field names below define the integration contract. Portuguese is the default interface language.

## Transcription queue and languages

The interface is localized in Portuguese, English, and Spanish. The browser preference `rw-ai.locale` stores `pt`, `en`, or `es`; transcript and metadata content is not translated. Queue errors include stable `error_code` values for localized display.

- `POST /api/jobs` with body `{url,kind:"auto"|"video"|"playlist",language:"auto"|"pt"|"en"|"es"}` returns HTTP 202 with JobDetail. URLs must be HTTPS YouTube video or playlist links. Automatic mode resolves a watch URL with a list parameter to the whole playlist; explicit video mode processes only the video. Repeated submissions of the same active URL, kind, and language return the existing job.
- `GET /api/jobs?page=1&page_size=10`: `{items:Job[],total,page,page_size}`.
- `GET /api/jobs/{id}`: JobDetail with every discovered item and the latest 200 log entries.
- `POST /api/jobs/{id}/cancel`: requests cancellation, preserving completed videos. Returns JobDetail.
- `POST /api/jobs/{id}/retry`: queues failed or cancelled items again, preserving successful ones. Only jobs with status `failed`, `partial`, or `cancelled` are accepted. Returns JobDetail.
- Job: `{id,url,kind,language,title,status,stage,total,completed,failed,skipped,cancel_requested,created_at,updated_at,error,error_code}`.
- JobDetail adds `items:[{id,position,video_id,title,status,stage,error,error_code,updated_at}]` and `logs:[{id,time,stage,message}]`.
- Job statuses: `queued`, `discovering`, `running`, `completed`, `partial`, `failed`, `cancelled`. Item statuses: `queued`, `running`, `completed`, `failed`, `skipped`, `cancelled`.
- Stages: `queued`, `discovering`, `model`, `downloading`, `converting`, `transcribing`, `publishing`, `completed`, `failed`, `cancelled`.

A single worker enumerates the entire playlist and processes items serially. Unavailable items and per-video failures do not stop the rest. Valid existing videos, including Trash entries, are retained rather than overwritten. Incomplete conflicting folders produce an explicit item error. SQLite stores queue state; container shutdown stops process groups, and unfinished work resumes on startup. Cancellation persists. Complete artifacts are validated and published atomically under the catalog scan lock before becoming visible. No paid or hosted transcription API is used.

Docker uses the `youtube-catalog-storage` volume by default. SQLite and searchable transcript segments are under `/storage/catalog/`; original artifacts are under `/storage/transcripts/`; the model and unfinished work use `/storage/models/` and `/storage/work/`. Migration from a previous bind-mounted collection is explicit and leaves the old folders intact. See [scripts/storage.py](scripts/storage.py) and the [migration instructions](README.md#migrate-a-collection-from-the-previous-version).

## Delete and restore

- Removing a video from the catalog hides it from the visible library; source files remain untouched. Video includes the persistent field `deleted_at: string|null`. Background imports do not restore deleted videos.
- `DELETE /api/videos/{id}` returns `{id,deleted:true}` (idempotent for an existing video).
- `POST /api/videos/{id}/restore` restores the video to the catalog and returns Detail, preserving categories.
- `GET /api/videos?deleted=true` lists only deleted videos, with the same pagination and search contract. The default lists active videos only. Trash defaults to the most recently deleted videos first when `sort=added`. Thumbnail URLs continue to work in Trash, but direct details, segments, and downloads for deleted videos return HTTP 404 until restored.
- Stats include `deleted_videos`. All existing counts, channel and category counts, and regular search exclude deleted videos. Categories themselves remain available even with a count of 0. The Trash interface has restore buttons and explains that removing a video from the catalog preserves its originals, while permanent deletion erases them.
- The **Remove from catalog** button on a video's detail page opens a native accessible confirmation dialog explaining that source files are preserved. Success navigates to the active catalog with a message. **Trash** in the sidebar opens the deleted list. These labels are localized to the selected interface language.
- `DELETE /api/videos/{id}/permanent` with body `{confirmation: id}` permanently removes a trashed video, its original folder (including media), transcript indexes, custom assignments, and cached thumbnail. It returns `{id,permanently_deleted:true}`. Active videos are rejected with HTTP 409; an incorrect confirmation is rejected with HTTP 422. The interface requires typing `DELETE` in English, `EXCLUIR` in Portuguese, or `ELIMINAR` in Spanish in an explicit irreversible-action dialog before submitting the ID. Source directories are never taken from client paths; symlinks must not be followed. The source mount must allow writes. A failed physical removal must return a visible error rather than claim success.
- Summary fields `purge_pending:boolean` and `purge_error:string|null` identify an incomplete permanent removal. Filesystem failure returns HTTP 503 and keeps the trashed entry visible; a new explicit permanent-delete confirmation retries cleanup. Restore returns HTTP 409 after the physical operation has started. Startup never resumes destructive work automatically. The scanner skips pending removals. The operation journal persists in SQLite across restarts.

## Catalog API

- `GET /api/health`: `{status: "ok"}`.
- `GET /api/stats`: `{videos, channels, categories, methods, hours, deleted_videos, languages: [{id,name,count}], scanning, last_scan, warnings: [{video_id,message}]}`. Active counts include imported videos whose sources are unavailable; cards explicitly show availability. Trashed videos are counted only in `deleted_videos`.
- `GET /api/categories`: array of `{id: string, name: string, count: number}`.
- `POST /api/categories` with body `{name}` returns a category. `PATCH /api/categories/{id}` with body `{name}` returns a category.
- `GET /api/channels`: array of `{id: string, name: string, count: number}`.
- `GET /api/videos?q=&channel=&category=&language=&sort=added&page=1&page_size=24`: `{items: Video[], total, page, page_size}`. Sort values are `added`, `published`, `title`, and `relevance`. Empty filter values mean no filter; category `uncategorized` means no effective categories. The maximum page size is 100. Plain-text search matches titles, channels, tags, and transcripts without regard to accents or case. A non-empty search defaults to relevance. `added` and `published` sort in descending order; `title` sorts in ascending order.
- Video summary: `{id,title,channel,channel_id,duration,language,published_at,added_at,deleted_at,available,has_method,has_speakers,categories:[{id,name}],tags:string[],thumbnail_url,match?:{text,start_ms,segment_index}|null}`. Durations are in seconds; dates are ISO strings. Thumbnail URLs are always local.
- `GET /api/videos/{id}` returns Video plus `{description,method:string|null,downloads:[{filename,label,url}],category_override:boolean,suggestions:[{id,name,score}]}`. Method contains the Markdown body without YAML front matter. Suggestions include up to 3 local similarity results, excluding assigned categories.
- `GET /api/videos/{id}/segments?offset=0&limit=100&q=`: `{items:[{index,start_ms,end_ms,text,speaker:string|null}],total,offset,limit}`. Offset and total refer to the returned filtered sequence when `q` is present. Plain-text search terms match without regard to accents or case. Segments use the version with speaker labels when valid, otherwise the original. The maximum limit is 300.
- `PUT /api/videos/{id}/categories` with body `{category_ids:string[]}` replaces all effective categories with a persistent user override, including an explicitly empty list. Returns updated detail. `DELETE /api/videos/{id}/categories` resets to imported categories and returns detail.
- `POST /api/scan`: schedules an incremental scan without blocking and returns `{scanning:true}`; duplicate overlapping requests do not start another scan.
- `GET /api/videos/{id}/thumbnail`: cached image or local SVG fallback.
- `GET /api/videos/{id}/files/{filename}`: downloads an allowlisted original artifact; arbitrary filesystem paths are not accepted.
- Errors: non-2xx responses with `{detail: string}`.

## Runtime

Runtime environment variables: `TRANSCRIPTS_DIR` (default `/transcripts`), `DATA_DIR` (default `/data`), `STATIC_DIR` (default `/app/static`), `SCAN_INTERVAL_SECONDS` (default 60), and `DOWNLOAD_THUMBNAILS` (default true). The backend is `backend.app:app`; execute the Python module from the project root. The `create_app(...)` factory may support test isolation.

The backend serves the compiled single-page application as a fallback after API routes, but never returns HTML for unknown `/api/*` routes.
