import { useEffect, useState } from 'react';

export type FlashNotice = { category: 'success' | 'error' | 'warning' | 'info'; message: string };

/** Keeps at most five well-formed messages from Flask's `/get_flash_messages` answer. */
export function readFlashNotices(value: unknown): FlashNotice[] {
  const messages = value && typeof value === 'object' ? (value as { messages?: unknown }).messages : null;
  if (!Array.isArray(messages)) return [];
  return messages.filter((item): item is { category?: unknown; message: string } => !!item && typeof item === 'object'
    && typeof (item as { message?: unknown }).message === 'string')
    .slice(0, 5).map(item => ({
      category: typeof item.category === 'string' && ['success', 'error', 'warning', 'info'].includes(item.category)
        ? item.category as FlashNotice['category'] : 'info',
      message: item.message,
    }));
}

/** Reads (and so consumes) the messages Flask flashed before redirecting to a React page. */
export function useFlashNotices(): FlashNotice[] {
  const [notices, setNotices] = useState<FlashNotice[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    void fetch('/get_flash_messages', { credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: { Accept: 'application/json' } }).then(async response => {
      if (response.ok) setNotices(readFlashNotices(await response.json()));
    }).catch(() => {}).finally(() => clearTimeout(timer));
    return () => { controller.abort(); clearTimeout(timer); };
  }, []);
  return notices;
}

export function FlashNotices({ notices }: { notices: FlashNotice[] }) {
  return <>{notices.map((notice, index) => <div key={index} className={`flash-message flash-message--inline flash-${notice.category}`}
    role={notice.category === 'error' ? 'alert' : 'status'}><span className="flash-text">{notice.message}</span></div>)}</>;
}
