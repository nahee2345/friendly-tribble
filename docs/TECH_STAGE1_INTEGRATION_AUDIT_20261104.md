# Wealthy Stage 1 — integration and defect audit (2026-11-04)

**발신:** 기술팀  
**공유 대상:** 총괄팀 + QA팀 + 디자인팀  
**대상 저장소:** `nahee2345/friendly-tribble`  
**분석 기준 고정 SHA:** `6596f413405c17841460c913d078d2903c887929` (Draft PR #25)  
**보고 브랜치:** `tech/stage1-integration-audit-20261104`  
**범위:** Windows 이식 및 KIS 연동 전 Ubuntu 로컬 웹; 기존 main/운영 DB/키링/실거래/자동 PAPER 실행 변경 없음  
**판정:** 1단계 정적/문서/CI 증거 감사 완료, 실제 API-DB-Chrome 독립 E2E **NOT VERIFIED**. P0 **BLOCKED**, 배포/병합 보류.

## 1. 확인한 사실과 증거

1. PR #25는 **Draft / 미병합**. 해당 후보 HEAD는 위 고정 SHA. `main...PR25` 비교 시 후보 180 commits ahead, 0 behind, 변경 파일 목록 50개 이상 확인됨. `main`만 배포하면 후보 기능 포함되지 않음.
2. PR #25 변경 파일 5개: `.github/workflows/tech-qa24-us-calendar.yml`, `docs/QA24_NYSE_CALENDAR_HANDOFF_20261026.md`, `tests/test_stage1_us_market_calendar.py`, `wealthy_server/stage1_turnover_reservations.py`, `wealthy_server/stage1_us_market_calendar.py`.
3. 공식 NYSE 2026 정규장 달력은 정적 검사 기능. `establish_calendar_checked_session`은 `establish_session`에 시각을 전달할 뿐 실제 opening NAV의 가격·환율·출처 진위를 검증하지 않음. `nav_source` 문자열 길이 검사 ≠ 검증된 provenance.
4. `stage1_turnover_reservations.py`의 모듈 주석은 일반 수동·Stage4 주문이 예약원장을 아직 호출하지 않는다고 명시. 이전 기술 인계 문서에서는 `POST /api/v1/stage1/order`, Stage4 자동, `auto_buy_usd_with_fx` 세 경로 미연결이라고 확인. 따라서 거래한도 20% P0 **BLOCKED**.
5. `stage1_portfolios.py`는 `virtual_orders`를 `PRIMARY KEY(owner,portfolio,request_id)`로 저장하지만 새 예약 클레임은 `PRIMARY KEY(owner,request_id)`. 동일 owner의 여러 계좌에서 요청 ID 충돌/재사용 정책을 계약화해야 함.
6. `stage1_controls.py`의 `status`는 제어 행이 없으면 paused=true를 표시함. 반면 `stage1_portfolios.py::_assert_not_paused`는 테이블/행이 없으면 예외 없이 통과하는 구조로 읽힘. **표시 기본 중지와 주문 차단 동작 불일치 가능성 — 직접 API/DB 재현 전까지 잠재 P0**. 장치 제어 UI와 실제 거래 게이트의 같은 상태 계약을 요구함.
7. `stage4_portfolio.py`에는 별도 `stage4_execution_claims`와 `recorded_claims`가 있음. 새 통합 원장 `stage1_turnover_claims`와의 상태·재시작·원자적 정합성 연결이 독립적으로 입증되지 않음.
8. 기존 원본 Stage7/10 85 PASS / 24 FAIL, PR #25 대상 93 PASS, 백업 19 PASS + 하위 3 PASS, 앞선 Vitest 11 PASS/빌드 PASS는 **서로 다른 검증 범위**. GitHub Actions run 40275214508의 pinned-calendar-and-integrity 성공 확인. 전체 회귀 PASS로 해석 금지.
9. 기존 24 FAIL 분해: Stage4 10, Stage5 5, Stage6 1, Stage7 8. `docs/OFFLINE_PREKIS_100_ACCEPTANCE_20261026.md`는 pytest collection 이전 시간고정 시 2026-10-19 기준 역사적 83 PASS, 현재 시각 재실행 24 FAIL을 기록. 기존 테스트 삭제/생산 감사 완화 금지.
10. 운영 DB, 실제 Ubuntu Chrome, Secret Service, 사용자 변경/401·403/백업 복원에 대한 **이번 보고의 직접 재실행 없음**. 저장소 일부 핵심 파일과 이전 검증 문서를 읽은 정적 감사이며 전체 코드 완전 열람 또는 모든 경로의 동적 검증은 아님.

## 2. 진입점-저장소 통합 매트릭스

| 경로 | 확인한 구성 | 미완/위험 | 다음 증거 |
|---|---|---|---|
| 일반 PAPER HTTP `POST /api/v1/stage1/order` | `app.py`, `stage1_portfolios.py`, `virtual_orders`, `stage1_controls.py` | 통합 20% 원자적 예약 미연결; 행 없는 중지 기본값 불일치 가능성 | HTTP→service→SQLite trace, missing-control fail-closed |
| USD 가상환전+매수 | `Portfolios.auto_buy_usd_with_fx` | 전역 20% 세션 claim 미연결, FX provenance 및 승인 경합 | 단일 transaction, 검증된 FX/quote |
| Stage4 자동 | `stage4_portfolio.py` `PortfolioEngine`, `stage4_execution_claims` | 별도 claim과 session claim 관계 불명, 5% 정책 보존 필요 | 단계별 상태 전이/동시·재시작 |
| NAV/세션 | `stage1_us_market_calendar.py`, `stage1_turnover_reservations.py` | 공식 2026 세션만 검사; opening valuation 검증 부재; 2027 미지원 | 가격·FX·보유원장·기준시각·원문 해시 |
| 승인/긴급중지 | `stage1_controls.py`, `Stage1Page.tsx` | 조회 기본 중지와 DB 실행 차단 동일성 미증명; 만료/경합 | absent/null/true/false + 승인 30만원/24h |
| QA·UI | isolated CI, React view | 실제 Ubuntu Chrome/E2E 미검증 | 동일 SHA·격리 DB·로그·캡처 |

## 3. 우선순위별 작업 백로그 및 파일

### P0-A: API-DB 주문 경로 확정
- `app.py` 주문 엔드포인트 → `stage1_portfolios.py`의 `trade`, `auto_buy_usd_with_fx` → `stage4_portfolio.py` 실제 커밋을 호출 그래프로 계측. `stage1_turnover_reservations.py` 미호출/우회 지점을 모두 찾기.
- DB 테이블 `virtual_orders`, `stage1_turnover_sessions`, `stage1_turnover_claims`, `stage4_execution_claims`, `stage1_approvals`, `stage1_trade_control`의 키/시간/상태 의미를 명세화.
- 제어 행 미존재, `paused` 조회 오류, 시세/FX 없음, 세션 NAV 미검증 조건에서 모든 쓰기 경로가 **차단**되는지 독립 테스트. 발견 시 원본 DB 아닌 격리 fixture에서 재현.

### P0-B: opening NAV 검증 모델
- 세션 시작 시각의 현금+보유주식 평가, 기준시세/환율, source/id/as_of, 신뢰 기간, 해시·재현 입력과 사용자별 세션 불변성을 정의.
- 오류/결측은 거래 보류. 2027년 지원 전까진 미지원 연도 fail-closed. 오프라인에서 live opening 증거가 없으면 실제 장중 자동주문을 가능하다고 주장하지 않음.

### P0-C: 단일 SQLite 주문 트랜잭션 설계 (2단계 이관)
- 단일 writer lock에서 control→market/NAV→approval→20% turnover reserve→virtual_orders→claim finalization. 20%는 사용자별 모든 PAPER 계좌/매수+매도+UNKNOWN 포함.
- 기존 `request_id` 규칙, 다중계좌 키, Stage4 5% 한도, 30만원 승인/24시간, 14일 전략잠금, 중단 조건 보존.
- 중복/UNKNOWN/강제종료 복구는 임의 재실행 금지. 저장된 상태와 원장 대조 전 fill 불가.

### P0-D: 회귀 분리 및 독립 E2E
- 역사적 fixture 24 FAIL과 현재시장/현재시각 안전성 실패를 별도 workflow로 측정. 프로덕션 시간/보안 로직 수정으로 테스트를 억지로 통과시키지 않음.
- `tests/test_p0_session_reservations.py`, `tests/test_qa_p0_api_ledger.py`, `tests/test_stage1_us_market_calendar.py`, `tests/test_stage4_portfolio.py`, `tests/test_stage1_controls_cards.py` 등을 고정 SHA/격리 DB에서 실행.
- 동일 SHA HTTP 인증·CSRF·두 사용자·다중탭·백업·재시작·Chrome 검증 계획.

### P1 (P0 후순위)
- 8개 전략 정량, 공급원 출처/권리·뉴스/번역, 16시 7카드·알림, npm High 취약점, 시각·접근성.

## 4. 팀별 검토 요청 — 응답을 GitHub에 기록

**총괄팀:** PR #25를 감사 기준으로 후속 PR을 누적하는 방식 승인 여부, P0-A→B→C→D 순서 승인, 기존 Draft PR 병합/배포 보류, `main` 무변경 원칙 확인. 사용자 데이터와 KIS/Windows 범위는 제외.

**QA팀:** 동일 고정 SHA에서 (1) 제어 행 미존재 주문 여부, (2) 일반 HTTP·FX·Stage4 각각 session claim 미연결 재현, (3) 재시도/UNKNOWN/다중프로세스/상태 충돌, (4) 원본 24 FAIL의 과거시각/현재시각 분리 결과 제출. 증거: API 요청·응답, DB row delta, pytest 로그, 정확한 SHA 및 UTC. 실운영 DB 접근 금지.

**디자인팀:** `paperControl=null`와 DB 제어 행 부재에서 '실제 주문 가능'으로 잘못 보이지 않도록 문구 검토. 3상태 중지/승인·UNKNOWN·미검증 NAV·뉴스 캐시 및 401/403 금융정보 숨김을 실제 Ubuntu Chrome에서 검토. 라이트/다크·3해상도·100~200% 확대·키보드 검증. UI '거래 가능' 표시는 P0 완료 전 금지.

## 5. 다음 기술 구현 단계 진입 조건

- 본 1단계는 **감사 및 팀 공유만** 수행. 소스코드/실데이터/PR #25 기존 변경 없음.
- 팀 피드백과 QA 증거 확인 후 2단계에서 우선 P0-A 중지 기본 상태 안전검사, P0-B NAV 검증, P0-C 단일 주문 게이트 순서 구현.
- QA의 잠재 결함 확인 전 상태를 실제 재현된 버그로 단정하지 않음.
- 배포 승인 보류, PAPER 자동거래 OFF, KIS 실연동/Windows 이식 제외, 기능 완성도 기존 53% 추정 유지.

**참조**: Draft PR #25 https://github.com/nahee2345/friendly-tribble/pull/25; CI https://github.com/nahee2345/friendly-tribble/actions/runs/40275214508; `docs/QA24_NYSE_CALENDAR_HANDOFF_20261026.md`; `docs/OFFLINE_PREKIS_100_ACCEPTANCE_20261026.md`.
