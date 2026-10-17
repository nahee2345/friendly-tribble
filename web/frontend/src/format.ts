export function formatDateTime(value?: string | null): string {
  if (!value) return "확인 필요";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "확인 필요";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short"
  }).format(date);
}

export function formatKrw(value?: string | null): string {
  if (value == null || value === "") return "미입력";
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return "확인 필요";
  const grouped = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const fraction = match[3] ? `.${match[3]}` : "";
  return `${match[1]}₩${grouped}${fraction}`;
}

export function displayScope(scope?: string | null): string {
  const labels: Record<string, string> = {
    SAMPLE: "SAMPLE · 샘플 자료",
    MANUAL_PAPER_SNAPSHOT: "MANUAL_PAPER_SNAPSHOT · 수동 모의 자료",
    MANUAL_ACTUAL: "MANUAL_ACTUAL · 수동 실제 자료",
    AUTOMATED_MARKET: "AUTOMATED_MARKET · 자동 시장 자료",
    MISSING: "MISSING · 자료 미입력"
  };
  return scope ? labels[scope] ?? "자료 영역 확인 필요" : "자료 미입력";
}
