# [기술팀 → QA팀·디자인팀·총괄팀] 20% PAPER 세션 예약원장 후속 검증 보고

공유 대상: QA팀 + 디자인팀 + 총괄팀(부재)
기존 검증 부모: PR #22, f0ec53c282a2736c4a2fef468b69f5d179e7a053
신규 작업 브랜치: tech/pre-kis-p0-reservation-20261026
목표: Ubuntu 로컬 웹, Windows 이식 및 한국투자증권 API 도입 이전 기능 100%.
최종 판정: **기술팀 격리 대상 PASS / P0 전체 BLOCKED / 병합 및 배포 금지**

## 1. 수정한 파일과 기능

- wealthy_server/stage1_turnover_reservations.py (신규): 사용자·미국 세션별로 시작 NAV, 검증 출처, 세션 시작/종료시각을 불변 저장하고, 동일 SQLite BEGIN IMMEDIATE에서 매수+매도·모든 계좌·예약·UNKNOWN·FILLED 금액을 합산하여 20%를 넘지 않도록 검사. 제안 주문의 요청 ID와 기존 불변 내용이 다르면 거부.
- 새 테이블: stage1_turnover_sessions 및 stage1_turnover_claims. 기존 계좌·거래 원장 데이터를 삭제하거나 다시 쓰지 않는 추가형 스키마. 사용자 운영 DB에는 적용하지 않음.
- UNKNOWN 기록은 중단·재시작 뒤에도 계속 한도를 사용하며, 자동 만료·해제 금지. CANCELED/EXPIRED/REJECTED는 거래 미체결을 확인한 경우에만 해제. 예약 없이 기존 거래가 있거나 Stage4 실행 미확정이 남으면 보수적으로 차단.
- wealthy_server/stage1_portfolios.py: verified_turnover_session_date를 별도로 전달한 **선택적 거래 경로만** 단일 SQLite 트랜잭션에서 reserve → virtual_orders insert → mark_filled 수행. 계좌 잔액·종목 제한·가격/FX·기존 승인/만료 정책 유지. 검증된 세션이 없으면 신규 거래를 거부.
- tests/test_p0_session_reservations.py: 4백만원 세션 NAV 기준 매수+매도 80만원 상한, 초과 1원, 여러 계좌, 재시작, UNKNOWN, 요청 ID 중복·분할, 취소 후 재사용, 유효 세션 밖, 기존 체결·Stage4 claim 미대조, 사용자 간 격리, 동시 스레드 및 다른 OS 프로세스에 걸친 SQLite 잠금, 선택적 실제 거래 원장 커밋을 검증.
- .github/workflows/tech-p0-reservation-isolated-20261026.yml: 격리 API/P0·React·빌드/백업 및 기존 Stage7/10 실패를 분리 기록.

## 2. 기술팀 CI 근거

Actions: https://github.com/nahee2345/friendly-tribble/actions/runs/40271786739

- 격리 Python 및 P0 통합/단위: **76 PASS** (경고 1)
- React/Vitest: **8 PASS**
- TypeScript/Vite: **PASS**
- 격리 백업 및 재시작: **19 PASS + 하위시험 3 PASS**
- 원본 Stage7/10 전체 수용: **85 PASS / 24 FAIL** → 전체 CI FAIL
- 독립 QA의 자체 DB 시험 및 Ubuntu Chrome: **NOT VERIFIED**

## 3. 절대 완성으로 간주하지 말 것

- 일반 POST /api/v1/stage1/order, Stage4 자동거래, USD 가상환전+매수 경로가 신규 통합 거래한도 원장을 아직 호출하지 않음. 선택적 내부 Portfolios.trade 경로만 검증했으므로 자동 PAPER 거래 활성화 금지.
- 시작 NAV의 공급원·휴장일/서머타임·미국 정규장 세션과 실제 장중 기준시각을 독립 검증하는 파이프라인 없음. nav_source 문자열 입력만으로 실제 검증됨을 뜻하지 않음.
- Stage4 기존 5%/집중도·8개 전략·30만원 승인·24시간 만료 등 보호정책을 바꾸거나 대체하지 않았으며, 모든 자동 주문과 UNKNOWN 복구의 종단간 원자성은 미구현.
- 기존 CI 24건은 과거 fixture 시점과 현재 실행시점 불일치와 관련 있으며, 2026-10-19 10:00 UTC에 정확히 고정하면 기존 후보에서 83 PASS한 적 있음. 과거 fixture PASS를 현재 시장 안전성 PASS로 처리하지 말 것.
- 실제 공급원 권한, 뉴스/번역·보고서/8개 전략의 완전한 평가, npm 개발 의존 undici High 취약점 패치, Chrome 디자인·접근성 최종 검증은 남음.

## 4. QA팀 독립 재검증 요청

같은 최신 후보 SHA를 별도 격리 DB에서 실행:
- 사용자별 NAV와 세션별 원장, 300000원 승인 경계, 400만원 기준 매수+매도 800000원, 초과 및 계좌별 분할, 요청 재사용 방지.
- PENDING→RESERVED→UNKNOWN→재시작, 원장 불일치·미체결의 안전한 해제, 기존 Stage4 미확정 상태, 다중 프로세스 동시 거래, 휴장/종가·검증시각 불확실.
- 동일 트랜잭션에서 예약 + virtual_orders + FILLED 상태 일치, 중간 예외 발생 시 원장 전부 롤백.
- 아직 연결되지 않은 일반 API/Stage4/FX 경로에서 신규 20% 한도 우회를 재현해 OPEN/BLOCKED로 명시.
- 기존 QA-001~003·QA-016 API↔화면·백업·소유자 분리·재시작 검증. 실사용 DB를 사용하지 말 것.

격리 시험 명령:
    git clone https://github.com/nahee2345/friendly-tribble.git wealthy-qa-p0-turnover
    cd wealthy-qa-p0-turnover
    git fetch origin tech/pre-kis-p0-reservation-20261026
    git checkout --detach origin/tech/pre-kis-p0-reservation-20261026
    python3 -m venv .venv-qa
    .venv-qa/bin/python -m pip install -e '.[web]' pytest
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q tests/test_p0_session_reservations.py tests/test_p0_turnover.py tests/test_qa_p0_api_ledger.py

## 5. 디자인팀 확인 요청

이번 PR은 기존 UI/CSS 수정 없음. 같은 후보에서 PR #22의 화면 안내를 실제 인증 Ubuntu Chrome에서 확인:
- 20% 한도 또는 NAV 미검증이 발생해도 가상체결 성공이나 800000원 가능액을 임의 표시하지 않음.
- 장 미확인, NAV 근거 없음, 금액 초과, 기존 Stage4 미대조 거래, 결과 UNKNOWN별 한국어 상태 및 재시도/긴급중지 안내 설계.
- 자산 범위 PAPER/직접 실보유/SAMPLE 비혼합, 승인 전·후·미체결 구분, 뉴스 fresh/cached/stale/장애, 금융정보 세션 종료 후 비노출.
- 라이트·다크, 1366×768 / 1600×900 / 1920×1080, 100~200% 확대, Tab/Enter/Space/Escape 및 수정 전후 동일조건 스크린샷.
- 실제 Chrome을 실행하지 않았다면 NOT VERIFIED. 화면 시각 수정은 디자인팀 CSS·토큰 담당, 데이터·로직 TSX는 기술팀 담당. 공용 파일은 충돌 조율.

## 6. 이어서 기술팀이 해야 할 작업

1. 검증 가능한 NYSE/US 시장 세션 및 변하지 않는 세션 시작 PAPER NAV 증거를 제공. 장중 시세가 누락되면 거래 보류.
2. manual, Stage4, USD FX+BUY **세 주문 경로 모두** 같은 SQLite 예약-체결/UNKNOWN 거래 계약에 연동. 기존 Stage4 5% 별도 제한과 14일 전략 제한 등은 유지.
3. 승인·예약금·실제 저장·UNKNOWN 중복/강제종료/재시작 자동 복구 종단간 회귀.
4. API·화면·원장 완전 일치, 뉴스·전략·7카드 보고서·재실행 복구, undici High 패치와 QA/디자인 독립 Chrome 검수.
5. 모든 핵심 PASS 후 총괄팀 별도 배포 판단. Draft PR 유지, 실주문/KIS/윈도우 이식/운영 DB 적용 금지.

진행률: **약 51% (이전 50% 대비 +1%p, 기술팀 검증 기반 추정)**. 단독 내부 거래경로와 세션 예약 원장은 구현했으나 전체 20% 자동·수동 보호가 아닌 이유로 P0는 OPEN 유지.
