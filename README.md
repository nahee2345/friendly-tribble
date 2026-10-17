# Wealthy — Ubuntu 로컬 웹

현재 이 저장소는 Wealthy 1단계 웹 배포를 위한 설정 공간입니다. **04 버전 전체 소스는 아직 업로드되지 않았습니다.**

## 데이터와 로그인

관리자 계정은 GitHub에 저장하지 않습니다. Ubuntu 호스트의 `/home/ww/wealthy-stage1-data/state/web-state.sqlite3` 에 보관합니다. 기존 DB가 유지되면 업데이트할 때마다 관리자 계정을 재생성하지 않습니다. 관리자 계정이 없으면 최초 한 번 생성해야 합니다.

절대 커밋하지 말 것: `.env`, 비밀키, DB, WAL/SHM, 계정 정보, API 토큰, 복구키, 백업 파일, 파생 빌드, `node_modules`, `.venv`.

## 로컬 실행

기존 04 소스가 설치된 Ubuntu에서:

```bash
cd /home/ww/Wealthy_Stage1_Web_04
source .venv/bin/activate
python -m wealthy_server.local_runtime doctor --data-dir /home/ww/wealthy-stage1-data/state --market-database /home/ww/wealthy-stage1-data/market.sqlite3
python -m wealthy_server.local_runtime serve --data-dir /home/ww/wealthy-stage1-data/state --market-database /home/ww/wealthy-stage1-data/market.sqlite3 --host 127.0.0.1 --port 8000
```

접속: http://localhost:8000/stage1

※ 현재 사용자의 DB에서 users 테이블에 계정이 0개라는 검사가 있었으므로, 관리자 생성은 아직 한 번 필요할 수 있습니다. 비밀번호는 채팅 또는 GitHub에 공유하지 마세요.
