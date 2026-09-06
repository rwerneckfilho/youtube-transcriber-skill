import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ApiError, api, date, duration, language, number, useApi, videoUrl } from './api';
import type { Category, Page, Video } from './api';
import { getLocale, t, useLocale } from './i18n';
import type { Locale } from './i18n';
import { Icon } from './Icons';

type SkillState = 'queued' | 'reading' | 'generating' | 'packaging' | 'completed' | 'failed' | 'cancelled';
type SkillJob = { id: string; title: string; objective: string; video_ids: string[]; language: Locale; status: SkillState; stage: string; created_at: string; updated_at: string; progress: number; message?: string; error_code?: string | null; error?: string | null; skill_name?: string; skill_markdown?: string; files?: { path: string; content: string }[]; warnings?: string[]; download_url?: string };
type SkillStatus = { available: boolean; model: string | null; models: string[]; error_code?: string; message?: string };
type Suggestion = { id: string; title: string; description: string; reason: string; video_ids: string[]; videos: Video[]; shared_tags: string[]; category: string | null };
type Draft = { selected: Video[]; title: string; objective: string; language: Locale; languageChosen: boolean };
const draftKey = 'rw-ai.skill-draft';
const textChunk = 24000;
const busyJob = (job: SkillJob) => ['queued', 'reading', 'generating', 'packaging'].includes(job.status);
const stageNames: Record<string, string> = { queued: 'Aguardando', reading: 'Lendo as fontes', generating: 'Gerando instruções', packaging: 'Preparando o pacote', completed: 'Concluído', failed: 'Falhou', cancelled: 'Cancelado' };
const errorNames: Record<string, string> = {
  skill_model_missing: 'Instale um modelo de texto local no Ollama para gerar skills.', skill_model_unavailable: 'Não foi possível conectar ao Ollama neste computador.', skill_local_only: 'Selecione um modelo instalado neste computador. Modelos em nuvem não são usados.', skill_no_transcript: 'Um dos vídeos não tem transcrição disponível.', skill_invalid_sources: 'Selecione de 1 a 8 vídeos diferentes para gerar a skill.', skill_generation_failed: 'Não foi possível concluir a geração local. Verifique a IA local e tente novamente.', skill_output_incomplete: 'A resposta da IA local ficou incompleta. Tente um objetivo mais específico.', skill_timeout: 'A geração local excedeu o tempo disponível. Tente novamente com menos vídeos.', skill_invalid_output: 'Não foi possível gerar uma skill válida. Revise o objetivo ou tente outras fontes.', skill_package_too_large: 'As fontes ultrapassam o limite do pacote. Selecione menos vídeos.',
  invalid_selection: 'Selecione de 1 a 8 vídeos diferentes para gerar a skill.', invalid_input: 'Confira o nome, o objetivo e o idioma da skill.', source_unavailable: 'As fontes selecionadas não estão disponíveis. Ajuste a seleção e tente novamente.', queue_full: 'A fila de skills está cheia. Aguarde uma geração terminar.', not_found: 'Este item não foi encontrado.', invalid_state: 'Esta ação não está disponível no estado atual. Atualize e tente novamente.', invalid_package: 'Não foi possível gerar uma skill válida. Revise o objetivo ou tente outras fontes.', storage_error: 'Não foi possível acessar o armazenamento da skill.', generation_failed: 'Não foi possível concluir a geração local. Verifique a IA local e tente novamente.', ollama_unavailable: 'Não foi possível conectar ao Ollama neste computador.', model_not_found: 'O modelo local indicado não está disponível no Ollama.', model_unavailable: 'O modelo local indicado não está disponível no Ollama.', generation_timeout: 'A geração local excedeu o tempo disponível. Tente novamente com menos vídeos.',
};
function skillError(error: unknown) { return error instanceof ApiError && error.code ? errorNames[error.code] || error.message : error instanceof Error ? error.message : errorNames.generation_failed; }
function EmptyDraft(): Draft { return { selected: [], title: '', objective: '', language: getLocale(), languageChosen: false }; }
function loadDraft(): Draft {
  try {
    const value = JSON.parse(sessionStorage.getItem(draftKey) || 'null');
    if (!value || !Array.isArray(value.selected)) return EmptyDraft();
    return { selected: value.selected.filter((item: Video) => item && typeof item.id === 'string' && typeof item.title === 'string').slice(0, 8), title: typeof value.title === 'string' ? value.title.slice(0, 120) : '', objective: typeof value.objective === 'string' ? value.objective.slice(0, 2000) : '', language: ['pt', 'en', 'es'].includes(value.language) ? value.language : getLocale(), languageChosen: !!value.languageChosen };
  } catch { return EmptyDraft(); }
}
function SkillError({ message }: { message: string }) { return <div className="error-box" role="alert"><Icon name="alert" size={18}/><span>{t(message)}</span></div>; }

function PackagePreview({ job }: { job: SkillJob }) {
  const files = job.files?.length ? job.files : job.skill_markdown ? [{ path: 'SKILL.md', content: job.skill_markdown }] : [];
  const mainPath = files.find(file => /(^|\/)SKILL\.md$/.test(file.path))?.path || files[0]?.path || '';
  const [chosen, setChosen] = useState(mainPath);
  const [raw, setRaw] = useState(false);
  const [limit, setLimit] = useState(textChunk);
  const [targetAnchor, setTargetAnchor] = useState('');
  const preview = useRef<HTMLDivElement>(null);
  const file = files.find(item => item.path === chosen) || files[0];
  useEffect(() => { setLimit(textChunk); setRaw(false); }, [chosen]);
  useEffect(() => {
    if (!targetAnchor || !preview.current || !file) return;
    const element = preview.current.querySelector<HTMLElement>(`[data-package-anchor="${CSS.escape(targetAnchor)}"]`);
    if (element) { element.scrollIntoView({ behavior: 'smooth', block: 'center' }); setTargetAnchor(''); }
    else if (limit < file.content.length) setLimit(value => Math.min(file.content.length, value + textChunk));
    else setTargetAnchor('');
  }, [chosen, targetAnchor, limit, file]);
  function select(path: string, anchor = '') { setChosen(path); setTargetAnchor(anchor); setRaw(false); if (!anchor) preview.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  function packageLink(href: string) {
    try {
      const [pathname, hash = ''] = href.split('#');
      const parts = pathname ? (file?.path.split('/').slice(0, -1) || []) : [];
      for (const part of decodeURIComponent(pathname).split('/')) { if (part === '..') { if (!parts.length) return null; parts.pop(); } else if (part && part !== '.') parts.push(part); }
      const path = pathname ? parts.join('/') : file?.path;
      return files.some(item => item.path === path) ? { path: path!, anchor: decodeURIComponent(hash) } : null;
    } catch { return null; }
  }
  if (!file) return null;
  const content = file.content.slice(0, limit);
  // Preserve only explicit inert anchors for citation navigation; all other raw HTML stays disabled.
  const markdown = content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').replace(/<a\s+id=["']([A-Za-z0-9_-]+)["']\s*>\s*<\/a>/g, '\n[↳](#package-anchor-$1)\n');
  return <div className="skill-package">
    <div className="skill-file-list"><h4>{t('Arquivos do pacote')}</h4><ul>{files.map(item => <li key={item.path}><button className={file.path === item.path ? 'selected' : ''} aria-pressed={file.path === item.path} onClick={() => select(item.path)} data-package-file={item.path}><Icon name="file" size={15}/><span>{item.path}</span></button></li>)}</ul></div>
    <div className="skill-file-preview" ref={preview}><div className="skill-preview-toolbar"><strong>{file.path}</strong><div className="segmented"><button aria-pressed={!raw} className={!raw ? 'selected' : ''} onClick={() => setRaw(false)}>{t('Prévia')}</button><button aria-pressed={raw} className={raw ? 'selected' : ''} onClick={() => setRaw(true)}>{t('Texto do arquivo')}</button></div></div>
      {raw || !/\.md$/i.test(file.path) ? <pre className="skill-raw">{content}</pre> : <article className="markdown skill-markdown"><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{ h3: ({ children }) => <h3 data-package-anchor={typeof children === 'string' && /^segment-\d+$/.test(children) ? children : undefined}>{children}</h3>, img: ({ alt }) => alt ? <span className="muted">{t('[Imagem:')} {alt}]</span> : null, a: ({ href, children }) => { if (!href) return <span>{children}</span>; if (href.startsWith('#package-anchor-')) return <span className="skill-source-anchor" data-package-anchor={href.slice(16)} aria-hidden="true"/>; if (/^https?:\/\//i.test(href)) return <a href={href} target="_blank" rel="noopener noreferrer">{children}<Icon name="external" size={12}/></a>; const destination = packageLink(href); return destination ? <button className="skill-inline-link" onClick={() => select(destination.path, destination.anchor)}>{children}</button> : <span title={t('Link disponível no pacote ZIP.')}>{children}</span>; } }}>{markdown}</ReactMarkdown></article>}
      {file.content.length > limit && <div className="skill-more"><p>{t('Apenas parte do arquivo está visível. O pacote ZIP inclui o conteúdo completo.')}</p><button className="button secondary" onClick={() => setLimit(value => value + textChunk)}>{t('Mostrar mais conteúdo')}<Icon name="plus" size={15}/></button></div>}
    </div>
  </div>;
}

function SkillJobCard({ job, update, toast, autoExpand }: { job: SkillJob; update: () => void; toast: (message: string) => void; autoExpand: boolean }) {
  const [expanded, setExpanded] = useState(autoExpand);
  const [detail, setDetail] = useState<SkillJob | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState('');
  const [sources, setSources] = useState<Record<string, Video>>({});
  useEffect(() => { if (autoExpand) setExpanded(true); }, [autoExpand]);
  useEffect(() => {
    if (!expanded) return;
    let current = true; setLoading(true);
    api<SkillJob>(`/skills/${encodeURIComponent(job.id)}`).then(value => { if (current) { setDetail(value); setError(''); } }).catch(value => { if (current) setError(skillError(value)); }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [expanded, job.id, job.updated_at]);
  const sourceIds = job.video_ids.join(',');
  useEffect(() => {
    if (!expanded) return;
    let current = true;
    Promise.allSettled(job.video_ids.map(id => api<Video>(`/videos/${encodeURIComponent(id)}`))).then(results => { if (!current) return; setSources(Object.fromEntries(results.flatMap(result => result.status === 'fulfilled' ? [[result.value.id, result.value]] : []))); });
    return () => { current = false; };
  }, [expanded, sourceIds]);
  async function act(kind: 'cancel' | 'retry') {
    if (action) return; setAction(kind); setError('');
    try { const value = await api<SkillJob>(`/skills/${encodeURIComponent(job.id)}/${kind}`, { method: 'POST' }); setDetail(value); update(); toast(kind === 'cancel' ? 'Cancelamento da geração solicitado.' : 'Geração adicionada novamente à fila.'); }
    catch (value) { setError(skillError(value)); }
    finally { setAction(''); }
  }
  const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
  return <article className="skill-job" data-skill-id={job.id}>
    <div className="skill-job-heading"><div className="skill-job-symbol"><Icon name="spark" size={22}/></div><div><div className="skill-job-meta">{date(job.created_at)} · {language(job.language)} · {number(job.video_ids.length)} {t('vídeos')}</div><h3>{job.title || job.skill_name || t('Skill sem título')}</h3></div><span className={`job-status status-${job.status}`}>{busyJob(job) && <span className="spinner"/>}{t(stageNames[job.status] || 'Aguardando')}</span></div>
    {busyJob(job) && <><div className="job-progress" role="progressbar" aria-label={t('Progresso da geração')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress}><span style={{ width: `${progress}%` }}/></div><div className="skill-progress-meta"><span>{t(stageNames[job.stage] || stageNames[job.status])}</span><strong>{number(progress)}%</strong></div></>}
    {job.error && <SkillError message={errorNames[job.error_code || ''] || errorNames.generation_failed}/>}{error && <SkillError message={error}/>}
    <div className="skill-job-actions"><button className="text-link" data-skill-action="expand" aria-expanded={expanded} aria-controls={`skill-detail-${job.id}`} onClick={() => setExpanded(value => !value)}>{t(expanded ? 'Ocultar detalhes' : 'Ver detalhes')}<Icon name={expanded ? 'left' : 'right'} size={15}/></button><div>{busyJob(job) && <button className="button secondary" data-skill-action="cancel" disabled={!!action} onClick={() => act('cancel')}><Icon name="close" size={15}/>{t('Cancelar geração')}</button>}{['failed', 'cancelled'].includes(job.status) && <button className="button secondary" data-skill-action="retry" disabled={!!action} onClick={() => act('retry')}><Icon name="refresh" size={15} className={action ? 'rotating' : ''}/>{t('Gerar novamente')}</button>}{job.status === 'completed' && <a className="button primary" href={`/api/skills/${encodeURIComponent(job.id)}/download.zip`} download data-skill-action="download"><Icon name="download" size={16}/>{t('Baixar pacote ZIP')}</a>}</div></div>
    {expanded && <div className="skill-job-detail" id={`skill-detail-${job.id}`}>{loading && !detail ? <div className="loading-line"><span className="spinner"/>{t('Carregando detalhes…')}</div> : detail && <>{detail.objective && <p className="skill-objective">{detail.objective}</p>}<div className="skill-job-sources"><h4>{t('Fontes desta skill')}</h4><ul>{job.video_ids.map(id => <li key={id}>{sources[id] ? <a href={videoUrl(id)}><Icon name="book" size={15}/><span>{sources[id].title}</span><Icon name="arrow" size={14}/></a> : <span title={t('Não foi possível carregar esta fonte. Confira a cópia incluída no pacote.')}><Icon name="file" size={15}/>{id} · {t('Fonte incluída no pacote')}</span>}</li>)}</ul></div>{detail.status === 'completed' && <><div className="skill-validation"><Icon name="alert" size={18}/><p>{t('A validação confere a estrutura e as fontes. A skill não foi executada nem testada automaticamente.')}</p></div>{!!detail.warnings?.length && <details className="skill-notices"><summary>{t('Confira os avisos antes de usar a skill.')} ({number(detail.warnings.length)})</summary><ul>{detail.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul></details>}<PackagePreview key={detail.id} job={detail}/></>}</> }</div>}
  </article>;
}

export default function Skills({ categories, version, videoId, toast }: { categories: Category[]; version: number; videoId: string | null; toast: (message: string) => void }) {
  const [locale] = useLocale();
  const [draft, setDraft] = useState<Draft>(loadDraft);
  const [manual, setManual] = useState(false);
  const [q, setQ] = useState('');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<SkillStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [statusBusy, setStatusBusy] = useState(true);
  const [statusVersion, setStatusVersion] = useState(0);
  const [jobsVersion, setJobsVersion] = useState(0);
  const [jobsPage, setJobsPage] = useState(1);
  const [jobsTotal, setJobsTotal] = useState(0);
  const [jobs, setJobs] = useState<SkillJob[] | null>(null);
  const [jobsError, setJobsError] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [newJob, setNewJob] = useState('');
  const selection = useRef<HTMLHeadingElement>(null);
  const history = useRef<HTMLElement>(null);
  const { data: suggestions, error: suggestionsError, loading: suggestionsLoading } = useApi<{ items: Suggestion[]; total: number }>(`/skills/suggestions?language=${locale}`, version);
  const query = new URLSearchParams({ q: search, category, page: String(page), page_size: '12' });
  const { data: videos, error: videosError, loading: videosLoading } = useApi<Page>(`/videos?${query}`, version);
  useEffect(() => { const timer = setTimeout(() => { setSearch(q.trim()); setPage(1); }, 250); return () => clearTimeout(timer); }, [q]);
  useEffect(() => { try { sessionStorage.setItem(draftKey, JSON.stringify(draft)); } catch { /* The selection remains usable when browser storage is unavailable. */ } }, [draft]);
  useEffect(() => { if (!draft.languageChosen) setDraft(value => ({ ...value, language: locale })); }, [locale, draft.languageChosen]);
  useEffect(() => {
    if (!videoId) return;
    let current = true;
    api<Video>(`/videos/${encodeURIComponent(videoId)}`).then(video => { if (current) { if (video.available) { setDraft({ ...EmptyDraft(), selected: [video] }); setError(''); } else { setError('As fontes selecionadas não estão disponíveis. Ajuste a seleção e tente novamente.'); showSelection(); } window.history.replaceState(null, '', '#/skills'); } }).catch(value => { if (current) setError(skillError(value)); });
    return () => { current = false; };
  }, [videoId]);
  useEffect(() => {
    let current = true; let timer: ReturnType<typeof setTimeout>;
    async function check() {
      setStatusBusy(true);
      try { const value = await api<SkillStatus>('/skills/status'); if (current) { setStatus(value); setStatusError(''); } }
      catch (value) { if (current) { setStatus(null); setStatusError(skillError(value)); } }
      finally { if (current) { setStatusBusy(false); timer = setTimeout(check, 30000); } }
    }
    check(); return () => { current = false; clearTimeout(timer); };
  }, [statusVersion]);
  useEffect(() => {
    let current = true; let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      let delay = 12000;
      try { const value = await api<{ items: SkillJob[]; total: number }>(`/skills?page=${jobsPage}&page_size=10`); if (!current) return; setJobs(value.items); setJobsTotal(value.total); setJobsError(''); if (value.items.some(busyJob)) delay = 3000; }
      catch (value) { if (current) setJobsError(skillError(value)); }
      finally { if (current) timer = setTimeout(poll, delay); }
    }
    poll(); return () => { current = false; clearTimeout(timer); };
  }, [jobsVersion, jobsPage]);
  function updateDraft(values: Partial<Draft>) { setDraft(value => ({ ...value, ...values })); setError(''); }
  function toggle(video: Video) {
    if (!video.available && !draft.selected.some(item => item.id === video.id)) return;
    setDraft(value => ({ ...value, selected: value.selected.some(item => item.id === video.id) ? value.selected.filter(item => item.id !== video.id) : value.selected.length < 8 ? [...value.selected, video] : value.selected })); setError('');
  }
  function showSelection() { requestAnimationFrame(() => { selection.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); selection.current?.focus({ preventScroll: true }); }); }
  function chooseSuggestion(suggestion: Suggestion) { updateDraft({ selected: suggestion.videos.filter(video => video.available && suggestion.video_ids.includes(video.id)).slice(0, 8), title: draft.title || suggestion.title.slice(0, 120) }); toast('Combinação selecionada. Você pode ajustar os vídeos.'); showSelection(); }
  async function generate(event: FormEvent) {
    event.preventDefault(); if (submitting || pendingGeneration) return;
    if (!draft.selected.length) { setError('Selecione pelo menos um vídeo para gerar a skill.'); return; }
    if (draft.selected.some(video => !video.available)) { setError('As fontes selecionadas não estão disponíveis. Ajuste a seleção e tente novamente.'); return; }
    if (!status?.available) { setError('IA local indisponível'); return; }
    setSubmitting(true); setError('');
    try { const job = await api<SkillJob>('/skills', { method: 'POST', body: JSON.stringify({ video_ids: draft.selected.map(video => video.id), title: draft.title.trim(), objective: draft.objective.trim(), language: draft.language }) }); setJobs(value => [job, ...(value || []).filter(item => item.id !== job.id)]); setJobsPage(1); setNewJob(job.id); setJobsVersion(value => value + 1); toast('Geração de skill adicionada à fila.'); requestAnimationFrame(() => history.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })); }
    catch (value) { setError(skillError(value)); setStatusVersion(value => value + 1); }
    finally { setSubmitting(false); }
  }
  const pendingGeneration = jobs?.some(busyJob) || false;
  return <>
    <div className="page-eyebrow">{t('DO CONHECIMENTO À AÇÃO')}</div><div className="page-title-row"><div><h1>{t('Gerar skill')}</h1><p className="page-description skills-intro">{t('Escolha vídeos que se complementam. A IA local reúne o conhecimento em uma skill que você pode revisar e baixar.')}</p></div></div>
    <section className={`skill-local-status ${status?.available ? 'ready' : ''}`} aria-label={t('Modelo local')}><Icon name="spark" size={23}/><div><strong>{t(statusBusy && !status ? 'Verificando IA local…' : status?.available ? 'IA local pronta' : 'IA local indisponível')}</strong><p>{status?.available ? t('Nenhum conteúdo é enviado a serviços externos.') : t('Abra o Ollama neste computador e disponibilize o modelo indicado. Depois, verifique novamente.')}</p>{status?.model && <span className="skill-model">{t('Modelo local')}: <code>{status.model}</code></span>}{statusError && <p className="skill-status-error">{t(statusError)}</p>}{!status?.available && status?.error_code && errorNames[status.error_code] && <p>{t(errorNames[status.error_code])}</p>}</div><button className="button secondary" onClick={() => setStatusVersion(value => value + 1)} disabled={statusBusy} data-action="check-skill-status"><Icon name="refresh" size={15} className={statusBusy ? 'rotating' : ''}/>{t('Verificar IA local')}</button></section>
    <section className="skill-suggestions"><div className="section-heading"><div><h2>{t('Comece por uma combinação')}</h2><p>{t('Sugestões a partir dos assuntos e das fontes do seu acervo. Você pode ajustar cada seleção.')}</p></div><button className="button secondary" onClick={() => { setManual(true); showSelection(); }}><Icon name="plus" size={16}/>{t('Escolher manualmente')}</button></div>{suggestionsError && <SkillError message={suggestionsError}/>} {suggestionsLoading ? <div className="loading-line"><span className="spinner"/>{t('Carregando combinações…')}</div> : suggestions?.items.length ? <div className="skill-suggestion-grid">{suggestions.items.map(suggestion => <article className="skill-suggestion-card" key={suggestion.id} data-suggestion-id={suggestion.id}><div className="skill-suggestion-covers">{suggestion.videos.slice(0, 4).map(video => <img key={video.id} src={video.thumbnail_url} alt={video.title} loading="lazy"/>)}</div><div className="skill-suggestion-content"><span className="skill-topic">{suggestion.category || suggestion.shared_tags.slice(0, 2).join(' · ') || `${number(suggestion.videos.length)} ${t('vídeos')}`}</span><h3>{suggestion.title}</h3><p>{suggestion.reason || suggestion.description}</p><button className="button secondary" data-action="use-suggestion" onClick={() => chooseSuggestion(suggestion)}>{t('Usar combinação')}<Icon name="arrow" size={16}/></button></div></article>)}</div> : !suggestionsError && <div className="skill-empty-suggestions"><Icon name="book" size={25}/><div><h3>{t('Ainda não há combinações sugeridas')}</h3><p>{t('Selecione os vídeos manualmente ou adicione mais conteúdo ao acervo.')}</p></div></div>}</section>
    <form className="skill-builder" onSubmit={generate} noValidate><div className="skill-source-panel"><div className="section-heading"><div><h2 ref={selection} tabIndex={-1}>{t('Sua seleção')} <span className="skill-selected-count">{number(draft.selected.length)}/8</span></h2><p>{t('De 1 a 8 vídeos por skill.')}</p></div>{draft.selected.length > 0 && <button type="button" className="text-link" onClick={() => updateDraft({ selected: [] })}>{t('Limpar seleção')}</button>}</div>{draft.selected.length ? <ul className="skill-selected-list">{draft.selected.map(video => <li key={video.id}><img src={video.thumbnail_url} alt=""/><div><strong>{video.title}</strong><span>{video.channel} · {duration(video.duration)}</span></div><button type="button" className="icon-button" aria-label={t('Remover {title} da seleção', { title: video.title })} onClick={() => toggle(video)} data-remove-source={video.id}><Icon name="close" size={17}/></button></li>)}</ul> : <div className="skill-empty-selection"><Icon name="book" size={27}/><h3>{t('Nenhum vídeo selecionado')}</h3><p>{t('Use uma combinação acima ou escolha os vídeos do seu acervo.')}</p></div>}<button className="button secondary skill-manual-toggle" type="button" aria-expanded={manual} aria-controls="skill-video-picker" onClick={() => setManual(value => !value)}><Icon name={manual ? 'close' : 'plus'} size={16}/>{t(manual ? 'Ocultar seleção manual' : 'Adicionar ou trocar vídeos')}</button>
      {manual && <section id="skill-video-picker" className="skill-picker"><div className="skill-picker-filters"><div className="input-icon"><Icon name="search" size={17}/><input id="skill-search" value={q} onKeyDown={event => { if (event.key === 'Enter') event.preventDefault(); }} onChange={event => setQ(event.target.value)} placeholder={t('Buscar por título, canal ou conteúdo…')} aria-label={t('Buscar vídeos para a skill')}/></div><select id="skill-category" aria-label={t('Categoria')} value={category} onChange={event => { setCategory(event.target.value); setPage(1); }}><option value="">{t('Todas as categorias')}</option>{categories.map(item => <option value={item.id} key={item.id}>{item.name}</option>)}<option value="uncategorized">{t('Sem categoria')}</option></select></div>{draft.selected.length === 8 && <p className="skill-picker-note">{t('Você pode selecionar no máximo 8 vídeos.')}</p>}{videosError && <SkillError message={videosError}/>} {videosLoading ? <div className="loading-line"><span className="spinner"/>{t('Carregando vídeos')}</div> : videos?.items.length ? <><div className="skill-picker-grid">{videos.items.map(video => { const checked = draft.selected.some(item => item.id === video.id); return <label key={video.id} className={`skill-video-option ${checked ? 'selected' : ''}`}><input type="checkbox" checked={checked} disabled={!video.available || (!checked && draft.selected.length >= 8)} onChange={() => toggle(video)} aria-label={t('Selecionar {title}', { title: video.title })} data-select-source={video.id}/><img src={video.thumbnail_url} alt="" loading="lazy"/><span><strong>{video.title}</strong><small>{video.available ? video.channel : t('Fonte indisponível')}</small></span></label>; })}</div>{videos.total > 12 && <div className="pagination"><button type="button" className="button secondary" disabled={page === 1} onClick={() => setPage(value => value - 1)}>{t('Anterior')}</button><span>{t('Página')} {number(page)} {t('de')} {number(Math.ceil(videos.total / 12))}</span><button type="button" className="button secondary" disabled={page * 12 >= videos.total} onClick={() => setPage(value => value + 1)}>{t('Próxima')}</button></div>}</> : !videosError && <p className="skill-picker-note">{t('Nenhum vídeo encontrado')}</p>}</section>}
    </div><div className="skill-settings"><div className="skill-settings-heading"><Icon name="spark" size={21}/><h2>{t('Defina o resultado')}</h2></div><label htmlFor="skill-title">{t('Nome da skill')}</label><input id="skill-title" value={draft.title} onChange={event => updateDraft({ title: event.target.value })} maxLength={120} placeholder={t('Ex.: Planejar uma pesquisa de mercado')}/><p className="field-help">{t('Opcional; a IA pode sugerir um nome.')}</p><label htmlFor="skill-objective">{t('O que a skill deve ajudar a fazer?')}</label><textarea id="skill-objective" value={draft.objective} onChange={event => updateDraft({ objective: event.target.value })} maxLength={2000} rows={5} placeholder={t('Descreva a tarefa, o público e o resultado esperado.')}/><p className="field-help">{t('Um objetivo claro ajuda a transformar as fontes em instruções úteis.')}</p><label htmlFor="skill-language">{t('Idioma da skill')}</label><select id="skill-language" value={draft.language} onChange={event => updateDraft({ language: event.target.value as Locale, languageChosen: true })}><option value="pt">{t('Português')}</option><option value="en">{t('Inglês')}</option><option value="es">{t('Espanhol')}</option></select>{error && <SkillError message={error}/>}<button className="button primary skill-generate" type="submit" data-action="generate-skill" disabled={!draft.selected.length || draft.selected.some(video => !video.available) || !status?.available || submitting || pendingGeneration}><Icon name={submitting || pendingGeneration ? 'clock' : 'spark'} size={18}/>{t(submitting ? 'Gerando solicitação…' : pendingGeneration ? 'Gerando instruções' : 'Gerar skill')}</button><p className="skill-generation-note">{t('A geração acontece localmente e pode levar alguns minutos. Você pode continuar navegando.')}</p><div className="skill-synthesis-note"><Icon name="book" size={17}/><p>{t('A síntese usa trechos das transcrições, selecionados com apoio das análises existentes. As fontes completas acompanham o pacote.')}</p></div></div></form>
    <section className="skill-history" ref={history}><div className="section-heading"><div><h2>{t('Skills geradas')}</h2><p>{t('Acompanhe os processamentos e revise os pacotes antes de usar.')}</p></div><button className="icon-button" aria-label={t('Atualizar skills')} data-action="refresh-skills" onClick={() => setJobsVersion(value => value + 1)}><Icon name="refresh" size={19}/></button></div>{jobsError && <SkillError message={jobsError}/>} {jobs === null && !jobsError ? <div className="loading-line"><span className="spinner"/>{t('Carregando detalhes…')}</div> : jobs?.length ? <div className="skill-job-list">{jobs.map(job => <SkillJobCard key={job.id} job={job} update={() => setJobsVersion(value => value + 1)} toast={toast} autoExpand={newJob === job.id}/>)}</div> : !jobsError && <div className="skill-empty-history"><Icon name="file" size={27}/><h3>{t('Nenhuma skill por aqui ainda')}</h3><p>{t('Seu primeiro pacote aparecerá aqui depois de iniciar uma geração.')}</p></div>}{jobsTotal > 10 && <div className="pagination"><button type="button" className="button secondary" disabled={jobsPage <= 1} onClick={() => { setJobsPage(value => value - 1); setJobs(null); }}>{t('Anterior')}</button><span>{t('Página')} {number(jobsPage)} {t('de')} {number(Math.ceil(jobsTotal / 10))}</span><button type="button" className="button secondary" disabled={jobsPage * 10 >= jobsTotal} onClick={() => { setJobsPage(value => value + 1); setJobs(null); }}>{t('Próxima')}</button></div>}</section>
  </>;
}
