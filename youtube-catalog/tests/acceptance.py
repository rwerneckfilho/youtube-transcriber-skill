#!/usr/bin/env python3
"""Read-only HTTP acceptance checks against the user's real local collection.

Uses Python's standard library only. Creates no videos, categories or overrides.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urljoin, urlsplit
from urllib.request import urlopen


ARTIFACT_NAMES = {
    "video.json", "METODO.md", "transcript.json", "transcript.txt", "transcript.srt",
    "transcript-speakers.json", "transcript-speakers.txt", "transcript-speakers.srt",
}


def normalized(value: str) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def manifest(source: Path) -> dict[str, str]:
    return {
        str(path.relative_to(source)): digest(path)
        for path in sorted(source.glob("*/*"))
        if path.is_file() and not path.is_symlink() and path.name in ARTIFACT_NAMES
    }


def yaml_scalar(text: str, field: str) -> str | None:
    """Read the simple top-level quoted scalars used by the actual METODO files."""
    if not text.startswith("---"):
        return None
    header = text.split("---", 2)[1]
    match = re.search(r"^" + re.escape(field) + r":\s*(.*?)\s*$", header, re.M)
    if not match:
        return None
    value = match.group(1)
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def segments_from(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, list):
        return document
    return document.get("transcription", document.get("segments", []))


def offset_ms(segment: dict, edge: str) -> int:
    if "offsets" in segment:
        return int(segment["offsets"]["from" if edge == "start" else "to"])
    return round(float(segment[edge]) * 1000)


def inventory(source: Path) -> dict[str, dict]:
    records = {}
    for metadata in sorted(source.glob("*/video.json")):
        try:
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            original = metadata.parent / "transcript.json"
            segments = segments_from(original)
            if not isinstance(meta, dict) or not meta.get("id") or not segments:
                raise ValueError("empty metadata or segments")
            speaker_path = metadata.parent / "transcript-speakers.json"
            if speaker_path.is_file():
                try:
                    preferred = segments_from(speaker_path)
                    if preferred:
                        segments = preferred
                except (ValueError, KeyError, TypeError):
                    pass
            method_path = metadata.parent / "METODO.md"
            method = method_path.read_text(encoding="utf-8") if method_path.exists() else None
            records[meta["id"]] = {
                "meta": meta, "directory": metadata.parent, "segments": segments,
                "method": method, "cluster": yaml_scalar(method, "cluster") if method else None,
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"WARNING skipped source: {metadata}: {error}")
    return records


class Acceptance:
    def __init__(self, base_url: str, source: Path):
        self.base_url = base_url.rstrip("/") + "/"
        self.source = source
        self.records = inventory(source)
        self.all_records = self.records.copy()
        self.passed = 0
        self.failed = 0
        self.cards: dict[str, dict] = {}

    def check_trash_inventory(self):
        stats = self.json("/api/stats")
        found = []
        page = 1
        while True:
            result = self.json("/api/videos", params={"deleted": "true", "page": page, "page_size": 100})
            assert result["total"] == stats["deleted_videos"]
            found.extend(result["items"])
            if len(found) >= result["total"]:
                break
            assert result["items"], "Trash pagination ended before the reported total"
            page += 1
        deleted_ids = [video["id"] for video in found]
        assert len(deleted_ids) == len(set(deleted_ids)), "Duplicate videos in Trash"
        assert set(deleted_ids) <= set(self.all_records), "Trashed video has no matching source in this collection"
        assert all(video.get("deleted_at") for video in found), "Trashed video has no deletion timestamp"
        self.records = {key: value for key, value in self.all_records.items() if key not in set(deleted_ids)}
        assert stats["videos"] + stats["deleted_videos"] == len(self.all_records)
        print(f"       {stats['videos']} active videos; {stats['deleted_videos']} in Trash; {len(self.all_records)} sources preserved")

    def get(self, path: str, *, params: dict | None = None, expected: int = 200):
        url = urljoin(self.base_url, path)
        if urlsplit(url).netloc != urlsplit(self.base_url).netloc:
            raise AssertionError(f"URL should remain local: {path}")
        if params:
            url += "?" + urlencode(params)
        try:
            response = urlopen(url, timeout=25)
        except HTTPError as error:
            response = error
        with response:
            body = response.read()
            status = response.status
            headers = response.headers
        assert status == expected, f"GET {path}: HTTP {status}, expected {expected}; {body[:180]!r}"
        return body, headers

    def json(self, path: str, **kwargs):
        body, headers = self.get(path, **kwargs)
        assert "application/json" in headers.get("Content-Type", ""), f"Expected JSON: {path}"
        return json.loads(body)

    def run(self, name: str, check):
        try:
            check()
        except Exception as error:
            self.failed += 1
            print(f"FAILED {name}: {error}", flush=True)
        else:
            self.passed += 1
            print(f"OK     {name}", flush=True)

    def wait_ready(self, seconds: int):
        deadline = time.monotonic() + seconds
        last = "server still unavailable"
        while time.monotonic() <= deadline:
            try:
                health = self.json("/api/health")
                stats = self.json("/api/stats")
                if health.get("status") == "ok" and not stats.get("scanning") and stats.get("videos"):
                    return
                last = f"import in progress: {stats.get('videos', 0)} videos"
            except (OSError, URLError, AssertionError, ValueError) as error:
                last = str(error)
            time.sleep(1)
        raise RuntimeError(f"Application was not ready within {seconds}s: {last}")

    def check_inventory(self):
        assert self.records, "No valid sources found"
        stats = self.json("/api/stats")
        channels = {record["meta"].get("channel_id") or record["meta"].get("channel")
                    for record in self.records.values()}
        methods = sum(bool(record["method"]) for record in self.records.values())
        clusters = {record["cluster"] for record in self.records.values() if record["cluster"]}
        assert stats["videos"] == len(self.records), f"{stats['videos']} videos; sources: {len(self.records)}"
        assert stats["channels"] == len(channels), f"{stats['channels']} channels; sources: {len(channels)}"
        assert stats["methods"] == methods, f"{stats['methods']} methods; sources: {methods}"
        assert stats["categories"] >= len(clusters), f"Missing imported categories: {clusters}"
        assert stats.get("last_scan"), "Missing last-import timestamp"
        print(f"       Sources: {len(self.records)} videos, {len(channels)} channels, {methods} methods, {len(clusters)} categories")

    def check_pagination(self):
        found = []
        page = 1
        while True:
            result = self.json("/api/videos", params={"page": page, "page_size": 17, "sort": "title"})
            assert result["total"] == len(self.records)
            assert result["page"] == page
            assert result["page_size"] == 17
            assert len(result["items"]) <= 17
            found.extend(result["items"])
            if len(found) >= result["total"]:
                break
            assert result["items"], "Pagination ended before the reported total"
            page += 1
        ids = [video["id"] for video in found]
        assert len(ids) == len(set(ids)), "Duplicate videos across pages"
        assert set(ids) == set(self.records), "Imported IDs differ from the sources"
        self.cards = {video["id"]: video for video in found}
        for video in found:
            assert video["title"] == self.records[video["id"]]["meta"]["title"]
            assert video["available"] is True
            assert video["thumbnail_url"].startswith("/"), "Thumbnail must have a local URL"
        empty = self.json("/api/videos", params={"page": 10000})
        assert empty["items"] == [] and empty["total"] == len(self.records)

    def check_filters(self):
        assert self.cards, "Pagination check returned no videos"
        channels = self.json("/api/channels")
        categories = self.json("/api/categories")
        stats = self.json("/api/stats")
        assert len(channels) == stats["channels"]
        assert len(categories) == stats["categories"]
        assert sum(channel["count"] for channel in channels) == len(self.records)
        for channel in channels:
            result = self.json("/api/videos", params={"channel": channel["id"], "page_size": 100})
            expected = {key for key, video in self.cards.items() if video["channel_id"] == channel["id"]}
            assert {video["id"] for video in result["items"]} == expected
            assert result["total"] == channel["count"] == len(expected)
        for category in categories:
            result = self.json("/api/videos", params={"category": category["id"], "page_size": 100})
            expected = {key for key, video in self.cards.items()
                        if any(item["id"] == category["id"] for item in video["categories"])}
            assert {video["id"] for video in result["items"]} == expected
            assert result["total"] == category["count"] == len(expected)
        uncategorized = self.json("/api/videos", params={"category": "uncategorized", "page_size": 100})
        assert {video["id"] for video in uncategorized["items"]} == {
            key for key, video in self.cards.items() if not video["categories"]
        }
        target = next((video for video in self.cards.values() if video["categories"]), None)
        if target is None:
            print("       WARNING: no classified active video available to test combined filters")
            return
        params = {"channel": target["channel_id"], "category": target["categories"][0]["id"],
                  "language": target["language"], "page_size": 100}
        result = self.json("/api/videos", params=params)
        expected = {key for key, video in self.cards.items()
                    if video["channel_id"] == params["channel"] and video["language"] == params["language"]
                    and any(item["id"] == params["category"] for item in video["categories"])}
        assert {video["id"] for video in result["items"]} == expected

    def check_search(self):
        candidates = [(key, word) for key, record in self.records.items()
                      for word in re.findall(r"[^\W\d_]+", record["meta"]["title"])
                      if len(word) >= 5 and normalized(word) != word.casefold()]
        if candidates:
            video_id, word = max(candidates, key=lambda candidate: len(candidate[1]))
            result = self.json("/api/videos", params={"q": normalized(word).upper(), "page_size": 100})
            assert video_id in {video["id"] for video in result["items"]}, f"Title not found: {word}"
            accented = self.json("/api/videos", params={"q": word, "page_size": 100})
            assert {video["id"] for video in accented["items"]} == {video["id"] for video in result["items"]}
        else:
            print("       WARNING: no active accented title available for this scenario")
        longest_id = max(self.records, key=lambda key: self.records[key]["meta"].get("duration", 0))
        record = self.records[longest_id]
        words = [(len(word), word) for segment in record["segments"]
                 for word in re.findall(r"[^\W\d_]+", segment.get("text", ""))
                 if len(word) >= 9 and normalized(word) not in normalized(record["meta"]["title"])]
        assert words, "No transcript search term found in the source"
        _, transcript_word = max(words)
        result = self.json("/api/videos", params={"q": transcript_word, "page_size": 100})
        match_video = next((video for video in result["items"] if video["id"] == longest_id), None)
        assert match_video, f"Transcript not found: {transcript_word}"
        match = match_video.get("match")
        assert match and match.get("segment_index") is not None, "Result has no segment position"
        segment = self.json(f"/api/videos/{longest_id}/segments", params={"offset": match["segment_index"], "limit": 1})["items"][0]
        assert normalized(transcript_word) in normalized(segment["text"])
        assert match["start_ms"] == segment["start_ms"]
        filtered = self.json(f"/api/videos/{longest_id}/segments", params={"q": normalized(transcript_word).upper(), "limit": 300})
        assert filtered["total"] > 0
        assert all(normalized(transcript_word) in normalized(segment["text"]) for segment in filtered["items"])
        assert self.json("/api/videos", params={"q": "zzzznonexistentterm987654321"})["total"] == 0

    def check_long_transcript(self):
        video_id = max(self.records, key=lambda key: self.records[key]["meta"].get("duration", 0))
        source = self.records[video_id]["segments"]
        previous_end = -1
        seen = 0
        for offset in range(0, len(source), 100):
            result = self.json(f"/api/videos/{video_id}/segments", params={"offset": offset, "limit": 100})
            assert result["total"] == len(source)
            assert result["offset"] == offset
            assert len(result["items"]) == min(100, len(source) - offset)
            for index, segment in enumerate(result["items"], start=offset):
                original = source[index]
                assert segment["index"] == index
                assert segment["start_ms"] == offset_ms(original, "start"), f"Incorrect millisecond offset in segment {index}"
                assert segment["end_ms"] == offset_ms(original, "end")
                assert segment["text"].strip() == original["text"].strip()
                assert segment["start_ms"] >= previous_end, "Segments are out of order"
                assert segment["end_ms"] >= segment["start_ms"]
                previous_end = segment["start_ms"]
                seen += 1
        assert seen == len(source)
        print(f"       Longest video: {video_id}, {self.records[video_id]['meta']['duration']}s, {seen} segments")
        assert self.json(f"/api/videos/{video_id}/segments", params={"offset": len(source) + 50})["items"] == []

    def check_details_and_speakers(self):
        method_id = next((key for key, value in self.records.items() if value["method"]), None)
        if method_id:
            detail = self.json(f"/api/videos/{method_id}")
            assert detail["has_method"] is True and detail["method"].strip()
            assert not detail["method"].lstrip().startswith("---"), "YAML exposed as method content"
            assert len(detail["suggestions"]) <= 3
            assigned = {category["id"] for category in detail["categories"]}
            assert not assigned.intersection(category["id"] for category in detail["suggestions"])
        else:
            print("       WARNING: no active video with a method analysis available for this scenario")
        missing = next((key for key, value in self.records.items() if not value["method"]), None)
        if missing:
            without = self.json(f"/api/videos/{missing}")
            assert without["has_method"] is False and without["method"] is None
        else:
            print("       WARNING: no video without a method analysis available in this collection")
        speaker_ids = [key for key, value in self.records.items() if any(segment.get("speaker") for segment in value["segments"])]
        if not speaker_ids:
            print("       WARNING: no active transcript with speaker labels available to test")
        for video_id in speaker_ids:
            detail = self.json(f"/api/videos/{video_id}")
            assert detail["has_speakers"] is True
            original = self.records[video_id]["segments"]
            first_speaker_index = next(index for index, segment in enumerate(original) if segment.get("speaker"))
            segment = self.json(f"/api/videos/{video_id}/segments", params={"offset": first_speaker_index, "limit": 1})["items"][0]
            assert segment["speaker"] == original[first_speaker_index]["speaker"]
            filenames = {entry["filename"] for entry in detail["downloads"]}
            assert {"transcript.json", "transcript.srt", "transcript.txt", "transcript-speakers.json", "transcript-speakers.srt", "transcript-speakers.txt"} <= filenames
        # Downloads remain part of acceptance even when the only speaker example
        # has intentionally been moved to Trash.
        for video_id in speaker_ids or [next(iter(self.records))]:
            detail = self.json(f"/api/videos/{video_id}")
            filenames = {entry["filename"] for entry in detail["downloads"]}
            assert {"transcript.json", "transcript.srt", "transcript.txt"} <= filenames
            for entry in detail["downloads"]:
                path = self.records[video_id]["directory"] / entry["filename"]
                assert path.parent == self.records[video_id]["directory"] and path.is_file()
                body, _ = self.get(entry["url"])
                assert hashlib.sha256(body).hexdigest() == digest(path), f"Download differs from the source: {entry['filename']}"

    def check_http_boundaries(self):
        video_id = next(iter(self.records))
        for path in ["/api/not-a-real-endpoint", "/api/videos/does-not-exist",
                     f"/api/videos/{video_id}/files/Dockerfile",
                     f"/api/videos/{video_id}/files/%2e%2e%2fvideo.json",
                     f"/api/videos/{video_id}/files/%252e%252e%252fvideo.json"]:
            error = self.json(path, expected=404)
            assert isinstance(error.get("detail"), str), f"Error response has no message: {path}"
        thumbnail, headers = self.get(f"/api/videos/{video_id}/thumbnail")
        assert headers.get("Content-Type", "").startswith("image/") and thumbnail
        root, headers = self.get("/")
        assert "text/html" in headers.get("Content-Type", "")
        html = root.decode("utf-8")
        assert "<html" in html.lower() and "<script" in html.lower(), "Compiled interface was not served"
        deep, headers = self.get(f"/videos/{video_id}")
        assert "text/html" in headers.get("Content-Type", "") and deep == root, "SPA route does not work"
        assets = re.findall(r'(?:src|href)=["\']([^"\']+\.(?:js|css)(?:\?[^"\']*)?)["\']', html)
        assert assets, "No local interface assets found"
        for asset in assets:
            assert not urlsplit(asset).scheme and not asset.startswith("//"), f"External interface dependency: {asset}"
            body, _ = self.get(asset)
            assert body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--source", type=Path, default=Path("../yt-transcripts"))
    parser.add_argument("--wait", type=int, default=90, help="Seconds to wait for the initial import")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_dir():
        parser.error(f"Source directory not found: {source}")
    if urlsplit(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Use the local catalog address (localhost or loopback).")
    before = manifest(source)
    suite = Acceptance(args.url, source)
    print(f"Read-only verification: {args.url}; {len(before)} original files protected by SHA-256", flush=True)
    try:
        suite.wait_ready(args.wait)
    except Exception as error:
        print(f"FAILED readiness: {error}")
        return 1
    for name, check in [
        ("explicit Trash listing and sources of deleted videos", suite.check_trash_inventory),
        ("actual source inventory and statistics", suite.check_inventory),
        ("pagination, titles and availability", suite.check_pagination),
        ("channels, categories and combined filters", suite.check_filters),
        ("accent-insensitive search and matching segment access", suite.check_search),
        ("progressive reading and timestamps of the longest video", suite.check_long_transcript),
        ("methods, speakers and downloads identical to the sources", suite.check_details_and_speakers),
        ("API errors, path restrictions and local interface", suite.check_http_boundaries),
    ]:
        suite.run(name, check)

    def check_source_untouched():
        after = manifest(source)
        changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
        assert not changed, f"Original files changed during verification: {changed}"

    suite.run("original sources unchanged (SHA-256)", check_source_untouched)
    print(f"\nResult: {suite.passed} checks passed; {suite.failed} failures.")
    return 1 if suite.failed else 0


if __name__ == "__main__":
    sys.exit(main())
