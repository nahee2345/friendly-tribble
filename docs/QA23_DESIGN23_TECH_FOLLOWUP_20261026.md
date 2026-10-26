# [기술팀 → QA팀·디자인팀·총괄팀] PR #23 최종 보고 대응 — 안전성·화면 표시 2차 보완

**발신:** 기술팀  
**수신:** QA팀 + 디자인팀  
**공유:** 총괄팀(부재) + QA팀 + 디자인팀 + 기술팀  
**범위:** Ubuntu 로컬 웹의 Windows 이식·한국투자증권 API 이전 기능 100% 준비  
**원본 검토 SHA:** PR #23 `0fe90ff5029620ceb762508691332f5baab9361f`  
**작업 브랜치:** `tech/qa23-design-lock-hardening-20261026`  
**배포 승인:** **보류** / 모의 자동거래 활성화 **금지** / 운영 DB 미접근

## 1. QA 및 디자인팀 최종 보고 확인

- QA: https://github.com/nahee2345/friendly-tribble/issues/17#issuecomment-6408684934  
  기존 P0 `_locked(db)`는 `in_transaction`만 확인하여 **BEGIN DEFERRED**에서도 통과함. 실제 SQLite 직렬화 증거 필요. 전체 일반 HTTP 주문/Stage4 자동/USD FX+매수는 새 예약원장 미연결이므로 P0 BLOCKED. 실제 NYSE 정규장·휴장·DST·개장 NAV 검증기 미구현.
- 디자인: https://github.com/nahee2345/friendly-tribble/issues/17#issuecomment-6408681985  
  `Stage1Page.tsx`의 `paperControl=null` 상태에서 상단을 '가상 자동주문 허용'으로 표시하는 **미확인 상태 오안내**. PAPER 거래 중지/허용/조회 실패의 세 상태를 분리 요청. Chrome·키보드·다크/라이트 독립 시각 검증은 NOT VERIFIED.

## 2. 이번 구현 변경 및 효과

| 변경 파일 | 구현 범위 | 안전 계약 |
|---|---|---|
| `wealthy_server/stage1_turnover_reservations.py` | _locked 안에서 무해한 SQLite UPDATE 0행으로 **실제 writer lock을 강제로 획득** (BEGIN DEFERRED 상태여도 write lock으로 승격) | 동일 연결의 SELECT/claim 사이 외부 writer 개입 불가, 경합 시 ValueError로 fail-closed |
| 같은 파일 | 요청 ID 기존 상태 UNKNOWN인 경우 재예약/체결 자동 재사용 거부 | `UNKNOWN_RESERVATION_NEEDS_RECONCILIATION` |
| `wealthy_server/stage1_portfolios.py` | 새 세션 예약 경로에서 `already_reserved`면 **체결 실행 금지** | `RESERVATION_RECONCILIATION_REQUIRED`; 기존 미확정 요청의 중복 체결 차단 |
| `web/frontend/src/Stage1Page.tsx` | 중지/허용/미확인 3상태로 출력; 미확인은 거래 실행 보류, **긴급 중지 요청만 허용** | 확인되지 않은 중지 상태를 허용으로 오안내하지 않음 |
| `web/frontend/src/paperControlDisplay.ts` 신규 | 명시적 3상태 UI 문구 계약과 중지 액션 | null→거래 실행 보류 |
| `tests/test_p0_session_reservations.py` | BEGIN DEFERRED 실쓰기 잠금 경쟁, UNKNOWN 및 이전 RESERVED 요청 실행 차단 시험 3건 | 임시 격리 SQLite로만 검증 |
| `web/frontend/src/paperControlDisplay.test.ts` 신규 | null / true / false 상태·허용/중지 액션 Vitest 3건 | 조회 실패를 활성으로 보이지 않음 |
| `.github/workflows/tech-qa23-lock-and-design.yml` 신규 | 같은 SHA에서 대상 Python/Vitest/TS/Vite/백업 및 기존 수용시험 그대로 실행 | FAIL 미숨김 |

**중요:** 이 수정은 `BEGIN IMMEDIATE` 문자 자체를 introspection으로 인증하는 것이 아니라, **실제 SQLite RESERVED writer lock 취득을 DML로 강제**해 동일한 직렬화 효과를 제공한다. 이미 쓰기 트랜잭션을 보유하면 no-op. 운영 시스템에서 실제 사용 여부는 아직 별도 QA가 확인해야 한다.

## 3. GitHub Actions 자체검증

**코드 시험 SHA:** `35983c34fbc8bd42daab693fac4111c930cc4262`  
**링크:** https://github.com/nahee2345/friendly-tribble/actions/runs/40273065703

- 격리 Python(P0 세션 예약·원장·기존 API·한도·승인) **63 PASS / 경고 1**
- React/Vitest(기존 수동자산 8 + 중지 3) **11 PASS**
- TypeScript·Vite 빌드 **PASS**
- 격리 백업·재시작 **19 PASS + 하위시험 3 PASS**
- 변경하지 않은 원래 Stage7/10 수용시험 **85 PASS / 24 FAIL**
- **전체 Actions FAIL**(역사적 기준시각과 현재 테스트시각 혼용한 기존 수용시험 실패, 독립 수용 조치 아직 미완료)
- QA 독립 격리 DB/Ubuntu 실제 Chrome: **NOT VERIFIED**.

과거 Stage4~7 적정 fixture 시각 `2026-10-19T10:00:00Z`로 수집 전 고정했을 때 원본 및 PR #22 후보 **83 PASS**였던 사실은 현재 시장 주문 안전성 검증과 별개로 유지한다. 원장 감사·한도 기준·승인 정책·기존 실패 테스트를 수정해 회피하지 않았다.

## 4. QA팀 독립 시험 요청

**대상은 최종 신규 PR 커밋 1개 SHA**이며, 사용자 운영 DB `~/program`, `~/.local/share/wealthy`, market-data SQLite·WAL/SHM, OS keyring 사용 금지.

```bash
mkdir -p ~/wealthy-qa23-followup
cd ~/wealthy-qa23-followup
git clone https://github.com/nahee2345/friendly-tribble.git candidate
cd candidate
git fetch origin tech/qa23-design-lock-hardening-20261026
git checkout --detach origin/tech/qa23-design-lock-hardening-20261026
git rev-parse HEAD
python3 -m venv .venv-qa
.venv-qa/bin/python -m pip install -e '.[web]' pytest
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-qa/bin/python -m pytest -q \
  tests/test_p0_session_reservations.py \
  tests/test_qa_p0_api_ledger.py \
  tests/test_p0_turnover.py \
  tests/test_stage1_controls_cards.py
npm --prefix web/frontend ci
npm --prefix web/frontend run test -- src/paperControlDisplay.test.ts src/ManualAssetsPanel.qa016.test.tsx
npm --prefix web/frontend run build
```

추가 독립 시나리오:
1. 세션 NAV 4백만원, 매수·매도+예약+UNKNOWN 80만원 정확한 합산 및 1원 초과/여러 계좌/사용자 분리.
2. **BEGIN DEFERRED** 사용시 보호 함수가 실제 writer lock을 얻어 동시 프로세스 `BEGIN IMMEDIATE`를 거부하는지. DB 잠금 불가 상황은 fail-closed이고 원장 변경이 없을 것.
3. UNKNOWN / RESERVED 기존 ID로 `Portfolios.trade`를 호출해도 새 `virtual_orders` 0건; 응답 유실/재시작 후 재처리하지 않음.
4. PAPER 승인 30만원 경계, 24시간 만료/정지, 미검증 시세·환율, 3개 **아직 미연결** 운영 경로 우회는 BLOCKED. HTTP→SQLite→UI는 아직 독립 NOT VERIFIED.
5. 종료/백업/복원·WAL/SHM·로그아웃·2개 계정·2개 브라우저 탭의 데이터 분리.

## 5. 디자인팀 검수 요청

- 확인되지 않은 PAPER 상태(null): **'상태 확인 불가 · 거래 실행 보류'**, 액션은 **'긴급 중지 요청'**만. 임의 자동매매 시작/성공 표시 금지.
- 실제 paused=true: 중지 상태 + 사용자 승인에 따른 시작 허용 버튼.
- 실제 paused=false: 허용 상태 + 긴급 중지 버튼. 다른 원장/시장 검증이 완료됐다는 뜻으로 표시하지 않음.
- 명령 실행/조회 실패 시 상태 null로 되돌림. API 조회 실패와 실제 서버 중지 상태를 혼동하지 말 것.
- 이전 요청·뉴스·자산의 기존 레이아웃을 유지. Ubuntu Chrome 로그인 후 1366×768, 1600×900, 1920×1080 / 라이트·다크 / 100~200% / 키보드 및 비인가 시 금융정보 비노출 확인.
- 금번은 UI 로직 수정만, 신규 디자인 시스템/CSS 변경 없음.

## 6. 여전히 미완료 및 다음 기술팀 순서

1. 미검증 NYSE/US 정규장·휴일/DST 및 시작 PAPER NAV에 실제 신뢰 가능한 검증기와 공급원/시각 근거 추가. 문자열만으로 검증됨을 표시하지 말 것.
2. 일반 HTTP 주문·Stage4 자동·USD FX 매수 **3경로 전체**를 검증된 세션/예약/가상체결의 단일 SQLite 트랜잭션으로 통합. 기존 Stage4 개별 위험/5% 상한, 30만원 대형거래 승인, 14일 전략 잠금, 긴급정지 정책 유지.
3. 24건 역사적 fixture 실패는 수집 이전 공통 시각 고정으로 현재 시각 안전성 시험과 **분리**하고, current-time 필수 안전 결함/DB 사고 모의 시험을 추가. 현재 실제 운영 조건 PASS 요구.
4. 웹 UI/API/원장/PAPER·USER_DECLARED_REAL·SAMPLE 합계; 실제 QA/디자인 Chrome, NEWS 갱신/번역, 전략 8종의 지원 근거, 16시 7개 카드/오답노트, PC 재시작 복구, npm undici High 등 검증 및 결함 수정.

**개발 진행률 추정:** 기존 약 51% → **약 52%**. 위험한 예약 재사용 경로와 미확인 상태 오안내가 수정되고 증거가 추가되었지만, 핵심 P0 전경로 연동/독립 E2E 미완료이므로 완성률을 크게 올리지 않음.

**최종 판정:** 부분 기능 코드+CI PASS / **전체 P0 BLOCKED / QA 승인 없음 / 실제 Ubuntu Chrome NOT VERIFIED / 병합·배포·자동 PAPER 실행 금지**.
