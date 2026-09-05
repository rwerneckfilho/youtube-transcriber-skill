from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "youtube-full"
    / "scripts"
    / "diarize-transcript.py"
)
SPEC = importlib.util.spec_from_file_location("diarize_transcript", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DiarizeTranscriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.turns = MODULE.normalize_turns(
            [
                {"start": 0.0, "end": 4.0, "speaker": "SPEAKER_00"},
                {"start": 4.0, "end": 9.0, "speaker": "SPEAKER_01"},
            ]
        )
        self.transcript = {
            "result": {"language": "pt"},
            "transcription": [
                {
                    "timestamps": {"from": "00:00:00,000", "to": "00:00:03,000"},
                    "offsets": {"from": 0, "to": 3000},
                    "text": " Primeira fala.",
                },
                {
                    "timestamps": {"from": "00:00:03,000", "to": "00:00:07,000"},
                    "offsets": {"from": 3000, "to": 7000},
                    "text": " Segunda fala.",
                },
                {
                    "timestamps": {"from": "00:00:10,000", "to": "00:00:11,000"},
                    "offsets": {"from": 10000, "to": 11000},
                    "text": " Sem voz correspondente.",
                },
            ],
        }

    def test_assigns_speaker_with_largest_overlap(self) -> None:
        assigned = MODULE.assign_speakers(self.transcript, self.turns)
        self.assertEqual(assigned[0]["speaker"], "SPEAKER_00")
        self.assertEqual(assigned[1]["speaker"], "SPEAKER_01")
        self.assertEqual(assigned[1]["speaker_overlap_ms"], 3000)
        self.assertEqual(assigned[2]["speaker"], "UNKNOWN")

    def test_ties_are_deterministic(self) -> None:
        speaker, overlap = MODULE.choose_speaker(3000, 5000, self.turns)
        self.assertEqual((speaker, overlap), ("SPEAKER_00", 1000))

    def test_writes_txt_srt_and_json_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            paths = MODULE.write_outputs(
                output_dir,
                self.transcript,
                self.turns,
                diarization_model=MODULE.DEFAULT_MODEL,
                speaker_options={"num_speakers": 2},
            )
            self.assertEqual(len(paths), 4)
            self.assertTrue(all(path.is_file() for path in paths))
            txt = (output_dir / "transcript-speakers.txt").read_text(encoding="utf-8")
            srt = (output_dir / "transcript-speakers.srt").read_text(encoding="utf-8")
            payload = json.loads(
                (output_dir / "transcript-speakers.json").read_text(encoding="utf-8")
            )
            self.assertIn("SPEAKER_00\nPrimeira fala.", txt)
            self.assertIn("[SPEAKER_01] Segunda fala.", srt)
            self.assertEqual(payload["diarization"]["processing"], "local")
            self.assertEqual(payload["transcription"][2]["speaker"], "UNKNOWN")

    def test_formats_long_srt_timestamp(self) -> None:
        self.assertEqual(
            MODULE.format_timestamp(3_726_045, decimal=","),
            "01:02:06,045",
        )


if __name__ == "__main__":
    unittest.main()
