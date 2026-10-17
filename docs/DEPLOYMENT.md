# Wealthy Ubuntu localhost 설치·운영

확인 기준일: 2026-10-11

현재 배포는 외부 서버가 아니라 Ubuntu 24.04 사용자 PC 한 대입니다. 주소는
`http://localhost:8000`, socket bind는 `127.0.0.1:8000`입니다. LAN·인터넷·터널·공개
도메인으로 노출하지 않습니다.

## 전제 조건

- Ubuntu 24.04, Python 3.12, Google Chrome
- 프로젝트 전용 `.venv`; 시스템 Python/ROS 2/`~/.bashrc` 변경 없음
- Node/npm은 frontend build에만 사용하고 runtime은 built static asset을 FastAPI가 제공
- 로그인한 Ubuntu 사용자 세션의 Secret Service
- 데이터 기본 후보:
  - 앱 상태: `/home/ww/.local/share/wealthy/web-state.sqlite3`
  - 기존 시장자료: `/home/ww/.local/share/long-term-asset-assistant/market-data.sqlite3`

경로는 예시가 아니라 현재 사용자용 권장 절대경로입니다. 기존 DB가 다른 XDG 경로에 있으면
자동으로 새 빈 DB를 만들어 대체하지 말고 온라인 백업·검증 복사 또는 명시적
`--market-database`로 원본을 우선 사용합니다.

## 설치와 frontend build

프로젝트 루트 `/home/ww/program`에서 실행합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[web]'
npm --prefix web/frontend ci
npm --prefix web/frontend run build
```

전역 `pip`, `sudo pip`, ROS workspace 환경을 사용하지 않습니다. frontend `dist/`는 생성물이며
소스 인계 ZIP에서 제외합니다.

## 최초 관리자

관리자 생성 명령은 local web-state SQLite를 명시하고 대화형 TTY에서 사용자 이름과
비밀번호를 입력합니다. 비밀번호는 12자 이상이며 CLI 인수·shell history·파일로 넘기지
않습니다.

```bash
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m wealthy_server.local_runtime create-admin \
  --data-dir /home/ww/.local/share/wealthy \
  --market-database /home/ww/.local/share/long-term-asset-assistant/market-data.sqlite3
```

이미 관리자가 있으면 새 계정을 자동 추가하거나 덮어쓰지 않고 중지합니다.

## 직접 실행

```bash
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m wealthy_server.local_runtime serve \
  --data-dir /home/ww/.local/share/wealthy \
  --market-database /home/ww/.local/share/long-term-asset-assistant/market-data.sqlite3 \
  --host 127.0.0.1 --port 8000
```

Chrome에서 `http://localhost:8000`만 엽니다. 종료는 실행 terminal에서 `Ctrl+C`입니다.
`0.0.0.0`, LAN IP, Docker/Caddy의 과거 8080 경로는 현행 실행법이 아닙니다.

## 예약 작업 dry-run·설치·상태·해제

설치 전 dry-run은 5개 unit의 내용·경로·충돌만 검사하고 systemd를 변경하지 않습니다.

```bash
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m tools.web_local_service install \
  --project-root /home/ww/program \
  --python /home/ww/program/.venv/bin/python \
  --data-dir /home/ww/.local/share/wealthy \
  --market-database /home/ww/.local/share/long-term-asset-assistant/market-data.sqlite3 \
  --dry-run
```

확인 뒤 `--dry-run`만 제거하면 fixed-name user units를 설치·enable합니다. 이 도구는 sudo,
system service, lingering 변경, legacy unit 삭제를 하지 않습니다.

```bash
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m tools.web_local_service status \
  --project-root /home/ww/program \
  --python /home/ww/program/.venv/bin/python \
  --data-dir /home/ww/.local/share/wealthy \
  --market-database /home/ww/.local/share/long-term-asset-assistant/market-data.sqlite3

env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m tools.web_local_service uninstall
```

생성 대상은 `wealthy-local-web.service`, `wealthy-local-collect.service/.timer`,
`wealthy-local-report.service/.timer`입니다. 15:00·16:00 Asia/Seoul, `Persistent=true`,
UMask 0077, 비밀 환경변수 제거, `NoNewPrivileges`, `PrivateTmp`를 적용합니다. 기존
`asset-assistant-*` 또는 부분/동일 이름 unit이 있으면 중복을 피하려고 설치를 거부합니다.
uninstall은 marker가 일치하는 5개 unit만 제거하고 DB·보고서·legacy unit은 보존합니다.

## 실행 상태의 정확한 의미

| 상태 | backend/작업 | 알림 |
|---|---|---|
| Chrome만 닫음 | user service가 실행 중이면 backend와 timer는 계속 가능 | Chrome 알림 수신 보장 없음 |
| backend service 중지 | 웹/API 중지; 독립 task service는 timer로 실행 가능 | 내부 알림 화면 접근 불가 |
| 웹 로그아웃 | session 폐기; backend/timer는 유지 | 해당 browser session 알림 없음 |
| 마지막 Ubuntu 로그아웃 | 현재 `Linger=no`이면 user manager 지속 보장 없음 | 보장 없음 |
| 절전 | 절전 중 실행 안 됨; 복귀 뒤 persistent timer catch-up 가능 | 자료 기준시각·복구 상태 확인 |
| PC 종료/전원 꺼짐 | 실행 불가 | 수신 불가 |

PC off 동안 수행했다고 표시하지 않습니다. 복귀 때 15시와 16시 timer가 함께 발화할 수 있어
DB slot claim/lease와 부분 보고서가 최종 중복·순서 방어선입니다.

## 장애 확인과 복구

```bash
systemctl --user status wealthy-local-web.service --no-pager
systemctl --user list-timers 'wealthy-local-*' --all --no-pager
journalctl --user -u wealthy-local-web.service -n 100 --no-pager
journalctl --user -u wealthy-local-collect.service -n 100 --no-pager
journalctl --user -u wealthy-local-report.service -n 100 --no-pager
```

로그에는 key/token/전체 계좌/업로드 원문이 없어야 합니다. 오류가 있어도 DB를 삭제하거나
빈 DB로 교체하지 말고, 서비스 중지→암호화 백업→SQLite integrity/schema 검사→새 임시
위치 복원 순서로 확인합니다.

## 비활성 legacy 상태

과거 Docker Compose의 backend/PostgreSQL/Caddy/worker는 2026-10-11 범위 정정 때
`docker compose stop`으로 정지했습니다. `down`, volume 삭제, secret 삭제, PostgreSQL→SQLite
역이행은 수행하지 않았습니다. Oracle/cloud/DNS/HTTPS/PWA/Web Push/Windows·mobile 배포도
더 진행하지 않으며 현행 PASS로 취급하지 않습니다.
