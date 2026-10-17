# Wealthy 로컬 웹

> **상태**: 현재 GitHub에는 문서와 배포 보조 스크립트만 있습니다. 04 전체 소스는 아직 업로드되지 않았으며 이 저장소만 clone해 실행할 수 없습니다.

## 서버 오류 원인 (2026-10-17)

`/home/ww/wealthy-stage1-data/state/web-state.sqlite3`의 무결성은 정상이며, `users` 테이블에 계정이 **0개**입니다. 소스의 로컬 서버는 계정이 정확히 1개가 아니면 fail-closed 처리합니다. 최초 한 번만 다음을 수행하십시오.

```bash
cd /home/ww/Wealthy_Stage1_Web_04
source .venv/bin/activate
python -m wealthy_server.local_runtime create-admin --data-dir /home/ww/wealthy-stage1-data/state --market-database /home/ww/wealthy-stage1-data/market.sqlite3
python -m wealthy_server.local_runtime doctor --data-dir /home/ww/wealthy-stage1-data/state --market-database /home/ww/wealthy-stage1-data/market.sqlite3
python -m wealthy_server.local_runtime serve --data-dir /home/ww/wealthy-stage1-data/state --market-database /home/ww/wealthy-stage1-data/market.sqlite3 --host 127.0.0.1 --port 8000
```

접속: **http://localhost:8000/stage1**

관리자 계정을 매번 새로 만들면 안 됩니다. 반드시 동일한 `--data-dir`를 유지하십시오. DB와 비밀키는 GitHub에 올리지 않습니다.

## 증분 업데이트 목표

04 소스를 GitHub에 **최초 1회 안전하게 커밋한 후**에는 프로그램 디렉터리를 재생성하지 않고 `git pull --ff-only`로 수정된 파일만 동기화할 수 있습니다. 의존성이 바뀐 경우만 `python -m pip install -e '.[web]'`, `npm --prefix web/frontend ci && npm --prefix web/frontend run build`를 다시 실행합니다. 기존 계정 DB는 소스 폴더 밖에 있으므로 유지됩니다.

배포 시 DB·API 키·시크릿·인증서·.env·.venv·node_modules·dist·백업은 Git에 포함하지 마십시오.
