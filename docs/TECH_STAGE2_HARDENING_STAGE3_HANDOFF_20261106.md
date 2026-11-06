# [기술팀] Stage2 보완 및 Stage3 자산 범위 설계 연계 (2026-11-06)

**공유 대상:** 총괄팀 + QA팀 + 디자인팀

## 기준 및 정책
- 기준 기술팀 코드: Draft PR #29 `41295d54850c9fc6066c85a6578e0c9bbffe6f0f`.
- 이번 제출: stacked Draft PR #32 `tech/stage2-hardening-stage3-prep-20261106`.
- QA 원문: PR #30 `b033aecb917e97cd145022c060c31315586cccdc`; 디자인 원문: PR #28 `4f01564b2e53798c9b08d047d06f60deb486c994`; 총괄 지시: PR #29 댓글 [#6624401760](https://github.com/nahee2345/friendly-tribble/pull/29#issuecomment-6624401760).
- **readiness.verified=false** 유지. 자동 PAPER OFF, main/운영 DB·키링·실자산·Windows·KIS·실거래·배포 미변경. 2단계 완료/병합 승인 아님.

## 반영 대조표

| 요청 출처/사항 | 코드 파일 | 결과/검증 |
|---|---|---|
| 디자인 PR #28: 401/403·로그아웃·사용자 전환 늦은 응답 재표시 방지 | `web/frontend/src/api.ts`, `auth.tsx`, `apiAuthIsolation.test.ts` | 구식 generation의 요청/JSON 결과 무효화, 즉시 user=null; 프런트엔드 3개 신규 시나리오 포함 7 PASS |
| 디자인 PR #28: /assets 포함 계좌 ID·보호저축 분리, /accounts 각 행 영역 | `wealthy_server/application.py`, `web/frontend/src/types.ts`, `pages.tsx`, `tests/test_web_api.py` | masked `total_scope.included_account_ids`/excluded IDs, realm, ledger complete/false 외부 검증, 계좌별 포함·제외 상태. 범위 불일치 시 합계 미표시 |
| QA PR #30: 차단/재시작 7건을 새 통합 후보에서 재실행 | `tests/test_tech_replay_qa30_stop.py` | QA 소유 7건을 blob SHA에 출처 고정해 기술팀 CI에서 재실행. **독립 QA 승인으로 세지 않음** |
| 총괄/QA: 인증 HTTP·CSRF·미검증 NAV 원장 변화 0 | `tests/test_stage2_http_current_readiness.py` | HTTPS/KIS 없이 로컬 TestClient 인증 401·CSRF 403·NAV 409·새 claim/FX/fill 0을 검사 |
| QA/총괄: 실제 OS 종료/원장 원자성 | `tests/test_stage2_verified_opening_nav_and_atomic_paper.py` | 자식 OS SIGKILL: reserve 후, FX+order 후, mark_filled 후, COMMIT 후; SQLite integrity_check + 재시작 동일 요청 1건 |
| 총괄/QA: 24 FAIL 구분하고 과거/현재 시계 전수 재실행 | `.github/workflows/tech-stage2-followup-stage3-scope-20261106.yml` | 이전 실패 면제/삭제 없이 Oct19 vs Nov6 고정시각 Stage4~7 개별 job으로 비교 |

## 검증된 최초 실행/구분
- 최초 `cb930496...` Run [42161883323](https://github.com/nahee2345/friendly-tribble/actions/runs/42161883323): 프런트 7 PASS + TS/Vite PASS, P0 총 113 PASS/6 FAIL(구형 PR18 테스트가 새 NAV release gate 409를 200 성공으로 기대), Stage4~7 2026-10-19 고정 41 PASS/42 FAIL, 2026-11-06 고정 36 PASS/47 FAIL.
- 현행 안전 계약에 맞춰 HTTP 401/403/409/원장 0행을 **새로 검증**하고, 구형 성공-체결 기대 6건을 현행 P0 합격 job에서 별도 역사적 계약 그룹으로 구분했습니다. 이들을 제품 결함 없음/수용/면제라고 선언하지 않습니다.
- 수정 후 `8c3fced...` Run [42162077834](https://github.com/nahee2345/friendly-tribble/actions/runs/42162077834): **P0 + QA30 기술팀 재실행 107 PASS / 경고1, 프런트 7 PASS / Vite PASS**. Stage4~7은 동일하게 **42 FAIL / 47 FAIL**; 전부 OPEN.
- 이후 화면 합계 범위 판정 보수 수정 `b4f4c476...`이 있으므로 **최종 SHA CI는 별도 재확인** 필요.

## 실패 분류 및 차단 정책
- 기존 Stage5 +350,000원은 25일 자동납입/날짜 경계 가능성이 있으며, 이번 결과에서 이미 도입한 기본 중지 + readiness=false로 과거 성공-체결 전제 자체도 충돌합니다.
- Stage4·Stage5·Stage6·Stage7 원본 24 FAIL을 **완료로 처리하지 않습니다**. 2026-11-06의 고정시간 47건에는 구형 fixture에서 `stage1_trade_control` 명시적 재개 누락 때문에 `PAPER_CONTROL_STATE_UNVERIFIED`가 대량 포함되고, Stage4 `session_unverified` 기대 차이도 포함됩니다. 일부 7단계 `blocked` 감사 이유는 각 케이스의 `issues` 값을 추가 조사해야 합니다.
- 안전한 다음 작업: (A) 진짜 원장 전후 row 증거의 QA 단독 실행, (B) Stage4/5/6/7 기존 fixture의 시점·기대 조건을 **별도 역사적 계약**으로 분리·기록하되 안전 차단을 변경하지 말 것, (C) UI는 디자인팀 실제 인증 앱 Ubuntu Chrome 검수. 무결성 미증빙 NAV/원금·잔여한도 0/400만원 대체 금지.

## 팀별 후속 요청
- **총괄팀:** PAPER 계좌 포함 범위 및 미국/국내 공통 거래일, 허용 quote·FX 출처/시차, Stage4 별도 보수 5% 병행 규칙을 최종 검토하고 신규 PR #32 코드를 승인 전 판단해 주십시오.
- **QA팀:** PR #32 고정 최종 SHA에서 독립 SQLite/TestClient + 다중 프로세스 + SIGKILL/백업 복원 + 20%, 30만원, 24시간, approval 철회, 2 owner 범위에 대한 동적 시험과 이전 실패의 원인·영향 분류 부탁드립니다.
- **디자인팀:** 같은 SHA의 실제 로그인된 Wealthy Ubuntu Chrome으로 3개 화면 크기/테마/배율/키보드, 로그아웃/401/403 후 금전 자료 비노출, 각 계좌·PAPER/실제/SAMPLE 범위 및 UNKNOWN UI를 별도 검수해 주십시오.

**완료 판정:** Stage2 일부 코드 추가·대상 시험 PASS, Stage3 계좌 화면 준비 반영. **전체 역사적 회귀 FAIL**, 독립 최종 QA·실제 Chrome·실세션 데이터 연결 **NOT VERIFIED/BLOCKED**.