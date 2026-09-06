import { useEffect, useState } from 'react';
import { apiError, intlLocale, t } from './i18n';
export type Category = { id: string; name: string; count?: number };
export type Channel = { id: string; name: string; count: number };
export type Video = { id: string; title: string; channel: string; channel_id: string; duration: number; language: string; published_at: string; added_at: string; deleted_at: string | null; purge_pending: boolean; purge_error: string | null; available: boolean; has_method: boolean; has_speakers: boolean; categories: Category[]; tags: string[]; thumbnail_url: string; match?: { text: string; start_ms: number; segment_index: number } | null };
export type Detail = Video & { description: string; method: string | null; downloads: { filename: string; label: string; url: string }[]; category_override: boolean; suggestions: (Category & { score: number })[] };
export type Stats = { videos: number; deleted_videos: number; channels: number; categories: number; methods: number; hours: number; languages: { id: string; name: string; count: number }[]; scanning: boolean; last_scan: string | null; warnings: { video_id: string; message: string; kind?: 'purge' }[] };
export type Segment = { index: number; start_ms: number; end_ms: number; text: string; speaker: string | null };
export type Page = { items: Video[]; total: number; page: number; page_size: number };
export type SegmentPage = { items: Segment[]; total: number; offset: number; limit: number };
export class ApiError extends Error {
  constructor(message: string, public code?: string, public status?: number) { super(message); this.name = 'ApiError'; }
}
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } }); }
  catch { throw new Error('Não foi possível conectar ao aplicativo. Verifique se ele está ligado no Docker.'); }
  if (!response.ok) { const error = await response.json().catch(() => ({})); throw new ApiError(apiError(error.detail, error.error_code, response.status), error.error_code, response.status); }
  return response.json();
}
export function useApi<T>(path: string, version = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => { let active = true; setLoading(true); setError(''); api<T>(path).then(value => { if (active) setData(value); }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [path, version]);
  return { data, error, loading, setData };
}
export function videoUrl(id: string, segment?: number) { return `#/video/${encodeURIComponent(id)}${segment != null ? `?segment=${segment}` : ''}`; }
export function duration(seconds: number) { const n = Math.max(0, Math.round(seconds || 0)); const h = Math.floor(n / 3600); const m = Math.floor((n % 3600) / 60); return h ? `${h}:${String(m).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}` : `${m}:${String(n % 60).padStart(2, '0')}`; }
export function number(n: number) { return (n || 0).toLocaleString(intlLocale()); }
export function language(code: string) { try { return new Intl.DisplayNames([intlLocale()], { type: 'language' }).of(code) || code; } catch { return code || t('Não informado'); } }
export function date(value: string) { if (!value) return ''; const d = new Date(value); return Number.isNaN(d.valueOf()) ? '' : d.toLocaleDateString(intlLocale(), { day: 'numeric', month: 'short', year: 'numeric' }); }
