import json
from pathlib import Path

import pytest

from backend.catalog import Catalog


@pytest.fixture
def collection(tmp_path):
    source = tmp_path / "sources"
    source.mkdir()
    return source


@pytest.fixture
def catalog(collection, tmp_path):
    return Catalog(collection, tmp_path / "data")


def make_video(source: Path, video_id="video_1", title="Automação com agentes", channel="Canal Café", category="C1", category_name="Agentes e operações", texts=None, seconds=False, speakers=False):
    folder = source / video_id
    folder.mkdir(exist_ok=True)
    metadata = {"id": video_id, "title": title, "channel": channel, "channel_id": "channel-" + channel,
                "duration": 22300, "language": "pt-BR", "upload_date": "20260813",
                "description": "Operações de empresas com agentes e processos documentados.",
                "tags": ["processos", "automação"], "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"}
    (folder / "video.json").write_text(json.dumps(metadata), encoding="utf-8")
    texts = texts or ["Ação e estratégia no início.", "Automação para uma operação eficiente.", "Um teste ao fim do vídeo."]
    segments = []
    for index, text in enumerate(texts):
        segment = {"text": text}
        if seconds:
            segment.update(start=index * 10.25, end=index * 10.25 + 5.75)
        else:
            segment["offsets"] = {"from": index * 10250, "to": index * 10250 + 5750}
        if speakers:
            segment["speaker"] = f"SPEAKER_0{index % 2}"
        segments.append(segment)
    transcript = {"result": {"language": "pt"}, "segments" if seconds else "transcription": segments}
    (folder / "transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    (folder / "transcript.txt").write_text("\n".join(texts), encoding="utf-8")
    (folder / "transcript.srt").write_text("1\n00:00:00,000 --> 00:00:05,750\n" + texts[0], encoding="utf-8")
    if category:
        (folder / "METODO.md").write_text(f'---\ncluster_id: {category}\ncluster: "{category_name}"\ntags: ["escala", "agentes"]\n---\n\n# Método\n\nPrática detalhada.', encoding="utf-8")
    return folder
