import type { ReactNode } from 'react';
export type IconName = 'home' | 'grid' | 'channels' | 'tag' | 'search' | 'refresh' | 'arrow' | 'left' | 'right' | 'book' | 'file' | 'external' | 'download' | 'close' | 'check' | 'clock' | 'plus' | 'edit' | 'folder' | 'alert' | 'filter' | 'trash' | 'restore';
export function Icon({ name, size = 20, className = '' }: {
    name: IconName;
    size?: number;
    className?: string;
}) {
    const paths: Record<IconName, ReactNode> = {
        home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/></>,
        grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
        channels: <><rect x="3" y="6" width="18" height="14" rx="3"/><path d="m8 2 4 4 4-4M10 10l5 3-5 3Z"/></>,
        tag: <><path d="M20 13 11 22 2 13V3h10l8 8a1.5 1.5 0 0 1 0 2Z"/><circle cx="7" cy="8" r="1"/></>,
        search: <><circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/></>,
        refresh: <><path d="M20 7V2l-3 3a8 8 0 0 0-13 5m0 7v5l3-3a8 8 0 0 0 13-5M20 2h-5M4 22h5"/></>,
        arrow: <><path d="M4 12h16m-6-6 6 6-6 6"/></>,
        left: <path d="m14 5-7 7 7 7"/>, right: <path d="m9 5 7 7-7 7"/>,
        book: <><path d="M12 5c-4-3-8-2-10-1v15c3-1 6-2 10 1 4-3 7-2 10-1V4c-2-1-6-2-10 1Zm0 0v15"/></>,
        file: <><path d="M14 2H5a1 1 0 0 0-1 1v18h16V8Zm0 0v6h6M8 12h8m-8 4h6"/></>,
        external: <><path d="M14 3h7v7m0-7L10 14M10 3H4v17h17v-6"/></>,
        download: <><path d="M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6"/></>,
        close: <path d="m6 6 12 12M6 18 18 6"/>, check: <path d="m4 12 5 5L20 6"/>,
        clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
        plus: <path d="M12 4v16M4 12h16"/>,
        edit: <><path d="m15 3 6 6L8 22H2v-6ZM12 6l6 6"/></>,
        folder: <path d="M3 5h6l2 3h10v12H3Z"/>,
        alert: <><path d="m12 3 10 18H2Z M12 9v5"/><circle cx="12" cy="17.5" r=".5"/></>,
        trash: <><path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/></>,
        restore: <><path d="M3 10a9 9 0 1 1 1 8M3 4v6h6M12 7v5l4 2"/></>,
        filter: <><path d="M4 7h16M4 17h16"/><circle cx="8" cy="7" r="2"/><circle cx="16" cy="17" r="2"/></>,
    };
    return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>{paths[name]}</svg>;
}
