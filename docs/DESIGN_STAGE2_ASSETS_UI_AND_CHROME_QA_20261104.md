# [디자인팀] Stage2 화면·자산 범위 검수 및 제출 결과 — 2026-11-04

**공유:** 총괄팀 + 기술팀 + QA팀  
**디자인 branch / PR:** `design/stage2-ui-acceptance-20261104` / #28  
**화면 검토 코드:** 기술팀 최신 Draft PR #29 `babbfb7427801f1c68d01c30c7a5daaec212b70b` (backend 후보; #28은 #27 기준)  
**원칙:** 검토·화면 CSS 및 테스트 자동화만 변경. 거래 기능·API·DB·main 및 타 팀 브랜치 불변. 실제 Chrome 인증 앱 E2E는 실행하지 않았으며 PASS로 표시하지 않음.

## A. 빌드/Chrome 검수 증거

1. 디자인 CSS 실제 변경: `web/frontend/src/stage1.css` — 상태/경고 줄바꿈, 키보드 focus-visible, disabled 버튼, 760/480px 반응형 개선.
2. 빌드/Chromium용 workflow `.github/workflows/design-stage2-css-build.yml` 신규. `npm ci`, `npm --prefix web/frontend run build`, PAPER 상태 Vitest와 **정적 디자인 fixture** Playwright Chromium 3해상도(1366/1600/1920) × light/dark × CSS zoom(100/125/150/200) = 24개 캡처/오버플로·포커스 검사를 설계. 스크린샷/JSON은 `design-stage2-chrome-fixture` artifact로 남기도록 구성.
3. fixture script: `scripts/design_stage2_chrome_fixture.mjs`. 여기서 사용하는 한국어 경고·UNKNOWN·NAV 결측은 **가상 정적 fixture이며 실제 앱 데이터/로그인/계좌·API 응답을 사용하지 않음**. 이를 실제 제품 Chrome E2E라고 보고하지 않을 것.
4. 현재 이 문서 제출 시점에 **워크플로 실행 결과·실 Chrome 캡처/빌드 성공은 아직 확인하지 못했으므로 모두 NOT VERIFIED**. 시스템 실행 결과를 확인하기 전에는 PASS 처리 금지. runner 재현 실패는 FAIL/환경 BLOCKED로 별도 기록.
5. 실제 제품 로그인 Chrome E2E(인증 401/403, 회원 전환, 실제 API 값, 토글 클릭, 수동기록 복원)는 **NOT VERIFIED**. 인증·서버 DB가 준비된 격리 Ubuntu 웹에서 같은 최종 SHA와 별도 화면 증거가 필요.

## B. 4가지 핵심 상태의 문구·버튼·필드 명세

| 상태 | 사용자 안내 문구 | 버튼·행동 | 필수 API/데이터 |
|---|---|---|---|
| NAV 검증 대기 | `시작 NAV 검증 대기 · 현금·보유자산·시세·환율·원장 중 확인되지 않은 자료가 있습니다.` | 수동 PAPER/Stage4/FX+BUY 실행 비활성. 진위 확인 후 새로고침만 가능 | `readiness.verified` 실제 bool, `readiness.reason`, 검증 세션 ID·검증 기준시각·NAV 증거 품질·누락 사유. 이름은 **요구 계약**으로 기술팀 확인 필요 |
| 일일 20% 한도 초과 | `PAPER 일일 거래 한도를 초과했습니다. 매수·매도 실사용액과 유효 예약·미확정액을 중복 없이 반영합니다.` | 실행 비활성, 당일 사용액/예약액·근거 조회만 가능 | owner+session/policy version, opening NAV, 한도·사용·예약·UNKNOWN/중복차감 배제 합산. 검증 안 된 숫자는 표시 금지 |
| 승인 만료 | `승인 유효기간(24시간)이 만료됐습니다. 이 승인으로는 주문할 수 없습니다.` | 승인된 주문 실행 비활성, 기존 승인 재사용 금지; 새로운 요청은 별도 사용자 동작·ID 필요 | `status`, `expires_at`, `executed`, `needs_reconciliation`, 서버 시각, approval purpose/owner/fingerprint |
| UNKNOWN | `거래 결과 미확정 · 예약 유지 · 원장 대조 전 다시 주문하지 마세요.` | 자동 재전송·예약 해제·재주문 비활성, 상태 재조회만 가능 | `request_id`, owner/portfolio, `claim_status`, `ledger_order_verified`, 예약 금액·대조 결과·last_checked_at (검증 전 가상 표시 금지) |

**공통:** 화면의 `paused=false`는 사용자 중지 해제일 뿐 **readiness verified/시장·시세/20%·승인 통과와 다름**. `PENDING` 승인대기, `APPROVED` 승인완료·체결 전, `RESERVED/CLAIMED` 한도예약·체결 전, `FILLED` 원장확인 PAPER 체결로 분리. 인증 401/403 후 오래된 비동기 응답으로 금융정보 재표시 금지. 한국/미국 시장·환율·원가 결측은 '미확인'이지 0원 아님.

## C. 자산·계좌 화면 실코드 검토 — 누락 목록·수정안

검토한 `App.tsx`, `pages.tsx`, `Stage1Dashboard.tsx`, `Stage1Page.tsx`, `ManualAssetsPanel.tsx` 모두 PR #29 SHA 기준 정적 확인. 이 화면들은 **서로 다른 API·자료 영역**을 사용하므로 UI 전체 금액 합산 정책이 별도로 검증돼야 합니다.

| ID / 우선순위 | 코드 확인 | 누락·오해 위험 | 수정안·필요 증거 |
|---|---|---|---|
| ASSET-01 / P0 계약 | `/assets`는 `api.portfolio`를 사용해 `data_scope` banner와 `investable_total_krw`를 보여줌; 별도 `ManualAssetsSummary` 배치 | 페이지 안에서 `investable_total_krw`가 PAPER인지 SAMPLE인지 실제직접기록인지 초보 사용자가 합산 오해 가능. 두 수치가 다른 원장이라는 것은 코드상 명확 | 합계 제목에 `displayScope(data_scope)`를 직접 포함, 혼합 합계 없음 문구 근접 배치. scope 미상/복합은 합계 차단. API `data_scope`·included account IDs·exclusions·timestamp 비교 |
| ASSET-02 / P1 | `/accounts`는 `api.portfolio`만 사용해 계좌명/유형/tax/자료영역/평가액/상태를 나열 | `검증된 수동 원장 계좌` 헤더와 실제로 `SAMPLE`이 조회되는 경우의 표현 충돌 소지. 각 계좌 별칭이 실제 증권사 계좌처럼 읽힐 수 있음 | 페이지 설명을 「저장된 자료 범위의 계좌 원장 · 증권사 실계좌 조회 아님」으로 정리. 각 row는 `data_scope` 기준 PAPER/SAMPLE/USER_DECLARED_REAL 표시·상태 미확정이면 평가액 미확정. 계좌별 API owner/scope 검증 |
| ASSET-03 / P0 회귀 | `Stage1Dashboard`은 `asset_scope.selected==='PAPER'`일 때 전체 PAPER 총액을 표시하고 Stage8 owner fallback 계좌 사용 | `hub` 누락 + 별도 Stage8 계좌 자료를 함께 사용하는 경우 출처·기준시각이 다른 값을 혼합 해석할 위험은 동적 재현 필요 | hub/Stage8 서로 다른 기준시간·계좌 ID/owner/소스 응답 재현; total을 fallback 자료의 가상총액으로 채우지 않음. 데이터 오류 시 「총액 미확정」 |
| ASSET-04 / P1 | `ManualAssetsPanel`은 `실제 보유 직접기록 · PAPER와 별도`·증권사 미검증·원가 결측 표기, PAPER/SAMPLE 합산 제외 문구 있음 | 원가가 known이어도 외부 시세와 실계좌 잔고가 검증된 것은 아님; `verified_real_asset_total_krw` 필드가 type에 있어도 UI에 확정 총액을 보여주지 않는 이유를 설명해야 함 | 「사용자 신고 자료, 계좌·가격 미검증」 고정. 원가 known/unknown, 실보유 원장 역사, 중복 통화·원화 환산/FX 검증 별도 badge. 실제 총자산은 검증 전 계산 불가 |
| ASSET-05 / P1 | `/assets` 및 `/accounts`는 `api.portfolio`, `/dashboard`은 PAPER hub; 각각 독립 로딩·에러 컴포넌트 | 동일 메뉴 사이에서 `투자자산 합계`·`계좌 원장`이 같거나 다른 이유가 설명되지 않을 수 있음 | 전역 탭/페이지 제목에 scope 명시, 용어 사전: `PAPER 모의자산`, `실제 직접기록(미검증)`, `SAMPLE 시험자료`. 동적 API 값/브라우저 화면 교차 확인 |
| ASSET-06 / P0 보안 | 401/403 보호 분기는 Stage1Dashboard 존재. `/assets`, `/accounts`, `ManualAssetsSummary`는 별도 API 요청 | 로그아웃/사용자 전환 직후 지연 요청이 이전 사용자 자료를 보여주는지 실제 실험 없이는 확정 불가 | 같은 SHA 두 사용자, 401/403, 인플라이트 fetch, 뒤늦은 응답의 모든 금융 카드/모달/알림 비노출 검증. 결함 단정은 보류 |
| ASSET-07 / P1 | `App.tsx`에 `/providers/kis` 탐색 항목 존재 | 현재 Windows/KIS 연동 전 단계인데 실계좌 연동 사용 준비가 된 것처럼 인식 가능 | 해당 페이지에 「설정 준비 단계 · 실제 연결/주문 불가」 라벨 및 비활성화 조건 검토. KIS API 연결은 범위 밖 |

**기술팀 반영 요청:** `pages.tsx`, `Stage1Dashboard.tsx`, `Stage1Page.tsx` 같은 공용 TSX는 담당 기술팀이 API 상태 및 화면 문구를 수정한 **별도 SHA**를 제출해 주십시오. 디자인팀은 기존 CSS와 명세만 변경해 브랜치 충돌을 피합니다. `data_scope` 문자열을 화면 텍스트만 바꿔 실제 서비스의 자산 격리가 검증된 것으로 오인시키면 안 됩니다.

## D. 완료 체크 기준

- **빌드:** 신규 CI의 `npm ci`, `tsc`, Vite build, Vitest 모두 동일 SHA에서 결과/로그 확인.
- **정적 Chrome fixture:** 3해상도 × 2테마 × 4확대 = 24개 결과와 focus/overflow JSON·캡처 artifacts. **이는 인증된 실제 웹 E2E가 아님**.
- **실제 제품 Chrome:** 격리 localhost 인증, 같은 SHA, 실제 세션/API 연결, 각 상태 스크린샷·키보드·401/403·로그아웃·PAPER/실제/SAMPLE 자료 섞임 0. 미실행은 NOT VERIFIED.
- **QA:** 독립 SQLite/TestClient의 동일 SHA 거래 차단·20%·UNKNOWN·재시작과 화면 숫자/버튼의 정확한 상태 대조. P0 미통과 시 자동 PAPER OFF.
- **운영:** Draft·미병합, main/기존 사용자 DB 보존, Windows/KIS·실거래·외부 공개 금지.

**현재 디자인 작업 범위 판정:** 상태·오류 CSS 및 검수 자동화 스크립트·수용 문서 작성 완료 / 빌드·Chrome 실행 결과 미확인 / 실제 인증 앱 Chrome NOT VERIFIED / 2단계 P0 BLOCKED.
