import { useEffect, useState } from 'react';
import type { DetailMessages } from './types';

type Point = { latitude: number; longitude: number };

const SEARCH_URL = 'https://nominatim.openstreetmap.org/search';

function validPoint(latitude: unknown, longitude: unknown): Point | null {
  const lat = typeof latitude === 'string' ? Number(latitude) : latitude;
  const lon = typeof longitude === 'string' ? Number(longitude) : longitude;
  if (typeof lat !== 'number' || typeof lon !== 'number') return null;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  return { latitude: lat, longitude: lon };
}

/**
 * Small location map, loaded only after the visitor presses "Näytä kartta".
 *
 * Events with stored coordinates embed the map straight away. Many newer events
 * have only an address (coordinates used to be filled in by the old Flask page),
 * so for those the address is looked up from OpenStreetMap at that moment. No
 * third-party request happens before the visitor asks for the map.
 */
export default function DetailMap({ latitude, longitude, query, messages }: {
  latitude: number | null; longitude: number | null; query: string; messages: DetailMessages;
}) {
  const stored = validPoint(latitude, longitude);
  const [point, setPoint] = useState<Point | null>(stored);
  const [failed, setFailed] = useState(!stored && !query.trim());

  useEffect(() => {
    if (stored || !query.trim()) return;
    const controller = new AbortController();
    const params = new URLSearchParams({ format: 'jsonv2', limit: '1', countrycodes: 'fi', q: query.trim() });
    fetch(`${SEARCH_URL}?${params}`, { signal: controller.signal, headers: { Accept: 'application/json' }, credentials: 'omit' })
      .then(response => (response.ok ? response.json() : []))
      .then((rows: unknown) => {
        const first = Array.isArray(rows) ? rows[0] as { lat?: unknown; lon?: unknown } | undefined : undefined;
        const found = first ? validPoint(first.lat, first.lon) : null;
        if (found) setPoint(found); else setFailed(true);
      })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
    // The lookup depends only on the address; stored coordinates skip it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  if (failed) return <p className="detail-note" role="status">{messages.mapError ?? messages.error}</p>;
  if (!point) return <p role="status">{messages.mapLoading}</p>;
  const bbox = [Math.max(-180, point.longitude - .012), Math.max(-90, point.latitude - .006),
    Math.min(180, point.longitude + .012), Math.min(90, point.latitude + .006)].join(',');
  const params = new URLSearchParams({ bbox, layer: 'mapnik', marker: `${point.latitude},${point.longitude}` });
  return <iframe className="detail-map" src={`https://www.openstreetmap.org/export/embed.html?${params}`}
    title={messages.map} loading="lazy" referrerPolicy="no-referrer" sandbox="allow-scripts allow-same-origin allow-popups" />;
}
