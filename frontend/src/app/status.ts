export const statusTitle = 'Palvelun tila';
export const statusLead = 'Mielenosoitukset.fi — reaaliaikainen tila';
export const statusUnavailable = 'Palvelun tilaa ei voitu hakea.';

// The database row is always there. Go adds the cache and file storage rows, in
// this order, only where their checks are configured (go-api/status_checks.go).
const serviceMessages = {
  Tietokanta: 'Yhteys kunnossa',
  Välimuisti: 'Yhteys kunnossa',
  Tiedostovarasto: 'Saavutettavissa',
} as const;
type ServiceName = keyof typeof serviceMessages;
const optionalServices: ServiceName[] = ['Välimuisti', 'Tiedostovarasto'];

export type ServiceStatus = {
  overall_ok: boolean;
  services: { name: ServiceName; status: 'ok' | 'err'; message: string }[];
  updated_at: string;
  latency_ms: number;
};

export function uncheckedServicesNote(status: ServiceStatus | null): string | null {
  const names = new Set(status?.services.map(service => service.name));
  const cache = !names.has('Välimuisti');
  const storage = !names.has('Tiedostovarasto');
  if (cache && storage) return 'Välimuistin ja tiedostovaraston tilaa ei tarkisteta.';
  if (cache) return 'Välimuistin tilaa ei tarkisteta.';
  if (storage) return 'Tiedostovaraston tilaa ei tarkisteta.';
  return null;
}

function validServices(raw: unknown): ServiceStatus['services'] | null {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 3) return null;
  const allowed: ServiceName[] = ['Tietokanta', ...optionalServices];
  const services: ServiceStatus['services'] = [];
  let next = 0;
  for (const [index, service] of raw.entries()) {
    const position = allowed.indexOf(service?.name);
    if (position < next || (index === 0) !== (position === 0) || !['ok', 'err'].includes(service.status)
      || service.message !== (service.status === 'ok' ? serviceMessages[allowed[position]] : 'Ei vastaa')) return null;
    next = position + 1;
    services.push({ name: allowed[position], status: service.status, message: service.message });
  }
  return services;
}

export async function loadStatus(readBase: string): Promise<ServiceStatus | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1500);
  try {
    const response = await fetch(`${readBase}/status`, { signal: controller.signal });
    if (!response.ok) return null;
    const payload = await response.json();
    if (typeof payload?.overall_ok !== 'boolean'
      || typeof payload.updated_at !== 'string' || !/^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2} UTC$/.test(payload.updated_at)
      || !Number.isSafeInteger(payload.latency_ms) || payload.latency_ms < 0) return null;
    const services = validServices(payload.services);
    if (!services || payload.overall_ok !== services.every(service => service.status === 'ok')) return null;
    return { overall_ok: payload.overall_ok, services, updated_at: payload.updated_at, latency_ms: payload.latency_ms };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
