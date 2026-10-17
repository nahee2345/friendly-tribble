# localhost 웹 전환 요구사항 추적표

기준일: 2026-10-15 (C-20261011-27 P1 순수 domain 구현 반영)  
현재 승인: Ubuntu 24.04 PC 한 대 · Chrome · `http://localhost:8000`

상태 정의: `구현`은 코드가 있고 검증 대상이라는 뜻, `PASS`는 명시한 환경에서 실제 수행한
결과, `PARTIAL`은 일부 연결, `NOT RUN`은 미실행입니다. 시안 화면 존재만으로 업무 기능을
PASS 처리하지 않습니다.

## 이전 통합 지시 대비 범위 변경

| 이전 항목 | 최신 P0 처리 | 보존 상태/근거 |
|---|---|---|
| Oracle/무료 cloud, server/domain 가입 | 중단 | 조사 문서만 legacy, 계정·자원 생성 없음 |
| public HTTPS, tunnel, port 공개 | 중단 | localhost loopback만, Caddy config 비활성 |
| PostgreSQL 운영 전환 | 중단 | 기존 SQLite 우선; PG volume과 코드 삭제 안 함 |
| Docker/Caddy/server worker | 중단 | compose `stop`, volume 보존; local Uvicorn/systemd로 대체 |
| PWA/외부 Web Push | 중단 | SW unregister/cache cleanup만; 페이지 열린 Chrome 알림 |
| Windows/mobile 전용 | 중단 | 기존 문서/코드 보존, 새 구현·검증 없음 |
| React/FastAPI/Python engine | 계속 | 같은 origin localhost 앱으로 재사용 |
| 인증·CSRF·XSS·소유권 | 계속 | local SQLite/Secret Service 경계로 재사용 |
| 승인 디자인·전략·보고서 | 계속 | mock 제거, 실제/결측 상태 연결 |
| cloud 암호화 backup | 전환 | 별도 SQLite online AES-GCM backup/새 위치 restore |

## 플랫폼·데이터·보안

| ID | 요구 | 구현 연결 | 검증 | 상태/비고 |
|---|---|---|---|---|
| LOC-01 | 127.0.0.1:8000 same-origin | `local_runtime.py`, `static_app.py` | `test_web_local_runtime`, browser smoke | 구현; 실제 Chrome 결과는 최신 C- 기록 |
| LOC-02 | 기존 SQLite 우선 | `MarketDataStore`, `Database.create_local_schema` | schema/path/distinct DB tests | 구현; 자동 PG migration 없음 |
| LOC-03 | UTC µs 보존 | market v3 epoch µs(기존 v2 자료 보존) | precision/automation/market-information migration tests | 기존 cutoff 회귀 유지 대상 |
| LOC-04 | 기존 DB 원본 보존 | explicit `--market-database` | hash/schema/count checks | 안정 경로 실제 이전은 별도 기록 |
| AUTH-01 | local 최초 관리자 | TTY `local_runtime create-admin` | runtime admin tests | 비밀번호 CLI 인수 금지 |
| AUTH-02 | session/login 제한 | Auth/Session repositories | local API integration | cookie+server revoke |
| SEC-01 | Host/Origin/CSRF/XSS | app middleware, Markdown renderer | API/browser security tests | localhost도 신뢰하지 않음 |
| SEC-02 | path/command 차단 | static app/path validators, no shell | traversal/unknown arg tests | 전체 path 비노출 |
| SEC-03 | OS 자격증명 | `LocalCredentialRepository`, Secret Service | fake keyring + 실제 availability | 평문 fallback 없음 |
| UI-SEC-03 | 확인된 보안 저장소 사용 불가 안내 | credential `storage.confirmed/available`, `/providers/kis`, `/market` | API/Vitest/browser | 확인되지 않은 잠금 원인 추정·polling keyring probe·재확인 버튼 없음 |
| SEC-04 | 브라우저 비밀 금지 | frontend auth/theme boundary | source scan/browser test | theme만 허용 |
| BKP-01 | SQLite 암호화 backup | `tools.web_local_backup` | roundtrip/tamper/wrong key + market v3 table/count | 구현; 실제 user DB backup 여부 별도 |
| BKP-02 | 새 환경 restore | new/empty directory only | integrity/schema/count/value | fixture PASS 대상 |
| COST-01 | 기본 0원/상한 30k | local stack, 비용 문서 | source/runtime review | 유료 승인 없음 |

## 화면·전략·금융 정책

| ID | 요구 | 화면/API/엔진 | 검증 | 상태/비고 |
|---|---|---|---|---|
| UI-01 | 승인 공통 shell | sidebar/header/card/table/status | Vitest/Chrome viewport | light/dark 포함 |
| UI-02 | 작은 창·zoom | 1200/900/600 breakpoints | 1024px, zoom 100~200 | 실제 Chrome와 headless 구분 |
| DATA-01 | SAMPLE/MANUAL 영역 | scenario/dashboard/portfolio | API+browser | 합산 금지, sample 전역 badge |
| DATA-02 | 결측≠0 | nullable API/display | unit/API/UI | 미입력/계산 불가 표시 |
| STR-01 | 8개 목표 전략 | `/strategies`, target API | 95/105/99.99/100.01/100 | 전체 투자자산 분모 |
| STR-02 | 후보 가중치 분리 | MarketDataStore `StrategyMix` | model/table mutation tests | 목표 저장으로 변경 안 됨 |
| STR-03 | 불변 version/history | target repository/history API | duplicate/append-only tests | 덮어쓰기 없음 |
| STR-04 | draft/saved/actual | draft provider/portfolio actual | route/reload UI tests | 실제 귀속 없으면 unavailable |
| STR-05 | 중복 귀속 금지 | actual allocation contract | duplicate fixture tests | 실제 자료 연결 범위 확인 필요 |
| STR-06 | 상세·필요 자료 | catalog/detail routes | frontend tests/browser | 전망·가상 종목 생성 없음 |
| RISK-01 | 코어/위성 별도 축 | existing Python allocation | full financial regressions | 전략으로 정책 우회 불가 |
| RISK-02 | 집중/금지상품 | existing policy validators | boundary regressions | 주문 endpoint 없음 |
| PORT-01 | 월별/계좌/ISA/세금/배당 | portfolio + capability routes | API/UI | 기존 실제 자료만; 일부 PARTIAL |
| ANL-01 | 분석/공급자 상태 | candidate endpoints + `/market` metadata 목록 | fixture provider/market-information tests | 후보평가와 시장정보 목록은 자동 연결하지 않음; 실연결은 공급자별 별도 |
| REP-01 | 15시 수집/16시 7절 | automation + reports | fake clock/slot/browser | 정시 실제 발화 별도 |
| REP-02 | 과거 보고·근거 보존 | MarketDataStore snapshots/record IDs | persistence tests | append-only |
| ORD-01 | 주문 비활성 | status/API absence | endpoint/source scan | 실제·모의 주문 0회 |
| ORD-02 | fake order 안전 구조 | 상태·정책 경계만 | static/security review | broker 전송 code 활성화 없음 |

## 미국 공식 시장정보 목록

`UI-MARKET-01`은 기사·공시 본문이나 투자 해석이 아니라 공식 원문으로 이동하기 위한 로컬
메타데이터 목록입니다. 구현 존재와 공급자 실연결 결과를 구분하며, 실제 호출 상태는 최신
C-/V- 기록과 `docs/DATA_SOURCES.md`에서 출처별로 판정합니다.

| ID | 요구 | 화면/API/DB 연결 | 검증 | 상태/비고 |
|---|---|---|---|---|
| MI-01 | SEC EDGAR·SEC/Fed 보도·BLS·BEA 메타데이터 | `market_information.py`, `/market`, list API | RSS/Atom fixture parse, source registry review | 구현; 4개 기관·8개 출처 상태 분리, 본문 미저장 |
| MI-02 | 안정 ID·중복 제거·정정/다중 출처 보존 | canonical key, `info_id`, `sources_json`, first-seen | duplicate/revision/sample-live ID tests | 구현; 같은 공식 문서를 목록에 중복 추가하지 않음 |
| MI-03 | 발행 정밀도·UTC µs·수집시각 | market schema v3 ISO+epoch µs | DATE/DATETIME/UNKNOWN, migration/precision tests | 구현; 미확인을 0/가짜 시각으로 바꾸지 않음 |
| MI-04 | 자동/수동 갱신·부분 실패·호출 제한 | background service, provider state/claim, status/refresh API | cooldown, retry, isolation, concurrent refresh tests | 구현; backend 실행 중만 자동, manual도 제한 우회 없음 |
| MI-05 | 검색·필터·정렬·안정 목록 | `/api/v1/market-information`, snapshot/cursor, 새 정보 banner | API bounds/cursor/filter + Vitest/browser smoke | 구현; 읽던 목록 자동 재정렬·scroll 이동 없음 |
| MI-06 | 원문 링크와 외부 입력 안전 | official host allowlist, DNS/redirect check, React text rendering | SSRF/XML bound/link/XSS tests | 구현; `SAFE`는 구조 허용이며 본문 확인 의미 아님 |
| MI-07 | SEC 요청 식별정보 | `/credentials/sec/default`, Secret Service, `NEEDS_CONFIGURATION` | invalid header/value, no-echo, fake keyring tests | 구현; 실제 사용자 값/SEC 연결은 별도 사용자 설정 |
| MI-08 | v2 보존 v3 migration·backup/restore | market-information 3 tables, `web_local_backup.py` | unsupported preflight/nonmutation + v3 roundtrip | 구현; 기존 금융/시장행 삭제·재작성 없음 |
| MI-09 | 보고·추천·주문 권한 분리 | market service/API only | endpoint/source scan, report/order regression | 구현; 요약·번역·감성·전망·보고서 자동 연결·주문 없음 |
| MI-10 | light/dark·responsive·접근성 | `MarketPage`, market CSS, status/alert/details/labels | Vitest + viewport/zoom visible/headless Chrome | 구현; 실제 Chrome PASS 여부는 최신 C-/V- 증거 기준 |
| UI-MARKET-02 | 수집 시간 초과와 작업/자료 상태 분리 | status의 last attempt/success/current result/data as-of/timeout limit | timeout/partial/previous-data unit+Vitest+browser fixture | 구현; DNS 강제취소 한계를 표시하고 timeout을 완료로 표시하지 않음 |
| UI-MARKET-03 | 공급자별 부분 완료·재시도 계약 | provider status의 attempt/success/result/data/retry/error | provider isolation/cooldown + Vitest/browser fixture | 구현; 성공 자료와 이전 정상 자료 유지 여부 분리 |
| UI-MARKET-04 | 달력 1년 보존·만료 상태 | market item retention fields, repository calendar-year 계산 | 30일 경계/정확 만료/재수집 불변/추정·불가 tests | 구현; 표시 기준이며 삭제 권한 아님 |
| UI-MARKET-05 | 만료 메타데이터 읽기 전용 미리보기 | `GET /api/v1/market-information/retention`, DB cursor/snapshot, `/market` 보조 영역 | API paging/Vitest/browser, delete absence scan | 구현; 정보 항목/출처 기록 단위 분리, 삭제 API·자동 정리 없음 |

## 내부 PAPER 추적 — P1 순수 domain 구현 / 운영 경로 미구현

아래는 2026-10-14 총괄팀이 확정한 설계 요구를 후속 P1 구현과 대조한 것이다. `P1 메모리 구현`은
`asset_assistant/paper.py`의 순수 domain과 단일 프로세스 thread 테스트를 뜻하며 API/운영
DB/UI/공급자/scheduler/broker/자동 모의투자가 PASS라는 뜻이 아니다.
논리 schema·상태·전략식·검증 조건의 원본은 `docs/FUTURE_VALIDATION_DESIGN.md`의 C-21 절이다.

| ID | 요구 | 현행 연결 / 제안 연결 | 검증 계획 | 상태·차이 |
|---|---|---|---|---|
| PAPER-01 | 4,000,000 KRW 내부 PAPER, SAMPLE/수동/실제/KIS 분리 | 별도 `PaperScope.INTERNAL_PAPER`·`PaperPortfolio`; 기존 `DataRealm`과 병합하지 않음 | genesis once, scope 혼합·import 차단 | P1 메모리 구현·테스트 / 영속 DB·API 미구현 |
| PAPER-02 | KRW/USD cash·lot·거래·배당·FX·비용·손익·기업행동·정정 원장 | P1 append-only memory ledger·검증 FX·lot·BUY/SELL·fee·배당·실현/미실현손익·안전한 cash 정정; 기업행동 event type만 예약 | Decimal 보존식, 비용 포함 lot 원가의 반복 부분매도 후 잔여 0, USD 정상/오류 FX provenance, correction, cash≠미실현손익, reconciliation | 부분 구현 / 복식 분개 balancing journal·기업행동 전용 계산·영속 reconciliation 미구현 |
| PAPER-03 | 8전략 score·예산·actual·위험축 분리 | P1 전용 8전략 ID, 불변 strategy/budget version, 14일 적용, 명시적 분류·위험 gate와 위성 손실 `PASS/UNKNOWN/BLOCKED` gate | 미분류 위성 1회 계상+신규매수 차단, 손실 gate 결측 차단, 전략예산 격리·동시 위험노출 | 부분 구현 / 실제 8전략 종목선정·시장자료·운영 actual 미구현 |
| PAPER-04 | 미국 session 시작 NAV 20% 일한도 | 저장된 검증 valuation snapshot 기반 `PaperDailyLimit` | 20% ±1원, buy+sell, used/reserved, thread concurrency | P1 메모리 구현 / DB restart·multiprocess 미검증 |
| PAPER-05 | 후보 1~4와 우회 금지·재생성 | P1 `CandidateSet` state/rank와 permanent-reason fail-closed gate; 앞 rank의 활성 예약·시도·fill이 있으면 clearance/후순위 차단 | 승인된 확정 원인만 다음 rank, waiting/rejected/unknown/가격조건/`MISSED_WINDOW` 및 active/executed 선순위 차단 | 부분 구현 / 후보 생성 알고리즘·공식 근거 수집 미구현 |
| PAPER-06 | `CANDIDATE_SET`·집계액 `>=300,000원`의 `LARGE_TRADE`·새 목적의 `NEW_ROOT_PURPOSE` 세 승인, 각 24h | P1 불변 target/hash·stable-root group·취소/무효·exact expiry·NEW_ROOT 원자 1회 소비/audit | 299999/300000/300001, 149999+150001, type 교환 거부, ±1µs, split `FAIL/UNKNOWN`, 동시 소비·rollback | P1 메모리 구현 / API·CSRF·영속 multiprocess 미구현 |
| PAPER-07 | cash/cap/SELL lot 수량 예약·가격조건·가상체결·대조 | P1 cash/cap/전략예산/SELL lot 예약, typed 매수 상한·매도 하한 조건과 체결 직전 재검사, run-attempt 결합 가상체결, UNKNOWN 유지·명시 대조 | 동시 SELL 6+5/6+4, 부분체결, 중복 fill, 조건 미충족 rollback, 고정 승인 FX의 USD 체결, UNKNOWN 예약 유지, 실행창·승인·대조 UTC µs causal floor | 부분 구현 / 실제 quote adapter·crash durability·영속 ReconciliationCase 미구현·자동거래 비활성 |
| PAPER-08 | PC off·절전·자료 지연·restart와 proposal/run state | P1 session-scoped 불변 `ExecutionRun`, proposal 상태, attempt idempotency·중지·대조 | 0건만 `MISSED_WINDOW`, partial, UNKNOWN 즉시 `RECONCILIATION_REQUIRED`·후속 attempt 차단, 예약 유지, 소급 fill 0 | 부분 구현 / process restart 영속성·scheduler·PC-off 복구 미구현 |
| PAPER-09 | 8개 전략 정량 규칙 | 현행 후보점수는 required 8종과 일부만 겹침 | point-in-time, 필수 metric 하나 결측→score UNAVAILABLE·후보 제외, cutoff ±1µs, walk-forward | 기술 제안값 작성 / 승인·자료 대기 |
| PAPER-10 | 무료 공급자와 권리 | `docs/DATA_SOURCES.md`의 C-21 review | 공식문서 근거·fixture/실연결 분리; 총수익/기업행동 부족 차단 | 설계 완료 / KIS 등 미연결 |
| PAPER-11 | 정책 단계 C·D1, 월 예산, PAPER 위험분모 관계 | 단계 C는 KIS 모의환경; P1 memory domain만 있고 운영 runtime 없음 | 내부 결과로 C/D1 통과 표시 0, 실제 자산 분모 유입 0, 월 예산 미확정 실행 차단 | 설계 완료 / 사용자 결정 대기 |
| PAPER-12 | 같은 종목 하루 1회 이중 날짜 guard | P1 KST intent date + US session date memory claim | 같은 session/다른 KST일, 같은 KST일/다른 session, 부분체결은 동일 proposal fill | P1 메모리 구현 / 예외 정책·영속 claim 미구현 |
| PAPER-13 | 승인 근거 provenance·미확정 분류 | 저장소에는 별도 기술팀 최종 보고 원문 없음; 제안 release 제외 `user_data/approval_evidence/paper/`와 manifest | 직접 사용자 승인 원문/기술팀 인용/기술 제안/미확정 구분, SHA-256·문맥 대조, 비밀정보·release 포함 0, null fail-closed | 설계 완료 / 원문 자료 미제공 |
| UI-PAPER-01 | PAPER 메인·scope/NAV/cash/전략 | 제안 `/paper/status`, ledger | 실제 response만, 결측≠0, no mock, viewport/zoom | 디자인 초안 / UI 미구현 |
| UI-PAPER-02 | 미국 session 일일 한도 | 제안 `/paper/daily-limits/{session}` | opening NAV·20%·used/reserved/available, buy+sell, KST reset 금지 | 디자인 초안 / UI 미구현 |
| UI-PAPER-03 | 실행 상태·중지·보류·종료 | 제안 `/paper/status`와 session-scoped run contract | exact enum, attempted boolean, run scope·불변 목록/hash·attempt/resolved/missed count·UTC µs·FULL/PARTIAL·terminal reason 표시, 0회 missed와 1회 partial 구분, proposal-level missed 분리, unknown 예약 유지·대조, restart count 보존, 새 run gate 재검증 | 디자인 초안 / UI 미구현 |
| UI-PAPER-04 | 후보 1~4와 후순위 gate | 제안 candidate-set/proposal API | 승인된 확정 원인 외 rank 우회 0, 일시 거래정지/재개 같은 rank, 세션 종료·가격조건·missed 후순위 action 0, 마스킹·snapshot 불변 | 디자인 초안 / UI 미구현 |
| UI-PAPER-05 | 후보군·큰 거래·새 root 목적 승인 | 제안 approval/proposal API의 세 type별 카드/action/state | type 재사용 0, 불변 target version/hash, prior→proposed root·목적 변경 근거, 승인 사용자/시각·24h·취소/무효·소비 표시, 동시 1회 소비, 동일 목적 차단, 새 root 뒤 stable-root 집계·후보군/큰 거래/일일·위험 gate 재검사, CSRF/replay | 디자인 초안 / UI 미구현 |
| UI-PAPER-06 | 사실·계산·해석·미확인 근거 | 제안 evidence snapshot | cutoff·찬반·비용·전후 영향, 근거 부족 승인 비활성, XSS·마스킹 | 디자인 초안 / UI 미구현 |
| UI-PAPER-07 | 후보군 재생성 | 제안 candidate-set POST + approval | old set 불변, diff·이전 불가 사유, 항상 새 후보군 승인 | 디자인 초안 / UI 미구현 |
| UI-PAPER-08 | 전략·예산·2주 변경 제한 | 제안 strategy-version API | score/budget/actual/risk 네 축, append-only | 디자인 초안 / UI 미구현 |
| UI-PAPER-09 | 내부 알림함 | 제안 notification API | commit 뒤 알림, read/ack/resolved/approval 분리, popup/OS/WebPush 0 | 디자인 초안 / UI 미구현 |
| UI-PAPER-10 | 재실행 안내 | 제안 jobs/status/reconciliation | PC off·절전·backend stop·사용자 stop 구분, 소급 fill 0, restart idempotency | 디자인 초안 / UI 미구현 |
| REPORT-PAPER-01 | 기존 순서·원문 제목의 7절 PAPER 카드 | 기존 7절 renderer + 제안 paper snapshot | 카드 4 `실제 배당과 배당 전환 능력`+`PAPER 모의 배당`, 카드 5 `위성 손실 현황`, 카드 6 `제안 주문안과 이유`+`PAPER 거래 제안`, 목표 의미·실제/PAPER 합산 금지 | 디자인 초안 / 연결 미구현 |
| SAFE-PAPER-01 | W1~W4·X1·D1~D5·O1/O3/O4 보호 재사용 | current database/auth/backup/fetcher guards | strict schema migration, WAL/SHM·UID/symlink, DNS/TLS/XML/deadline/claim | 현행 기반 검증 / PAPER 경로는 미검증 |
| ORDER-PAPER-01 | 증권사·KIS 주문 없음 | 현행 endpoint 0; 제안 executor도 network 권한 없음 | source/API import scan, 외부 network tripwire | 유지 / 실제·모의 주문 범위 제외 |

P1 순수 PAPER domain engine 구현 및 코드 검증 완료. 공급자·UI·scheduler·broker는 활성화하지
않았고, 비용·MDD·위성·산업 cap 등 미확정 정책은 typed gate로 차단한다. 이는 자동 모의투자
운영 가능 또는 실제 거래 검증 완료가 아니다. P2 이후는 총괄팀의 다음 지시 전 미착수다.

## P2 영속 repository 설계 추적 (C-20261011-28, 미구현)

| ID | 요구 | 설계 연결 | 다음 단계 검증 | 현재 상태 |
|---|---|---|---|---|
| PAPER-P2-01 | 기존 DB 보존과 owner scope | 별도 `paper-state.sqlite3`, 불변 owner binding, 모든 PK/FK/query의 owner+portfolio scope | 다른 owner IDOR, 0/2-user fail-closed, market/web DB inode 충돌 | 설계 완료 / DB·schema 미생성 |
| PAPER-P2-02 | P1 상태의 영속 단일 원본 | `BEGIN IMMEDIATE` 안에서 hydrate→순수 command→event/projection/audit→revision CAS→commit | stale revision, 별도 process writer 경합, restart snapshot hash | 설계 완료 / adapter 미구현 |
| PAPER-P2-03 | 승인 소비·목적·감사 원자성 | conditional one-use approval consume와 TradePurpose/audit/split 근거를 한 transaction에 반영 | 8-process 동시 소비 1승자, type/hash/만료, 중간 kill rollback | 설계 완료 / 미검증 |
| PAPER-P2-04 | 현금·SELL lot·일일 한도 예약 | proposal 활성 unique, owner/portfolio/lot 복합 제약과 최신 합계 재검사 | 현금·6+5/6+4·buy+sell cap 별도 process 경쟁 | 설계 완료 / 미검증 |
| PAPER-P2-05 | fill idempotency·UNKNOWN | command/fill idempotency unique, ledger·projection·reservation 같은 commit, UNKNOWN 예약 유지 | 중복 fill, 다른 payload 차단, commit 응답 유실, restart 대조 | 설계 완료 / 미검증 |
| PAPER-P2-06 | UTC µs·Decimal·crash 내구성 | 판정시각 INTEGER UTC µs, 금액 canonical Decimal TEXT, WAL/FULL, projection revision/hash | ±1µs, commit 전/후 SIGKILL, integrity/projection mismatch | 설계 완료 / 미검증 |
| PAPER-P2-07 | schema·migration·backup/restore | exact manifest/checksum, 순방향 migration, 현행 2-DB V1 보존+제안 3-DB V2 | migration failure rollback, V1 호환, V2 새 위치 복원 exact | 설계 완료 / 미구현 |
| PAPER-P2-08 | 파일·공간·busy 실패 안전 | parent 0700, DB/WAL/SHM 0600, live fd X1 보호, bounded busy, FULL 후 write lock | symlink/hardlink/UID, deleted fd 0, 제한 fixture ENOSPC, 자동 재개 0 | 설계 완료 / 현재 디스크 부족으로 구현 차단 |

이 표의 `설계 완료`는 문서 검토 상태일 뿐 P2 코드·SQLite migration·다중 프로세스·crash 시험
통과를 뜻하지 않는다. 메모리 `RLock` 결과는 PAPER-P2-02~08의 실행 증거가 아니다.

## P2 M1·O1 보완 추적 (C-20261011-29, 미구현)

아래 행은 V-20261011-23의 M1과 O1을 반영해 PAPER-P2-02·05·06을 구체화한다. 앞 표의
`설계 완료`를 구현 또는 실행 검증 PASS로 바꾸지 않는다.

| ID | 요구 | 설계 연결 | 다음 단계 검증 | 현재 상태 |
|---|---|---|---|---|
| PAPER-P2-09 | `_PaperState` 21개 필드와 과거 판정 근거의 완전 영속·hydrate | `FUTURE_VALIDATION_DESIGN` M1 매핑: strategy application, KST/US claim, rank clearance, price check, 해제 예약·audit까지 owner/portfolio 원본 또는 전체 이력+projection 대조 | A 무중단/B 저장·hydrate 동등성, 14일·두 날짜 claim·rank·가격시각 경계, 각 transition restart, snapshot+후속 event, 손상/owner 혼입 차단 | 설계 보완 / SQLite·restart·subprocess 미구현·미검증 |
| PAPER-P2-10 | idempotency 완료 receipt를 revision/CAS보다 먼저 판정 | `FUTURE_VALIDATION_DESIGN` unit-of-work와 `WEB_API` mutation envelope: auth/scope → key/fingerprint → 같은 완료 결과 → 다른 payload 충돌 → 신규 요청만 revision/CAS; 결과+상태 같은 transaction | 응답 유실 뒤 revision 변경 재시도, 같은 key 동시 요청, 다른 fingerprint, owner 교차, receipt/state failpoint | 설계 보완 / router·DB·API test 미구현·미검증 |

PAPER-P2-09는 정상적인 완전 빈 DB 외에는 누락 필드를 빈 collection·0·현재시각으로 채우지
않는다. 미지원 version, sequence gap, dangling ref, duplicate, hash·owner 불일치는 write 차단 또는
명시적 대조 필요다. PAPER-P2-10의 `historical_replay` 결과는 현재 승인이나 새 실행 권한이 아니다.

## 예약·알림·복구

| ID | 요구 | 구현 | 검증 | 상태/비고 |
|---|---|---|---|---|
| JOB-01 | user systemd | `tools.web_local_service` 5 units | fake runner + `systemd-analyze verify` | 실제 install 별도 사용자 결정 |
| JOB-02 | 15/16 KST | persistent collect/report timers | generated unit text | 정시 실제 발화 NOT RUN일 수 있음 |
| JOB-03 | 중복 방지 | existing slot/lease | scheduler/concurrency tests | legacy unit 충돌 시 install 거부 |
| JOB-04 | resume/missed | persistent timer + job history | fake clock/recovery | PC off 동안 실행 주장 금지 |
| NOT-01 | 내부 알림함 | job/report derived endpoint | API/Vitest/browser | 구현 |
| NOT-02 | 열린 Chrome 알림 | Notification API page-open only | browser permission test | Chrome 종료 뒤 보장 없음 |
| NOT-03 | Ubuntu session 알림 | 보조 후보 | 실제 session | 구현/실행 상태는 최신 C- 기록 |

## 공급자 상태

공급자 이용 조건·URL·확인일은 `docs/DATA_SOURCES.md`가 원본입니다. 이번 local 전환 통합
검증에서 fixture를 사용한 공급자를 실연결 PASS로 바꾸지 않습니다. 사용자 키가 필요한
KIS/OpenDART/ECOS는 Secret Service 등록 뒤 읽기 연결만 검증하며, 계좌·주문은 없습니다.
SEC/BLS/Fed도 실제 network 호출을 수행한 C-/V- 기록이 있을 때만 그 1회 결과를 표시합니다.

미국 시장정보 목록의 공식 endpoint·이용조건·확인일·cadence와 실제 호출 결과는
`docs/DATA_SOURCES.md`가 원본입니다. 코드에 출처가 등록됐거나 fixture가 통과했다는 이유만으로
실연결 성공, 현재 목적지 접근 가능 또는 본문 사용 권한을 주장하지 않습니다.

## 운영·환경 검증표

| 환경 | 상태 |
|---|---|
| Ubuntu Python unit/integration | 최신 C-/V- 기록의 명령·수 기준 |
| React lint/type/test/build | 최신 C-/V- 기록 기준 |
| localhost API + 두 SQLite | 최신 C-/V- 기록 기준 |
| Ubuntu Chrome visible | 캡처·DISPLAY·Chrome version 증거가 있을 때만 PASS |
| Chrome headless | visible 검증과 별도 기록 |
| systemd unit static/fake | 구현, 실제 등록과 구분 |
| 15:00/16:00 실제 정시 발화 | 실제 기다려 확인하지 않으면 NOT RUN |
| 실제 공급자 | 공급자별 fixture/unauth/auth 상태 분리 |
| Windows/mobile/cloud/public HTTPS/PWA/Push | 현행 범위 제외, NOT RUN |

## 입력 문서 차이

P0에서 지목한 기술팀 `붙여넣은 텍스트(1)(5).txt`와 디자인팀
`붙여넣은 텍스트(2)(1).txt`는 확인 가능한 경로에 없었습니다. 이전 통합 지시와 저장소 승인
PNG/가이드는 확인했습니다. 누락 문서 요구를 추측하지 않았으며, 발견되면 이 표에 원문 ID와
현재 구현 차이를 새 기록으로 추가해야 합니다.

이번 미국 시장정보 지시가 참조한 `TECH-20261013-MARKET-INFO-01`과
`DESIGN-20261013-MARKET-01`도 저장소와 확인 가능한 첨부에 없었습니다. 해당 문서의 내용을
추측하지 않았고, 총괄팀 본문·`PROJECT_RULES.md`·현행 승인 웹 토큰에 근거해 위 연결만
기록했습니다. 원문을 받으면 요구 ID·화면·API·DB·테스트 차이를 새 이력으로 추가해야 합니다.
