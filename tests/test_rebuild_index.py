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
    / "rebuild-index.py"
)
SPEC = importlib.util.spec_from_file_location("rebuild_index", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RebuildIndexTests(unittest.TestCase):
    def test_includes_diarized_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "video-id"
            folder.mkdir()
            (folder / "video.json").write_text(
                json.dumps(
                    {
                        "id": "video-id",
                        "title": "Example",
                        "uploader": "Channel",
                        "duration": 65,
                    }
                ),
                encoding="utf-8",
            )
            (folder / "transcript.json").write_text(
                json.dumps({"result": {"language": "en"}}),
                encoding="utf-8",
            )
            (folder / "transcript.txt").write_text("text\n", encoding="utf-8")
            (folder / "transcript-speakers.txt").write_text(
                "SPEAKER_00\ntext\n", encoding="utf-8"
            )
            (folder / "diarization.json").write_text("{}\n", encoding="utf-8")

            entry = MODULE.collect_entries(Path(temporary))[0]
            self.assertIn("TXT with speakers", entry["files"])
            self.assertIn("Diarization", entry["files"])
            self.assertEqual(entry["duration"], "1:05")


if __name__ == "__main__":
    unittest.main()
