const OFFICIAL_MARKET_HOSTS = new Set([
  "sec.gov",
  "www.sec.gov",
  "data.sec.gov",
  "federalreserve.gov",
  "www.federalreserve.gov",
  "bls.gov",
  "www.bls.gov",
  "bea.gov",
  "www.bea.gov",
  "apps.bea.gov"
]);

function isBlockedIpv4(hostname: string): boolean {
  const parts = hostname.split(".");
  if (parts.length !== 4 || parts.some((part) => !/^\d{1,3}$/.test(part))) return false;
  const values = parts.map(Number);
  if (values.some((value) => value > 255)) return true;
  const [a, b] = values;
  return a === 0 || a === 10 || a === 127 || a >= 224
    || (a === 100 && b >= 64 && b <= 127)
    || (a === 169 && b === 254)
    || (a === 172 && b >= 16 && b <= 31)
    || (a === 192 && (b === 0 || b === 168))
    || (a === 198 && (b === 18 || b === 19));
}

function isBlockedHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
  if (!host || host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal") || host.endsWith(".home.arpa")) return true;
  if (isBlockedIpv4(host)) return true;
  if (host.includes(":")) {
    const compact = host.replace(/^0+(?=:)/, "");
    if (compact === "::" || compact === "::1" || compact.startsWith("::ffff:") || compact.startsWith("fc") || compact.startsWith("fd") || /^fe[89ab]/.test(compact)) return true;
    const mapped = compact.match(/(?:^|:)ffff:(\d+\.\d+\.\d+\.\d+)$/);
    if (mapped && isBlockedIpv4(mapped[1])) return true;
  }
  return false;
}

/** Secondary browser-side guard. The server's registered-provider URL validation remains authoritative. */
export function safeExternalHref(value?: string | null, linkStatus?: string | null): string | null {
  if (linkStatus !== "SAFE" || !value || /[\u0000-\u001f\u007f]/.test(value)) return null;
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || parsed.username || parsed.password || isBlockedHost(host) || !OFFICIAL_MARKET_HOSTS.has(host)) return null;
    return parsed.href;
  } catch {
    return null;
  }
}
