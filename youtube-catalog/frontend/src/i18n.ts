import { useSyncExternalStore } from 'react';
import { translations } from './translations';

export type Locale = 'pt' | 'en' | 'es';
const storageKey = 'rw-ai.locale';
function initialLocale(): Locale { try { const value = localStorage.getItem(storageKey); return value === 'en' || value === 'es' ? value : 'pt'; } catch { return 'pt'; } }
let locale: Locale = initialLocale();
const listeners = new Set<() => void>();
const canonical = new Map<string, string>();
for (const [source, entry] of Object.entries(translations)) { canonical.set(entry.en, source); canonical.set(entry.es, source); }
export function getLocale() { return locale; }
export function intlLocale() { return { pt: 'pt-BR', en: 'en-US', es: 'es-ES' }[locale]; }
export function setLocale(value: Locale) { if (value === locale) return; locale = value; try { localStorage.setItem(storageKey, value); } catch { /* Language selection still works when browser storage is unavailable. */ } listeners.forEach(listener => listener()); }
export function useLocale(): [Locale, typeof setLocale] { const value = useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener); }, getLocale, () => 'pt' as Locale); return [value, setLocale]; }
export function t(source: string, values?: Record<string, string | number>) { const key = translations[source] ? source : canonical.get(source) || source; const translated = locale === 'pt' ? key : translations[key]?.[locale] || source; return values ? translated.replace(/\{(\w+)\}/g, (match, name: string) => String(values[name] ?? match)) : translated; }
export function notice(source: string) { if (locale === 'pt' || translations[source] || canonical.has(source)) return t(source); return t('Um arquivo do acervo precisa de atenção. Confira os arquivos originais e atualize o catálogo.'); }
const errorMessages: Record<string, string> = {
  items_failed: 'Um ou mais vídeos não foram processados. Tente os pendentes novamente.', queue_full: 'A fila está cheia. Aguarde alguns processamentos terminarem antes de adicionar mais vídeos.', invalid_url: 'Informe um endereço válido do YouTube.',
  discovery_failed: 'Não foi possível obter os vídeos deste endereço.', unavailable: 'O vídeo não está disponível para download.', live_unsupported: 'Transmissões ao vivo em andamento não são compatíveis.', download_failed: 'Não foi possível baixar o vídeo.', conversion_failed: 'Não foi possível preparar o áudio.', transcription_failed: 'Não foi possível transcrever o áudio.', invalid_artifacts: 'Não foi possível validar os arquivos da transcrição.', model_download_failed: 'Não foi possível baixar o modelo de transcrição.', model_checksum: 'O modelo de transcrição precisa ser baixado novamente.', storage_error: 'Não foi possível acessar o armazenamento local.', existing: 'Este vídeo já está no acervo.', cancelled: 'Processamento cancelado.', interrupted: 'Processamento interrompido. Será retomado ao ligar o aplicativo.',
};
export function jobError(code?: string | null, fallback?: string | null) { if (code && errorMessages[code]) return t(errorMessages[code]); if (fallback && translations[fallback]) return t(fallback); return t('Não foi possível concluir esta etapa. Tente novamente.'); }
export function apiError(detail: unknown, code?: string, status?: number): string { if (code && errorMessages[code]) return errorMessages[code]; if (typeof detail === 'string' && (translations[detail] || canonical.has(detail))) return canonical.get(detail) || detail; if (status === 404) return 'Este item não foi encontrado.'; if (status === 409) return 'Esta ação não está disponível no estado atual. Atualize e tente novamente.'; if (status === 422 || status === 400) return 'Confira os dados informados e tente novamente.'; if (status === 403) return 'Não foi possível acessar este recurso.'; if (status === 503) return 'O processamento ainda não está disponível. Tente novamente em instantes.'; return 'Não foi possível concluir a operação. Tente novamente.'; }
