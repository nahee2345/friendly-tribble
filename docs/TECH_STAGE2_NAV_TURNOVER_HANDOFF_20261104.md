# [기술팀 → 총괄팀·QA팀·디자인팀] Stage2 P0-2 시작 NAV·20% 원장 통합 후보

2026-11-04 / **공유 대상: 총괄팀 + QA팀 + 디자인팀**  
부모: PR #27 `1ed3b0a0fce742fad90816597b1c09dfc9c214e7`; 작업: PR #29 `tech/stage2-nav-turnover-integration-20261104`.

## 구현 범위 (Draft / 병합·배포 금지)
1. `stage1_nav_proof.py`: NYSE 2026 공식 정규장 시작시각 기준, owner PAPER 명시 계좌 ID 전체를 읽고 4종 계좌·자금/이체/주문 원장 fingerprint, 시세/환율의 provider·URL·`as_of`·`recorded_utc`·`last_verified_utc`·SHA256를 보존. 공급원 캐시 검증은 writer **밖**, 시작 NAV와 proof/session INSERT는 동일 writer **안**. 시작 이후 수집되거나 유효기간 초과된 근거는 거부. 기존 proof/session을 수정하지 않음.
2. `stage1_verified_execution.py`: 명시적 `paused=0`, 검증 proof/시장 시각, 30만원 이상 승인(계좌·종목·방향·수량·금액/24h), owner 전계좌/매수+매도 20% 예약, 필요 USD 가상환전, `virtual_orders`, receipt, FILLED를 **단일 BEGIN IMMEDIATE**에 커밋. 실패주입은 전체 rollback. 동일 owner의 다른 계좌 요청키 충돌 및 RESERVED/UNKNOWN 재체결 거부. 승인대기는 별도 승인 원장에만 저장하며 아직 예약이 아니므로 20%는 예약시부터 소비.
3. `stage1_portfolios.py`: verified 세션으로 호출한 `auto_buy_usd_with_fx`는 공통 writer로 위임(기존 테스트 전용 무검증 경로 잔존).
4. `app.py`: 로그인·CSRF 필수 NAV proof 생성 전용 API. 기존 HTTP 주문은 readiness가 verified=true가 된 후 공통 writer로 위임하도록 연결. **현재 readiness는 false 고정이므로 운영 주문은 기존대로 409 차단**.
5. `stage4_p0_verified.py` 및 `stage4_portfolio.py`: Stage4 verified 경로는 거래단위 원자 writer 사용. 기존 Stage4 5% 매수한도·종목상한·현금유보·가격/FX 변화·전략 위험 해시 검사 유지. 국내 자동주문은 총괄팀의 공통 세션 정책 미결정으로 차단. 기존 legacy Stage4 구현부는 release gate 뒤에서 실행 불가능하게 처리.
6. 신규 synthetic 격리 SQLite/공개 스냅샷 검증 및 주입 시험; `.github/workflows/tech-stage2-nav-atomic-20261104.yml`.

## 실행 불가 조건 / 미해결 정책
- `paper_execution_readiness().verified`는 **여전히 False**. 이 PR은 실제 2026-11-04 개장 시 공급원 관측 및 독립 QA 없이 브라우저에서 PAPER 체결을 허용하지 않음.
- 개발 중인 기존 direct `Portfolios.trade(...verified_turnover_session_date=None)` 및 `auto_buy_usd_with_fx` 역사적 테스트 입력은 유지되어 있음. 운영 API/worker는 새 경로로 전환하지만, 운영 객체 수준 차단 증명 및 전수 회귀가 필요.
- 국내 거래의 미국 세션 분모/날짜 규칙 확정 전 국내 자동매매 금지. 기존 Stage4 5%는 별도 추가 보수 제한이며 사용자 확정 정책으로 변경하지 않음.
- 2027 NYSE, 실 시장 공급자·비정상/추가 휴장, 실제 Chrome, OS SIGKILL/WAL 복구, 사용자 원장/DB/키링, KIS 및 Windows 범위 미검증/미변경.
- 직접 보호해야 할 업계 안전성 점검: 사용자의 데이터·키링·운영 WAL/SHM 보존, 승인 요청 상태 불변, 타 계좌/사용자 분리.

## 팀별 수용 요청
- **총괄팀:** PAPER 모든 owner 계좌 ID 합산, 달력/국내 정책 결정, Stage4 5% 추가제약 유지, NAV source 승인 범위를 검토해 주십시오.
- **QA팀:** 정확한 PR #29 SHA 대상으로 (a) 시작 전/후 시세·FX 신뢰도, immutable proof/원장수정, (b) HTTP+Stage4+FX 세 경로/20%+5%·30만원·24h·중복·UNKNOWN, (c) 두 프로세스 및 reserve/FX/INSERT/mark_filled 직전 강제종료/재시작·백업 및 CSRF 독립 검증을 요청합니다. 본 기술팀 CI는 독립 QA 승인 아님.
- **디자인팀:** readiness=false 유지, 실제 NAV/잔여한도 미표시, PENDING/RESERVED/UNKNOWN/FILLED, HTTP 409 및 401/403 비노출 실제 Chrome 3해상도/접근성 검증을 요청합니다.

**판정:** 소스 변경 후보 제출, `main` 미수정, 자동 PAPER OFF, P0 전체 종결·배포 승인 보류. CI 최신 실행 결과는 별도 동일 SHA 댓글로 공유합니다.
