# 기술팀 Stage2 — 디자인팀 PR #35·QA팀 PR #36 후속 인증 화면 경합 검토 (2026-11-06)

**공유 대상:** 총괄팀 + QA팀 + 디자인팀. **기술팀 소유 수정**, 이전 승인/자동 PAPER 정책 미변경.  
**개발 Draft:** [PR #37](https://github.com/nahee2345/friendly-tribble/pull/37), 기존 [PR #34](https://github.com/nahee2345/friendly-tribble/pull/34) 위 분리 스택.  
**실행 증거:** [GitHub Actions 42167160604](https://github.com/nahee2345/friendly-tribble/actions/runs/42167160604) (Ubuntu 24.04/Node 24/Python 3.12/격리 임시 SQLite).  
**최종 수정 HEAD(문서 전):** `9b631d0a9261416791368775abbf611925b6d32d`.

## 참고한 외부 팀 증거 (기술팀 재실행으로 승격하지 않음)
- 디자인팀 [PR #35](https://github.com/nahee2345/friendly-tribble/pull/35), `e08fc0916879e932d02bcfc89e267a68924e86ed`, [Chrome 42165054438](https://github.com/nahee2345/friendly-tribble/actions/runs/42165054438): 임시 1사용자 로그인→로그아웃 후 401, 2사용자 DB의 양쪽 400 차단, 30+2 스크립트 항목 및 JavaScript 오류 0. 샘플/실제/PAPER API 동적 금액 비교, 지연된 401/403, 상태별 원장 대조 미실행.
- QA팀 [PR #36](https://github.com/nahee2345/friendly-tribble/pull/36), `e722bfa1975a7bb1548dabcd1473365579306394`, [QA 42165414797](https://github.com/nahee2345/friendly-tribble/actions/runs/42165414797): 신규 독립 12+기존 HTTP 3 PASS. 로그아웃 401, 정확히 24h 승인, DB 변경 경합, 테스트용 TCP 응답 유실/재시작. **운영 성공 체결 endpoint나 실제 시세 NAV E2E는 아님.**

## 기술팀에서 직접 확인하여 수정한 공통 인증 경합 결함
1. 기존 `web/frontend/src/api.ts`의 session generation 보호가 `Stage1Page.tsx`와 `ManualAssetsPanel.tsx`의 독립 `fetch` 경로에는 적용되지 않았습니다. 한쪽 화면에서 오래된 API 결과가 도착해도 같은 보호로 무효화되지 않는 **경로 불일치**입니다.
2. `api.ts`의 과거 요청이 늦게 401/403을 반환하면, 그 요청 이후 새 로그인이 완료돼도 전역 `invalidateAuthSession()`을 다시 호출할 수 있는 **새 세션 무효화 경합**이 있었습니다.
3. Stage1의 미검증 NAV/승인 조회 및 별도 실제 보유 수동기록 조회·입력 모두 공용 `authenticatedRequest`로 라우팅합니다. 동일한 **CSRF / same-origin / cache:no-store / generation** 검사, 401/403 시 금융 UI 무효화가 적용됩니다. 수동기록의 기존 HTTP status 분기와 request ID 복구 정책 유지.
4. 인증 generation이 다른 과거 401/403은 현재 로그인 세션을 다시 무효화하지 않습니다. 과거 200 응답의 JSON도 화면 자료로 반환하지 않으며, **현재 세션의** 401/403만 즉시 무효화합니다.

## 고정 SHA CI 증거 및 투명한 기존 실패
- **신규 인증 및 직접자산/기존 API 4개 파일:** **24 PASS / 0 FAIL** (그중 신규 `authRace.test.ts` **7 PASS**).
- **TS + Vite 빌드:** PASS.
- **기존 P0 무결성·NAV·20%·writer 차단 서버 시험:** **47 PASS / 0 FAIL**, 기존 TestClient warning 1. 아직 실제 시장 NAV/주문 성공은 검증 아님.
- **`npm audit --omit=dev --audit-level=high`: 0 취약점**; 전부 포함한 `npm ci`에서는 개발/test 의존성 **HIGH 1** 잔존. 정리 대상이나 실사용 보안 무결성 PASS로 오인 금지.
- **전체 프런트엔드**: PR #34 원본 HEAD `9e0bd832...` **66 PASS / 2 FAIL** (68개), 기술팀 신규 `9b631d0a...` **73 PASS / 2 FAIL** (75개). **동일한** `App.test.tsx` 두 테스트 실패. 새로 추가된 7개는 PASS.
- 실패 원인: 2개의 옛 `App.test.tsx`가 `/dashboard`에서 `DashboardPage` 제목 `통합 대시보드`를 기대하지만 현재 제품 `App.tsx` 라우터는 `/dashboard`에 `Stage1Page` (Stage1Dashboard)를 제공합니다. **부모 PR #34에서도 동일 FAIL이 재현되어 신규 인증 수정의 회귀라고 주장하지 않음.** 라우터/시험 기대 수정 없이 차이 보고. **테스트 삭제·skip·assertion 약화 없음**. 따라서 전체 CI는 여전히 FAIL로 유지합니다.
- 역사적 Stage4~7 83개 수용 시험 역시 PR #34의 10/19 **72/11**, 11/06 **64/19**, 기존 실패 미종결. 모든 PASS를 합산하거나 2단계 승인으로 승격하지 않습니다.

## 디자인팀 요청: 실제 HTTP + Chrome 종단간 상태 검사
**아래는 이 SHA에서 실제 Chrome E2E 수행 완료 목록이 아니라, 실행을 요청할 구체적 케이스입니다.**

1. **세션 경합 A:** 로그인 후 `GET /api/v1/portfolio` 응답을 test proxy에서 지연 → 로그아웃 HTTP 200 → 지연된 200 전달. `/assets` 금액·owner·알림·PAPER 카드 재표시 **0**, 보호 API 401, 로그인 화면 확인.
2. **세션 경합 B:** 로그인 A의 지연된 401/403 → 로그아웃 뒤 신규 로그인(새 격리된 단일사용자 세션; 두 사용자 혼합 DB 아님) → 오래된 401/403 도착. **새 세션은 그대로 유지**, A의 이전 금융자료 재표시 없음. 반대로 **새 세션의** 401/403은 즉시 화면 금융정보 비노출.
3. **데이터 범위:** 실제 인증 상태에서 `GET /api/v1/portfolio`의 `data_scope`, `total_scope` 및 `/api/v1/stage1/manual-assets`의 `included_in_paper_nav`, `included_in_sample_nav` 필드, `/api/v1/stage1/portfolios` 3개 PAPER 원장을 비교. `SAMPLE`, `MANUAL_ACTUAL`, `MANUAL_PAPER_SNAPSHOT`, 검증 미완료 상태를 별개 격리 케이스로 만들고 섞은 합계 출력 금지.
4. **승인 및 무결성 상태:** `GET /api/v1/stage1/approvals`의 `PENDING`, `APPROVED`, `EXPIRED`·`executed`·`needs_reconciliation` 및 `/api/v1/stage1/paper-execution-readiness`의 `verified:false`, `/api/v1/stage1/release-integrity`의 `blocked`를 UI와 원장에 대조. `UNKNOWN`은 실제 Stage4/turnover **격리 상태 스키마를 먼저 확인**하고 재시도 금지/체결 미확정으로 표시; **가짜 200 체결은 사용 금지**.
5. 확대·해상도별 overflow 측정값 **FAIL 판정 기준 및 육안 스크린샷** 별도 증빙. 이전 디자인 PR #35 CSS는 이 기술 Draft에 아직 병합하지 않았으므로 독립 디자인 새 브랜치에서 정확한 SHA로 비교.

## QA·총괄 인계
- **QA팀:** 기술팀 PR #37 정확한 SHA의 request-generation 직접자산·Stage1 보호, 401/403 race, 임시 로그인/로그아웃 실 API 400/401/403 교차 검사와 24h 승인·UNKNOWN/20% 무결성 재검증; 시험 `App.test.tsx` 두 건은 부모 코드와 동일 FAIL로 별도 분류.
- **총괄팀:** 기존 /dashboard와 신규 통합 화면의 수용 계약 선택을 QA·디자인 시나리오와 정렬한 뒤 원본 2개 시험의 정식 계약 재작성 여부를 결정. 승인 전에는 그대로 적색.
- **디자인팀:** 위 5개 실제 앱 HTTP·Chrome 사례의 screenshot/result와 날짜·세션·account scope 대응을 PR #35 또는 별도 검증 보고에 제출.

**릴리스 결정:** Stage2 전체 **P0 BLOCKED**. `readiness.verified=false`, 자동 PAPER OFF, 기존 20%/30만원·24h 승인 fail-closed 유지. main/운영 DB·키링·KIS/Windows·실거래·배포 무변경.
