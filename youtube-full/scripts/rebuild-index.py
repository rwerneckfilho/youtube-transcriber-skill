#!/usr/bin/env python3
"""Rebuild the Markdown index for a yt-transcripts collection."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def escape_cell(value: object) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ").strip()


def format_duration(seconds: object) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        return "—"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def file_links(folder: Path) -> str:
    labels = (
        ("transcript.txt", "TXT"),
        ("transcript.srt", "SRT"),
        ("transcript.json", "JSON"),
        ("source.m4a", "Áudio M4A"),
        ("audio.wav", "Áudio WAV"),
        ("video.json", "Metadados"),
    )
    return " · ".join(
        f"[{label}]({folder.name}/{name})" for name, label in labels if (folder / name).is_file()
    ) or "—"


def collect_entries(root: Path) -> list[dict[str, str]]:
    entries = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        metadata_path = folder / "video.json"
        if not metadata_path.is_file():
            continue
        metadata = read_json(metadata_path)
        transcript = read_json(folder / "transcript.json")
        transcript_file = folder / "transcript.txt"
        timestamp_source = transcript_file if transcript_file.exists() else metadata_path
        added = dt.datetime.fromtimestamp(timestamp_source.stat().st_mtime).astimezone().date().isoformat()
        video_id = str(metadata.get("id") or folder.name)
        webpage_url = str(metadata.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}")
        language = (transcript.get("result") or {}).get("language") or "—"
        entries.append(
            {
                "added": added,
                "title": escape_cell(metadata.get("title") or folder.name),
                "url": webpage_url,
                "channel": escape_cell(metadata.get("uploader") or metadata.get("channel")),
                "duration": format_duration(metadata.get("duration")),
                "language": escape_cell(language),
                "files": file_links(folder),
            }
        )
    return entries


def build_markdown(entries: list[dict[str, str]]) -> str:
    lines = [
        "# Índice de transcrições do YouTube",
        "",
        "Atualizado automaticamente pela skill `youtube-full`.",
        "",
        "| Adicionado | Vídeo | Canal | Duração | Idioma | Arquivos |",
        "|---|---|---|---:|---|---|",
    ]
    for entry in entries:
        lines.append(
            f"| {entry['added']} | [{entry['title']}]({entry['url']}) | {entry['channel']} | "
            f"{entry['duration']} | {entry['language']} | {entry['files']} |"
        )
    if not entries:
        lines.append("| — | Nenhuma transcrição adicionada | — | — | — | — |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="yt-transcripts collection directory")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "INDEX.md"
    temporary = root / ".INDEX.md.tmp"
    temporary.write_text(build_markdown(collect_entries(root)), encoding="utf-8")
    temporary.replace(target)
    print(target)


if __name__ == "__main__":
    main()
