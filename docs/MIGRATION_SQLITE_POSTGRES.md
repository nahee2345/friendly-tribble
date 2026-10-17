# SQLite → PostgreSQL 마이그레이션 설계와 읽기 전용 점검 — 비활성 legacy

> **현재 이행 계획이 아닙니다.** 2026-10-11 P0 변경으로 PostgreSQL 전환을 중단하고 기존
> SQLite를 우선 운영합니다. 자동 importer·역이행·원본 삭제를 수행하지 않습니다. 아래는
> 과거 검토 근거와 기존 volume 보존을 위한 참고입니다.

확인 기준일: **2026-10-11 (Asia/Seoul)**

기존 Tk/CLI의 SQLite 시장자료와 새 웹 앱의 사용자별 PostgreSQL 자료는 소유권·schema가
다릅니다. 따라서 앱 시작이나 배포 시 자동 복사하지 않습니다. 특히
`MANUAL_ACTUAL`은 사용자 승인, 대상 사용자, 자료영역을 확정하기 전에는 가져오지 않습니다.

현재 구현된 `tools.web_migration_plan`은 **검사/계획 전용**입니다. PostgreSQL에 연결하지
않고 쓰지 않으며, SQLite row 원문을 출력하지 않습니다. 완성된 importer인 것처럼 표시하지
않습니다.

## 1. 원본 고정과 읽기 전용 점검

가능하면 기존 앱과 scheduler를 먼저 정상 종료하고 원본을 별도 읽기 전용 사본으로
보존합니다. 경로에는 개인 식별정보를 넣지 않습니다.

```bash
.venv/bin/python -B -m tools.web_migration_plan \
  --source /protected/source/market-data.sqlite3 \
  --output /dev/shm/wealthy-sqlite-plan.json
```

도구는 SQLite URI `mode=ro`, `PRAGMA query_only=ON`, 명시적 read transaction을 사용하고
다음을 기록합니다.

- `PRAGMA integrity_check`
- main DB와 존재하는 WAL의 byte 크기/SHA-256(점검 전후 동일성 확인)
- 알려진 table별 열 개수, schema SHA-256, 행 수, type/length 구분 canonical content SHA-256
- schema version과 알 수 없는 table 개수(사용자 제어 이름·열 이름은 출력하지 않음)
- `MANUAL_ACTUAL` snapshot 존재 여부(boolean만 출력)
- `postgres_import_performed: false`
- `automatic_actual_import_allowed: false`

계획 파일에는 row 값은 없지만 구조·행 수 자체도 운영정보가 될 수 있어 `0600`으로
생성합니다. 기존 계획 파일을 덮어쓰지 않습니다. 점검 중 DB/WAL 또는 `data_version`이
변하면 실패하며 결과를 확정하지 않습니다.

SQLite의 `-wal`을 무시하고 main 파일만 복사하면 최신 commit이 빠질 수 있습니다. 이 도구는
WAL을 발견하면 hash에 포함하지만, 장시간 쓰기가 계속되는 DB를 완전 동결하는 도구는
아닙니다. 안정된 사본을 만드는 방법은 운영 중인 SQLite의 검증된 backup 절차로 별도
확정해야 합니다.

## 2. 사용자 확인 없이는 중단

계획을 검토해 다음을 명시적으로 결정해야 합니다.

- 대상 웹 사용자 ID(모든 target row의 `owner_id`)
- SAMPLE, MANUAL_PAPER_SNAPSHOT, MANUAL_ACTUAL별 가져올 범위
- 시장자료/공급자 설정/전략 버전/보고서/job 이력의 보존 범위
- 기존 OS keyring 비밀은 가져오지 않고 웹 앱 안에서 다시 등록한다는 점
- 중복·시각·통화·Decimal·자료영역 검증 실패 행 처리

실제 원장의 자동 병합, SAMPLE과 ACTUAL 합산, 초기자산 400만 원 자동 합산은 금지됩니다.
SQLite의 `app_settings` 안 값도 새 서버의 공식 공급자 문자열로 신뢰하지 않고 현행 입력
validator를 다시 통과시켜야 합니다.

## 3. 향후 importer 필수 구조

실제 importer를 별도 승인 후 만들 때 다음 단계를 지켜야 합니다.

1. source main/WAL SHA-256과 schema version을 allowlist로 재확인합니다.
2. PostgreSQL에 migration run을 `PENDING`으로 만들고 source hash, 대상 owner, 도구 버전을
   저장합니다. 같은 `(source_hash, owner, tool_version)` 재실행은 idempotent하게 거부하거나
   기존 결과만 조회합니다.
3. 운영 table이 아닌 run별 staging schema에 변환합니다.
4. 모든 시각을 aware UTC로, 금액/비율을 exact decimal로, JSON을 중복키 없이 다시
   검증합니다. 계좌번호형 사용자 출력 마스킹도 그대로 적용합니다.
5. source table별 행 수, 제외 행 수와 사유 코드, 변환 후 deterministic hash를 계산합니다.
   row 원문이나 금융값을 로그에 남기지 않습니다.
6. `MANUAL_ACTUAL`은 별도 사용자 승인 token이 있는 run만 target 후보로 둡니다.
7. dry-run에서는 staging 검증 결과만 만들고 운영 table에 insert하지 않습니다.
8. apply는 하나의 명시적 transaction에서 ownership/FK/unique/policy 검사를 모두 통과한
   뒤 commit합니다. 한 항목이라도 실패하면 전체 rollback합니다.
9. commit 후 target count와 deterministic hash를 다시 계산해 migration run에 기록하고,
   source count/hash와 변환 명세에 따라 대조합니다.
10. 원본 SQLite는 삭제·변경하지 않고, rollback은 새 run으로 가져온 target row만 migration
    provenance를 이용해 별도 승인 후 transaction으로 되돌립니다.

SQLite와 PostgreSQL의 직렬화가 달라 source content hash와 target hash가 그대로 같지는
않습니다. 변환 명세 버전을 고정하고, 같은 source를 같은 버전으로 변환한 staging export의
hash가 재현되는 방식으로 확인해야 합니다. 단순 행 수 일치만으로 성공 처리하지 않습니다.

## 4. 검증표

| 검증 | 현재 상태 |
|---|---|
| read-only fixture DB integrity/count/hash | PASS |
| `MANUAL_ACTUAL` 존재 boolean, 자동 import false | PASS |
| 원본 fixture byte 불변 | PASS |
| 실제 사용자 SQLite 점검 | NOT RUN |
| PostgreSQL staging dry-run importer | 미구현 |
| transaction apply/rollback/idempotency | 미구현 |
| 실제 사용자 ownership/RLS 격리 검증 | NOT RUN |
| Windows 원본 경로·잠금·WAL 검증 | NOT RUN |

미구현 항목은 UI/API에서 완료로 표시하면 안 됩니다.
