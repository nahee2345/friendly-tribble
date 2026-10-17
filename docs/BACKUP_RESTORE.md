# localhost SQLite 암호화 백업·복원

확인 기준일: 2026-10-11

구현 상태 갱신: 2026-10-16. 아래 기존 market/web 두 파일 절차는 **V1**로 그대로 유지한다.
PAPER 포함 V2는 이 문서 뒤의 별도 절을 따르며 기존 CLI 기본 동작을 바꾸지 않는다.

현행 운영 DB는 `MarketDataStore`와 web-state 두 개의 SQLite 파일입니다. 백업은 두 DB를
각각 SQLite online backup으로 일관된 메모리 snapshot에 복사하고 무결성·스키마를 검사한
뒤 하나의 AES-256-GCM bundle로 저장합니다. 평문 backup 임시 파일을 만들지 않습니다.
두 파일의 snapshot은 순서대로 생성되므로 각 파일 내부 일관성은 보장하지만 두 DB 사이의
완전히 같은 순간을 원자적으로 보장하지 않습니다. 두 DB를 함께 바꾸는 운영 전환 전에는 앱과
one-shot을 중지해 교차 파일 변경을 막은 뒤 backup하고, 행 수·불변 기록을 대조합니다.

## 포함·제외

포함:

- `market-data.sqlite3`: 시장자료, 공급자 설정, 수집/작업/보고/월별/전략 이력
- `web-state.sqlite3`: 사용자, Argon2 hash, server session 상태, scenario, 목표 전략 version

제외:

- 공급자 API key/secret 원문: Ubuntu Secret Service에만 있음
- backup key: Secret Service의 별도 fixed item
- frontend `dist`, cache, `.venv`, 실제 캡처, 생성 보고서 파일
- 정지된 PostgreSQL volume: 이 SQLite bundle과 별도 legacy 자산

Secret Service 자체의 이식/복구는 OS 계정의 별도 보안 절차입니다. DB backup만으로 공급자
키가 복원되었다고 주장하지 않습니다.

## 키 경계

로컬 backup key는 `org.long-term-asset-assistant.wealthy-local-web` service의
`sqlite-backup-key-v1` item으로 생성·보관합니다. DB, backup, 소스, 환경변수, CLI 인수에
키를 쓰지 않습니다. Secret Service가 없거나 잠겨 있으면 평문 key file fallback 없이
작업을 거부합니다.

## 백업

웹과 one-shot이 실행 중이어도 online backup을 사용할 수 있지만, source path 두 개는 명시적
절대경로이고 서로 다른 소유자 일반 파일이어야 합니다. output은 기존 파일을 덮어쓰지 않습니다.

```bash
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m tools.web_local_backup backup \
  --market-database /home/ww/.local/share/long-term-asset-assistant/market-data.sqlite3 \
  --web-state-database /home/ww/.local/share/wealthy/web-state.sqlite3 \
  --output /home/ww/Backups/wealthy-local-20261011.wbackup
```

성공 JSON에는 format, 암호화 파일 byte 수, SHA-256, 고정 entry 이름, 평문 backup을 쓰지
않았다는 상태만 나옵니다. path, 사용자 데이터, key는 출력하지 않습니다. 파일 권한은 0600입니다.

## 복원 시험

운영 DB에 바로 복원하지 않습니다. 존재하지 않거나 완전히 빈 새 디렉터리만 허용합니다.

```bash
env -u PYTHONPATH -u AMENT_PREFIX_PATH \
  .venv/bin/python -B -m tools.web_local_backup restore \
  --input /home/ww/Backups/wealthy-local-20261011.wbackup \
  --destination-directory /dev/shm/wealthy-restore-check
```

순서:

1. bundle type, AES-GCM tag를 인증합니다.
2. 내부 metadata size/name/SHA-256과 후행 데이터 부재를 확인합니다.
3. 두 SQLite를 메모리에 deserialize하고 `PRAGMA integrity_check`와 schema version을 확인합니다.
4. table count를 산출합니다.
5. 모든 검사가 끝난 뒤 새 디렉터리에 owner-only partial DB를 만들고 atomically rename합니다.
6. 결과 파일을 다시 열어 핵심 불변 row·UTC µs·Decimal text·보고/전략 history를 대조합니다.

잘못된 key, 1byte 변조, 손상 SQLite, 알 수 없는 schema, nonempty target, symlink와 overwrite는
복원 파일을 만들기 전에 거부합니다. 오류 메시지에 전체 path나 자료 원문을 넣지 않습니다.

## 운영 전환

복원 시험 성공이 곧 운영 교체 승인은 아닙니다. 실제 전환은 서비스를 중지하고 source·backup
SHA-256, schema/version, table/row counts, 불변 기록, 사용자 소유권, 시각/Decimal 정밀도를
대조한 후 사용자가 명시적으로 path를 바꿔야 합니다. 원본 DB와 backup을 보존하고 검증되지
않은 역이행·덮어쓰기를 하지 않습니다.

## 보관과 비용

기본 인프라 비용 0원 범위에서는 사용자 소유 로컬/외장 저장소 후보를 사용합니다. 외부 cloud
upload는 현재 범위가 아니며 금융자료·backup을 자동 업로드하지 않습니다. backup과 key를 같은
장치·폴더에 두면 재해 복구가 아니므로 사용자가 별도 보안 보관 위치를 결정해야 합니다.

## legacy PostgreSQL backup

`tools/web_backup.py`, `web_restore.py`, `web_backup_crypto.py`와 기존 `.wbak` fixture는 과거
PostgreSQL 검증 재현용으로 보존됩니다. 현행 localhost 실행은 이를 호출하지 않습니다.
기존 volume/backup을 삭제하거나 SQLite로 자동 역이행하지 않았습니다.

## PAPER 포함 V2 개발 인터페이스

V2의 고정 entry는 `market-data.sqlite3`, `web-state.sqlite3`, `paper-state.sqlite3` 세 개다.
각 파일은 SQLite online backup으로 메모리에 복사한 뒤 integrity, exact schema, table count,
SHA-256을 검사하고 하나의 별도 V2 AES-256-GCM bundle로 만든다. web-state의 정확히 한 owner와
PAPER owner binding도 일치해야 한다. 기존 V1 magic·2-entry 공개 함수와 CLI는 그대로 남는다.

세 개의 개별 online snapshot은 그 자체로 같은 시각의 복구 지점을 보장하지 않는다. V2 builder는
평범한 boolean을 받지 않고, 모든 참여 writer가 공유 잠금에 진입한 뒤 exclusive
`backup_quiesce()`가 발급한 현재 프로세스·현재 inode의 활성 증명을 요구한다. 증명이 없거나 만료,
상속, 경로 교체, writer 점유 상태이면 bundle을 만들지 않는다. 현행 운영 server/one-shot/PAPER
API는 이 공통 writer barrier에 아직 연결되지 않았으므로 **운영 V2 생성은 비활성/NOT RUN**이다.
개발 검증은 새 `/dev/shm` fixture의 세 DB만 사용한다.

V2 restore는 인증된 header의 정확한 세 entry와 recovery-point metadata, 각 payload의 hash/schema/
행 수 및 owner 연결을 전부 검사한 뒤 존재하지 않거나 완전히 빈 새 디렉터리에 0600 파일을 만든다.
부분 publish가 생기면 그 호출이 만든 파일만 회수하고, 기존 운영 DB는 덮어쓰지 않는다. restore 후
PAPER repository를 다시 열어 21필드 state hash·revision·UNKNOWN 예약과 다음 순수 판정을 대조해야
복구 시험이 완료된다. DB bundle에는 OS Secret Service의 공급자 secret이나 backup key가 들어가지
않는다.

V2 공개 Python 인터페이스는 다음과 같다. 이는 현재 사용자 실행 CLI가 아니다.

```python
from tools.web_local_backup import (
    backup_quiesce,
    backup_local_databases_v2,
    restore_local_databases_v2,
)

with backup_quiesce(coordination_lock) as proof:
    backup_local_databases_v2(
        market_db, web_db, paper_db, output, key, quiesce=proof
    )

restore_local_databases_v2(bundle, new_empty_directory, key)
```

backup 보존 자동 정리는 별도 fixture-only 관리 manifest에 `VERIFIED`로 등록되고 새 정상 복구 지점이
검증된 뒤에만 후보를 계산한다. 보존 개수가 미확정이면 삭제 0건이며, 유일한 정상 복구 지점과
C20/C27 소스 ZIP·검증 캡처·복구키·미등록 파일은 대상이 아니다.
