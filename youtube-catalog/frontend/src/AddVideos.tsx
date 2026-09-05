import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { api, date, language as languageName, number, videoUrl } from './api';
import { Icon } from './Icons';
import { intlLocale, jobError, t } from './i18n';

type JobStatus = 'queued' | 'discovering' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelled';
type Stage = 'queued' | 'discovering' | 'model' | 'downloading' | 'converting' | 'transcribing' | 'publishing' | 'completed' | 'cancelled' | 'failed';
type JobItem = { id: string; position: number; video_id: string | null; title: string | null; status: 'queued' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'; stage: Stage; error: string | null; error_code: string | null; updated_at: string };
type Job = { id: string; url: string; kind: 'video' | 'playlist'; language: 'auto' | 'pt' | 'en' | 'es'; title: string | null; status: JobStatus; stage: Stage; total: number; completed: number; failed: number; skipped: number; cancel_requested: boolean; created_at: string; updated_at: string; error: string | null; error_code: string | null };
type JobDetail = Job & { items: JobItem[]; logs: { id: string; time: string; stage: Stage; message: string }[] };
type JobsPage = { items: Job[]; total: number; page: number; page_size: number };
const active = (job: Job) => ['queued', 'discovering', 'running'].includes(job.status);
const states: Record<string, string> = { queued: 'Aguardando', discovering: 'Descobrindo vídeos', running: 'Processando', completed: 'Concluído', partial: 'Concluído com falhas', failed: 'Falhou', cancelled: 'Cancelado', skipped: 'Já no acervo' };
const stages: Record<string, string> = { queued: 'Aguardando', discovering: 'Descobrindo vídeos', model: 'Preparando modelo', downloading: 'Baixando áudio', converting: 'Preparando áudio', transcribing: 'Transcrevendo', publishing: 'Salvando no acervo', completed: 'Concluído', cancelled: 'Cancelado', failed: 'Falhou' };
function statusLabel(status: string) { return t(states[status] || 'Processando'); }
function stageLabel(stage: string) { return t(stages[stage] || 'Processando'); }
function JobError({ message }: { message: string }) { return <div className="error-box" role="alert"><Icon name="alert" size={18}/><span>{t(message)}</span></div>; }
function time(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.valueOf()) ? '' : parsed.toLocaleString(intlLocale(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); }
function validateUrl(value: string, kind: string) {
  if (!value.trim()) return 'Informe o endereço de um vídeo ou de uma playlist.';
  try {
    const parsed = new URL(value.trim());
    if (!['http:', 'https:'].includes(parsed.protocol) || !['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be'].includes(parsed.hostname.toLowerCase())) return 'Informe um endereço válido do YouTube.';
    const hasList = !!parsed.searchParams.get('list');
    const hasVideo = !!parsed.searchParams.get('v') || /\/(shorts|embed|live)\/[^/]+/.test(parsed.pathname) || (parsed.hostname.endsWith('youtu.be') && parsed.pathname.length > 1);
    if (kind === 'playlist' && !hasList) return 'Use um endereço de playlist que contenha a lista de vídeos.';
    if (kind === 'video' && !hasVideo) return 'Use o endereço de um vídeo específico.';
    if (!hasList && !hasVideo) return 'Informe o endereço de um vídeo ou de uma playlist.';
    return '';
  } catch { return 'Informe um endereço válido do YouTube.'; }
}
function JobCard({ job, update, toast }: { job: Job; update: () => void; toast: (message: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [details, setDetails] = useState<JobDetail | null>(null);
  const [detailError, setDetailError] = useState('');
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState('');
  const [actionError, setActionError] = useState('');
  useEffect(() => { if (!expanded) return; let current = true; setLoading(true); api<JobDetail>(`/jobs/${encodeURIComponent(job.id)}`).then(value => { if (current) { setDetails(value); setDetailError(''); } }).catch(error => { if (current) setDetailError(error.message); }).finally(() => { if (current) setLoading(false); }); return () => { current = false; }; }, [expanded, job.id, job.updated_at]);
  async function act(kind: 'cancel' | 'retry') {
    if (action) return;
    setAction(kind); setActionError('');
    try { const result = await api<JobDetail>(`/jobs/${encodeURIComponent(job.id)}/${kind}`, { method: 'POST' }); setDetails(result); update(); toast(kind === 'cancel' ? 'Cancelamento solicitado. Os vídeos concluídos continuam no acervo.' : 'Os vídeos pendentes foram adicionados novamente à fila.'); }
    catch (error) { setActionError((error as Error).message); }
    finally { setAction(''); }
  }
  const done = Math.min(job.total, job.completed + job.failed + job.skipped);
  const percent = job.total ? Math.round(done / job.total * 100) : 0;
  const title = job.title && !(job.total === 0 && ['Vídeo do YouTube', 'Playlist do YouTube'].includes(job.title)) ? job.title : t(job.kind === 'playlist' ? 'Playlist completa' : 'Vídeo');
  return <article className="job-card" data-job-id={job.id}>
    <div className="job-heading"><div className="job-kind"><Icon name={job.kind === 'playlist' ? 'grid' : 'channels'} size={21}/></div><div className="job-title"><div className="job-overline">{t(job.kind === 'playlist' ? 'Playlist completa' : 'Vídeo')} · {date(job.created_at)} · {job.language === 'auto' ? t('Detectar automaticamente') : languageName(job.language)}</div><h3>{title}</h3><p className="job-url">{job.url}</p></div><span className={`job-status status-${job.status}`}>{active(job) && <span className="spinner"/>}{statusLabel(job.status)}</span></div>
    <div className="job-progress" role="progressbar" aria-label={t('Progresso da transcrição')} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }}/></div>
    <div className="job-summary"><span className="job-stage">{job.cancel_requested ? t('Cancelamento solicitado') : stageLabel(job.stage)}</span><span>{number(job.completed)} {t('concluídos')} · {number(job.failed)} {t('com falha')} · {number(job.skipped)} {t('já no acervo')}<strong>{number(done)}/{number(job.total)}</strong></span></div>
    {job.error && <div className="job-error"><Icon name="alert" size={16}/>{jobError(job.error_code, job.error)}</div>}
    {actionError && <JobError message={actionError}/>}
    <div className="job-actions"><button className="text-link" data-job-action="expand" data-job-id={job.id} onClick={() => setExpanded(value => !value)} aria-expanded={expanded} aria-controls={`job-details-${job.id}`}>{t(expanded ? 'Ocultar detalhes' : 'Ver detalhes')}<Icon name={expanded ? 'left' : 'right'} size={15}/></button><div>{active(job) && <button className="button secondary" data-job-action="cancel" data-job-id={job.id} disabled={!!action || job.cancel_requested} onClick={() => act('cancel')}><Icon name="close" size={15}/>{t(job.cancel_requested ? 'Cancelamento solicitado' : 'Cancelar processamento')}</button>}{['partial', 'failed', 'cancelled'].includes(job.status) && <button className="button secondary" data-job-action="retry" data-job-id={job.id} disabled={!!action} onClick={() => act('retry')}><Icon name="refresh" size={15} className={action ? 'rotating' : ''}/>{t('Tentar pendentes novamente')}</button>}</div></div>
    {expanded && <div className="job-details" id={`job-details-${job.id}`}>
      {detailError && <JobError message={detailError}/>}
      {loading && !details ? <div className="loading-line"><span className="spinner"/>{t('Carregando detalhes…')}</div> : <><h4>{t('Vídeos deste processamento')}</h4>{details?.items.length ? <ol className="job-items">{details.items.map(item => <li key={item.id} data-job-item-id={item.id}><span className="job-item-number">{number(item.position)}</span><div><strong>{item.title && !/^Vídeo indisponível \(#\d+\)$/.test(item.title) ? item.title : t('Vídeo indisponível #{position}', { position: item.position })}</strong><div className="job-item-meta"><span className={`job-status status-${item.status}`}>{statusLabel(item.status)}</span>{item.status === 'running' && <span>{stageLabel(item.stage)}</span>}{(item.status === 'completed' || item.status === 'skipped') && item.video_id && <a href={videoUrl(item.video_id)} className="text-link">{t('Abrir transcrição')}<Icon name="arrow" size={14}/></a>}</div>{item.error && <p className="job-item-error">{jobError(item.error_code, item.error)}</p>}</div></li>)}</ol> : <p className="job-empty-detail">{t('Vídeos aparecerão após a identificação do conteúdo.')}</p>}{!!details?.logs.length && <div className="job-logs"><h4>{t('Histórico de etapas')}</h4><ol>{details.logs.map(log => <li key={log.id}><time dateTime={log.time}>{time(log.time)}</time><span>{stageLabel(log.stage)}</span></li>)}</ol></div>}</>}
    </div>}
  </article>;
}
export default function AddVideos({ refreshCatalog, toast }: { refreshCatalog: () => void; toast: (message: string) => void }) {
  const [url, setUrl] = useState('');
  const [kind, setKind] = useState<'auto' | 'video' | 'playlist'>('auto');
  const [language, setLanguage] = useState<'auto' | 'pt' | 'en' | 'es'>('auto');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [page, setPage] = useState(1);
  const [version, setVersion] = useState(0);
  const [data, setData] = useState<JobsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [queueError, setQueueError] = useState('');
  const seen = useRef(new Map<string, { status: JobStatus; completed: number }>());
  const refresh = useRef(refreshCatalog);
  refresh.current = refreshCatalog;
  const update = () => setVersion(value => value + 1);
  useEffect(() => {
    let mounted = true; let timer: ReturnType<typeof setTimeout>;
    async function fetchQueue() {
      let delay = 10000;
      try {
        const value = await api<JobsPage>(`/jobs?page=${page}&page_size=10`);
        if (!mounted) return;
        setData(value); setQueueError('');
        let catalogChanged = false;
        for (const job of value.items) { const old = seen.current.get(job.id); if (old && (job.completed > old.completed || (['queued', 'discovering', 'running'].includes(old.status) && !active(job)))) catalogChanged = true; seen.current.set(job.id, { status: job.status, completed: job.completed }); }
        if (catalogChanged) refresh.current();
        if (value.items.some(active)) delay = 2500;
      } catch (error) { if (mounted) setQueueError((error as Error).message); }
      finally { if (mounted) { setLoading(false); timer = setTimeout(fetchQueue, delay); } }
    }
    fetchQueue(); return () => { mounted = false; clearTimeout(timer); };
  }, [page, version]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (submitting) return;
    const error = validateUrl(url, kind); if (error) { setSubmitError(error); return; }
    setSubmitting(true); setSubmitError('');
    try { await api<JobDetail>('/jobs', { method: 'POST', body: JSON.stringify({ url: url.trim(), kind, language }) }); setUrl(''); setPage(1); update(); toast('Processamento adicionado à fila.'); }
    catch (error) { setSubmitError((error as Error).message); }
    finally { setSubmitting(false); }
  }
  return <><div className="page-eyebrow">{t('TRAGA SUAS PRÓXIMAS IDEIAS')}</div><div className="page-title-row"><div><h1>{t('Adicionar vídeos')}</h1><p className="page-description">{t('Do YouTube para sua biblioteca.')}</p></div><span className="count-pill"><Icon name="folder" size={15}/>{t('Armazenamento local no Docker')}</span></div>
    <div className="add-layout"><form className="add-video-form" onSubmit={submit} noValidate><h2>{t('Um vídeo ou uma playlist inteira')}</h2><p>{t('Adicione um vídeo ou uma playlist inteira. O aplicativo baixa o áudio e transcreve tudo localmente no Docker.')}</p><label htmlFor="source-url">{t('URL do vídeo ou da playlist')}</label><input id="source-url" name="url" type="url" value={url} onChange={event => { setUrl(event.target.value); setSubmitError(''); }} placeholder="https://www.youtube.com/watch?v=…" aria-describedby="source-url-help" disabled={submitting} autoComplete="off"/><span id="source-url-help" className="field-help">{t('Cole um endereço do YouTube')}</span><div className="add-field-grid"><div><label htmlFor="source-kind">{t('Tipo de conteúdo')}</label><select id="source-kind" name="kind" value={kind} onChange={event => setKind(event.target.value as typeof kind)} disabled={submitting}><option value="auto">{t('Identificar automaticamente')}</option><option value="video">{t('Vídeo')}</option><option value="playlist">{t('Playlist completa')}</option></select></div><div><label htmlFor="transcription-language">{t('Idioma da transcrição')}</label><select id="transcription-language" name="language" value={language} onChange={event => setLanguage(event.target.value as typeof language)} disabled={submitting}><option value="auto">{t('Detectar automaticamente')}</option><option value="pt">{t('Português')}</option><option value="en">{t('Inglês')}</option><option value="es">{t('Espanhol')}</option></select></div></div><p className="field-help">{t('O idioma escolhido orienta a transcrição; o conteúdo não é traduzido.')}</p>{submitError && <JobError message={submitError}/>}<button className="button primary" type="submit" disabled={submitting} data-action="submit-job"><Icon name={submitting ? 'refresh' : 'plus'} size={18} className={submitting ? 'rotating' : ''}/>{t(submitting ? 'Adicionando…' : 'Adicionar à fila')}</button></form>
    <aside className="add-explainer"><div className="add-explainer-icon"><Icon name="folder" size={25}/></div><h2>{t('Processamento local')}</h2><p>{t('Os vídeos de uma playlist são processados um de cada vez. Se um falhar, os outros continuam.')}</p><p>{t('A fila e os arquivos ficam salvos no Docker. Ao desligar o aplicativo, o trabalho pausa e retoma quando ele for ligado.')}</p><div className="model-note"><Icon name="clock" size={16}/><span>{t('O primeiro processamento pode demorar mais enquanto o modelo de transcrição é baixado.')}</span></div></aside></div>
    <section className="jobs-section"><div className="section-heading"><div><h2>{t('Fila de transcrições')}</h2><p>{t('Acompanhe os vídeos adicionados, os resultados e as tentativas.')}</p></div><button className="icon-button" aria-label={t('Atualizar fila')} onClick={update}><Icon name="refresh" size={18}/></button></div>{queueError && <JobError message={queueError}/>} {loading && !data ? <div className="loading-line"><span className="spinner"/>{t('Carregando a fila…')}</div> : data?.items.length ? <><div className="job-list">{data.items.map(job => <JobCard key={job.id} job={job} update={update} toast={toast}/>)}</div>{data.total > 10 && <div className="pagination"><button className="button secondary" disabled={page <= 1} onClick={() => { setPage(value => value - 1); setLoading(true); }}>{t('Anterior')}</button><span>{t('Página')} <strong>{number(page)}</strong> {t('de')} {number(Math.ceil(data.total / 10))}</span><button className="button secondary" disabled={page * 10 >= data.total} onClick={() => { setPage(value => value + 1); setLoading(true); }}>{t('Próxima')}</button></div>}</> : !queueError && <div className="empty"><div className="empty-icon"><Icon name="book" size={30}/></div><h2>{t('Nenhum processamento por aqui')}</h2><p>{t('Adicione um vídeo ou uma playlist no formulário acima.')}</p></div>}</section>
  </>;
}
