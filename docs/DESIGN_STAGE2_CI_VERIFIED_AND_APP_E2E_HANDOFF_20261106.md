# [디자인팀 Stage 2 실제 검증 갱신] CSS 빌드·정적 Chromium 24건 PASS 및 실제 앱 인계

**공유:** 총괄팀·기술팀·QA팀·디자인팀  
**디자인 PR:** #28 https://github.com/nahee2345/friendly-tribble/pull/28  
**고정 커밋:** `4f01564b2e53798c9b08d047d06f60deb486c994`  
**실제 GitHub Actions:** https://github.com/nahee2345/friendly-tribble/actions/runs/41786252825  
**실행 Job:** `build-and-visual-fixture` ID `126428956901` — **SUCCESS**  
**스크린샷 artifact:** `design-stage2-chrome-fixture` ID `13300754398`, ZIP 3,335,168 B.

## 1. 직접 확인한 CI 로그: PASS로 갱신
| 항목 | 실행 결과 | 해석 |
|---|---|---|
| `npm ci` | PASS | 실제 runner에서 프런트 의존성 설치 |
| TypeScript + Vite `npm run build` | PASS | Vite v8.3.4, built in 213ms |
| `paperControlDisplay.test.ts` | **4 PASS, 0 FAIL** | 3상태 허용/차단 helper의 타깃 단위시험 |
| Playwright Chromium 설치·headless 실행 | PASS | Linux GitHub Runner의 Chromium fixture |
| viewport×theme×CSS zoom | **24 PASS, 0 FAIL** | 1366/1600/1920 × light/dark × 100/125/150/200%; 스크립트의 가로 스크롤·포커스 기준 |
| screenshot + JSON artifact | **업로드 완료** | artifact ID 상기 참조, 실제 파일 내용 시각 수동 판독 아님 |

초기 workflow run https://github.com/nahee2345/friendly-tribble/actions/runs/41785956861 은 JS fixture 파일 커밋 전에 실행되어 `Cannot find module scripts/design_stage2_chrome_fixture.mjs`로 실패. 해당 오류는 파일 추가 이후 같은 워크플로가 성공해 해결됨. **성공 커밋과 최초 실패 커밋을 혼동하지 말 것**.

## 2. 엄격한 검증 범위
- 위 Chromium 24건은 **고정 HTML/CSS 정적 fixture**. 실제 React 라우트, authenticated Wealthy 웹, FastAPI, owner/SQLite, POST/GET 응답·실제 금융카드·로그아웃 지연응답은 사용하지 않았음.
- 따라서 **정적 디자인 빌드/fixture = PASS**, **실제 Ubuntu 로그인 앱 Chrome E2E = NOT VERIFIED**로 분리.
- 실제 Chrome E2E에는 임시 SQLite+가짜 계정·키링, 서버 runbook, 인증 CSRF 세션, app/frontend와 backend를 같은 최종 후보 SHA로 시작한 로그, GET/POST 응답, 3해상도·양 테마·배율·키보드 스크린샷이 필요. 원본 운영 DB·KIS·실계좌 사용 금지.
- 디자인팀은 PR #28에서 공용 거래 TSX/백엔드 미수정. SHA `4f01564`은 **PR #27 기반**, 최신 기술팀 PR #29 HEAD `41295d54850c9fc6066c85a6578e0c9bbffe6f0f`과 조합한 최종 통합 HEAD가 아님. 두 결과를 동일 SHA PASS로 승계하지 말 것.

## 3. 실 앱 검수 다음 우선순위
1. **기술팀:** 운영 코드 무변경의 임시 localhost 검증 부팅 방법/테스트 사용자 API fixture/정확한 브랜치·SHA 전달. `readiness=false`, control null/paused, NAV 미검증 409, 실제·SAMPLE·PAPER scope의 프런트 응답 계약 전달.
2. **디자인팀:** 인증 앱에서 NAV 미검증, 거래 중지/미확인, 30만원 승인/24h 만료, UNKNOWN 예약 보류, 20% 초과 및 USER_DECLARED_REAL/PAPER/SAMPLE 자료 영역 화면 대조. 각 상태에서 화면 문구·비활성 버튼·401/403 로그아웃 지연응답을 기록.
3. **QA팀:** PR #30의 독립 CI 7+36 PASS 결과와 분리해 **같은 통합 SHA**의 실제 E2E HTTP↔DB·Chrome 상태를 검증. 테스트 실패/환경장애는 PASS로 변환하지 말 것.

## 4. 전체 판정
**디자인 PR #28 빌드·Chromium 정적 fixture PASS / 실제 로그인 화면 NOT VERIFIED / 기술팀 PR #29 전체 20% 세 경로 독립 QA·실증 NAV 미승인 / Stage2 P0 BLOCKED.** 자동 PAPER OFF, Draft·미병합, Windows 이식·KIS·실계좌·운영 DB 변경·배포 금지.

정적 screenshot artifact는 실제 앱 UI 스크린샷으로 재분류하지 않음.
