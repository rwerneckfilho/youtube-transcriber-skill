#!/usr/bin/env python3
"""Create two fake, explicitly disposable videos for permanent-deletion UI tests."""

import json
from pathlib import Path
import tempfile
import wave


def main():
    root = Path(tempfile.mkdtemp(prefix="youtube-catalog-permanent-"))
    source, data = root / "sources", root / "data"
    source.mkdir()
    data.mkdir()
    entries = {
        "permanent_remove": "[TESTE DESCARTÁVEL] Vídeo para exclusão definitiva",
        "permanent_keep": "[TESTE DESCARTÁVEL] Vídeo que deve permanecer",
    }
    for video_id, title in entries.items():
        folder = source / video_id
        folder.mkdir()
        meta = {"id": video_id, "title": title, "channel": "Canal de testes descartáveis",
                "channel_id": "permanent-test-channel", "duration": 20, "language": "pt",
                "upload_date": "20260905", "description": "Conteúdo artificial criado exclusivamente para testes.",
                "tags": ["teste", "descartável"]}
        (folder / "video.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        text = "Esta transcrição artificial pode ser apagada durante o teste."
        transcript = {"result": {"language": "pt"}, "transcription": [
            {"offsets": {"from": 0, "to": 5000}, "text": text},
            {"offsets": {"from": 5000, "to": 10000}, "text": "O outro vídeo deve permanecer intacto."},
        ]}
        (folder / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
        (folder / "transcript.txt").write_text(text + "\n", encoding="utf-8")
        (folder / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:05,000\n" + text + "\n", encoding="utf-8")
        (folder / "METODO.md").write_text("---\ncluster_id: C1\ncluster: Testes descartáveis\n---\n# Método\n\nSomente uma fixture de teste.\n", encoding="utf-8")
        with wave.open(str(folder / "audio.wav"), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\x00\x00" * 3200)
        nested = folder / "artifacts"
        nested.mkdir()
        (nested / "fixture.bin").write_bytes(b"Disposable nested test artifact\n")
    manifest = {"purpose": "youtube-catalog-permanent-deletion-test", "version": 1,
                "source": str(source), "remove_id": "permanent_remove", "keep_id": "permanent_keep",
                "videos": entries}
    (root / ".permanent-test-fixture.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"root": str(root), "source": str(source), "data": str(data),
                      "remove_id": manifest["remove_id"], "keep_id": manifest["keep_id"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
