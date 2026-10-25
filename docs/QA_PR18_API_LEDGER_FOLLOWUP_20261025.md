# [기술팀 → QA팀·디자인팀·총괄팀] PR #18 API/원장 후속 독립 재검증

**기준:** PR #18 `a87f394011ee07e4a59a38ece139777e7a5c0615` (원본 미수정)
**부모:** PR #19 `14feee3580c0ad0f0dedc7e14b9e9cb8bc420715`
**새 검증 브랜치:** `tech/qa-p0-api-ledger-regression-20261025`
**검증 PR:** #20 (Draft / 절대 병합·운영 적용 금지)
**CI:** https://github.com/nahee2345/friendly-tribble/actions/runs/40181096879
**판정:** P0 UI 수정에 대한 격리 API 시험 일부 PASS / 원자적 20% 규칙 미구현 / 기존 전체 CI FAIL.

## 1. 실제 코드에서 새로 발견된 API 결함과 수정

- `wealthy_server/app.py`의 `VirtualOrderBody` 및 `PaperApprovalDecision`이 `create_app` 함수 내부에 선언되고 지연 어노테이션과 결합되어 `POST /api/v1/stage1/order`의 JSON 본문이 쿼리 변수 `body`로 해석됨.
- 현상: 정상 요청도 `422 INVALID_INPUT`. 격리 시험의 오류 위치 `["query","body"]`, 종류 `missing`으로 확인.
- 새 후보에서 해당 Pydantic 모델만 모듈 범위로 이동. 요청 검사/CSRF/로그인 정책, 금액 및 승인 게이트의 기존 조건을 완화하지 않음.
- 원본 PR #18/#19 및 사용자 PC는 변경하지 않았으며 이 수정은 QA 후보에만 존재.

## 2. 새 격리 HTTP API 및 SQLite 시험

새 파일 `tests/test_qa_p0_api_ledger.py`:
1. 가짜 OS keyring·격리 시장 DB·웹 DB 생성, 실제 사용자 로그인과 CSRF 포함 HTTP POST.
2. 정상 승인 신청 → APPROVED 전까지 `virtual_orders=0` → 승인 후 POST → 주문 ID/종목/방향/수량 SQLite 저장 1건 확인.
3. 동일 request_id 재전송은 이미 실행된 응답, 저장 1건 유지.
4. REJECTED·EXPIRED 거래는 체결 0건.
5. EMERGENCY PAUSE는 409로 차단, 서버 재시작 후 기록이 그대로 유지.
6. 동일 request_id 병렬 DB 요청 3건에서도 주문 행 1건.

**기술팀 CI 실측:**
- HTTP API/SQLite 4 PASS
- 기존 P0 게이트+회전율 순수계산+UI 정적회귀 33 PASS
- TypeScript/Vite 빌드 PASS
- 기존 Stage7/10 수용시험 85 PASS / 24 FAIL.
- 전체 워크플로 FAIL; QA 독립 DB 수행 및 실제 Ubuntu Chrome은 NOT VERIFIED.

위 결과는 승인 실행 **경로가 동작하는지**만 검증함. 20% 전체 안전 정책을 만족한다는 의미가 **아님**.

## 3. 미종결 P0 — merge blocker

`stage1_order`는 `list_approvals`/ `approval_execution_gate` 사전 확인 후 독립적인 `Portfolios.trade` 트랜잭션으로 원장을 기록한다. 다음은 같은 트랜잭션 안에서 확인되지 않는다:
- 미국 거래소 세션 식별과 시작 시점의 **검증된 PAPER NAV**
- 매수·매도 체결과 대기·예약·UNKNOWN 금액의 20% 통합 집계
- 30만원 승인 금액·환율·시세·수량·전략 버전과 실제 체결의 동시 대조
- 이미 차감된 예약금/동시 요청/다른 계좌의 겹친 주문/재시작 이후 UNKNOWN
- 기존 Stage4 5% 제한 및 사용자 긴급 정지와의 계층적 호환

따라서 **PR #20 병합/배포 금지. 자동 PAPER 매매 활성화 금지.**
모든 P0 종단간 시험에서 검증 전 fail-closed 설계를 완료한 후에만 사용자 테스트 환경 후보로 승격할 것.
UNKNOWN 복구, 전원/프로세스 강제종료, 다중 세션 연속 실행은 NOT VERIFIED.

## 4. npm HIGH 조사

`npm ls undici jsdom` 결과 `jsdom@30.1.2 → undici@8.11.2`.
`npm audit --omit=dev`: 0 vulnerabilities.
`npm audit`: HIGH 1 (`undici 8.0.0–8.12.1`, 고갈/무결성 검증 관련 권고; 자동 수정 가능).
상용 번들 무영향을 단정하지 않는다. `npm audit fix --force` 사용 금지.
격리 의존성 갱신 후 `npm ci`, `npm audit`, TS/Vite, 테스트 및 lockfile diff 검수 필요.

## 5. 기존 실패 24건

기존 Stage7/10 동일 후보 워크플로에서 **24 FAIL / 85 PASS** 재현.
QA 보고 분류 Stage4 10, Stage5 5, Stage6 1, Stage7 8.
`2026-10-19` 고정 fixture vs `2026-10-25` 런타임 시각,
매월 25일 350,000원 가상입금, NAV/원장 무결성 및 기존 예약 검증을
동일 시계·DB로 **기준 및 수정 후보 각각** 비교하는 추가 검증은 미완료.
안전 검사를 완화하거나 실패한 항목을 CI에서 빼지 않음.

## 6. 독립 QA 절차(운영 PC 자료 사용 금지)

```bash
mkdir -p ~/wealthy-qa-pr18-independent
cd ~/wealthy-qa-pr18-independent
git clone https://github.com/nahee2345/friendly-tribble.git candidate
cd candidate
git fetch origin tech/qa-p0-api-ledger-regression-20261025
git checkout --detach origin/tech/qa-p0-api-ledger-regression-20261025
git rev-parse HEAD
python3 -m venv .venv-qa
.venv-qa/bin/python -m pip install -e '.[web]' pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q \
  tests/test_qa_p0_api_ledger.py \
  tests/test_p0_turnover.py tests/test_stage1_controls_cards.py \
  tests/test_p0_ui_click_contract.py
npm --prefix web/frontend ci
npm --prefix web/frontend run build
```

`~/program`, 기존 실사용 `~/.local/share/wealthy`, `snap/code/.../market-data.sqlite3`에 연결 금지.
위 테스트는 OS 브라우저 검증이 아니라 TestClient 기반 API 통합시험.

## 7. 디자인팀과 QA팀 분담

- QA: 신규 SHA의 로그인+CSRF, 승인 요청·완료·만료·중지 및 SQLite 원장 확인, 대기·UNKNOWN·동시 실행, 다른 사용자, 백업·복원, 고정 시간 회귀 비교.
- 디자인: Chrome 정상 승인/차단 버튼, 승인 완료 vs PAPER 가상체결, 결과 미확인 안내, 1366×768/1600×900/1920×1080, 라이트·다크, 키보드, 인증 차단 시 카드·알림 비노출. 브라우저 미실행은 NOT VERIFIED.
- 총괄팀: 부재. Draft PR #15/#16/#18/#19/#20 모두 병합/배포 금지.

**공유 대상:** QA팀 + 디자인팀 + 기술팀 + 총괄팀.
