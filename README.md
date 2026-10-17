# Wealthy — Stage 1 Ubuntu local web

**개발·검증 중**인 가상 투자 웹입니다. 실제 계좌/주문/자산을 조작하지 않습니다.

## 업데이트 및 실행

```bash
cd /home/ww/wealthy-github
git pull --ff-only
bash start_wealthy.sh
```

Chrome 접속: **http://localhost:8000/stage1**

데이터는 GitHub 소스와 분리되어 `/home/ww/wealthy-stage1-data` 아래에 보존됩니다. 첫 실행에만 관리자 계정을 등록하고 그 이후에는 기존 DB를 재사용합니다. DB·비밀번호·API 키는 GitHub에 올리지 마십시오.

## 이번 구현

- 기본 계좌 **미국 일반·한국 일반·ISA 각 1개**, 사용자 추가 계좌는 총 10개 한도
- 시작 4,000,000원: **미국 3,200,000원 / 한국 400,000원 / ISA 400,000원**
- 매월 **25일 350,000원** 가상 입금: **미국 280,000 / 한국 35,000 / ISA 35,000** (기본 정책)
- 계좌 관리 화면에서 **미래 미배분 입금**의 세 계좌 비율 변경 가능 (합계 100%); 이전 이체 불변, 중복 입금 방지
- 미국 주식/ETF Stooq 지연 종가, USD/KRW Frankfurter 참고환율; 필요한 경우 가상 환전·매수를 동일 DB 트랜잭션 처리
- 국내 6자리 종목코드의 비공식 Yahoo Finance 지연 일간 종가: 가격·통화·날짜 검증 실패 시 거래 차단
- ISA에서는 보수적인 국내상장 ETF 허용목록 `069500`, `360750`만 지원 (실제 ISA 전 상품 적격성 검증이 아님)
- 서버 가동 직후와 이후 매시간 자동 검토: 미국 ETF 1주/일 이내(최초 투자금 5% 기준), 국내 일반·ISA 각각 1주/일 이내(계좌별 가격/보유 제한)
- 지연 종가 기준 **-7% / +15%** 가상 매도 검토, 동일 날짜 자동 재매수 제한
- 포트폴리오 현금, 평균취득원가, 미실현/실현 평가, 입금액 제외 수익률 API와 화면. 시세 하나라도 없으면 전체 수익률 확정 금지
- 미국 뉴스 헤드라인 번역: 외부 번역 서비스 2곳 순차 시도, 실패 시 영어 원문/실패 상태 유지
- 최근 30일 자동 거래·검토·건너뛴 이유의 소유자별 일일 보고서
- GitHub Actions에서 Python 집중 테스트 및 React 빌드 검증 자동화

## 데이터 공급원 및 성격

| 데이터 | 공급원 | 제약 |
| --- | --- | --- |
| 미국 주식·ETF | Stooq 공개 CSV | 지연 일간 종가. 실시간 호가 아님 |
| 국내 주식·ETF | Yahoo Finance 공개 차트 | 비공식/무보증 경로. 실패 시 거래 없음 |
| 원/달러 | Frankfurter | 참고환율. 실제 환전 호가 아님 |
| 미국/한국 뉴스 | Google News 공개 RSS | 제목 위주, 분석 정확도·완전성 보장 없음 |
| 영어 헤드라인 번역 | MyMemory, 비공식 Google Translate 경로 | 두 공급원 모두 실패 가능. 실패 시 영어 원문 표시 |

현재 매매 규칙은 **검증용 기계적 기본 규칙**이며 재무제표·뉴스를 정량 결합한 완성형 AI 투자전략이나 수익 보장 서비스가 아닙니다.

## 미완성과 추가 검증

**1단계 전체 완성 판정은 아직 보류**입니다. 다음 항목은 현재 완성으로 주장하지 않습니다.

- 뉴스와 재무제표를 활용한 포괄적 투자 신호, 8개 전략 혼합, 고급 리밸런싱/백테스트
- 공식 국내주식 시세(KIS 등) 연계, 상품별 ISA 적격성 종합 검증
- 실제 세금 규정의 종합 반영, 수수료/배당·환차손익 세부 정산
- 시간대별 시장 휴장 달력, 네트워크 장애 장시간 복구, 시스템 알림
- 최신 GitHub 수정본의 Ubuntu 실제 통합 실행·브라우저 수락 테스트

## 테스트

```bash
cd /home/ww/wealthy-github
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q \
  tests/test_stage1_bootstrap.py tests/test_stage1_funding_policy.py \
  tests/test_stage1_portfolios.py tests/test_stage1_paper.py \
  tests/test_stage1_autorun.py tests/test_stage1_domestic.py \
  tests/test_stage1_risk.py tests/test_stage1_valuation.py \
  tests/test_stage1_diary.py tests/test_stage1_discovery.py
npm --prefix web/frontend run build
```

GitHub Actions: [자동 검사 내역](https://github.com/nahee2345/friendly-tribble/actions).
검사 결과를 확인하기 전에는 성공으로 간주하지 마십시오.
