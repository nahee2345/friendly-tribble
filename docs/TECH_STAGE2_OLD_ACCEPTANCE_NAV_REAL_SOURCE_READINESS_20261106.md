# Wealthy 기술팀 — 2단계 안정화 / 실공급자 시세·환율 NAV 준비 (2026-11-06)

**공유 대상:** 총괄팀 + QA팀 + 디자인팀. **이 문서/코드는 시장자료 실제 연결 또는 개장 NAV 실증 결과가 아닙니다.**

## 1. 검증 기준
- **부모 코드:** 기술팀 [PR #37](https://github.com/nahee2345/friendly-tribble/pull/37) 기준 `75a9b8e96bc7b8cae8306e8c904da441a9a7fa9d`; 후속 Draft `tech/stage2-old-acceptance-20261106`.
- **기존 실패 재현:** 이전 PR #34/37에서 Stage4~7 83개 노드(2026-10-19 UTC 10:00: **72/11**, 2026-11-06 UTC 10:00: **64/19**), App 화면 75개 중 **73/2**.
- **개선 항목:** `/dashboard`는 기존대로 `Stage1Page`를 보여 줍니다. 숨김 접근성 heading `통합 대시보드`, **실제 증권사 주문 기능 없음**, 시장원장 미조회 시 `미입력` 상태의 실제 표시, 같은 Stage1 화면으로 가는 사이드바 중복링크 제거; `/stage1` URL 호환 유지. Stage1Dashboard 공용 인증 세대 처리 추가.
- **Stage7의 날짜 버그:** 기존 테스트의 역사적 `audit(now=2026-10-19)`와 Python runner의 2026-11-06 실제 `stage1_events.created_utc` 기록시각이 불일치. `tests/test_stage7_release.py`의 **테스트 모듈에서만**, 원장의 `Stage1Ledger`/배분/포트폴리오/제어 시간 생성기를 `NOW`로 고정. 운영 생산 코드·감사 `_timestamp` 기준·원본 assertion·skip **일절 변경 없음**. 미래 자료를 정상 승인하는 규칙은 추가하지 않았음.
- **남은 11개:** Stage4 `PortfolioEngine.run`이 PR #29부터 `paper_execution_readiness().verified=false`에서 바로 `session_unverified`를 반환하여 **구형 다중트랜잭션 실행 코드에 진입하지 않는** 의도적 계약 충돌입니다. 해당 11개에 기존 `completed`/손절 `SELL`/ISA 매수/매수 중지 사유/수량을 기대하는 옛 수용시험이 포함되며, `IndexError` 사례는 실 API 예외가 아니라 **반환된 빈 `skipped[]` 배열을 구형 시험에서 인덱싱한 연쇄 증상**입니다. 옛 기대값을 약화하거나 생산 release barrier를 끄면 안 됩니다. Stage4 준비 단계 `preview`는 기존에 직접 PASS인 부분이 확인되었습니다.
- 정식 정책 선택(총괄팀): (A) 과거 `run` 성공을 새로운 **검증된 NAV + owner 20% 원자예약 + 계좌 scope + 실자료 provenance**를 제공하는 별도 격리 수용시험으로 대체·원본은 역사적 비호환으로 유지, 또는 (B) 원자·멱등·승인/한도 같은 수준으로 증명되는 별도의 **테스트 전용 옛 거래 runner**를 production에서 불러올 수 없도록 분리. **기술팀은 어느 쪽도 현재 코드에서 몰래 우회하지 않았습니다.**

## 2. 실제 시세·환율 기반 NAV 검증 준비

- [`wealthy_server/stage1_nav_proof.py`](../wealthy_server/stage1_nav_proof.py): NYSE 정규 세션 개장 `09:30 America/New_York`, **개장 후 15분 미만**에서만 새로운 `stage1_verified_opening_nav` 생성. 2026-11-06은 미국 서머타임 종료 후 **14:30 UTC / 23:30 KST** 개장(15분 생성기간 23:30~23:45 KST, 23:45는 포함하지 않음). 당일 공식 특수 휴장·공급자 상태는 별도 검증 필요.
- [`stage1_us_market_calendar.py`](../wealthy_server/stage1_us_market_calendar.py)에는 **2026년의 예정 휴장·조기종료만 고정**돼 있습니다. 2027년부터는 미검증으로 차단. 예정 시간은 공급자 작동, 거래 허가, 실제 개장 여부를 대신 보증하지 않습니다.
- **지연 참조자료 공급원:** 미국 종가 **Stooq**(공개 EOD), 국내 일간 종가 **Yahoo Finance 비공식 Chart**, USD/KRW **Frankfurter**. 실제 실시간 주문호가/브로커 매매 가능한 시세가 아닙니다. 공식 공급자·환율 종가 시각·라이선스 등 정책 검토가 필요하며, 수집 실패를 가짜 금액으로 보완하면 안 됩니다.
- 수집은 `Collector.collect(kind,symbol)` 등 **사용자 승인된 로컬 환경의 별도 사전 수집 단계**에서 수행합니다. 네트워크 FETCH 및 시세·FX 검증은 SQLite writer 시작 **전에** 종료되어야 합니다. 원장 락 내 통신 금지. 현재 이 브랜치에서 실제 공급자를 호출하거나 비밀키를 사용하지 않았습니다.
- 조건: 사용자 고유 owner의 `PAPER` `default`, `kr-base`, `isa-base` 및 명시된 추가 PAPER 계좌만 포함; 수동 실보유 `MANUAL_ACTUAL` / 샘플 `SAMPLE` / 저축·보호자산은 제외. **4,000,000원은 사용자 계좌 실증이 아닌 초기 가상입금 설정값**이며 원장 확인 전 자동 NAV 대체 불가.
- `MarketStore.latest_many`가 원본 레코드 **sha256 일치**를 검사하고, `_validated_reference`가 `quality=VERIFIED_REFERENCE`, `tradeable=False/use_for_paper_execution=False`, `source_url=https://…`, `recorded_utc <= last_verified_utc <= 개장시각`, **개장 전 5일 이내 수집**, **기준일 6일 이내**, 정확한 `FX USD/KRW`·통화·티커 일치를 검증합니다. **당일 개장 이후 가져온 응답을 개장 전 실제 참고시세로 역기록 금지**.
- 증거가 없거나 늦으면 인증된 `POST /api/v1/stage1/opening-nav/verify`는 **409 차단**. 성공 시에도 증거 해시와 시작 NAV를 불변 기록할 뿐 반환 `execution_authorized=false`이며 **PAPER 허용 전환 아님**.
- 실자료 실제 검증에 필요한 증거: 공식 거래일·시장시간 교차, 원격 URL/HTTP 상태·원문 해시와 서버 저장 SHA256, 공급원 타임스탬프/기준일, 달러환율 및 USD/KRW 통화, owner-paper 범위(계좌 목록·잔고·미결/UNKNOWN), `PRAGMA integrity_check`와 개장 당시 원장 스냅샷, 시작 NAV 불변성, 수동/Stage4/USD FX+BUY 총 20% 1세션 원자 합산 및 예약/체결 상태. **실자료 직접 수집·실제 개장기간 POST 성공은 아직 미실행**.

## 3. 합성·격리에서 새로 고정할 source 문제
[`tests/test_stage2_nav_source_boundary.py`](../tests/test_stage2_nav_source_boundary.py)는 별도 임시 SQLite/MarketStore에서 **SHA 파손**, **개장 후 확인된 환율을 개장 전 자료로 위장하는 상황**, **오래된 source**, **정확히 +15분 이후 증명 시도**, **캘린더 2027 미검증**을 검증하고, 성공한 NAV/claim이 **0건**인지 확인합니다. 이는 **실제 공급자 통신 PASS가 아닙니다.**

## 4. 팀별 필수 완료 증거
| 담당 | 수용 항목 |
|---|---|
| 기술팀 | Stage4 11건의 구형 성공경로 계약 분리·현재 verified writer를 이용한 동등/강화된 보안 시험 설계, 실제 공개자료 read-only 수집 증거 연결 검토 |
| QA팀 | 동일 최종 SHA로 83개 역사적 node × 두 시계, 신규 시세/Fx 시간·해시 실증, 20%/300,000원/24h/UNKNOWN/재시작·단일 사용자 인증 독립 검사 |
| 디자인팀 | 동일 SHA 실제 FastAPI·Chrome에서 자산/PAPER/실보유/SAMPLE 범위, 승인·UNKNOWN·NAV 미검증 및 401/403 지연 응답/확대 수평 overflow 검증 |
| 총괄팀 | 새 실자료 공급자/타임스탬프 정책, 구형 Stage4 수용계약, release 승인/배포 조건 결정 |

**릴리스 상태:** Stage2 P0 **BLOCKED**. `paper_execution_readiness().verified=false`, 자동 PAPER OFF, 모든 PR Draft, `main` / 운영 DB / 실제 증권계좌 / keyring / Windows / KIS / 실거래 / 배포 미변경.
