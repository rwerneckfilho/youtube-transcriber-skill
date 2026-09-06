import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { t, useLocale, notice } from './i18n';
import type { Locale } from './i18n';
import AddVideos from './AddVideos';
import Skills from './Skills';
import Watchers from './Watchers';
import { Icon } from './Icons';
import type { IconName } from './Icons';
import { api, useApi, videoUrl, duration, number, language, date } from './api';
import type { Category, Channel, Detail, Page, Segment, SegmentPage, Stats, Video } from './api';
function Wordmark() { return <span className="wordmark" aria-label="rw-ai"><span className="brand-rw">rw</span><span className="brand-sep">/</span><span className="brand-ai">ai</span></span>; }
function navigate(path: string) { window.location.hash = path; }
function useRoute() {
    const [hash, setHash] = useState(window.location.hash.slice(1) || '/');
    useEffect(() => { const update = () => { setHash(window.location.hash.slice(1) || '/'); window.scrollTo({ top: 0, behavior: 'instant' }); }; window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
    const [path, query = ''] = hash.split('?');
    return { path, params: new URLSearchParams(query) };
}
function ErrorBox({ message, retry }: {
    message: string;
    retry?: () => void;
}) { return <div role="alert" className="error-box"><Icon name="alert"/><span>{t(message)}</span>{retry && <button onClick={retry}>{t("Tentar novamente")}</button>}</div>; }
function Empty({ title, text, icon = 'search', children }: {
    title: string;
    text: string;
    icon?: IconName;
    children?: ReactNode;
}) { return <div className="empty"><div className="empty-icon"><Icon name={icon} size={30}/></div><h2>{title}</h2><p>{text}</p>{children}</div>; }
function Skeleton({ count = 4 }: {
    count?: number;
}) { return <div className="video-grid" aria-label={t("Carregando v\u00EDdeos")} aria-busy="true">{Array.from({ length: count }, (_, i) => <div className="skeleton-card" key={i}><div /><span /><span /></div>)}</div>; }
function VideoCard({ video }: {
    video: Video;
}) {
    return <a className="video-card" href={videoUrl(video.id, video.match?.segment_index)}>
    <div className="card-image"><img src={video.thumbnail_url} alt="" loading="lazy"/><div className="card-hover"><span><Icon name="book" size={18}/>{t("Ler transcri\u00E7\u00E3o")}</span><Icon name="arrow"/></div>{!video.available && <span className="unavailable-badge">{t("Fonte indispon\u00EDvel")}</span>}<span className="duration">{duration(video.duration)}</span></div>
    <div className="card-channel">{video.channel}</div><h3>{video.title}</h3><div className="card-meta"><span>{video.categories[0]?.name || t("Sem categoria")}</span>{video.has_method && <span className="method-indicator" title={t("An\u00E1lise de m\u00E9todo dispon\u00EDvel")}><Icon name="file" size={13}/>{" " + t("M\u00E9todo")}</span>}</div>
    {video.match && <div className="match"><Icon name="search" size={13}/><span>{video.match.text}</span><small>{duration(video.match.start_ms / 1000)}</small></div>}
  </a>;
}
function Shelf({ title, subtitle, query, version, icon }: {
    title: string;
    subtitle?: string;
    query: string;
    version: number;
    icon?: IconName;
}) {
    const { data, loading, error } = useApi<Page>(`/videos?${query}&page_size=12`, version);
    const row = useRef<HTMLDivElement>(null);
    if (!loading && !error && !data?.items.length)
        return null;
    return <section className="shelf"><div className="section-heading"><div><h2>{icon && <Icon name={icon} size={19}/>} {title}</h2>{subtitle && <p>{subtitle}</p>}</div><div className="shelf-actions"><a href={`#/videos?${query}`} className="text-link">{t("Ver todos") + " "}<Icon name="right" size={15}/></a><div className="scroll-buttons"><button className="icon-button" aria-label={t('Rolar {title} para a esquerda', { title })} onClick={() => row.current?.scrollBy({ left: -600, behavior: 'smooth' })}><Icon name="left" size={16}/></button><button className="icon-button" aria-label={t('Rolar {title} para a direita', { title })} onClick={() => row.current?.scrollBy({ left: 600, behavior: 'smooth' })}><Icon name="right" size={16}/></button></div></div></div>{error ? <ErrorBox message={error}/> : loading && !data ? <Skeleton /> : <div className="shelf-row" ref={row}>{data?.items.map(v => <VideoCard key={v.id} video={v}/>)}</div>}</section>;
}
function Home({ stats, categories, channels, version }: {
    stats: Stats | null;
    categories: Category[];
    channels: Channel[];
    version: number;
}) {
    const { data, loading, error } = useApi<Page>('/videos?page_size=1&sort=added', version);
    const [group, setGroup] = useState<'category' | 'channel'>('category');
    const [channelLimit, setChannelLimit] = useState(8);
    const first = data?.items[0];
    return <>
    <div className="page-eyebrow">{t("SUA BIBLIOTECA DE CONHECIMENTO")}</div><div className="page-title-row"><div><h1>{t("Grandes ideias.") + " "}<span>{t("Sempre \u00E0 m\u00E3o.")}</span></h1><p className="page-description">{t("O que voc\u00EA assiste, organizado para ir al\u00E9m.")}</p></div><div className="collection-counter"><strong>{number(stats?.videos || 0)}</strong><span>{t("v\u00EDdeos no acervo")}</span></div></div>
    {error && <ErrorBox message={error}/>}
    {loading && !first ? <div className="hero skeleton-hero"/> : first ? <section className="hero"><img className="hero-image" src={first.thumbnail_url} alt=""/><div className="hero-shade"/><div className="hero-content"><span className="eyebrow"><span className="tiny-dot"/>{" " + t("REC\u00C9M-CHEGADO AO ACERVO")}</span><div className="hero-channel">{first.channel}</div><h2>{first.title}</h2><div className="hero-meta"><span><Icon name="clock" size={15}/>{duration(first.duration)}</span><span className="capitalize">{language(first.language)}</span>{first.has_method && <span><Icon name="file" size={15}/>{t("An\u00E1lise de m\u00E9todo")}</span>}</div><div className="hero-categories">{first.categories.slice(0, 2).map(c => <a key={c.id} href={`#/videos?category=${encodeURIComponent(c.id)}`} className="chip">{c.name}</a>)}</div><a className="button primary" href={videoUrl(first.id)}><Icon name="book" size={18}/>{" " + t("Explorar transcri\u00E7\u00E3o") + " "}<Icon name="arrow" size={18}/></a></div><div className="hero-footnote"><span className="tiny-dot"/>{" " + t("Salvo no seu acervo local")}</div></section> : <Empty icon="folder" title={stats?.scanning ? t("Preparando seu acervo") : t("Seu acervo come\u00E7a aqui")} text={stats?.scanning ? t("Estamos organizando os v\u00EDdeos. Eles aparecer\u00E3o aqui automaticamente.") : t("Adicione um v\u00EDdeo ou uma playlist para come\u00E7ar sua biblioteca.")}/>}
    <a className="skill-home-cta" href="#/skills"><span className="skill-home-icon"><Icon name="spark" size={26}/></span><span><strong>{t('Transforme vídeos em uma skill.')}</strong><span>{t('Combine ideias do seu acervo em instruções reutilizáveis para um assistente de IA.')}</span></span><span className="skill-home-action">{t('Gerar skill')}<Icon name="arrow" size={19}/></span></a>
    {first && <><Shelf title={t("Adicionados recentemente")} subtitle={t("Continue descobrindo o que chegou \u00E0 sua biblioteca.")} query="sort=added" version={version}/><div className="browse-heading"><div><div className="page-eyebrow">{t("ENCONTRE SUA PR\u00D3XIMA IDEIA")}</div><h2>{t("Explore o acervo")}</h2></div><div className="segmented" aria-label={t("Organiza\u00E7\u00E3o do acervo")}><button className={group === 'category' ? 'selected' : ''} onClick={() => setGroup('category')} aria-pressed={group === 'category'}><Icon name="tag" size={15}/>{t("Por categoria")}</button><button className={group === 'channel' ? 'selected' : ''} onClick={() => setGroup('channel')} aria-pressed={group === 'channel'}><Icon name="channels" size={15}/>{t("Por canal")}</button></div></div>{(group === 'category' ? categories : channels.slice(0, channelLimit)).filter(c => (c.count || 0) > 0).map(c => <Shelf key={`${group}-${c.id}`} title={c.name} query={`${group}=${encodeURIComponent(c.id)}`} version={version}/>)}{group === 'category' && <Shelf title={t("Sem categoria")} query="category=uncategorized" version={version}/>} {group === 'channel' && channelLimit < channels.length && <div className="center"><button className="button secondary" onClick={() => setChannelLimit(n => n + 8)}>{t("Explorar mais canais") + " "}<Icon name="plus" size={16}/></button></div>}</>}
  </>;
}
function AllVideos({ params, stats, categories, channels, version }: {
    params: URLSearchParams;
    stats: Stats | null;
    categories: Category[];
    channels: Channel[];
    version: number;
}) {
    const q = params.get('q') || '';
    const channel = params.get('channel') || '';
    const category = params.get('category') || '';
    const lang = params.get('language') || '';
    const sort = params.get('sort') || (q ? 'relevance' : 'added');
    const page = Math.max(1, Number(params.get('page')) || 1);
    const query = new URLSearchParams({ q, channel, category, language: lang, sort, page: String(page), page_size: '24' });
    const { data, loading, error } = useApi<Page>(`/videos?${query}`, version);
    const change = (key: string, value: string) => {
        const next = new URLSearchParams(params);
        if (value)
            next.set(key, value);
        else
            next.delete(key);
        if (key !== 'page')
            next.delete('page');
        navigate(`/videos?${next}`);
    };
    const chosenChannel = channels.find(c => c.id === channel)?.name;
    const chosenCategory = categories.find(c => c.id === category)?.name || (category === 'uncategorized' ? t("Sem categoria") : '');
    return <><div className="page-eyebrow">{t("SUA BIBLIOTECA")}</div><div className="page-title-row"><div><h1>{q ? t("Encontre aquela ideia.") : chosenChannel || chosenCategory || t("Todos os v\u00EDdeos")}</h1><p className="page-description">{q ? <>{t("Resultados para") + " "}<strong>“{q}”</strong></> : t("Um lugar para tudo o que vale revisitar.")}</p></div></div><div className="filters"><div className="filter-label"><Icon name="filter" size={17}/><span>{t("Filtrar")}</span></div><label><span className="sr-only">{t("Canal")}</span><select value={channel} onChange={e => change('channel', e.target.value)}><option value="">{t("Todos os canais")}</option>{channels.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label><label><span className="sr-only">{t("Categoria")}</span><select value={category} onChange={e => change('category', e.target.value)}><option value="">{t("Todas as categorias")}</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}<option value="uncategorized">{t("Sem categoria")}</option></select></label><label><span className="sr-only">{t("Idioma")}</span><select value={lang} onChange={e => change('language', e.target.value)}><option value="">{t("Todos os idiomas")}</option>{stats?.languages.map(l => <option key={l.id} value={l.id}>{language(l.id)}</option>)}</select></label>{(q || channel || category || lang) && <a className="clear-filters" href="#/videos"><Icon name="close" size={14}/>{t("Limpar")}</a>}</div><div className="results-heading"><span>{loading ? t("Buscando v\u00EDdeos\u2026") : <><strong>{number(data?.total || 0)}</strong> {(data?.total || 0) === 1 ? t("v\u00EDdeo encontrado") : t("v\u00EDdeos encontrados")}</>}</span><label>{t("Ordenar por") + " "}<select value={sort} onChange={e => change('sort', e.target.value)}>{q && <option value="relevance">{t("Relev\u00E2ncia")}</option>}<option value="added">{t("Inclus\u00E3o recente")}</option><option value="published">{t("Publica\u00E7\u00E3o recente")}</option><option value="title">{t("T\u00EDtulo de A a Z")}</option></select></label></div>{error ? <ErrorBox message={error}/> : loading ? <Skeleton count={8}/> : data?.items.length ? <><div className="video-grid">{data.items.map(v => <VideoCard key={v.id} video={v}/>)}</div><div className="pagination"><button className="button secondary" disabled={page <= 1} onClick={() => change('page', String(page - 1))}><Icon name="left" size={16}/>{t("Anterior")}</button><span>{t("P\u00E1gina") + " "}<strong>{page}</strong>{" " + t("de") + " "}{Math.max(1, Math.ceil(data.total / 24))}</span><button className="button secondary" disabled={page * 24 >= data.total} onClick={() => change('page', String(page + 1))}>{t("Pr\u00F3xima")}<Icon name="right" size={16}/></button></div></> : <Empty title={t("Nenhum v\u00EDdeo por aqui")} text={t("Experimente outros termos ou ajuste os filtros para continuar explorando.")}><a className="button secondary" href="#/videos">{t("Ver todo o acervo")}</a></Empty>}</>;
}
function Directory({ type, categories, channels, refresh, toast }: {
    type: 'categories' | 'channels';
    categories: Category[];
    channels: Channel[];
    refresh: () => void;
    toast: (s: string) => void;
}) {
    const [q, setQ] = useState('');
    const [newName, setNewName] = useState('');
    const [editing, setEditing] = useState('');
    const [editName, setEditName] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const items = (type === 'categories' ? categories : channels).filter(c => c.name.toLocaleLowerCase().includes(q.toLocaleLowerCase()));
    async function save(event: FormEvent, id?: string) {
        event.preventDefault();
        if (!(id ? editName : newName).trim()) { setError(t("Informe um nome para a categoria.")); return; }
        setBusy(true);
        setError('');
        try {
            await api(`/categories${id ? `/${encodeURIComponent(id)}` : ''}`, { method: id ? 'PATCH' : 'POST', body: JSON.stringify({ name: id ? editName.trim() : newName.trim() }) });
            setEditing('');
            setNewName('');
            refresh();
            toast(id ? t("Categoria renomeada.") : t("Categoria criada."));
        }
        catch (e) {
            setError((e as Error).message);
        }
        finally {
            setBusy(false);
        }
    }
    return <><div className="page-eyebrow">{t("EXPLORE SEU ACERVO")}</div><div className="page-title-row"><div><h1>{type === 'categories' ? t("Ideias em boa companhia.") : t("Vozes que voc\u00EA acompanha.")}</h1><p className="page-description">{type === 'categories' ? t("Organize seus v\u00EDdeos por assuntos que fazem sentido para voc\u00EA.") : t("Todos os canais que fazem parte da sua biblioteca.")}</p></div><span className="count-pill">{number((type === 'categories' ? categories : channels).length)} {type === 'categories' ? t("categorias") : t("canais")}</span></div><div className="directory-toolbar"><div className="input-icon"><Icon name="search" size={18}/><input aria-label={t(type === 'categories' ? 'Buscar categorias' : 'Buscar canais')} value={q} onChange={e => setQ(e.target.value)} placeholder={t(type === 'categories' ? 'Buscar categoria…' : 'Buscar canal…')}/></div>{type === 'categories' && <form className="create-category" noValidate onSubmit={e => save(e)}><input aria-label={t("Nome da nova categoria")} placeholder={t("Nome da nova categoria")} value={newName} onChange={e => setNewName(e.target.value)} maxLength={100} required/><button className="button primary" disabled={busy || !newName.trim()}><Icon name="plus" size={16}/>{t("Criar categoria")}</button></form>}</div>{error && <ErrorBox message={error}/>}<div className="directory-grid">{items.map((item, i) => <div className={`directory-card color-${i % 5}`} key={item.id}>{editing === item.id ? <form className="rename-form" noValidate onSubmit={e => save(e, item.id)}><label htmlFor={`rename-${item.id}`}>{t("Renomear categoria")}</label><input id={`rename-${item.id}`} value={editName} onChange={e => setEditName(e.target.value)} autoFocus maxLength={100} required/><div><button className="button primary" disabled={busy || !editName.trim()}>{t("Salvar")}</button><button className="button secondary" type="button" onClick={() => setEditing('')}>{t("Cancelar")}</button></div></form> : <><a href={`#/videos?${type === 'categories' ? 'category' : 'channel'}=${encodeURIComponent(item.id)}`}><div className="directory-icon">{type === 'channels' ? <span>{item.name.slice(0, 2).toLocaleUpperCase()}</span> : <Icon name="tag" size={25}/>}</div><h2>{item.name}</h2><div className="directory-card-bottom"><span>{number(item.count || 0)} {item.count === 1 ? t("v\u00EDdeo") : t("v\u00EDdeos")}</span><Icon name="arrow" size={18}/></div></a>{type === 'categories' && <button className="icon-button edit-category" aria-label={t('Renomear {name}', { name: item.name })} title={t("Renomear categoria")} onClick={() => { setEditing(item.id); setEditName(item.name); }}><Icon name="edit" size={15}/></button>}</>}</div>)}{type === 'categories' && !q && <a className="directory-card uncategorized" href="#/videos?category=uncategorized"><div className="directory-icon"><Icon name="folder" size={25}/></div><h2>{t("Sem categoria")}</h2><div className="directory-card-bottom"><span>{t("Prontos para organizar")}</span><Icon name="arrow" size={18}/></div></a>}</div>{!items.length && q && <Empty title={t("Nada encontrado")} text={t("Tente buscar por outro nome.")}/>}</>;
}
function CategoryEditor({ video, categories, onSave, onClose }: {
    video: Detail;
    categories: Category[];
    onSave: (d: Detail) => void;
    onClose: () => void;
}) {
    const [selected, setSelected] = useState(video.categories.map(c => c.id));
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const dialog = useRef<HTMLDialogElement>(null);
    useEffect(() => { const el = dialog.current; el?.showModal(); return () => el?.close(); }, []);
    async function save(reset = false) {
        setBusy(true);
        setError('');
        try {
            const d = await api<Detail>(`/videos/${encodeURIComponent(video.id)}/categories`, { method: reset ? 'DELETE' : 'PUT', ...(!reset ? { body: JSON.stringify({ category_ids: selected }) } : {}) });
            onSave(d);
            onClose();
        }
        catch (e) {
            setError((e as Error).message);
        }
        finally {
            setBusy(false);
        }
    }
    return <dialog ref={dialog} className="category-dialog" aria-labelledby="category-title" onCancel={e => {
            e.preventDefault();
            if (!busy)
                onClose();
        }} onClick={e => {
            if (e.target === e.currentTarget && !busy)
                onClose();
        }}><div className="dialog-header"><div><div className="page-eyebrow">{t("ORGANIZE SUAS IDEIAS")}</div><h2 id="category-title">{t("Categorias do v\u00EDdeo")}</h2></div><button className="icon-button" aria-label={t("Fechar")} onClick={onClose} disabled={busy}><Icon name="close"/></button></div><p>{t("Um v\u00EDdeo pode fazer parte de v\u00E1rios assuntos.")}</p><div className="category-options">{categories.map(c => <label key={c.id}><input type="checkbox" checked={selected.includes(c.id)} onChange={e => setSelected(s => e.target.checked ? [...s, c.id] : s.filter(id => id !== c.id))}/><span>{c.name}</span></label>)}</div>{!categories.length && <p>{t("Crie categorias na se\u00E7\u00E3o Categorias para organizar este v\u00EDdeo.")}</p>}{video.suggestions.length > 0 && <div className="suggestions"><h3>{t("Sugest\u00F5es do seu acervo")}</h3><p>{t("Assuntos parecidos com os de outros v\u00EDdeos da biblioteca.")}</p><div className="chip-row">{video.suggestions.map(c => <button key={c.id} className={`chip ${selected.includes(c.id) ? 'chip-selected' : ''}`} aria-pressed={selected.includes(c.id)} onClick={() => setSelected(s => s.includes(c.id) ? s.filter(id => id !== c.id) : [...s, c.id])}><Icon name={selected.includes(c.id) ? 'check' : 'plus'} size={14}/>{c.name}</button>)}</div></div>}{error && <ErrorBox message={error}/>}<div className="dialog-footer">{video.category_override && <button className="text-button" onClick={() => save(true)} disabled={busy}>{t("Restaurar categorias importadas")}</button>}<button className="button primary" onClick={() => save()} disabled={busy}>{busy ? t("Salvando\u2026") : t("Salvar categorias")}</button></div></dialog>;
}
function DeleteDialog({ video, onClose, onDeleted }: {
    video: Detail;
    onClose: () => void;
    onDeleted: () => void;
}) {
    const dialog = useRef<HTMLDialogElement>(null);
    const cancelButton = useRef<HTMLButtonElement>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    useEffect(() => {
        const trigger = document.activeElement as HTMLElement | null;
        const element = dialog.current;
        element?.showModal();
        cancelButton.current?.focus();
        return () => {
            element?.close();
            if (trigger?.isConnected)
                trigger.focus();
        };
    }, []);
    async function remove() {
        setBusy(true);
        setError('');
        try {
            await api(`/videos/${encodeURIComponent(video.id)}`, { method: 'DELETE' });
            onDeleted();
        }
        catch (e) {
            setError((e as Error).message);
            setBusy(false);
        }
    }
    return <dialog ref={dialog} className="category-dialog delete-dialog" aria-labelledby="delete-title" aria-describedby="delete-description" onCancel={e => {
            e.preventDefault();
            if (!busy)
                onClose();
        }}>
    <div className="dialog-header"><div><div className="delete-icon"><Icon name="trash" size={25}/></div><h2 id="delete-title">{t("Excluir do cat\u00E1logo?")}</h2></div><button className="icon-button" aria-label={t("Fechar")} onClick={onClose} disabled={busy}><Icon name="close"/></button></div>
    <p className="delete-video-title">{video.title}</p><p id="delete-description">{t("O v\u00EDdeo ser\u00E1 movido para a lixeira e deixar\u00E1 de aparecer no acervo.") + " "}<strong>{t("Os arquivos originais ser\u00E3o preservados.")}</strong>{" " + t("Voc\u00EA pode restaur\u00E1-lo pela Lixeira a qualquer momento.")}</p>
    {error && <ErrorBox message={error}/>}<div className="dialog-footer"><button ref={cancelButton} className="button secondary" onClick={onClose} disabled={busy} autoFocus>{t("Cancelar")}</button><button className="button danger" onClick={remove} disabled={busy}><Icon name="trash" size={16}/>{busy ? t("Excluindo\u2026") : t("Excluir do cat\u00E1logo")}</button></div>
  </dialog>;
}
function PermanentDeleteDialog({ video, onClose, onDeleted, onFailed }: {
    video: Video;
    onClose: () => void;
    onDeleted: () => void;
    onFailed: () => void;
}) {
    const dialog = useRef<HTMLDialogElement>(null);
    const cancelButton = useRef<HTMLButtonElement>(null);
    const [confirmation, setConfirmation] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const confirmationWord = t('EXCLUIR');
    const confirmed = confirmation === confirmationWord;
    useEffect(() => {
        const trigger = document.activeElement as HTMLElement | null;
        const element = dialog.current;
        element?.showModal();
        cancelButton.current?.focus();
        return () => {
            element?.close();
            if (trigger?.isConnected)
                trigger.focus();
            else
                document.querySelector<HTMLButtonElement>(`[data-permanent-video="${CSS.escape(video.id)}"]`)?.focus();
        };
    }, []);
    async function remove(event: FormEvent) {
        event.preventDefault();
        if (!confirmed || busy)
            return;
        setBusy(true);
        setError('');
        try {
            await api(`/videos/${encodeURIComponent(video.id)}/permanent`, { method: 'DELETE', body: JSON.stringify({ confirmation: video.id }) });
            onDeleted();
        }
        catch (e) {
            setError((e as Error).message);
            setBusy(false);
            onFailed();
        }
    }
    return <dialog ref={dialog} className="category-dialog delete-dialog permanent-delete-dialog" aria-labelledby="permanent-delete-title" aria-describedby="permanent-delete-description" onCancel={e => {
            e.preventDefault();
            if (!busy)
                onClose();
        }}>
    <div className="dialog-header"><div><div className="delete-icon"><Icon name="trash" size={25}/></div><h2 id="permanent-delete-title">{t("Excluir definitivamente?")}</h2></div><button className="icon-button" aria-label={t("Fechar")} onClick={onClose} disabled={busy}><Icon name="close"/></button></div>
    <p className="delete-video-title">{video.title}</p>
    <p id="permanent-delete-description">{t("A pasta original deste v\u00EDdeo ser\u00E1 apagada, incluindo transcri\u00E7\u00F5es") + " "}<strong>{t("TXT, SRT e JSON, METODO.md e arquivos de \u00E1udio ou m\u00EDdia")}</strong>{t(", quando existirem. O v\u00EDdeo tamb\u00E9m ser\u00E1 removido da lixeira.")}</p>
    <div className="permanent-delete-warning"><Icon name="alert" size={18}/><p>{t("Esta a\u00E7\u00E3o n\u00E3o pode ser desfeita. O v\u00EDdeo e seus arquivos n\u00E3o poder\u00E3o ser restaurados.")}</p></div>
    <form onSubmit={remove}>
      <label className="permanent-confirmation" htmlFor="permanent-confirmation">{t("Digite") + " "}<strong>{confirmationWord}</strong>{" " + t("para confirmar a exclus\u00E3o dos arquivos originais.")}<input id="permanent-confirmation" name="permanent-confirmation" value={confirmation} onChange={e => setConfirmation(e.target.value)} autoComplete="off" autoCapitalize="characters" spellCheck={false} disabled={busy} aria-describedby="permanent-confirmation-help"/></label>
      <p id="permanent-confirmation-help" className="permanent-confirmation-help">{t("Digite exatamente {word}, em letras maiúsculas.", { word: confirmationWord })}</p>
      {error && <ErrorBox message={error}/>}
      <div className="dialog-footer"><button ref={cancelButton} className="button secondary" type="button" onClick={onClose} disabled={busy} autoFocus>{t("Cancelar")}</button><button className="button danger" type="submit" disabled={!confirmed || busy}><Icon name="trash" size={16}/>{busy ? t("Excluindo arquivos\u2026") : t("Excluir definitivamente")}</button></div>
    </form>
  </dialog>;
}
function Trash({ params, version, refresh, toast }: {
    params: URLSearchParams;
    version: number;
    refresh: () => void;
    toast: (s: string) => void;
}) {
    const page = Math.max(1, Number(params.get('page')) || 1);
    const q = params.get('q') || '';
    const [search, setSearch] = useState(q);
    const [busy, setBusy] = useState('');
    const [mutationError, setMutationError] = useState('');
    const [permanentVideo, setPermanentVideo] = useState<Video | null>(null);
    const { data, loading, error } = useApi<Page>(`/videos?deleted=true&page_size=24&page=${page}&q=${encodeURIComponent(q)}`, version);
    useEffect(() => setSearch(q), [q]);
    const changePage = (next: number) => navigate(`/trash?${new URLSearchParams({ ...(q ? { q } : {}), page: String(next) })}`);
    async function restore(video: Video) {
        setBusy(video.id);
        setMutationError('');
        try {
            await api(`/videos/${encodeURIComponent(video.id)}/restore`, { method: 'POST' });
            if (data?.items.length === 1 && page > 1)
                changePage(page - 1);
            refresh();
            toast(t("V\u00EDdeo restaurado ao acervo com suas categorias."));
        }
        catch (e) {
            setMutationError((e as Error).message);
        }
        finally {
            setBusy('');
        }
    }
    function permanentlyDeleted() {
        setPermanentVideo(null);
        if (data?.items.length === 1 && page > 1)
            changePage(page - 1);
        refresh();
        toast(t("V\u00EDdeo e arquivos originais exclu\u00EDdos definitivamente."));
    }
    return <><div className="page-eyebrow">{t("ORGANIZE SEU ACERVO")}</div><div className="page-title-row"><div><h1>{t("Lixeira")}</h1><p className="page-description">{t("V\u00EDdeos exclu\u00EDdos do cat\u00E1logo, dispon\u00EDveis para restaurar.")}</p></div><div className="count-pill">{number(data?.total || 0)} {(data?.total || 0) === 1 ? t("v\u00EDdeo") : t("v\u00EDdeos")}</div></div>
    <div className="trash-explanation"><Icon name="folder" size={22}/><div><strong>{t("Excluir do cat\u00E1logo mant\u00E9m os arquivos originais.")}</strong><p>{t("Ao restaurar um v\u00EDdeo, ele volta ao cat\u00E1logo com suas categorias e transcri\u00E7\u00E3o. A exclus\u00E3o definitiva apaga a pasta original e todos os arquivos do v\u00EDdeo. Se esse processo ficar incompleto, alguns arquivos poder\u00E3o j\u00E1 ter sido removidos e a restaura\u00E7\u00E3o ficar\u00E1 indispon\u00EDvel.")}</p></div></div>
    <form className="trash-search" onSubmit={e => { e.preventDefault(); navigate(`/trash${search.trim() ? `?q=${encodeURIComponent(search.trim())}` : ''}`); }} role="search"><div className="input-icon"><Icon name="search" size={18}/><input value={search} onChange={e => setSearch(e.target.value)} placeholder={t("Buscar na lixeira\u2026")} aria-label={t("Buscar na lixeira")}/>{search && <button className="icon-button" aria-label={t("Limpar busca da lixeira")} type="button" onClick={() => { setSearch(''); navigate('/trash'); }}><Icon name="close" size={15}/></button>}</div><button className="button secondary">{t("Buscar")}</button></form>
    {mutationError && <ErrorBox message={mutationError}/>} {error ? <ErrorBox message={error}/> : loading ? <Skeleton count={4}/> : data?.items.length ? <><div className="video-grid trash-grid">{data.items.map(video => <article className="video-card trash-card" key={video.id}><div className="card-image"><img src={video.thumbnail_url} alt="" loading="lazy"/><span className="duration">{duration(video.duration)}</span></div><div className="card-channel">{video.channel}</div><h2>{video.title}</h2><div className="trash-date"><Icon name="clock" size={13}/><span>{t("Exclu\u00EDdo em") + " "}{date(video.deleted_at || '') || t("data n\u00E3o informada")}</span></div><>{video.purge_pending && <div className="purge-notice" role="status"><Icon name="alert" size={16}/><div><strong>{t("Exclus\u00E3o incompleta. Confirme novamente para concluir.")}</strong>{video.purge_error && <p className="purge-error">{notice(video.purge_error)}</p>}<p>{t("A restaura\u00E7\u00E3o fica indispon\u00EDvel ap\u00F3s o in\u00EDcio da exclus\u00E3o definitiva.")}</p></div></div>}</><button className="button secondary restore-button" disabled={!!busy || video.purge_pending} title={video.purge_pending ? t("Restaura\u00E7\u00E3o indispon\u00EDvel: a exclus\u00E3o definitiva j\u00E1 foi iniciada.") : undefined} onClick={() => restore(video)} aria-label={t('Restaurar vídeo: {title}', { title: video.title })}><Icon name="restore" size={16}/>{busy === video.id ? t("Restaurando\u2026") : t("Restaurar v\u00EDdeo")}</button><button className="button permanent-delete-trigger" data-permanent-video={video.id} disabled={!!busy} onClick={() => setPermanentVideo(video)} aria-label={t('Excluir definitivamente: {title}', { title: video.title })}><Icon name="trash" size={15}/>{t("Excluir definitivamente")}</button></article>)}</div><div className="pagination"><button className="button secondary" disabled={page <= 1} onClick={() => changePage(page - 1)}><Icon name="left" size={16}/>{t("Anterior")}</button><span>{t("P\u00E1gina") + " "}<strong>{page}</strong>{" " + t("de") + " "}{Math.max(1, Math.ceil(data.total / 24))}</span><button className="button secondary" disabled={page * 24 >= data.total} onClick={() => changePage(page + 1)}>{t("Pr\u00F3xima")}<Icon name="right" size={16}/></button></div></> : <Empty icon="trash" title={q ? t("Nenhum v\u00EDdeo encontrado") : t("Sua lixeira est\u00E1 vazia")} text={q ? t("Experimente outro termo para encontrar o v\u00EDdeo que quer restaurar.") : t("Os v\u00EDdeos exclu\u00EDdos do cat\u00E1logo aparecer\u00E3o aqui. Voc\u00EA pode restaur\u00E1-los quando quiser.")}><a className="button secondary" href={q ? '#/trash' : '#/videos'}>{q ? t("Ver toda a lixeira") : t("Voltar ao acervo")}</a></Empty>}
    {permanentVideo && <PermanentDeleteDialog video={permanentVideo} onClose={() => setPermanentVideo(null)} onDeleted={permanentlyDeleted} onFailed={refresh}/>}
  </>;
}
function Reader({ video, initialSegment }: {
    video: Detail;
    initialSegment: number | null;
}) {
    const [q, setQ] = useState('');
    const [search, setSearch] = useState('');
    const initialOffset = Math.floor(Math.max(0, initialSegment || 0) / 100) * 100;
    const [segments, setSegments] = useState<Segment[]>([]);
    const [start, setStart] = useState(initialOffset);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [highlight, setHighlight] = useState(initialSegment);
    const [retry, setRetry] = useState(0);
    const scrolled = useRef(false);
    const request = async (offset: number) => api<SegmentPage>(`/videos/${encodeURIComponent(video.id)}/segments?offset=${offset}&limit=100&q=${encodeURIComponent(search)}`);
    useEffect(() => { const timer = setTimeout(() => setSearch(q.trim()), 300); return () => clearTimeout(timer); }, [q]);
    useEffect(() => {
        let active = true;
        const offset = search ? 0 : initialOffset;
        setLoading(true);
        setError('');
        setSegments([]);
        setStart(offset);
        request(offset).then(d => {
            if (active) {
                setSegments(d.items);
                setTotal(d.total);
                setStart(d.offset);
            }
        }).catch(e => {
            if (active)
                setError(e.message);
        }).finally(() => {
            if (active)
                setLoading(false);
        });
        return () => { active = false; };
    }, [video.id, search, initialOffset, retry]);
    useEffect(() => {
        if (!loading && highlight != null && !scrolled.current && segments.some(s => s.index === highlight)) {
            document.getElementById(`segment-${highlight}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            scrolled.current = true;
        }
    }, [loading, segments, highlight]);
    async function more(previous = false) {
        if (loading)
            return;
        setLoading(true);
        setError('');
        const offset = previous ? Math.max(0, start - 100) : start + segments.length;
        try {
            const d = await request(offset);
            setSegments(old => previous ? [...d.items, ...old] : [...old, ...d.items]);
            if (previous)
                setStart(offset);
            setTotal(d.total);
        }
        catch (e) {
            setError((e as Error).message);
        }
        finally {
            setLoading(false);
        }
    }
    function showContext(segment: Segment) { setHighlight(segment.index); scrolled.current = false; navigate(`/video/${encodeURIComponent(video.id)}?segment=${segment.index}`); setQ(''); setSearch(''); }
    return <section className="reader" aria-label={t("Leitor de transcri\u00E7\u00E3o")}><div className="reader-toolbar"><div className="input-icon"><Icon name="search" size={17}/><input value={q} onChange={e => setQ(e.target.value)} placeholder={t("Buscar nesta transcri\u00E7\u00E3o\u2026")} aria-label={t("Buscar nesta transcri\u00E7\u00E3o")}/>{q && <button className="icon-button" onClick={() => setQ('')} aria-label={t("Limpar busca")}><Icon name="close" size={14}/></button>}</div><span>{number(total)} {search ? t("trechos encontrados") : t("trechos")}{video.has_speakers && <span className="speaker-mode">{" " + t("\u2022 Com falantes")}</span>}</span></div><div className="reader-note"><Icon name="book" size={15}/><span>{search ? t("Selecione \u201CVer contexto\u201D para ler o trecho na transcri\u00E7\u00E3o completa.") : t("A transcri\u00E7\u00E3o \u00E9 carregada por partes. Os hor\u00E1rios abrem o trecho no YouTube.")}</span></div>{error && <ErrorBox message={error} retry={() => setRetry(n => n + 1)}/>} {start > 0 && !search && <button className="load-earlier button secondary" disabled={loading} onClick={() => more(true)}>{t("Carregar trechos anteriores")}</button>}<div className="segments" aria-busy={loading}>{segments.map(segment => <article key={segment.index} className={`segment ${segment.index === highlight ? 'highlighted' : ''}`} id={`segment-${segment.index}`}><a className="timestamp" href={`https://www.youtube.com/watch?v=${encodeURIComponent(video.id)}&t=${Math.floor(segment.start_ms / 1000)}s`} target="_blank" rel="noopener noreferrer" aria-label={t('Abrir vídeo no YouTube em {time}', { time: duration(segment.start_ms / 1000) })}>{duration(segment.start_ms / 1000)}</a><div>{segment.speaker && <span className="speaker">{segment.speaker}</span>}<p>{segment.text}</p>{search && <button className="context-button" onClick={() => showContext(segment)}>{t("Ver contexto") + " "}<Icon name="arrow" size={13}/></button>}</div></article>)}</div>{loading && <div className="loading-line"><span className="spinner"/>{t("Carregando transcri\u00E7\u00E3o\u2026")}</div>}{!loading && !segments.length && !error && <Empty title={search ? t("Nenhum trecho encontrado") : t("Transcri\u00E7\u00E3o ainda n\u00E3o dispon\u00EDvel")} text={search ? t("Experimente uma palavra ou express\u00E3o diferente.") : t("Os trechos aparecer\u00E3o ap\u00F3s a pr\u00F3xima atualiza\u00E7\u00E3o do cat\u00E1logo.")}/>} {start + segments.length < total && segments.length > 0 && <div className="load-more"><span>{t("Exibindo") + " "}{number(start + 1)}–{number(start + segments.length)}{" " + t("de") + " "}{number(total)}{" " + t("trechos")}</span><button className="button secondary" onClick={() => more()} disabled={loading}>{t("Carregar pr\u00F3ximos 100 trechos") + " "}<Icon name="plus" size={16}/></button></div>}{!loading && segments.length > 0 && start + segments.length >= total && <div className="reader-end"><Icon name="check" size={16}/> {search ? t("Todos os resultados carregados") : t("Voc\u00EA chegou ao fim da transcri\u00E7\u00E3o")}</div>}</section>;
}
function VideoDetail({ id, params, categories, version, refresh, toast }: {
    id: string;
    params: URLSearchParams;
    categories: Category[];
    version: number;
    refresh: () => void;
    toast: (s: string) => void;
}) {
    const { data: video, error, loading, setData } = useApi<Detail>(`/videos/${encodeURIComponent(id)}`, version);
    const [tab, setTab] = useState<'transcript' | 'method'>('transcript');
    const [edit, setEdit] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const rawSegment = params.get('segment');
    const segment = rawSegment !== null && Number.isFinite(Number(rawSegment)) ? Math.max(0, Math.floor(Number(rawSegment))) : null;
    if (error)
        return <><a className="back-link" href="#/videos"><Icon name="left" size={16}/>{t("Voltar ao acervo")}</a><ErrorBox message={error}/></>;
    if (loading && !video)
        return <div className="loading-line"><span className="spinner"/>{t("Abrindo v\u00EDdeo\u2026")}</div>;
    if (!video)
        return null;
    function safeLink(href: string | undefined) {
        if (!href)
            return undefined;
        const filename = href.split('/').pop()?.split('#')[0];
        const file = video?.downloads.find(d => d.filename === filename);
        if (file)
            return file.url;
        return /^(https?:\/\/|#)/.test(href) ? href : undefined;
    }
    return <><a className="back-link" href="#/videos"><Icon name="left" size={16}/>{t("Voltar ao acervo")}</a><div className="detail-header"><div className="detail-art"><img src={video.thumbnail_url} alt=""/><span className="duration">{duration(video.duration)}</span></div><div className="detail-info"><a className="detail-channel" href={`#/videos?channel=${encodeURIComponent(video.channel_id)}`}><Icon name="channels" size={16}/>{video.channel}<Icon name="right" size={14}/></a><h1>{video.title}</h1><div className="detail-meta"><span><Icon name="clock" size={15}/>{duration(video.duration)}</span><span className="capitalize">{language(video.language)}</span>{video.published_at && <span>{date(video.published_at)}</span>}</div><div className="chip-row">{video.categories.length ? video.categories.map(c => <a className="chip" href={`#/videos?category=${encodeURIComponent(c.id)}`} key={c.id}>{c.name}</a>) : <span className="chip muted">{t("Sem categoria")}</span>}<button className="chip edit-chip" onClick={() => setEdit(true)}><Icon name="edit" size={12}/>{t("Editar")}</button></div><div className="detail-actions"><a className="button primary" href={`#/skills?video=${encodeURIComponent(video.id)}`} data-action="skill-from-video"><Icon name="spark" size={16}/>{t('Gerar skill')}</a><a className="button secondary external-video" href={`https://www.youtube.com/watch?v=${encodeURIComponent(video.id)}`} target="_blank" rel="noopener noreferrer"><Icon name="external" size={15}/>{t("Abrir no YouTube")}</a><button className="button delete-trigger" onClick={() => setDeleting(true)}><Icon name="trash" size={15}/>{t("Excluir do cat\u00E1logo")}</button></div></div></div>{!video.available && <div className="notice"><Icon name="alert" size={18}/>{t("A pasta original est\u00E1 indispon\u00EDvel. A \u00FAltima transcri\u00E7\u00E3o importada continua dispon\u00EDvel aqui; alguns downloads podem n\u00E3o funcionar.")}</div>}{video.description && <details className="description"><summary>{t("Sobre este v\u00EDdeo") + " "}<Icon name="right" size={14}/></summary><p>{video.description}</p></details>}
    <div className="detail-layout"><div className="detail-main"><div className="tabs" role="tablist" aria-label={t("Conte\u00FAdo do v\u00EDdeo")}><button id="transcript-tab" role="tab" aria-selected={tab === 'transcript'} aria-controls="video-panel" className={tab === 'transcript' ? 'active' : ''} onClick={() => setTab('transcript')}><Icon name="book" size={18}/>{t("Transcri\u00E7\u00E3o")}</button>{video.has_method && <button id="method-tab" role="tab" aria-selected={tab === 'method'} aria-controls="video-panel" className={tab === 'method' ? 'active' : ''} onClick={() => setTab('method')}><Icon name="file" size={18}/>{t("M\u00E9todo")}</button>}</div><div role="tabpanel" id="video-panel" aria-labelledby={tab === 'transcript' ? 'transcript-tab' : 'method-tab'}>{tab === 'transcript' ? <Reader key={`${id}-${segment ?? ''}`} video={video} initialSegment={segment}/> : <article className="markdown"><div className="method-heading"><div className="page-eyebrow">{t("DO CONTE\u00DADO \u00C0 PR\u00C1TICA")}</div><h2>{t("An\u00E1lise de m\u00E9todo")}</h2></div><ReactMarkdown skipHtml remarkPlugins={[remarkGfm]} components={{ img: ({ alt }) => alt ? <span className="muted">{t("[Imagem:") + " "}{alt}]</span> : null, a: ({ href, children }) => { const safe = safeLink(href); return safe ? <a href={safe} {...(safe.startsWith('http') ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>{children}</a> : <span>{children}</span>; } }}>{video.method || t("Ainda n\u00E3o h\u00E1 an\u00E1lise de m\u00E9todo para este v\u00EDdeo.")}</ReactMarkdown></article>}</div></div><aside className="detail-aside"><section className="aside-box"><div className="aside-title"><Icon name="download" size={17}/><h2>{t("Seus arquivos")}</h2></div><p>{t("Leve o conte\u00FAdo no formato que preferir.")}</p><div className="download-list">{video.downloads.map(d => <a href={d.url} key={d.filename} download><div className="file-type">{d.filename.split('.').pop()?.toUpperCase()}</div><span>{t(d.label)}</span><Icon name="download" size={14}/></a>)}</div>{!video.downloads.length && <p>{t("Nenhum arquivo dispon\u00EDvel.")}</p>}</section>{video.suggestions.length > 0 && <section className="aside-box"><div className="aside-title"><Icon name="tag" size={17}/><h2>{t("Conecte suas ideias")}</h2></div><p>{t("Este v\u00EDdeo pode combinar com:")}</p><div className="chip-row">{video.suggestions.map(c => <button className="chip" key={c.id} onClick={() => setEdit(true)}><Icon name="plus" size={12}/>{c.name}</button>)}</div><button className="text-link suggestion-link" onClick={() => setEdit(true)}>{t("Escolher categorias") + " "}<Icon name="arrow" size={14}/></button></section>}{video.tags.length > 0 && <section className="aside-box tags-box"><h2>{t("Assuntos do v\u00EDdeo")}</h2><div>{video.tags.map(t => <a key={t} href={`#/videos?q=${encodeURIComponent(t)}`}>#{t}</a>)}</div></section>}<div className="local-note"><span className="status-dot"/><span>{t("Leitura local")}<br /><small>{t("Dispon\u00EDvel mesmo sem internet.")}</small></span></div></aside></div>{edit && <CategoryEditor video={video} categories={categories} onClose={() => setEdit(false)} onSave={d => { setData(d); refresh(); toast(t("Categorias atualizadas.")); }}/>} {deleting && <DeleteDialog video={video} onClose={() => setDeleting(false)} onDeleted={() => { navigate('/videos'); refresh(); toast(t("V\u00EDdeo movido para a lixeira. Os arquivos originais foram preservados.")); }}/>}</>;
}
export default function App() {
    const [locale, changeLocale] = useLocale();
    const { path, params } = useRoute();
    const [version, setVersion] = useState(0);
    const [stats, setStats] = useState<Stats | null>(null);
    const [connectionError, setConnectionError] = useState('');
    const [search, setSearch] = useState(params.get('q') || '');
    const [toastMessage, setToastMessage] = useState('');
    const [scanBusy, setScanBusy] = useState(false);
    const lastScan = useRef<string | null>(null);
    const { data: categories, error: categoriesError } = useApi<Category[]>('/categories', version);
    const { data: channels, error: channelsError } = useApi<Channel[]>('/channels', version);
    const refresh = () => { setVersion(n => n + 1); api<Stats>('/stats').then(setStats).catch(() => { }); };
    useEffect(() => {
        let active = true;
        async function update() {
            try {
                const s = await api<Stats>('/stats');
                if (!active)
                    return;
                setStats(s);
                setConnectionError('');
                if (lastScan.current !== s.last_scan) {
                    lastScan.current = s.last_scan;
                    refresh();
                }
            }
            catch {
                if (active)
                    setConnectionError(t("O cat\u00E1logo est\u00E1 desconectado. Verifique se o aplicativo est\u00E1 ligado no Docker."));
            }
        }
        update();
        const timer = setInterval(update, 4000);
        return () => { active = false; clearInterval(timer); };
    }, []);
    useEffect(() => { setSearch(params.get('q') || ''); }, [path, params.get('q')]);
    useEffect(() => {
        if (!toastMessage)
            return;
        const timer = setTimeout(() => setToastMessage(''), 4500);
        return () => clearTimeout(timer);
    }, [toastMessage]);
    useEffect(() => { document.title = `${path.startsWith('/video/') ? t("Transcri\u00E7\u00E3o") : path === '/channels' ? t("Canais") : path === '/categories' ? t("Categorias") : path === '/videos' ? t("Todos os v\u00EDdeos") : path === '/trash' ? t("Lixeira") : path === '/add' ? t("Adicionar v\u00EDdeos") : path === '/skills' ? t('Gerar skill') : path === '/watchers' ? t('Acompanhar playlists') : t("In\u00EDcio")} · rw-ai | ${t('Catálogo de transcrições')}`; document.documentElement.lang = locale; }, [path, locale]);
    async function scan() {
        setScanBusy(true);
        try {
            await api('/scan', { method: 'POST' });
            setStats(s => s ? { ...s, scanning: true } : s);
            setToastMessage(t("Atualizando o acervo. Os novos v\u00EDdeos aparecer\u00E3o automaticamente."));
        }
        catch (e) {
            setToastMessage((e as Error).message);
        }
        finally {
            setScanBusy(false);
        }
    }
    const navigation: {
        path: string;
        label: string;
        icon: IconName;
    }[] = [{ path: '/', label: t("In\u00EDcio"), icon: 'home' }, { path: '/videos', label: t("Todos os v\u00EDdeos"), icon: 'grid' }, { path: '/channels', label: t("Canais"), icon: 'channels' }, { path: '/categories', label: t("Categorias"), icon: 'tag' }, { path: '/trash', label: t("Lixeira"), icon: 'trash' }, { path: '/add', label: t("Adicionar v\u00EDdeos"), icon: 'plus' }, { path: '/skills', label: t('Gerar skill'), icon: 'spark' }, { path: '/watchers', label: t('Acompanhar playlists'), icon: 'clock' }];
    return <><a className="skip-link" href="#main-content" onClick={e => { e.preventDefault(); document.getElementById('main-content')?.focus(); }}>{t("Pular para o conte\u00FAdo")}</a><aside className="sidebar"><a className="brand" href="#/" aria-label={t("rw-ai \u2014 In\u00EDcio")}><Wordmark /><span className="brand-subtitle">{t('Catálogo de transcrições')}</span></a><div className="nav-label">{t("BIBLIOTECA")}</div><nav aria-label={t("Navega\u00E7\u00E3o principal")}>{navigation.map(item => <a key={item.path} href={`#${item.path}`} aria-label={item.label} className={path === item.path || (item.path === '/videos' && path.startsWith('/video/')) ? 'active' : ''} aria-current={path === item.path ? 'page' : undefined}><Icon name={item.icon} size={19}/><span className="nav-text-full">{item.label}</span><span className="nav-text-short">{item.path === '/videos' ? t("V\u00EDdeos") : item.path === '/add' ? t("Adicionar") : item.path === '/skills' ? t('Skills') : item.path === '/watchers' ? t('Fontes') : item.label}</span>{item.path === '/videos' && stats && <small>{number(stats.videos)}</small>}{item.path === '/trash' && !!stats?.deleted_videos && <small>{number(stats.deleted_videos)}</small>}</a>)}</nav><div className="sidebar-divider"/><div className="nav-label">{t("SEU ACERVO")}</div><div className="sidebar-stats"><div><Icon name="channels" size={16}/><span>{t("Canais")}</span><strong>{number(stats?.channels || 0)}</strong></div><div><Icon name="file" size={16}/><span>{t("An\u00E1lises de m\u00E9todo")}</span><strong>{number(stats?.methods || 0)}</strong></div><div><Icon name="clock" size={16}/><span>{t("Horas de conte\u00FAdo")}</span><strong>{number(Math.round(stats?.hours || 0))}</strong></div></div><div className="sidebar-bottom"><div className="local-badge"><span className="status-dot"/><span>{t("Armazenamento local no Docker")}</span></div><p>{t("Seu conhecimento.")}<br />{t("No seu computador.")}</p><span className="sidebar-version">{t("RW-AI \u00B7 ACERVO PESSOAL")}</span></div></aside><div className="app-content"><header className="topbar"><a className="mobile-brand" href="#/" aria-label={t("rw-ai \u2014 In\u00EDcio")}><Wordmark /></a><form className="global-search" onSubmit={e => { e.preventDefault(); navigate(`/videos${search.trim() ? `?q=${encodeURIComponent(search.trim())}` : ''}`); }} role="search"><Icon name="search" size={19}/><input aria-label={t("Buscar em todo o acervo")} placeholder={t("Busque v\u00EDdeos, canais ou uma ideia\u2026")} value={search} onChange={e => setSearch(e.target.value)}/>{search ? <button type="button" className="icon-button" aria-label={t("Limpar busca")} onClick={() => setSearch('')}><Icon name="close" size={15}/></button> : <span className="search-hint">{t("Buscar no acervo")}</span>}<button type="submit" className="search-submit" aria-label={t("Buscar")}><Icon name="arrow" size={18}/></button></form><div className="topbar-actions"><label className="interface-language"><span className="sr-only">{t("Idioma da interface")}</span><select id="interface-language" value={locale} onChange={e => changeLocale(e.target.value as Locale)} aria-label={t("Idioma da interface")}><option value="pt">{t("Portugu\u00EAs")}</option><option value="en">{t("Inglês")}</option><option value="es">{t("Espanhol")}</option></select></label><span className="connection"><span className={`status-dot ${connectionError ? 'offline' : ''}`}/>{connectionError ? t("Desconectado") : stats?.scanning ? t("Organizando acervo") : t("Acervo local")}</span><button className="button refresh-button" onClick={scan} disabled={scanBusy || stats?.scanning} title={t("Atualizar cat\u00E1logo")}><Icon name="refresh" size={16} className={scanBusy || stats?.scanning ? 'rotating' : ''}/><span>{scanBusy || stats?.scanning ? t("Atualizando\u2026") : t("Atualizar cat\u00E1logo")}</span></button></div></header><main id="main-content" tabIndex={-1}>{connectionError && <ErrorBox message={connectionError}/>} {(categoriesError || channelsError) && !connectionError && <ErrorBox message={categoriesError || channelsError}/>} {stats?.warnings.length ? <details className="warnings"><summary><Icon name="alert" size={16}/>{stats.warnings.length} {stats.warnings.length === 1 ? t("v\u00EDdeo precisa de aten\u00E7\u00E3o") : t("v\u00EDdeos precisam de aten\u00E7\u00E3o")}<span>{t("Ver avisos")}</span></summary><ul>{stats.warnings.map((w, i) => <li key={`${w.video_id}-${i}`}><a href={w.kind === 'purge' ? '#/trash' : videoUrl(w.video_id)}>{w.video_id}</a> — {notice(w.message)}</li>)}</ul></details> : null}
    {path === '/' ? <Home stats={stats} categories={categories || []} channels={channels || []} version={version}/> : path === '/videos' ? <AllVideos params={params} stats={stats} categories={categories || []} channels={channels || []} version={version}/> : path === '/add' ? <AddVideos refreshCatalog={refresh} toast={setToastMessage}/> : path === '/watchers' ? <Watchers toast={setToastMessage}/> : path === '/skills' ? <Skills categories={categories || []} version={version} videoId={params.get('video')} toast={setToastMessage}/> : path === '/trash' ? <Trash params={params} version={version} refresh={refresh} toast={setToastMessage}/> : path === '/channels' || path === '/categories' ? <Directory key={path} type={path === '/channels' ? 'channels' : 'categories'} categories={categories || []} channels={channels || []} refresh={refresh} toast={setToastMessage}/> : path.startsWith('/video/') ? <VideoDetail key={path} id={decodeURIComponent(path.slice(7))} params={params} categories={categories || []} version={version} refresh={refresh} toast={setToastMessage}/> : <Empty title={t("Esta p\u00E1gina n\u00E3o existe")} text={t("Seu acervo est\u00E1 logo ali.")}><a className="button primary" href="#/">{t("Voltar ao in\u00EDcio")}</a></Empty>}
    <footer><Wordmark /><span>{t("Ideias para revisitar. Conhecimento para guardar.")}</span><span><span className="status-dot"/>{" " + t("Armazenado localmente")}</span></footer></main></div>{toastMessage && <div className="toast" role="status"><Icon name="check" size={18}/>{t(toastMessage)}<button className="icon-button" aria-label={t("Fechar aviso")} onClick={() => setToastMessage('')}><Icon name="close" size={15}/></button></div>}</>;
}
