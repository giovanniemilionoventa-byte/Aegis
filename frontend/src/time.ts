import { getLang } from "./i18n";

/**
 * The backend sends UTC. A timestamp without a zone would be read by the
 * browser as local time and shown hours off ("121 min fa"), so a naive string
 * is treated as UTC here instead of trusting every producer to add the Z.
 */
export function parseUtc(value: string): Date {
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const parsed = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(parsed.getTime()) ? new Date(value) : parsed;
}

const locale = () => (getLang() === "it" ? "it-IT" : "en-GB");

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return parseUtc(value).toLocaleString(locale(), { dateStyle: "medium", timeStyle: "short" });
}

/** "3 minuti fa" / "in 5 minutes": past and future, in the current language. */
export function timeAgo(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "—";
  const seconds = (parseUtc(value).getTime() - now) / 1000;
  const abs = Math.abs(seconds);
  const rtf = new Intl.RelativeTimeFormat(locale(), { numeric: "auto" });
  if (abs < 60) return rtf.format(Math.round(seconds), "second");
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), "hour");
  return rtf.format(Math.round(seconds / 86400), "day");
}

export const minutesSince = (value: string, now = Date.now()) =>
  (now - parseUtc(value).getTime()) / 60000;
