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

const offlineEducation: Record<TargetStrategyId, {description:string;philosophy:string;criteria:string[];benefits:string[];risks:string[];mapping:string}> = {
  LONG_TERM_INDEX:{description:'광범위한 시장지수를 장기간 보유하는 방식',philosophy:'시장 전체에 분산하고 낮은 비용으로 장기 복리 효과를 추구합니다.',criteria:['추종 지수와 분산 정도','총보수 및 추적오차','장기 보유 가능성'],benefits:['개별 종목 위험 분산','낮은 운용 비용'],risks:['시장 전체 하락에 노출','수익 보장 없음'],mapping:'지수형 상품 분류·장기 보유 정책을 참조합니다.'},
  VALUE:{description:'기업의 가치 대비 상대적으로 낮은 가격을 찾는 방식',philosophy:'재무제표와 현금흐름을 검증해 가치와 가격 차이를 분석합니다.',criteria:['수익성과 재무건전성','기업가치 대비 가격','현금흐름의 지속성'],benefits:['가격 대비 가치 중심'],risks:['가치 함정','재무 수치의 시차'],mapping:'가치 지표의 최신성·비교 가능성이 필요합니다.'},
  QUALITY_GROWTH:{description:'수익성과 성장의 지속성을 함께 검토',philosophy:'재무적으로 건전하고 이익을 지속적으로 성장시키는 기업을 찾습니다.',criteria:['이익 증가율','자본수익률','부채와 현금흐름'],benefits:['성장·재무건전성을 같이 분석'],risks:['높은 밸류에이션','성장 둔화'],mapping:'우량 성장지표를 검증 후 점수화합니다.'},
  GROWTH:{description:'매출과 이익의 높은 성장 가능성을 분석',philosophy:'높은 성장성의 지속성과 그에 대한 가격을 따로 평가합니다.',criteria:['매출·이익 성장률','성장 원천과 시장 규모','희석·현금 소모'],benefits:['고성장 기업 탐색'],risks:['높은 가격 변동성','실적 기대 미달'],mapping:'성장 자료가 불충분하면 평가하지 않습니다.'},
  HIGH_DIVIDEND:{description:'현재 배당수익률과 지급 안정성 중심',philosophy:'높은 배당률뿐 아니라 지급 유지 가능성을 검토합니다.',criteria:['최근 배당수익률','배당성향','현금흐름과 부채'],benefits:['현금흐름 기대'],risks:['배당 삭감','주가 하락에 따른 착시 고배당'],mapping:'검증된 배당·가격·재무 자료를 요구합니다.'},
  DIVIDEND_GROWTH:{description:'배당이 지속적으로 증가하는 기업 분석',philosophy:'배당 증액의 꾸준함과 사업 현금창출력을 봅니다.',criteria:['다년간 배당 성장','이익·현금흐름의 뒷받침','지급 안정성'],benefits:['장기 현금흐름 성장 가능성'],risks:['배당 증액 중단','배당수익률이 낮을 수 있음'],mapping:'배당 이력과 기업 재무자료를 종합합니다.'},
  MOMENTUM:{description:'검증된 가격 추세를 이용하는 방식',philosophy:'가격의 지속적인 추세를 활용하되 손실 한도와 거래 비용을 고려합니다.',criteria:['중장기 수익률','변동성과 추세 지속성','유동성과 거래 비용'],benefits:['추세에 빠르게 대응 가능'],risks:['추세 급반전','빈번한 매매비용'],mapping:'가격 시계열 누락 시 모멘텀 점수를 보류합니다.'},
  ASSET_ALLOCATION:{description:'주식·현금성 자산 등 비중 분산',philosophy:'상관관계가 다른 자산을 적절히 배치하고 위험을 관리합니다.',criteria:['목표 비중','위험 허용도','리밸런싱 및 현금 비중'],benefits:['집중 위험 완화'],risks:['모든 자산이 동시에 하락할 가능성','리밸런싱 비용'],mapping:'계좌 및 자산별 실제 비중이 검증되어야 합니다.'}
};
export function safeCatalogItem(id: TargetStrategyId, remote?: StrategyCatalogItem): StrategyCatalogItem {
  const education=offlineEducation[id];
  return {
    ...remote,
    id,
    name: remote?.name || strategyNames[id],
    description: remote?.description || education.description,
    philosophy: remote?.philosophy || education.philosophy,
    criteria: remote?.criteria?.length ? remote.criteria : education.criteria,
    benefits: remote?.benefits?.length ? remote.benefits : education.benefits,
    risks: remote?.risks?.length ? remote.risks : education.risks,
    required_data: remote?.required_data || [],
    status: remote?.status || '교육용 설명만 제공 · 실데이터 평가 미확인',
    mapping: remote?.mapping || education.mapping
  };
}
