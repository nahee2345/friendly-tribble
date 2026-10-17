# Wealthy localhost 웹 전환 총괄팀 보고서

이 문서는 총괄팀 판정을 위한 Codex 구현 인계 보고서이다. 독립 Claude 검증과 총괄팀 승인이
끝나기 전의 최종 제품 승인서가 아니다.

## 1. 보고서 ID·작성시각·대상 기록

- 보고서 ID: `CR-20261011-LOCAL-01`
- 작성시각: 2026-10-11 22:00 Asia/Seoul
- 대상 Codex 기록: `C-20261011-13`(Codex 전체 자체검증 뒤 `VALIDATION_LOG.md` 끝에서 확정)
- 기준 독립 기록: `V-20261011-08`과 그 이전 기록 보존
- 새 localhost 독립 Claude 검증: **PENDING**
- 현재 제품 기준: `PROJECT_RULES.md` v1.3의 Ubuntu 1인 localhost 운영
- 누락 입력: P0 지시에 이름이 나온 기술팀 `붙여넣은 텍스트(1)(5).txt`와 디자인팀
  `붙여넣은 텍스트(2)(1).txt`는 확인 가능한 경로에 없었다. 이전 통합 지시와 저장소의 승인
  디자인 원본은 확인했으며, 누락 문서 내용을 추측해 구현하지 않았다.

## 2. 이전 클라우드 지시에서 변경한 범위

| 이전 진행 범위 | 현행 처리 | 보존/비활성 상태 |
|---|---|---|
| Oracle 등 cloud VM·domain·public HTTPS | 중단 | 계정·VM·domain·인증서 생성 없음. 조사 문서는 legacy banner와 함께 보존 |
| Caddy reverse proxy·외부 port | 중단 | 현행 FastAPI는 `127.0.0.1:8000`만 bind. 기존 deploy 구성 삭제 안 함 |
| PostgreSQL 운영 전환 | 중단 | 기존 SQLite 우선. legacy PostgreSQL volume·코드·과거 증거 보존, 역이행 없음 |
| Docker backend/worker/Caddy | 중단 | Wealthy 컨테이너는 stop 상태, volume·secret 파일 삭제 안 함 |
| PWA·service-worker cache·Web Push | 중단 | 설치 진입점 제거. 과거 same-origin Wealthy SW/cache 정리 코드만 유지 |
| Windows·mobile 전용 구현 | 중단 | 현행 범위 밖, 기존 문서/코드 삭제 안 함, 검증 NOT RUN |
| React·FastAPI·Python engine·승인 디자인 | 계속 | 동일 localhost origin과 SQLite adapter로 재사용 |
| 인증·CSRF·XSS·owner 경계 | 계속 | local web-state SQLite와 Ubuntu Secret Service로 전환 |
| 목표 전략·보고서·예약 | 계속 | 주문과 분리된 local API, systemd user unit 생성 도구로 전환 |
| cloud backup | 전환 | 두 SQLite online backup을 한 AES-GCM bundle로 만드는 local 도구 |

범위 변경 때문에 기존 구현이나 사용자 데이터를 초기화하지 않았다. cloud 관련 파일은 삭제
후보로만 분류했고 현재 runtime import·시작 경로에서 제외했다.

## 3. 작업 전후 상태와 재사용한 구현

### 작업 전

- 이전 C-12 기준 Docker/Caddy/FastAPI/PostgreSQL/PWA 중심의 비공개 웹 구현이 있었다.
- 기존 Python 엔진은 투자 계산·위험정책·7절 보고서·공급자 adapter·SQLite v2·UTC epoch
  microsecond cutoff를 이미 보유했다.
- 발견한 기존 market DB는
  `/home/ww/snap/code/259/.local/share/long-term-asset-assistant/market-data.sqlite3`이다.
  검사 당시 schema version 2이고 금융 원장·수집·보고·전략 행은 0이었다. DB 본문에 논리
  쓰기·이행·삭제를 하지 않았고 WAL은 0 byte이다. SQLite 읽기 연결이 SHM 조정 영역의
  mtime을 갱신할 수 있으므로 WAL/SHM 바이트 불변까지 주장하지 않는다.
- 기존 user systemd Wealthy unit은 없었고 `Linger=no`였다.

### 작업 후

- built React와 FastAPI를 `127.0.0.1:8000`에서 같은 origin으로 제공하는 local runtime을
  추가했다.
- 기존 `MarketDataStore`를 시장·보고·월별·작업·후보 원본으로 유지하고, 인증과 화면 상태에
  필요한 최소 `web-state.sqlite3`를 별도 생성한다.
- Secret Service를 공급자 비밀·local fingerprint·backup key 원본으로 재사용한다. 사용할 수
  없으면 평문으로 대체하지 않고 실패한다.
- user systemd web/collect/report service와 collect/report timer의 고정 5개 unit을 생성·검사하는
  도구를 추가했다. 실제 설치·해제는 하지 않았다.
- legacy Wealthy Docker container는 정지 상태이고 volume은 보존했다. 문서 작성 중에는
  SAMPLE 전용 localhost fixture server만 `127.0.0.1:8000`에서 검증용으로 실행했으며 최종
  teardown 상태는 `C-20261011-13`의 process/port 점검을 원본으로 삼는다.

재사용한 핵심은 `asset_assistant` 계산·정책·보고 엔진, `MarketDataStore` v2, 공급자 adapter,
React 공통 shell·승인 token·8개 전략 화면, FastAPI session/CSRF/owner 경계이다.

## 4. 요구사항별 구현·독립 검증 상태

| 영역 | Codex 구현 상태 | Codex 증거 | Claude 독립 검증 |
|---|---|---|---|
| loopback same-origin | 구현 | local runtime/API/static integration, visible/headless Chrome | PENDING |
| 기존 SQLite·µs cutoff | 구현·보존 | schema/path/inode/precision 회귀 | PENDING |
| SAMPLE/수동 JSON·7절 보고서 | 구현 | API/Chrome/기존 CLI 회귀 | PENDING |
| 8개 목표 전략 exact 100 | 구현 | Decimal·불변 version/history·UI smoke | PENDING |
| 목표/후보 가중치/위험 축 분리 | 구현 | 서로 다른 table/contract·mutation 회귀 | PENDING |
| 월별·계좌·ISA·세금·배당 | PARTIAL | 실제 저장 자료/결측 표시, 확정 계산 일부 없음 | PENDING |
| 종목·ETF 검색/통합 분석 | PARTIAL | 후보평가와 공급자 상태, master/search 일부 없음 | PENDING |
| 15시 수집·16시 보고 | 구현·실등록 NOT RUN | fake clock/slot/lease/unit static 검사 | PENDING |
| 내부 알림함 | 구현 | job/report 파생 API·UI | PENDING |
| 열린 Chrome 알림 | 구현 | 사용자 opt-in, page-open only | PENDING |
| Ubuntu session 알림 | 미구현 후보 | 성공 조건과 분리 | PENDING |
| Secret Service | 구현·실비밀 NOT RUN | fake backend/fail-closed·availability 검사 | PENDING |
| local encrypted backup | 구현 | fixture roundtrip/tamper/wrong-key | PENDING |
| 실제/모의 주문 | 비활성 | endpoint·broker 전송 경로 없음 | PENDING |
| cloud/Windows/mobile/PWA/Push | 현행 범위 제외 | 비활성 문서·route/source 검사 | NOT RUN |

전체 명령·최종 test 수·skip 사유는 `C-20261011-13`을 정확한 실행 원본으로 사용한다.
`VALIDATION_LOG.md`가 ZIP 멤버라 최종 ZIP hash를 같은 로그에 자기참조 없이 넣을 수 없으므로,
로그에는 pre-log ZIP을 기록하고 최종 경로·크기·멤버 수·SHA-256은 Codex 최종 답변과 Claude
인계 프롬프트를 원본으로 삼는다. 문서 작성 시점의 최종 전체 재실행은 Python 286개 실행,
`OK (skipped=6)`이었다.
skip은 `/dev/shm`과 저장소가 다른 filesystem인 hardlink 1개와 명시적 PostgreSQL URL이 없는
legacy opt-in 5개이다. frontend 7 files 35 tests, local service+scheduler 집중 26 tests,
backup 집중 4 tests가 PASS했다. hardlink 별도 실행과 기타 최종 도구·release 결과는 C-13에서
확정한다. visible/headless Chrome smoke는 각각 PASS했다.

## 5. 실제 Ubuntu 실행 명령·접속 주소

프로젝트와 확인한 기존 DB를 명시한다. ROS 환경은 명령에서 제거한다.

```bash
cd /home/ww/program
npm --prefix web/frontend run build

# 최초 1회, Ubuntu Secret Service가 열린 대화형 session에서만
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m wealthy_server.local_runtime create-admin \
  --data-dir /home/ww/.local/share/wealthy \
  --market-database /home/ww/snap/code/259/.local/share/long-term-asset-assistant/market-data.sqlite3

# 로컬 웹 실행
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m wealthy_server.local_runtime serve \
  --data-dir /home/ww/.local/share/wealthy \
  --market-database /home/ww/snap/code/259/.local/share/long-term-asset-assistant/market-data.sqlite3 \
  --host 127.0.0.1 --port 8000
```

Chrome 접속 주소는 [http://localhost:8000](http://localhost:8000)이다. `create-admin`은 TTY에서
비밀번호를 묻고 CLI 인수·파일·로그로 비밀번호를 받지 않는다. 종료는 foreground terminal의
`Ctrl+C`이다. `0.0.0.0`, public IP, tunnel, Caddy, domain을 사용하지 않는다.

## 6. 디자인 시안 대비 결과·캡처 위치

- 원본 archive SHA-256:
  `48f1d2a5241654d579521e254600fbfaf44931cf4c379d152dc9f5ccbb291a08`
- 실제 DISPLAY Chrome: Google Chrome `151.0.7922.137`
- visible 캡처: `/dev/shm/wealthy-local-smoke.6_3pl1jg/visible/`, 18 PNG,
  2,218,576 bytes
- visible manifest:
  `/dev/shm/wealthy-local-smoke.6_3pl1jg/VISIBLE_SHA256SUMS`
- visible manifest SHA-256:
  `65cf000a03c32a0563dd9c763ea47e675f70dd5b93135649ce2f3ce67a02ef5e`
- headless 캡처: `/dev/shm/wealthy-local-smoke.6_3pl1jg/headless/`, 18 PNG,
  2,127,778 bytes
- headless manifest SHA-256:
  `21eb3085bb32778c8eddd3ba3a5c9e6355084f34433b97e1a86b0f0c30888786`

dashboard 1920×1080/1600×900/1366×768/1280×720/1024×768/1000×680,
1000×680의 125/150/200% equivalent, portfolio, strategy mix/detail, settings,
desktop/mobile viewport report와 dark 설정 화면을 검사했다. 이는 실제 Ubuntu DISPLAY의 visible Chrome과 별도
headless 결과이며 Windows DPI·모바일 실기기 결과가 아니다. 상세 시안 차이와 각 파일은
`docs/DESIGN_COMPARISON.md`가 원본이다. 캡처는 SAMPLE fixture만 사용했고 저장소/release에서
제외한다.

## 7. API·DB·전략 데이터 계약 변경

### API

- React static과 `/api/v1/*`를 한 FastAPI origin에서 제공한다. API가 SPA fallback보다 먼저다.
- Host는 localhost/127.0.0.1, Origin은 same-origin만 허용한다.
- server-side opaque session hash, HttpOnly/SameSite cookie, mutation CSRF cookie+header를 쓴다.
- portfolio·notifications·target strategy history·actual strategy availability를 local adapter로
  연결했다. 주문·계좌조회 API는 만들지 않았다.
- legacy `sw.js`/manifest와 미존재 asset·path traversal은 SPA로 fallback하지 않는다.

### DB

- `market-data.sqlite3`: 기존 v2 단일 원본. market record, provider run, candidate assessment,
  report, monthly snapshot, job slot/lease, 설정과 기존 후보 전략 version을 보존한다.
- `web-state.sqlite3`: user, session, login attempt, scenario snapshot, target strategy version,
  migration ledger의 최소 6 table만 둔다.
- 두 DB가 같은 path/inode이거나 web-state에 알 수 없는 table이 있으면 시작을 거부한다.
- WAL, foreign key, FULL synchronous, busy timeout, directory 0700/file 0600을 적용한다.
- PostgreSQL 자동 migration과 기존 자료 삭제는 없다. 안정 경로 이전은 online backup,
  integrity/schema/count 비교, 원본 보존 후 별도 사용자 결정이다.

### 전략

- 목표 전략: 전체 투자자산 분모, 8종 Decimal 합 exact 100%, append-only immutable version.
- 후보 평가 전략: 기존 MarketDataStore의 별도 `StrategyMix`; 목표 전략 저장으로 바뀌지 않는다.
- 실제 전략: 확정된 원장 귀속을 자산당 한 번만 합산한다. 자료가 없으면 `UNAVAILABLE`이다.
- 코어/위성·주식/채권/현금성은 별도의 위험 배분 축이다. 어떤 목표/후보 비중도 위험 규칙을
  변경하거나 주문을 허용하지 않는다.

## 8. 투자정책·금융기록 보존 증거

- `PROJECT_RULES.md`의 수익 비보장, 사용자 승인 없는 주문 금지, 보호저축 제외, 코어 90/
  위성 최대 10, 개별 종목 5, ETF 40/승인 지수 50, 금지상품, 위성 손실 규칙은 유지했다.
- 목표 전략은 후보 평가 가중치나 위험 배분을 덮어쓰지 않는다. 전략 간 중복 귀속으로 자산을
  두 번 합산하지 않는다.
- 초기자산 4,000,000원 선언값을 상세 원장에 자동 합산하지 않는다. SAMPLE/MANUAL 영역도
  합산하지 않는다. 결측을 0으로 바꾸지 않는다.
- 실제 원본 SQLite와 WAL/SHM, legacy PostgreSQL volume을 삭제하지 않았다. SQLite 본문에
  논리 쓰기·migration을 하지 않았으며, 읽기 연결에 따른 SHM 조정 가능성은 작업 상태에
  기록한다. 자동 양방향 migration이나 검증되지 않은 역이행을 수행하지 않았다.
- 기존 C-/V- 기록과 `AGENTS.md`를 수정하지 않았다. `PROJECT_RULES.md`의 웹 운영절 외 투자
  수치·주문 정책을 바꾸지 않았다.

## 9. 공급자별 fixture·실연결 상태

| 공급자 | 현행 코드/표시 | 이번 localhost 전환 실연결 |
|---|---|---|
| KIS | 읽기 전용 시세 adapter, paper/live 비밀 분리, 설정 필요 | NOT RUN; key·계좌·주문 0 |
| OpenDART | 공시/재무 adapter, key 필요 | NOT RUN |
| ECOS | 경제지표 adapter, key 필요 | NOT RUN |
| SEC EDGAR | 공시 adapter, 식별 가능한 User-Agent 필요 | NOT RUN |
| BLS | 공식 공개 adapter, fixture 회귀 | 실제 network 0회 |
| Federal Reserve news | 공식 공개 feed adapter, fixture 회귀 | 실제 network 0회 |

이 작업에서는 모든 공급자 검증을 fixture/fake client로 수행했다. C-12의 과거 BLS/Fed 실제
통신 증거는 보존하지만 새 localhost runtime의 실연결 PASS로 승격하지 않았다. UI의
`AVAILABLE`은 adapter 지원 상태이지 이번 작업의 network 성공 선언이 아니다. 사용자 key가
필요한 경우 local UI에서 Secret Service로 등록해야 하며 채팅·로그로 받지 않는다.

## 10. 예약·알림·백업·복원 결과

### 예약

- `wealthy-local.service`, collect/report oneshot service, collect/report timer의 고정 5개 unit을
  생성한다.
- collect는 Asia/Seoul 15:00, report는 16:00이며 `Persistent=true`다.
- 기존 slot/lease가 중복 실행을 막고, legacy/부분 설치 충돌 시 installer가 거부한다.
- unit 생성·fake runner·`systemd-analyze verify`는 PASS했다. 실제 install/uninstall과 정시
  15:00/16:00 발화는 **NOT RUN**이다.
- 도구는 sudo·lingering 변경·기존 timer 삭제를 하지 않는다. 현재 `Linger=no`다.

### 알림

- 웹 내부 알림함은 저장된 job/report 상태에서 비민감 문구를 파생한다.
- Chrome Notification은 페이지가 열리고 사용자가 명시적으로 허용한 동안만 시도한다.
- Chrome 종료 뒤 browser 알림, 로그아웃 뒤 backend 지속, 절전·PC 종료 중 실행을 보장하지
  않는다. Ubuntu 사용자 session native 알림은 미구현 후보이다.

### backup/restore

- 두 SQLite를 online backup으로 읽고 integrity/schema/table count를 검사해 AES-GCM bundle로
  만든다. 평문 DB temp file을 만들지 않고 key는 별도 Secret Service item이다.
- restore는 AEAD tag·내부 SHA-256·schema/count를 검사하고 새 빈 directory에만 복원한다.
  운영 DB를 덮어쓰지 않는다.
- fixture roundtrip, 변조, 잘못된 key, 기존 목적지 거부 테스트는 PASS했다. 실제 사용자 DB
  backup/restore와 재해·key 분실 훈련은 **NOT RUN**이다.

## 11. 보안·비용·주문 비활성 검증

- localhost도 신뢰하지 않는다. Host/Origin allowlist, CSRF, XSS-safe renderer, owner scope,
  session revoke, login throttling, path traversal/unknown asset 차단을 적용했다.
- key·secret·token은 Secret Service 외 저장하지 않는다. 브라우저 저장소에는 theme와 비민감
  draft만 허용하고 API·console·report·DB에 원문 비밀을 반환하지 않는다.
- provider 본문은 데이터일 뿐 설정·권한·도구 호출을 바꾸지 않는다. 사용자 입력은 기존
  R1~R3 계좌번호형 마스킹 경계를 유지한다.
- `0.0.0.0`, public port, tunnel, domain, cloud upload, 유료 API를 사용하지 않았다. 기본
  인프라 비용은 0원 구조이고 월 30,000원은 승인 없는 지출 한도가 아니라 비용 상한이다.
  이번 작업의 신규 유료 계약·결제는 0이다.
- 실제/모의 주문 route와 broker 전송 코드를 새로 연결하지 않았다. 화면은 `주문 기능 없음`을
  표시한다. 500,000원은 확정 주문 한도로 사용하지 않았다.
- source release는 exact allowlist를 사용해 `.venv`, `node_modules`, frontend `dist`, DB,
  backup, secret, 금융자료, report, screenshot, 실행파일을 제외한다. pre-log ZIP은
  `C-20261011-13`, 로그 추가 뒤 최종 ZIP 경로·member 수·size·SHA-256은 Codex 최종 답변과
  Claude 인계 프롬프트가 원본이다.

## 12. 발견 결함·수정·재검증 결과

| 발견 사항 | 조치 | 현재 상태 |
|---|---|---|
| cloud/PostgreSQL runtime이 최신 localhost 기준과 충돌 | Wealthy compose stop, local runtime·SQLite 문서/entrypoint 추가 | Codex PASS, Claude PENDING |
| PWA/Web Push가 현행 범위를 초과 | manifest link 제거, old Wealthy SW/cache cleanup만 남김 | Codex unit/browser PASS, Claude PENDING |
| web-state와 market data 중복 위험 | 최소 6-table local schema, 동일 path/inode·unknown schema 거부 | Codex PASS, Claude PENDING |
| 정적 SPA fallback이 API/asset 오류를 숨길 위험 | API 우선, asset/traversal/NUL/legacy SW 404/410 경계 | Codex PASS, Claude PENDING |
| 운영 비밀의 평문 fallback 위험 | Secret Service fail-closed local repository | fake backend PASS, 실비밀 NOT RUN |
| 전략 목표를 실제·후보 비중으로 오인할 위험 | target/candidate/actual 별도 contract와 화면 경고 | unit/API/Chrome PASS, Claude PENDING |
| 이전 cloud 캡처가 현행 증거로 남음 | localhost visible/headless 캡처로 비교 문서 갱신 | Codex 시각 확인 PASS, Claude PENDING |

독립 Claude가 발견한 재현 가능한 결함은 숨기거나 PASS로 바꾸지 않는다. 현 승인 범위의 결함은
Codex 수정 후 Claude 재검증을 받으며, 정책·주문·새 대규모 기능이 필요한 사항은 총괄팀 결정으로
돌린다.

## 13. 미완료·NOT RUN·사용자 설정

- 독립 Claude localhost 검증: **PENDING**
- 실제 사용자 최초 관리자/Secret Service 운영 key 생성: 사용자 대화형 설정 필요
- KIS/OpenDART/ECOS/SEC 인증 실연결: key/User-Agent 설정 뒤 별도 읽기 연결 검증 필요
- 실제 계좌 조회, KIS 모의·실전 주문, 자동매매: 미구현·금지
- 실제 user systemd 설치·해제, 15:00/16:00 정시 발화, suspend/resume·재로그인 장기 운영:
  NOT RUN
- `Linger=no` 변경: 자동 수행하지 않음. 마지막 로그아웃 뒤 지속 요구를 사용자와 먼저 결정
- 실제 사용자 DB 안정 경로 backup/restore·원본 대조: NOT RUN
- Ubuntu session native notification: 미구현
- 월별 운영 자료, 실제 전략 귀속/점진적 전환, 계좌·ISA·세금·배당·검색/분석 완성: PARTIAL
- 실제 OS 125/150/200% 배율, Chrome 수동 접근성/키보드 전수 검사: NOT RUN 또는 부분
- Windows·mobile·cloud·public HTTPS·PWA·Web Push·`.exe`: 현행 범위 제외, NOT RUN
- 누락된 최신 기술팀/디자인팀 첨부 2개: 사용자/총괄팀이 제공해야 원문 차이 추가 가능

## 14. 변경·미변경 파일·삭제 후보

### 이번 localhost 범위의 주요 변경·신규 파일

- 정책/진입: `PROJECT_RULES.md`, `DOCS_INDEX.md`, `CODEX_WORKFLOW.md`, `README.md`,
  `pyproject.toml`, `CLAUDE_VALIDATION.md`
- 설계/추적: `WEB_DESIGN_SPEC.md`, `UI_DESIGN_SPEC.md`,
  `docs/WEB_ARCHITECTURE.md`, `docs/WEB_API.md`, `docs/DEPLOYMENT.md`,
  `docs/BACKUP_RESTORE.md`, `docs/COST_BUDGET.md`, `docs/DESIGN_COMPARISON.md`,
  `docs/REQUIREMENTS_TRACEABILITY.md`, 이 보고서
- backend: `wealthy_server/local_runtime.py`, `local_adapters.py`, `local_credentials.py`,
  `static_app.py`, `database.py`, `application.py`, `app.py`, `repositories.py`
- engine/release: `asset_assistant/market_data.py`, `asset_assistant/release.py`
- frontend: `web/frontend/index.html`, `public/sw.js`, `src/App.tsx`, `api.ts`, `format.ts`,
  `main.tsx`, `notifications.tsx`, `pages.tsx`, `pwa.ts`, `runtimeStatus.tsx`, `styles.css`,
  `types.ts`와 관련 tests
- 운영도구: `tools/web_local_service.py`, `tools/web_local_backup.py`,
  `tools/web_browser_smoke.mjs`
- 테스트: `tests/test_web_local_runtime.py`, `test_web_local_service.py`,
  `test_web_local_backup.py`, `test_release.py`와 frontend local tests
- 로그: 기존 기록을 건드리지 않고 끝에 추가할 `C-20261011-13` 한 항목

### 미변경·보존

- `AGENTS.md`, 기존 C-/V- 본문, 투자정책 숫자·주문 금지, 기존 SAMPLE JSON
- 기존 사용자 SQLite/WAL/SHM, legacy PostgreSQL volume, deploy secret 파일
- 기존 계산·원장·배분·보고·공급자·CLI/Tk 구현과 회귀(필요한 adapter/release 변경 제외)
- 실제 계좌·key·token·개인 금융자료는 읽거나 쓰지 않음

### 총괄팀 승인 전 삭제하지 않을 후보

- `deploy/`의 Docker/PostgreSQL/Caddy/cloud 구성과 관련 legacy tests/docs
- legacy SQLAlchemy/PostgreSQL repository·worker·cloud backup 경로
- frontend의 미사용 manifest 및 PWA/Push legacy 보존 파일
- 기존 Tk GUI·Windows scheduler/package 문서·코드
- 과거 cloud 캡처·검증 산출물

사용처와 비활성 상태가 추적 가능하도록 보존했다. 대규모 삭제나 DB 역이행은 하지 않는다.

## 15. 기술팀에 공유할 사항

총괄팀이 기술팀 공유 여부를 판정할 내용이다.

- 현행 단일 실행 계약은 `wealthy_server.local_runtime serve`, loopback 8000, 두 SQLite,
  Secret Service이다. cloud compose가 운영 진입점이 아니다.
- market DB와 web-state DB는 절대 같은 path/inode를 쓰면 안 된다. 기존 market schema v2와
  UTC epoch microsecond는 호환성 필수조건이다.
- target strategy, candidate scoring mix, actual attribution, risk allocation은 네 개의 다른
  의미다. API/DB/UI에서 병합하지 않는다.
- user systemd installer는 fixed 5-unit allowlist, 충돌 fail-closed, no sudo/no linger/no delete
  계약을 유지해야 한다.
- backup은 각 DB의 일관된 online snapshot, schema/count/hash, AEAD와 새 위치 restore를
  모두 만족해야 한다. 두 파일 사이의 완전히 같은 순간은 현재 원자적으로 보장하지 않으므로
  운영 전환용 backup은 앱과 one-shot을 중지해 만든다. 운영 DB overwrite 기능을 추가하지 않는다.
- 주문·계좌 조회 endpoint나 모호한 500,000원 한도를 추가하지 않는다.
- 누락된 기술팀 최종 첨부가 제공되면 기존 구현과 ID 단위로 대조해야 한다.

## 16. 디자인팀에 공유할 사항

총괄팀이 디자인팀 공유 여부를 판정할 내용이다.

- 현행 비교 증거는 `docs/DESIGN_COMPARISON.md`와 visible Chrome 18개 캡처이다.
- mock 종목·수익률·세금·전환일을 실제값처럼 표시하지 않는다. 결측·미지원·계산 불가는
  화면의 정상 상태다.
- 목표 전략/초안/저장/실제/후보 가중치/위험 배분의 시각 구분을 유지한다.
- 1000×680과 zoom equivalent에서 menu·상단 상태 rail·본문 scroll을 보존한다.
- `LOCALHOST_ONLY`, SQLite, SAMPLE, 기준시각, KIS 설정 필요, 주문 없음은 장식이 아니라
  필수 안전 상태다.
- mobile viewport 캡처는 반응형 참고이며 mobile app/PWA 승인 증거가 아니다.
- 누락된 디자인팀 최종 첨부가 제공되면 승인 token·spacing·상태 문구 차이를 다시 추적한다.

## 17. 총괄팀·사용자 결정이 필요한 사항

1. 독립 Claude 결과를 받은 뒤 현 localhost 구현을 운영 기준으로 승인할지
2. 확인한 Snap revision 종속 market DB를 안정 경로로 backup/restore할지, 언제 할지
3. 실제 user systemd unit을 설치할지와 마지막 로그아웃 뒤 운영 필요 여부. 필요한 경우에만
   `loginctl enable-linger`의 보안·전원 영향을 사용자와 별도 협의할지
4. 사용자 본인 공급자 중 무엇부터 Secret Service에 등록해 읽기 실연결을 검증할지
5. 계좌·ISA·세금·배당·종목 검색 중 다음 완성 우선순위와 필요한 공식 자료
6. 실제 전략 귀속과 점진적 전환의 데이터 계약·일정. 목표 전략을 실제로 간주하면 안 됨
7. Ubuntu session native 알림이 필요한지. page-open Chrome/internal 알림만으로 충분한지
8. 누락된 기술팀·디자인팀 최종 첨부 2개를 제공할 수 있는지
9. legacy cloud/PostgreSQL/PWA/Tk/Windows 파일 삭제를 별도 승인할지. 현재는 보존
10. 실제 주문/모의주문은 이번 결정 대상이 아님. 별도 정책·한도·사용자 승인 전 계속 비활성

## 18. 다음 단계 후보와 근거

다음 순서는 총괄팀의 다음 지시 전에는 대규모 개발을 시작하지 않는다는 전제의 후보이다.

1. **Claude 독립 검증**: 별도 임시 DB/환경에서 기능·디자인·보안·DB·예약·restore와 source
   release를 재현한다. 구현자 자체검증과 독립 판정을 분리하기 위해 최우선이다.
2. **재현 결함만 수정·재검증**: 승인 범위 안 결함을 파일/재현/기대 결과 단위로 수정한다.
   정책·주문·대규모 기능은 수정 대상으로 확대하지 않는다.
3. **총괄팀 판정과 사용자 설정**: 운영 DB 안정 경로, 관리자 생성, systemd 설치,
   provider 우선순위를 사용자와 정한다.
4. **제한된 localhost 운영 acceptance**: SAMPLE 후 수동 비식별 자료, 실제 user session의
   Secret Service, timer 정시/절전 복귀, encrypted backup/새 위치 restore를 차례로 확인한다.
5. **부분 화면 우선순위 결정**: 공식 자료와 정책이 준비된 월별·계좌/ISA·세금·배당·분석 중
   승인된 한 묶음만 다음 구현 범위로 만든다.

Cloud/public deployment, PostgreSQL 전환, Windows/mobile/PWA, 실제 주문은 후보에 포함하지
않는다. 총괄팀의 다음 지시 없이 시작하지 않는다.

---

# C-15/V-11 종결·릴리스 보존·로컬 실행 준비 부록

이 부록은 기존 `CR-20261011-LOCAL-01`을 당시 기록으로 그대로 보존하면서 이후 상태만
갱신한다. 아래 상태가 위 보고서의 `Claude PENDING`과 C-13 수치를 대체한다. 이전 본문을
소급 수정하지 않았으며, 이 부록 뒤 변경은 다시 독립 검증되기 전까지 V-11 `PASS` 범위가
아니다.

## 1. 보고서 ID·작성시각·대상 C-/V- 기록

- 보고서 ID: `CR-20261013-LOCAL-02`
- 작성시각: 2026-10-13 Asia/Seoul
- 종결 대상: `C-20261011-15` / `V-20261011-11`
- 준비·문서 변경 대상: `C-20261011-16`(이번 작업), 새 Claude V- 검증 대기
- 코드 revision: Git 저장소가 아니므로 commit 없음. C-15 코드 식별은 아래 보존 ZIP의
  크기·168 members·SHA-256을 사용한다.

최신 로그의 C-15/V-11 전체와 사용자가 전달한 요약을 대조했다. X1 반복표, W4 우회 발견과
차단, test 수, Chrome, 릴리스 ZIP, 정리 실수 1건과 `NOT RUN` 목록까지 불일치가 없었다.
V-11에 총괄팀 전달 프롬프트가 없었던 점은 검증 결과 불일치가 아니라 인계 절차 누락이며
이번 문서 변경으로 보완한다.

## 2. 이전 cloud 범위와 이번 변경 범위

`CR-20261011-LOCAL-01`의 cloud/PostgreSQL/PWA/public 범위 중단은 그대로다. 이번에는 X1/W4
종결, 검증된 소스 후보 보존, 인계 체계 수정, localhost 사용자 인수 확인 준비만 수행했다.
cloud·외부 공개·PostgreSQL·Windows/mobile·PWA/Push·실제 주문 작업은 재개하지 않았다.

## 3. 작업 전후 상태와 재사용한 구현

- 작업 전: C-15 수정과 V-11 독립 검증은 끝났지만 후보 ZIP은 tmpfs에만 있었고, Claude가
  결함 없음으로 끝낼 때 총괄팀 프롬프트를 강제하는 절차가 없었다.
- 작업 후: 동일 바이트의 후보를 프로젝트의 전용 0700 폴더에 0600으로 보존했다. Codex →
  Claude → Codex 버전 보고 → 총괄팀 판정 절차를 명문화했다.
- FastAPI/React/Python engine/SQLite/Secret Service 경계는 바꾸지 않았다. 실제 운영 자료나
  keyring item을 읽거나 이전하지 않았다.

## 4. 요구사항별 구현·독립 검증 상태

| 항목 | 상태 | 근거 |
|---|---|---|
| X1 live SQLite 잠금 보존 | **CLOSED / PASS** | V-11 current 3/3, sidecar 유지, deleted fd 0, session 4/4, restore 4 |
| X1에서 드러난 W4 우회 | **CLOSED / PASS** | C-14 동작은 200 우회, C-15는 사용자 2명 때 400·삭제 즉시 200 |
| W1~W4 회귀 | **PASS** | V-11 probe 70/70, 집중 85/85 |
| 전체 현행 non-PostgreSQL | **PASS** | 292 실행·skip 6, hardlink 별도 1/1, 실질 287/287 |
| frontend / release / Chrome / Tk | **PASS** | 36/36, 12/12, Chrome 두 모드 24 checks·18장씩, Tk PASS |
| C-15 후보 보존 | **PASS** | 아래 원본·대상 SHA/크기/멤버/권한 대조 |
| 실제 Secret Service item·관리자·로그인 | **NOT RUN** | 사용자 keyring unlock과 비공개 TTY/Chrome 입력 필요 |
| 이번 C-16 문서·제외 규칙 | **Codex focused PASS / Claude PENDING** | C-15/V-11 PASS를 자동 승계하지 않음 |

## 5. 실제 Ubuntu 실행 명령·접속 주소

운영 주소는 `http://localhost:8000`, bind는 `127.0.0.1:8000`이다. 정확한 임시 검증 폴더
준비, 선택적 Secret Service 격리 probe, TTY `create-admin`, foreground `serve`, Chrome
로그인·로그아웃·재시작·정리 명령은 `README.md`의 “실제 localhost 인수 확인” 절을 따른다.
비밀번호와 key는 prompt/UI에만 입력하며 채팅·인수·파일에 넣지 않는다.

## 6. 디자인 시안 대비 결과·캡처 위치

V-11 실제 Ubuntu Chrome 151 headless/visible은 각각 24 checks와 18 PNG를 통과했고,
SAMPLE과 MANUAL_ACTUAL 보고서 이력을 구분했다. 캡처·manifest는
`/dev/shm/claude-c15.DIvM9N`에 보존되어 있다. 이번 C-16은 UI/CSS를 변경하지 않았으므로 새
캡처를 만들지 않았다. Windows DPI·mobile 실기기는 계속 `NOT RUN`이다.

## 7. API·DB·전략 데이터 계약 변경

없다. C-15의 실행 중 SQLite path-only permission 검사와 기존 UTC microsecond, 두 SQLite,
single-user fail-closed, 목표 전략/후보 가중치/실제 귀속/위험 축 분리를 유지한다. 이번
변경은 인계 문서, release 보존·제외 명시, 사용자 acceptance 절차뿐이다.

## 8. 투자정책·금융기록 보존 증거

`PROJECT_RULES.md` SHA-256은
`04a44f468ad56bee3f85f51174e05269b8daff576ce89bd6c71eca1e4fc5af20`이며 수정하지 않았다.
실계좌·실제 개인 금융자료·공급자 key/token을 읽거나 쓰지 않았다. 기존 C-/V- 본문도
수정하지 않고 C-16만 끝에 추가한다. SAMPLE/수동 영역, 초기자산 선언, 위험 한도와 주문
금지는 변경하지 않았다.

## 9. 공급자별 fixture·실연결 상태

이번 작업의 외부 공급자 통신은 0회다. KIS/OpenDART/ECOS/SEC/BLS/Fed 모두 새 실연결을
수행하지 않았고 C-15/V-11 fixture 상태를 그대로 유지한다. Secret Service backend 발견은
공급자 인증 성공이 아니다. 실제 key·계좌 조회·모의/실전 주문은 0회다.

## 10. 예약·알림·백업·복원 결과

실제 user systemd 등록·해제, lingering 변경, 정시 15:00/16:00 발화는 수행하지 않았다.
Chrome 알림·native 알림·실제 사용자 DB backup/restore도 이번 범위에서는 재실행하지 않았다.
V-11의 X1 온라인 backup 최신 session 4건 복원 PASS가 코드 종결 근거이며, 실제 사용자
backup 승인으로 확대하지 않는다.

## 11. 보안·비용·주문 비활성 검증

- read-only preflight에서 `keyring.backends.SecretService.Keyring`이 허용 backend이고 priority
  양수, session D-Bus와 `org.freedesktop.secrets` daemon 활성임을 확인했다. item
  read/write/delete는 하지 않아 실제 Secret Service 통과가 아니다.
- 관리자 없는 새 tmpfs DB의 `serve`는 종료 2와 일반 오류만 내고, 경로·traceback·비밀번호를
  출력하지 않았다. web-state는 0600이고 owner 검사 전에 시장 DB를 만들지 않았다.
- 8000은 전후 모두 비어 있었고 재확인에서 Wealthy serve process가 없었다. 첫 process 검사
  pattern이 검사 shell 자체를 잡은 false positive는 교정 pattern으로 즉시 재확인했다.
- 유료 서비스·cloud·공개 port·실제 주문·계좌 조회는 0회다.

## 12. 발견 결함·수정·재검증·종결 결과

X1과 관련 W4 우회는 V-11의 제품/C-14 동작/control 비교로 종결한다. C-14 동작에서만 WAL/SHM
삭제, deleted fd 2, 외부 session 1, restore 1, W4 200 우회가 재현됐고 C-15 제품은 세 번 모두
sidecar 유지, deleted fd 0, session/restore 4, W4 400 후 즉시 회복을 보였다. 새 결함은 없다.
이 종결은 실제 사용자 Secret Service·공급자·예약 장기 운영의 승인으로 확대하지 않는다.

## 13. 미완료·NOT RUN·사용자 설정

- 실제 Secret Service 격리 item read/write/delete와 앱 고정 master item
- 사용자 TTY 최초 관리자 생성, 실제 Chrome 로그인·로그아웃, backend 재시작 뒤 계정·전략
  설정·SAMPLE 원장/보고서 보존
- 사용자 본인 MANUAL_ACTUAL 자료 구분(사용하지 않으면 계속 `NOT RUN`)
- 실제 공급자, 계좌, 주문, systemd 등록·정시 실행, suspend/restart 장기 운영
- 실제 다른 UID 파일, 동시 동일 페이지 손상 시나리오, PostgreSQL 5건
- Windows·mobile·cloud, `.exe`

위 실제 localhost 항목은 사용자가 README 절차에서 로컬 keyring을 필요할 때만 잠금 해제하고
비밀을 TTY/Chrome에 직접 입력해야 한다. 기존 keyring을 초기화·삭제하지 않는다.

## 14. 변경·미변경 파일·릴리스 후보·삭제 후보

이번 변경은 `CODEX_WORKFLOW.md`, `CLAUDE_VALIDATION.md`, `AGENTS.md`, `CLAUDE.md`,
`DOCS_INDEX.md`, `README.md`, `.gitignore`, `tests/test_release.py`, 이 보고서,
`VALIDATION_LOG.md`의 새 C-16 및 보존 파일
`releases/C-20261011-15/wealthy-local-source-c15-final.zip`이다. 애플리케이션 소스,
`PROJECT_RULES.md`, 기존 C-/V- 기록, 사용자 DB·keyring, legacy volume은 변경하지 않았다.
기존 GUI/cloud legacy 파일은 삭제하지 않았다.

보존 후보는 코드 검증용 소스 ZIP이지 개인 DB·인증정보 backup이 아니다. 복사 당시 root
filesystem 여유는 3,706,089,472 bytes였다. 원본과 대상은 각각 3,341,217 bytes, mode 0600,
168 members, 압축 해제 5,049,530 bytes, SHA-256
`6951d3499a40ec9f05662b9c57d1b7d0189c88d7fed9cc68b5482ecfa7fca0f8`로 일치하고 원본은
삭제하지 않았다. 대상은 기존 파일이 없는 것을 확인한 뒤 만들었다. `.gitignore`의
`/releases/`와 exact allowlist로 다음 source ZIP에 중첩되지 않는다.

## 15. 기술팀에 공유할 사항

- X1 종결의 핵심 계약은 live SQLite connection 중 같은 프로세스가 DB inode를 별도
  open/close하지 않는 것이다. 실행 중 권한 검사는 path metadata/chmod만 사용한다.
- C-15 보존 ZIP은 V-11과 바이트 식별 가능한 기준선이다. 이후 문서·test 변경은 새 검증 없이
  그 `PASS`를 승계하지 않는다.
- localhost acceptance는 임시 분리 경로, 실제 Secret Service, TTY admin, loopback만 사용하며
  provider/systemd/order를 섞지 않는다.
- release builder는 exact allowlist이며 프로젝트 `releases/`를 source ZIP에 넣지 않는다.

## 16. 디자인팀에 공유할 사항

이번 작업에 화면·token·layout 변경은 없다. V-11 visible Chrome의 SAMPLE/MANUAL_ACTUAL 구분과
24-check 결과가 최신 시각 증거다. 실제 사용자 acceptance에서 비밀·전체 경로가 화면/toast에
나오지 않는지와 SAMPLE banner만 확인하며, 새 디자인 작업은 총괄팀 지시 전 시작하지 않는다.

## 17. 총괄팀·사용자 결정이 필요한 사항

1. C-15/V-11 종결과 보존 ZIP을 localhost 코드 기준 후보로 승인할지
2. 사용자가 README의 임시 경로 acceptance를 지금 수행할지와 결과 전달 형식
3. acceptance 뒤 안정적인 운영 data 경로로 전환할지(검증 없는 자동 이전 금지)
4. 실제 공급자와 user systemd 검증을 다음 별도 승인 범위로 열지
5. C-16 문서·release 제외 변경의 Claude 독립 검증 결과를 받은 뒤 운영 문서로 확정할지

## 18. 다음 단계 후보와 근거

1. Claude가 C-16의 릴리스 보존, 인계 규칙, focused release 검사와 localhost preflight 증거를
   독립 검증하고 새 V- 기록 및 총괄팀 프롬프트를 작성한다.
2. 사용자가 승인하면 README의 로컬 TTY/Chrome acceptance를 수행한다. 실제 Secret Service와
   fixture 결과를 혼동하지 않고 `PASS`/`NOT RUN`으로 돌려준다.
3. Codex가 새 V-와 사용자 결과를 읽고 이 문서에 새 보고서 ID/버전을 추가한다.

총괄팀의 다음 지시 없이 실제 공급자, systemd 등록, cloud/PostgreSQL/PWA, Windows/mobile,
주문 또는 다른 대규모 개발을 시작하지 않는다.

---

# C-17/V-12 미국 시장정보 검증·D1~D5 보정 부록

이 부록은 기존 `CR-20261011-LOCAL-01`과 `CR-20261013-LOCAL-02` 본문을 소급 수정하지
않고, C-17/V-12 결과와 그 후속 보정 상태만 연결한다. C-18은 Codex 자체검증 단계이며 새
Claude 검증 전에는 V-12의 독립 PASS 범위를 자동 승계하지 않는다.

## 1. 보고서 ID·작성시각·대상 C-/V- 기록

- 보고서 ID: `CR-20261014-MARKET-03`
- 작성시각: 2026-10-14 Asia/Seoul
- 연결 대상: `C-20261011-17` / `V-20261011-12`
- 후속 보정: `C-20261011-18`(Codex), `V-20261011-13` 독립 검증 대기
- 코드 식별: Git 저장소가 아니므로 C-18 최종 source ZIP의 크기·멤버 수·SHA-256을
  `VALIDATION_LOG.md`의 C-18 및 Claude 인계값으로 식별한다.

## 2. 범위 변경

C-17은 미국 공식 시장정보 메타데이터 목록과 백엔드 실행 중 자동 갱신을 추가했고, V-12는
핵심 기능을 통과시키면서 LOW D1~D5를 발견했다. C-18은 그 다섯 결함만 보정한다. cloud,
PostgreSQL, PWA/Push, 실제 주문, systemd 등록, Windows/mobile 구현은 재개하지 않았다.

## 3. 작업 전후 상태와 재사용 구현

- 전: UTF-16 DTD 우회, DNS 검사/접속 사이 재해석, 프로세스 간 cooldown 경합, 공급자 상태의
  `$` 노출, 자동상태 부분 문자열 오판정이 남아 있었다.
- 후: XML 선언 이벤트 차단, 검증 IP 고정 HTTPS, claim 후 state 재조회, 정확한 상태·시각 라벨을
  기존 FastAPI/React/SQLite 구조에 최소 변경으로 적용했다.
- 기존 계산·7절 보고·원장·전략·보안 저장·CLI/Tk 경계는 재사용하며 정책 로직은 바꾸지 않았다.

## 4. 요구사항별 구현·독립 검증 상태

| 항목 | 상태 | 근거 |
|---|---|---|
| C-17 시장정보 핵심 | **V-12 PASS** | 전체 317, 집중 110, frontend 43, Chrome 30 checks |
| D1~D3 backend 보정 | **Codex PASS / Claude PENDING** | UTF-16 3종, pinned DNS/TLS, 별도 프로세스 race 회귀 |
| D4~D5 web 보정 | **Codex PASS / Claude PENDING** | Vitest exact mapping, 실제 Chrome API↔화면 대조 |
| 기존 계산·위험·보고 회귀 | **Codex PASS / Claude PENDING** | C-18 전체 Python·frontend·CLI·Tk·release 실행 |

## 5. 실제 Ubuntu 실행 기준

운영 주소와 bind는 계속 `http://localhost:8000`, `127.0.0.1:8000`이다. C-18 브라우저 검증은
별도 `/dev/shm` DB, 정확히 한 fixture 사용자와 주입 memory keyring으로만 수행했다. 실제
Secret Service나 사용자 DB를 사용한 운영 승인이 아니다.

## 6. 디자인·캡처 결과

C-18 실제 `DISPLAY=:0` Chrome은 공급자 상태 `<details>`를 펼친 뒤 `$` 비노출과 정상
`다음 확인`/실패·대기 `재시도`를 검사했다. 캡처와 `SHA256SUMS`는
`/dev/shm/wealthy-d1-d5-c18/browser-headless-final` 및 `browser-visible-final`에 있으며,
각 26장이다. 새 레이아웃·색상·기능 디자인은 추가하지 않았다.

## 7. API·DB·데이터 계약 변경

API schema와 SQLite v3 schema, UTC microsecond 열은 변경하지 않았다. HTTPS 내부 연결만 DNS
검증 결과 IP로 고정하고 원래 hostname을 Host/TLS SNI/인증서 검증에 유지한다. 공급자 상태
API의 기존 `next_retry_at` alias를 화면에서 상태별 의미로 구분한다.

## 8. 투자정책·금융기록 보존

`PROJECT_RULES.md`와 기존 C-/V- 기록은 수정하지 않았다. 전략 비중, 자산배분, 위험 한도,
주문 금지와 SAMPLE/MANUAL 영역은 그대로다. 실제 계좌·개인 금융자료를 읽거나 만들지 않았다.

## 9. 공급자별 fixture·실연결 상태

C-17의 실제 제한 연결 기록(Fed 20, BLS 4×12, BEA 48; SEC NOT RUN/설정 필요)은 V-12가
검토했다. C-18은 외부 네트워크 0회이며 모든 D1~D3 검증은 fixture·socket 주입·tripwire로
수행했다. 따라서 C-18 결과를 새로운 공급자 가용성 확인으로 해석하지 않는다.

## 10. 예약·알림·백업·복원

실제 systemd 등록·해제, 정시 발화, lingering 변경은 없다. background lifecycle은 fixture
서버에서 `auto_start=True`로 실행했지만 모든 출처 cooldown을 미래로 두어 외부 fetch 0회를
확인했다. 기존 예약·알림·백업 구현은 변경하지 않았다.

## 11. 보안·비용·주문 비활성

DTD/ENTITY는 인코딩과 무관하게 parser 선언 단계에서 거부한다. DNS 답의 모든 주소를 공인
주소로 확인한 뒤 그 주소만 연결하며 TLS hostname/certificate 검증은 유지한다. 실제 keyring,
유료 서비스, 계좌 조회, 주문 호출은 0회이고 fixture 비밀번호는 mode 0600 파일로만 전달 후
해당 파일만 제거했다.

## 12. 발견 결함·수정·재검증 상태

- D1: UTF-16/LE/BE DTD·ENTITY를 Expat handler에서 일반 오류로 거부.
- D2: 최초와 redirect별 DNS를 한 번만 검증·고정하고 재해석 주소로 연결하지 않음.
- D3: claim 성공 직후 최신 state/cooldown/validator를 재조회하고 WAITING 시 claim 해제.
- D4: `$` 제거, `OK/NOT_MODIFIED=다음 확인`, 나머지 실패·대기=`재시도`.
- D5: `RUNNING`/`ENABLED_WHILE_BACKEND_RUNNING`/`STOPPED`를 exact mapping.

위 수정의 독립 종결 여부는 새 V-13에서 판정한다.

## 13. 미완료·NOT RUN

실제 사용자 Secret Service, 실제 SEC 연락정보, 새 공급자 실연결, systemd 정시·절전 복귀,
Windows·mobile·cloud·PostgreSQL, 실제 주문은 NOT RUN이다. `.exe`는 만들지 않았다.

## 14. 변경·미변경 파일·삭제 후보

C-18 애플리케이션 변경은 `asset_assistant/market_information.py`, `web/frontend/src/pages.tsx`,
회귀는 `tests/test_market_information.py`, `web/frontend/src/MarketPage.test.tsx`,
`tools/web_browser_smoke.mjs`, 문서는 이 부록과 새 C-18 로그다. `PROJECT_RULES.md`,
`CLAUDE_VALIDATION.md`, 기존 C-/V-, 사용자 DB/keyring과 legacy 파일은 미변경이다. 삭제 후보를
새로 실행하지 않았다.

## 15. 기술팀 공유사항

- feed HTTPS는 hostname을 IP로 치환하는 것이 아니라 연결 socket만 검증 IP에 고정한다.
- claim 전 state는 낙관적 확인일 뿐이며, 성공한 claim 아래 재조회한 cooldown/validator가
  수집 여부의 최종 근거다.
- parser·DNS·동시성 테스트는 실제 외부 네트워크 없이 재현 가능하다.

## 16. 디자인팀 공유사항

공급자 패널 문구의 의미만 바로잡았고 layout/token은 바꾸지 않았다. 정상 출처의 다음 주기는
`다음 확인`, 실패·설정 대기는 `재시도`이며 literal `$`는 표시되지 않는다. 자동상태는
`활성(백엔드 실행 중 자동)`과 실제 수집 중인 `실행 중`을 구분한다.

## 17. 총괄팀·사용자 결정 필요사항

1. V-13 독립 검증 뒤 D1~D5를 종결할지
2. C-17의 실제 공급자 제한 연결 결과를 장기 운영 승인과 분리해 유지할지
3. 실제 Secret Service·공급자·systemd 검증을 별도 승인 범위로 열지

## 18. 다음 단계 후보와 근거

Claude가 C-18을 별도 임시 환경에서 재현하고 새 V-13만 추가한다. Codex는 그 결과를 읽어 이
보고서의 다음 버전 부록을 작성한다. 총괄팀의 새 지시 전에는 대규모 기능, 실제 공급자 인증,
예약 등록 또는 주문 개발을 시작하지 않는다.

---

# C19 안정화 UI·수집 상태·보존 관리 부록

이 부록은 이전 본문과 `CR-20261014-MARKET-03`을 소급 수정하지 않고, V-13 이후 총괄팀의
C18 안정화 추가 지시 이행만 연결한다. 독립 Claude 검증 전에는 아래 Codex PASS를 제품 전체
승인으로 확대하지 않는다.

## 1. 보고서 ID·작성시각·대상 기록

- 보고서 ID: `CR-20261014-STABILITY-04`
- 작성시각: 2026-10-14 Asia/Seoul
- 기준 독립 기록: `C-20261011-18` / `V-20261011-13`
- 이번 구현 기록: `C-20261011-19`
- 다음 독립 검증: `V-20261011-14` PENDING

## 2. 범위

O1(반복 keyring 접근), O3(수집 전체 상한), O4(장기 보존/목록 확장성)와 추가 UI 요구
`UI-SEC-03`, `UI-MARKET-02`~`05`만 보완했다. 공급자·투자전략·주문·예약 등록·cloud 범위는
넓히지 않았다.

## 3. 작업 전후·재사용

기존 FastAPI/React/SQLite v3, 공급자별 claim/cooldown, 승인 디자인 token과 시장정보 화면을
재사용했다. 작업 전 passive credential 조회는 Secret Service를 반복 접근할 수 있었고, 수집
전체 상한과 보존 만료·미리보기가 없었다. 작업 후 시작 시 한 번 읽은 keyring 상태, 협력적
60초 상한, 실행/자료 분리 상태, 달력 1년 보존과 읽기 전용 DB 페이지 미리보기를 제공한다.

## 4. 요구사항·검증 상태

| 요구 | 구현 | Codex 검증 | Claude |
|---|---|---|---|
| UI-SEC-03 | 확인된 불가만 표시, 자동 재검사 없음 | fake keyring/API/Vitest PASS | PENDING |
| UI-MARKET-02 | 시도·성공·이번 결과·자료 기준·timeout 분리 | service/API/Vitest/Chrome PASS | PENDING |
| UI-MARKET-03 | 공급자별 부분 결과·이전 자료·retry | fixture 여섯 상태 Chrome PASS | PENDING |
| UI-MARKET-04 | 달력 1년·30일·정확 만료·추정/불가 | 경계/재수집 불변 PASS | PENDING |
| UI-MARKET-05 | 만료 metadata read-only keyset preview | SQL/API/UI/paging/no-delete PASS | PENDING |

## 5. 실제 Ubuntu 실행 기준

운영 bind와 주소는 계속 `127.0.0.1:8000`, `http://localhost:8000`이다. 실제 Chrome 검증은
별도 `/dev/shm` DB, 정확히 한 fixture 사용자와 주입 memory keyring에서 수행했고 사용자
Secret Service·DB를 읽지 않았다.

## 6. 디자인·캡처

전체 layout/token은 바꾸지 않았다. 기존 시장정보 화면 안에서 상태 카드·펼침 공급자 패널과
보존 보조 영역을 추가했다. C-19 headless/visible 캡처는
`/dev/shm/wealthy-c19-root.4ApEaa/browser-{headless,visible}`에 있으며 실행 요약, 정상/실패/
timeout/이전 자료/성공 없음 공급자 행, 보존 미리보기와 light/dark 화면을 구분한다. 모두
fixture이고 실제 공급자 상태가 아니다.

## 7. API·DB 계약

schema version은 v3 그대로다. status는 마지막 시도·성공, 현재 자료 기준, 이번 실행 결과,
실패/timeout/미시작 ID, 재시도와 공급자별 결과를 반환한다. `using_previous_data`와 이번 오류
코드는 해당 실행 overlay를 사용한다. 보존 API는 `GET /api/v1/market-information/retention`
하나이며 `EXPIRED` metadata만 snapshot/cursor keyset으로 반환한다. SQL은 `LIMIT+1`이고
snapshot 뒤 최초 수집 행을 제외한다. 삭제 method는 없다.

## 8. 투자정책·기록 보존

`PROJECT_RULES.md`, 금융원장·거래·보고서·추천·검증 기록, 기존 C-/V-를 바꾸지 않았다.
보존 만료는 시장정보 표시 기준일 뿐 투자자료 삭제 권한이나 공급자 지연 상태가 아니다.

## 9. 공급자 상태

이번 검증은 전부 fixture이다. 실제 공급자 fetch tripwire 호출은 0회다. SEC/KIS 연락정보나
API key를 만들거나 요청하지 않았고 기존 C-17 실연결 결과를 새 PASS로 갱신하지 않는다.

## 10. 예약·알림·백업·복원

실제 systemd 등록·해제, lingering, 정시 발화, 절전 복귀는 수행하지 않았다. 수집 상한은
백엔드 프로세스 안의 한 refresh에 적용될 뿐 PC 종료 중 실행을 보장하지 않는다. 기존
알림·backup/restore 코드는 회귀 테스트만 유지했다.

## 11. 보안·비용·주문

keyring은 서버 시작 때 configured flag와 SEC 연락정보를 한 번 cache하고 passive API polling은
다시 열지 않는다. 일반 Secret Service의 안전한 비대화형 probe를 보장할 수 없어
`safe_recheck_allowed=false`이며 재확인 버튼을 제공하지 않는다. 평문 fallback, 실제 주문,
계좌 조회, 유료·외부 공개 서비스는 없다.

## 12. 수집 상한·남은 한계

기본 전체 상한은 60초다. 검증 IP 연결, TLS, redirect, 응답 chunk 읽기에 남은 budget을
전달하고 뒤 공급자는 `NOT_STARTED_TIME_LIMIT`으로 분리한다. Python 프로세스 안에서 이미
진행 중인 OS DNS resolver 호출 자체는 강제 취소할 수 없으며 화면·API에 이 한계를 명시한다.

## 13. 보존 계산

최초 수집시각 + 달력 1년이며 2월 29일은 다음 해 2월 28일로 계산한다. 만료 전 정확히 30일
이내는 `EXPIRING_SOON`, 정확한 만료시각부터 `EXPIRED`다. 원래 최초 시각이 신뢰 불가하지만
수집시각이 유효하면 추정으로, 둘 다 불가하면 `CALCULATION_UNAVAILABLE`로 표시한다. 재수집은
최초 시각·만료일을 연장하지 않는다.

## 14. 변경·미변경·삭제

정확한 파일 목록은 C-19 기록을 따른다. 정책·Claude 지침·사용자 DB/keyring·legacy volume과
기존 산출물은 미변경이며 삭제 API/button/job도 없다. frontend `dist`와 `/dev/shm` 캡처는
검증 산출물이고 source ZIP 허용목록 밖이다.

## 15. 기술팀 공유사항

- keyring 상태는 startup prime 후 cache-only이며 명시적 등록·교체·삭제만 OS 저장소에 접근한다.
- 전체 수집 60초는 협력적 deadline이며 OS DNS 진행 중 취소 한계가 있다.
- 공급자 current-run overlay와 persisted 상태/자료시각을 구분하고 retry는 cooldown 계약을 따른다.
- 보존 API는 read-only `EXPIRED` preview이고 정보 항목/출처 기록 집계 단위를 분리한다.
- DB 페이지는 snapshot에 묶인 `LIMIT+1` keyset이며 전체 행을 Python으로 적재하지 않는다.

## 16. 디자인팀 공유사항

기존 token/layout을 유지하면서 상태의 의미만 추가했다. `부분 완료`, `전체 실패`, `시간 초과`,
`이전 정상 자료 사용`, `성공 기록 없음`을 서로 다른 문구로 표시하고 보존 미리보기는 같은
시장정보 화면의 보조 카드다. 삭제 실행 control은 없다.

## 17. 미완료·결정 필요

실제 Secret Service, 실제 공급자, 장시간 cadence/DNS hang, systemd 정시·절전 복귀,
Windows/mobile/cloud/PostgreSQL, 실제 주문은 NOT RUN이다. 총괄팀은 V-14 뒤 이번 안정화 범위를
종결할지와 실제 사용자/공급자 검증을 별도 승인할지 결정해야 한다.

## 18. 다음 단계

Claude가 별도 임시 경로와 memory keyring/fixture로 C-19를 검증해 새 V-14만 추가하고, 결함
유무와 무관하게 총괄팀 전달 프롬프트를 작성한다. 그 결과를 받은 뒤 Codex가 이 보고서에 새
버전 부록을 연결한다. 새 총괄팀 지시 전에는 대규모 개발을 시작하지 않는다.

---

# C20 수집 제한시간 재수정 부록

이 부록은 기존 본문과 `CR-20261014-STABILITY-04`를 보존하면서, C-19에 대한 독립 검증
`V-20261011-14`와 그 결함만 수정한 C-20을 연결한다. C-20의 독립 종결 판정은 V-15 전까지
대기 상태다.

## 1. 보고서 식별정보와 판정 범위

- 보고서 ID: `CR-20261014-STABILITY-05`
- 작성시각: 2026-10-14 Asia/Seoul
- 대상 기록: `C-20261011-19` / `V-20261011-14`
- 후속 구현: `C-20261011-20`
- 다음 독립 검증: `V-20261011-15` PENDING

V-14에서 O1(`UI-SEC-03`)과 O4(`UI-MARKET-04·05`)는 PASS했다. O3의 화면·상태 분리는
대부분 PASS했지만 느린 응답이 전체 제한시간을 우회하는 M1과 결과 분류 L1·L2, 화면 문구
L3가 확인되어 O3는 미종결이었다. C-20은 이 네 건만 수정했다.

## 2. M1·L1·L2·L3 수정

- M1: 검증 IP 연결, 원래 hostname의 Host/TLS SNI와 인증서 검증 구조를 유지한다. 정상 TLS
  연결 뒤 실제 socket file reference를 보존한 reader가 헤더와 본문의 매 저수준
  `readinto()` 전에 monotonic 절대 마감까지의 잔여시간을 timeout으로 다시 적용한다. 따라서
  한 바이트씩 보내 relative timeout을 반복 갱신하는 응답도 전체 마감을 넘기지 못한다.
- L1: `NEEDS_CONFIGURATION` 출처는 성공·실패·시간초과 집계에서 제외한다. 설정 필요 출처만
  있는 기존 결과는 계속 `NEEDS_CONFIGURATION`이다.
- L2: 공급자 cooldown을 전체 deadline보다 먼저 검사한다. 아직 수집 차례가 아닌 출처는
  `WAITING`이며 `NOT_STARTED_TIME_LIMIT`·미완료 목록에 들어가지 않고 저장 상태도 바뀌지 않는다.
- L3: 성공 이력이 없는 공급자는 `최근 성공 기록 없음`으로 한 번만 표시한다. 저장 자료나 API
  계약, 레이아웃과 디자인 token은 변경하지 않았다.

## 3. 재현·회귀 증거

비밀정보 없는 임시 자체서명 CA를 테스트 프로세스의 `/dev/shm` 안에서만 만들고 loopback TLS
서버로 slow header와 slow body를 각각 재현했다. client는 공인 DNS 검증 결과를 받은 것으로
주입하되 연결만 loopback test server로 돌렸고 SNI `apps.bea.gov`와 인증서 검증을 유지했다.
두 경로 모두 짧은 절대 deadline 안에서 `COLLECTION_TIMEOUT`으로 끝난다. 실제 외부 네트워크는
0회다.

C-20 자체 검증은 전체 Python 336건 중 330 PASS·6 SKIP, tmpfs hardlink 별도 1 PASS,
집중 7모듈 129/129, frontend 53/53, build·compileall·pip check·release 12/12, CLI SAMPLE,
Tk DISPLAY smoke, Chrome headless·visible을 통과했다. Chrome fixture는 정확히 한 사용자와 memory
keyring, `127.0.0.1:8000`, 수집 tripwire를 사용했으며 실제 공급자 호출은 0회다. 최종 수치와
산출물 식별값은 `C-20261011-20`과 Codex 인계값을 따른다.

## 4. API·DB·정책 영향

API schema, SQLite v3 schema, UTC microsecond 열, 보존 계산과 read-only 미리보기는 바뀌지
않았다. 투자 배분·위험 한도·7절 보고·주문 금지와 O1 keyring cache 경계도 그대로다. 새
공급자·주문·삭제·예약 등록 기능이나 dependency는 추가하지 않았다.

## 5. 기술팀 공유사항

- socket의 한 번 설정한 상대 timeout은 slow drip 전체 상한이 아니다. 헤더와 본문의 각 raw
  read에 같은 absolute monotonic deadline의 잔여시간을 적용해야 한다.
- urllib가 header 처리 뒤 connection socket을 닫아도 body reader가 유효하도록 실제
  `socket.makefile(..., buffering=0)`의 file-reference 수명을 보존한다.
- `NEEDS_CONFIGURATION`, `WAITING`, `NOT_STARTED_TIME_LIMIT`, `TIMED_OUT`은 실행 집계에서
  서로 다른 상태다. 설정 필요와 cooldown 대기는 실패나 미시작으로 바꾸지 않는다.
- OS resolver 호출 자체의 장시간 정지는 기존 문서화된 한계로 남고 이번 socket read 수정의
  종결 범위가 아니다.

## 6. 디자인팀 공유사항

기존 시장정보 화면·카드·색상·반응형 구성은 그대로다. 성공 이력이 없는 행의 중복 문구만
`최근 성공 기록 없음`으로 정정했고, 정상·부분 실패·전체 실패·시간 초과·이전 자료·성공 없음의
기존 여섯 상태 구분을 유지했다.

## 7. 미실행·남은 위험·결정 대기

실제 Secret Service, 실제 공급자/SEC 연결, OS DNS 장기 hang, 실제 serve 관리자 생성,
systemd 등록·정시 발화·절전 복귀, PostgreSQL, Windows·mobile·cloud와 주문은 NOT RUN이다.
`.exe`는 만들지 않았다. V-15는 M1 slow TLS header/body와 L1~L3를 독립 재현하고 기존
O1·O4·W1~W4·X1 및 금융 회귀를 확인해야 한다. 총괄팀은 V-15 결과 뒤 O3와 C-20 릴리스 후보의
종결 여부를 결정한다.

---

# C20/V15 종결 및 내부 PAPER 개발 준비 부록

이 부록은 기존 본문과 모든 안정화 부록을 보존한다. C20/V15 종결을 실제 사용자 환경·공급자·
예약 운영 승인으로 확대하지 않으며, C-21은 문서 설계만 수행했다.

## 1. 보고서 ID·작성시각·대상 기록

- 보고서 ID: `CR-20261014-PAPER-DESIGN-01`
- 작성시각: 2026-10-14 Asia/Seoul
- 종결 연결: `C-20261011-20` / `V-20261011-15`
- 신규 설계 기록: `C-20261011-21` / 다음 독립 검증 `V-20261011-16` 예정
- 정책 원본 SHA-256: `04a44f468ad56bee3f85f51174e05269b8daff576ce89bd6c71eca1e4fc5af20`

## 2. C20/V15 판정

V-15는 M1·L1·L2·L3를 독립적으로 CLOSED(PASS)했고 새 제품 결함을 찾지 않았다. 기존
O1·O3·O4와 W1~W4·X1·D1~D5 보호도 해당 fixture·시험 범위에서 유지됐다. 따라서 C20 수정
범위는 종결한다. 실제 Secret Service, 실제 사용자 `serve`/관리자 생성, 실제 공급자 장기
운영, systemd 정시 발화, Windows/mobile/cloud/PostgreSQL 및 주문은 계속 NOT RUN이다.

## 3. 릴리스 후보 보존

원본 `/dev/shm/wealthy-c20-root.H56XF1/wealthy-local-source-c20-final.zip`의 mode 0600,
3,475,641 bytes, 173 members, SHA-256
`91e842acd174c4a1053e807172cdb6d62f7a324aa59516eda113f2f571051752`를 확인했다. 기존 대상이
없는 상태에서 `releases/C-20261011-20/wealthy-local-source-c20-final.zip`에 복사했고 target
directory 0700, file 0600, owner `ww`, size/hash 동일을 확인했다. 원본은 삭제하지 않았다.
이는 C20 **소스 검증 후보**이며 개인 DB·인증정보 backup이 아니다. `releases/`는 새 source
ZIP의 exact allowlist 밖에 계속 둔다.

## 4. 작업 전후 상태와 재사용 구현

작업 전 현행 앱은 localhost React/FastAPI, 두 SQLite, 단일 사용자, 기존 8전략 목표/후보점수,
7절 보고, 공식 시장정보 metadata와 일부 read-only 공급자 adapter를 갖고 있었다. 내부 PAPER
cash/ledger/candidate set/approval/reservation/fill/daily cap/error notebook은 전혀 없었다.
작업 후 runtime은 동일하며, 기존 `docs/FUTURE_VALIDATION_DESIGN.md`를 중복 없이 확장해 이
기능들의 논리 계약과 완료조건을 작성했다. `MANUAL_PAPER_SNAPSHOT`, KIS paper 인증환경,
`market_information`, `candidate_assessments`를 자동 PAPER로 오인하지 않도록 정의했다.

## 5. 요구사항별 구현·검증 상태

`docs/REQUIREMENTS_TRACEABILITY.md`의 `PAPER-*`, `UI-PAPER-*`, `SAFE-PAPER-01` 표가 원본이다.
기존 localhost·시장정보·안전기반은 최신 V-15 범위에서 PASS지만, 신규 PAPER 행은 전부
`설계 완료 / 미구현·자료/승인 대기`다. 이번 작업을 “자동 모의투자 완성”이나 PAPER PASS로
기록하지 않는다.

## 6. 운영 주소·Ubuntu 실행

현행 운영 기준은 Ubuntu 24.04, Chrome, `http://localhost:8000`, Uvicorn `127.0.0.1`이다.
이번 문서 작업에서는 server, Secret Service, provider, systemd를 실행하지 않았다. 실제 실행
명령과 사용자 입력 절차는 기존 `README.md`를 유지하며 PAPER endpoint나 route는 없다.

## 7. 디자인·캡처 상태

기존 shell/card/table/chart, light/dark, breakpoint와 접근성 규칙을 재사용하는
`UI-PAPER-01~10` 및 7개 세로 카드 계약을 `WEB_DESIGN_SPEC.md`에 설계 전용으로 추가했다.
UI code와 승인 PNG는 바꾸지 않았고 새 캡처도 만들지 않았다. 따라서 시안 대비 실제 PAPER
화면 검증은 NOT RUN이다.

## 8. API·DB·전략 데이터 계약

`docs/WEB_API.md`에는 `/api/v1/paper/*`를 **미구현 제안**으로만 기록했다. 논리 model은
append-only ledger, strategy/budget version, US session cap, candidate/proposal, 두 approval,
cash/cap reservation, virtual fill, reconciliation, observation, notification이다. 금액은 Decimal
string, 시각은 UTC µs+aware ISO, 수량은 정수 1주다. 현 SQLite schema·OpenAPI·strict allowlist와
backup format은 바꾸지 않았다. 현행 candidate scoring 8종과 새 PAPER 8종은 자동 mapping하지
않는다.

## 9. 투자정책·승인 증거 보존

`PROJECT_RULES.md`는 수정하지 않았다. 4,000,000원, 20% 일한도, 300,000원 이상 별도 승인,
24시간, 후보 1~4와 재승인, 영역분리 등 이번 사용자 확정은 설계에 반영했다. 정수 1주·비용
반영·개별주 5%는 직접 원문을 확인했다. 최대 12종목, 전체 MDD 자동정지, 산업 cap, 구체
비용·재생성 횟수·보관기간 등은 기술팀 승인 이력 보고만 있고 원문/값이 없어 `분류 2+4`,
실행값 null·차단으로 기록했다. 제안 전략 threshold는 분류 3이며 정책으로 승격하지 않았다.

## 10. 공급자별 fixture·실연결 상태

공식 문서만 열람했고 금융 API 호출·가입·로그인·키 발급은 0회다. SEC/BLS/BEA/Fed/ECB와
SEC N-PORT는 조건을 지킨 설계 후보, KIS 해외 시세는 사용자 인증·시세권한·저장권 확인 뒤
조건부, 거래정지 RSS는 약관 고정 뒤 조건부다. 최신 기업행동·ETF 구성·총수익 benchmark·
일반뉴스는 무료 운영 요구를 충족하는 공급자를 확인하지 못했다. FRED API는 현재 저장·cache
제한 때문에 영구 local DB에 부적합하다고 판정했다. 세부 공식 URL·한도·권리는
`docs/DATA_SOURCES.md`의 2026-10-14 표에 있다.

## 11. 예약·알림·백업·복원 결과

30분 갱신, 15시 수집, 16시 보고를 설계 제약으로 유지하되 PAPER scheduler는 만들지 않았다.
사용자 중지 지속, PC off 소급체결 금지, missed report와 fill 분리, 중복 claim/idempotency를
명세했다. 내부 알림함만 허용하고 PAPER popup·OS·WebPush를 금지했다. 새 DB/table이 없어
backup/restore 실행·변경은 없고, 향후 strict schema/count/hash와 W2/W4/X1을 필수 검증으로
연결했다.

## 12. 보안·비용·주문 비활성

실제 key/token/account/personal data/OS keyring, 금융 API, 계좌조회·KIS 주문, systemd,
cloud/PostgreSQL/PWA/Push를 사용·변경하지 않았다. 내부 PAPER executor 제안도 network와 broker
method를 갖지 않는다. 기본 인프라 비용 0원 조건을 유지하고 승인 없는 유료 공급자를 채택하지
않았다. 구체 가상비용은 미확정이라 실행을 차단한다.

## 13. 상태기계·전략 검증 초안

후보군 승인 전 reservation 없음, 승인 뒤 cash+20% cap 원자 예약, 30만원 이상 두 번째 승인,
결과 미확인 시 잠금·대조, 선순위 확정 불가만 후순위, 재생성 항상 새 승인을 설계했다.
8전략은 목적·입력·exact weight·filter·신호·주기·동점·비용·누락·독립시험을 수치 제안으로
작성했다. point-in-time, correction, cutoff ±1µs, missing 비재정규화, survivorship/look-ahead
차단을 완료조건으로 두었다. 총수익/기업행동/ETF 자료가 없으면 관련 전략은 UNAVAILABLE이다.

## 14. 변경·미변경 파일·삭제 후보

변경 문서는 `DOCS_INDEX.md`, `WEB_DESIGN_SPEC.md`, `docs/WEB_API.md`,
`docs/REQUIREMENTS_TRACEABILITY.md`, `docs/DATA_SOURCES.md`,
`docs/FUTURE_VALIDATION_DESIGN.md`, 이 보고서와 새 `VALIDATION_LOG.md` C-21 항목이다.
소스·테스트·dependency·DB·`PROJECT_RULES.md`·승인 PNG·운영 설정은 미변경이다. 대규모 삭제나
legacy 정리는 없으며 삭제 후보도 새로 집행하지 않았다.

## 15. 기술팀 공유사항

- 첫 구현은 network/UI 없는 Decimal domain engine, 상태기계와 property test(P1)로 제한한다.
- local web-state는 strict table allowlist이므로 table만 추가하면 기동이 fail closed한다.
  schema version, migration rollback, online backup bundle을 함께 설계해야 한다.
- W2/W4/X1의 owner·sidecar·single-owner·SQLite fd 잠금과 D1~D3/O3의 parser/DNS pin/deadline/
  claim 재확인을 모든 새 repository·collector·runner에 적용한다.
- 전략 score, PAPER budget/actual, 코어·위성 risk allocation은 네 개의 다른 축이다.
- 무료 자료 부족을 다른 factor weight 재분배로 숨기지 않고 `UNAVAILABLE`로 둔다.

## 16. 디자인팀 공유사항

- 현행 shell/token/layout을 유지하면서 scope badge, daily cap, exact run state, 후보 1~4,
  두 승인, evidence, regeneration, strategy/budget, internal inbox, restart 안내를 추가하는 초안이다.
- `PAPER 거래 기록 완료`를 쓰고 broker 체결과 혼동되는 문구는 금지한다.
- 기존 7절 순서·목표 의미를 유지하고 PAPER 성과·배당을 실제 목표에 합치지 않는다.
- 실제 ledger/API가 생기기 전 mock 종목·금액·수익률·빈 chart를 화면에 두지 않는다.

## 17. 총괄팀·사용자 결정 필요사항

PAPER DB 위치/backup version, 현행/신규 8전략 mapping, 비용·FX 판정, 12종목과 재생성 횟수,
추가매수·유동성·변동성·산업 cap, 전체 MDD 정지/회복, 위성 손실 분모·기간·재개, 개선 검증기간,
backup/알림 보관, 미국 calendar, KIS 저장권과 ETF/기업행동/총수익 결손 처리, 승인 재인증을
결정해야 한다. 기술팀 승인 원문이 있으면 우선 제공해 분류 2를 대조해야 한다.

## 18. 다음 단계 후보와 근거

먼저 Claude가 새 V-20261011-16에서 코드와 gap 표, 승인근거, 상태 우회, 전략 미래정보,
공급자 권리, UI/API 문서 일치, 금지 변경 0건을 문서 독립 검증한다. 총괄팀은 V-16을 검토해
결정항목을 확정한 뒤 P1 범위를 별도 지시할 수 있다. 그 전에는 자동 PAPER 구현·공급자 연결·
운영 적용을 시작하지 않는다.

# C21/V16 PAPER 설계 보완 부록

이 부록은 위 `CR-20261014-PAPER-DESIGN-01`과 기존 본문을 보존한 채 Claude
`V-20261011-16`의 문서 결함 F1~F8에 대한 Codex 설계 보완을 연결한다. runtime 구현이나
PAPER 운영 승인이 아니다.

## 1. 식별정보와 판정 범위

- 보고서 ID: `CR-20261014-PAPER-DESIGN-02`
- 작성시각: 2026-10-14 Asia/Seoul
- 입력 기록: `C-20261011-21` / `V-20261011-16`
- 보완 기록: `C-20261011-22`; 독립 검증 예정: `V-20261011-17`
- 상태: 문서 보완 완료 / PAPER 소스·API·DB·UI·scheduler·주문은 미구현·비활성

## 2. F1~F8 보완 결과

| 항목 | 설계 보완 | 실행 상태 |
|---|---|---|
| F1 | 8전략과 보통주·ETF의 core/satellite 귀속 초안을 작성했다. 전략명이 위험분류를 바꾸지 않으며 미분류는 위성 분자에 포함하되 신규매수를 차단한다. 5%·ETF 40/50%·위성 10% 분모는 PAPER-only 사용자 결정으로 두고 실제 자산 유입을 금지했다. | 미구현 / 분모·상품 승인 대기 |
| F2 | SELL lot/수량 예약과 `available_qty = remaining_qty - active_sell_reservations`, UNKNOWN 예약 유지, 동시매도 경계를 계약에 추가했다. | 미구현 |
| F3 | 30만원 판정액을 `(portfolio, purpose, instrument, side, US session)`으로 합산하고 group hash 승인·분할 경계를 추가했다. | 미구현 |
| F4 | 내부 PAPER는 KIS 모의환경인 단계 C의 선행·보완자료일 뿐 대체·통과 증거가 아니며 D1 근거로 자동 사용하지 않는다. 월 승인예산의 PAPER 매수·매도 적용은 사용자 결정 전 차단한다. | 정책 결정 대기 |
| F5 | 같은 종목의 KST intent date 또는 US session date 중 하나라도 겹치면 새 의도를 차단하는 보수적 초안을 정했다. | 미구현 / 예외 확정 대기 |
| F6 | `MISSED_WINDOW`, `TEMPORARILY_HELD`, `USER_STOPPED`, candidate set `REGENERATION_REQUIRED`와 1순위 승인 거절 뒤 무자동 후순위·만료/재생성 경로를 정의했다. | 미구현 |
| F7 | 필수 metric 하나라도 결측이면 종목 score 전체를 `UNAVAILABLE`로 두고 후보에서 제외하며 0점·재가중·대체값 사용을 금지했다. | 미구현 |
| F8 | 카드 4 제목을 `실제 배당과 배당 전환 능력`으로 유지하고 `PAPER 모의 배당` 배지만 추가하는 표현으로 통일했다. | 디자인 계약 / UI 미구현 |

관찰사항인 30분 갱신과 현행 공급자 cadence 차이도 gap 표에 추가했다. scheduler tick이 30분이어도
3,600초 공급자 cooldown을 우회하지 않고 중간 tick은 `WAITING`으로 남기는 계약이다.

## 3. 정책·데이터·API 영향

`PROJECT_RULES.md`는 바꾸지 않았다. 귀속표는 정책 원문을 대신하지 않는 fail-closed 초안이고,
core 후보도 매수 승인이 아니다. PAPER의 위험 한도에는 PAPER 안에서 검증한 분자·분모만 쓰며
실제 계좌·보호저축·초기자산 선언값과 합산·상계하지 않는다. 내부 PAPER 4,000,000원과 전략별
budget도 월 승인 신규 투자 예산으로 간주하지 않는다.

`docs/WEB_API.md`의 `/paper/*`는 계속 제안·미구현이다. proposal 응답에 향후 필요한 귀속,
분모 준비상태, 두 날짜 key, cash/SELL 수량 예약, exact state를 명시했을 뿐 router/OpenAPI/DB를
추가하지 않았다. broker 주문·계좌조회·정책 override·PAPER/실제 merge 금지는 그대로다.

## 4. 기술팀·디자인팀 공유사항

기술팀에는 P1 전에 PAPER-only 분모, 단계 C 관계, 월 예산, 동일 종목 날짜 key를 먼저 확정하고,
SELL 동시예약·집계승인·상태전이를 property/concurrency test로 고정해야 한다는 점을 공유한다.
필수 metric 결측은 숫자 score가 아니며 후보 수가 4보다 적어도 정상이다.

디자인팀에는 카드 4 원문 제목을 바꾸지 말고 `PAPER 모의 배당` 배지로 scope를 나타내며,
승인 카드에 proposal 단건이 아닌 group 집계액과 SELL 예약수량을 표시해야 한다는 점을 공유한다.
held, user stopped, missed window, regeneration required를 같은 일반 오류로 합치지 않는다.

## 5. 미변경·미검증·결정 요청

소스·테스트·dependency·DB·정책 원본·Claude 지침·기존 C-/V- 기록과 보존 C20 ZIP은
변경하지 않았다. 실제 key/account/keyring/provider/KIS/order/server/systemd/cloud/
PostgreSQL/PWA/Push는 실행하지 않았다. PAPER runtime, UI, 공급자, 실제 예약·동시성은 모두
`NOT RUN`이며 문서 검토 결과를 기능 PASS로 확대하지 않는다.

총괄팀·사용자는 PAPER 5%·ETF 40/50%·위성 10% 분모, 내부 PAPER와 단계 C/D1 관계, PAPER
월 승인예산과 매도 적용, KST/US 날짜 규칙의 예외를 결정해야 한다. 먼저 Claude가
`V-20261011-17`에서 F1~F8 문서 계약과 비-Markdown 변경 0을 독립 검증해야 하며, 그 결과와
결정 전에는 P1 구현을 시작하지 않는다.

# C22/V17 PAPER 설계 정합성 보완 부록

이 부록은 기존 본문과 `CR-20261014-PAPER-DESIGN-01/02`를 기록 당시 내용 그대로 보존하면서,
Claude `V-20261011-17`에서 확인한 G1~G4의 정정 계약을 연결한다. 특히 아래 F3 정정은
`CR-20261014-PAPER-DESIGN-02`의 역사적 행을 고쳐 쓰지 않고 이 부록으로 대체 설명한다.

## 1. 식별정보와 범위

- 보고서 ID: `CR-20261014-PAPER-DESIGN-03`
- 작성시각: 2026-10-14 Asia/Seoul
- 입력 기록: `C-20261011-22` / `V-20261011-17`
- 보완 기록: `C-20261011-23`; 독립 검증 예정: `V-20261011-18`
- 상태: G1~G4 문서 보완 / PAPER 소스·테스트·의존성·DB·UI·주문은 미구현·비활성

## 2. V-17 결함별 정정

| 항목 | 정정 계약 | 실행 상태 |
|---|---|---|
| G1 | 후순위 gate는 영구 정책 부적격·검증된 거래정지·사용자 승인 영구 원인 목록으로 선순위가 `DEFINITIVELY_UNEXECUTABLE`인 경우만 연다. 세션·실행창 종료는 terminal `MISSED_WINDOW`이며 가격조건 미충족과 함께 후순위 사유가 아니다. 가격조건의 영구 원인 기준은 사용자 결정 전 차단한다. | 설계 보완 / 미구현 |
| G2 | 카드 5·6 원문 제목을 각각 `위성 손실 현황`, `제안 주문안과 이유`로 유지하고 카드 6 PAPER 범위는 `PAPER 거래 제안` 배지로만 구분한다. | 디자인 계약 / UI 미구현 |
| G3 | 이전 부록 F3 요약의 정확한 집계 키는 `(portfolio, root purpose, instrument, side, US session)`이다. stable instrument와 purpose version이 아닌 안정된 root를 사용하며 descendant purpose version을 같은 group에 합산한다. | 요약 정정 / 기능 미구현 |
| G4 | `RUNNING` 정상 완료는 `COMPLETED`; 결과 미확인은 `RECONCILIATION_REQUIRED`, 명시 대조 완료는 `RECONCILED`다. `COMPLETED/MISSED_WINDOW/RECONCILED`는 해당 run ID의 terminal이며 이후에는 모든 gate를 재검증한 새 run ID만 `READY`가 될 수 있다. | 설계 보완 / 미구현 |

## 3. 기술팀·디자인팀 공유와 결정 요청

기술팀은 session/window 만료를 rank 우회 신호로 쓰지 말고, 영구 원인 code와 사용자 승인 목록
version을 snapshot으로 보존해야 한다. 실행 상태기는 terminal run을 되살리지 않으며 대조가
끝나기 전 cash/SELL reservation을 유지한다. 가격조건을 영구 원인으로 인정할 객관적 기준은
총괄팀·사용자 결정 전 null이고 fail-closed다.

디자인팀은 카드 원문 제목과 PAPER 배지를 분리하고, `MISSED_WINDOW`,
`RECONCILIATION_REQUIRED`, `RECONCILED`, `COMPLETED`를 같은 일반 완료/오류 문구로 합치지 않는다.
세션 종료 화면에서 후순위 action은 비활성이고 새 run에는 전체 재검증 필요 안내를 표시한다.

이 부록은 PAPER 구현 승인이나 기능 PASS가 아니다. 실제 key/account/keyring/provider/KIS/order,
server/systemd/cloud는 실행하지 않았으며 PAPER runtime과 UI는 `NOT RUN`이다. 총괄팀은 가격조건을
영구 원인 목록에 포함할지와 그 판정·승인 기준을 별도로 결정해야 한다. 먼저 Claude가
`V-20261011-18`에서 이 정정과 기존 F1~F8 회귀를 독립 검증해야 하며 그 전에는 P1 구현을
시작하지 않는다.

# C23/V18 PAPER 실행 계약 보완 부록

이 부록은 기존 본문과 `CR-20261014-PAPER-DESIGN-01/02/03`을 당시 기록 그대로 보존하면서,
Claude `V-20261011-18`의 H1과 2026-10-15 총괄팀 보완 지시를 연결한다. 과거 부록의 일반
`검증된 거래정지`와 가격조건 영구 원인 가능성 문구는 이 부록의 더 엄격한 계약으로 정정해
읽는다.

## 1. 식별정보·대상 기록

- 보고서 ID: `CR-20261015-PAPER-DESIGN-04`
- 작성시각: 2026-10-15 00:08 Asia/Seoul
- 확인 기록: `C-20261011-21` / `V-20261011-16`, `C-20261011-22` /
  `V-20261011-17`, `C-20261011-23` / `V-20261011-18`
- 이번 Codex 기록: `C-20261011-24`; 독립 검증 예정: `V-20261011-19`
- 범위: 설계 Markdown 정합성만. PAPER 소스·테스트·dependency·DB·API·UI·운영은 미구현·비활성

## 2. G1~G4·H1 최종 정정 계약

| 항목 | 정정·확인 결과 | 구현 상태 |
|---|---|---|
| G1 | 세션·실행창 종료는 `MISSED_WINDOW`, 가격조건 미충족·이탈과 일시 자료 부족은 실행창 안에서 `TEMPORARILY_HELD`다. 일반 거래정지는 해제 가능하므로 일시 보류가 기본이며, 공식 근거·reason code·승인된 영구 원인 목록으로 확정된 거래 불가만 후순위 gate를 연다. 승인 대기·거절·결과 미확인·사용자 중지도 후순위 사유가 아니다. | 문서 보완 / 미구현 |
| G2 | 7개 카드 제목·순서를 재대조했다. 카드 5는 `위성 손실 현황`, 카드 6은 `제안 주문안과 이유`이고 PAPER 의미는 카드 4·6의 별도 배지로만 표시한다. 추가 수정 없이 기존 계약을 유지했다. | 문서 계약 유지 / UI 미구현 |
| G3 | 승인 집계 키는 `(portfolio_id, stable_root_trade_purpose_id, stable instrument_id, side, US_session_date)`다. 후보군·제안·purpose 하위 version과 새 후보군 승인으로 root를 바꾸지 않는다. 새 root는 실제 경제적 목적 변경의 별도 사용자 승인·audit·중복/분할 우회 검사를 모두 요구한다. | 문서 보완 / 미구현 |
| G4 | 모든 run 상태의 진입, 출구 event, 승인 유효성, cash·SELL 수량·일한도 예약, restart·결과 미확인 처리를 lifecycle 표로 고정했다. 사용자 중지·승인 거절 뒤 자동 재개와 미확인 결과의 임의 성공·실패·예약 해제를 금지했다. | 문서 보완 / 미구현 |
| H1 | `ExecutionRun`은 한 PAPER portfolio·한 US session의 불변 proposal 묶음이다. run `MISSED_WINDOW`는 영속 시도 0건에만 쓰고, 시도 1건 이상 뒤 결과가 모두 확정되면 `COMPLETED(FULL/PARTIAL)`, 하나라도 미확인이면 `RECONCILIATION_REQUIRED`다. 미시도 proposal만 proposal-level `MISSED_WINDOW`이며 run 상태로 예약을 일괄 해제하지 않는다. | 논리 계약 보완 / 미구현 |

## 3. 실행·승인 데이터 계약

`ExecutionRun`은 run ID, portfolio/session 범위, 불변 included proposal ID/version 목록과 hash,
실행창·시작·종료 UTC µs, distinct 영속 attempt event/count, attempted proposal IDs,
`completion_kind`, terminal reason을 보존한다. 동일 idempotency retry는 count를 늘리지 않으며
restart는 DB event에서 목록/hash/count를 복구한다. `COMPLETED(PARTIAL)`은 orchestration이
정리됐다는 뜻이고 모든 proposal 체결 성공을 뜻하지 않는다.

승인 집계의 stable root는 앱의 불투명 식별자이며 사용자가 임의로 새 값을 넣을 수 없다. 같은
경제적 목적의 candidate set·proposal 재생성은 descendant로 누적한다. 승인 거절은 후순위 자동
실행이나 같은 run 자동 재개를 만들지 않으며, 결과 미확인 중에는 cash·SELL lot·일한도 예약을
유지한다.

## 4. 승인 근거 자료와 미확정 결정 분류

총괄팀이 언급한 기술팀 PAPER 최종 보고 원본은 현재 저장소·확인 가능한 첨부에 없다. 사용자가
대조 자료를 제공할 때는 release 제외 로컬 경로 `user_data/approval_evidence/paper/`에 보고 원본,
인용 부록, 인용된 사용자 승인 원문, 파일명·SHA-256·교차참조 manifest를 둔다. 권장 권한은
디렉터리 `0700`, 파일 `0600`이고 키·계좌·개인 금융자료를 포함하지 않는다. 기술팀 보고의 승인
인용은 직접 확인한 사용자 승인 원문과 구분하며, 원문 대조 전 `effective_value=null`과
fail-closed를 유지한다. 이미 직접 확인한 승인 결정을 다시 묻지 않는다.

기존 결정 목록은 다음 세 축으로 분리했다.

- 영향받는 P1 성공 경로 전에 필요한 사용자 정책 결정
- Codex가 대안을 제안하고 총괄팀이 동결할 기술 설계 선택
- 공급자 연결·운영 활성화 전에 필요한 권리·값·운영 결정

미확정값이 있어도 향후 별도 승인된 순수 엔진은 nullable field와
`POLICY_INPUT_REQUIRED/UNAVAILABLE/DISABLED` 상태를 검증할 수 있다. 다만 값을 추정해
`READY`로 만들거나 운영을 활성화할 수 없으며, 이 부록은 P1 착수 승인이 아니다.

## 5. 변경·미변경 및 팀 공유

변경 문서는 `DOCS_INDEX.md`, `WEB_DESIGN_SPEC.md`, `docs/FUTURE_VALIDATION_DESIGN.md`,
`docs/WEB_API.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, 이 보고서와 새 validation append뿐이다.
`PROJECT_RULES.md`, `CLAUDE_VALIDATION.md`, 소스·테스트·dependency·DB와 기존 C-/V-/부록은
수정하지 않는다.

기술팀 공유사항은 session-batch run 식별자·영속 attempt event·proposal별 reservation,
stable-root anti-splitting, 공식 근거 기반 확정 부적격 gate다. 디자인팀 공유사항은 run과
proposal 상태 분리, `완료(부분)`, attempt/resolved/missed count, 일반 거래정지의 `일시 보류`,
원문 7카드 제목과 PAPER 배지 분리다.

실제 key/account/keyring/provider/KIS/order/server/systemd/cloud는 실행하지 않았고, PAPER
runtime·UI·DB migration·동시성은 모두 `NOT RUN`이다. 총괄팀의 다음 지시 전에는 P1 구현을
시작하지 않는다. Claude는 `V-20261011-19`에서 G1~G4·H1과 F1~F8 문서 회귀, Markdown 외 변경
0을 독립 검증해야 한다.

# C24/V19 PAPER stable-root 계약 보완 부록

이 부록은 기존 본문과 `CR-20261015-PAPER-DESIGN-04`까지를 당시 기록 그대로 보존하면서,
Claude `V-20261011-19`의 J1을 설계 계약에서만 보완한 결과를 연결한다.

## 1. 식별정보·범위

- 보고서 ID: `CR-20261015-PAPER-DESIGN-05`
- 작성시각: 2026-10-15 00:36 Asia/Seoul
- 대상 기록: `C-20261011-24` / `V-20261011-19`
- 이번 보완 기록: `C-20261011-25`; 독립 검증 예정: `V-20261011-20`
- 범위: J1의 논리 데이터·제안 API·추적 계약 정합성만. PAPER 소스·테스트·dependency·DB·API·UI·운영은 계속 미구현·비활성

## 2. J1 정정 계약

`TradePurpose`는 불변 `stable_root_trade_purpose_id`, `purpose_version`,
`parent_trade_purpose_version_id`로 이어지는 descendant 계보를 소유한다. 새 root 발급에는
`new_root_user_approval_ref`, `new_root_audit_ref`, `root_overlap_split_check_result`가 모두 필요하다.
같은 경제적 목적의 후보 대체·재생성·재승인은 같은 root의 새 version이고, 경제적 목적이 실제로
바뀌어 별도 승인을 받은 경우만 새 root를 발급할 수 있다. 임의 root로 300,000원 집계 누적을
초기화할 수 없다.

CandidateSet·TradeProposal 제안 계약은 목적 ID/version/stable root를 함께 보존하고,
candidate-set은 parent purpose-version ref도 반환한다. 큰 거래 승인은
`(portfolio_id, stable_root_trade_purpose_id, stable instrument_id, side, US_session_date)`의
descendant version 전체와 불변 group hash를 대상으로 한다. 추적표 `PAPER-06`도 같은 필드와
집계 키를 단일 용어로 사용한다.

필수 경계 검증은 다음 세 가지다.

- 같은 경제적 목적 v1→v2 재생성·재승인은 parent 연결과 stable root를 유지한다.
- 사용자 승인 ref가 없는 새 root 발급은 거부한다.
- 승인된 새 root라도 기존 root와 stable instrument·side·US session이 같으면 분할 우회를
  탐지하고 누적액 초기화를 허용하지 않는다.

## 3. 검증 상태·팀 공유

`V-20261011-19`에서 종결된 H1·G1·G2·G4와 기존 F1~F8은 이번 수정의 회귀 대상으로 유지한다.
문서 구조·release·C20 보존 ZIP 대조와 append-only prefix 증거는 `C-20261011-25`에 기록하고,
Claude가 `V-20261011-20`에서 독립 재검증하기 전 J1 종결로 확대하지 않는다.

기술팀 공유사항은 stable root의 발급 주체·불변 version 계보·새 root 승인/audit·분할 우회
검사를 하나의 `TradePurpose` 계약으로 구현해야 한다는 점이다. 디자인팀에 새 화면 변경은 없고,
기존 PAPER 상태·7절 카드 계약도 바뀌지 않는다. 총괄팀의 다음 지시 전에는 P1 구현을 시작하지
않으며 실제 key/account/keyring/provider/KIS/order/server/systemd/cloud와 PAPER runtime은 모두
`NOT RUN`이다.

# C25/V20 PAPER 새 root 승인 계약 보완 부록

이 부록은 기존 본문과 `CR-20261015-PAPER-DESIGN-05`까지를 당시 기록 그대로 보존하면서,
Claude `V-20261011-20`의 K1을 설계 계약에서만 보완한 결과를 연결한다.

## 1. 식별정보·범위

- 보고서 ID: `CR-20261015-PAPER-DESIGN-06`
- 작성시각: 2026-10-15 00:53 Asia/Seoul
- 대상 기록: `C-20261011-25` / `V-20261011-20`
- 이번 보완 기록: `C-20261011-26`; 독립 검증 예정: `V-20261011-21`
- 범위: K1의 `NEW_ROOT_PURPOSE` 승인·집계 우회 방지·UI 표시 계약 정합성만. PAPER
  소스·테스트·dependency·DB·API·UI·운영은 계속 미구현·비활성

## 2. K1 승인 계약

`NEW_ROOT_PURPOSE`는 `CANDIDATE_SET`, `LARGE_TRADE`와 교환하거나 재사용할 수 없는 세 번째
승인 type이다. 승인 target은 기존 root, 서버가 발급한 proposed root, TradePurpose ID/version/hash,
실제 경제적 목적 변경 근거 ref/hash와 intended-effect 차이, 중복·분할 검사 `PASS` 및 그 검사
input hash·policy version·checked-at/cutoff를 묶은 불변 snapshot이다. 승인 사용자, 승인 UTC µs,
승인시각부터 24시간인 만료 UTC µs, 취소·무효 사유, 소비시각·소비 ref와 audit ref를 보존한다.

승인은 정확히 일치하는 새 `TradePurpose`와 audit를 만드는 한 transaction에서 한 번만 소비하며,
동시 소비는 하나만 성공한다. 취소, 정확한 만료시각 도달, 기소비, target version/hash/evidence 변경,
중복·분할 검사 `FAIL/UNKNOWN`이면 무효다. 후보 교체·재생성, strategy version·금액 변경과 하위
version 재승인은 새 경제적 목적의 근거가 아니다. 사용자가 승인했어도 의미가 같은 목적이면
승인을 소비하지 않고 기존 stable root를 유지하며, 변경을 입증하지 못하면 기존 root 유지 또는
`TEMPORARILY_HELD(reason=PURPOSE_CHANGE_UNPROVEN)`로 보류한다.

적법한 새 root도 후보군 승인, `LARGE_TRADE`, 300,000원 집계, 미국 session 일일 한도,
cash·SELL lot 예약과 모든 상품·집중도·코어/위성 위험 gate를 다시 통과해야 한다. 따라서 새 root
승인은 거래 승인이나 위험 한도를 대체하지 않는다.

## 3. 독립 검증 경계·회귀

다음 여섯 계약을 `V-20261011-21`에서 독립 검증한다.

- 다른 승인 type을 새 root 승인으로 재사용하면 거부한다.
- 승인 만료와 target version/hash/evidence 변경을 거부한다.
- 같은 승인을 동시에 소비하면 정확히 한 transaction만 성공하고 부분 root/audit를 남기지 않는다.
- 후보 재생성·전략 version·금액 변경으로 stable root나 누적 판정액을 초기화하지 않는다.
- 같은 경제적 목적의 새 root 승인 요청·발급을 차단한다.
- 실제 새 목적을 적법하게 승인해도 모든 거래 승인·일일 한도·예약·위험 gate를 재검사한다.

J1의 stable-root 계보·분할 우회 차단, H1의 session-scoped `ExecutionRun`, G1~G4와 F1~F8의
문서 계약을 회귀 대상으로 유지한다. 이 부록은 기능 PASS나 PAPER/P1 착수 승인이 아니다.

## 4. 기술팀·디자인팀 공유와 결정 상태

기술팀은 승인 type discriminator, immutable target hash, exact 24h expiry, 원자적 one-use consume,
TradePurpose·audit 동시 기록과 stable-root anti-splitting 검사를 하나의 transaction 계약으로
구현해야 한다. proposed root는 서버 발급값이어야 하며 사용자 입력으로 누적액을 초기화할 수 없다.

디자인팀은 세 승인 type을 별도 card/action/state로 표시하고, prior→proposed root, 목적 변경 근거,
승인 사용자·시각·만료·취소·소비 상태, 중복·분할 검사 결과를 보여야 한다. 검사 `FAIL/UNKNOWN`,
근거 부족, 동일 목적에는 승인 action을 비활성화한다.

실제 key/account/keyring/provider/KIS/order/server/systemd/cloud와 PAPER runtime·API·UI·DB
migration·동시성은 모두 `NOT RUN`이다. 총괄팀의 다음 지시 전에는 P1을 시작하지 않는다. 새 결함이
있으면 Codex 수정 요청과 총괄 최종 보고 대기로 남기며, 정책 결정 없이는 해결할 수 없는 문제는
반복 수정하지 않고 총괄팀 결정 필요로 보고한다.

# C27 P1 내부 PAPER 순수 domain 구현 부록

이 부록은 기존 본문과 `CR-20261015-PAPER-DESIGN-06`까지를 당시 기록 그대로 보존하면서,
총괄팀이 `C-20261011-26` / `V-20261011-21` 계약을 근거로 승인한 P1 구현 결과를 연결한다.

## 1. 식별정보·범위

- 보고서 ID: `CR-20261015-PAPER-P1-01`
- 작성시각: 2026-10-15 15:35 Asia/Seoul
- 계약 기준: `C-20261011-26` / `V-20261011-21`
- 구현 기록: `C-20261011-27`; 독립 검증 예정: `V-20261011-22`
- 구현물: `asset_assistant/paper.py`, 검증물: `tests/test_paper.py`
- 범위: 외부 I/O 없는 결정적 INTERNAL_PAPER domain과 단일 프로세스 메모리 repository. 운영
  SQLite schema·API·UI·scheduler·공급자·브로커·실제 또는 KIS 모의 주문은 포함하지 않는다.

## 2. 구현 결과와 정책 보존

P1은 실제/SAMPLE/MANUAL_PAPER_SNAPSHOT과 합산되지 않는 `INTERNAL_PAPER` 영역에 가상
4,000,000원 genesis를 한 번만 만든다. KRW·USD 현금, lot·원가·실현/미실현손익, append-only
원장·정정, 현금·SELL lot·전략예산·일한도 예약, 부분 가상체결과 UNKNOWN 대조 상태를 구현했다.
금액은 전용 Decimal context, 시각은 호출자가 제공한 timezone-aware UTC µs 입력만 사용한다.

위험 판정은 PAPER-only 검증 NAV와 명시 상품분류를 사용한다. 보통주 5%, 일반 ETF 40%,
광범위 지수 ETF 50%, 위성 10%, 금지상품과 미분류 fail-closed, typed 위성 손실 gate를 적용한다.
세션 시작 NAV의 20%를 매수·매도 합산 한도로 고정하고 실제 사용액과 활성 예약액을 분리한다.
기존 실제 자산 분모·보호저축·비상금·기존 데이터 realm은 가져오지 않았다.

승인은 `CANDIDATE_SET`, `LARGE_TRADE`, `NEW_ROOT_PURPOSE` 세 유형으로 분리했다. exact 24시간,
불변 target/version/hash, 취소·무효·일회 결합/소비, stable-root 기준 300,000원 합산과 새 root
분할 우회 검사를 구현했다. 후보·proposal·run 상태, 확정 영구 원인 외 후순위 차단, 가격조건,
KST 날짜/US session 중복 claim, 14일 전략 적용 계보도 순수 상태기계로 검증한다.

PROJECT_RULES.md, 기존 C-/V- 기록, C20 보존 ZIP은 변경하지 않았다. P1 메모리 원장은
append-only event와 파생 cash/lot 잔액을 제공하지만 완전한 balancing double-entry journal은
아니며, 내부 PAPER 실적은 정책 단계 C 또는 D1 통과 근거가 아니다.

## 3. 코드 검토에서 추가로 닫은 불변식

구현 중 독립 읽기 전용 검토로 확인한 우회도 최종 코드와 회귀 테스트에서 닫았다.

- 금액·가격조건·FX·위험문맥·approval target·run gate의 dataclass subclass/duck-type 우회와
  악성 datetime/iterable의 raw 예외를 안전한 `DomainValidationError`로 차단
- 같은 재생성 부모의 동시 자식은 한 건만 성공하고 부모를 원자적으로 `SUPERSEDED`; 거절시각
  이전 소급 재생성 금지
- 전략 버전은 현재 적용 버전의 직접 후속만 적용하고 같은 버전 재적용·stale sibling·parent보다
  이른 child·첫 적용 non-root를 차단
- portfolio·strategy·budget·TradePurpose parent의 causal floor를 보존
- 가격·비용 개선 완전체결에서 남은 예약을 `RELEASE` 원장과 audit로 추가 기록
- 미확인 실행시도 뒤 사용자 중지는 예약을 유지한 `RECONCILIATION_REQUIRED`로 처리

## 4. 검증 결과·한계

Ubuntu 24.04 / Python 3.12의 프로젝트 `.venv`, ROS 경로 해제, `/dev/shm` 임시 환경에서
PAPER 112/112, PAPER+관련 원장·배분·계산·전략·release 158/158, 전체 448건 중 실행 가능한
442건 PASS·6건 SKIP을 확인했다. SKIP은 PostgreSQL 5건과 `/dev/shm` 다른 filesystem의
하드링크 1건이며 하드링크는 `/tmp`에서 별도 1/1 PASS했다. compileall·pip check·기존 CLI
SAMPLE 7절·release validator도 PASS했다. P1 소스의 금지 I/O import는 0건이다.

메모리 copy-on-write transaction은 한 Python 프로세스의 thread 원자성만 검증했다. 운영 DB,
다중 프로세스, crash durability, API owner scope/CSRF, UI, 실제 시세·달력·환율 adapter,
scheduler, PC 종료 복구는 NOT RUN/미구현이다. 실제 키·계좌·개인 금융자료·Secret Service·KIS·
주문·네트워크는 사용하지 않았다. Windows·mobile·cloud도 NOT RUN이고 `.exe`는 만들지 않았다.

검증 후보(pre-log)는 `/dev/shm/codex-c27-verify.Jtb5G2/wealthy-local-source-c27-prelog.zip`,
0600, 3,651,110 bytes, 압축 해제 6,404,267 bytes, 175 members, SHA-256
`c9425b6ef635f40fb15d78e11703651b0aba14c3e314aa3fcf4373ef57577b61`이다. 최종 append를 포함한
인계 ZIP은 C-27 기록 뒤 다시 생성해 별도 식별한다.

## 5. 기술팀·디자인팀 공유와 다음 게이트

기술팀은 P2 이전에 repository port를 owner-scoped 영속 transaction으로 옮길 때 stable-root 승인
one-use, reservation unique key, fill idempotency, UTC µs, restart reconciliation을 동일 계약으로
유지해야 한다. 전략·월 예산·위성 손실 등 미확정 정책은 typed prerequisite가 없으면 계속 차단한다.

디자인팀에는 아직 새 화면 구현이 없다. 후속 UI는 INTERNAL_PAPER를 실제·SAMPLE과 구분하고,
run과 proposal 상태, 승인 유형, UNKNOWN/대조 필요, 자료 미입력을 실제 결과처럼 보이지 않게 해야
한다. 총괄팀 다음 지시 전 P2 운영 DB·API·UI·공급자·자동 운용으로 확대하지 않는다. Claude는
`V-20261011-22`에서 실제 구현·경계·동시성·실패 주입·금지 경로와 최종 ZIP을 독립 검증해야 한다.

# P1 독립 검증 종결·C27 보존·P2 설계 부록

이 부록은 기존 본문과 `CR-20261015-PAPER-P1-01`을 당시 기록 그대로 보존하면서, 후속 독립
검증과 이번 읽기 전용 디스크 점검·P2 설계를 연결한다.

## 1. 식별정보와 판정

- 보고서 ID: `CR-20261015-PAPER-P1-02`
- 작성시각: 2026-10-15 16:25 Asia/Seoul
- 대상: 구현 `C-20261011-27`, 독립 검증 `V-20261011-22`, 이번 설계 기록
  `C-20261011-28`
- 코드 식별: `asset_assistant/paper.py`
  SHA-256 `5a04d3e8937b3d813a2ed85429e51c039d788daa64ef3f0851c5d43896048844`,
  `tests/test_paper.py`
  `d0a622aed8ed55a2b6a6b2e3324244e17b11ac4e2ca28d0919d118af11090e1f`

`V-20261011-22`는 PAPER 112/112, 관련 집중 158/158, 전체 실행 가능 442/442와 독립 경계
프로브 18개를 통과했고 새 제품 결함은 0이라고 판정했다. 전체 discover의 SKIP 6건은
PostgreSQL 환경 5건과 `/dev/shm` hardlink 1건이며 hardlink는 `/tmp`에서 별도 1/1 PASS했다.
compileall·pip check·release 12/12도 PASS였다. C-27/V-22 기록과 실제 식별정보 사이의 불일치는
발견하지 못했다.

**P1은 외부 I/O 없는 순수 domain과 단일 Python 프로세스의 thread-safe 메모리 repository
범위에서만 종결됐다.** 운영 SQLite 영속성, process 간 원자성, commit/crash 내구성, restart
대조, API/UI·scheduler·자동 PAPER 운용 또는 실제·KIS 모의 주문이 검증됐다는 뜻이 아니다.
내부 PAPER 결과는 정책 단계 C나 D1 통과 근거가 아니다.

## 2. 디스크 점검

`df`, `df -i`, `findmnt`, `du`, `stat`, `lsof`의 metadata만 확인했고 민감 파일 내용은 열지
않았다. 루트와 `/home/ww/program`, `/tmp`는 같은 ext4 `/dev/nvme0n1p4`이고 `/dev/shm`만 별도
tmpfs다. inode는 약 19% 사용으로 병목이 아니었다.

점검 중 루트 가용 공간은 처음 131,346,432 bytes, C27 복사 직후 121,917,440 bytes였으나,
활성 editor/agent/system 로그 쓰기가 이어지는 동안 최저 4,362,240 bytes까지 내려갔다가
16:25에는 73,576,448 bytes로 변동했다. `df` 표시는 계속 100%다. 따라서 한 시점의 “약
185 MB”를 안정적인 작업 여유로 간주할 수 없다. `/dev/shm`은 약 6.6 GiB가 표시됐지만 실제
가용 RAM은 약 1.8 GiB이고 swap이 거의 소진돼 전체 tmpfs 여유를 작업 예산으로 쓸 수 없다.

| 항목 | 사용량(du disk blocks) | 판정 |
|---|---:|---|
| 프로젝트 전체 | 약 300,900,352 B | 루트 전체 사용량의 작은 일부 |
| `.venv` | 134,287,360 B | 현행 Python 실행·검증 환경, 임의 삭제 금지 |
| `web/frontend/node_modules` | 131,289,088 B | 현행 frontend 의존성, 임의 삭제 금지 |
| `.venv-clean` | 16,072,704 B | 과거 격리 검증 환경 가능성, 용도 확인 전 삭제 금지 |
| `releases/` | 복사 뒤 약 10.5 MB | C15/C20/C27 검증 후보, 보존 대상 |
| project·frontend `dist` | 각각 409,600 B | 빌드 산출물, 이번에는 삭제·재빌드하지 않음 |
| repository 내부 venv/node_modules 제외 Python cache | 약 950,272 B | 재생성 가능하나 별도 승인 전 삭제하지 않음 |
| `VALIDATION_LOG.md` / 총괄 보고 | 603,638 B / 94,380 B(추가 전) | append-only 증거, 정리 대상 아님 |

루트 전체 사용량은 프로젝트보다 사용자·시스템 영역이 훨씬 크다. 아래는 **정리 실행 목록이
아니라 별도 승인·앱 종료·보존정책 확인을 위한 후보**다.

| 후보 | 확인 크기 | 삭제 영향 | 복구 가능성·주의 |
|---|---:|---|---|
| `~/.cache/Raspberry Pi` | 2,361,573,376 B | 해당 앱 cache miss·재다운로드 가능 | 앱 용도 확인 뒤 대체로 재생성 가능 |
| `~/.cache/google-chrome` | 1,971,462,144 B | Chrome cache/세션 중 파일 경합 가능 | Chrome 종료·공식 정리 방식 필요, 재생성 가능 |
| `~/.cache/pip-tools` | 1,460,285,440 B | offline dependency 해석 cache 상실 | 네트워크 재생성 필요 |
| `/var/log/journal` | 985,108,480 B | 시스템 감사·장애 이력 감소 | 복구 불가, 관리자 retention 결정 필요 |
| `/var/cache/apt` | 541,896,704 B | offline package cache 상실 | 관리자 승인·네트워크 재다운로드 필요 |
| `~/.npm` | 390,430,720 B | offline frontend install cache 상실 | lockfile 기반 재다운로드 가능 |
| `~/.cache/pip` | 61,288,448 B | offline Python install cache 상실 | 네트워크 재다운로드 가능 |
| project `.venv-clean` | 16,072,704 B | 과거 검증 재현성 영향 가능 | 용도 확인·의존성 재설치 필요 |
| `web/frontend/.vite` | 약 4.0 MB | frontend test/build cache만 영향 | 재생성 가능 |

사용자 파일, DB, backup, 두 가상환경, cache, `/tmp/claude-*`, `/dev/shm/claude-*`, 과거
산출물은 하나도 삭제하지 않았다. P2 소스·DB·backup/restore·crash/ENOSPC 시험을 안전하게
수행하려면 루트 사용자 가용 공간 최소 1 GiB, 권장 2 GiB를 먼저 확보한다. 임시 DB와 pycache는
새 `/dev/shm` 하위에 두되 256~512 MiB처럼 명시적 상한을 두는 것을 권고한다.

## 3. C27 릴리스 후보 보존

원본 `/dev/shm/codex-c27-verify.Jtb5G2/wealthy-local-source-c27-final.zip`은 regular file,
0600, 3,657,912 bytes, 175 members, CRC 정상, SHA-256
`53d3b29972c036c79f34bc810cdd836388a7fcf90f213fc3d60dac73c3a96909`였다. 대상이 없고
`releases/`가 symlink가 아닌 owner 소유 directory이며 당시 여유가 파일 크기보다 충분함을
확인한 뒤 덮어쓰기 없이 다음 위치에 복사했다.

`releases/C-20261011-27/wealthy-local-source-c27-final.zip`

대상 directory는 0700, ZIP은 0600이고 원본과 `cmp`·크기·SHA-256·175 members·CRC가 모두
일치한다. 원본은 삭제하지 않았고 C20 ZIP SHA-256
`91e842acd174c4a1053e807172cdb6d62f7a324aa59516eda113f2f571051752`도 변하지 않았다.
이 ZIP은 코드 검증 후보이며 개인 DB·자격증명 또는 사용자 금융자료 backup이 아니다.
`releases/`는 release builder exact allowlist 밖이므로 다음 source ZIP에 중첩되지 않는다.

## 4. P2 추천 구조

기존 시장자료 SQLite는 수집·보고 schema이고 web-state SQLite는 인증·세션 최소 schema라서
어느 쪽에 PAPER 고빈도 원장·예약을 넣어도 장애 범위와 exact schema 계약이 결합된다. 추천은
owner-only local data directory의 세 번째 `paper-state.sqlite3`다. market/web 두 DB는 변경하지
않고 PAPER schema·migration·backup·복구를 격리한다.

별도 DB에도 모든 행과 repository 호출에 owner+portfolio scope를 두며, web-state의 정확히
한 사용자와 불변 `paper_owner_bindings`를 대조한다. P1 command는 `BEGIN IMMEDIATE` 안에서
DB 최신 revision을 hydrate한 뒤 한 번 실행하고 event·projection·audit와 revision CAS를 같은
commit으로 저장한다. 장기 메모리 aggregate나 dual-write를 권위 원본으로 두지 않는다.

승인 소비와 새 TradePurpose/audit, cash·SELL lot·일일 한도 예약, fill·ledger·projection은
각각 하나의 transaction이다. fill/command idempotency와 활성 reservation unique constraint,
UTC epoch µs integer, canonical Decimal text, append-only ledger를 적용한다. `UNKNOWN`과
`USER_STOPPED` 및 모든 예약은 재시작 뒤에도 유지한다.

W2/X1의 DB·WAL·SHM 0600, 첫 연결 전 fd 검사, 실행 중 open/close 금지, runtime lstat와
symlink/UID/inode 검사 방식을 재사용한다. 현행 2-entry encrypted backup은 V1로 보존하고,
PAPER 포함 3-entry bundle은 별도 V2 계약으로 제안한다. 정확한 schema와 시험 계획은
`docs/FUTURE_VALIDATION_DESIGN.md`의 C-28 P2 절과
`docs/REQUIREMENTS_TRACEABILITY.md` PAPER-P2-01~08에 연결했다.

## 5. 구현·검증 순서와 차단 요인

1. 승인된 방법으로 최소 1 GiB, 권장 2 GiB root 여유 확보 및 baseline 재측정
2. 별도 empty PAPER schema와 owner/path/security guard
3. aggregate hydrate, command receipt, revision CAS transaction
4. 승인·새 root·audit와 현금/SELL/daily 예약 원자성
5. fill idempotency, UNKNOWN/USER_STOPPED restart 복구
6. 순방향 migration과 online encrypted backup V2·새 위치 restore
7. 실제 subprocess 경합, commit 전/후 `SIGKILL`, bounded `SQLITE_FULL`/busy 시험
8. P1 전체 불변식과 W1~W4/X1 SQLite 보안 회귀

별도 process 승인 소비, 동시 현금·SELL·일한도 예약, 중복 fill, owner IDOR, transaction failpoint,
commit 응답 유실, restart 일치, 공간 부족, backup 복원, DB/WAL/SHM 보안 시험을 반드시 실행한다.
메모리 `RLock` 결과로 대체하지 않는다.

현재 차단 요인은 불안정한 root 여유와 총괄팀의 P2 구현 미승인이다. DB 크기·최대 WAL·backup
정책이 없으므로 운영 preflight의 정확한 공간식도 P2 구현 전에 확정해야 한다. 이번 작업에서는
DB 파일·schema·migration·repository 소스·테스트·의존성을 만들거나 실행하지 않았다.

## 6. 변경·공유·결정 요청

- 변경: P2 설계 절과 추적표, 이 append-only 부록, `C-20261011-28`, 조건부 C27 ZIP 보존본.
- 미변경: P1 코드·테스트, `PROJECT_RULES.md`, `CLAUDE_VALIDATION.md`, 기존 C-/V-와 기존 총괄
  부록, 운영 DB·설정·dependency·server/systemd/cloud.
- 기술팀 공유: 별도 PAPER DB, owner binding, revision CAS, one-use approval, reservation/fill
  uniqueness, V2 backup, subprocess/crash test는 구현 승인 전 검토 대상이다.
- 디자인팀 공유: 이번 변경은 화면이 없으며 P1/P2 상태를 운영 가능처럼 표시하면 안 된다.
- 총괄팀 결정: 정리 후보 중 실제 정리 범위, 최소 공간 gate, 별도 PAPER DB와 backup V2 계약,
  P2 구현 승인 여부. 결정 전 구현·DB 접근·자동 운용을 시작하지 않는다.

# P2 M1·O1 설계 보완 부록

이 부록은 앞선 본문과 모든 기존 부록을 당시 기록 그대로 보존하고, `V-20261011-23`의 M1과
관찰 O1에 대한 **문서 계약 보완만** 연결한다.

## 1. 식별정보와 범위 판정

- 보고서 ID: `CR-20261015-PAPER-P2-DESIGN-01`
- 작성시각: 2026-10-15 21:59 Asia/Seoul
- 대상: `C-20261011-28`, 독립 검토 `V-20261011-23`, 이번 기록 `C-20261011-29`
- V-23 판정: P1 순수 메모리 범위와 C27 보존·디스크 점검은 PASS. P2 설계 20개 기준 중
  19개 PASS/REVIEWED, M1 1개 MEDIUM FAIL, O1은 안전 방향의 보완 관찰이었다.

이번 보완은 P2 구현, SQLite schema 생성·migration, DB open, subprocess/crash/restart 시험이
아니다. P1 종결 범위도 순수 domain과 단일 Python 프로세스 memory repository 그대로다.

## 2. M1 전체 상태 매핑

`asset_assistant/paper.py::_PaperState`의 실제 21개 필드를 직접 대조했다. 설계 표에는 portfolio,
cash, ledger, budget/strategy/version/application, lot/valuation/daily limit, purpose/candidate/rank
clearance/proposal/price check, approval/reservation/fill/run/audit와 KST/US 날짜 claim을 하나도
빠뜨리지 않고 다음 계약으로 연결했다.

- `strategy_applications`는 전체 append-only 적용 이력으로 복원해 마지막 적용과 14일 제한을 유지
- `candidate_rank_clearances`와 `price_condition_checks`는 evidence/hash와 확정 순서를 보존
- KST·US claim은 owner/portfolio/instrument/date unique이며 release/delete 없이 복원
- reservations는 활성 행만이 아니라 RELEASED·CONSUMED·UNKNOWN·NEEDS_RECONCILIATION,
  최초/잔여량과 released_at을 모두 hydrate
- audits, ledger, attempts, checks와 상태 event는 최신 tail이 아니라 전체 이력을 읽음
- cash/lot/daily-limit 같은 projection은 전체 원본 이력으로 재계산해 revision/hash와 대조

정상적인 완전 빈 DB만 empty state가 될 수 있다. 미지원 version, sequence gap, 누락·중복,
dangling reference, owner 혼입, hash 불일치를 빈 collection·0·현재시각으로 보정하지 않고 write
차단 또는 대조 필요로 처리한다. snapshot은 검증된 prefix hash와 event sequence가 있을 때만
가속에 쓰고 모든 후속 event를 replay한다.

## 3. 복원 동등성과 O1 순서

복원 시험은 A(재시작 없이 계속 실행)와 B(각 transition 뒤 commit·종료·hydrate 후 같은 suffix
실행)를 비교한다. 21개 field state hash뿐 아니라 다음 판정, cash/lot/ledger, 해제분을 포함한
예약, 승인 소비, 날짜 claim, rank, 가격조건 순서, run/attempt와 revision이 같아야 한다. 14일·
KST/US 날짜·가격시각의 µs 경계, 부분체결·취소·해제·UNKNOWN·USER_STOPPED, 불완전 이력,
owner 혼입, snapshot+후속 event를 포함한다.

O1 unit-of-work는 다음 순서로 고정했다.

1. 현재 인증·single owner·owner/portfolio scope
2. idempotency key와 canonical request fingerprint
3. 같은 완료 요청이면 당시 저장 결과 반환
4. 같은 key·다른 fingerprint면 충돌, 손상 receipt면 대조 필요
5. receipt가 없는 신규 요청만 revision/CAS와 현재 gate를 검사해 한 번 실행

state/event/audit/revision과 terminal receipt는 같은 transaction이다. commit 뒤 응답 유실과 이후
revision 증가가 있어도 같은 요청은 저장 결과를 반환한다. `historical_replay`는 현재 승인이나
재실행 권한이 아니며 새 mutation은 새 key와 현재 gate를 다시 통과한다.

## 4. 변경 문서와 검증 상태

- `docs/FUTURE_VALIDATION_DESIGN.md`: 21필드 매핑, 완전 hydrate·손상 처리, A/B 복원 시험,
  O1 unit-of-work. SHA-256 `46f2655fce8268b4b998849d0c33c8846cea92b949418ea3660c8f04aaf0923c`.
- `docs/WEB_API.md`: 미구현 PAPER mutation envelope과 receipt-before-revision 계약. SHA-256
  `49e4a6beb6c69b074042bef0d6d72a14602f8af41214cb4b30682f14952f2bd5`.
- `docs/REQUIREMENTS_TRACEABILITY.md`: PAPER-P2-09·10 보완 추적. SHA-256
  `ebd4516245e5c725b8719b8b326fa3b4877354057d16a6e7e5996abc699852c0`.
- 이 append-only 부록과 `C-20261011-29`만 추가한다. 앞선 총괄 보고 104,299 bytes의
  SHA-256 `af7d5649189ea8b84e4d3fbbc5311157b76f6d21f3f9db89fb1b83701cff464b`를 prefix로 보존한다.

문서 UTF-8 strict, NUL, Markdown 표 열, `_PaperState` 21필드 매핑 완전성, O1 순서와 기존 prefix를
작은 읽기 전용 검사로 확인한다. 소스·테스트·dependency·DB·정책은 바꾸지 않는다. 실제 P2
repository·API·migration·backup/restore·multiprocess·crash 시험은 NOT RUN/미구현이며 검증
PASS로 표시하지 않는다.

## 5. 디스크·공유·다음 게이트

문서 작업 전 root 가용 공간은 36,477,218,816 bytes, 기록 직전은 36,218,527,744 bytes로
문서 append에는 충분했다. V-23 당시 약 97 MiB보다 크게 달라졌으나 Codex는 사용자 파일·cache·
DB·backup·과거 산출물을 삭제하거나 원인을 추정하지 않았다. 기존 정리 후보와 영향 표는 바로 앞
`CR-20261015-PAPER-P1-02` 2절을 연결하며 이번에 대규모 재검색·정리·설치·build·새 ZIP 생성을
하지 않았다.

- 기술팀 공유: P2 adapter는 21개 필드와 전체 역사, receipt-before-revision, A/B 동등성 시험을
  구현 전 계약으로 사용해야 하며 최근 event tail이나 active reservation만 읽어서는 안 된다.
- 디자인팀 공유: 화면 변경은 없다. 향후 과거 receipt는 현재 실행 성공/승인으로 표시하지 않고,
  손상·미복원 상태를 0이나 정상으로 표시하지 않아야 한다.
- 총괄팀 결정: 별도 PAPER DB·backup V2·P2 구현 승인은 여전히 필요하다. 이번 문서 보완에 대한
  Claude 독립 검증 전후에도 총괄팀 다음 지시 없이 P2 구현·운영 연결을 시작하지 않는다.

Claude는 실제 21개 `_PaperState` 필드 대조, 전체-history hydrate, A/B restart 계획, O1 처리 순서와
기존 P2 계약 회귀를 읽기 전용으로 확인한다. 결함이 있으면 `Codex 수정 요청 / 총괄 최종 보고 대기`,
범위 내 결함이 모두 종결된 때만 `총괄팀에게 전달할 프롬프트`를 작성한다.
