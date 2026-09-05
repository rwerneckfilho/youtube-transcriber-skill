# Interface local do catálogo

All API endpoints are same-origin under `/api`. JSON field names below are the integration contract. All user-facing text is Portuguese.

## Excluir e restaurar

- Excluir removes a video from the visible catalog only; source files remain untouched. Persistent `deleted_at: string|null` is added to Video. Background imports do not restore deleted videos.
- `DELETE /api/videos/{id}` returns `{id,deleted:true}` (idempotent for an existing video).
- `POST /api/videos/{id}/restore` restores to the catalog and returns Detail, preserving categories.
- `GET /api/videos?deleted=true` lists only deleted videos, with the same pagination and search contract. Default is active only. Trash default order is most recently deleted first (when sort=added). Thumbnail URLs continue to work in trash, but direct details/segments/downloads of deleted videos return404 until restored.
- Stats gain `deleted_videos`. All existing counts, channel/category counts and regular search exclude deleted videos. Categories themselves remain available even with count0. Trash UI has restore buttons and page-level explanation that originals were preserved.
- UI confirmation button `Excluir do catálogo` on a video detail opens a native accessible dialog explaining the source files are preserved. Success navigates to active catalog with a message. Sidebar `Lixeira` opens deleted list.
- `DELETE /api/videos/{id}/permanent` body `{confirmation: id}` permanently removes a trashed video, its original folder (including media), transcript indexes, custom assignments and cached thumbnail. It returns `{id,permanently_deleted:true}`. Active videos are rejected with409; an incorrect confirmation is rejected with422. The UI requires typing `EXCLUIR` in an explicit irreversible-action dialog before submitting the ID. Source directories are never taken from client paths; symlinks must not be followed. The source mount must allow writes. A failed physical removal must return a visible error rather than claim success.
- Summary fields `purge_pending:boolean` and `purge_error:string|null` identify an incomplete permanent removal. Filesystem failure returns503 and keeps the trashed entry visible; a new explicit permanent-delete confirmation retries cleanup. Restore returns409 after the physical operation has started. Startup never resumes destructive work automatically. The scanner skips pending removals. The operation journal persists in SQLite across restarts.

- `GET /api/health`: `{status: "ok"}`.
- `GET /api/stats`: `{videos, channels, categories, methods, hours, deleted_videos, languages: [{id,name,count}], scanning, last_scan, warnings: [{video_id,message}]}`. Active counts include imported unavailable videos; availability is explicit on cards. Trashed videos are counted only in `deleted_videos`.
- `GET /api/categories`: array of `{id: string, name: string, count: number}`.
- `POST /api/categories` body `{name}`; returns category. `PATCH /api/categories/{id}` body `{name}`; returns category.
- `GET /api/channels`: array of `{id: string, name: string, count: number}`.
- `GET /api/videos?q=&channel=&category=&language=&sort=added&page=1&page_size=24`: `{items: Video[], total, page, page_size}`. Sort values `added`, `published`, `title`, `relevance`. Empty filter values mean no filter; category `uncategorized` means no effective categories. Page size max 100. Search is plain text, accents and case insensitive, in titles/channels/tags/transcript. Non-empty search defaults to relevance. `added`/`published` are descending; `title` ascending.
- Video summary: `{id,title,channel,channel_id,duration,language,published_at,added_at,deleted_at,available,has_method,has_speakers,categories:[{id,name}],tags:string[],thumbnail_url,match?:{text,start_ms,segment_index}|null}`. Durations in seconds; dates ISO strings. Thumbnail URL always local.
- `GET /api/videos/{id}` returns Video plus `{description,method:string|null,downloads:[{filename,label,url}],category_override:boolean,suggestions:[{id,name,score}]}`. Method is Markdown body without YAML. Suggestions are up to 3 local similarity results excluding assigned categories.
- `GET /api/videos/{id}/segments?offset=0&limit=100&q=`: `{items:[{index,start_ms,end_ms,text,speaker:string|null}],total,offset,limit}`. Offset and total refer to returned filtered sequence when q exists. Plain search terms matched accent/case insensitive. Segments are preferred speakers variant if valid, otherwise original. Limit max 300.
- `PUT /api/videos/{id}/categories` body `{category_ids:string[]}` replaces all effective categories with a persistent user override, including explicitly empty list. Returns updated detail. `DELETE /api/videos/{id}/categories` resets to imported categories and returns detail.
- `POST /api/scan`: schedules incremental scan (nonblocking), returns `{scanning:true}`; duplicate overlapping requests do not start another scan.
- `GET /api/videos/{id}/thumbnail`: cached image or local SVG fallback.
- `GET /api/videos/{id}/files/{filename}`: download allowlisted original artifact, no arbitrary filesystem paths.
- Errors: non-2xx with `{detail: string}`.

Runtime env: `TRANSCRIPTS_DIR` (default `/transcripts`), `DATA_DIR` (default `/data`), `STATIC_DIR` (default `/app/static`), `SCAN_INTERVAL_SECONDS` (default 60), `DOWNLOAD_THUMBNAILS` (default true). Backend `backend.app:app`; Python module executed from project root. `create_app(...)` factory may support test isolation.

Backend serves compiled SPA fallback after API routes, never HTML for unknown `/api/*` routes.
