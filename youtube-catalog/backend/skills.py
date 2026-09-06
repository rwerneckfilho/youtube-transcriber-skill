"""Local skill recommendations, immutable source snapshots and a durable worker."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import math
import re
import threading
import uuid
import zipfile
from collections import Counter
from pathlib import PurePosixPath

from .catalog import VIDEO_ID, normalize, now

LOG = logging.getLogger(__name__)
ACTIVE = ("queued", "reading", "generating", "packaging")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
MAX_PACKAGE = 16 * 1024 * 1024
SCHEMA = """
CREATE TABLE IF NOT EXISTS skill_jobs (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL, video_ids TEXT NOT NULL,
 language TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
 message TEXT NOT NULL DEFAULT '', error_code TEXT, error TEXT,
 cancel_requested INTEGER NOT NULL DEFAULT 0, sources TEXT NOT NULL,
 result TEXT, archive BLOB
);
CREATE INDEX IF NOT EXISTS skill_jobs_created ON skill_jobs(created_at DESC,id);
"""
STOP = set("""a o as os um uma uns umas de da do das dos e em para por com sem que se ao aos na no nas nos ou como sobre mais muito sua seu suas seus isso esta este voce voces sao ser ter fazer usar pode quando onde entre todo todos passo passos metodo metodos processo processos descricao principio central necessario necessarias necessario curso video videos aula tutorial introducao completo completa parte canal youtube transcript transcricao the a an and or for to of in on with from by at is are be it its this that these those you your we our how what why when where can will get use using make full course video videos tutorial guide introduction step steps method process description central principle o el la los las un una de del y en para por con sin que se como su sus este esta usted curso completo proceso metodo ai ia artificial intelligence inteligencia aprender learn learning hoje today agora now links link subscribe subscription inscricao inscreva siga follow instagram facebook twitter patreon sponsor sponsorship patrocinio obrigado thanks www http https com org""".split())
GENERIC_TAGS = {"ai", "ia", "artificial intelligence", "inteligencia artificial", "course", "curso", "tutorial", "youtube", "education", "educacao", "technology", "tecnologia", "app", "apps", "business", "empreendedorismo", "entrepreneurship"}


class SkillError(Exception):
    def __init__(self, code, message, status=422):
        self.code, self.status = code, status
        super().__init__(message)


class SkillCancelled(Exception):
    pass


class SkillInterrupted(Exception):
    pass


def tokens(text):
    return [word for word in re.findall(r"[a-z0-9]+", normalize(text)) if len(word) > 2 and word not in STOP and not word.isdigit()]


def method_lines(method):
    """Only the source-specific description/principle/process, never template sections."""
    selected, active = [], False
    for line in (method or "").splitlines():
        heading = re.match(r"^#{1,6}\s+(.+)", line.strip())
        if heading:
            label = normalize(heading[1]).strip()
            active = label in {"descricao", "description", "descripcion", "principio central", "core principle", "processo", "process", "proceso", "procedure"}
        elif active and line.strip() and not line.strip().startswith("<!--"):
            selected.append(re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip())
    return selected


def recommendations(catalog, language="pt"):
    with catalog.db() as conn:
        rows = list(conn.execute("""SELECT v.* FROM videos v WHERE deleted_at IS NULL AND available=1
            AND EXISTS(SELECT 1 FROM segments s WHERE s.video_id=v.id AND trim(s.text)<>'')
            AND NOT EXISTS(SELECT 1 FROM purge_jobs p WHERE p.video_id=v.id) ORDER BY v.id"""))
        categories = catalog.effective_categories(conn, [r["id"] for r in rows])
    if len(rows) < 2:
        return {"items": [], "total": 0}
    lines = {r["id"]: method_lines(r["method"]) for r in rows}
    # Repeated sentences are shared templates, even inside an otherwise useful section.
    repeated = Counter(normalize(line) for group in lines.values() for line in set(group))
    repeated_descriptions = Counter(normalize(line.strip()) for row in rows for line in set(row["description"].splitlines()) if line.strip())
    tag_labels = {}
    tag_frequency = Counter()
    channel_frequency = Counter(row["channel_id"] for row in rows)
    channel_tags = Counter()
    for row in rows:
        tags = {normalize(str(tag)).strip(): str(tag).strip() for tag in json.loads(row["tags"])}
        tags = {key: value for key, value in tags.items() if key not in GENERIC_TAGS and tokens(key)}
        tag_labels[row["id"]] = tags
        tag_frequency.update(tags.keys())
        channel_tags.update((row["channel_id"], key) for key in tags)
    counts, anchors, method_terms = {}, {}, {}
    for row in rows:
        video_id = row["id"]
        channel_words = set(tokens(row["channel"]))
        title = [term for term in tokens(row["title"]) if term not in channel_words]
        method = " ".join(line for line in lines[video_id] if repeated[normalize(line)] == 1)[:10000]
        specific = set(title + tokens(method))
        tags = {key: value for key, value in tag_labels[video_id].items()
                if (len(rows) < 5 or tag_frequency[key] / len(rows) <= .65)
                and not (set(tokens(key)) <= channel_words)
                and not (channel_frequency[row["channel_id"]] >= 3
                         and channel_tags[(row["channel_id"], key)] / channel_frequency[row["channel_id"]] >= .6
                         and len(set(tokens(key)) & specific) < min(2, len(set(tokens(key))))) }
        tag_labels[video_id] = tags
        tag_words = tokens(" ".join(tags))
        anchors[video_id] = set(title + tag_words)
        description = " ".join(line for line in row["description"].splitlines() if "http" not in line and repeated_descriptions[normalize(line.strip())] == 1 and not re.search(r"subscribe|inscreva|patreon|sponsor|instagram", line, re.I))[:6000]
        method_terms[video_id] = set(tokens(method))
        counts[video_id] = Counter(term for term in title * 3 + tag_words * 4 + tokens(description) + tokens(method) * 2 if term not in channel_words)
    frequency = Counter(term for values in counts.values() for term in values)
    vectors = {}
    for video_id, values in counts.items():
        weighted = {term: (1 + math.log(count)) * math.log(1 + len(rows) / frequency[term]) for term, count in values.items()}
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1
        vectors[video_id] = {term: value / norm for term, value in weighted.items()}
    by_id = {row["id"]: row for row in rows}
    cat_ids = {key: {item["id"] for item in value} for key, value in categories.items()}
    edges, evidence = {}, {}
    ids = list(by_id)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            common = set(vectors[left]) & set(vectors[right])
            shared_tags = set(tag_labels[left]) & set(tag_labels[right])
            shared_anchors = anchors[left] & anchors[right]
            similarity = sum(vectors[left][term] * vectors[right][term] for term in common)
            # Category is a prior only; it can never create a connection by itself.
            related = ((shared_tags and similarity >= .10)
                       or (len(common) >= 3 and shared_anchors and similarity >= .22)
                       or (len(common) >= 4 and len(method_terms[left] & method_terms[right]) >= 3 and similarity >= .14))
            unique_left = set(counts[left]) - set(counts[right])
            unique_right = set(counts[right]) - set(counts[left])
            if not related or len(unique_left) < 2 or len(unique_right) < 2 or similarity > .97:
                continue
            diversity = by_id[left]["channel_id"] != by_id[right]["channel_id"]
            same_category = bool(cat_ids.get(left, set()) & cat_ids.get(right, set()))
            score = similarity + min(len(shared_tags), 3) * .08 + same_category * .06 + diversity * .03
            pair = tuple(sorted((left, right)))
            edges[pair] = score
            evidence[pair] = sorted(common, key=lambda term: -(vectors[left][term] * vectors[right][term]))[:5]
    candidates = []
    for pair, score in sorted(edges.items(), key=lambda item: (-item[1], item[0])):
        group = list(pair)
        # Every added source must have a meaningful connection to every group member.
        while len(group) < 4:
            additions = [(sum(edges[tuple(sorted((candidate, member)))] for member in group) / len(group), candidate)
                         for candidate in ids if candidate not in group and all(tuple(sorted((candidate, member))) in edges for member in group)]
            if not additions:
                break
            additions.sort(key=lambda item: (-item[0], item[1]))
            group.append(additions[0][1])
        if any(set(group) == set(previous[1]) for previous in candidates):
            continue
        candidates.append((score, group, pair))
    items, used = [], Counter()
    for _, group, pair in candidates:
        if any(used[video_id] >= 2 for video_id in group) or any(len(set(group) & set(item["video_ids"])) / len(set(group) | set(item["video_ids"])) > .6 for item in items):
            continue
        common_tags = set.intersection(*(set(tag_labels[video_id]) for video_id in group))
        labels = [tag_labels[group[0]][key] for key in sorted(common_tags)][:5]
        theme = ", ".join(labels[:3] or evidence[pair][:3])
        # Presentation only: use one supported topic, preferring its concise
        # form over variants such as "app development for beginners".
        topic_options = labels or evidence[pair]
        topic_words = {label: set(tokens(label)) for label in topic_options}
        concise = [label for label in topic_options if not any(topic_words[other] < topic_words[label] for other in topic_options)]
        topic = min(concise, key=lambda label: (
            -sum(min(vectors[video_id].get(term, 0) for video_id in group) for term in topic_words[label]) / max(1, len(topic_words[label])),
            len(label), normalize(label),
        ))
        common_categories = set.intersection(*(cat_ids.get(video_id, set()) for video_id in group))
        category = next((item["name"] for item in categories.get(group[0], []) if item["id"] in common_categories), None)
        titles = " + ".join(by_id[video_id]["title"] for video_id in group[:2])
        channel_count = len({by_id[video_id]["channel_id"] for video_id in group})
        texts = {
            "pt": (f"Combinar conhecimentos sobre {topic}", f"Transforme {len(group)} vídeos em um procedimento reutilizável: {titles}.", f"Conexão por {'tags compartilhadas' if labels else 'termos presentes no conteúdo'}: {theme}. Os vídeos trazem detalhes distintos de {channel_count} canal(is)."),
            "en": (f"Combine knowledge about {topic}", f"Turn {len(group)} videos into a reusable procedure: {titles}.", f"Connected by {'shared tags' if labels else 'terms present in the content'}: {theme}. The videos add distinct details from {channel_count} channel(s)."),
            "es": (f"Combinar conocimientos sobre {topic}", f"Convierte {len(group)} vídeos en un procedimiento reutilizable: {titles}.", f"Conexión por {'etiquetas compartidas' if labels else 'términos presentes en el contenido'}: {theme}. Los vídeos aportan detalles distintos de {channel_count} canal(es)."),
        }[language]
        items.append({"id": hashlib.sha256("|".join(sorted(group)).encode()).hexdigest()[:16], "title": texts[0][:120], "description": texts[1], "reason": texts[2], "video_ids": group,
                      "videos": [catalog.summary(by_id[video_id], categories) for video_id in group], "shared_tags": labels, "category": category})
        used.update(group)
        if len(items) >= 8:
            break
    return {"items": items, "total": len(items)}


def package_result(result):
    """Create only a bounded documentation bundle; generated paths never touch disk."""
    if not isinstance(result, dict):
        raise SkillError("invalid_package", "A geração não retornou um pacote de skill válido.")
    name, markdown, files = result.get("name"), result.get("skill_markdown"), result.get("files")
    if not isinstance(name, str) or len(name) > 64 or not NAME.fullmatch(name) or not isinstance(markdown, str) or not markdown.strip():
        raise SkillError("invalid_package", "O nome ou o conteúdo da skill é inválido.")
    if not isinstance(files, list) or not 1 <= len(files) <= 24:
        raise SkillError("invalid_package", "O pacote deve conter entre 1 e 24 arquivos de documentação.")
    total, seen = 0, set()
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str):
            raise SkillError("invalid_package", "O pacote contém um arquivo inválido.")
        path = item["path"]
        allowed = path == "SKILL.md" or bool(re.fullmatch(r"references/[A-Za-z0-9][A-Za-z0-9_-]*\.(?:md|txt|json)", path)) or path == "agents/openai.yaml"
        if not allowed or path in seen or ".." in PurePosixPath(path).parts:
            raise SkillError("invalid_package", "O pacote contém um caminho não permitido ou duplicado.")
        seen.add(path)
        total += len(item["content"].encode("utf-8"))
    if total > MAX_PACKAGE or not any(item["path"] == "SKILL.md" and item["content"] == markdown for item in files):
        raise SkillError("invalid_package", "O pacote excede 16 MiB ou não contém o SKILL.md correspondente.")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            entry = zipfile.ZipInfo(name + "/" + item["path"])
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, item["content"])
    return output.getvalue()


class SkillQueue:
    def __init__(self, catalog, engine=None):
        self.catalog, self._engine = catalog, engine
        self.stop, self.wake = threading.Event(), threading.Event()
        self.run_lock = threading.Lock()
        self.last_checkpoint = None
        self.thread = None
        with catalog.db() as conn:
            conn.executescript(SCHEMA)
            conn.execute("""UPDATE skill_jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,
                stage=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,progress=0,updated_at=?,
                message='A geração interrompida será retomada.',result=NULL,archive=NULL
                WHERE status IN ('reading','generating','packaging')""", (now(),))

    @property
    def engine(self):
        if self._engine is None:
            try:
                from .skill_engine import LocalSkillEngine
                self._engine = LocalSkillEngine()
            except ImportError as exc:
                raise SkillError("engine_unavailable", "O gerador local de skills não está disponível.", 503) from exc
        return self._engine

    def status(self):
        try:
            return self.engine.status()
        except Exception as exc:
            return {"available": False, "model": None, "models": [], "error_code": getattr(exc, "code", "engine_unavailable"), "message": str(exc)}

    def start(self):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self.loop, daemon=True, name="skill-worker")
            self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                LOG.error("Skill worker did not stop within 30 seconds; its next checkpoint will interrupt it")

    def check(self, job_id):
        if self.stop.is_set():
            raise SkillInterrupted()
        with self.catalog.db() as conn:
            row = conn.execute("SELECT cancel_requested FROM skill_jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row[0]:
            raise SkillCancelled()

    def checkpoint(self, job_id, stage, progress, message):
        self.check(job_id)
        if stage not in ACTIVE[1:]:
            stage = "generating"
        progress, message = max(0, min(99, int(progress))), str(message)[:2000]
        current = (job_id, stage, progress, message)
        if current == self.last_checkpoint:
            return
        with self.catalog.db() as conn:
            conn.execute("UPDATE skill_jobs SET status=?,stage=?,progress=?,message=?,updated_at=? WHERE id=?", (stage, stage, progress, message, now(), job_id))
        self.last_checkpoint = current
        self.check(job_id)

    def enqueue(self, video_ids, title="", objective="", language="pt"):
        if not isinstance(video_ids, list) or not 1 <= len(video_ids) <= 8 or any(not isinstance(value, str) or not VIDEO_ID.fullmatch(value) for value in video_ids) or len(set(video_ids)) != len(video_ids):
            raise SkillError("invalid_selection", "Selecione entre 1 e 8 vídeos diferentes.")
        if not isinstance(title, str) or len(title) > 120 or not isinstance(objective, str) or len(objective) > 2000 or language not in ("pt", "en", "es"):
            raise SkillError("invalid_input", "Confira o título, o objetivo e o idioma da skill.")
        title, objective = title.strip(), objective.strip()
        # Snapshot briefly shares the scanner lock; model calls never hold it.
        with self.catalog.scan_lock, self.catalog.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            selected = set(video_ids)
            candidates = conn.execute("""SELECT id,title,video_ids FROM skill_jobs WHERE language=? AND objective=?
                AND cancel_requested=0 AND status IN ('queued','reading','generating','packaging') ORDER BY created_at,id""", (language, objective)).fetchall()
            for candidate in candidates:
                if set(json.loads(candidate["video_ids"])) != selected:
                    continue
                effective_title = title
                if not effective_title:
                    # Resolve the default against the saved snapshot, even if its
                    # source has since been renamed, hidden or permanently removed.
                    original = conn.execute("""SELECT json_extract(source.value,'$.title') FROM skill_jobs job,json_each(job.sources) source
                        WHERE job.id=? AND json_extract(source.value,'$.id')=?""", (candidate["id"], min(selected))).fetchone()
                    effective_title = "Skill: " + original[0][:113] if original else ""
                if candidate["title"] == effective_title:
                    return self.detail(candidate["id"])
            self.capacity(conn)
            sources = []
            for video_id in video_ids:
                row = conn.execute("SELECT * FROM videos WHERE id=? AND deleted_at IS NULL AND available=1 AND NOT EXISTS(SELECT 1 FROM purge_jobs WHERE video_id=?)", (video_id, video_id)).fetchone()
                segments = [dict(segment) for segment in conn.execute("SELECT idx,start_ms,end_ms,text,speaker FROM segments WHERE video_id=? ORDER BY idx", (video_id,))]
                if not row or not segments:
                    raise SkillError("source_unavailable", f"O vídeo {video_id} está indisponível, na Lixeira ou sem transcrição.", 409)
                source = {key: row[key] for key in ("id", "title", "channel", "description", "method", "language", "duration")}
                source.update(tags=json.loads(row["tags"]), segments=segments)
                sources.append(source)
            snapshot = json.dumps(sources, ensure_ascii=False)
            if len(snapshot.encode("utf-8")) > 64 * 1024 * 1024:
                raise SkillError("source_too_large", "As transcrições selecionadas excedem 64 MiB. Gere a skill com menos vídeos.")
            job_id, timestamp = uuid.uuid4().hex, now()
            title = title or "Skill: " + min(sources, key=lambda source: source["id"])["title"][:113]
            conn.execute("""INSERT INTO skill_jobs(id,title,objective,video_ids,language,status,stage,created_at,updated_at,sources)
                VALUES(?,?,?,?,?,'queued','queued',?,?,?)""", (job_id, title, objective, json.dumps(video_ids), language, timestamp, timestamp, snapshot))
        self.wake.set()
        return self.detail(job_id)

    @staticmethod
    def capacity(conn):
        if conn.execute("SELECT count(*) FROM skill_jobs WHERE status IN ('queued','reading','generating','packaging')").fetchone()[0] >= 30:
            raise SkillError("queue_full", "Há 30 skills pendentes. Aguarde ou cancele algumas antes de adicionar outra.", 429)

    def detail(self, job_id, include_files=True):
        columns = "id,title,objective,video_ids,language,status,stage,created_at,updated_at,progress,message,error_code,error"
        payload = "result" if include_files else "json_remove(result,'$.files','$.skill_markdown') AS result"
        with self.catalog.db() as conn:
            row = conn.execute(f"SELECT {columns},{payload},archive IS NOT NULL AS has_archive FROM skill_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return None
        result = {key: row[key] for key in ("id", "title", "objective", "language", "status", "stage", "created_at", "updated_at", "progress", "message", "error_code", "error")}
        result.update(video_ids=json.loads(row["video_ids"]), warnings=[])
        generated = json.loads(row["result"]) if row["result"] else {}
        result.update(warnings=generated.get("warnings", []), skill_name=generated.get("name"), model=generated.get("model"), validation=generated.get("validation"))
        if include_files:
            result.update(files=generated.get("files", []), skill_markdown=generated.get("skill_markdown"))
        if row["status"] == "completed" and row["has_archive"]:
            result["download_url"] = f"/api/skills/{job_id}/download.zip"
        return result

    def list(self, page=1, page_size=20):
        if page < 1 or not 1 <= page_size <= 100:
            raise SkillError("invalid_input", "A página ou o tamanho da lista é inválido.")
        with self.catalog.db() as conn:
            total = conn.execute("SELECT count(*) FROM skill_jobs").fetchone()[0]
            ids = [row[0] for row in conn.execute("SELECT id FROM skill_jobs ORDER BY created_at DESC,id LIMIT ? OFFSET ?", (page_size, (page - 1) * page_size))]
        return {"items": [self.detail(job_id, False) for job_id in ids], "total": total, "page": page, "page_size": page_size}

    def cancel(self, job_id):
        with self.catalog.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM skill_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise SkillError("not_found", "Skill não encontrada.", 404)
            if row[0] not in ACTIVE and row[0] != "cancelled":
                raise SkillError("invalid_state", "Esta geração já terminou.", 409)
            conn.execute("UPDATE skill_jobs SET cancel_requested=1,updated_at=?,message='Cancelamento solicitado.' WHERE id=?", (now(), job_id))
            if row[0] == "queued":
                conn.execute("UPDATE skill_jobs SET status='cancelled',stage='cancelled' WHERE id=?", (job_id,))
        self.wake.set()
        return self.detail(job_id)

    def retry(self, job_id):
        with self.catalog.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM skill_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise SkillError("not_found", "Skill não encontrada.", 404)
            if row[0] not in ("failed", "cancelled"):
                raise SkillError("invalid_state", "Somente gerações com falha ou canceladas podem ser retomadas.", 409)
            self.capacity(conn)
            conn.execute("""UPDATE skill_jobs SET status='queued',stage='queued',progress=0,message='',error_code=NULL,error=NULL,
                cancel_requested=0,result=NULL,archive=NULL,updated_at=? WHERE id=?""", (now(), job_id))
        self.wake.set()
        return self.detail(job_id)

    def download(self, job_id):
        with self.catalog.db() as conn:
            row = conn.execute("SELECT status,result,archive FROM skill_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise SkillError("not_found", "Skill não encontrada.", 404)
        if row["status"] != "completed" or not row["archive"]:
            raise SkillError("invalid_state", "O pacote da skill ainda não está disponível.", 409)
        name = json.loads(row["result"])["name"]
        if not NAME.fullmatch(name):
            raise SkillError("invalid_package", "O nome do pacote armazenado é inválido.")
        return row["archive"], name + ".zip"

    def loop(self):
        while not self.stop.is_set():
            try:
                if self.run_once():
                    continue
            except Exception:
                LOG.exception("Unexpected skill worker failure")
            self.wake.wait(1)
            self.wake.clear()

    def run_once(self):
        if not self.run_lock.acquire(blocking=False):
            return False
        job_id = None
        try:
            if self.stop.is_set():
                return False
            with self.catalog.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT * FROM skill_jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at,id LIMIT 1").fetchone()
                if not row:
                    return False
                job_id = row["id"]
                conn.execute("UPDATE skill_jobs SET status='reading',stage='reading',progress=1,updated_at=? WHERE id=?", (now(), job_id))
            self.checkpoint(job_id, "reading", 3, "Lendo as transcrições selecionadas.")
            generated = self.engine.generate(json.loads(row["sources"]), row["title"], row["objective"], row["language"], lambda stage, progress, message: self.checkpoint(job_id, stage, progress, message))
            self.checkpoint(job_id, "packaging", 95, "Preparando o pacote da skill.")
            archive = package_result(generated)
            self.check(job_id)
            with self.catalog.db() as conn:
                conn.execute("BEGIN IMMEDIATE")
                cancelled = conn.execute("SELECT cancel_requested FROM skill_jobs WHERE id=?", (job_id,)).fetchone()
                if cancelled[0]:
                    raise SkillCancelled()
                if self.stop.is_set():
                    raise SkillInterrupted()
                conn.execute("""UPDATE skill_jobs SET status='completed',stage='completed',progress=100,message='Skill pronta para baixar.',
                    title=?,result=?,archive=?,updated_at=?,error=NULL,error_code=NULL WHERE id=?""", (str(generated.get("title") or row["title"])[:120], json.dumps(generated, ensure_ascii=False), archive, now(), job_id))
            return True
        except (SkillCancelled, SkillInterrupted) as exc:
            if job_id:
                with self.catalog.db() as conn:
                    cancelled = isinstance(exc, SkillCancelled) or conn.execute("SELECT cancel_requested FROM skill_jobs WHERE id=?", (job_id,)).fetchone()[0]
                    state = "cancelled" if cancelled else "queued"
                    conn.execute("UPDATE skill_jobs SET status=?,stage=?,progress=0,message=?,updated_at=? WHERE id=?", (state, state, "Geração cancelada." if cancelled else "A geração será retomada ao iniciar.", now(), job_id))
            return True
        except Exception as exc:
            if not job_id:
                raise
            code = getattr(exc, "code", "generation_failed")
            if isinstance(exc, OSError):
                code = "storage_error"
            LOG.warning("Skill generation %s failed (%s)", job_id, code)
            with self.catalog.db() as conn:
                conn.execute("UPDATE skill_jobs SET status='failed',stage='failed',error_code=?,error=?,message=?,updated_at=? WHERE id=?", (code, str(exc)[:2000], "Não foi possível concluir a skill.", now(), job_id))
            return True
        finally:
            self.run_lock.release()
