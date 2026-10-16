export const shortId = (id: string) => `${id.slice(0, 8)}…${id.slice(-4)}`;

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3 } as Intl.DateTimeFormatOptions);
}

export function formatAmount(amount: string, currency: string): string {
  const n = Number(amount);
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export const formatPercent = (ratio: number | null | undefined) => (ratio == null ? "—" : `${(ratio * 100).toFixed(ratio === 1 || ratio === 0 ? 0 : 1)}%`);
export const formatInt = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString());

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function between(from: string | null, to: string | null): number | null {
  return from && to ? (new Date(to).getTime() - new Date(from).getTime()) / 1000 : null;
}

export function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : m ? `${m}m ${Math.floor(seconds % 60)}s` : `${Math.floor(seconds)}s`;
}
