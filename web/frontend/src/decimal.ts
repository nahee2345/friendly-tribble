const DECIMAL_PATTERN = /^(?:0|[1-9]\d{0,2})(?:\.(\d{1,6}))?$/;

export interface ParsedDecimal {
  coefficient: bigint;
  scale: number;
}

export function parsePercent(value: string): ParsedDecimal | null {
  const normalized = value;
  const match = DECIMAL_PATTERN.exec(normalized);
  if (!match) return null;
  const fraction = match[1] ?? "";
  const [whole] = normalized.split(".");
  const coefficient = BigInt(whole) * 10n ** BigInt(fraction.length) + BigInt(fraction || "0");
  const hundred = 100n * 10n ** BigInt(fraction.length);
  if (coefficient > hundred) return null;
  return { coefficient, scale: fraction.length };
}

export function canonicalPercent(value: string): string | null {
  const parsed = parsePercent(value);
  if (!parsed) return null;
  if (parsed.scale === 0) return parsed.coefficient.toString();
  const base = 10n ** BigInt(parsed.scale);
  const whole = parsed.coefficient / base;
  const fraction = (parsed.coefficient % base).toString().padStart(parsed.scale, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole.toString();
}

export function sumPercentages(values: readonly string[]): string | null {
  const parsed = values.map(parsePercent);
  if (parsed.some((value) => value === null)) return null;
  const entries = parsed as ParsedDecimal[];
  const scale = Math.max(0, ...entries.map((value) => value.scale));
  const total = entries.reduce(
    (sum, value) => sum + value.coefficient * 10n ** BigInt(scale - value.scale),
    0n
  );
  return formatSigned(total, scale);
}

function formatSigned(coefficient: bigint, scale: number): string {
  const negative = coefficient < 0n;
  const absolute = negative ? -coefficient : coefficient;
  if (scale === 0) return `${negative ? "-" : ""}${absolute}`;
  const base = 10n ** BigInt(scale);
  const whole = absolute / base;
  const fraction = (absolute % base).toString().padStart(scale, "0").replace(/0+$/, "");
  const body = fraction ? `${whole}.${fraction}` : whole.toString();
  return `${negative ? "-" : ""}${body}`;
}

export function compareDecimal(left: string, right: string): -1 | 0 | 1 | null {
  const a = parsePercent(left);
  const b = parsePercent(right);
  if (!a || !b) return null;
  const scale = Math.max(a.scale, b.scale);
  const av = a.coefficient * 10n ** BigInt(scale - a.scale);
  const bv = b.coefficient * 10n ** BigInt(scale - b.scale);
  return av === bv ? 0 : av < bv ? -1 : 1;
}

export function totalGuidance(values: readonly string[]): { valid: boolean; total: string | null; message: string } {
  const total = sumPercentages(values);
  if (total === null) return { valid: false, total: null, message: "모든 비중에 0~100 사이 숫자를 입력해 주세요." };
  const comparison = compareDecimal(total, "100");
  if (comparison === 0) return { valid: true, total, message: "합계가 100%입니다. 서버 검증 후 저장할 수 있습니다." };

  const parsed = parsePercent(total);
  if (!parsed) {
    // 합계는 개별 값과 달리 100을 넘을 수 있으므로 별도로 뺀다.
    const parts = total.split(".");
    const scale = parts[1]?.length ?? 0;
    const totalCoefficient = BigInt(parts.join(""));
    const hundred = 100n * 10n ** BigInt(scale);
    const difference = formatSigned(totalCoefficient > hundred ? totalCoefficient - hundred : hundred - totalCoefficient, scale);
    return { valid: false, total, message: `${difference}%를 ${totalCoefficient > hundred ? "줄여야" : "추가해야"} 합니다.` };
  }
  const hundred = 100n * 10n ** BigInt(parsed.scale);
  const difference = formatSigned(hundred - parsed.coefficient, parsed.scale);
  return { valid: false, total, message: `${difference}%를 추가해야 합니다.` };
}

export function sliderValue(value: string): string {
  const parsed = parsePercent(value);
  if (!parsed) return "0";
  // range는 정수/소수 문자열을 그대로 받을 수 있다. 0.01보다 긴 입력은 숫자칸에서 보존한다.
  const canonical = canonicalPercent(value) ?? "0";
  const fraction = canonical.split(".")[1] ?? "";
  return fraction.length <= 2 ? canonical : canonical.slice(0, canonical.indexOf(".") + 3);
}
