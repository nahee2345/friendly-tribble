# 기술팀 인계 — QA-003, QA-001, QA-002 수정 후보

**공유:** 총괄팀 + QA팀 + 디자인팀
**상태:** Draft / 독립 QA 미승인 / 로컬 사용자 DB 미변경 / 배포 금지
**기준 브랜치:** `stage10-offline-hardening` @ `dc92c6021f1bbd9c71b44c4accdcf0b02c64dd22`
**부모 변경:** `tech/p0-trade-safety-20261025` @ `72b38a5ce401c55b89973639ede9a172596c041e`
**현재 후보 브랜치:** `tech/qa001-003-isolated-20261025` (정확한 HEAD는 PR #16 확인)
**Draft PR:** https://github.com/nahee2345/friendly-tribble/pull/16 (base: 기술팀 P0 브랜치)
**주의:** 원격 GitHub 조회는 Ubuntu PC 미커밋 변경을 증명하지 않습니다. 사용자 `~/program`을 reset/overwrite하지 마십시오.

## QA-003 — Scope and dashboard accounting

- `GET /api/v1/stage1/hub`: 기존 `performance`, `accounts` 유지, 새 `asset_scope` 필드 제공:
  `selected:PAPER`, `included_scopes:["PAPER"]`, `excluded_scopes:["USER_DECLARED_REAL","SAMPLE"]`,
  `included_accounts` 및 `value_field:"performance.portfolio_value_krw"`.
- 메인 대시보드의 단일 합계는 동일 응답 `hub.performance.portfolio_value_krw`만 사용하며,
  Stage5 회계 데이터와 합산하지 않습니다. 평가가 불완전하면 'PAPER 현금만' 또는 미평가로 표시.
- 직접 입력 보유/현금은 `USER_DECLARED_REAL`. 검증된 실제 총자산은 **null**이고
  PAPER NAV/SAMPLE과 합산하지 않습니다. 과거 `account=default/kr-base/isa-base` 별칭은
  직접 입력 구분에만 사용하며 실제 증권사 계좌 연결로 간주하지 않습니다.
- (owner, account, symbol, currency) 키는 기존 테이블에서 유일, 같은 계좌·동일 종목의
  다른 통화로 재등록하려는 새 입력은 차단; 기존 충돌 행은 삭제하지 않고 계산불가 표시.
  서로 다른 별칭 계좌에 동일 종목을 보유한 것이 실제 중복인지 아직 확인할 방법이 없어
  검증된 실자산 총합 계산을 허용하지 않습니다.
- API 인증 실패 시 최신 금융정보를 화면에 남기지 않도록 수동 보유 및 대시보드 캐시를 숨김.
- 동일 후보의 실제 API/Chrome 값 대조는 **NOT VERIFIED**.

## QA-001 — Lossless manual entry history and missing basis

- 기존 `stage10_manual_assets`·`stage10_manual_trade_history`의 레코드/스키마는 유지.
- `BUY`/`SELL`/`IN`/`OUT`/`CORRECT_ADD`/`CORRECT_REMOVE` 구분.
  과거 `ADD`/`REMOVE`는 보존하며 매수·매도로 소급 재분류하지 않음.
- 평균취득단가: 기록된 원가가 모두 유효한 경우에만 이동평균 원가 계산.
  취득단가 누락, 정정으로 인한 원가 불확실, 이력-잔고 불일치, 과거 혼합 통화 충돌은
  `cost_basis_status:unavailable` 및 `average_cost_native:null`로 반환.
- 부분매도: 알려진 매도가와 알려진 평균취득단가가 모두 있으면
  원 통화의 `realized_pnl_native` 산출; 그렇지 않으면 null.
  시세/환율을 검증하지 않아 `unrealized_pnl_native:null` 유지. 절대 0원이나 현재가로 원가 대체 금지.
- 새 선택적 `request_id`: (owner, event_id) 중복 제출이면 기존 결과 반환,
  내용이 다른 재사용은 차단. 실제 거래가 중복됐는지 확인하기 위한 외부 거래 ID 검증은 아직 없음.
- 현금성 수동 입출금 및 정정은 독립 `stage10_manual_balance_history` 이력에 기록.
  이 테이블은 `CREATE TABLE IF NOT EXISTS` 방식으로 **새롭게 추가**되며
  기존 잔액이나 PAPER 현금 및 가상주문은 변경하지 않음.
- 기존 잔액에 이력이 부족하면 과거 원가/실현손익을 소급 생성하지 않음.

## QA-002 — Last-good news behavior

- `GET /api/v1/stage1/news-30d`는 기존 뉴스 배열 유지.
  `cache_status`(`fresh`/`partial`/`stale_cache`/`unavailable`),
  `last_success_by_market`, `last_attempt_utc`, `unavailable` 추가.
- 사용자별 SQLite `stage10_news_last_good` 및 `stage10_news_fetch_state`에
  유효 HTTPS 허용 도메인·발행시각·원문만 보존. URL/중복/만료 형식 검증.
- 공급자 전체 장애 시 캐시 파일 행은 덮어쓰지 않음.
  최근 30일 범위에 해당하는 마지막 정상 뉴스는 `stale_cache` 경고와 함께 제공.
  기준일을 벗어난 오래된 기사도 DB에서 임의 삭제하지 않으나 최근 뉴스 UI에는 표시하지 않음.
- 일부 공급원 장애는 성공 데이터만 새로 저장하고 나머지 시장의 기존 캐시 유지.
- 번역 실패 시 영문 원제목 제공; 기사 제목은 매수/매도 신호가 아님.
- 오래된 캐시 뉴스는 새 위험 알림으로 보내지 않음.

## 테스트 / 차단 (기술팀만 수행)

- GitHub Actions `Wealthy QA001-003 Candidate Verification`의 `targeted`에서
  `tests/test_qa001_003_manual_scope.py`, `test_qa002_last_good_news.py` 및
  Stage10 수동 자산·대시보드·뉴스 기존 테스트, P0 승인·20% 계산 테스트.
- 프런트엔드 TypeScript 컴파일 및 Vite build.
- 별도 SQLite 격리에서 Stage1 backup, schema migration, 웹 암호화 backup/restore,
  Stage1 ledger restart/idempotency 테스트 (고정 날짜 미주입 테스트는 별도 기록).
- 전체 기존 Stage7/Stage10 수용시험은 2026-10-25 시점 **24건 FAIL** (테스트 기준일/정기입금 시점 충돌 포함).
  Stage1 exchange 레거시 별도 테스트도 정기입금 350,000원과 고정 기대금액 충돌. 절대 운영
  날짜/자금 정책을 바꾸거나 감사를 무력화해 PASS 처리하지 않을 것.
- 기존 거래 20% 제한은 순수 계산 모듈만 준비됨; 실제 요청·예약·커밋 원자 처리
  및 후보 승인 정책 전체는 BLOCKED. KIS/API/실제 주문 미연결.

## 격리 QA 실행 절차 (운영 폴더 및 DB와 별개)

```bash
mkdir -p ~/wealthy-qa-isolated
cd ~/wealthy-qa-isolated
git clone https://github.com/nahee2345/friendly-tribble.git candidate
cd candidate
git fetch origin tech/qa001-003-isolated-20261025
git checkout --detach origin/tech/qa001-003-isolated-20261025
git rev-parse HEAD
python3 -m venv .venv-qa
.venv-qa/bin/python -m pip install -e '.[web]' pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q \
  tests/test_qa001_003_manual_scope.py tests/test_qa002_last_good_news.py \
  tests/test_stage10_news_manual_assets.py tests/test_stage10_dashboard_fallback.py \
  tests/test_stage10_news_evidence.py tests/test_stage1_controls_cards.py tests/test_p0_turnover.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q \
  tests/test_stage1_backup.py tests/test_stage1_schema_migration.py \
  tests/test_web_local_backup.py \
  tests/test_stage1_paper.py::test_funding_restart_no_duplicate \
  tests/test_stage1_paper.py::test_account_eligibility
npm --prefix web/frontend ci
npm --prefix web/frontend run build
```

명령 실행 전 `~/wealthy-qa-isolated` 내부에 기존 사용자 데이터가 없는지 확인할 것.
`~/program`, `~/.local/share/wealthy` 및 시장정보 SQLite는 테스트 대상으로 사용하지 마십시오.
실제 사용자 DB를 QA 후보 커밋으로 바로 실행하지 마십시오.

### Ubuntu Chrome 인증·화면 확인 (독립 QA 미실시)

운영 경로가 아닌 새 데이터 폴더와 별도 임시 시장 DB를 마련하고, 안내된 공식
로컬 관리자 생성·Secret Service 절차로 테스트 계정만 사용하십시오.
`http://localhost:8000` 접속 후 로그인/설정 > 자산 및 현금 관리에서 다음 확인:
1. PAPER 시작 4,000,000원은 PAPER 영역에만 표시되는지.
2. 수동 실보유 AAPL 및 수동 예금 금액을 추가해도 PAPER 합계가 그대로인지.
3. TEST SAMPLE 값도 PAPER 또는 실보유에 합산되지 않는지.
4. 같은 요청 ID 재전송시 이력이 한 건만 늘어나는지, 종목 중복 통화 입력이 차단되는지.
5. 원가 미입력은 '계산 불가'; 매수 10@100 + 10@120, 매도 5@130은 잔량 15,
   평균 110, 원 통화 실현손익 100인지.
6. RSS 전체 실패 후 과거 캐시와 마지막 성공시각을 표시하며 재전송/번역 실패 때 원제목을 보존하는지.
7. 로그아웃/401·403 실패 시 금융 카드·뉴스·자산 요약·알림이 비노출되는지.
8. 라이트·다크, Chrome 100%/125%/200%, 키보드 탭 이동, 기존 카드/표 구조 보존.
9. 수정 전후 같은 해상도·자료·인증 상태 스크린샷 증거 확보.
이 검증은 기술팀 GitHub Actions에서 실제 Chrome으로 수행되지 않아 **NOT VERIFIED**.

## 안전한 이행·복원 정책

- 현재 기술팀은 사용자 DB를 열거나 변경하지 않았고, GitHub의 코드·CI만 수정.
- 이행은 기존 원장·관리자 보안정보를 보존한 상태에서 권한·WAL·SHM·동시 접근을 검사,
  공식 온라인 SQLite backup/암호화 backup으로 **별도 격리 복제본**을 생성해 사전 검증.
- 격리 복제본에서만 신규 테이블 생성 후 `PRAGMA integrity_check`,
  기존 사용자별 원장·입금·체결·수동기록 row count/해시 및 재시작 비교.
- 실패하면 격리 복제본을 폐기하고 기존 운영 DB/원장을 그대로 둠. 사용자 운영 DB 직접 덮어쓰기·reset·schema downgrade 금지.
- 운영 배포/복원 시점은 QA 독립 검증과 총괄팀 승인 후 별도 절차로 결정.

## 변경 소유권

기술팀: `wealthy_server/stage1_hub.py`, `wealthy_server/stage10_manual_assets.py`,
`wealthy_server/manual_cost_basis.py`, `wealthy_server/stage10_news_cache.py`,
`wealthy_server/app.py`, `web/frontend/src/Stage1Dashboard.tsx`,
`web/frontend/src/ManualAssetsPanel.tsx`, 신규 QA tests/CI, 이 문서.
디자인팀: CSS/토큰 및 시각검수. QA팀: 독립 시험·브라우저 증거.
공용 TSX/APP 파일은 기술팀 변경이 완료될 때까지 동시 수정 금지; 충돌 시 총괄팀 인계.
