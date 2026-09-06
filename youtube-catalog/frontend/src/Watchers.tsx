import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api, number, videoUrl } from './api';
import { intlLocale, t } from './i18n';
import { Icon } from './Icons';
import './watchers.css';

type Settings = { name: string; language: string; interval_minutes: number; enabled: boolean };
type Source = Settings & { id: string; url: string; title: string; initial_mode: string; initialized: boolean; status: string; last_checked: string | null; last_success: string | null; next_check: string; last_error: string | null; last_new: number; last_unavailable: number; known: number; pending: number; queued: number; recent: { video_id: string; title: string; detected_at: string; state: string; processing_status: string | null }[] };
const intervals = [[15, 'A cada 15 minutos'], [30, 'A cada 30 minutos'], [60, 'A cada hora'], [120, 'A cada 2 horas'], [360, 'A cada 6 horas'], [720, 'A cada 12 horas'], [1440, 'Uma vez por dia']] as const;
function timestamp(value: string | null) { return value ? new Date(value).toLocaleString(intlLocale(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : t('Ainda não verificada'); }
function Language({ value, change, id }: { value: string; change: (value: string) => void; id: string }) { return <><label htmlFor={id}>{t('Idioma da transcrição')}</label><select id={id} value={value} onChange={e => change(e.target.value)}><option value="auto">{t('Detectar automaticamente')}</option><option value="pt">{t('Português')}</option><option value="en">{t('Inglês')}</option><option value="es">{t('Espanhol')}</option></select></>; }
function Frequency({ value, change, id }: { value: number; change: (value: number) => void; id: string }) { return <><label htmlFor={id}>{t('Verificar novidades')}</label><select id={id} value={value} onChange={e => change(Number(e.target.value))}>{!intervals.some(([minutes]) => minutes === value) && <option value={value}>{value} min</option>}{intervals.map(([minutes, label]) => <option key={minutes} value={minutes}>{t(label)}</option>)}</select></>; }
function SourceCard({ source, refresh, toast }: { source: Source; refresh: () => void; toast: (text: string) => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [editing, setEditing] = useState(false), [removing, setRemoving] = useState(false);
  const [settings, setSettings] = useState<Settings>({ name: source.name, language: source.language, interval_minutes: source.interval_minutes, enabled: source.enabled });
  async function act(action: 'check' | 'pause' | 'save' | 'remove') {
    if (busy) return;
    setBusy(true); setError('');
    try {
      if (action === 'remove') await api(`/watchers/${source.id}`, { method: 'DELETE' });
      else if (action === 'check') await api(`/watchers/${source.id}/check`, { method: 'POST' });
      else await api(`/watchers/${source.id}`, { method: 'PATCH', body: JSON.stringify(action === 'save' ? { ...settings, enabled: source.enabled } : { name: source.name, language: source.language, interval_minutes: source.interval_minutes, enabled: !source.enabled }) });
      setEditing(false); setRemoving(false); refresh(); toast(action === 'check' ? 'Verificação solicitada.' : action === 'remove' ? 'Acompanhamento removido.' : 'Acompanhamento atualizado.');
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const status = !source.enabled ? 'Pausado' : source.status === 'checking' ? 'Verificando…' : source.status === 'error' ? 'Precisa de atenção' : 'Acompanhando';
  const states: Record<string, string> = { pending: 'Aguardando envio', baseline: 'Já estava na playlist', existing: 'Já no acervo', queued: 'Na fila', running: 'Processando', failed: 'Falhou', cancelled: 'Cancelado', completed: 'Concluído', skipped: 'Já no acervo' };
  return <article className="watch-card" data-watch-id={source.id}>
    <div className="watch-heading"><div className="job-kind"><Icon name="channels" size={22}/></div><div><h3>{source.name || source.title || t('Playlist do YouTube')}</h3><a className="watch-url" href={source.url} target="_blank" rel="noopener noreferrer">{source.url}<Icon name="external" size={12}/></a></div><span className={`watch-state ${source.status === 'error' && source.enabled ? 'watch-error' : ''}`}>{source.enabled && source.status === 'checking' && <span className="spinner"/>}{t(status)}</span></div>
    <div className="watch-stats"><div><strong>{number(source.known)}</strong><span>{t('Vídeos identificados')}</span></div><div><strong>{number(source.queued)}</strong><span>{t('Enviados à fila')}</span></div><div><strong>{number(source.pending)}</strong><span>{t('Aguardando envio')}</span></div></div>
    <div className="watch-timing"><span>{t('Última verificação')}: <strong>{timestamp(source.last_checked)}</strong></span>{source.enabled && <span>{t('Próxima verificação')}: <strong>{timestamp(source.next_check)}</strong></span>}</div>
    {source.last_error && <div className="error-box" role="alert"><Icon name="alert" size={17}/><span>{t(source.last_error)}</span></div>}
    {!!source.last_unavailable && <p className="field-help">{t('{count} itens indisponíveis ou ao vivo serão verificados novamente.', { count: source.last_unavailable })}</p>}
    {!source.initialized && source.initial_mode === 'new' && <p className="field-help">{t('A primeira leitura bem-sucedida registra os vídeos atuais sem transcrevê-los.')}</p>}
    {error && <div className="error-box" role="alert">{t(error)}</div>}
    <div className="watch-actions"><button className="button secondary" data-watch-action="check" disabled={busy || !source.enabled || source.status === 'checking'} onClick={() => act('check')}><Icon name="refresh" size={15}/>{t('Verificar agora')}</button><button className="button secondary" data-watch-action="pause" disabled={busy} onClick={() => act('pause')}>{t(source.enabled ? 'Pausar acompanhamento' : 'Retomar acompanhamento')}</button><button className="text-link" data-watch-action="edit" disabled={busy} onClick={() => { setEditing(!editing); setSettings({ name: source.name, language: source.language, interval_minutes: source.interval_minutes, enabled: source.enabled }); }}>{t('Configurações')}</button><button className="icon-button" aria-label={t('Remover acompanhamento')} disabled={busy} onClick={() => setRemoving(!removing)}><Icon name="trash" size={17}/></button></div>
    {editing && <form className="watch-edit" onSubmit={e => { e.preventDefault(); act('save'); }}><div><label htmlFor={`watch-name-${source.id}`}>{t('Nome da fonte')}</label><input id={`watch-name-${source.id}`} maxLength={160} value={settings.name} onChange={e => setSettings({ ...settings, name: e.target.value })}/></div><div className="watch-fields"><div><Frequency id={`watch-interval-${source.id}`} value={settings.interval_minutes} change={interval_minutes => setSettings({ ...settings, interval_minutes })}/></div><div><Language id={`watch-language-${source.id}`} value={settings.language} change={language => setSettings({ ...settings, language })}/></div></div><button className="button primary" disabled={busy}>{t('Salvar configurações')}</button></form>}
    {removing && <div className="watch-remove"><p>{t('Remover este acompanhamento? As transcrições e tarefas já criadas continuam no aplicativo.')}</p><button className="button danger" disabled={busy} onClick={() => act('remove')}>{t('Remover acompanhamento')}</button><button className="button secondary" onClick={() => setRemoving(false)}>{t('Cancelar')}</button></div>}
    {!!source.recent.length && <details className="watch-recent"><summary>{t('Últimos vídeos identificados')}</summary><ul>{source.recent.map(item => <li key={item.video_id}><div>{['completed', 'skipped'].includes(item.processing_status || '') || item.state === 'existing' ? <a href={videoUrl(item.video_id)}>{item.title}</a> : <span>{item.title}</span>}<small>{timestamp(item.detected_at)}</small></div><span>{t(states[item.processing_status || item.state] || 'Na fila')}</span></li>)}</ul></details>}
    <p className="field-help">{t('Pausar interrompe novas verificações e envios. Transcrições já na fila continuam.')} <a href="#/add">{t('Ver fila de transcrições')}</a></p>
  </article>;
}

export default function Watchers({ toast }: { toast: (message: string) => void }) {
  const [sources, setSources] = useState<Source[]>([]), [loading, setLoading] = useState(true), [error, setError] = useState(''), [formError, setFormError] = useState(''), [busy, setBusy] = useState(false), [version, setVersion] = useState(0);
  const [url, setUrl] = useState(''), [name, setName] = useState(''), [language, setLanguage] = useState('auto'), [interval, setInterval] = useState(120), [mode, setMode] = useState('all');
  const refresh = () => setVersion(value => value + 1);
  useEffect(() => {
    let current = true, timer: ReturnType<typeof setTimeout>;
    async function load() { try { const result = await api<{ items: Source[] }>('/watchers'); if (current) { setSources(result.items); setError(''); } } catch (e) { if (current) setError((e as Error).message); } finally { if (current) { setLoading(false); timer = setTimeout(load, 5000); } } }
    load(); return () => { current = false; clearTimeout(timer); };
  }, [version]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return;
    setFormError('');
    try { const parsed = new URL(url.trim()); if (parsed.protocol !== 'https:' || !['youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','www.youtu.be'].includes(parsed.hostname) || !parsed.searchParams.get('list')) throw new Error(); }
    catch { setFormError('Informe o link completo de uma playlist do YouTube.'); return; }
    setBusy(true);
    try { await api('/watchers', { method: 'POST', body: JSON.stringify({ url: url.trim(), name, language, interval_minutes: interval, initial_mode: mode }) }); setUrl(''); setName(''); refresh(); toast('Playlist adicionada ao acompanhamento.'); }
    catch (e) { setFormError((e as Error).message); } finally { setBusy(false); }
  }
  return <><div className="page-eyebrow">{t('SUA BIBLIOTECA EM MOVIMENTO')}</div><div className="page-title-row"><div><h1>{t('Acompanhar playlists')}</h1><p className="page-description">{t('Adicione vídeos no YouTube. Eles chegam à sua fila automaticamente.')}</p></div><a className="button secondary" href="#/add"><Icon name="file" size={16}/>{t('Ver fila de transcrições')}</a></div>
    <div className="watch-intro"><form className="watch-form" onSubmit={submit}><h2>{t('Adicionar uma fonte')}</h2><label htmlFor="watch-url">{t('Link da playlist')}</label><input id="watch-url" type="url" required value={url} onChange={e => setUrl(e.target.value)} placeholder="https://www.youtube.com/playlist?list=…" autoComplete="off"/><label htmlFor="watch-name">{t('Nome da fonte')}</label><input id="watch-name" value={name} maxLength={160} onChange={e => setName(e.target.value)} placeholder={t('Opcional: um nome para organizar suas fontes')}/><div className="watch-fields"><div><Frequency id="watch-interval" value={interval} change={setInterval}/></div><div><Language id="watch-language" value={language} change={setLanguage}/></div></div><label htmlFor="watch-mode">{t('Primeira verificação')}</label><select id="watch-mode" value={mode} onChange={e => setMode(e.target.value)}><option value="all">{t('Importar os vídeos atuais e os próximos')}</option><option value="new">{t('Acompanhar apenas os próximos vídeos')}</option></select><p className="field-help">{t('Vídeos já conhecidos não são importados novamente. A ordem da playlist não importa.')}</p>{formError && <div className="error-box" role="alert">{t(formError)}</div>}<button className="button primary" data-action="create-watcher" disabled={busy}><Icon name={busy ? 'refresh' : 'plus'} size={17}/>{t(busy ? 'Adicionando…' : 'Começar acompanhamento')}</button></form>
    <aside className="watch-explainer"><Icon name="clock" size={30}/><h2>{t('Você escolhe. O aplicativo acompanha.')}</h2><p>{t('A consulta à playlist usa internet. O áudio é transcrito pelas ferramentas locais já instaladas.')}</p><p>{t('O acompanhamento funciona enquanto o aplicativo está ligado no Docker. Ao iniciar novamente, ele verifica as fontes atrasadas e retoma a fila.')}</p><p>{t('Falhas de consulta geram uma nova tentativa automática. Transcrições com falha podem ser retomadas na fila.')}</p><span className="local-badge"><span className="status-dot"/>{t('Sem depender deste chat')}</span></aside></div>
    <section className="watch-list"><div className="section-heading"><div><h2>{t('Suas fontes')}</h2><p>{t('Verifique os resultados ou ajuste quando cada playlist será consultada.')}</p></div><button className="icon-button" aria-label={t('Atualizar acompanhamentos')} onClick={refresh}><Icon name="refresh" size={18}/></button></div>{error && <div className="error-box" role="alert">{t(error)}</div>}{loading ? <div className="loading-line"><span className="spinner"/>{t('Carregando…')}</div> : sources.length ? sources.map(source => <SourceCard key={source.id} source={source} refresh={refresh} toast={toast}/>) : !error && <div className="empty"><div className="empty-icon"><Icon name="channels" size={26}/></div><h2>{t('Nenhuma playlist acompanhada')}</h2><p>{t('Cadastre uma playlist pública ou acessível pelo link para começar.')}</p></div>}</section>
  </>;
}
