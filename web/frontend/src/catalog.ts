import type { StrategyCatalogItem, TargetStrategyId } from "./types";

export const strategyNames: Record<TargetStrategyId, string> = {
  LONG_TERM_INDEX: "장기 지수투자",
  VALUE: "가치투자",
  QUALITY_GROWTH: "우량 성장투자",
  GROWTH: "성장주 투자",
  HIGH_DIVIDEND: "고배당 투자",
  DIVIDEND_GROWTH: "배당성장 투자",
  MOMENTUM: "모멘텀 투자",
  ASSET_ALLOCATION: "자산배분 투자"
};

export function safeCatalogItem(id: TargetStrategyId, remote?: StrategyCatalogItem): StrategyCatalogItem {
  if (remote) return remote;
  return {
    id,
    name: strategyNames[id],
    description: "전략 설명은 서버 카탈로그 연결 후 표시됩니다.",
    criteria: [],
    benefits: [],
    risks: [],
    required_data: [],
    status: "평가 자료 연결 필요",
    mapping: "기존 평가 엔진과의 대응 상태 확인 필요"
  };
}
