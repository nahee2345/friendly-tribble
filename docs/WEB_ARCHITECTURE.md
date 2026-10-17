# Wealthy localhost 웹 아키텍처

확인 기준일: 2026-10-13

## 실행 경계

```text
Google Chrome ── http://localhost:8000 ── FastAPI/Uvicorn 127.0.0.1:8000
                                             │
                      ┌──────────────────────┼──────────────────────┐
                      │                      │                      │
              built React SPA        Python domain engine     authenticated API
                                            │
                          ┌─────────────────┴─────────────────┐
                          │                                   │
              market-data.sqlite3                  web-state.sqlite3
              MarketDataStore v3                   local web state v1
              시장/공시/보고/월별/                 사용자/세션/login limit/
              작업/후보/공급자 설정/               scenario/목표 전략 version
              미국 공식정보 metadata
                          │                                   │
                          └───────── 서로 다른 파일 ───────────┘

Ubuntu Secret Service ── provider keys + local fingerprint/backup keys
systemd --user ── 15:00 collect / 16:00 report (독립 one-shot, 같은 market DB)
```

React 정적 빌드는 API와 같은 FastAPI 프로세스에서 마지막 route로 마운트됩니다. `/api/*`가
항상 우선하고 client route만 `index.html` fallback을 받습니다. 파일처럼 보이는 미존재 경로,
경로 순회, NUL, legacy `sw.js`/manifest 요청은 SPA로 되돌리지 않습니다.

## 단일 원본과 DB 분리

- 금융 계산·위험 판정·보고서는 `asset_assistant` Python 엔진만 수행합니다. React는 API가
  반환한 결과를 표시하고 Decimal 계산을 복제하지 않습니다.
- `MarketDataStore` v3가 기존 시장 기록, 수집 run, UTC epoch 마이크로초, 후보 평가, 보고서,
  월별 snapshot, 작업 slot/lease, 설정과 불변 전략 기록의 원본입니다. v3는 이 원본에
  `market_information`, 공급자 상태, refresh claim 세 테이블을 더하며 기존 행을 삭제하거나
  다시 쓰지 않습니다.
- `web-state.sqlite3`는 브라우저 인증에 필요한 최소 테이블만 가집니다. 기존 시장·보고·작업
  테이블을 SQLAlchemy 모델로 중복 생성하지 않습니다.
- 두 DB가 같은 path/inode이거나 기존 알 수 없는 테이블이 있는 web-state 파일이면 시작을
  거부합니다. 모두 WAL, foreign key, busy timeout, 소유자 전용 권한을 적용합니다.
- 실제 사용자 자료의 자동 이전은 없습니다. 안정 경로로 옮길 때는 SQLite online backup,
  원본 보존, 무결성·스키마·건수 대조를 먼저 수행합니다.

## 요청·인증·비밀 경계

- 공개 회원가입은 없고 TTY 전용 로컬 관리 명령으로 최초 관리자를 만듭니다. 비밀번호는
  Argon2id hash만 DB에 저장하고 CLI 인수·로그로 받지 않습니다.
- 로그인 후 opaque session은 서버 DB에 hash로 저장합니다. 쿠키는 HttpOnly/SameSite이고,
  mutating API는 별도 CSRF 쿠키와 header를 대조합니다. 로그아웃과 비밀번호 변경은 세션을
  폐기합니다.
- Host는 `localhost`, `127.0.0.1`만 허용합니다. Origin은 same-origin만 허용하고 CORS를
  열지 않습니다. localhost라도 XSS·IDOR·SQL injection·path traversal·SSRF·명령 실행을
  신뢰하지 않습니다.
- 공급자 키·secret은 기존 `CredentialStore`의 Ubuntu Secret Service에만 저장합니다.
  web-state/market DB, 환경변수, `.env`, 브라우저 저장소, API 응답에 원문을 두지 않습니다.
  보안 저장소가 없으면 연결을 `설정 필요`로 두고 평문 fallback을 만들지 않습니다.
- KIS 인증은 공식 시장자료 읽기 adapter용일 뿐 계좌 조회·주문으로 전달되지 않습니다.

## 화면 데이터 흐름

1. SAMPLE 또는 검증된 수동 JSON을 인증+CSRF upload합니다.
2. 서버가 공통 입력 검증·마스킹 후 scenario snapshot과 7절 report를 만듭니다.
3. 대시보드·portfolio는 해당 snapshot을 읽습니다. 결측은 0이 아니라 `null/미입력`입니다.
4. 사용자 목표 전략은 전체 투자자산 기준 exact 100% 불변 version입니다.
5. 후보 평가 가중치는 기존 MarketDataStore의 별도 `StrategyMix`입니다. 두 모델은 서로
   자동 변경하지 않습니다.
6. 실제 전략 비중은 확정된 자산 귀속이 있을 때만 한 번씩 합산합니다. 자료가 없으면
   `UNAVAILABLE`이며 목표값을 실제값으로 복사하지 않습니다.

## 예약과 동시성

- `wealthy-local-collect.timer`: Asia/Seoul 15:00, 자료 수집·검증·후보 평가.
- `wealthy-local-report.timer`: Asia/Seoul 16:00, 그때까지의 정상/부분 자료로 7절 보고서.
- one-shot은 `AutomationService.run_scheduled()`를 호출해 기존 DB slot claim/lease와
  중복 방지를 재사용합니다. 웹 서버와 작업이 같은 DB를 쓰며 WAL/busy timeout을 적용합니다.
- `Persistent=true`라 절전 복귀나 다음 로그인 때 놓친 각 timer가 한 번 발화할 수 있습니다.
  정확한 순서는 보장하지 않으므로 report가 부분 상태와 기준시각을 표시해야 합니다.
- installer는 legacy timer 또는 부분 설치를 발견하면 실패합니다. lingering 변경이나 기존
  unit 삭제를 자동 수행하지 않습니다.

Chrome을 닫아도 사용자 systemd와 backend가 로그인 세션에서 실행 중이면 작업은 계속될 수
있습니다. backend만 중지되어도 독립 one-shot은 실행될 수 있습니다. 현재 `Linger=no`이면
마지막 로그아웃 뒤 지속을 보장하지 않습니다. 절전·PC 종료 중에는 실행되지 않습니다.

## 알림

- 내부 알림함은 기존 job/report 상태에서 비민감 문구를 파생합니다. 별도 금융 알림 원장을
  중복 생성하지 않습니다.
- Chrome Notification은 페이지가 열려 있고 사용자가 허용한 동안만 표시합니다.
- PWA service worker와 외부 Web Push delivery는 비활성입니다. 과거 SW를 제거하기 위한
  same-origin unregister/cache cleanup만 수행합니다.
- 가능한 Ubuntu 사용자 세션 알림은 보조 경로이며 서버 작업 성공 조건이 아닙니다.

## 현재 사용하지 않는 legacy

`deploy/` Docker Compose, PostgreSQL model/repository, Caddy, server worker, cloud backup,
PWA/Push 자료는 과거 증거로 보존하지만 localhost runtime이 import·시작하지 않습니다. 기존
컨테이너는 중지했고 volume과 secret 파일은 삭제하지 않았습니다. 역이행·삭제는 총괄팀 승인
전 수행하지 않습니다.
