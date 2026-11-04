# [기술팀 → 총괄팀·QA팀·디자인팀] 2단계 PAPER 거래 안전성 보완 — 1차 코드 후보

**작성일:** 2026-11-04  
**발신:** 기술팀  
**공유 대상:** 총괄팀 + QA팀 + 디자인팀  
**범위:** Ubuntu Wealthy 로컬 PAPER 웹 / Windows 이식·KIS API·실제 주식주문 제외  
**부모:** PR #26 `7ca818bfb5ab6ac37077871b40668356cef7c3f2` (감사 문서), PR #25 `6596f413405c17841460c913d078d2903c887929` (코드 기준)  
**작업 브랜치:** `tech/stage2-paper-safety-guards-20261104`  
**변경 판정:** 서버 기본중지/Stage4/HTTP fail-closed **구현 후보**, E2E·CI 결과는 GitHub Actions 확인 전까지 **NOT VERIFIED**. **통합 거래 20% 및 opening NAV 개발은 아직 P0 BLOCKED**. 전체 2단계 완료라고 주장하지 않음.

## 1. 타 팀 지적 사항 확인

### 총괄팀 — PR #26 댓글
- 세 경로 수동 HTTP, USD FX+BUY, Stage4를 우선 안전화. 제어 행 없음/NULL/조회 오류 시 서버 writer에서 fail-closed.
- opening NAV는 정규장 달력이나 `nav_source` 문자열만으로 신뢰하지 않음.
- 전체 owner 합산 20%와 기존 개별 계좌·Stage4 5% 정책 충돌은 정책 버전·분모·계좌 범위 합의 선행.
- 기존 PR #26 보고서 유지, 구현은 별도 stacked Draft PR. 개발자 자체 검증을 독립 QA/Chrome 승인으로 오인 금지.

### QA팀 — PR #26 댓글
- `stage1_order`은 session date 없이 `trade` 호출, Stage4와 FX+BUY도 새 session claims 미연결이므로 통합 20% 우회 구조 **정적 확인**.
- `_assert_not_paused`는 테이블 또는 행이 없으면 진행해 UI의 기본 중지와 불일치.
- 소유자별 요청 키, 중복·UNKNOWN·승인·24시간·다중 writer·NAV 원본 시세/FX와 복원 시험 요청.

### 디자인팀 — PR #26 댓글
- Stage4 버튼의 `paperControl?.paused`가 null일 때 undefined/falsy로 간주되는 문제.
- Stage4 CLAIMED/EXECUTED/UNKNOWN 상태 설명, 검증 없는 누적원금 400만원 fallback, 자동 운용 상태 null 오안내, 401/403 중 늦은 응답에 의한 상태 재표시 우려.

## 2. 이번에 실제 수정한 코드

| 파일 | 변경 사항 |
|---|---|
| `wealthy_server/stage1_portfolios.py` | SQLite 동일 writer 경계 `_assert_not_paused`: 제어 테이블 없음/행 없음/타 사용자/NULL·비정상값/DB 조회 오류를 모두 **차단**. 오직 명시적 `paused=0`만 통과 |
| `wealthy_server/stage1_controls.py` | 비정상 paused 값의 GET 응답도 `paused=true,state_verified=false`로 fail-closed |
| `wealthy_server/p0_paper_execution_readiness.py` (신규) | 현재 시작 NAV 증명과 세 경로 20% 원자 연결이 없음을 명시하는 **비활성 해제 불가** 서버 release gate. 임의 `nav_source`/운영자 토글로 해제 불가 |
| `wealthy_server/app.py` | `GET /api/v1/stage1/paper-execution-readiness` 인증형 상태 제공. `POST /api/v1/stage1/order`는 NAV 미검증 시 409 `UNVERIFIED_SESSION_OPENING_NAV`로 주문/승인 진입 차단 |
| `wealthy_server/stage4_portfolio.py` | 자동 워커·UI 한 번 실행 공통 `PortfolioEngine.run`에서 세션 미검증 시 `status=session_unverified, executed=[]`. legacy claim/가상체결 INSERT 전 차단 |
| `web/frontend/src/Stage1Page.tsx` | Stage4 버튼 명시적 `paused===false`와 서버 readiness, stage4 `turnover_session_verified` 모두 참인 경우에만 사용. 승인된 개별 주문도 서버 readiness 없으면 비활성, 클릭 전 재조회 |
| 같은 TSX | 갑작스런 control GET 응답 역전 시 epoch가 오래된 값을 무시. `CLAIMED/UNKNOWN/RESERVED/FILLED`의 체결 불확실성과 재시도 금지 안내. Stage4 5% 계획예산과 검증된 20% 한도 구분 |
| 같은 TSX | 미확인 `performance.contributions_krw`의 400만원 확정 fallback 제거. 매월 적립액·400만원 시작금은 계획으로 명시. `auto.enabled`를 체결허가로 표현하지 않음 |
| `web/frontend/src/paperControlDisplay.ts` 및 .test.ts | `paperMayExecute(false)`만 참, true/null/undefined는 거부하도록 공유 함수 및 Vitest 테스트 |
| `tests/test_p0_explicit_paper_control.py` (신규) | 임시 DB에서 제어 테이블 없음/행 없음/타 사용자/중지/오염값 및 직접 trade·FX 쓰기 0 검증 |
| `tests/test_p0_unverified_paper_release.py` (신규) | 신규 release gate 및 Stage4 미검증 시 기존 실행 claim/가상체결 생성하지 않음 |
| `tests/test_qa_p0_api_ledger.py` | 기존 test 유지, 인증+CSRF HTTP 409/no fills와 read-only readiness 200/verified=false 신규 테스트 추가 |
| `tests/test_p0_session_reservations.py` | 기존 opt-in 격리 fixture에 명시적 `set_paused(false)`만 추가(생산 게이트 완화 없음) |
| `.github/workflows/tech-stage2-paper-safety-20261104.yml` | 새 격리 Python P0, 인증 API, Vitest/TS/Vite 빌드, npm runtime HIGH audit 개별 job |

## 3. 중요한 한계 및 단계 상태

1. **거래 통합 완료가 아님.** 현재 런타임은 세션 NAV의 신뢰 가능한 평가 증거가 전혀 없으므로 **수동 HTTP + Stage4 자동 체결을 의도적으로 BLOCKED**한다. 기존 옵션형 `Portfolios.trade(... verified_turnover_session_date=None)`과 `auto_buy_usd_with_fx`는 내부 역사적 테스트용 코드를 유지하며, 실제 운영 API와 worker만 우선 차단. 내부 primitives는 아직 일반 사용 허가 대상이 아님.
2. 독립 검증된 시작 NAV 산출 파이프라인 미완. 시세·FX·보유원장·세션·provenance·해시·조회 시각을 생성/검증하여 하나의 SQLite writer 안에서 20% claim→fill 할 수 있어야 차단을 해제한다. 새 `readiness`의 verified=true를 단독으로 반환하도록 변경하는 것은 수용 불가.
3. Stage4의 `stage4_execution_claims`와 `stage1_turnover_claims`를 여전히 원자적으로 결합하지 않음. P0-D crash/UNKNOWN, 키 collisions, 멀티프로세스/동시성, 가격·환율 갱신과 승인 30만원/24시간까지 QA 필요.
4. 기존 일부 오래된 Stage4 및 API '정상 체결' 테스트는 새로운 런타임 격리 차단과 동작 기대가 충돌할 수 있음. **기존 테스트를 지우거나 성공으로 바꾸지 않았으며**, 현재 변경을 전체 회귀 PASS로 평가하지 않는다. 역사적 feature 테스트 vs 현행 릴리스 차단을 구분한 새 수용 절차가 필요하다.
5. `main`, PR #25, 운영자/사용자 DB, WAL/SHM, keyring, 실제 계좌, 운영 설정은 변경하지 않음. PAPER 자동 실행/병합/배포 계속 금지.

## 4. 테스트와 증거

신규 전용 CI: `.github/workflows/tech-stage2-paper-safety-20261104.yml`
- 고정 HEAD SHA: 본 PR 최신 커밋을 기준으로 확인할 것.
- Python: 중지 없음/조회 오류·무효 row, opt-in 기존 세션 cap/중복, 공식 2026 NYSE 달력, 인증 API session release gate.
- UI: PAPER 상태 null/true/false/undefined, TypeScript/Vite build.
- npm: `npm audit --omit=dev --audit-level=high`로 런타임 의존성 HIGH 여부 분류. `npm ls undici --all`은 dependency provenance 참고, audit 자체는 CI 증거가 필요.
- **현재 이 문서를 작성하는 시점에는 새 후보 CI의 PASS/FAIL이 미확인**. CI 전/후 SHA를 혼용하지 말 것.

## 5. 필요한 팀별 후속 검토

**총괄팀 (정책 결정 요청)**  
① owner-wide 20% 한도 산정 모수: PAPER 미국/한국/ISA 전 계좌의 미국 세션 시작 평가 NAV, 각 통화 FX, 매수+매도+예약/UNKNOWN 산정 여부 및 기존 Stage4 5% 하루 매수상한이 **추가 보수 한도**로 유지되는지 확정.  
② 국내 주문이 미국 장 외 시간에 발생하면 공통 미국 세션 quota에 산입할지, 주문 자체를 금지할지 확정. 기존 승인 정책을 조용히 변경하지 않음.  
③ 상기 검토 전엔 `paper_execution_readiness` release gate 유지.

**QA팀 (독립 실행 요청)**  
신규 정확한 SHA에서 제어 테이블/행 없음·DB read error, HTTP 409, Stage4 no claims, UI 승인 버튼 null, 두 사용자 분리, 원장 변화 0/모든 거래 결측 fail-closed 검증. 기존 24 FAIL과 Stage4 정상 체결 역사적 fixture 실패는 별도 목록·실제 원인 제출. 제공된 CI는 대상 자기검증일 뿐 독립 QA 아님. 테스트 코드/보안 보호를 완화하지 말 것.

**디자인팀 (실 Chrome 확인 요청)**  
P0 Stage4/승인 버튼: `paused=null`, `readiness=null/false`, API 401/403 및 비동기 응답 경쟁에 대해 화면 오안내 없이 비활성인지 실제 Chrome 검수. UNKNOWN/CLAIMED 및 자산 `contributions=null`, 400만원 계획 금액, Stage4 예산과 통합 20% 차이를 확인. 1366×768 / 1600×900 / 1920×1080, 100/125/150/200%, 라이트/다크·키보드·다중 탭.

**기술팀 다음 구현**  
NAV 독립 증거 모델·검증기 → 단일 트랜잭션 세 경로 원장 통합 → Stage4 기존 개별 제한·승인 보존 → UNKNOWN/중복/재시작·현재시각 QA PASS → 서버 readiness 진실된 true 분기. 보안 High 분류는 지금 바로 CI 결과로 재판정.

**최종 상태: 기능 개발 추정치 53% 유지 / 전체 Stage 2 미완 / P0 BLOCKED / 0건 거래 활성화 승인 / 배포 보류.**
