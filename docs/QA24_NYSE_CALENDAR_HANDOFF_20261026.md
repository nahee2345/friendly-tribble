# [기술팀 → QA팀·디자인팀·총괄팀] PR #24 후속 NYSE 2026 세션 검증 및 오프라인 완성도 인계

**발신:** 기술팀
**공유:** QA팀 + 디자인팀 + 총괄팀(부재)
**부모:** Draft PR #24 `920d33fed0eec881671f74b36e6a73c041a31c03`
**작업 브랜치:** `tech/qa24-nyse-session-calendar-20261026`
**목표:** Windows 이식·한국투자증권 API·실주문 **이전의 Ubuntu 로컬 웹 기능 100%**.
**판정:** 시장 세션 달력/격리시험 부분 PASS; 전체 P0 BLOCKED / 병합·운영 배포 승인 보류.

## 1. QA·디자인 최신 보고 확인

- 디자인팀 PR #23: `paperControl=null`에서 `가상 자동주문 허용`을 표시하는 오안내 및 NEW/PENDING/UNKNOWN/FILLED의 차단/한국어 표시 구분 요청.
- QA팀 PR #23: 세션 시작 NAV 실제 검증기 부재, 20% 통합한도 적용 누락(일반 HTTP/Stage4 자동/USD FX), writer-lock 증명, 다중프로세스·재시작·UNKNOWN, 기존 24개 현재시각 회귀 실패 등의 P0 차단.
- **PR #24**에서 기술팀이 이 중 `paperControl` 3상태 표시, SQLite writer barrier, UNKNOWN·이전 RESERVED 재시도 차단을 반영함. 해당 변경 자체시험: Python 63 PASS / Vitest 11 PASS / 백업 19 PASS+하위 3 PASS / TS/Vite PASS, 기존 Stage7/10 85 PASS·24 FAIL. **독립 QA·실제 Ubuntu Chrome 미검증**.

## 2. 이번 기술팀의 신규 수정

1. `wealthy_server/stage1_us_market_calendar.py` 신규: **공식 NYSE 2026 정규주식시장 시간표**만 지원. 휴일 10일, 11월 27일·12월 24일 오후 1시 조기폐장, 뉴욕 동부시간/서머타임 전환을 UTC로 정확히 변환. 주말·휴일·미지원 연도·잘못된 날짜·UTC 시각 불일치·조기폐장 후에는 **예외로 차단**. 2027년 이후를 일률 평일로 취급해 열림 판정하지 않음.
2. `stage1_turnover_reservations.py`에 `establish_calendar_checked_session()` 추가. 신규 세션 생성의 **선택적 경로**에서 공식 정규장 opening/closing 시각을 만들어 기존 불변 NAV/예약 스키마에 전달. 외부 입력 임의 영업시간을 신뢰하지 않음.
3. `tests/test_stage1_us_market_calendar.py`: DST 개시/종료, 겨울/여름 UTC, 휴일·주말·날짜/연도 비지원, 조기폐장, naive clock, 범위 밖 주문, 400만원 NAV의 80만원 예약 검증.
4. `.github/workflows/tech-qa24-us-calendar.yml`: 위 새 테스트와 기존 P0 요청·원장·승인/동시성·백업 시험을 **동일 SHA**에서 실행.

**공식 시간표:** https://www.nyse.com/trade/hours-calendars (대조 https://www.nasdaq.com/market-activity/stock-market-holiday-schedule). 해당 자료는 정규장 예정일/예정시각 근거만 제공함. **거래소 긴급휴장·시세 공급망 장애·특정 종목 거래중단·현재 실제 개장 여부/자산 평가 증빙을 제공하지 않는다.**

## 3. CI 검증

- Action: https://github.com/nahee2345/friendly-tribble/actions/runs/40275044522
- 날짜 검증 + 기존 PAPER 승인/예약·동시성/원장 격리 시험: **93 PASS** (경고 1).
- 백업·재시작 격리 시험: **19 PASS + 하위시험 3 PASS**.
- 상위 PR #24 TypeScript/Vite **PASS**, Vitest **11 PASS**. 이번 변경에서는 TSX/CSS·패키지 의존성 수정 없음.
- 기존 전체 Stage7/10은 상위 PR #24 기준 **85 PASS / 24 FAIL**. 새 날짜 시험 PASS로 이 실패가 해소됐다고 쓰지 말 것.
- **QA 독립 SQLite, 실제 Ubuntu Chrome: NOT VERIFIED**.

## 4. 본 수정으로 해결되지 않은 문제

- **20% P0 차단 유지**: 일반 `POST /api/v1/stage1/order`, Stage4 자동 및 USD 환전 매수 경로가 아직 새 통합 예약원장을 호출하지 않음.
- 예약원장 `opening_nav_krw`는 여전히 독립 검증된 세션 시작 valuation/가격·환율/provenance 파이프라인에서 생성되지 않음. 달력 시간 검증은 **NAV 금액 진위와 별개**. 사용자가 임의 입력한 긴 `nav_source` 문자열은 진위 증거 아님.
- 현재 코드는 공식 달력 **2026년만 정적 검증**하므로 연말 이전 2027-2028 공식 자료 갱신·원문 검증 필요. 긴급 휴장·단축 휴장 변경 시 계속 fail closed를 유지할 라이브 확인/운영 제한 필요.
- 실제 체결과 예약의 여러 경로에 걸친 직렬화, 계좌 간 자산/환율·승인 30만원/24시간, 강제종료 후 UNKNOWN, Stage4 기존 5% 정책, 기존 과거 fixture 24 FAIL의 정확한 현재시간 감사 문제는 미종결.
- 8개 전략 전체 정량 증빙, 무료 시세/공시/뉴스 번역 권리 및 결측, 7장 16시 보고/알림·오답노트, npm `undici` HIGH, 실 Chrome 독립 QA도 미완성.

## 5. QA팀 요청

- 신규 SHA의 `tests/test_stage1_us_market_calendar.py`을 독립 컨테이너/격리 DB에서 재실행. 한국/미국 DST 및 2026-03-06 vs 03-09, 2026-10-26 vs 11-02, 추수감사절 휴장 11-26, 조기폐장 11-27·12-24, 미지원 2027년의 fail-closed 확인.
- `establish_calendar_checked_session()`와 기존 `establish_session()`이 **별개**이며 실제 API·Stage4/FX 진입점에서 어떤 경로를 사용하는지 호출 경로로 대조; 신규 달력 함수가 모의주문을 자동으로 안전하게 만든 것처럼 종결하지 말 것.
- 영업시간·시세 기준시각·opening NAV 독립 증거·실제 사용자별 20%·예약 UNKNOWN 복구의 종단간 검증 설계/재현, 기존 24개 실패 별도 OPEN.
- PASS/FAIL/BLOCKED/NOT VERIFIED로 고정 SHA, API/DB 증거, 시험일 UTC, 사용 범위 제출. 기존 운영 DB/키링/시장정보 DB 접근 금지.

## 6. 디자인팀 요청

- UI는 PR #24에서 3상태 PAPER stop label이 수정되었고 이번 PR은 TSX/CSS 변경 없음. 동일 SHA에서 인증된 실제 Chrome의 `paused=true/false/null`, 승인·UNKNOWN/RESERVED/FILLED, 뉴스 캐시 상태, 자산 범위 및 오류/보안 카드 비노출의 시각 검수를 계속 진행.
- 공식 세션 달력만으로 `거래 가능`, `검증된 NAV`, `잔여 한도 80만원` 등 성공 수치/문구는 화면에 노출하지 말 것. 지금은 **미검증 세션 NAV · 거래 보류**가 적절.
- 라이트·다크, 1366×768/1600×900/1920×1080, 확대 100/125/150/200%, Tab·Enter·Space·Escape, 사용자 변경/401·403 후 금융정보 비노출 증거 필수. 미실행은 NOT VERIFIED.

## 7. 다음 기술팀 진행 순서

1. 검증된 연도별 정규장 달력 외에 시세/환율 마지막 확인시각, 실제 시작 NAV의 평가 근거·해시·재현 원장 생성.
2. 위 증거가 모두 신뢰 가능한 경우 **일반 수동/Stage4 자동/FX USD 매수 모두** 단일 DB `reserve→virtual_orders→FILLED/UNKNOWN`으로 연결하고 병렬·재시작·크래시 회귀; 미입력시 차단.
3. 기존 Stage4 위험 제한·14일 전략 변경·승인3종·일일20% 정책 모두 보존, Stage7/10 현재 시각 QA 합격시까지 자동 PAPER 거래 OFF.
4. 전략8종·데이터 품질·뉴스·보고서·알림·백업·npm High 수정, 동일 SHA Chrome 독립 검증.

**추정 완성도: 약 53% / 100% (PR #24의 52% 대비 +1%p).** 이는 기술팀 소프트웨어 구축 및 일부 격리시험 기반 주관적 추정이며 QA 승인율/실사용 가능율/배포 승인과 다름. 총괄팀 부재 중 기존 PR 병합/운영 DB 수정·KIS 연결·실주문/외부공개 금지.
