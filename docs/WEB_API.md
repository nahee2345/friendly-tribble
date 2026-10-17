# Wealthy localhost 웹 API 계약

확인 기준일: 2026-10-15 (C-20261011-27 P1 순수 domain 계약 반영)  
base URL: `http://localhost:8000/api/v1`

## 공통 계약

- FastAPI와 built React는 `127.0.0.1:8000`의 같은 origin입니다. 별도 CORS·공개 API는
  제공하지 않습니다.
- 금액·비중은 JSON number가 아니라 정확한 십진 문자열 또는 `null`입니다. 결측을 `"0"`으로
  바꾸지 않습니다.
- 시각은 offset이 있는 ISO 8601입니다. 저장 비교는 UTC/마이크로초이고 화면은
  Asia/Seoul을 명시합니다.
- mutation은 유효한 server session과 `X-CSRF-Token`이 필요합니다. 로그인 전에 얻은 CSRF
  cookie와 header도 일치해야 합니다.
- 오류는 고정 code와 안전한 한국어 메시지만 반환합니다. traceback, 전체 local path,
  SQL/환경변수, 비밀·사용자 입력 원문은 반환하지 않습니다.
- report Markdown과 외부 문구는 HTML로 실행하지 않습니다. 사용자 텍스트는 서버 공통
  계좌번호형 마스킹 후 반환합니다.
- API에는 계좌 조회·주문·가짜 주문 전송 endpoint가 없습니다.

## 인증

| method/path | 기능 | 안전 조건 |
|---|---|---|
| `GET /api/v1/csrf` | 로그인 전 CSRF 준비 | secret을 body에 반환하지 않음 |
| `POST /api/v1/login` | username/password 로그인 | rate limit, dummy Argon2, session hash 저장 |
| `POST /api/v1/logout` | 현재 session 폐기 | 인증+CSRF |
| `GET /api/v1/session` | 인증 상태·사용자 식별 | 비밀번호/hash/token 없음 |
| `POST /api/v1/password` | 비밀번호 변경 | 현재 비밀번호, 새 12자 이상, 모든 session 폐기 |

최초 관리자는 브라우저 공개 가입이 아니라 local TTY 명령으로 생성합니다. 비밀번호를 CLI
argument, JSON, 환경 파일로 전달하지 않습니다.

## 상태·자료·보고서

| method/path | 기능 | 자료 원본/제한 |
|---|---|---|
| `GET /api/health` | 비민감 process health | 인증 불필요, 자산·DB path 없음 |
| `GET /api/v1/status` | local/SQLite/scheduler/order/provider 상태 | `orders_enabled=false`, PWA push disabled |
| `GET /api/v1/dashboard` | 기준시각·자료영역·자산합계·경고 | latest validated scenario |
| `GET /api/v1/portfolio` | 계좌/position/자산군/월별/실제 전략 | 결측 `null`; actual 귀속 없으면 unavailable |
| `POST /api/v1/scenarios/sample` | bundled SAMPLE import | SAMPLE badge 유지, 실제 원장과 병합 안 함 |
| `POST /api/v1/scenarios/upload` | 제한 크기 JSON import | 인증+CSRF, 중복 키/depth/형식/마스킹 검사 |
| `POST /api/v1/reports/generate` | 기존 7절 report 새 snapshot | Python engine 사용, 주문 없음 |
| `GET /api/v1/reports` | report history | 기존 SQLite 불변 이력 |
| `GET /api/v1/reports/{id}` | report detail | owner 검사, raw HTML 실행 금지 |
| `GET /api/v1/jobs` | 15/16시 작업 이력 | 설치·실행 보장은 별도 systemd status |
| `GET /api/v1/notifications` | job/report 기반 내부 알림 | 비민감 문구, `PAGE_OPEN_ONLY` |

`portfolio.actual_strategy`는 목표 비중과 다릅니다. 검증된 보유자산 귀속과 중복 합산 검사가
없으면 `status=UNAVAILABLE`, `weights=null`, `double_count_checked=false`를 반환합니다.

## 사용자 목표 전략과 후보 평가

| method/path | 기능 | 계약 |
|---|---|---|
| `GET /api/v1/strategy-targets/current` | 마지막 저장 목표 | 전체 투자자산 기준 8전략 |
| `GET /api/v1/strategy-targets/history` | append-only version history | 과거 version 수정·삭제 없음 |
| `POST /api/v1/strategy-targets/preview` | exact Decimal 검증·목표 금액 preview | 완전한 자산 합계 없으면 금액 null |
| `POST /api/v1/strategy-targets` | 새 불변 version 저장 | 각 0~100, 합계 정확히 100, 인증+CSRF |
| `GET /api/v1/strategy-catalog` | 설명·자료/engine 상태 | 전망·종목·수량 생성 없음 |
| `GET /api/v1/candidate-assessments` | latest 후보 상태·근거·record IDs | 후보 가중치 모델, 목표 배분과 별도 |

같은 version 이름·다른 내용은 명시적 conflict이며 기존 row를 변경하지 않습니다. 전략 화면의
초안은 저장 version이 아니고 risk allocation을 바꾸지 않습니다.

## 미국 공식 시장정보

시장정보 API는 인증된 로컬 사용자에게 공식 피드의 **메타데이터**만 제공합니다. 원문 본문,
요약·번역·감성, 추천·전망·보고서 문장은 응답하거나 생성하지 않습니다. 모든 endpoint는
로컬 단일 사용자 전제를 확인하고, mutation인 수동 갱신과 SEC 설정은 CSRF 검사를 거칩니다.

| method/path | 기능 | 인증·상태 변경 |
|---|---|---|
| `GET /api/v1/market-information` | 필터·정렬된 고정 snapshot 목록 | 인증 필요, 읽기 전용 |
| `GET /api/v1/market-information/status` | 자동 갱신과 출처별 최근 상태 | 인증 필요, 읽기 전용 |
| `GET /api/v1/market-information/retention` | 만료된 시장정보 메타데이터의 고정 snapshot 미리보기 | 인증 필요, 읽기 전용; 삭제 기능 없음 |
| `POST /api/v1/market-information/refresh` | 같은 수집 경로로 수동 갱신 | 인증+CSRF, 중복 claim/주기 제한 유지 |

### 목록 query와 응답

`GET /api/v1/market-information`의 query 계약은 다음과 같습니다.

| 이름 | 값/기본값 | 제한 |
|---|---|---|
| `q` | 빈 문자열 | 최대 100자; 제목·출처·관련 종목만 검색 |
| `category` | `ALL` | `ALL`, `MARKET`, `COMPANY`, `ECONOMY`, `FILING` |
| `related` | `ALL` | `ALL`, `RELATED`, `UNCONFIRMED` |
| `provider` | 빈 문자열 | 등록된 공식 출처 ID만 허용 |
| `sort` | `PUBLISHED_DESC` | `PUBLISHED_DESC`, `COLLECTED_DESC` |
| `limit` | `30` | 1~100 |
| `snapshot` | 빈 문자열 | 첫 응답의 aware UTC ISO 8601 값을 다음 page에 그대로 전달 |
| `cursor` | 빈 문자열 | 서버가 발급한 opaque cursor; snapshot·sort·filter가 달라지면 거부 |

현재 등록된 `provider` 값은 `sec_edgar`, `sec_press`, `federal_reserve`, `bls_employment`,
`bls_cpi`, `bls_ppi`, `bls_jolts`, `bea`입니다. 이는 한 기관의 여러 피드를 별도 상태로
추적하기 위한 출처 ID이고, 기관 수와 같은 뜻이 아닙니다.

응답은 `{items, next_cursor, snapshot, has_more}`입니다. 첫 요청 시점까지 처음 확인된 행만
snapshot에 포함되므로, 다음 page를 읽는 동안 새 수집이 발생해도 중복·누락으로 목록 순서가
바뀌지 않습니다. cursor는 client가 해석하거나 다른 검색에 재사용하지 않습니다.

각 item의 계약은 다음과 같습니다.

- 안정 식별자: `info_id`, `provider_id`, `institution_id`, `provider_item_id|null`,
  `source_document_id|null`.
- 표시 메타데이터: `provider_name`, `source_name`, `title_original`, `language`, `category`,
  `related_symbols`, `original_url|null`, `link_status`, `is_sample`.
- 시각: `published_at|null`, `published_precision`(`DATE`, `DATETIME`, `UNKNOWN`),
  `collected_at`, `first_collected_at|null`, `retention_expires_at|null`. 저장·비교는 UTC epoch
  microseconds이고 ISO 문자열로 반환합니다. `retention_status`는 `WITHIN_RETENTION`,
  `EXPIRING_SOON`, `EXPIRED`, `CALCULATION_UNAVAILABLE`이고 최초 시각이 복구 추정이면
  `first_collected_estimated=true`입니다.
- 출처 보존: `sources[]`에는 출처별 제목·원문 URL·발행 정밀도·공급자/문서 ID를 둡니다.
  같은 안정 문서의 후속 출처·정정 메타데이터를 기존 최초 확인 이력과 함께 보존합니다.
- 원장 관계: `holding_status`는 검증된 `MANUAL_ACTUAL` position 식별자와 관련 종목이
  일치할 때만 `HELD`이고, SAMPLE·paper·미입력·미확정은 `UNKNOWN`입니다. 시장정보 자료를
  보유자산에 합산하지 않습니다.

`link_status=SAFE`는 등록된 공식 host와 URL 구조가 허용됐다는 뜻입니다. 원문 목적지에 다시
접속해 본문을 확인했다는 뜻이 아니며, URL이 거부되면 `original_url=null` 또는
`UNAVAILABLE`로 반환합니다. 외부 원문은 text data이고 HTML/Markdown으로 실행하지 않습니다.

### 상태와 갱신 응답

상태 응답은 `automatic_state`, `overall_state`, `refreshing`, `last_attempt_at|null`,
`last_success_at|null`, `current_data_as_of|null`, `current_result`, `partial`, `failed_provider_ids`,
`timed_out_provider_ids`, `incomplete_provider_ids`, `using_previous_data`, `retry_allowed`,
`next_retry_at`, `collection_timeout_seconds`, `timeout_limitations`, `providers[]`, `runtime_notice`를
제공합니다. `overall_state`는 `NO_SOURCES`, `NEEDS_CONFIGURATION`, `WAITING`, `OK`, `PARTIAL`,
`FAILED`, `TIMED_OUT` 중 하나입니다. 출처별 상태에는 최소 `provider_id`, `institution_id`,
기관/출처 이름, 저장 상태 `status`, 이번 실행 결과 `this_collection_result`, cadence, 최근
시도·성공, 현재 보존 자료 기준시각, 다음 허용/재시도 시각, 안전한 `error_code`,
현재 실행의 `this_collection_error_code`, `retry_wait`, `retry_allowed`, `timed_out`,
`using_previous_data`, `stale`, `not_modified`가
포함됩니다. `NOT_STARTED_TIME_LIMIT`은 전체 상한 때문에 요청을 시작하지 않은 출처이며 실제
요청 중 `TIMED_OUT`과 구분합니다. 비밀 header·User-Agent 원문·응답 본문은 포함하지 않습니다.
현재 실행 때문에 이전 정상 자료를 쓰는지는 저장된 과거 상태가 아니라 해당 실행 결과를
overlay하여 반환하므로, 제한시간 내 미시작 출처도 이 상태를 잃지 않습니다.

`automatic_state=ENABLED_WHILE_BACKEND_RUNNING`은 백엔드가 살아 있는 동안만 출처 cadence에
따라 확인한다는 뜻입니다. 수집은 ETag/Last-Modified 조건부 요청, 공급자별 실패 격리,
Retry-After/제한 backoff, 단일 갱신 lock과 내구성 claim을 사용합니다. 수동 갱신도 제한을
우회하지 않으며 완료 응답은 `status`, `new_items`, `started_at`, `finished_at`, `manual`을
반환합니다. 가능한 status는 `OK`, `PARTIAL`, `FAILED`, `TIMED_OUT`, `NEEDS_CONFIGURATION`,
`RATE_LIMITED`, `ALREADY_RUNNING`입니다. 전체 상한은 연결·TLS·응답 읽기에 협력적으로
전파되지만 진행 중인 운영체제 DNS 해석 호출을 강제로 중단할 수는 없습니다.

### 보존 상태와 읽기 전용 미리보기

`GET /api/v1/market-information/retention`은 `status=EXPIRED`만 허용하고 `limit`(1~100),
서버 `cursor`, 첫 응답 `snapshot`으로 DB 수준 페이지 조회를 수행합니다. 응답은
`summary`, `items`, `next_cursor`, `snapshot`, `has_more`, `deletion_enabled=false`, 고정 안내를
포함합니다. summary는 정보 항목과 출처 기록을 서로 다른 단위로 집계하고 만료·만료 예정
(만료 전 30일 이내)·계산 불가·보존 기간 내 건수와 확인시각을 제공합니다. 재수집은
`first_collected_at`과 달력 기준 1년인 만료시각을 갱신하지 않습니다.
같은 snapshot으로 이어지는 페이지와 summary에서는 snapshot 뒤 처음 수집된 행을 제외해
페이지 사이에 새 수집이 완료되어도 대상 집합을 바꾸지 않습니다.

미리보기 항목은 만료된 시장정보 메타데이터뿐이며 금융원장·거래·보고서·추천·검증 기록은
포함하지 않습니다. 삭제 method, 삭제 버튼, 자동 삭제 작업은 없습니다. 보존 만료는 자료의
보존 표시이고 공급자 갱신 실패·오래됨 상태와 별개입니다.

### SEC 요청 식별정보

SEC EDGAR·보도자료는 SEC 요청 식별 정책을 만족하는 사용자 본인의 User-Agent 연락정보가
필요합니다. `PUT /api/v1/credentials/sec/default`에 정확히
`{"values":{"user_agent":"..."}}` 형태로 로컬 UI가 제출하고, 삭제는
`DELETE /api/v1/credentials/sec/default`를 사용합니다. 원문은 OS Secret Service에만
보관하고 credential 조회·시장정보 상태·오류·로그로 돌려주지 않습니다. 보안 저장소를
사용할 수 없거나 값이 미설정이면 SEC만 `NEEDS_CONFIGURATION`으로 두며 다른 공식 출처와
기존 저장 자료는 계속 사용할 수 있습니다.

### 수집·저장 안전 경계

- 외부 요청 대상은 소스에 등록된 HTTPS endpoint와 공식 host allowlist로 한정합니다.
  userinfo·비표준 port·local/private IP를 거부하고 DNS 결과와 최대 3회 redirect도 같은
  정책으로 검사하며 환경 proxy를 사용하지 않습니다.
- XML 응답은 content type, 2 MiB 크기, 항목 100개, node/text/depth 한도를 적용하고
  DOCTYPE/entity를 거부합니다. 제목·식별자 길이를 제한하며 본문 필드는 파싱·저장하지
  않습니다.
- 시장정보는 기존 시장 SQLite schema v3의 `market_information`, provider state, refresh
  claim에 저장되고 UTC epoch microseconds와 최초 확인 이력을 유지합니다. v2→v3 migration은
  기존 행을 삭제하지 않으며, 지원하지 않는 version은 변경 전에 거부합니다.
- 온라인 암호화 backup/새 위치 restore는 v3 세 테이블을 필수 구조로 검사합니다. 시장정보
  추가가 W1~W4/X1의 단일 사용자, WAL/SHM, 소유권·복원 경계를 완화하지 않습니다.
- 이 API는 후보 평가·7절 보고서·알림·계좌 조회·주문 endpoint에 시장정보를 자동 주입하지
  않습니다. 영향 분석과 보고서 연결은 별도 검증·승인 전까지 미구현입니다.

## 공급자 인증정보

| method/path | 기능 | 계약 |
|---|---|---|
| `GET /api/v1/credentials` | 항목별 configured 상태 | 원문·길이·부분값 반환 금지 |
| `PUT /api/v1/credentials/{provider}/{environment}` | OS Secret Service 등록/교체 | 인증+CSRF, body의 `values`가 해당 조합의 필수 field와 정확히 일치 |
| `DELETE /api/v1/credentials/{provider}/{environment}` | 해당 조합의 보안 저장소 항목 삭제 | 인증+CSRF |

KIS `paper`와 `live` 항목은 분리하지만 둘 다 현재 시장자료 읽기 준비용입니다. SEC
`default/user_agent`는 공식 피드의 요청 식별정보이며 API key가 아니지만 같은 OS 보안 저장소
경계를 적용합니다. 계좌 번호, 계좌 조회, 주문 권한은 없습니다. Secret Service를 백엔드가
사용할 수 없다고 확인하면 503/안전 오류로 실패하고 SQLite·환경변수·브라우저로 평문
fallback하지 않습니다. UI는 확인되지 않은 잠김 등 원인을 추정하지 않습니다. 일반 Secret
Service에는 안전한 비대화형 재검사 계약이 없어 상태 polling은 OS prompt를 열지 않으며
재검사 endpoint를 제공하지 않습니다.

## 정적 파일·폐기된 endpoint

- built asset만 고정 frontend root에서 제공합니다. 사용자가 path를 지정하는 download/open
  endpoint는 없습니다.
- client route는 SPA fallback을 쓰지만 `..`, backslash, NUL, asset-like 미존재 path는
  거부/404입니다.
- `/sw.js`와 `/manifest.webmanifest`는 현재 PWA 재등록을 막기 위해 410/no-store 처리합니다.
- 과거 `/api/v1/push/*`는 localhost mode에서 사용할 수 없으며 `PUSH_DISABLED`로 실패합니다.
- cloud billing, server provisioning, PostgreSQL migration/apply, OS command, 파일 임의 접근,
  계좌·주문 endpoint는 없습니다.

## 내부 PAPER 제안 API — 설계 전용·미구현

2026-10-14 총괄팀 지시에 따른 개발 준비 계약이다. 후속 승인으로 외부 I/O 없는
`asset_assistant.paper` P1 메모리 domain은 구현했지만, 현재 FastAPI router, OpenAPI,
SQLite에는 아래 endpoint·table이 없고 서버·frontend·worker·scheduler도 이를 호출하지 않는다.
따라서 아래 표는 계속 **제안 API·미구현**이다. `MANUAL_PAPER_SNAPSHOT`, KIS
`paper` 인증환경, 증권사 주문과 모두 별개다. 상세 데이터·상태 불변식은
`docs/FUTURE_VALIDATION_DESIGN.md`의 C-20261011-21 절이 원본이다.

| method/path(제안) | 기능 | 필수 경계 |
|---|---|---|
| `GET /api/v1/paper/status` | INTERNAL_PAPER 준비·중지·대조 상태 | 기본 `enabled=false`, `stage_c_relationship=PREPARATORY_SUPPLEMENT_ONLY`, `stage_c_credit=NOT_GRANTED`; 위험분모·월예산 prerequisite와 session-scoped `execution_run`의 ID, US session, 불변 included proposal ID/version 목록/hash, 실행창·started/ended UTC µs, exact state, attempted boolean, distinct attempt count/IDs, `FULL/PARTIAL`, terminal reason, proposal별 상태 count를 표시하고 상태 조회로 실행·Secret Service prompt가 발생하지 않음 |
| `POST /api/v1/paper/portfolios` | 4,000,000 KRW genesis 생성 | 별도 구현 승인 후 한 번만; SAMPLE/actual import·merge 금지 |
| `GET /api/v1/paper/ledger` | append-only 원장 cursor 조회 | Decimal string, UTC µs, source/correction ref, scope·owner guard |
| `GET/POST /api/v1/paper/strategy-versions` | 8전략 rule·score·budget version | score weight와 budget 분리, 제안식 `max(이번 승인, 직전 효력+14일)`보다 이른 효력 금지; 2주 기준점 확정 전 실행 차단, UPDATE/DELETE 없음 |
| `GET/POST /api/v1/paper/candidate-sets` | 목적별 1~4순위와 재생성 | set/version/hash·cutoff·근거와 `trade_purpose_id`·`purpose_version`·불변 `stable_root_trade_purpose_id`·`parent_trade_purpose_version_id`를 반환/보존; 후보 재생성·strategy version·금액 변경만으로 새 목적/root를 만들지 않으며 생성만으로 승인·예약·실행하지 않음 |
| `GET /api/v1/paper/proposals` | 거래안·승인·예약·위험 영향 | `trade_purpose_id`·`purpose_version`·불변 `stable_root_trade_purpose_id`, core/satellite class·PAPER 분모 준비상태, KST intent date·US session date, 현금/SELL lot 수량 예약, exact proposal state를 반환; 자료 부족은 `UNAVAILABLE`; 실제 broker/order 상태는 반환하지 않음 |
| `POST /api/v1/paper/approvals` | 후보군/30만원 이상 거래/새 root 목적의 서로 다른 승인 | discriminator는 `CANDIDATE_SET`·`LARGE_TRADE`·`NEW_ROOT_PURPOSE`; 공통 불변 target ID/version/hash, 승인 사용자·UTC µs 승인시각·24h 만료·취소/무효·소비 상태와 audit ref를 보존. 큰 거래는 `(portfolio_id, stable_root_trade_purpose_id, stable instrument_id, side, US_session_date)` descendant 전체와 group hash를 target으로 함. 새 root target은 prior root와 서버 발급 proposed root, trade-purpose ID/version/hash, 실제 목적 변경 evidence ref/hash, overlap/split `PASS`와 검사 input hash·policy version·checked-at/cutoff를 포함하고 정확히 일치하는 TradePurpose·audit 생성 transaction에서 한 번만 소비; 동시 소비 하나만 성공. 다른 type 재사용, 취소·만료·기소비·target/hash/evidence 변경, 검사 `FAIL/UNKNOWN`, 동일 목적, 후보/strategy/금액 변경만으로 새 root 발급은 거부. 적법한 새 root도 후보군·`LARGE_TRADE`·30만원 집계·일일/위험/예약 gate를 대체하지 않음; CSRF/replay 보호, 승인 원문 echo 금지 |
| `POST /api/v1/paper/proposals/{id}/cancel` | 실행 전 PAPER 제안 취소 | 실행/결과 미확인이면 cash·일한도·SELL lot 수량 예약을 풀지 않고 대조 case로 전환 |
| `GET /api/v1/paper/daily-limits/{session}` | 미국 session 시작 NAV 20% 한도 | buy+sell gross, used/reserved/available 분리; KST 자정 reset 금지. 5%·ETF 40/50%·위성 10%의 별도 PAPER 분모 미확정 상태도 숨기지 않음 |
| `GET /api/v1/paper/observations` | 관측 전 예측과 일별 결과 | 손익과 적중 분리; strategy 자동 적용 mutation 없음 |
| `GET /api/v1/paper/notifications` | 내부 PAPER 알림 | read/ack/resolved/approval/re-enable 분리; popup·OS·WebPush 없음 |

모든 mutation은 현재의 single-owner fail-closed, session+CSRF, safe error, account-like text
masking, 같은-origin을 그대로 적용한다. proposed executor는 외부 network와 broker method를
가질 수 없다. broker 전송, 계좌 조회, 승인 없는 execute, 정책 override, ledger/history DELETE,
PAPER/실제 자산 merge endpoint는 설계에서도 금지한다.

### P2 mutation receipt·revision 계약 — 저장 어댑터 구현, 웹 API 미구현

PAPER mutation request envelope은 `idempotency_key`, canonical `request_fingerprint`,
`expected_revision`을 분리한다. fingerprint는 method/command type, 현재 인증 owner,
`portfolio_id`, canonical payload와 대상 불변 version/hash를 포함하고 비밀정보나 표시용 원문을
포함하지 않는다. 처리 순서는 다음과 같으며 revision 검사를 receipt 조회 앞으로 옮길 수 없다.

1. 현재 인증·session/CSRF·single-owner·owner/portfolio scope 검증
2. scope 안에서 idempotency key 형식과 canonical request fingerprint 확인
3. 같은 key·같은 fingerprint의 `COMPLETED` receipt면 저장된 result body/hash,
   `result_revision`, `completed_at_utc_us`를 `historical_replay=true`로 반환
4. 같은 key·다른 fingerprint면 값 원문 없이 `IDEMPOTENCY_KEY_CONFLICT`; 불완전·손상 receipt는
   실행하지 않고 `RECONCILIATION_REQUIRED`
5. receipt가 없는 신규 요청만 `expected_revision`/CAS, 현재 gate·승인 검증과 domain mutation 수행

신규 mutation의 state/event/audit, 증가 revision, terminal result와 receipt는 한 SQLite
transaction에서 함께 commit하거나 함께 rollback한다. 응답 유실 뒤에는 이후 revision이 바뀌어도
3단계 결과를 반환하므로 거래를 중복 실행하지 않는다. 이 과거 결과는 당시 요청의 receipt일 뿐
현재 승인 연장·현재 실행 권한·재실행 허가가 아니다. 새 동작에는 새 key와 현재 revision·gate·승인이
필요하다. 이 계약과 `_PaperState` 21개 필드의 완전 hydrate는 별도
`SQLitePaperRepository` 개발 어댑터에 구현했지만 현재 router/OpenAPI·session/CSRF나 운영 경로에는
연결하지 않았다. 따라서 아래 `/api/v1/paper/*`는 계속 미래 계약이며 HTTP로 호출할 수 없다.

Proposal state 계약에는 `MISSED_WINDOW`, `TEMPORARILY_HELD`, `USER_STOPPED`가 포함된다.
`UNKNOWN/NEEDS_RECONCILIATION`에서는 cash·일한도와 SELL lot 수량 예약을 유지한다. 후순위
검토는 앞 순위가 영구 정책 부적격이거나, 승인된 영구 원인 목록과 공식 source record·reason
code·effective/cutoff가 일치해 확정 거래 불가로 검증된 경우에만 허용한다. 일반 거래정지와
재개 여부 미확인은 `TEMPORARILY_HELD`이고, 공식 재개·정정 뒤 같은 rank의 모든 gate를 다시
검증한다. 세션·실행창 종료의 `MISSED_WINDOW`, 가격조건 미충족·이탈, 일시 자료 부족,
승인 대기·거절, 결과 미확인은 후순위 실행으로 이어지지 않으며, 개별승인 거절은 candidate set을
`REGENERATION_REQUIRED`로 만든다.

ExecutionRun은 한 `portfolio_id × US_session_date`의 불변 proposal 묶음이다. run
`MISSED_WINDOW`는 실행창 종료 때 영속 `attempt_count=0`인 경우만 허용한다. 한 번이라도 시도했고
모든 결과가 확정되면 `COMPLETED`: 전부 처리됐으면 `FULL`, 미시도 proposal이 있으면 `PARTIAL`이며
미시도 proposal만 proposal-level `MISSED_WINDOW`다. 시도 결과가 하나라도 미확인이면
`RECONCILIATION_REQUIRED`, 명시적 원장·event·reservation 대조 뒤 `RECONCILED`다. 결과 미확인
예약은 유지하고 run 상태만으로 일괄 해제하지 않는다. `COMPLETED/MISSED_WINDOW/RECONCILED`는
해당 run ID의 terminal state이고 같은 run을 `READY`로 되돌리지 않는다. 재시작은 불변 목록/hash와
distinct attempt event를 복구하고, 이후 실행은 새 run ID에서 모든 gate를 재검증해야 한다. 이
상태·집계·분모 필드는 제안 계약일 뿐 현재 OpenAPI나 DB에는 존재하지 않는다.

## 변경 검증

계약 변경은 API test, SQLite integration, frontend type/test, browser smoke, 민감정보 비노출,
release allowlist를 함께 갱신합니다. fixture 통과와 실제 공급자 연결을 별도 상태로 기록하며,
실제 API를 호출하지 않았으면 연결 성공으로 표시하지 않습니다.
