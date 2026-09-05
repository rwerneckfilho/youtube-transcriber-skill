#!/usr/bin/env python3
"""Run local speaker diarization and merge it with whisper.cpp output."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "pyannote/speaker-diarization-community-1"
UNKNOWN_SPEAKER = "UNKNOWN"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read JSON from {path}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def normalize_turns(raw_turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns = []
    for raw in raw_turns:
        try:
            start_ms = max(0, round(float(raw["start"]) * 1000))
            end_ms = max(start_ms, round(float(raw["end"]) * 1000))
            speaker = str(raw["speaker"]).strip()
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid diarization turn: {raw!r}") from error
        if speaker and end_ms > start_ms:
            turns.append(
                {
                    "start": start_ms / 1000,
                    "end": end_ms / 1000,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "speaker": speaker,
                }
            )
    return sorted(turns, key=lambda turn: (turn["start_ms"], turn["end_ms"], turn["speaker"]))


def serialized_turns(output: Any) -> list[dict[str, Any]]:
    if hasattr(output, "serialize"):
        serialized = output.serialize()
        raw_turns = serialized.get("exclusive_diarization") or serialized.get("diarization") or []
        return normalize_turns(raw_turns)

    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", output)
    raw_turns = [
        {"start": turn.start, "end": turn.end, "speaker": speaker}
        for turn, _, speaker in annotation.itertracks(yield_label=True)
    ]
    return normalize_turns(raw_turns)


def choose_speaker(start_ms: int, end_ms: int, turns: list[dict[str, Any]]) -> tuple[str, int]:
    overlap_by_speaker: dict[str, int] = defaultdict(int)
    for turn in turns:
        overlap = min(end_ms, turn["end_ms"]) - max(start_ms, turn["start_ms"])
        if overlap > 0:
            overlap_by_speaker[turn["speaker"]] += overlap
    if not overlap_by_speaker:
        return UNKNOWN_SPEAKER, 0
    speaker, overlap_ms = min(
        overlap_by_speaker.items(),
        key=lambda item: (-item[1], item[0]),
    )
    return speaker, overlap_ms


def assign_speakers(
    transcript: dict[str, Any], turns: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    assigned = []
    for index, segment in enumerate(transcript.get("transcription") or []):
        offsets = segment.get("offsets") or {}
        try:
            start_ms = int(offsets["from"])
            end_ms = int(offsets["to"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Transcript segment {index} has invalid offsets") from error
        speaker, overlap_ms = choose_speaker(start_ms, end_ms, turns)
        enriched = dict(segment)
        enriched["speaker"] = speaker
        enriched["speaker_overlap_ms"] = overlap_ms
        assigned.append(enriched)
    return assigned


def format_timestamp(milliseconds: int, *, decimal: str = ".") -> str:
    milliseconds = max(0, int(milliseconds))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{decimal}{millis:03d}"


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def write_outputs(
    output_dir: Path,
    transcript: dict[str, Any],
    turns: list[dict[str, Any]],
    *,
    diarization_model: str,
    speaker_options: dict[str, int],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    assigned = assign_speakers(transcript, turns)
    speakers = sorted({turn["speaker"] for turn in turns})

    diarization_payload = {
        "schema_version": 1,
        "engine": "pyannote.audio",
        "model": diarization_model,
        "processing": "local",
        "speaker_options": speaker_options,
        "speakers": speakers,
        "turns": turns,
    }
    transcript_payload = dict(transcript)
    transcript_payload["diarization"] = {
        "engine": "pyannote.audio",
        "model": diarization_model,
        "processing": "local",
        "speakers": speakers,
    }
    transcript_payload["transcription"] = assigned

    diarization_path = output_dir / "diarization.json"
    transcript_json_path = output_dir / "transcript-speakers.json"
    transcript_txt_path = output_dir / "transcript-speakers.txt"
    transcript_srt_path = output_dir / "transcript-speakers.srt"
    write_json(diarization_path, diarization_payload)
    write_json(transcript_json_path, transcript_payload)

    txt_blocks = []
    srt_blocks = []
    for index, segment in enumerate(assigned, start=1):
        start_ms = int(segment["offsets"]["from"])
        end_ms = int(segment["offsets"]["to"])
        speaker = segment["speaker"]
        text = str(segment.get("text") or "").strip()
        txt_blocks.append(
            f"[{format_timestamp(start_ms)} --> {format_timestamp(end_ms)}] {speaker}\n{text}"
        )
        srt_blocks.append(
            f"{index}\n"
            f"{format_timestamp(start_ms, decimal=',')} --> {format_timestamp(end_ms, decimal=',')}\n"
            f"[{speaker}] {text}"
        )
    transcript_txt_path.write_text("\n\n".join(txt_blocks) + "\n", encoding="utf-8")
    transcript_srt_path.write_text("\n\n".join(srt_blocks) + "\n", encoding="utf-8")

    return [
        transcript_txt_path,
        transcript_srt_path,
        transcript_json_path,
        diarization_path,
    ]


def run_pyannote(
    audio_path: Path,
    *,
    model: str,
    device: str,
    num_speakers: int | None,
    min_speakers: int | None,
    max_speakers: int | None,
) -> list[dict[str, Any]]:
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    matplotlib_cache = Path(
        os.environ.setdefault("MPLCONFIGDIR", str(cache_root / "youtube-full" / "matplotlib"))
    )
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    try:
        import torch
        from pyannote.audio import Pipeline
    except ImportError as error:
        raise RuntimeError(
            "Missing diarization dependencies. Run scripts/setup-diarization.sh first."
        ) from error

    model_path = Path(model).expanduser()
    model_source = str(model_path.resolve()) if model_path.exists() else model
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    load_options = {"token": token} if token else {}
    try:
        pipeline = Pipeline.from_pretrained(model_source, **load_options)
    except Exception as error:
        raise RuntimeError(
            f"Could not load diarization model {model!r}. Accept its Hugging Face terms and "
            "authenticate with HF_TOKEN, or pass a downloaded local model directory. "
            f"Original error: {error}"
        ) from error
    if pipeline is None:
        raise RuntimeError(
            "Could not load the diarization model. Accept its Hugging Face terms and set HF_TOKEN, "
            "or pass a downloaded local model directory."
        )

    if device != "cpu":
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested but is not available.")
        if device == "mps" and not (
            hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        ):
            raise RuntimeError("MPS was requested but is not available.")
        pipeline.to(torch.device(device))

    apply_options = {
        key: value
        for key, value in {
            "num_speakers": num_speakers,
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
        }.items()
        if value is not None
    }
    try:
        output = pipeline(str(audio_path.resolve()), **apply_options)
    except Exception as error:
        raise RuntimeError(f"Local pyannote inference failed: {error}") from error
    return serialized_turns(output)


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    parser.add_argument("--num-speakers", type=positive_integer)
    parser.add_argument("--min-speakers", type=positive_integer)
    parser.add_argument("--max-speakers", type=positive_integer)
    args = parser.parse_args()
    if args.num_speakers is not None and (
        args.min_speakers is not None or args.max_speakers is not None
    ):
        parser.error("--num-speakers cannot be combined with --min-speakers or --max-speakers")
    if (
        args.min_speakers is not None
        and args.max_speakers is not None
        and args.min_speakers > args.max_speakers
    ):
        parser.error("--min-speakers cannot be greater than --max-speakers")
    return args


def main() -> int:
    args = parse_args()
    try:
        transcript = read_json(args.transcript)
        turns = run_pyannote(
            args.audio,
            model=args.model,
            device=args.device,
            num_speakers=args.num_speakers,
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
        )
        speaker_options = {
            key: value
            for key, value in {
                "num_speakers": args.num_speakers,
                "min_speakers": args.min_speakers,
                "max_speakers": args.max_speakers,
            }.items()
            if value is not None
        }
        outputs = write_outputs(
            args.output_dir,
            transcript,
            turns,
            diarization_model=args.model,
            speaker_options=speaker_options,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Speaker diarization failed: {error}", file=sys.stderr)
        return 1

    print("Speaker diarization outputs:")
    for path in outputs:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
