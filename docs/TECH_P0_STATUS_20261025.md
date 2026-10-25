# 기술팀 P0 구현 상태 및 검증 인수인계 — 2026-10-25

**공유 대상:** 총괄팀 · QA팀 · 디자인팀
**코드 기준:** `stage10-offline-hardening` @ `dc92c6021f1bbd9c71b44c4accdcf0b02c64dd22`
**격리 작업 브랜치:** `tech/p0-trade-safety-20261025`
**검토 PR:** https://github.com/nahee2345/friendly-tribble/pull/15 (Draft; 미배포)
**미커밋 로컬 파일:** GitHub 원격 커넥터로는 조회 불가. Ubuntu 작업 디렉터리의 `git status --porcelain`은 별도 확인 필요.

## 1. 구현 상태 분류

| 범주 | 현재 확인한 코드 | 상태/남은 차이 |
|---|---|---|
| PAPER 자산/입금 원장 | `stage1_paper.py`, `stage1_portfolios.py`, `stage1_bootstrap.py` | 구현 존재, 현재 계좌별 정합성 시험 필요 |
| 수동 자산 | `stage10_manual_assets.py` | Stage10 Draft 구현, owner 격리/화면 확인 필요 |
| 승인·중지 | `stage1_controls.py`, `app.py` | 승인 24시간 및 중지 코드 존재; 가격 하락 시 거절·만료 우회 결함을 이 PR에서 수정 |
| 승인-체결 식별 정합성 | `stage1_controls.py` | 계좌·종목·매수매도·수량 불일치 대조 표시 및 UI 차단 구현 |
| 일일 회전율 | `stage4_portfolio.py` | 기존 Stage4는 신규매수 5%, 최대 50만원; 승인된 매수+매도 20%와 불일치, **운영 P0 차단** |
| 예약·동시성 | `stage4_execution_claims` | Stage4 별도 클레임 존재; 승인/예약/동시성의 통합 20% 원자적 검증 미완료 |
| 후보 1~4순위 및 재생성 승인 | 복수 후보/정량 평가 코드 존재 | 확정 정책 그대로 실행되는 운영 경로 검증되지 않음 |
| 8개 전략 | `stage1_strategy_policy.py`, `stage1_quantitative.py`, `asset_assistant/strategy.py` | 전략 평가 코드 존재, 완성된 매수/매도 실거래일 신호 검증되지 않음 |
| 계좌·배당·세금 | `stage5_accounting.py`, `stage5_tax.py` | 시뮬레이션·검증 필요, 실제 자산과 PAPER 분리 확인 필요 |
| 뉴스·번역 | Stage10 backend/UI modules | Draft 구현, 외부 자료/법적 이용권/번역 품질 미검증 |
| 7개 일일 보고 카드 | `stage1_daily_cards.py` | PREVIEW/스냅샷 코드와 테스트 존재, 실사용 데이터 전체 검증 필요 |
| 알림 | `stage1_inbox.py` 및 프런트엔드 | 코드 존재, 중복/실패/읽음/보관 정책과 QA 검증 필요 |
| 복구·데이터 안전 | `stage7_release.py`, `stage1_backup.py` | 감사 및 백업 코드 존재; 운영 DB는 조회/수정하지 않았음 |
| PC 종료·Windows | `stage1_autorun.py`, 문서 | PC 중단 시 소급거래 금지 검증 필요, Windows NOT VERIFIED |

## 2. 이 PR에 포함된 실제 변경

1. `list_approvals`: 같은 요청 ID의 거래가 다른 종목/방향/수량일 때 `executed=true`라고 표시하던 문제를 수정. `needs_reconciliation=true`를 제공.
2. `approval_execution_gate`: 기존 승인 요청의 거절/만료/대기/충돌을 신규 소액거래로 우회할 수 없도록 사전 검증.
3. `stage1_order`: API에서 위 검사를 사용, 올림 원화 금액으로 새 승인 요청. 주문 자체는 기존 가상 원장만 사용.
4. `Stage1Page.tsx`: 원장 불일치 경고 및 실행 버튼 비활성화.
5. `test_stage1_controls_cards.py`: 승인/체결 불일치, 299999·300000원 경계, 거절/만료, 상태별 검증 추가.
6. 격리 CI 워크플로: Stage10 회귀를 포함하고 Stage4 오래된 고정시각 테스트는 별도 차단 항목으로 유지.

## 3. 잠정 테스트 결과 및 위험

- 최초 GitHub Actions에서 **25 통과·10 실패**. Stage4 fixture의 정적 `NOW=2026-10-19`보다 현재 생성한 테스트 원장 시각이 미래가 되어 읽기전용 Stage7 감사가 `INVALID_FUNDING_ALLOCATION`을 포함한 무결성 차단을 반환.
- 이는 위험 차단을 해제할 이유가 아니라 **테스트 시계의 재현성/기존 테스트 적합성 조사 과제**. 테스트 기대값·감사 검사를 변경하지 말 것.
- 이후 워크플로는 새 승인 테스트, Stage10 수용시험, Stage4 검증을 별도 단계로 보고하도록 분리했다. 각 GitHub Actions 실행 결과 확인 필요.
- 원자적 `trade` 원장 커밋 순간에 승인 버전·만료·예약 한도가 **같은 트랜잭션**으로 검사되는지 아직 검증하지 못함. P0 완성 판정 금지.
- Stage4 5%를 전체 20%로 검색·치환하는 것 금지. 사용자 확정 정책을 반영한 통합 거래 세션·잔액·예약 원장을 먼저 설계해야 함.
- `stage1_portfolios.Portfolios`의 원장 직접 호출 경로 및 가상 환전 비용, 통화별 거래 한도 동시성 검증 추가 필요.

## 4. QA 재현 및 디자인 확인

**격리된 시험 저장소에서만 실행.** 실제 사용자 SQLite/인증정보/배포 경로와 분리.

```bash
git fetch origin tech/p0-trade-safety-20261025
git switch --detach origin/tech/p0-trade-safety-20261025
python3 -m venv .venv-qa
.venv-qa/bin/python -m pip install -e '.[web]' pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q tests/test_stage1_controls_cards.py
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q tests/test_stage4_portfolio.py
npm --prefix web/frontend ci
npm --prefix web/frontend run build
bash scripts/stage10_acceptance.sh
```

- 승인요청 ID 충돌 시 완료 아닌 원장 대조 안내 확인
- 이미 거절·만료한 거래는 현재 예상금액 30만원 미만으로 내려가도 승인 우회 금지
- 30만원 이상 계산 시 소수 원화 올림 적용
- 승인·대조 화면 한국어 안내, 중지 상태, 키보드 접근성 확인
- React 타입검사/빌드, 인증된 Chrome 상세 경로 및 사용자 DB 보존은 별도 QA/디자인 확인 대상

## 5. 외부 공급원 / 비용 / 운영 미승인

- SEC EDGAR: 인증키 없이 XBRL/공시 API 공개. Fair Access 10 req/s 상한 등 적용. https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- Alpha Vantage: 기본 무료 API 대다수 25 요청/일, 미국 실시간·15분 지연 시세는 별도 유료 entitlement. https://www.alphavantage.co/support/
- 국가/ETF별 시세·구성자료·환율·뉴스 제공 및 저장·번역·화면 표시 이용권은 개별 검증 필요.
- 외부 API 실연결, 실전 KIS 주문, 유료 서비스, 자동 코드 수정, 사용자 DB 삭제 및 운영 배포 없음.

## 6. 다음 순서 (총괄팀 승인 범위)

1. 기존 승인 요청·종목·시가·수량·24시간·비용을 원장 커밋에서 **원자적으로** 검사.
2. 미국 거래 세션 기준 시작 NAV의 20%를 매수+매도+예약에 적용. 누락 가격/FX fail-closed.
3. 승인 가격대/버전/후보 슬레이트 재생성(하루 최대 2회)/14일 전략 적용 제한의 데이터 계약 검증.
4. 분리 원장·백업·재시작·클레임 동시성 테스트가 통과한 다음 P1 API/전략 개발 착수.
5. QA 독립 승인 및 총괄팀 병합·배포 승인 전 자동 PAPER 거래 활성화 금지.

## 7. 변경 파일 소유권

기술팀 작업: `wealthy_server/stage1_controls.py`, `wealthy_server/app.py`, `tests/test_stage1_controls_cards.py`, `web/frontend/src/Stage1Page.tsx`, `.github/workflows/tech-p0-approval-check.yml`, 이 보고서.

동시작업자는 해당 파일에 별도 수정을 시작하기 전에 총괄팀을 통해 조정 바람. QA는 코드를 직접 덮어쓰기보다 PR #15에 재현 결과를 회신하고, 디자인팀은 경고 문구·버튼 비활성화 상태를 검토.

**현재 판정:** P0 일부 수정 제출, 전체 P0 미완료, 자동매매 활성화 및 배포 미승인.
