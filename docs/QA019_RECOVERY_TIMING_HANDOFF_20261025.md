# [기술팀 → QA팀·디자인팀·총괄팀] PR #19 후속 검증 인계

**작업 기준:** PR #19 `14feee3580c0ad0f0dedc7e14b9e9cb8bc420715`; PR #20 `2a7597fb26e563e0b7dc025c29ca4b65620627a2`를 부모로 하는 별도 Draft PR #21. 원본 PR #15·#16·#18·#19·#20 변경/병합 없음.
**공동 추적:** https://github.com/nahee2345/friendly-tribble/issues/17
**CI:** https://github.com/nahee2345/friendly-tribble/actions/runs/40183283074
**배포:** 차단 / USER DB와 기존 원장 미변경 / KIS·실거래·자동 PAPER 활성화 금지.

## QA-016-03 — 미확정 수동기록 새로고침·탭 종료 대조

**직접 코드 수정:**
- `wealthy_server/stage10_manual_assets.py`: `request_status()`가 유효한 불변 이벤트 ID를 소유자 한정으로 조회. 결과는 `recorded` / `not_found` / `conflict`이며 어느 경우도 `safe_to_resubmit: false`. 주식과 현금 기록 양쪽에 같은 이벤트 ID를 재사용하려는 조정은 트랜잭션 내 차단.
- `wealthy_server/app.py`: `GET /api/v1/stage1/manual-assets/request/{request_id}` 인증 필수 읽기 전용 API. 기존 수동 주식/현금 HTTP 모델을 모듈 범위로 이동해 `POST JSON`이 쿼리 `body`로 잘못 해석되는 버그 방지. 기존 인증/CSRF/owner 정책 유지.
- `web/frontend/src/ManualAssetsPanel.tsx`: 입력 전 opaque 요청 ID/종류/시각만 `localStorage`에 저장(종목·가격·금액·계좌·인증/토큰 저장 **금지**). 응답 불확실/다시 로드/탭 재접속 시 owner-scoped GET 조회. 확정 기록이 있으면 재POST 없이 저장 상태 복구, `not_found` 또는 오류면 신규 쓰기를 보류하고 상태 재확인. 결과가 확정되지 않았다는 이유만으로 신규 UUID/요청을 만들지 않음. localStorage 접근 실패도 fail-closed.
- **제약:** 브라우저 저장소가 지워지거나 다른 기기로 바뀌면 미확정 ID를 복구하지 못함. 동일 브라우저에서는 사용자의 관리 권한으로 접근하는 opaque ID이므로 공유 단말/공용 브라우저 환경의 계정 간 격리 UX는 실제 Chrome 독립 시험 필요. `not_found`는 늦은 서버 커밋 가능성이 있어 수동 재제출을 자동 허용하지 않음. 명시적 관리자 대조/복구 해제 절차 및 충돌 사용자 지원 플로우는 추가 설계 필요.

## QA-P0-UI-01 · QA-016-04

PR #20의 주문 JSON 입력 오류 수정과 격리 API 4 PASS를 포함하되, 원본 PR #18 클릭 UI의 독립 Chrome 검수 미실시. 승인 상태 확인과 실제 `Portfolios.trade` 원장 커밋이 다른 SQLite 트랜잭션이며 세션 시작 NAV·매수+매도+대기+예약·UNKNOWN 20% 원자성 **여전히 미구현**. 안전 검사를 비활성화하지 않았으며 자동 PAPER 활성화 금지.

## 이번 후보 기술팀 대상 시험

GitHub Actions `40183283074`:
- Python 격리 API·원장·요청상태·뉴스·승인/20% 사전계산: **56 PASS** (+ deprecation warning 1).
- React Vitest 수동주식/현금 응답 유실·동시 클릭·새로고침·탭 종료: **5 PASS**.
- TypeScript/Vite: **PASS**.
- QA 독립 TestClient/실제 Ubuntu Chrome: **NOT VERIFIED**.
- 운영 SQLite/사용자 정보, KIS 및 실제 주문을 시험에 사용하지 않음.

## Stage4~7 기존 실패 재현성 비교

실제 CI에서 baseline `dc92c6021f1bbd9c71b44c4accdcf0b02c64dd22`와 후보를 **같은 UTC 일시**로 테스트하였음. 정적 얼리기(freezegun)의 SQLite 사전 백업 이름 충돌을 피하기 위해 test별 1초씩의 고정 증가를 적용. 임시 pytest plugin만 사용하고 **운영 백업·감사 코드는 변경하지 않음**.

| 기준일 12:00 UTC | baseline | candidate |
|---|---|---|
| 2026-10-19 | 58 PASS / 25 FAIL | 58 PASS / 25 FAIL |
| 2026-10-25 | 59 PASS / 24 FAIL | 59 PASS / 24 FAIL |

- 10월 19일에만 추가 실패: `tests/test_stage5_accounting.py::test_dividend_without_pre_exdate_holdings_is_not_counted`; 원인은 배당 기준일과 보유 기준시각 해석을 추가 조사해야 하며 무조건 테스트 결함으로 판정 불가.
- 10월 25일 기존 24건 공통: Stage4 10, Stage5 5, Stage6 1, Stage7 8.
- Stage5 최소 4건은 초기 400만 원 기대와 월 25일 +35만 원 입금으로 435만 원 실제 값 불일치.
- Stage7 원장 `blocked`는 Stage4 계획 및 Stage6에 연쇄 전파. 상세 `INVALID_FUNDING_ALLOCATION` 등의 감사 원인 분리 필요. 실제 운용 안전감사 차단을 완화하는 수정 금지.
- **정리:** 날짜별 재현 원인은 일부 관측했으나 기존 24건의 전체 수정·결함별 독립 종결은 미완료.

## 독립 QA 재현 (사용자 운영 DB 사용 금지)

```bash
mkdir -p ~/wealthy-qa019-review
cd ~/wealthy-qa019-review
git clone https://github.com/nahee2345/friendly-tribble.git candidate
cd candidate
git fetch origin tech/qa019-recovery-timing-20261025
git checkout --detach origin/tech/qa019-recovery-timing-20261025
git rev-parse HEAD
python3 -m venv .venv-qa
.venv-qa/bin/python -m pip install -e '.[web]' pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q \
 tests/test_qa_p0_api_ledger.py tests/test_qa001_003_manual_scope.py \
 tests/test_qa016_news_freshness.py tests/test_qa002_last_good_news.py \
 tests/test_p0_ui_click_contract.py tests/test_stage1_controls_cards.py tests/test_p0_turnover.py
npm --prefix web/frontend ci
npm --prefix web/frontend run test -- src/ManualAssetsPanel.qa016.test.tsx
npm --prefix web/frontend run build
```

**브라우저 검수:** 격리 계정/DB로 정상 매수·거절·만료·긴급중지·저장 실패/UNKNOWN, 수동입력 후 응답유실/새로고침/탭 재접속, 잘못된 인증/401/403 비노출, 시장별 RSS 장애 메시지, 1366×768/1600×900/1920×1080, 라이트/다크/키보드. 브라우저 미실행은 `NOT VERIFIED`.

**다음 의사결정:** QA 독립 검증, P0 원자적 20% 처리, 남은 24건 차단 원인 종결, 패키지 `undici` high 패치 시험 후에만 총괄팀에 병합 검토 요청.
