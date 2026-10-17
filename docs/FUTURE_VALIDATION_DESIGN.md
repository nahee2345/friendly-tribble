# 향후 원장·승인·주문 상태와 알고리즘 검증 설계

최초 작성일: 2026-10-11 (Asia/Seoul)  
최신 설계 감사일: 2026-10-15 (C-20261011-27 P1 순수 domain 구현 반영)

이 문서는 아직 구현하지 않은 단계의 안전 설계와 후속 P1 순수 domain 구현 경계를 함께 기록합니다. 실전 기능 활성화나 투자 정책 승인이
아니며, 모든 수치·한도는 `PROJECT_RULES.md`가 단일 원본입니다.

## 후속 전용 — 미국 정보 영향분석 검증 기준

`UI-MARKET-01`은 현재 공식 원문의 최소 메타데이터 목록과 링크만 제공합니다. 미국 정보가
시장·업종·종목에 미칠 영향을 분석하는 기능은 **이번 구현에 포함되지 않았으며**, 총괄팀의 별도
지시와 독립 검증 전에는 기존 후보평가·7절 보고서·전략·주문에 연결하지 않습니다.

후속 설계는 다음 경계를 모두 충족해야 합니다.

- 확인된 원문 사실, 일반 코드로 계산한 값, 모델의 추론을 필드와 화면에서 분리합니다.
- 방향은 `상승 영향/하락 영향/혼합/판단 불가` 같은 조건부 분류로만 표현하고 “상승 예정”처럼
  확정하지 않습니다.
- 단기·중기 등 영향 기간, 전달 경로, 찬성 근거, 반대 근거와 불확실성을 함께 보존합니다.
- 분석 당시의 source revision, published/collected cutoff, 입력 hash, 규칙·모델 version을
  불변 snapshot으로 저장하고 새 결과로 과거 판단을 덮어쓰지 않습니다.
- 공표시각 이후의 자료만 사용하고 정정 공시는 새 revision으로 평가해 미래정보 누수를 막습니다.
- 검증된 모델과 외부 검증 성능이 없으면 확률·목표가격·예상수익률을 생성하지 않습니다.
- 설명 모델을 쓰더라도 정책 계산, 위험한도 변경, 인증정보 접근, 원장 수정과 주문 권한을 주지
  않습니다. 영향분석 결과만으로 주문안이나 주문을 자동 생성하지 않습니다.

최소 검증에는 동일 입력 재현성, 반대 근거 누락, source 추적, cutoff ±1µs, 정정 전후 version,
자료 부족 시 `판단 불가`, prompt injection을 데이터로만 처리하는 검사와 기간별 out-of-sample
평가가 포함되어야 합니다.

## 정책과 원장 데이터 설계

| 엔터티 | 필수 내용 | 불변조건 |
|---|---|---|
| `PolicyVersion` | 정책 원본 해시, 효력 시작/종료, 사용자 승인 참조 | 일반 알고리즘 변경이 위험한도·금지상품·승인 규칙을 바꾸지 못함 |
| `PolicySnapshot` | 주문안/보고서가 사용한 정책 버전과 미확정 항목 | 과거 보고서를 새 정책으로 소급 재계산해 덮어쓰지 않음 |
| `Account` | 내부 ID·별칭·유형·통화·목적·자료영역·API 지원상태 | 전체 계좌번호의 앱 DB·로그·화면·평문 저장 금지. 공식 API 필수성이 확인된 경우에도 별도 승인된 OS 보호 저장소와 런타임 최소 사용만 허용하고 계좌별 현금·한도 분리 |
| `LedgerEntry` | 불변 ID, 계좌, 자산, 유형, 수량/금액, 통화, 발생/기록시각, 출처, 정정참조 | 내부이체·배당재투자·외부납입·손익을 구분, 삭제 대신 반대/정정 항목 |
| `ValuationSnapshot` | 포지션 평가액, 시세/환율 기준시각, 출처, 품질 | 결측·오래됨·오류를 0으로 바꾸지 않음, 미래시점 사용 금지 |
| `BudgetReservation` | 월 예산, 자금 출처, 주문의도 ID, 예약/해제 상태 | 미체결과 비용을 포함해 여러 주문으로 예산 우회 금지 |
| `Approval` | 승인 type, 불변 target ID/version/hash, 정책 버전, 환경, 승인 사용자·승인/만료/소비시각, 소비 대상·감사 참조 | type 불일치·내용 변경·취소·만료·환경 불일치·기소비 시 무효; 승인 대상 mutation과 일회성 소비는 원자적으로 기록 |
| `OrderIntent` | 고유 ID, 계좌/상품/방향/수량/가격·최대금액, 시세시각, 근거 | 승인 전 전송 불가, DB 고유 제약으로 버튼 연타·재시작 중복 방지 |
| `BrokerOrder` | 증권사 주문번호, 접수/체결/취소 이벤트, 원문 응답 해시 | HTTP 성공과 체결을 구분, 응답 유실은 `UNKNOWN`으로 유지 |
| `EvidenceSource` | 공식 URL/문서 ID, 발표/수집시각, 당시 공개상태, 권리·품질 | 외부 문서 지시 무시, 원문·계산값·AI 설명 분리 |
| `ReportSnapshot` | 기준시각, 자료영역, 정책/가정 버전, 결측 목록, 결과 해시 | 생성 뒤 새 입력으로 기존 보고서를 소급 변경하지 않음 |

금액·규칙 판정은 `Decimal` 기반 일반 코드로만 수행합니다. LLM은 최소 집계값과 근거를 받아
설명 초안을 만들 수 있지만 원장 수정, 승인 생성, 주문 상태 전이는 할 수 없습니다.

## 주문 상태와 복구 경계

계획 상태는 다음과 같습니다.

```text
DRAFT → VALIDATED → APPROVED → SUBMITTING → ACCEPTED
                                      └────→ UNKNOWN
ACCEPTED → PARTIALLY_FILLED → FILLED
         → CANCEL_PENDING
PARTIALLY_FILLED → CANCEL_PENDING
CANCEL_PENDING → CANCELLED / PARTIALLY_FILLED / FILLED / UNKNOWN
별도 종료: REJECTED / EXPIRED
```

- 이 도식은 핵심 경계를 보인 것이며, 모든 증권사 이벤트를 망라한 구현 상태표가 아닙니다.
- 취소 요청 뒤에도 추가 체결이 올 수 있으므로 각 이벤트의 수량·순서를 반영하고 최종 대조 전
  예약 예산을 임의 해제하지 않습니다.
- `SUBMITTING` 전에 주문의도·승인·예산예약을 한 트랜잭션으로 내구 저장합니다.
- 전송 응답을 잃으면 `UNKNOWN`으로 두고 자동 재전송하지 않습니다.
- 새 세션은 `UNKNOWN`/미완료 주문과 실제 주문·체결·잔고를 먼저 대조합니다.
- 취소 요청만으로 현금을 풀지 않고 잔여수량과 최종 상태를 확인합니다.
- 모드, 계좌, 정책, 시세시각, 자금 출처 중 하나라도 승인 내용과 달라지면 전송을 막습니다.
- DB 기록 실패, 잔고 불일치, 시세/환율 지연, 환경 혼동 때 신규 주문을 fail closed합니다.
- OFFLINE 빌드에는 이 상태기계의 전송 어댑터를 포함하지 않습니다.

## 단계별 완료 조건 보완

- **A OFFLINE(현재)**: 애플리케이션·테스트 런타임의 외부 호출 0건, 자료영역 분리,
  Decimal·결측·보호자금·보고서 테스트,
  주문 경로 부재와 OFFLINE 표시를 검증합니다.
- **B READ_ONLY**: 계좌유형별 조회 상태가 `VERIFIED`일 때만 연결하고, 주문 메서드/권한이
  존재하지 않음을 테스트합니다. Windows 자격증명 저장소와 실제 계좌 대조는 별도 검증합니다.
- **C PAPER**: KIS가 공식 지원하는 모의 범위에서만 수행합니다. 사용자가 3개월/원칙 6개월
  제안 중 실제 기간과 기준을 확정한 뒤, `PROJECT_RULES.md` 14절의 관련 필수 안전검증을 모두
  통과하고 중복·무승인 주문 0건, 미해결 원장불일치 0건, 장애/복구 근거를 남겨야 합니다.
  현재 코드의 `MANUAL_PAPER_SNAPSHOT`은 로컬 입력 영역일 뿐 단계 C 운영 실적이 아닙니다.
- **D1 LIVE_PILOT**: C 완료 외에 시작일·계좌·자금출처·월별 예산·100만 원 의미를 확정하고
  별도 실전 활성화 및 매 주문 일회성 승인이 필요합니다. 검증 자금은 초기 400만 원의 일부로
  취급하며, 그 잔액·손익과 나머지 초기자산의 서로 겹치지 않는 잔액만 전체 원장에 한 번
  반영합니다.
- **D2 LIVE_APPROVAL**: D1 결과·미해결 이슈 보고, 실제 시작 평가액·현금흐름·계좌 입력,
  최신 제도 확인과 별도 사용자 활성화 뒤에도 건별 승인을 유지합니다.

## 필수 검증 매핑

현재 1차 구현은 아래 항목의 일부만 다룹니다.

| `PROJECT_RULES.md` 14절 | 현재 상태 | 재현 방향 |
|---|---|---|
| 01 현금흐름·보호저축·원 단위 | 구현/자동 테스트 | 0·음수·1원 잉여금, 합계 보존 |
| 02 부분연도·마감일 | 부분 구현 | 2028년 7회 역산 구현, 2034/2043은 보고 경고, 전체 일정은 후속 |
| 03 총수익·배당 중복 | 부분 구현 | 총수익률 함수에 배당 별도 인수 없음; 거래유형 원장은 후속 |
| 04 배분 63/22.5/4.5/10·44.1/18.9 | 구현/자동 테스트 | 코어 분모와 전체 목표를 구분, 연도 경계·합계 100% |
| 05 코어 ±5%p·위성 10% 상한·집중도 | 코어/위성 경계 구현 | 위성 0/10/10.01/14.99% 자동 테스트; 종목/ETF 노출은 후속 |
| 07 위성 B_y 경계 | 경계 계산 구현 | 홀수 경계, 0·미입력; 손실 원장/연도 복구는 후속 |
| 22 결측 날조 방지 | 구현/자동 테스트 | 빈 실제 원장·MISSING·2062 미확정 |
| 23 초기 400만·모의/실제 분리 | 구조 구현 | 선언값은 대조만, 자료영역 혼합 차단; D1 누적원금은 후속 |
| 06, 08~21, 24~28 | 미구현 | 관련 기능이 생기기 전 차단 상태 유지, 단계별 테스트 추가 |

미구현 테스트를 통과했다고 기록하지 않습니다.

## 주간 검토 샘플 보고서

실제 운영 기능이 추가되면 본문 7개 절을 유지하고 아래처럼 작성합니다.

1. **요약 3줄**: 기준시각/모드, 가장 큰 위험·결측, 필요한 사용자 결정
2. **자산 현황**: 계좌/자산별 평가액, 목표·실제·차이, 자료 품질과 기준시각
3. **목표 대비**: 최초 목표, 전년도 확정 목표, 최신 전망, 5% 가정선, 85% 경고선,
   누적 외부납입과 투자손익
4. **배당**: 실제 세후 수령, 사용자 공식 비교지표, 인출 조건 반영 추정 또는 계산 불가
5. **위성**: 연초 저장 B_y, 측정손실·근거 원장, 50/100% 상태와 허용 행동
6. **주문안과 이유**: 주문안 해시·승인/만료·비용·자금출처·전후 비중 또는 차단 사유
7. **위반·주의**: 정책위반, 결측, 제도 미확인, 데이터 라이선스, 정책 결정 요청

부록에는 이번 주에 실제로 새로 공개된 가격·공시·뉴스·기업지표만 출처와 함께 표시합니다.
재무가 갱신되지 않았으면 “변경 없음/재사용”이라고 씁니다. 현행 알고리즘과 변경 후보의
동일 시점 비교표, 반대 근거, 표본 한계, 예상 영향, 복구방법을 별도 부록에 둡니다.

## 알고리즘 변경 검증 절차

1. 데이터 갱신과 계산식/가중치/매매규칙 변경을 분리합니다.
2. 변경 후보에는 이유, 근거 출처, 영향범위, 정책 호환성, 롤백 버전을 붙입니다.
3. 당시 공개된 데이터만 사용하고 공시 발표시각·정정시각을 반영해 미래정보를 차단합니다.
4. 개발구간, 시간순 walk-forward 구간, 한 번만 쓰는 외부 검증구간을 분리합니다.
5. 현행 버전과 후보를 같은 현금흐름·수수료·환전·체결지연 조건으로 나란히 실행합니다.
6. 총수익 외에 최대낙폭, 변동성, 회전율, 비용, 목표비중 오차, 결측률, 규칙위반,
   중복/무승인 주문을 평가합니다. 안전 위반 0건을 먼저 확인합니다.
7. 검색한 변수·후보 수와 민감도를 기록하고 같은 기간 반복 선택의 과최적화를 점검합니다.
8. 통과 후보도 자동 적용하지 않고 사용자 승인을 받은 지정 시점부터 새 주문안에만 씁니다.
9. 문제가 생기면 신규 주문부터 잠그고 실제 계좌/미체결을 대조한 뒤 이전 버전으로 복구합니다.

## 추가로 확정할 사항

기존 미확정 목록에 더해 다음을 사용자와 실전 전에 정합니다.

- 주간 검토 요일·시간, 이용 가능한 자료와 유료 데이터 예산·사용권
- 최대 감내손실률과 회복 대기기간
- 1회 주문 한도를 매도에도 적용할지 여부
- 월 예산과 리밸런싱 매도대금 재사용 승인 방식
- 동일 종목 하루 1회 규칙의 계좌합산·정정·재주문 범위
- 승인 만료시간, 시세 유효시간, 지정가 괴리·호가 스프레드 기준
- 백업 암호화·복원 대조 절차와 Windows 사용자 데이터 경로

## Claude 독립 검증 절차

```text
env -u PYTHONPATH -u AMENT_PREFIX_PATH .venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python -m asset_assistant --input examples/offline_sample.json --output /tmp/claude-offline-report.md
```

Claude는 Ubuntu 결과를 Windows PASS로 바꾸지 않고, 빈 `MANUAL_ACTUAL`, 시각 불일치,
입출력 동일 경로, Markdown 줄바꿈/파이프, 생활비 부족, 계획 범위 밖 연도, 코어/위성의
분리 판정, 홀수 위성 경계 표시, 비정상·중복 JSON과 계좌번호형 별칭을 별도로 재현합니다.
실제 계좌·키·토큰·네트워크·주문은 사용하지 않습니다.

## C-20261011-21 — 내부 PAPER 개발 준비 통합 명세

이 절은 2026-10-14 총괄팀 지시를 현행 코드·문서·테스트와 대조한 **개발 준비 및 설계
초안**이다. 소스, 테스트, 의존성, SQLite schema·운영 자료, `PROJECT_RULES.md`, 운영 설정은
변경하지 않았다. 따라서 아래의 `제안` endpoint·table·상태·계산식은 현재 앱에 존재하지
않으며, 자동 모의투자 완성이나 실운영 검증 결과가 아니다.

설계 근거 식별자는 다음과 같다.

- 정책 단일 원본: `PROJECT_RULES.md`, SHA-256
  `04a44f468ad56bee3f85f51174e05269b8daff576ce89bd6c71eca1e4fc5af20`.
- 최신 구현 검증: `C-20261011-20` / `V-20261011-15`. M1·L1~L3와 O1·O3·O4는
  해당 범위에서 종결됐지만 실제 Secret Service, 사용자 `serve`, 실제 공급자 장기 운영은
  여전히 `NOT RUN`이다.
- 이번 총괄팀 지시 원문: SHA-256
  `9444b111dc02dd6d4adf1f292c3970514834d364e05069225884d2e0f79e5bdb`.
- `MANUAL_PAPER_SNAPSHOT`은 사용자가 입력한 정적 자료영역이다. 여기서 설계하는
  `INTERNAL_PAPER` 원장·승인·가상체결과 같지 않다. KIS 증권사 모의계좌도 또 다른 영역이며
  이번 설계는 계좌 조회·주문을 포함하지 않는다.

### 현행 구현·검증 및 정책 차이

상태는 `구현 및 검증 완료`, `구현됐으나 실제 환경 검증 필요`, `일부 구현`, `미구현`,
`자료·승인 대기`, `현재 범위 제외`로만 판정한다.

| ID | 요구 | 실제 연결 파일·함수·endpoint | 관련 검증 | 상태 / 실제 실행 | 확정 정책과 차이·필요 변경 / 선행조건 |
|---|---|---|---|---|---|
| PA-BASE-01 | localhost 단일 사용자 기반 | `wealthy_server/local_runtime.py`, `database.py`, auth/session repositories | `test_web_local_runtime`, W2·W4·X1 회귀 | 구현 및 검증 완료 / fixture Chrome PASS | PAPER repository도 사용자 정확히 1명, 0600 DB·WAL·SHM, symlink·타 UID fail-closed, 열린 SQLite fd 잠금 보존을 재사용해야 함 |
| PA-BASE-02 | 민감정보 보호 | `local_credentials.py`, OS Secret Service 경계 | memory keyring fixture, O1/W3 | 구현됐으나 실제 환경 검증 필요 / 실제 Secret Service NOT RUN | PAPER는 비밀을 새로 요구하지 않음. 향후 공급자 키는 기존 경계만 재사용하고 API·로그·브라우저에 원문 반환 금지 |
| PA-DATA-01 | SAMPLE/PAPER/실제 완전 분리 | scenario `data_scope`, 보고서·portfolio 자료영역; `paper.py`의 독립 `PaperScope.INTERNAL_PAPER` | API/browser DATA-01, `test_paper` scope·genesis | 일부 구현 / 기존 영역과 P1 메모리 scope 분리 PASS | 운영 DB 참조/owner는 아직 없음. `MANUAL_PAPER_SNAPSHOT`을 PAPER 거래 이력으로 승격하거나 병합 금지 |
| PA-DATA-02 | 시장정보 메타데이터 | `asset_assistant/market_information.py`, `/api/v1/market-information` | D1~D5, M1/L1~L3, UI-MARKET | 구현 및 검증 완료 / 제한 fixture·일부 과거 실연결 | 제목·링크 메타데이터일 뿐 가격·재무 점수나 체결 근거가 아님. 자동 PAPER 입력으로 연결 금지 |
| PA-DATA-03 | 가격·거래량·재무 record | `providers.py`, `market_data.py`, `automation.py`, candidate assessments | provider/automation/cutoff tests | 일부 구현 / 공급자별 fixture, 과거 제한 연결 | 현 KIS는 국내 현재가 관측, SEC는 지정 concept 수집 골격이다. 미국 일봉·조정/원시 구분·기업행동·ETF 구성·point-in-time 전략 feature가 필요 |
| PA-STR-01 | 현행 후보 평가 mix | `StrategyKind`, `StrategyMix`, `assess_configured_candidates`, `/candidate-assessments` | strategy/automation/API/UI tests | 구현 및 검증 완료 / fixture | 현재 8종은 `광범위 지수·퀄리티·가치·배당성장·저변동성·모멘텀·위성 테마·채권/현금성`; 새 PAPER 8종과 1:1이 아니며 목표예산도 아님 |
| PA-STR-02 | PAPER 8전략·실제 예산 | `paper.py`의 `PaperStrategyKind`, 불변 strategy/budget version·배정 예산 | `test_paper` 예산 격리·14일 적용 경계 | 일부 구현 / P1 메모리 domain PASS | 8전략 종목선정·시장자료 기반 실제 비중은 미구현. 평가 가중치와 예산 ledger를 분리하고 기존 mix 자동 변환 금지 |
| PA-RISK-01 | 코어·위성·집중·금지상품 | 기존 `calculations.py`·`validation.py`·7절 report; P1 `paper.py`의 `assess_paper_risk` | 기존 F1~F5·R1~R3·2027, P1 위험 경계 `test_paper` | 일부 구현 / 기존 offline 검사와 P1 메모리 gate PASS | P1은 명시 분류, PAPER-only NAV, 5%·40/50%·10%, 금지·미분류·위성 손실 결측을 fail-closed 처리한다. 분모 정책 입력 미확정 시 차단하며 운영 persistence·실자료 연결은 미구현 |
| PA-STAGE-01 | 내부 PAPER와 정책 단계 C·D1의 관계 | 운영 runtime 없음; P1 memory domain만 존재, 정책 단계 C는 KIS 모의환경 | 단계 C credit 부여 0 문서·test scan | 정책 충돌·사용자 결정 대기 | P1 내부 PAPER 결과는 단계 C 통과로 표시하지 않는다. 내부 PAPER는 기본적으로 단계 C의 선행·보완 증거일 뿐 대체나 통과 증거가 아니며 D1 전환 근거·월 승인예산 적용은 별도 확정 전 차단 |
| PA-LEDGER-01 | KRW 4,000,000 내부 PAPER 원장 | `paper.py`의 메모리 portfolio/cash/lot/append-only event·정정 | `test_paper` genesis·USD/FX·부분매도·손익·정정 | 일부 구현 / P1 단일 프로세스 메모리 PASS | full double-entry balancing journal·기업행동 전용 계산·영속 대조는 미구현 |
| PA-LIMIT-01 | 세션 시작 NAV의 일 20% 총거래 한도 | `PaperDailyLimit`, 검증 opening valuation, used/reserved 분리 | `test_paper` 800,000원 ±1원·매수+매도·thread 동시성 | 일부 구현 / P1 메모리 PASS | 운영 DB restart·다중 프로세스 원자성은 미검증 |
| PA-APP-01 | 후보군·30만원 이상 개별 거래·새 root 목적 승인, 24h | P1 `CANDIDATE_SET`·`LARGE_TRADE`·`NEW_ROOT_PURPOSE`, 불변 target/hash·감사 | `test_paper` type 교환·정확 만료·stable-root 집계·NEW_ROOT 동시 1회 소비/rollback | 일부 구현 / P1 메모리 PASS | API·CSRF·영속 다중 프로세스 소비는 미구현. 버튼 상태만으로 승인 생성 금지 |
| PA-CAND-01 | 1~4순위·대체·재생성 | P1 `CandidateSet`/rank state와 확정 원인 fail-closed gate | `test_paper` 일시 사유 후순위 0건·활성/체결 선순위 우회 차단 | 일부 구현 / 제공 후보 관리만 PASS | 후보 생성 알고리즘·공식 근거 수집은 미구현. 재생성은 항상 새 후보군 승인 필요 |
| PA-RES-01 | 현금·일한도 예약과 해제 | P1 copy-on-write memory transaction의 cash/cap/strategy/SELL lot 예약 | `test_paper` 동시 한도·6+5/6+4·부분체결·UNKNOWN 유지 | 일부 구현 / 단일 프로세스 thread 원자성 PASS | 운영 DB transaction/unique key·crash durability·다중 프로세스는 미구현 |
| PA-FILL-01 | PC 실행 조건의 가상체결 | P1 명시 가격·환율·세션 입력의 run/attempt/fill state; broker/order endpoint 없음 | 조건 재검사·과거시각 거부·중복 fill·0시도/부분 run | 일부 구현 / 순수 가상체결 코드 PASS·외부 거래 0회 | 실제 quote adapter·scheduler·broker 연결 없음. 꺼진 동안의 시가나 뒤늦은 자료로 소급체결 금지 |
| PA-REPORT-01 | 15시 수집·16시 7절 보고 | `AutomationService`, reports/jobs, 7절 renderer | fake clock/scheduler/API/Chrome | 일부 구현 / 실제 정시 발화 NOT RUN | 기존 보고서는 PAPER 원장·승인·체결을 모름. PAPER 자료임을 모든 카드에 표시하고 실제 자산과 합산 금지 |
| PA-CADENCE-01 | 실행 중 시장정보 30분 확인 | 현행 provider 최소 간격은 SEC EDGAR 1,800초, 그 밖의 현행 시장정보는 3,600초 | cooldown/deadline/claim 회귀 | 일부 구현 / PAPER 연결 미구현 | 30분 scheduler tick과 공급자별 실제 호출 cadence는 같은 뜻이 아니다. 3,600초 공급자는 중간 tick에 `WAITING`을 유지하고 제한을 우회하지 않도록 별도 설계 필요 |
| PA-NOTE-01 | 일별 오답노트 | 없음 | 없음 | 미구현 | 관측 전 예측 snapshot과 관측 결과를 분리하고 손익≠예측 적중. 전략 자동 변경 권한 없음 |
| PA-NOTIFY-01 | 내부 알림함 | `/notifications`, 현재 job/report derived UI | API/Vitest/browser | 일부 구현 | 세 승인 type별 필요·거절·만료·무효·소비, 대조·실패 event가 해당 원자적 commit 뒤 생성되도록 확장. popup/OS/WebPush 금지 |
| PA-BACKUP-01 | PAPER 이력 백업·복원 | `tools/web_local_backup.py`의 현행 두 DB | backup/restore·X1 | 일부 구현 / fixture | 새 PAPER table을 manifest/schema/count/hash 대조에 포함하고 online snapshot 일관성을 독립 검증해야 함 |
| PA-ORDER-01 | 증권사 전송 | endpoint·adapter 없음 | ORD-01/02 scan | 현재 범위 제외 | 실제·KIS 모의 주문 모두 금지. 내부 PAPER executor는 network 권한이 없는 별도 component여야 함 |

### 승인 근거 분류

분류는 `(1) 직접 사용자 승인 원문 확인`, `(2) 기술팀이 승인 이력을 보고했으나 원문 미확인`,
`(3) 기술 제안`, `(4) 미확정`이다. 한 항목 안에서도 원칙과 숫자의 근거가 다르면 나눠 적는다.
총괄팀 지시가 말한 별도 “기술팀 최종 보고” 원문은 저장소와 확인 가능한 첨부에서 발견되지
않았다. 따라서 분류 2는 승인 이력 주장을 부정하지 않되 `effective_value=null`,
`enforcement=BLOCKED`로 보존하는 상태다.

원문 대조가 필요하면 사용자는 채팅에 내용을 붙이거나 비밀정보를 보내지 않고, 로컬의
release 제외 경로 `user_data/approval_evidence/paper/`에 다음 자료를 둔다. 디렉터리는 `0700`,
파일은 `0600`을 권장하며 이 경로는 현재 `.gitignore`와 소스 ZIP 제외 규칙에 포함된다. 이번
문서 작업은 디렉터리를 만들거나 파일을 읽지 않았다.

- 기술팀 최종 보고 원본과 report ID·version·작성일·작성 주체
- 그 보고서가 인용한 부록·설정표·결정표 원본
- 인용된 사용자 승인 원문/export와 앞뒤 맥락·승인 시각
- 파일명·SHA-256·문서 간 참조를 적은 manifest(키·계좌·개인 금융자료는 포함 금지)

기술팀 보고에 적힌 승인 주장은 원문과 독립 대조하기 전까지 분류 2이고, 사용자가 직접 제공한
승인 원문을 문맥·hash와 함께 확인한 경우만 분류 1이다. 이미 분류 1로 확인한 결정은 다시
묻지 않는다. 자료가 없더라도 nullable policy와 fail-closed 상태 계약은 설계할 수 있으나,
미확정 값을 실행 가능한 기본값으로 바꾸지 않는다.

| 항목 | 분류·확인 근거 | 설계 반영 / 실행 상태 |
|---|---|---|
| 초기 PAPER 4,000,000원, 영역 완전 분리 | 1 — 이번 총괄팀 원문 §5 | P1 순수 메모리 source의 genesis·scope로 구현. 운영 DB에는 아직 없음 |
| 8전략 선택·혼합, score와 예산 분리, 변경 승인+2주 제한 | 1 — 이번 원문 §5 | 불변 strategy/budget version으로 설계. 2주의 기준점은 원문에 없으므로 `earliest_effective_at=max(approved_at, previous_effective_at+14일)`을 분류 3로 제안하며 사용자 확정 전 실행 차단 |
| 세션 시작 NAV 20%, 매수+매도, 승인 거래 포함 | 1 — 이번 원문 §5·§8 | 일한도 snapshot 및 reservation 불변식으로 채택 |
| 거래 300,000원 이상 개별 승인, 승인 후 24h | 1 — 이번 원문 §5 | candidate-set 승인과 별도. 경계 `>= 300000 KRW`; 비용·환율 판정 방식은 아래 사용자 결정 필요 |
| 실제 경제적 목적 변경의 새 root 전용 승인, 승인 후 24h | 1 — 2026-10-15 총괄팀 K1 보완 지시 | `NEW_ROOT_PURPOSE`는 candidate-set/large-trade 승인과 별개인 1회성 승인. 동일 목적·근거 불충분이면 기존 root 유지 또는 보류하고 모든 거래·위험 gate를 다시 검사 |
| 후보 1~4·선순위 불가·재생성 재승인 | 1 — 이번 원문 §5·§9 | 상태기계로 채택. 재생성 최대 횟수는 포함하지 않음 |
| 비용·환전비용 반영 원칙 | 1 — `PROJECT_RULES.md` 예산·성과·체결 모형 | 모든 예상/실제 PAPER entry에 비용 항목 필요 |
| 구체 수수료율·슬리피지·환전 spread | 2 — 총괄팀이 기술팀 승인 이력 존재를 전달, 원문·값 미확인; 동시에 4 | 설정 `null`, 체결 활성화 차단. 임의 0원 금지 |
| 정수 1주 | 1 — `PROJECT_RULES.md`의 소수점 거래 미가정 | 수량은 양의 정수. 잔돈은 해당 전략 KRW/USD cash로 유지 |
| 최대 12종목 | 2 — 기술팀 이력 보고만 있고 원문 없음 | 설계 parameter 후보로만 기록; 값 `null`, 한도 적용 금지 |
| 후보 재생성 횟수 | 재생성·재승인은 1, 최대 횟수는 2/4 | 무한 loop 방지는 한 run 1회 제안(3)이나 사용자 확정 전 자동 실행 금지 |
| 추가매수 간격 | 같은 종목 하루 2회 이상 금지는 1; 별도 간격은 2/4 | 현행 더 엄격한 차단 유지. 기간 숫자 미확정 |
| 유동성·변동성·급락 | 지표 사용·전체 -15% 경보는 1; 수치 필터는 2/4 | -15%는 자동 정지·손절 신호가 아님. 아래 전략 제안값은 3이며 활성화 금지 |
| 기업·산업 노출 | 개별종목 전체자산 5%는 1; 산업 cap은 2/4 | 기업 5% 강제. 산업 수치값은 승인 전 차단/경고만 |
| 전체 MDD 정지·회복 | 2/4 — 기술팀 이력 보고, 원문 없음; 정책은 -15%를 경보로만 정의 | 자동 정지·재개 기준으로 쓰지 않음. 사용자 결정 전 PAPER 위성 신규매수도 잠금 |
| 전략 개선 검증기간 | 2/4; 3~6개월은 현행 문서의 제안(3), 2주는 변경 제한 | 오답노트로 자동 적용 금지. 검증기간 사용자 확정 필요 |
| 백업·알림 보관기간 | 2/4 — 원문·기간 없음 | 기존 안전 백업과 내부 알림 채널만 재사용; 시장정보 1년을 전용하지 않음 |
| 내부 PAPER와 단계 C·D1 관계 | 1 — `PROJECT_RULES.md` 253~254행의 단계 C는 KIS 모의환경, D1은 모의 검증 통과 뒤 | 내부 PAPER는 선행·보완 자료로만 표시하고 단계 C를 대체하거나 통과시키지 않음. D1 근거 인정은 정책 변경으로 별도 사용자 결정 |
| 월 승인 신규 투자 예산의 PAPER 적용 | 4 — `PROJECT_RULES.md` 229행은 1회 주문 한도를 월 승인 신규 투자 예산에 묶지만 내부 PAPER 적용·매도 범위 미확정 | 초기 4,000,000원과 전략별 PAPER 예산을 월 승인 신규 투자 예산으로 간주하지 않음. 별도 PAPER 월 예산과 매도 적용 여부 확정 전 proposal 실행 차단 |
| PAPER 5%·ETF 40/50%·위성 10% 분모 | 4 — 실제 정책은 모든 투자계좌 합산 전체 자산을 기준으로 하나 내부 PAPER는 실제 영역과 분리 | PAPER-only 분모를 별도 결정하기 전 집중·위성 신규매수 차단. 실제 자산 평가액을 PAPER 분모나 분자에 가져오지 않음 |

### 코어·위성 귀속과 PAPER 위험분모 초안

이 절은 `PROJECT_RULES.md` 110행, 140~141행, 224~225행을 PAPER에 적용하기 위한
**fail-closed 초안**이다. 전략 이름만으로 코어가 되지 않으며 `instrument type + 추종 범위 +
strategy purpose + policy version`을 함께 판정한다. 다음 우선순위를 적용한다.

1. 레버리지·인버스·파생·신용·공매도 등 금지 상품은 귀속 전에 `INELIGIBLE`이다.
2. 코어 후보는 상품 식별자·기초지수·구성·비용·상품유형을 확인한 비레버리지 ETF 가운데
   S&P500, 별도 확정된 글로벌 지수, KOSPI200·고배당 ETF, 별도 승인된 일부 배당성장 상품,
   한국·미국 중기 국채 ETF, 승인된 MMF·단기채 범위뿐이다. `CORE_CANDIDATE`는 매수 승인이
   아니며 자료·집중·예산·승인 gate를 모두 다시 통과해야 한다.
3. 개별 성장주와 테마·섹터 ETF는 `SATELLITE`다. 그 밖의 개별 보통주, 좁은 지수 ETF,
   배당성장 상품을 포함해 원문만으로 귀속을 확정할 수 없는 상품은
   `UNCLASSIFIED_ASSUMED_SATELLITE`로 저장한다. 기존 노출 계산에서는 위성 분자에 포함하지만
   승인된 위성 분류로 승격하지 않으며 신규매수는 `POLICY_INPUT_REQUIRED`로 차단한다.
4. 상품 하나가 여러 전략에 들어가도 귀속은 한 policy snapshot에서 한 번만 정한다. 전략별
   lot는 보존하되 전체 instrument·기업·ETF 기초노출과 위성 분자는 중복 없이 합산한다.

| PAPER 전략 | 허용 가능한 귀속 초안 | fail-closed 경계 |
|---|---|---|
| 장기 지수 | 검증된 S&P500·확정 글로벌·KOSPI200 broad ETF는 `CORE_STOCK_CANDIDATE` | 좁은 지수·구성 미확인 ETF는 미분류 위성 계상·신규매수 차단 |
| 가치 | 전략 이름은 귀속을 바꾸지 않음. 개별 보통주는 원문상 코어 근거가 없어 미분류 위성 계상 | ETF도 별도 broad core 상품 요건을 통과하지 않으면 차단 |
| 우량 성장 | 전략 이름은 귀속을 바꾸지 않음. 개별 보통주는 미분류 위성 계상 | quality score로 core 승격 금지 |
| 성장주 | 개별 성장주와 성장 테마·섹터 ETF는 `SATELLITE` | 위성 분모·손실 재개조건 확정 전 신규매수 차단 |
| 고배당 | 검증된 KOSPI200·고배당 ETF는 `CORE_STOCK_CANDIDATE` | 개별 고배당주는 미분류 위성 계상; 높은 배당률로 core 승격 금지 |
| 배당성장 | 사용자가 상품 단위로 승인한 “일부 배당성장” ETF만 core 후보 | 승인 대상·상품유형이 불명확하면 미분류 위성 계상·차단; 개별주는 자동 core 금지 |
| 모멘텀 | 기초 상품의 기존 귀속을 그대로 사용 | momentum score나 매매 시점으로 core/satellite를 변경하지 않음 |
| 자산배분 | 승인 broad 주식 ETF, 중기 국채 ETF, MMF·단기채는 각각 core 주식·채권·현금성 후보 | 개별주·테마·섹터는 위성, 상품유형·기초노출 미확인은 미분류 위성 계상·차단 |

PAPER의 5%·ETF 40/50%·위성 10% 한도는 분자와 분모가 모두 `INTERNAL_PAPER` 안에 있어야
한다. 분자는 같은 instrument의 전략별 lot와 활성 제안 뒤 예상 노출을 중복 없이 합산한다.
분모 선택지는 `검증한 현재 PAPER NAV`, `불변인 미국 세션 시작 PAPER NAV`, 또는 사용자가
명시한 다른 **PAPER-only** 기준이다. 어느 기준을 각 한도에 적용할지는 미확정이며, 값이
`null`인 동안 관련 신규매수는 `POLICY_INPUT_REQUIRED`다. 실제 계좌·실제 자산·보호저축·
초기자산 선언값을 PAPER 분자나 분모에 더하거나 두 영역의 초과분을 상계하지 않는다.
한도 초과나 미분류는 신규 BUY 차단·조정안 보고 사유이지 승인 없는 자동매도 사유가 아니다.
독립 검증은 8전략 각각에 보통주·승인 broad ETF·테마/섹터 ETF·미분류 상품을 대입하고,
5%·40%·50%·10%의 직전/정확/초과, 전략·lot 분할 합산, 분모 null 차단, 실제 자산 fixture를
바꿔도 PAPER 판정 입력·결과가 바뀌지 않음을 포함한다.

### 정책 단계 C·D1 및 월 예산 관계

- 정책 단계 C는 KIS가 지원하는 실제 모의투자 환경에서 최소 3개월, 원칙적으로 6개월을
  제안하고 기간·통과조건을 사용자가 확정하는 단계다. 내부 PAPER는 현재 **선행 engineering
  simulation 및 보완 증거**이며 단계 C의 대체재가 아니다.
- 내부 PAPER 결과는 단계 C `PASS`, “KIS 모의 검증 완료”, D1 진입 가능으로 표시하지 않는다.
  이를 단계 C의 필수 선행조건·보완자료·대체자료 중 무엇으로 인정할지와 D1 근거에 포함할지는
  정책 변경으로 별도 사용자 결정을 받아야 한다. 결정 전 D1 gate는 계속 닫힌다.
  상태 계약은 `stage_c_relationship=PREPARATORY_SUPPLEMENT_ONLY`,
  `stage_c_credit=NOT_GRANTED`를 기본으로 한다.
- `PaperBudgetVersion`은 전략별 내부 배정일 뿐 `PROJECT_RULES.md` 229행의 “해당 월 승인된
  신규 투자 예산”이 아니다. PAPER 매수에 적용할 월 예산, 1회 한도, 매도 적용 여부가 모두
  확정되기 전에는 초기 4,000,000원을 월 예산으로 추정하지 않고 실행 proposal을 차단한다.

### 논리 데이터 계약 초안

아래는 SQLite 이행 전 검토할 논리 model이다. 모든 ID는 앱이 발급한 opaque UUID이고, 금액·
비중·가격·환율은 문자열로 직렬화한 유한 `Decimal`이다. 수량은 정수 주, 시각은 aware ISO
8601과 UTC epoch microseconds를 함께 저장한다. 원장은 UPDATE/DELETE 대신 반대 entry와
`corrects_id`로 정정한다.

| 엔터티 | 필수 필드 | 불변조건 |
|---|---|---|
| `PaperPortfolio` | `portfolio_id`, owner, `scope=INTERNAL_PAPER`, base currency KRW, created_at, status | SAMPLE/MANUAL_ACTUAL/MANUAL_PAPER_SNAPSHOT 및 KIS 계정과 병합 금지; genesis cash 4,000,000 KRW 한 번만 |
| `PaperStrategyVersion` | version/hash, 8개 전략 rule version, 승인 ref, approved/effective_at, prior_version | score weight와 budget allocation을 별도 객체로 참조; 기존 version 수정 금지; 제안식 `effective_at >= max(approved_at, previous_effective_at+14일)`은 기준점 승인 전 차단 |
| `PaperBudgetVersion` | 전략별 KRW 배정, 합계, unallocated, approved_at | 8개 합계가 가용 PAPER equity를 초과하지 않음; 전략 간 자동 이전 금지 |
| `PaperMonthlyBudgetVersion` | policy version, month, approved new-investment amount, BUY/SELL applicability, approved_at | 전략별 예산·4,000,000원 genesis와 별개; 값이나 적용범위가 null이면 관련 proposal 차단; 실제 월예산 import 금지 |
| `PaperLedgerEntry` | entry ID, event type(`DEPOSIT/FX/BUY/SELL/DIVIDEND/FEE/CORPORATE_ACTION/RESERVE/RELEASE/CORRECTION`), strategy, instrument, qty, money, currency, occurred/recorded_at, source | double-entry 합계와 통화 단위 보존; 미실현손익은 cash가 아님; 비용과 배당을 가격손익과 구분 |
| `PaperPositionLot` | strategy, instrument ID, acquisition event/UTC, integer qty, unit cost/currency, remaining qty | 같은 instrument가 여러 전략이면 lot를 별도 보존하되 전체 실제 instrument 노출은 합산해 위험검사; `available_qty = remaining_qty - active_sell_reservations`; 수량 이중 합산·음수 금지 |
| `PaperValuationSnapshot` | session ID, quote/FX source record IDs, as_of/cutoff, cash, positions, NAV, quality | 세션 시작 전 검증한 snapshot은 일한도용으로 불변; 결측·오래됨을 0으로 대체하지 않음 |
| `PaperRiskClassification` | policy version, stable instrument ID, instrument type/index/theme/sector evidence, result(`CORE_*`/`SATELLITE`/`UNCLASSIFIED_ASSUMED_SATELLITE`/`INELIGIBLE`), reason | 전략별로 중복 분류하지 않음; 미분류는 위성 분자에 한 번 포함하고 신규매수 차단; score로 승격 금지 |
| `PaperRiskLimitSnapshot` | policy version, as_of/cutoff, limit type(5%/ETF40/ETF50/satellite10), PAPER-only denominator kind/value, post-proposal numerator, status | 실제 자산 ref 금지; denominator kind/value가 null이면 관련 신규매수 `POLICY_INPUT_REQUIRED`; snapshot 뒤 proposal 변경 시 재계산 |
| `PaperDailyLimit` | US session date, session calendar source, opening NAV, 20% limit, executed gross, active reserved gross, revision | KST 자정 초기화 금지; `executed + reserved <= limit`; buy+sell gross 합산; FX 자체는 중복 거래액으로 세지 않음 |
| `ExecutionRun` | `execution_run_id`, `portfolio_id`, `scope_kind=PORTFOLIO_US_SESSION`, US session date, candidate/set refs, 불변 included proposal ID/version 목록과 hash, 실행창 start/end UTC µs, started/ended UTC µs, state, `attempted` boolean, 영속 attempt event refs와 distinct `attempt_count`, attempted proposal IDs, `completion_kind=NONE/FULL/PARTIAL`, terminal reason code | 한 PAPER portfolio의 한 US session만 포함하며 proposal 단위 run이 아님; `attempted == (attempt_count > 0)`; `READY` 뒤 목록·hash 변경 금지; 동일 idempotency event 재시도는 count를 늘리지 않음; run `MISSED_WINDOW`는 창 종료 때 `attempt_count=0`인 경우만, 1회 이상 시도 후 결과가 모두 확정되면 `COMPLETED(FULL/PARTIAL)`, 하나라도 미확인이면 `RECONCILIATION_REQUIRED`; proposal별 상태와 예약은 별도로 보존 |
| `TradePurpose` | `trade_purpose_id`, 불변 `stable_root_trade_purpose_id`, `purpose_version`/`purpose_version_hash`, `parent_trade_purpose_version_id`와 그 parent ref로 형성되는 descendant 연결, 새 목적의 `changed_from_stable_root_trade_purpose_id`, strategy/budget/policy refs, intended economic effect, 새 root 발급의 `new_root_user_approval_ref`·`new_root_audit_ref`·`root_overlap_split_check_result` | 동일 경제적 목적의 대체·재생성·재승인은 같은 root의 새 version; 새 root는 실제 경제적 목적 변경이 입증되고 target과 정확히 일치하는 미취소·미만료·미소비 `Approval(type=NEW_ROOT_PURPOSE)` 및 `root_overlap_split_check_result=PASS`를 같은 transaction에서 확인·1회 소비한 경우만 발급; 승인돼도 동일 목적이면 기존 root 유지; 임의 root 발급으로 누적 판정액 초기화 금지 |
| `CandidateSet` | set/version/hash, `trade_purpose_id`/`purpose_version`/`stable_root_trade_purpose_id`, 1~4 candidates, evidence cutoff, created_at, regeneration_of, status | rank 1~4 중복 금지; 내용 변경은 새 set; 재생성은 이전 불가 이유·변경점·위험 영향을 보존하고 같은 경제적 목적의 root를 바꾸지 않음 |
| `TradeProposal` | proposal/version, candidate set/rank, `trade_purpose_id`/`purpose_version`/`stable_root_trade_purpose_id`, side, integer qty, price condition, max gross/cost, target US session, KST intent date, core/satellite class, approval aggregate key, expires, evidence snapshot | 승인 뒤 어떤 필드든 바뀌면 승인 무효; 매도 신호를 다른 종목 매도로 대체 금지; 같은 종목 날짜 claim과 미분류 차단 상태 보존; 같은 경제적 목적의 재생성·재승인으로 root 변경 금지 |
| `Approval` | `approval_id`, type(`CANDIDATE_SET`/`LARGE_TRADE`/`NEW_ROOT_PURPOSE`), 불변 target ID/version/hash, aggregate judgment KRW, decision, `approved_by_user_id`, `approved_at_utc_us`, `expires_at_utc_us=approved_at+24h`, state, `cancelled_at_utc_us`, invalidation reason, `consumed_at_utc_us`/`consumed_by_ref`, audit ref; `NEW_ROOT_PURPOSE` target은 prior root와 서버 발급 proposed stable root ID, target trade-purpose ID/version/hash, 목적 변경 evidence ref/hash·intended-effect diff, `root_overlap_split_check_result=PASS`와 검사 input hash·policy version·`checked_at_utc_us`·cutoff를 포함 | 세 type은 상호 대체 불가; 후보군 승인은 모든 set에 필요하고 같은 집계 키 합 `>=300,000 KRW`이면 `LARGE_TRADE` 추가 승인; `NEW_ROOT_PURPOSE`는 정확히 일치하는 새 `TradePurpose`와 audit 생성 transaction에서 한 번만 소비하며 동시 소비는 하나만 성공; 취소·정확한 만료시각 도달·기소비·target/version/hash/evidence 변경 또는 중복·분할 검사 `FAIL/UNKNOWN`이면 무효 |
| `LargeApprovalGroup` | `portfolio_id`, `stable_root_trade_purpose_id`, stable instrument ID, side, US session date, member purpose/proposal versions, executed+active judgment KRW, group hash | 같은 economic purpose의 descendant version을 모두 같은 group에 합산; member·금액·비용·FX 변경 시 승인 무효; session 끝까지 체결 누계 보존 |
| `PaperReservation` | reservation ID, proposal, side, cash currency/amount, daily-limit KRW, SELL lot별 reserved qty, state, created/released_at, reason | 하나의 proposal에 활성 reservation 하나; BUY cash 또는 SELL lot 수량과 일한도를 한 transaction에서 생성/해제; `UNKNOWN/NEEDS_RECONCILIATION`은 모든 예약 유지 |
| `PaperFill` | fill ID, proposal, observed quote/FX refs, integer qty, price/cost, actual UTC, virtual flag, idempotency key | network/broker ID 없음. 승인 이전 또는 PC off 동안 가격으로 소급 생성 금지; 원장 commit과 원자적 |
| `ReconciliationCase` | proposal/fill/reservation refs, expected/observed, state, evidence | `UNKNOWN/NEEDS_RECONCILIATION`이면 신규 관련 거래를 fail closed하고 자동 재전송·예약해제 금지 |
| `PaperDailyObservation` | signal snapshot, expected condition, observed result, state, cutoff/source IDs | 관측 전 예측 불변; `IN_PROGRESS/MET/NOT_MET/UNASSESSABLE`; 손익과 예측 적중 분리 |
| `PaperNotification` | event ref, safe type/message code, created/read/ack/resolved timestamps | 성공 알림은 원장 commit 뒤 생성; 승인·읽음·문제해결·재활성화를 서로 다른 상태로 저장 |

#### 후속 P1 순수 domain 구현 대응 (C-20261011-27)

후속 사용자 승인을 받아 `asset_assistant/paper.py`에 외부 I/O 없는
`INTERNAL_PAPER` domain과 `PaperRepository` port, `InMemoryPaperRepository`를 구현했다.
구현된 범위는 4,000,000원 genesis, KRW/USD cash와 검증 FX, append-only ledger·lot·평가,
8전략/예산 불변 version과 14일 적용 경계, 명시적 상품분류·PAPER-only 위험검사,
검증 평가 snapshot 기반 session 20% 한도, `TradePurpose`/후보군/proposal, 상호 교환 불가한
세 승인 type, stable-root 집계, cash·SELL 수량·전략예산 예약, 부분 가상체결·실현/미실현손익,
typed 매수 상한/매도 하한 가격조건과 체결 직전 재검사, `ExecutionRun`과 명시적 대조 상태다.
위성 신규매수는 별도 typed 손실 gate가 `PASS`일 때만 허용하고 `UNKNOWN/BLOCKED`를 0이나
통과로 바꾸지 않는다. 결측 정책·가격·FX·원가방식은 실행 가능 값으로 추정하지 않고 차단한다.
근거 테스트는 `tests/test_paper.py`다.

P1 구현의 불변 hash는 후보군·proposal의 실행 관련 내용을 묶고, 입력 sequence를 tuple로
정규화해 호출자가 생성 뒤 list를 바꾸어 우회하지 못하게 한다. 위험 한도에서
`CURRENT_PAPER_NAV`를 선택하면 아직 발생하지 않은 proposal 및 활성 BUY 예약의 최대비용도
분모에서 차감한 보수적 사후 NAV를 사용하고, 세션 시작 NAV 방식은 저장된 opening snapshot을
그대로 유지한다. 기존 위성 노출이 10%를 넘은 경우 새 위성 매수만 차단하고 코어 매수는 각
5%·40%·50% 한도와 나머지 gate로 별도 판단한다. 앞 순위 proposal에 활성 예약·열린 시도·fill이
있으면 영구 실패 clearance를 만들거나 후순위를 활성화할 수 없다.

이 구현은 `RLock`과 copy-on-write가 제공하는 **동일 Python 프로세스의 thread 원자성**만
검증한다. SQLite·다중 프로세스·프로세스 crash/restart 영속성, `ReconciliationCase` 운영
저장, FastAPI/API session·CSRF·owner 보안, React UI, 공급자·scheduler·broker·주문과 자동
운용은 구현하지 않았다. `PaperMonthlyBudgetVersion`, observation, notification도 여전히 논리
설계이며 실행 경로가 아니다. P1 원장은 append-only event와 파생 cash/lot 잔액을 구현했지만,
위 논리 계약의 복식 분개 balancing journal과 계정과목별 debit/credit 대조까지 구현한 것은
아니다. 이는 P2 영속 schema 설계·migration 전에 해결해야 할 명시적 차이다.

통화 규칙은 다음과 같다.

- P1은 공급자 tick·정산 단위를 아직 구현하지 않았으므로 KRW·USD 입력을 임의 반올림하지 않는다.
  유한 `Decimal`만 받고 coefficient 128자리 및 계산 context 지수 범위 안에서 보존한다. 정확성이
  필요한 금액 곱셈·합계에는 `Inexact`를 차단하고, 반복 비례 lot 원가처럼 비종료 나눗셈에만
  지역 `prec=128`, `ROUND_HALF_EVEN` 문맥을 결정적으로 사용하며 마지막 전량 매도에서 잔여 원가를
  정확히 소진한다. 실제 공급자 tick·통화별 표시/정산 반올림과 version은 P3 전에 결정해야 하며,
  결정 전 값을 `ROUND_HALF_UP`이나 소수 4자리로 추정하지 않는다.
- USD 거래의 300,000원 승인 판정과 일한도 예약은 세션 시작 전 고정한 검증 FX snapshot으로
  `ceil(gross USD × KRW/USD + 최대 예상 비용 KRW)`를 사용한다. 실제 비용 차이는 원장에 별도
  기록하되 cap 초과를 허용하지 않으므로 headroom이 부족하면 실행을 차단한다.
- 가용 현금은 `posted cash - active cash reservations`; position 평가액·미실현이익은 현금에
  포함하지 않는다. 환전은 두 통화 entry와 비용 entry를 원자적으로 만들며 전략 간 cash를
  자동 이전하지 않는다.
- SELL은 결정론적으로 선택한 lot마다
  `available_qty = remaining_qty - sum(active SELL reserved_qty)`를 계산한다.
  예약·부분체결·lot 차감·미체결 잔여 예약 갱신은
  한 transaction이고, `UNKNOWN/NEEDS_RECONCILIATION`에서는 수량 예약을 유지해 후속 매도를
  차단한다. 취소·거절·만료라도 실행 시도 없음이 확인된 경우에만 해제한다.
- 300,000원 판정 집계 키는 `(portfolio_id, stable_root_trade_purpose_id, stable instrument_id, side,
  US session_date)`다. 판정액은 해당 키에서 이미 기록된 gross 판정액과 terminal이 아닌
  proposal의 최대 gross+비용 판정액을 합산한다. 대체 proposal은 이전 proposal과의 연결을
  보존해 같은 금액을 이중 집계하지 않되 취소·재생성으로 누적액을 숨길 수 없다. 합이
  `>=300,000 KRW`가 되는 순간 group 전체 hash를 대상으로 `LARGE_TRADE` 승인이 있어야 다음
  실행이 가능하고, 구성·금액 변경 시 승인은 무효다. 같은 business purpose의 version을 새로
  발급해도 root ID는 바뀌지 않으며 동일 session·instrument·side의 이전 체결액과 활성액을
  계속 합산한다. 후보군·제안·purpose 하위 version을 재생성하거나 후보군을 새로 승인해도 root
  ID는 바뀌지 않는다. 새 root는 경제적 거래 목적이 실제로 달라졌다는 별도 사용자 승인,
  audit 근거와 기존 root와의 instrument·side·session 중복/분할 우회 검사를 모두 통과해야 하며,
  사용자가 임의 ID를 입력해 같은 목적의 누적액을 초기화할 수 없다.

후보·후보군 재생성, strategy version 또는 금액 변경, proposal/set/purpose 하위 version 변경·재승인은
그 자체로 경제적 목적 변경이 아니며 새 root 요청·발급 근거가 될 수 없다. `CANDIDATE_SET`이나
`LARGE_TRADE` 승인 ref를 `new_root_user_approval_ref`로 재사용할 수 없다. 사용자가
`NEW_ROOT_PURPOSE`를 승인했더라도 의미 대조 결과 동일 목적이면 승인을 소비하지 않고 기존 root를
유지한다. 목적 변경을 입증할 수 없으면 기존 root를 유지하며, 안전하게 귀속할 수 없으면
`TEMPORARILY_HELD(reason=PURPOSE_CHANGE_UNPROVEN)`로 보류한다.

적법하게 새 root가 발급돼도 새 candidate set 승인, 새 root 집계액의 `LARGE_TRADE` 승인,
미국 session 일일 한도, cash·SELL 수량 reservation, 집중도·코어/위성·금지상품 등 모든 정책·위험
gate를 다시 검사한다. `NEW_ROOT_PURPOSE`는 이 승인·한도를 대체하거나 통과시킨 결과가 아니다.

### 상태 전이·예약·후보 우회 차단

```text
CandidateSet: DRAFT → VALIDATED → WAITING_SET_APPROVAL → SET_APPROVED
             WAITING_SET_APPROVAL → REJECTED / EXPIRED
             SET_APPROVED → EXPIRED / REGENERATION_REQUIRED
             REGENERATION_REQUIRED → EXPIRED / SUPERSEDED

ExecutionRun: DISABLED → READY → RUNNING
              READY → TEMPORARILY_HELD → READY(attempt_count=0)
              RUNNING → TEMPORARILY_HELD → RUNNING(attempt_count>0)
              READY / RUNNING / TEMPORARILY_HELD → USER_STOPPED
              USER_STOPPED → READY / RUNNING(명시적 재활성화+전체 재검증 뒤 count에 따라)
              READY / TEMPORARILY_HELD / USER_STOPPED
                  → MISSED_WINDOW(실행창 종료 && attempt_count=0; run terminal)
              RUNNING / TEMPORARILY_HELD / USER_STOPPED
                  → COMPLETED(attempt_count>0 && 모든 시도 결과 확정; FULL/PARTIAL, run terminal)
              RUNNING / TEMPORARILY_HELD / USER_STOPPED
                  → RECONCILIATION_REQUIRED(시도 결과 하나라도 미확인) → RECONCILED(run terminal)

Proposal: PROPOSED → ELIGIBILITY_CHECKED → RESERVATION_PENDING → RESERVED
          RESERVED → WAITING_LARGE_APPROVAL(>=300,000원) → READY
          READY → TEMPORARILY_HELD → READY
          READY / TEMPORARILY_HELD → USER_STOPPED
          USER_STOPPED → READY(명시적 재활성화 뒤에만)
          READY → PAPER_EXECUTING → PAPER_RECORDED → RECONCILED
          모든 실행 전 상태 → CANCELLED / EXPIRED / MISSED_WINDOW /
                               DEFINITIVELY_UNEXECUTABLE(승인된 영구 reason만)
          PAPER_EXECUTING → UNKNOWN → NEEDS_RECONCILIATION → RECONCILED
```

`ExecutionRun`은 한 `portfolio_id × US session_date`의 불변 proposal 묶음이다. 첫 영속
`PAPER_EXECUTING`/idempotency event를 원장과 함께 기록한 시점을 실행 시도로 세며, 중복 retry는
`attempt_count`를 늘리지 않는다. `COMPLETED`는 orchestration 결과가 모두 확정됐다는 뜻이지 모든
proposal이 체결·성공했다는 뜻이 아니다. 포함 proposal 전부가 시도·확정됐으면 `FULL`, 일부가
미시도 `MISSED_WINDOW`이고 나머지 시도 결과가 확정됐으면 `PARTIAL`이다. run 전체
`MISSED_WINDOW`는 `attempt_count=0`일 때만 쓴다.

실행 시도 결과가 `UNKNOWN`으로 확인되는 즉시 run은 실행창 종료를 기다리지 않고
`RECONCILIATION_REQUIRED`로 전이하며 이후 새 attempt를 받지 않는다. 실행창 종료는 종료시각과
terminal 사유를 보완할 뿐 이 차단을 늦추지 않는다. 불확실한 reservation과 proposal은 함께
`NEEDS_RECONCILIATION`으로 유지한다.

`COMPLETED`, `MISSED_WINDOW`, `RECONCILED`는 해당 `execution_run_id`의 terminal state이고 같은
run을 `READY`로 되돌리지 않는다. `RECONCILIATION_REQUIRED`는 실행·예약 해제를 막는
non-terminal blocked state이며 원장·event·reservation을 명시적으로 대조해 결론과 근거를
저장한 경우에만 `RECONCILED`로 간다. 이후 실행이 필요하면 기존 run을 변경하지 않고 새
`execution_run_id`를 만들며, 새 proposal/set·사용자 승인 필요 여부를 포함해 session, 자료,
정책, 승인, cash/lot reservation의 모든 gate를 명시적으로 다시 검증한 뒤에만 새 run이
`READY`가 될 수 있다. `MISSED_WINDOW` 뒤의 자동 재시도·소급 실행과
`RECONCILIATION_REQUIRED`에서 `READY`로의 직접 전이는 금지한다. 재시작은 불변 목록/hash와
영속 attempt event를 다시 읽어 count·상태를 복구하며 휘발성 메모리 값으로 0으로 되돌리지 않는다.

| run 상태 | 진입 조건 | 허용 출구·발생 이벤트 | 승인 유효성 | cash·SELL 수량·일한도 예약 | 재시작·결과 미확인 |
|---|---|---|---|---|---|
| `DISABLED` | 기능 비활성 또는 필수 정책·자료 계약 미충족 | 별도 구현 승인 뒤 모든 gate와 proposal 목록을 검증해 `READY` | 승인으로 간주하지 않음 | 생성 금지 | 그대로 유지; 자동 enable 금지 |
| `READY` | 한 session의 목록/hash 고정, 필요한 승인·정책·자료·예약 gate 통과, `attempt_count=0`; 거절된 승인은 이 상태에 진입할 수 없음 | 첫 영속 attempt event로 `RUNNING`; 회복 가능 사유면 `TEMPORARILY_HELD`; 명시 중지면 `USER_STOPPED`; 창 종료면 `MISSED_WINDOW` | 각 proposal 승인은 시도 순간에도 유효해야 함; 진입 뒤 만료·무효화되면 같은 run 자동 재개 금지 | proposal별 활성 예약 유지; 시도 0건이 입증된 terminal/cancel만 해당 예약 해제 | 목록·count 재구성; 자동 실행 금지 |
| `RUNNING` | 첫 영속 attempt event가 원자적으로 기록되어 `attempt_count>0` | 회복 가능 사유면 `TEMPORARILY_HELD`; 명시 중지면 `USER_STOPPED`; 모두 확정이면 `COMPLETED(FULL/PARTIAL)`; 미확인 하나라도 있으면 `RECONCILIATION_REQUIRED` | 시도 당시 approval snapshot 불변; 새 시도는 현재 유효성 재검사 | 확정 결과만 consume/release; 미확인 예약 유지; run 단위 일괄 해제 금지 | commit 여부 불명확하면 성공·실패 추정 없이 `RECONCILIATION_REQUIRED` |
| `TEMPORARILY_HELD` | 가격조건 미충족, 일시 자료 부족, 일반 거래정지처럼 재평가 가능한 사유 | 공식 재개/자료 회복 뒤 모든 gate 재검증으로 count 0이면 `READY`, count>0이면 `RUNNING`; 명시 중지면 `USER_STOPPED`; 창 종료 시 count 0은 `MISSED_WINDOW`, count>0 확정은 `COMPLETED(PARTIAL/FULL)`, 미확인은 `RECONCILIATION_REQUIRED` | 만료·거절된 승인은 자동 복구하지 않음 | proposal별 예약 유지하되 승인/예약 자체 만료도 실행 시도 여부와 원자적으로 대조 | 원인·재평가시각 보존; process 재시작만으로 해제 금지 |
| `USER_STOPPED` | 사용자의 내구성 있는 중지 결정 | 같은 run의 자동 출구 없음; 명시 재활성화와 전체 재검증 뒤 count 0이면 `READY`, count>0이면 `RUNNING`; 창 종료 시 count에 따라 terminal/대조 상태 | 중지 전 승인을 자동 연장·재사용하지 않음 | 미확인 시도 예약은 유지; 시도 0건인 취소/창 종료만 proposal별 해제 | restart 뒤 계속 중지; 자동 재개 금지 |
| `MISSED_WINDOW` | 실행창 종료 및 run `attempt_count=0` | terminal; 실행하려면 새 run과 전체 gate 재검증 | 기존 승인으로 자동 재실행 금지 | 모든 포함 proposal의 시도 0건을 증명한 뒤 각 예약만 원자적으로 해제 | 소급 fill·자동 후순위·READY 복귀 금지 |
| `COMPLETED` | `attempt_count>0`, 모든 시도 결과 확정; `FULL` 또는 `PARTIAL`과 terminal reason 저장 | terminal | 승인 snapshot은 감사용으로만 보존 | proposal별 확정 결과에 따라 consume/release; 미시도 proposal은 `MISSED_WINDOW` 뒤 해당 예약만 해제 | 재시작으로 상태 변경 금지; 새 실행은 새 run |
| `RECONCILIATION_REQUIRED` | 영속 시도 결과·원장 commit 중 하나라도 미확인 | 명시적 원장·event·reservation 대조 뒤 `RECONCILED`만 허용 | 결과 미확인을 승인 실패나 성공으로 바꾸지 않음 | 불확실한 cash·SELL 수량·일한도 예약 전부 유지 | 자동 성공·실패·release·재시도·READY 복귀 금지 |
| `RECONCILED` | 대조 결론·근거·proposal별 결과와 예약 정리를 원자적으로 저장 | terminal | 과거 승인 불변 보존; 새 run에 자동 승계 금지 | 확인된 결과에 따라 proposal별 consume/release; 미확인 잔여 0 | 재시작으로 재개하지 않음 |

0. 실제 경제적 목적 변경은 candidate set·reservation보다 먼저 `NEW_ROOT_PURPOSE` target을
   재검증하고, `now < expires_at`인 미취소·미소비 승인 소비와 새 `TradePurpose`·audit 생성을
   하나의 transaction에서 처리한다. 소비 뒤 만료는 생성된 root를 소급 삭제하지 않고 불변
   provenance로 남기며, 그 root의 proposal은 아래 실행 승인·한도를 새로 통과해야 한다.
1. 후보군 승인 대기 중에는 현금·일한도를 예약하지 않는다. 후보군 승인과 대상 세션·자료
   freshness·정책을 다시 확인한 뒤에만 `RESERVATION_PENDING`으로 간다.
2. cash 또는 SELL lot 수량과 일한도 reservation은 `BEGIN IMMEDIATE`에 준하는 한 transaction에서
   생성한다. 같은 승인 집계 키의 누적 판정액이 30만원 미만이면 `READY`, 이상이면 모든
   reservation을 유지한 채 group hash 대상 별도 개별 승인으로 간다. 이 시점은 현금·수량을
   잠글 수 있으므로 승인 UI에 집계 판정액·예약액/수량·24시간 만료를 표시한다.
3. 거절·사용자 취소·승인 만료·자료/정책상 확정 불가능이고 실행 시도가 없었다면 같은
   transaction에서 reservation을 해제한다. `PAPER_EXECUTING`, 결과 미확인, 원장 commit
   불확실은 자동 해제하지 않고 `NEEDS_RECONCILIATION`으로 둔다.
4. 다음 순위 검토는 앞 순위가 **영구 정책 부적격**이거나, 사용자가 승인한 영구 원인 목록의
   reason code와 공식 근거가 일치해 **확정적으로 거래 불가능**하다고 검증된 경우에만 허용한다.
   근거 snapshot에는 stable instrument, 공식 거래소·규제기관 등 source record ID, reason code,
   effective/published/cutoff UTC µs, 영구 원인 목록 version을 보존한다. 일반 거래정지와 재개 여부가
   확인되지 않은 정지는 해제될 수 있으므로 기본 `TEMPORARILY_HELD`이며, 거래정지 feed 존재만으로
   `DEFINITIVELY_UNEXECUTABLE`을 만들지 않는다. 공식 재개·정정·새 상태를 받으면 같은 rank에서
   freshness·정책·승인·예약을 다시 검증한다. 실행창까지 확정 근거가 없으면 `MISSED_WINDOW`이고
   후순위 검토·실행은 0건이다. 세션·실행창 종료, 가격조건 미충족·이탈, 일시 자료 부족,
   승인 대기·거절, 결과 미확인, 사용자 중지, 자금 부족도 후순위 사유가 아니다. 가격조건
   미충족·이탈은 영구 원인 목록에 넣지 않으며 후순위 gate로 바꾸지 않는다.
5. 1~4가 모두 확정 불가능하면 이전 set을 종료하고 새 version을 만든다. 변경점·이전 불가
   이유·예산·위험 영향을 저장하고 금액과 무관하게 후보군 재승인을 받는다. 전략 rule/비중
   변경이 수반되면 일반 재생성이 아니라 2주 제한을 적용한 전략 변경이다.
6. `idempotency_key = portfolio + session + purpose version + proposal version`의 DB unique
   constraint로 동시 실행·버튼 연타·재시작을 막는다. 일한도 row update, reservation, fill,
   ledger entries, 성공 event는 하나의 transaction이다.
7. `TEMPORARILY_HELD`는 자료 지연, 일반 거래정지, 실행창 안의 가격조건 미충족처럼 만료 전
   회복 가능한 상태이고 proposal별 reservation을 유지한다. `USER_STOPPED`도 process restart로
   풀지 않으며 사용자 명시 재활성화와 모든 gate 재검증 뒤에만 attempt count에 따라 `READY` 또는
   `RUNNING`으로 돌아간다. 실행창이 끝나면 **미시도 proposal만** `MISSED_WINDOW`이고, 그 proposal의
   시도 0건을 영속 event로 증명한 경우에만 해당 reservation을 해제한다. run의 `attempt_count=0`이면
   run도 `MISSED_WINDOW`; 1회 이상이고 결과가 모두 확정됐으면 `COMPLETED(FULL/PARTIAL)`; 결과가
   하나라도 미확인이면 `RECONCILIATION_REQUIRED`이며 불확실한 cash·SELL 수량·일한도 예약을
   유지한다. 승인 만료는 run이 연장하지 않으며 run terminal만을 이유로 예약을 일괄 해제하지
   않는다.
8. 1순위의 `LARGE_TRADE` 승인이 거절되면 그 proposal은
   `CANCELLED(reason=LARGE_TRADE_REJECTED)`, set은 즉시 `REGENERATION_REQUIRED`가 된다. 거절은
   2~4순위 자동 실행 사유가 아니다. 사용자가 아무 행동도 하지 않으면 기존 set은 만료까지
   실행 불가로 대기한 뒤 `EXPIRED`; 사용자가 재생성을 요청하면 먼저 새 DRAFT를 만들고 이전
   set을 `SUPERSEDED`로 닫으며 새 후보군 승인을 받아야 한다.
9. 동일 instrument의 새 주문 의도는 proposal이 처음 `RESERVATION_PENDING`으로 전이될 때
   `(Asia/Seoul intent date, US session_date)`를 함께 claim한다. 기존 claim과 KST 날짜 또는
   US session date 중 **하나라도 같으면** side·strategy·purpose와 무관하게 차단한다. 부분체결은
   같은 proposal이고 새 의도가 아니지만 취소 뒤 재주문·정정·retry는 새 proposal로 보아 정책
   확정 전 차단한다.

일한도는 `limit = floor(session_start_validated_NAV_KRW × 0.20)`이다. `used`는 해당 미국
거래 세션의 PAPER 매수·매도 체결 gross KRW 합계이고, 승인 여부와 무관하게 모두 포함한다.
`reserved`는 아직 체결되지 않은 활성 proposal의 최대 gross+비용이다. 분할 체결은 같은
proposal 사용액을 누적하며 `used + reserved`가 limit를 넘으면 원자적으로 거부한다. 거래일은
검증한 미국 거래소 calendar의 `US session_date`, 사건 시각은 UTC µs, 화면은 Asia/Seoul로
분리한다. calendar가 없거나 조기종료 여부를 확인하지 못하면 새 실행을 차단하며 KST 자정으로
reset하지 않는다.

필수 경계 test에는 다음을 포함한다.

- SELL remaining 10주에 동시 6+5주는 정확히 하나만 예약되고, 6+4주는 둘 다 성공해
  `available_qty=0`이어야 한다. 부분체결 뒤 lot 차감과 미체결 예약 합계가 보존되고 UNKNOWN이면
  두 번째 SELL이 계속 차단되어야 한다.
- 개별승인 판정은 299,999원은 미요구, 300,000원·300,001원은 요구한다. 같은 집계 키의
  149,999+150,001원과 이미 기록된 200,000+신규 100,000원은 정확히 300,000원으로 승인을
  요구하고, 150,000+149,999원은 299,999원으로 판정한다. group hash 변경은 기존 승인을
  무효화한다. 같은 root purpose의 v1 200,000원+v2 100,000원도 300,000원으로 합산한다.
- 같은 경제적 목적을 v1에서 v2로 재생성·재승인하면 `parent_trade_purpose_version_id` 연결과
  `stable_root_trade_purpose_id`가 유지되어야 한다. `new_root_user_approval_ref`가 없는 새 root
  발급은 거부하고, 승인된 새 root도 기존 root와 같은 stable instrument·side·US session이면
  `root_overlap_split_check_result`에서 분할 우회를 탐지해 누적액을 초기화하지 않아야 한다.
- `CANDIDATE_SET` 또는 `LARGE_TRADE` 승인 ref를 `NEW_ROOT_PURPOSE` 대신 사용하면 거부한다.
  `NEW_ROOT_PURPOSE`는 만료 `-1µs`까지 다른 gate가 유효할 때만 소비 후보이고 정확한 만료시각에는
  거부하며, target version/hash나 목적 변경 evidence hash가 달라져도 거부한다.
- 같은 `NEW_ROOT_PURPOSE` 승인을 두 transaction이 동시에 소비하면 정확히 하나만 새
  `TradePurpose`·audit를 원자적으로 만들고 다른 하나는 기소비로 실패해야 한다. 취소된 승인도
  소비할 수 없고 실패한 소비가 승인이나 root를 부분 기록해서는 안 된다.
- 후보 재생성, strategy version 변경, 금액 변경만 한 경우 기존 root와 누적 판정액을 유지한다.
  같은 목적에 대한 새 root 승인 요청은 생성 단계에서 차단하고, 이미 승인 ref가 있어도 의미
  대조가 같은 목적이면 발급을 거부한다.
- 실제 경제적 목적 변경 evidence와 승인 target이 일치해 새 root를 적법하게 한 번 발급한 뒤에도
  candidate-set·`LARGE_TRADE` 승인, 30만원 집계, 일일 한도, reservation과 모든 정책·위험 gate를
  재검사하며 하나라도 실패하면 proposal 실행을 차단한다.
- 같은 미국 session에서 KST 자정 `-1µs/+1µs` 의도는 session date가 같아 차단하고,
  서로 다른 미국 session이라도 KST 날짜가 같으면 차단한다. 두 날짜 키가 모두 다를 때만
  다른 cash/cap/정책 gate로 진행한다. 별칭이 다른 같은 stable instrument와 같은 UTC를 다른
  offset으로 쓴 경우도 결과가 같아야 하며 부분체결은 새 의도로 세지 않는다.
- 1순위 개별승인 거절 뒤 2~4순위가 실행되지 않고 set이 `REGENERATION_REQUIRED`인지, 만료
  대기와 명시 재생성 두 경로 모두 old set을 수정하지 않는지 검사한다. held/stop/missed와
  restart·reservation 해제 여부도 상태표대로 검증한다.
- 단일 proposal fixture에서 1순위가 가격조건 미충족인 채 세션·실행창이 끝나고 run
  `attempt_count=0`이면 proposal과 run 모두 `MISSED_WINDOW` terminal이고 2~4순위는 검토·실행
  0건이어야 한다. 일시 거래정지→공식 재개는 같은 rank를 재검증하고, 일시 거래정지→창 종료와
  영구성 미확인 정지는 `MISSED_WINDOW`·후순위 0건이어야 한다. 영구 정책 부적격 또는 승인 목록과
  공식 근거가 일치한 확정 거래 불가만 `DEFINITIVELY_UNEXECUTABLE`을 만들 수 있으며 미등록 reason은
  fail-closed여야 한다.
- `RUNNING`의 정상 ledger/event commit과 reservation 정리는 `COMPLETED` terminal이어야 한다.
  결과 미확인은 `RECONCILIATION_REQUIRED`에서 직접 `READY`로 갈 수 없고, 대조 완료 후 기존
  run은 `RECONCILED` terminal이어야 한다. `MISSED_WINDOW` 또는 `RECONCILED` 뒤 실행은 모든
  gate를 재검증한 **새 run ID**에서만 가능하고 이전 run·승인·예약을 재사용하지 않아야 한다.
- 실행창 `-1µs`에는 terminal 전이가 없고 정확한 종료시각에 attempt 0건인 run만
  `MISSED_WINDOW`여야 한다. 두 proposal 중 하나가 영속 시도·확정되고 다른 하나가 미시도인 채
  창이 끝나면 전자는 확정 상태, 후자는 proposal-level `MISSED_WINDOW`, run은
  `COMPLETED(PARTIAL)`·`attempt_count=1`이어야 하며 run `MISSED_WINDOW`가 아니어야 한다. 모두
  시도·확정이면 `COMPLETED(FULL)`이다.
- 영속 시도가 하나라도 미확인이면 창 종료 여부와 무관하게 `RECONCILIATION_REQUIRED`이며 관련
  예약을 유지하고, 명시 대조 뒤 기존 run만 `RECONCILED`한다. 재시작 전후 included proposal
  목록/hash, UTC µs, count가 같고 같은 idempotency event retry가 count를 늘리지 않아야 한다.
  한 run에 두 US session을 넣거나 `READY` 뒤 목록을 바꾸는 요청, run terminal을 근거로 다른
  proposal의 예약을 일괄 해제하는 요청은 모두 거부한다.

### PC 실행·자료 지연 시나리오

| 상황 | 채택 동작 | 금지 동작 |
|---|---|---|
| 개장 전 신호·승인, 개장 때 backend 실행 | 승인·session·freshness·reservation을 재검증하고, 개장 뒤 실제로 처음 관측한 적격 quote에 지정된 가상체결 model 적용 | 승인 전 price나 예상 시가를 fill로 기록 |
| 개장 때 PC 종료·절전 | 실행 시도 없이 실행창이 끝난 proposal/run을 terminal `MISSED_WINDOW`로 남기고 다음 실행 시 알림; 승인 목록과 공식 근거로 확정된 거래 불가만 별도 `DEFINITIVELY_UNEXECUTABLE`; 자동 소급·후순위 검토 없음 | PC가 켜진 뒤 과거 open price로 가상체결하거나 세션 종료·일반 거래정지를 후순위 사유로 사용 |
| 일부 proposal 시도 뒤 실행창 종료 | 미시도 proposal만 `MISSED_WINDOW`; 시도 결과가 모두 확정되면 run은 `COMPLETED(PARTIAL)`, 하나라도 미확인이면 `RECONCILIATION_REQUIRED`; 예약은 proposal별 처리 | 시도가 있었던 run 전체를 `MISSED_WINDOW`로 바꾸거나 run 종료를 이유로 예약 일괄 해제 |
| backend 실행 중 quote 지연 | `TEMPORARILY_HELD`; 자료 기준·수집시각·지연을 표시하고 reservation 만료 전 재검증 | 늦게 도착한 값이 당시 이용 가능했다고 가정 |
| 재시작 후 실행 결과 미확인 | idempotency event·ledger·reservation을 먼저 대조하고 `NEEDS_RECONCILIATION`; 관련 신규 실행 차단 | 만료로 간주해 예약 해제하거나 동일 proposal 재실행 |
| 사용자 중지 | 내구성 있는 `USER_STOPPED` 유지; restart 뒤 자동 해제하지 않음 | process 재시작을 승인·재활성화로 해석 |
| 16시 보고 미완료 | 부분 결과와 누락을 새 report version으로 기록. 대상 날짜·실제 생성시각·cutoff·보완 여부 표시 | 나중 자료로 당시 거래를 만들거나 과거 보고서를 덮어쓰기 |

보고서 보완과 거래 체결은 별개다. 당시 공개 자료 snapshot을 재현할 수 없으면 `보완 보고서`로
표시하며 당시 생성 보고서·승인 근거를 수정하지 않는다.

### 제안 API 계약 — 모두 미구현

모든 mutation은 기존 session+CSRF, 단일 사용자 fail-closed, safe error, Decimal 문자열,
append-only version 계약을 재사용한다. 아래 path가 현재 라우터에 없다는 점 자체가 이번 설계
감사의 기대 상태다.

| method/path(제안) | 기능 | 핵심 응답·안전조건 |
|---|---|---|
| `GET /api/v1/paper/status` | PAPER engine·사용자 중지·자료/정책 준비 상태 | `enabled=false` 기본, `stage_c_relationship=PREPARATORY_SUPPLEMENT_ONLY`, `stage_c_credit=NOT_GRANTED`, 위험분모·월예산 prerequisites, 주문 adapter 없음; session-scoped `execution_run`은 run ID, US session, 불변 included proposal ID/version 목록/hash, 실행창·started/ended UTC µs, exact state, attempted boolean, distinct attempt count/IDs, `FULL/PARTIAL`, terminal reason과 proposal별 상태 count를 반환; 상태 조회가 거래를 시작하지 않음 |
| `POST /api/v1/paper/portfolios` | 4,000,000 KRW 내부 원장 genesis | 별도 명시 승인 이후 한 번만; SAMPLE/actual import 금지 |
| `GET /api/v1/paper/ledger` | cursor 기반 immutable entry 조회 | 전략·통화·entry type filter; 전체행 메모리 load 금지 |
| `GET/POST /api/v1/paper/strategy-versions` | version 조회/새 승인 요청 | score mix와 budget version 분리, 제안식의 2주 earliest effective 및 기준점 상태 표시; UPDATE/DELETE 없음 |
| `GET/POST /api/v1/paper/candidate-sets` | 1~4순위·근거 snapshot 조회/생성 | 생성만으로 승인·예약·실행 안 됨; 재생성은 parent와 차이 필수 |
| `POST /api/v1/paper/approvals` | 후보군·큰 거래·새 root 목적 결정 | 세 type과 불변 target version/hash를 구분; `NEW_ROOT_PURPOSE`는 prior root, 서버 발급 proposed root, 목적 변경 evidence/hash, 중복/분할 검사 `PASS`와 그 input hash·policy version·checked-at/cutoff를 target으로 하고 24h·1회 원자 소비·취소/만료/변경 무효·TradePurpose/audit 연결을 적용; 검사 `FAIL/UNKNOWN`과 다른 type 재사용, 승인값 echo 금지 |
| `GET /api/v1/paper/proposals` | 상태·예약·근거·한도 영향 조회 | source record IDs/cutoff와 찬반 근거, `UNAVAILABLE` 보존 |
| `POST /api/v1/paper/proposals/{id}/cancel` | 실행 전 취소 요청 | UNKNOWN/실행 중이면 cash·일한도·SELL lot 수량을 즉시 해제하지 않고 대조 case 생성 |
| `GET /api/v1/paper/daily-limits/{session}` | opening NAV·20%·used/reserved/available | US session date와 UTC/KST 표시; KST 날짜 reset 금지 |
| `GET /api/v1/paper/observations` | 일별 오답노트 | 예측 snapshot과 이후 관측 분리; 자동 전략 변경 endpoint 없음 |
| `GET /api/v1/paper/notifications` | PAPER 내부 알림 상태 | read/ack/resolved/approval/re-enable 분리; OS/WebPush 없음 |

명시적으로 **제안하지 않는 endpoint**는 broker order 전송, 계좌 조회, 정책 override, 승인 없는
execute, DELETE ledger/history, 실제 자산과 PAPER merge이다.

### 8개 전략 공통 계산 계약

전략 명칭은 최신 사용자 지시의 `장기 지수 / 가치 / 우량 성장 / 성장주 / 고배당 / 배당성장 /
모멘텀 / 자산배분`을 사용한다. 현행 `StrategyKind` 8종은 이름과 의미가 다르므로 자동 rename
또는 과거 version 변환을 하지 않는다. 특히 현행 `LOW_VOLATILITY`, `SATELLITE_THEME`,
`BOND_CASH`를 새 전략에 억지로 대응하지 않고 migration mapping을 별도 승인받는다.

아래 숫자는 모두 **분류 3 기술 제안값**이며 공식 투자정책이 아니다. 구현할 때는 rule version에
값과 근거를 저장하고 사용자 승인 전 `DRAFT/EXECUTION_BLOCKED`로 둔다.

1. Universe는 미국 정규 거래소에 상장되고 stable identifier, 상품 유형, 통화, 거래상태,
   정책 적격성을 검증한 보통주 또는 비레버리지 ETF로 한정한다. ADR, preferred, OTC, ETN,
   파생·레버리지·인버스, 신용·공매도는 분류가 확실하지 않으면 제외한다.
2. 가격 feature는 cutoff 이전에 공급자가 공개한 point-in-time 자료만 쓴다. 일봉은 최소 252개,
   전략별 요구가 더 길면 그 수를 충족해야 한다. split/dividend와 원시/조정 가격을 분리하며
   총수익 자료가 없으면 총수익 feature가 필요한 전략은 `UNAVAILABLE`이다.
3. 재무 feature는 filing의 실제 `published_at` 이후에만 사용하고 연결/별도, 분기/누적,
   통화·단위를 보존한다. 최신 분기 filing이 150일을 넘거나 연차만 있는 경우 450일을 넘으면
   stale로 차단한다. restatement는 새 revision부터 적용하고 과거 평가를 덮어쓰지 않는다.
4. cross-section metric은 같은 산업 내에서 5·95 percentile winsorize 후
   `z=(x-median)/(1.4826×MAD)`로 표준화한다. MAD=0 또는 표본 10개 미만이면 해당 metric은
   `UNAVAILABLE`이다. rule version에 `required`로 선언된 metric이 **하나라도** `UNAVAILABLE`이면
   해당 instrument의 종합 score도 숫자 0이 아니라 `UNAVAILABLE`이고 후보·순위에서 제외한다.
   남은 항목으로 weight를 재배분하거나 0점·중앙값·과거값으로 대체하지 않는다. 후보는 4개보다
   적을 수 있고 그 빈자리에 다른 전략 예산을 옮기지 않는다. 모든 필수 metric이 있을 때만 z를
   -3~3으로 clamp한 뒤 `50 + (z/3)×50`으로 0~100에 선형 변환한다.
5. 여러 전략에 같은 instrument가 들어가도 lot·예산 귀속은 분리하고 위험검사에서는 실제
   instrument·기업·ETF underlying 노출을 합산한다. score weight는 전략별 예산이 아니다.
6. 동점은 `(자료 완전성 높은 순, 60거래일 중앙 거래대금 큰 순, 63일 변동성 낮은 순,
   stable instrument ID 오름차순)`으로 결정한다. 최대 후보는 목적별 4개다.
7. 평가비용은 수수료+세금+FX spread+한 방향 slippage를 모두 Decimal로 계산한다. 설계 검증용
   sensitivity는 한 방향 slippage `5/15/30 bps`, 기준 제안은 `15 bps`이나, 실제 실행 기본값은
   승인 근거가 없으므로 `null`이고 PAPER 체결은 차단한다. 기대수익률을 만들어 비용과 비교하지
   않고, 기존 보유 후보와 교체 후보 score 차가 10점 미만이면 회전 억제를 위해 `HOLD`한다.
8. 공통 신규매수 gate는 score·freshness·정책·자료권리·거래일정·cash/cap reservation·승인을
   모두 통과해야 한다. 매도 signal도 승인·원장 대조를 거치며 다른 종목 매도로 대체하지 않는다.
9. 우주 구성과 모든 feature snapshot을 cutoff와 함께 저장한다. survivorship-free 당시 universe,
   delisted symbol, filing revision을 포함하지 못하면 backtest 성능을 제시하지 않는다.
10. 위성으로 분류되는 전략·instrument는 PAPER 손실 분모, 측정기간, 재개조건이 미확정이므로
    신규 자동매수를 `POLICY_INPUT_REQUIRED`로 차단한다. 실제 연간 투자계획이나 실제 잔고를
    4,000,000원 PAPER 자금에 대입하지 않는다.

### 전략별 정량 규칙 초안

`Pctl(metric)`은 위 공통 산업 내 score, `InvPctl`은 낮을수록 좋은 metric의 역점수다.
ETF처럼 산업 비교가 부적절하면 사전 승인된 동종 상품군 안에서만 비교한다.

| 전략 | 목적·필요 입력 | 점수식·적격 filter(제안) | 매수·매도 signal / 주기 | 누락·위험·독립 검증 |
|---|---|---|---|---|
| 장기 지수 | 쉬운 설명: 넓은 시장을 낮은 비용으로 추종. 승인된 broad index ETF의 252일 원시·총수익, 보수, 순자산, 60일 거래대금, 구성 범위, benchmark return 필요 | `30×Pctl(log(60d median dollar volume)) + 25×InvPctl(expense ratio) + 25×InvPctl(252d tracking error) + 20×Pctl(holdings breadth)`를 100으로 나눔. 최소 100종목, 60일 거래대금 10,000,000 USD, 보수≤0.50%, 레버리지/인버스 아님 | 분기 평가·월 1회 거래. budget 부족이 `max(전략예산 5%, 1주+비용)` 이상이고 score≥60이면 후보. score<40이 2회 연속, 추종대상 변경, 정책 부적격이면 매도 검토 | 현재 ETF 전체 구성·권리·검증된 total-return benchmark가 없어 실행 불가. tracking error를 price return으로 대체 금지. issuer/SEC snapshot과 지수 정의를 독립 대조 |
| 가치 | 현금흐름 대비 저평가된 보통주. point-in-time price, shares, net income, equity, FCF, EV, EBITDA, sector 필요 | `0.30×Pctl(E/P)+0.30×Pctl(FCF/EV)+0.20×Pctl(B/P)+0.20×Pctl(EBITDA/EV)`. E·FCF·equity·EBITDA>0, EV>0, 60일 거래대금≥5,000,000 USD, 최신 filing freshness 충족 | 분기 평가, 월 거래. score≥70이며 상위 20%를 매수 후보. score<35가 2분기 연속, 필수 분모≤0, filing stale이면 매도/보류 검토 | 금융업처럼 EV/EBITDA가 부적절한 업종은 별도 검증식 승인 전 제외. restatement cutoff ±1µs, sector 표본·단위·분모 0 test |
| 우량 성장 | 수익성과 성장의 질을 함께 평가. 3개 연차+8분기 매출, invested capital, FCF, gross margin, debt, EBITDA, share count 필요 | `0.25×Pctl(ROIC)+0.20×Pctl(FCF margin)+0.20×Pctl(3y revenue CAGR)+0.15×InvPctl(8q gross-margin stddev)+0.10×InvPctl(net debt/EBITDA)+0.10×InvPctl(3y share dilution)`. TTM FCF>0, equity>0, 3y history 완전 | 분기 평가·월 거래. score≥70, 모든 quality gate 통과 시 매수. score<40 2분기 또는 FCF 음수 2분기, 중대한 restatement면 매도 검토 | ROIC 정의(평균 capital, 세율)를 version 고정. M&A·회계통화 변화는 자동 이어붙이지 않음. raw filing 재계산과 산업별 10개 표본 test |
| 성장주 | 이미 공개된 실적의 성장 지속성을 평가하며 전망치를 생성하지 않음. 3년·8분기 revenue, TTM revenue/FCF, operating margin, shares 필요 | `0.30×Pctl(3y revenue CAGR)+0.25×Pctl(TTM revenue YoY)+0.15×Pctl(operating-margin YoY change)+0.15×Pctl(TTM FCF YoY)+0.15×InvPctl(1y dilution)`. 매출 양수, 최소 3년, TTM 영업현금흐름 또는 FCF>0, 1y dilution≤10% | 분기 평가·월 거래. score≥75와 상위 15%면 매수. TTM 매출성장≤0 2분기, dilution>10%, score<40 2분기면 매도 검토 | 애널리스트 예상·목표가·확률 사용 금지. 적자기업의 P/E를 0으로 만들지 않음. 발표시각 이후 사용과 중단/매각 사업부 test |
| 고배당 | 실제 지급된 현금배당의 수준과 지속성을 평가. 5년 payment/cut 이력, trailing 12m dividend, price, FCF, payout, debt 필요 | `0.30×Pctl(TTM ordinary dividend yield)+0.25×InvPctl(FCF payout ratio distance from 50%)+0.15×Pctl(FCF/dividend coverage)+0.15×InvPctl(net debt/EBITDA)+0.15×InvPctl(5y cut count)`. yield>0, FCF payout 0~80%, coverage≥1.25, 특별배당 제외 확인 | 분기 평가, 지급 event 일별 반영, 월 거래. score≥70이면 매수 후보. 배당 삭감/중단, payout>100% 2분기, score<40이면 매도 검토 | 특별배당·return of capital 분류 없으면 실행 불가. ex-date가 아니라 실제 지급과 발표시각 구분. 5년 corporate action 대조 |
| 배당성장 | 배당을 꾸준히 늘리고 감당할 수 있는 기업. 5년 split-adjusted ordinary dividend, FCF, payout, ROIC, debt 필요 | `0.25×Pctl(5y dividend CAGR)+0.25×Pctl(consecutive years without cut, cap 10)+0.20×InvPctl(FCF payout)+0.15×Pctl(ROIC)+0.15×InvPctl(net debt/EBITDA)`. 5개 완전 회계연도, 5년 삭감 0, payout 0~75%, FCF>0 | 연차/분기 평가·월 거래. score≥70 매수. 삭감 즉시, payout>100%, score<40 2분기면 매도 검토 | split·통화·특별배당 조정이 검증되지 않으면 UNAVAILABLE. 원 dividend event와 조정 series 양방향 대조 |
| 모멘텀 | 최근 1개월을 뺀 중기 총수익 추세. 252일 split+dividend total-return series, 63일 변동성, 200일 평균 필요 | `0.40×Pctl(R12-1)+0.25×Pctl(R6-1)+0.15×Pctl(R3-1)+0.10×InvPctl(63d annualized volatility)+0.10×Pctl(price/MA200-1)`. 252 거래일 완전, price>MA200, 60일 거래대금≥5,000,000 USD | 매월 마지막 검증 거래일 평가, 다음 세션부터 거래. score≥80·상위 10% 매수. score<50 또는 종가<MA200 2회 연속이면 매도 검토 | 현재 검증된 total-return·기업행동 series가 없어 실행 불가. 당일 종가로 그 종가에 체결 금지. delisted universe 포함 walk-forward 검증 |
| 자산배분 | 승인된 주식·채권·현금성 위험축 안에서 broad ETF 후보의 상대 상태를 비교. 자산군별 252일 total return, 변동성, drawdown, 정책 target 필요 | 동종 자산군 안에서 `0.30×Pctl(R12-1)+0.20×Pctl(R6-1)+0.20×Pctl(price/MA200-1)+0.15×InvPctl(63d vol)+0.15×InvPctl(252d max drawdown)`. 기존 core/satellite·주식/채권/현금 target은 입력일 뿐 점수가 변경하지 못함 | 월 평가·분기 review. 승인 자산배분 안에서 부족분만 매수 후보. 별도 위험 target이 매도를 요구하거나 상품 부적격일 때만 매도 검토; 다른 전략 예산 자동 이전 금지 | `PROJECT_RULES.md` 목표·보호저축을 전략 score로 바꾸지 않음. target 미확정/ETF total-return 부족이면 실행 불가. 전략 변경 전후 risk allocation 동일성 test |

전략 입력과 공식 출처 후보는 다음처럼 고정 연결한다. 공급자 계약의 상세·URL은
`docs/DATA_SOURCES.md`의 2026-10-14 표가 원본이다.

| 입력 | 사용 전략 | 출처 후보·freshness 제안 | 현재 판정 |
|---|---|---|---|
| 미국 원시/조정 OHLCV·거래대금 | 8개 전부 | 권리 확인된 KIS 해외 일봉; 일봉은 최근 완료 정규장 뒤 36시간 이내, 가상체결 quote는 관측 뒤 5분 이내 | 해외 adapter·저장권 미확인, 미연결. 수정 flag를 배당 포함 total return으로 간주 금지 |
| point-in-time 재무·filing | 가치·우량 성장·성장주·고배당·배당성장 | SEC submissions/companyfacts/XBRL; 최신 분기 150일, 연차 450일, accepted/published cutoff | 수집 골격 일부, 전략 factor 미연결·실연결 NOT RUN |
| 배당·split·기업행동·total return | 장기 지수·고배당·배당성장·모멘텀·자산배분 | 계약이 확인된 exchange/issuer/KIS rights 및 raw filing; event는 효력 전 마지막 확인과 revision 보존 | 무료 완전 source 미확보, 관련 전략 실행 차단 |
| ETF 구성·보수·benchmark | 장기 지수·자산배분 및 ETF 중복노출 | SEC N-PORT(분기 지연), 권리 확인된 issuer file, 공식 benchmark 정의; 구성은 기준일과 수집일 모두 저장 | N-PORT 적재 미구현, 최신 일일 구성 미지원 |
| 거래일·조기종료·정지 | 모든 거래 signal | version된 NYSE/Nasdaq 공식 calendar와 약관 준수 halt feed; session 전에 확인 | machine calendar/비상변경 adapter 미구현, 실행 차단 |
| USD/KRW | USD 거래·모든 KRW cap/승인 판정 | ECOS 또는 ECB 교차 **참고 valuation**; 36시간 이내, actual conversion rate와 구분 | 운영 key/교차환율 adapter 미구현. 체결환율로 사용 금지 |
| 거시·공식 뉴스 metadata | 8개 위험 설명 | BLS/BEA/Fed/SEC 공식 발표; provider cadence와 published cutoff | 일부 metadata 구현. 일반 기업뉴스·본문·감성은 미지원이며 score를 임의 보완하지 않음 |

모든 score는 소수 둘째 자리까지 표시하되 정렬은 반올림 전 Decimal로 한다. threshold 경계
`-0.01/정확값/+0.01`, 자료 cutoff `-1µs/정확/+1µs`, 다른 offset의 동일 UTC, correction,
동점, 표본 9/10, MAD 0, 음수·0 denominator를 독립 fixture로 검증한다. 과거 성과 검증은 당시
universe와 당시 공개된 filing만 사용하고 최소 `훈련 / 시간순 walk-forward / 고정 외부검증`
구간을 분리한다. 전략을 한 번 통과시킨 결과는 자동 운영 승인이나 미래수익 보장이 아니다.

### P0 웹 화면·데이터 연결 초안

현행 sidebar/header/card/table/chart, light/dark, 1200/900/600px breakpoint, 100~200% zoom,
keyboard/focus/token을 재사용한다. UI 코드는 이번 단계에서 바꾸지 않았다. 모든 화면 상단에는
`내부 PAPER · 실제 자산/주문 아님`과 `SAMPLE이 아님`을 동시에 식별할 수 있는 scope badge를
둔다. 실제 숫자가 없는 상태에서 mock 종목·수익률·체결을 표시하지 않는다.

| UI ID / 위치 | 표시 필드 | 동작·오류 | 제안 API 의존 | 검증 기준 |
|---|---|---|---|---|
| UI-PAPER-01 메인 | PAPER NAV, KRW/USD 가용·예약 cash, 자료 cutoff, 전략 budget/actual, data readiness | 결측은 0이 아닌 `계산 불가`; actual/SAMPLE 합산 금지 | `/paper/status`, `/paper/ledger` | scope 전역 badge, 총계/ledger 대조, 1000×680·200% scroll |
| UI-PAPER-02 일일 한도 카드 | US session date, 시작 NAV, 20% limit, used, reserved, available, FX snapshot | KST 자정 reset 문구 금지; calendar/FX 결측 시 실행 차단 | `/paper/daily-limits/{session}` | 0/20% 경계, buy+sell·분할·동시성, used/reserved 이중합산 없음 |
| UI-PAPER-03 실행 상태 | exact run state, run ID, US session, immutable included proposal 목록/hash·수, attempted 여부·distinct attempt count, attempted/resolved/missed proposal 수, 실행창·started/ended UTC µs, `FULL/PARTIAL`, terminal reason, proposal별 상태 | 시작 버튼만으로 정책 승인되지 않음; 중지는 restart 후 유지; run `MISSED_WINDOW`는 시도 0건에만 표시; 일부 시도 뒤 창 종료는 `완료(부분)`이고 성공으로 과장하지 않음; terminal 뒤 재실행은 새 run+전체 재검증 | `/paper/status`와 별도 승인된 run mutation | exact enum, 0회 missed/1회 partial 구분, proposal-level missed 분리, unknown 예약 유지·대조 terminal, restart count/hash 보존, 직접 READY·중복 count 차단, PC off/절전 안내 |
| UI-PAPER-04 후보 1~4 | rank, instrument, score, proposal/set exact state, 이전 순위 영구 원인 code·승인 목록 version·공식 근거, set version | held/stopped/missed/reconciliation을 구분; 영구 정책 부적격 또는 승인 목록과 공식 근거가 일치한 확정 거래 불가 외에는 다음 순위 action 비활성; 일반 거래정지는 `일시 보류`; 세션 종료·가격조건 미충족·매도는 대체 사유가 아님 | `/paper/candidate-sets` | waiting/rejected/unknown·개별승인 거절·일시 거래정지·`MISSED_WINDOW`·가격조건 이탈로 우회 불가, 정지 재개 시 같은 rank 재평가, 세션 종료 후 후순위 실행 0, regeneration/동점 안정성 |
| UI-PAPER-05 승인 | `CANDIDATE_SET`·`LARGE_TRADE`·`NEW_ROOT_PURPOSE` 별도 카드/action/state; 공통 target version/hash·승인 사용자/시각·24h 만료·소비 상태, 새 root 카드는 prior→proposed root·목적 변경 evidence 요약·분할 검사 결과, 큰 거래 카드는 예약 현금·SELL lot 수량 | 세 승인을 합치거나 재사용하지 않음; 취소·만료·target/hash 변경은 무효; 같은 목적이면 새 root action 차단; 새 root 승인도 후보군·큰 거래·일일/위험 gate를 통과시키지 않음 | `/paper/approvals`, `/paper/proposals` | 다른 type 재사용 거부, 만료 ±1µs·hash 변경, 동시 1회 소비, 후보/전략/금액 변경 root 유지, 동일 목적 차단, 적법한 새 목적의 모든 gate 재검사, CSRF/replay |
| UI-PAPER-06 근거 | 사실·계산·해석·미확인, 출처 record/URL, cutoff, 찬성·반대, 비용, 거래 전후 cash/cap/risk, 이전안 차이; 새 root는 intended-effect diff·목적 변경 evidence hash·audit·overlap/split 검사 입력 hash/정책 version/checked_at/result | 근거 부족 또는 overlap/split `FAIL/UNKNOWN`이면 새 root 포함 승인 button 비활성; 외부 문구는 text data | candidate/proposal/purpose-change evidence snapshot | 미래자료·HTML/Markdown injection·민감정보 비노출, snapshot 불변, 검사 `PASS`만 새 root 소비 가능 |
| UI-PAPER-07 재생성 | 이전 1~4 불가 이유, 새 set diff, 예산·risk 영향, 승인 상태 | 항상 새 후보군 승인; 같은 경제적 목적은 root 유지, 실제 목적 변경만 별도 `NEW_ROOT_PURPOSE`; rule/weight 변경이면 2주 절차 | candidate set POST + approval | old set overwrite 없음, 재생성으로 집계 초기화 0, 재생성 횟수 미확정 표시 |
| UI-PAPER-08 전략·예산 | 8전략 rule version, score weight, budget, actual을 서로 다른 열로 표시, effective date | 정확히 100% UI는 예산과 score 각각의 계약에 맞게 검증; 자동 자금 이동 없음 | `/paper/strategy-versions` | 현행 strategy kind 자동 매핑 없음, 2주 경계·history 불변 |
| UI-PAPER-09 내부 알림함 | type별 승인 필요·거절·만료·무효·소비, 자료 지연, missed window, 대조 필요, report 완료 | read/ack/resolved/approval/re-enable를 별도 action으로 취급하고 다른 승인 type 상태를 합치지 않음 | `/paper/notifications` | popup·OS·WebPush 0, commit 전 성공 알림 0, 민감 승인 원문 0 |
| UI-PAPER-10 재실행 안내 | PC off/절전/backend stop, 놓친 session/report, 사용자 stop, reconciliation | 보고서 보완과 거래 소급을 분리; 삭제·자동 dismiss 없음 | jobs/status/reconciliation | restart idempotency, 소급 fill 0, 중복 report/notification 0 |

완료 문구는 `PAPER 거래 기록 완료`를 기본으로 하고 `주문 체결`, `증권사 접수`, `실제 수익`을
쓰지 않는다. 데이터가 충분해도 화면이 직접 주문 권한을 갖지 않고, 모든 계산·정책검사는
server의 일반 코드 결과만 표시한다.

### 기존 7절 보고서와 PAPER 카드 연결

| 순서 | 카드 입력 | PAPER 표시 / 금지 |
|---|---|---|
| 1. 요약 3줄 | report cutoff, PAPER NAV, 가장 큰 policy/data/reconciliation 상태 | 정확히 3개 핵심문장과 `내부 PAPER` 표시. 실제 자산 성과처럼 표현 금지 |
| 2. 자산 현황과 목표·실제 비중 | PaperLedger/Valuation, budget version, actual lot 귀속, risk allocation | 전략 budget/actual과 코어·위성 위험축을 별도 chart/table로 표시. 중복 instrument는 전체 노출 합산 |
| 3. 목표 대비 | PAPER 초기자금·외부 PAPER cash event·투자손익과 기존 목표의 비교 가능 여부 | 실제 장기 목표·실제 자산에 4,000,000원을 더하지 않음. 비교 계약이 없으면 `계산 불가`이며 단순 비중 카드로 대체 금지 |
| 4. 실제 배당과 배당 전환 능력 | 실제 계정 자료가 아니라 PAPER dividend entries, 비용, 세금 capability | 제목은 원문 그대로 유지하고 `PAPER 모의 배당` 배지를 붙인다. 실제 세후 배당/ISA/생활비 자료가 없으면 전환 능력은 미입력; 가상 세금 생성 금지 |
| 5. 위성 손실 현황 | 위성 lot/ledger, 별도 승인된 loss denominator·period·restart rule | 분모·기간·재개가 미확정이므로 상태는 `정책 확인 필요`, 신규 위성 자동매수 잠금. 실제 연간 투자금 대입 금지 |
| 6. 제안 주문안과 이유 | candidate/proposal/approval/reservation/cost/evidence snapshots | `PAPER 거래 제안`; rank·찬반 근거·전후 cash/cap/risk와 승인 만료. 수량은 budget/자료가 모두 있을 때만 |
| 7. 규칙 위반·주의사항 | policy validator, missing/stale/provider status, cap/reconciliation, rights | 확정 위반과 미확인을 분리. 실제 주문 없음, 실공급자 상태, PC off 한계 명시 |

세로 카드에는 핵심 Decimal, 짧은 한국어, 상태·출처·기준시각, keyboard `자세히 보기`를 둔다.
차트 값은 표와 text로도 제공하며 자료가 없으면 빈 mock chart를 만들지 않는다. 원본 Markdown
7절 snapshot은 새 report version으로 append-only 저장한다.

오답노트는 신호 생성 시 `예측 대상·방향·평가창·판정식·자료 cutoff`를 먼저 고정하고 매일
관측을 덧붙인다. `진행 중/충족/불충족/평가 불가`와 PAPER 손익을 다른 열에 둔다. 원인을
확인하지 못하면 `미확인`으로 남기며 오답노트가 전략 version이나 budget을 자동 변경하지 않는다.

### 단계별 구현·독립 검증 계획

각 단계는 이전 단계의 완료 증거와 별도 총괄팀 승인이 있어야 시작한다. C-21 당시에는 어느
단계도 구현하거나 PASS로 판정하지 않았다. 이후 별도 사용자 승인으로 P1 순수 domain만
구현·자체 코드 검증했으며, P2 이후와 운영 활성화는 시작하지 않았다.

| 단계 | 첫 구현 범위 | 완료 조건 | 차단 요인·독립 검증 |
|---|---|---|---|
| P0 계약 동결 | 본 절의 realm, approval provenance, enum, API/schema migration decision | 기술 계약과 fail-closed/null 표현 확정, threat model·migration/rollback·backup format review; 실행에 필요한 사용자 정책값은 별도 추적 | 기술팀 원문 없는 수치, PAPER DB 위치, 전략 mapping 미확정. 미확정값을 `null/POLICY_INPUT_REQUIRED`로 다루는 순수 model 검증은 가능하지만 `READY`·운영 활성화는 금지. Claude가 실제 코드와 gap 표 대조 |
| P1 순수 domain engine | `asset_assistant/paper.py`: Decimal ledger, lots, cash, strategy/budget version, session cap, approval·reservation·run state machine. network/UI 없음 | `tests/test_paper.py`에서 보존식·idempotency·thread 동시성·경계 자체 PASS; 4,000,000 genesis 단 한 번; 실제 주문·network·운영 DB import 없음 | **구현·Codex 코드 검증 완료, Claude 독립 검증 대기**. 메모리 thread 원자성만이며 DB/multiprocess/crash durability는 P2 검증 대상 |
| P2 SQLite·backup | 기존 strict allowlist와 별도 version migration, append-only repository, cursor, online backup/restore | 기존 DB hash/row 보존, migration rollback, DB/WAL/SHM 0600, symlink/UID/W4/X1, backup 최신행/restore exact PASS | DB 위치 결정 필요. 실제 사용자 DB를 개발 fixture로 사용 금지 |
| P3 자료 feature | 권리 확인된 공급자만 price/FX/filing/corporate action feature로 point-in-time 정규화 | fixture와 허용된 별도 실연결을 구분, unit/currency/revision/cutoff/stale/partial failure 검증 | KIS 권리·키, ETF 최신 구성·기업행동·총수익 자료 미확보. 부족 전략은 UNAVAILABLE |
| P4 전략 평가 | 새 8전략 version, universe snapshot, score/rank/evidence/오답노트. 실행 없음 | 각 식 golden fixture, 필수 metric별 결측→score `UNAVAILABLE`·후보 제외, look-ahead/survivorship/correction, 동일 입력 hash 재현, policy limit 불변 | threshold는 제안값이며 사용자 승인 전 execution disabled |
| P5 PAPER proposal/approval 운영 통합 | P1의 candidate/세 승인/reservation/virtual-fill 순수 모델을 P2 영속 repository·owner-scoped API·CSRF에 연결하고, 외부 network 권한 없는 simulated-fill adapter를 통합 | P1 불변식에 더해 multiprocess 원자성·crash recovery·replay/CSRF·restart 대조·PC off 소급 0 PASS | P1 모델을 다시 만드는 단계가 아님. 비용·calendar·quote quality 및 추가 정책 승인 필요; broker endpoint 계속 0 |
| P6 React P0·7카드 | UI-PAPER-01~10, 기존 7절에 PAPER data 연결, 내부 알림 | Vitest/API/Chrome visible+headless, light/dark, 100~200%, keyboard, scope/masking/XSS, mock 수치 0 | 승인 디자인 비교 캡처는 저장소 밖; 실제 계정 자료 금지 |
| P7 scheduler shadow | 30분 refresh, 15시 collect, 16시 report에 **분석-only shadow** 연결 | fake clock+동시 process+restart+missed slot; 실제 정시 발화 별도 NOT RUN/PASS 표시 | OS 등록과 PAPER 실행 활성화는 별도 승인. PC off 보장 표현 금지 |
| P8 제한 내부 PAPER 인수 | 총괄팀이 명시적으로 활성화한 fixture/가상 원장만 | 사용자 확정 정책·실공급자 권리·실제 Secret Service·serve 검증, 독립 V- 결함 0, rollback rehearsal | KIS/실제 주문은 여전히 별도 범위. 과거 C20 PASS 재사용 금지 |

후속 승인 범위인 **P1 순수 PAPER domain engine 구현 및 코드 검증 완료** 상태다. 외부 공급자,
UI, scheduler, broker는 함께 넣지 않았다. 비용·위성·MDD처럼 미확정된 값은 typed nullable
policy input과 차단 사유로 남겼다. 이 완료 표현은 자동 모의투자 운영 가능 또는 실제 거래
검증 완료를 뜻하지 않는다.

### 사용자·총괄팀 결정 필요사항

1. `INTERNAL_PAPER`를 web-state DB 확장으로 둘지 별도 0600 SQLite로 둘지, backup bundle과
   strict schema allowlist를 어떻게 version할지.
2. 새 8전략 명칭과 현행 candidate scoring 8종의 migration mapping, “2주 제한”의 기준점을
   직전 승인·직전 효력·calendar 중 무엇으로 볼지. 초안은 `max(이번 승인, 직전 효력+14일)`이며
   자동 mapping·효력 적용은 금지한다.
3. 구체 수수료, 한 방향 slippage, FX spread·최소비용과 USD 거래의 300,000원 판정 FX.
4. 최대 12종목, 후보 재생성 최대 횟수, 추가매수 간격, 유동성·변동성·산업 cap의 기술팀
   승인 원문 또는 새 사용자 확정. 원문 전까지 실행값은 `null`이다.
5. 전체 MDD 정지·회복 threshold와 대기기간. 현행 -15%는 경보일 뿐 자동정지가 아니다.
6. PAPER 위성 손실 분모, 측정 시작/기간, 손실 회복 판정, 사용자 재활성화 방식. 확정 전
   위성 신규 자동매수는 잠근다.
7. 전략 개선안의 최소 검증기간·승인자·rollback 조건. 2주 변경 제한과 3~6개월 제안 검증을
   같은 규칙으로 오인하지 않는다.
8. 백업·PAPER 원장·승인·내부 알림 보관기간. 시장정보 1년 보존을 그대로 적용하지 않는다.
9. 공식 미국 session calendar/비상휴장 공급자와 조기폐장 갱신 절차.
10. KIS 해외 시세의 사용자 계약상 저장·가공 허용, 시세 지연·권한·quota와 수정주가 의미.
11. 최신 ETF 구성·배당·split·복잡 기업행동 및 total-return benchmark가 없는 동안 어떤
    전략을 `UNAVAILABLE`로 유지할지.
12. 승인 동작의 추가 사용자 재인증 필요 여부와 reservation을 24시간 유지할 때의 UX.
13. PAPER의 개별종목 5%, ETF 40/승인 예외 50%, 위성 10% 각각의 분모를 현재 PAPER NAV,
    세션 시작 PAPER NAV, 다른 PAPER-only 기준 중 무엇으로 할지. 실제 전체자산은 선택지에서
    제외하며 미결정 동안 관련 신규매수를 차단한다.
14. 내부 PAPER를 정책 단계 C의 선행조건·보완자료·대체자료 중 무엇으로 볼지와 D1 전환
    증거로 인정할지. 결정 전에는 단계 C 통과나 D1 가능으로 표시하지 않는다.
15. 월 승인 신규 투자 예산·1회 주문 한도를 내부 PAPER 매수와 매도에 어떻게 적용할지.
    초기 4,000,000원 및 전략별 예산을 월 승인 예산으로 자동 간주하지 않는다.
16. `PROJECT_RULES.md` 228행의 제안 해석을 PAPER에서 KST 날짜와 US session date 중 하나라도
    겹치면 차단하는 보수적 이중 key로 확정할지, 취소·정정·재주문의 예외가 있는지.
17. 실행창 안에서 가격조건 미충족·이탈을 어떤 주기·자료로 재평가하고 사용자에게 어떻게
    설명할지. 어떤 선택에서도 이는 영구 원인·`DEFINITIVELY_UNEXECUTABLE`·후순위 gate가 아니며,
    `TEMPORARILY_HELD` 후 실행창 종료 시 `MISSED_WINDOW`만 허용한다.

위 항목은 전부를 한 묶음의 구현 차단 사유로 취급하지 않는다. 하나의 항목이 두 분류에 걸치면
정책값과 구현 방법을 분리해 추적한다.

| 결정 분류 | 해당 항목·범위 | 결정 전 순수 엔진에서 가능한 범위 | 금지되는 범위 |
|---|---|---|---|
| P1의 영향받는 성공 경로 전에 필요한 사용자 정책 결정 | 2의 전략 mapping·2주 기준점, 4의 정책 수치, 5~7, 13~17 | nullable policy field, `POLICY_INPUT_REQUIRED/UNAVAILABLE/DISABLED`, 경계·상태 전이·보존식 test를 설계할 수 있음 | 값을 추정해 proposal을 `READY`로 만들거나 단계 C/D1 통과·후순위·재개를 활성화 |
| Codex가 추천할 기술 설계 선택 | 1의 DB/backup 구조, 3의 Decimal·비용 필드 구조, 8의 보관 메커니즘, stable root anti-splitting, 한 portfolio·US session 단위 ExecutionRun과 불변 proposal 목록 | 대안·migration/rollback·threat model과 fail-closed interface를 제안하고 총괄팀 승인용 계약을 동결할 수 있음 | 사용자 정책값이나 공급자 권리를 기술 기본값으로 확정 |
| 공급자 연결 또는 운영 활성화 전에 필요한 결정 | 3의 실제 비용·FX source/value, 4의 자료 의존 한도값, 7의 운영 검증·rollback 주체, 8의 실제 보관기간, 9~12 | provider/operation port와 `UNAVAILABLE`, 권리·calendar·reauth 상태를 표현하는 interface fixture를 설계할 수 있음 | 실제 keyring/provider/scheduler/PAPER 실행, 값 0 대체, 자료권리 확인 전 가용 표시 |

이 표는 C-21 당시의 결정 분류를 보존한다. 후속 P1 승인으로 순수 Decimal domain model의
fail-closed 부분까지만 구현했으며, 미확정값이 필요한 성공 경로와 운영 활성화는 해당 분류의
결정이 끝날 때까지 계속 닫힌다. P2 이후는 총괄팀의 다음 지시 전 시작하지 않는다.

### C-21 문서 독립 검증 기준

- 실제 class/function/endpoint/table 존재 여부를 이 gap 표와 대조하고, PAPER가 없는 상태를
  구현 완료로 읽지 않는다.
- 승인 근거 각 행을 원문 hash·행과 대조하고 분류 2/4 값을 정책으로 승격하지 않았는지 본다.
- ledger 합계, cash/reservation/cap, 승인 만료, rank 우회, regeneration, unknown recovery의
  상태 경로를 표 기반 model checking으로 검토한다.
- core/satellite 귀속표가 전략명으로 위험 등급을 바꾸지 않는지, 미분류가 위성 분자에
  포함되면서 신규매수는 차단되는지, PAPER 분모에 실제 자산이 들어가지 않는지 검토한다.
- SELL lot 수량 보존식과 UNKNOWN 예약 유지, 300,000원 동일-key 집계, KST/US 이중 날짜 claim,
  `MISSED_WINDOW/TEMPORARILY_HELD/USER_STOPPED`, 1순위 승인 거절 뒤 재생성 상태를 경계값과
  동시성 model로 검토한다.
- 세션·실행창 종료, 가격조건 미충족, 일시 거래정지가 후순위 gate를 열지 않는지, 영구 정책
  부적격 또는 승인 목록과 공식 근거가 일치한 확정 거래 불가만 gate를 여는지 검토한다.
  ExecutionRun은 한 portfolio·US session의 불변 proposal 묶음이고, 시도 0건만 run
  `MISSED_WINDOW`, 시도 뒤 결과 확정은 `COMPLETED(FULL/PARTIAL)`, 결과 미확인은
  `RECONCILIATION_REQUIRED` 뒤 `RECONCILED`여야 한다. proposal별 missed·예약을 run terminal과
  분리하고, 재시작 뒤 count/hash를 보존하며 이후 실행은 모든 gate를 재검증한 새 run ID만
  가능해야 한다.
- 8개 전략식의 단위·분모·point-in-time cutoff·revision·missing behavior와 위험정책 우회,
  survivorship/look-ahead를 독립 검토한다. 필수 metric 하나의 결측도 score 0이 아닌
  `UNAVAILABLE`·후보 제외인지 확인한다.
- `docs/DATA_SOURCES.md`의 공식 URL·권리·조건부/미지원 판정과 이 문서의 required inputs를
  맞추고, 확보되지 않은 총수익·ETF·기업행동 자료를 가용하다고 쓰지 않았는지 본다.
- `WEB_DESIGN_SPEC.md`, `docs/WEB_API.md`, `docs/REQUIREMENTS_TRACEABILITY.md`의 `초안/미구현`
  상태, 7절 카드, scope separation, 내부 알림 계약이 일치하는지 본다. 카드 4는 원문 제목
  `실제 배당과 배당 전환 능력`과 `PAPER 모의 배당` 배지를 함께 사용해야 한다.
- 소스·테스트·의존성·DB·`PROJECT_RULES.md`가 바뀌지 않았고, 실제 keyring/provider/server/
  scheduler/order 호출이 0인지 확인한다. C20 보존 ZIP은 source artifact이지 개인 backup이
  아니며 원본·보존본 hash가 일치하는지만 확인한다.

# P2 SQLite 영속 repository 설계 초안 (C-20261011-28)

이 절은 `C-20261011-27` / `V-20261011-22`에서 종결된 P1 순수 메모리 domain을 영속화하기
위한 **설계안**이다. DB 파일·table·migration·repository 소스는 만들지 않았고 운영 DB도 열지
않았다. P1의 단일 Python 프로세스 thread 원자성 결과를 다중 프로세스·재시작·강제 종료
내구성 근거로 재사용하지 않는다. P2 구현은 총괄팀의 별도 승인과 디스크 여유 확보 뒤에만
시작한다.

## 저장소 선택

| 선택지 | 장점 | 위험·제약 | 판정 |
|---|---|---|---|
| 시장자료 SQLite에 추가 | 기존 WAL·UTC µs·수집 backup 구조 일부 재사용 가능 | 시장자료/보고/job 소유 schema와 금융 PAPER 원장의 장애 범위가 결합되고, 기존 `MarketDataStore` exact schema 및 복원 계약을 변경함 | 비추천 |
| web-state SQLite에 추가 | 사용자·세션 owner 모델과 SQLAlchemy transaction 재사용 가능 | 인증 DB와 고빈도 ledger/reservation/fill이 결합되고 local schema v1 exact allowlist·backup 호환성을 크게 바꿈 | 비추천 |
| 별도 `paper-state.sqlite3` | scope·schema version·backup·복구·쓰기 잠금과 장애 범위를 PAPER에 한정하고 기존 두 DB를 변경하지 않음 | 세 번째 DB의 경로 충돌·backup bundle·권한·startup integrity 검증을 새로 구현해야 함 | **추천** |

추천안은 기존 시장자료 DB와 web-state DB를 그대로 두고, 같은 owner-only 데이터 디렉터리의
별도 PAPER DB를 사용한다. 절대경로는 운영 설정에서 검증하며 다른 두 DB와 동일 inode,
symlink, hardlink, 잘못된 UID, 비정규 파일이면 기동을 거부한다. 로컬 단일 사용자 fail-closed를
유지하되 그 전제만 믿지 않고 모든 PAPER 행·조회·변경에 `owner_id`를 둔다. 다른 owner의 ID를
알아도 복합 foreign key와 repository 조건을 통과할 수 없어야 한다.

별도 DB는 web-state에 cross-database foreign key를 만들 수 없으므로
`paper_owner_bindings(owner_id PRIMARY KEY, bound_at_utc_us, binding_hash)`를 최초 transaction에서
한 번만 만든다. 시작과 매 요청에서 기존 owner guard가 web-state의 사용자가 정확히 한 명이며
binding과 일치함을 확인한다. 0명·2명 이상·불일치이면 기동과 요청을 모두 거부하고 자동 재귀속하지
않는다.

## P1 domain과 영속 상태의 단일 원본

P1의 `PaperRepository` port는 유지하고 P2 adapter만 교체한다. 프로세스 수명 동안 보존되는
가변 aggregate나 write-behind cache를 권위 상태로 두지 않는다. 모든 mutation은 다음 하나의
unit-of-work로 수행하며, **idempotency receipt 판정을 revision/CAS보다 먼저** 한다.

1. 현재 세션 인증, single-owner fail-closed, `owner_id × portfolio_id` scope를 먼저 검증한다.
   이 단계를 통과하지 못하면 idempotency key의 존재 여부도 반환하지 않는다.
2. 명령 종류·owner·portfolio·canonical payload를 묶은 request fingerprint와 idempotency key를
   검증하고 `BEGIN IMMEDIATE` 안에서 같은 scope의 `paper_command_receipts`를 조회한다.
3. 같은 key·같은 fingerprint의 완료 receipt가 있으면 현재 revision/CAS나 P1 command를 다시
   실행하지 않고 저장된 immutable 결과와 당시 result revision을 반환한다. 응답에는
   `historical_replay=true`를 표시한다.
4. 같은 key의 fingerprint가 다르면 충돌로 거부한다. 완료되지 않았거나 손상된 receipt,
   receipt가 가리키는 결과 누락은 재실행하지 않고 write 차단·대조 필요로 처리한다.
5. receipt가 없을 때만 같은 transaction snapshot에서 schema version과 portfolio revision을
   읽고 아래 21개 `_PaperState` 필드를 완전 hydrate한다. 그 뒤 호출자의 `expected_revision`을
   비교하고 일치하는 신규 요청에 한해 순수 P1 command를 한 번 적용한다.
6. 발생 event·상태 projection·audit/outbox, 증가한 revision, 결과 본문/hash와 완료 receipt를
   같은 transaction에 기록한다. `UPDATE ... WHERE revision=:expected` 영향 행이 정확히 1개가
   아니면 receipt를 포함해 전부 rollback한다. 외부에 보이는 `PENDING` receipt는 만들지 않는다.
7. 응답은 commit된 result revision의 immutable 결과다. commit 뒤 응답 유실 시 같은 key·같은
   fingerprint 재시도는 3단계의 과거 결과만 돌려주며 fill·예약·승인 소비를 반복하지 않는다.

과거 receipt 반환은 현재 실행 권한, 승인 유효성 연장, 재실행 허가가 아니다. 새 mutation은 새
idempotency key로 현재 gate·승인·revision을 다시 통과해야 한다. 단순히 이후 command로 portfolio
revision이 바뀌었다는 이유로 이미 commit된 receipt 결과를 잃거나 conflict로 바꾸지 않는다.

따라서 메모리와 DB에 이중으로 먼저 반영하는 dual-write 경로는 없다. 읽기 cache가 필요해도
`owner_id/portfolio_id/revision`에 묶인 파생값만 허용하고 commit 또는 외부 revision 관측 시
폐기한다. 재시작은 DB event와 projection을 다시 대조하며 메모리 기본값으로 덮지 않는다.

## 제안 schema·정밀도·제약

| 영역 | 제안 table·핵심 제약 |
|---|---|
| schema/portfolio | `paper_schema_ledger`, `paper_owner_bindings`, `paper_command_receipts`, `paper_portfolios`; receipt는 owner/portfolio/key, request fingerprint, terminal status, result revision/hash/body를 보존. schema checksum과 application min/max version, `UNIQUE(owner_id, portfolio_id)`, scope는 `INTERNAL_PAPER`만, genesis event 한 번 |
| 원장·cash·lot | `paper_ledger_entries`, `paper_cash_balances`, `paper_position_lots`; entry append-only, correction은 `corrects_entry_id`, owner/portfolio 복합 FK, cash/qty 음수 금지, projection revision 저장 |
| 전략·예산·위험 | `paper_strategy_versions`, `paper_strategy_applications`, `paper_budget_versions`, `paper_monthly_budget_versions`, `paper_valuation_snapshots`, `paper_risk_snapshots`, `paper_daily_limits`; 불변 hash/version과 전체 적용 이력·cutoff·policy ref 보존 |
| 목적·후보·제안 | `paper_trade_purposes`, `paper_candidate_sets`, `paper_candidate_rank_clearances`, `paper_proposals`, `paper_price_condition_checks`, `paper_large_approval_groups`; stable root/version 계보와 `(owner, portfolio, root, instrument, side, US session)` 집계 key, rank·가격조건 인과 순서 불변 |
| 승인 | `paper_approvals`; type/target version/hash, 정확한 UTC µs 24시간, 취소·소비·audit ref. `consumed_by_ref`는 한 번만 설정 가능 |
| 예약 | `paper_reservations`, `paper_sell_lot_reservations`, `paper_reservation_events`; 활성·UNKNOWN·대조 필요뿐 아니라 해제·소비된 행과 최초/잔여량·released_at을 보존. proposal당 활성 reservation partial unique index, lot별 활성 예약 합이 잔여수량을 넘지 않음 |
| 실행·체결 | `paper_execution_runs`, `paper_attempts`, `paper_fills`; run proposal 목록/hash 불변, `(owner_id, portfolio_id, idempotency_key)` unique, 같은 key의 다른 payload hash는 fail-closed |
| 날짜 claim | `paper_kst_intent_claims`, `paper_us_session_claims`; 각각 `(owner_id, portfolio_id, instrument_id, kst_intent_date)`와 `(owner_id, portfolio_id, instrument_id, us_session_date)` unique, proposal ref 불변, release/delete 없음 |
| 대조·감사 | `paper_domain_events`, `paper_reconciliation_cases`, `paper_audit_events`, `paper_outbox`; monotonic event sequence와 payload hash, UNKNOWN 근거·예약·사용자 중지·복구 판정 append-only, 알림은 commit 뒤 outbox에서 파생 |

모든 시각 비교 열은 UTC epoch microseconds `INTEGER`를 원본으로 저장하고 aware ISO 문자열은
감사·표시용으로 함께 둘 수 있다. Decimal은 P1 canonical finite decimal 문자열과 해당 field의
currency/unit·rounding/version을 저장하며 SQLite `REAL`로 변환하지 않는다. DB `CHECK`만 믿지
않고 repository read/write 양쪽에서 P1 validator를 통과시킨다. 파생 balance와 count는 원장
revision/hash로 대조하며 불일치 시 신규 PAPER 명령을 잠근다.

## M1 `_PaperState` 전체 영속 매핑과 hydrate 계약 (C-20261011-29)

`asset_assistant.paper._PaperState`의 필드는 아래 **21개가 전부**다. `원본`은 domain object의
canonical 필드를 잘라내지 않고 owner/portfolio scope로 저장한다는 뜻이고, `투영+대조`는 현재
값을 저장하되 전체 append-only 원본 이력으로 다시 계산해 일치해야 한다는 뜻이다. 목록형 필드의
순서는 영속 `event_seq`와 UTC µs·불변 ID로 보존한다. 최신 N건이나 활성 행만 읽는 hydrate는
금지한다.

| P1 field | 판정 용도 | P2 저장 구조 | hydrate 재구성 | 불변식·손상 대응 | 재시작 시험 |
|---|---|---|---|---|---|
| `portfolio` | INTERNAL_PAPER 영역·owner·4백만원 genesis 기준 | `paper_portfolios` 원본 + genesis ledger/event | 의존 행이 없을 때만 `None`, 아니면 정확히 1행 | 중복, owner 불일치, genesis 누락이면 write 차단 | 생성 직후 restart에도 genesis 1회·현금 4백만원 |
| `cash` | 통화별 게시 현금과 활성 예약 차감의 기준 | `paper_cash_balances` 투영+대조, 원본은 전체 ledger/fill event | 통화별 원장을 전부 replay하고 저장 projection과 비교 | 누락 통화를 0으로 보정 금지, 음수·revision/hash 불일치는 대조 필요 | 배당·BUY·SELL·부분체결 각 직후 잔액 A/B 동일 |
| `ledger_entries` | genesis·배당·BUY/SELL·release의 append-only 원장 | `paper_ledger_entries` 원본 전체 | `event_seq`, `recorded_at_utc_us`, `entry_id`의 확정 순서로 전부 적재 | sequence gap·중복 ID·잘못된 correction ref·owner 혼입 차단 | correction·release 전후 restart에도 행·순서·합계 동일 |
| `budgets` | 8전략 예산·policy version·예약 한도 | `paper_budget_versions` 원본 | version ID별 immutable 객체로 적재 | hash/8전략/총액·policy ref 불일치 차단 | 예산 저장 뒤 목적·예약 판정 A/B 동일 |
| `strategy_versions` | 전략 계보와 적용 가능 version | `paper_strategy_versions` 원본 | prior chain을 생성시각 순서로 검증 후 ID dict 구성 | 미상 prior·cycle·hash/시간 역행 차단 | root와 child 저장 각 직후 stale sibling 판정 동일 |
| `strategy_applications` | 현재 적용 전략과 14일 변경 제한 | `paper_strategy_applications` 원본 append-only | 전체 행을 `(applied_at_utc_us, event_seq, application_id)` 순으로 list 복원 | 마지막 적용 누락·중복 ID·version/approval ref 단절 시 차단 | 직전 적용+14일 `-1µs`, 정확 경계, `+1µs` A/B 동일 |
| `lots` | 보유수량·원가·SELL 가능수량 | `paper_position_lots` 투영+대조, acquisition/fill/ledger 원본 | acquisition부터 모든 fill을 replay해 remaining qty/cost를 projection과 비교 | 음수·원본 초과·acquisition ref/owner 불일치 차단 | BUY, 부분 SELL, 전량 SELL 직후 lot와 available qty 동일 |
| `valuations` | PAPER NAV·위험분모·세션 시작 기준 | `paper_valuation_snapshots` 원본 | snapshot ID별 cutoff/source hash 포함 객체 복원 | 미래 자료·필수 값 누락·source hash 불일치면 해당 gate 차단 | PASS/UNAVAILABLE snapshot 뒤 위험 판정 동일 |
| `daily_limits` | US session NAV 20% executed/reserved/available | `paper_daily_limits` 투영+대조, reservation/fill event 원본 | session별 opening snapshot 뒤 예약·체결 전부 replay | KST 자정 reset 금지, `executed+reserved<=limit`, 누락 시 0 보정 금지 | 예약·부분체결·해제 및 session 경계 A/B 동일 |
| `purposes` | stable root·version·승인 집계 | `paper_trade_purposes` 원본 | parent/root 계보와 hash 검증 뒤 ID dict 구성 | dangling parent, 임의 root, split 검사·승인 ref 불일치 차단 | 같은 목적 재생성·승인된 새 root 직후 집계 동일 |
| `candidate_sets` | 1~4순위·승인·재생성 계보 | `paper_candidate_sets`와 child candidates 원본, state event 전체 | set/version/hash와 rank 순서를 검증해 ID dict 구성 | rank gap/중복, purpose/root·regeneration ref 단절 차단 | 승인·거절·재생성 각 직후 후보 상태 동일 |
| `candidate_rank_clearances` | 앞 순위의 확정 부적격과 후순위 허용 | `paper_candidate_rank_clearances` 원본 append-only | `(set_id, failed_rank)` key로 reason/evidence/hash 전체 복원 | owner/portfolio/set scope unique, 승인 원인 밖 reason·중복·누락 차단 | rank 1 clearance 전후와 restart 뒤 rank 2 허용 결과 동일 |
| `proposals` | 상품·방향·금액·가격조건·실행 상태 | `paper_proposals` 원본 + proposal state event 전체 | candidate/purpose/version ref 검증 뒤 현재 exact state로 복원 | hash/ref/state transition gap이면 실행 차단 | hold·cancel·reject·missed·unknown 전후 상태 A/B 동일 |
| `price_condition_checks` | 가격조건의 관측 cutoff·인과 순서·결정 | `paper_price_condition_checks` 원본 append-only | global `event_seq`와 proposal별 check sequence로 list 전체 복원 | `price.as_of <= cutoff <= evaluated_at`, proposal hash 일치, gap·역행 차단 | 검사시각 `-1µs`/동일/+1µs 및 복수 검사 순서 A/B 동일 |
| `approvals` | type-safe 24시간 승인·취소·1회 소비 | `paper_approvals` 원본/current + approval state event 전체 | target/version/hash와 모든 transition을 검증해 ID dict 구성 | 다른 type, target/hash 변경, 중복 소비, 이력 누락 시 차단 | 승인·bind·취소·만료·응답 유실 뒤 상태/소비 결과 동일 |
| `reservations` | 현금·SELL lot·전략/월 예산·일한도 보존 | `paper_reservations`, sell-lot rows, `paper_reservation_events` 원본 전체 | **활성만이 아니라** RELEASED·CONSUMED와 UNKNOWN·NEEDS_RECONCILIATION, 최초/잔여량·released_at까지 ID dict 복원 | proposal당 활성 1개, 잔여량≤원래량; 과거 해제 행·audit 누락 시 causal floor 불명으로 차단 | 생성·부분체결·해제·취소·UNKNOWN 각각 직후 예약/available 값 동일 |
| `fills` | 가상 체결·원장/lot/cash 반영·중복 방지 | `paper_fills` 원본 append-only + command receipt | fill ID와 idempotency fingerprint 순으로 전부 복원 | 실제 broker ref 금지, 중복 fill·reservation/proposal ref 불일치 차단 | commit 전/후 kill·동일 key 재시도에도 fill 1건 |
| `runs` | 불변 proposal 묶음·attempt·중지·대조/terminal 상태 | `paper_execution_runs`, `paper_attempts`, run state event 원본 전체 | included hash 검증, distinct attempt ID와 상태 전이를 순서대로 복원 | UNKNOWN attempt 누락·count/hash 불일치·불법 terminal이면 대조 필요 | READY/RUNNING/PARTIAL/UNKNOWN/USER_STOPPED/RECONCILED 직후 동일 |
| `audits` | 승인·예약·상태변경 근거와 proposal causal floor | `paper_audit_events` 원본 append-only 전체 | `event_seq`, `recorded_at_utc_us`, `audit_id` 순서로 list 전체 복원 | 최신 tail만 읽기 금지, duplicate·관련 ref/owner 불일치 차단 | 해제·거절·중지 audit 뒤 과거시각 재생성 차단 결과 동일 |
| `kst_intent_claims` | 같은 종목 KST 날짜 1회 claim | `paper_kst_intent_claims` 원본, `(owner, portfolio, instrument, kst_date)` unique | 전체 행을 `(instrument_id, kst_intent_date) -> proposal_id`로 복원 | release/delete 없음, duplicate·proposal ref 누락 차단 | KST 자정 `-1µs`/정확/+1µs와 같은 날 두 process 결과 동일 |
| `us_session_claims` | 같은 종목 US session 날짜 1회 claim | `paper_us_session_claims` 원본, `(owner, portfolio, instrument, session_date)` unique | 전체 행을 `(instrument_id, us_session_date) -> proposal_id`로 복원 | release/delete 없음, calendar/proposal ref 누락 차단 | 서로 다른 offset의 같은 session과 session 전환 A/B 동일 |

### 완전 hydrate 순서와 손상 처리

1. 같은 read transaction에서 schema/checksum, owner binding, portfolio revision과 전체 event 범위를
   고정한다. 정상적인 완전 빈 DB만 `_PaperState()`로 시작할 수 있다.
2. portfolio, budget/strategy version과 **전체** strategy application을 읽고 계보·14일 순서를
   검증한다.
3. 전체 ledger와 fill/reservation event를 읽어 cash·lot·daily-limit을 재계산하고 저장 projection과
   맞춘 뒤 valuation을 적재한다.
4. purpose → candidate set → rank clearance → proposal → price check → approval → reservation →
   fill → run/attempt → 날짜 claim → audit의 모든 행을 읽는다. 객체 생성 순서와 무관하게 publish
   전에 모든 owner/portfolio 복합 참조·hash·UTC µs 인과관계를 다시 검사한다.
5. 21개 필드의 canonical state hash와 행 수, 마지막 event sequence를 snapshot metadata와 대조한
   뒤에만 aggregate를 공개한다. snapshot은 가속 수단일 뿐 원본이 아니며 `last_event_seq`와 prefix
   hash가 맞을 때만 사용하고 이후 event를 하나도 빠짐없이 replay한다. 최근 로그 일부만 읽거나
   retention으로 과거 차단 근거를 버리지 않는다.

미지원 schema/domain-event version, sequence gap, 누락·중복·dangling reference, owner 혼입,
payload/state hash 불일치, snapshot prefix 불일치는 빈 dict·0·현재시각으로 채우지 않는다. 원본
전체 replay로 안전하게 입증할 수 있으면 손상 snapshot을 폐기하고 다시 만들 수 있지만, 원본 이력도
불완전하면 PAPER mutation을 잠그고 `RECONCILIATION_REQUIRED`로 표시한다. UNKNOWN attempt와 그
예약은 명시적 대조 전까지 유지한다.

### 저장·hydrate 동등성 검증 계획

같은 초기 상태와 동일한 event/command suffix를 두 경로에 입력한다. A는 재시작 없이 계속 실행하고,
B는 지정 transition 직후 commit→process 종료→새 repository hydrate 뒤 계속 실행한다. 각 지점에서
21개 필드를 canonical 직렬화한 state hash뿐 아니라 다음 command의 결과/error code, cash·lot·원장,
모든 예약(해제 포함), 승인 소비, 두 날짜 claim, rank clearance, run/attempt 상태와 repository
revision이 같아야 한다.

- portfolio/budget/strategy draft·apply, purpose/candidate/rank clearance, proposal/가격검사,
  세 승인, 예약, run 전이, 부분/전체 fill, release/cancel의 **각 주요 transition 직후** restart한다.
- 14일 변경 제한과 KST 날짜·US session 날짜 경계의 `-1µs`/정확/+1µs, rank 1→2 clearance,
  가격조건 관측·cutoff·평가 순서를 A/B로 비교한다.
- 해제·취소·부분체결·UNKNOWN·NEEDS_RECONCILIATION·USER_STOPPED 뒤 restart에서 과거 causal
  floor와 예약이 사라지지 않고 자동 성공·실패·재개되지 않는지 확인한다.
- row/event/projection 저장 각 failpoint와 commit 전 강제 종료, event sequence 중간 누락,
  unknown version, duplicate ID, dangling ref, owner 간 참조 혼입은 부분 state를 publish하지 않고
  rollback 또는 write 차단해야 한다.
- 유효 snapshot+후속 event, 오래된 snapshot, snapshot hash 불일치, snapshot 이후 event gap을
  비교한다. 유효한 경우 full replay와 같고, 원본 손상이면 기본값 복원 없이 차단한다.
- idempotency는 commit 뒤 응답 유실 후 다른 command가 revision을 올린 경우까지 포함한다. 같은
  key·fingerprint는 당시 저장 결과를 반환하고, key가 같고 fingerprint가 다르면 충돌하며, 어느
  경우도 fill·승인 소비·예약·audit가 하나 더 생기지 않아야 한다.

## 원자적 승인·예약·fill 처리

- 새 root 발급은 유효한 `NEW_ROOT_PURPOSE` 행을 target/hash/만료/미취소/미소비 조건으로
  conditional update하고 `TradePurpose`·overlap/split 검사 근거·audit를 같은 transaction에
  insert한다. 영향 행이 1개가 아니면 전부 rollback한다. 다른 승인 type은 사용할 수 없다.
- `CANDIDATE_SET`과 `LARGE_TRADE`도 같은 방식으로 한 번만 소비한다. 새 root 승인은 큰 거래,
  일일 한도, 위험 gate를 대신하지 않는다.
- 현금·SELL lot·전략/월 예산·일일 gross 예약은 한 transaction에서 현재 executed와 활성 예약을
  다시 합산하고 생성한다. SQLite writer 직렬화와 portfolio revision을 함께 사용하며, 단순
  사전 `SELECT` 결과만으로 허용하지 않는다.
- fill은 idempotency row 선점을 먼저 하고 fill·ledger·cash/lot·reservation·daily limit·audit를
  같은 commit으로 반영한다. 같은 key·같은 payload 재수신은 기존 결과를 반환하고, 같은 key의
  다른 payload는 대조 필요로 차단한다.
- `UNKNOWN/NEEDS_RECONCILIATION`은 cash·daily·SELL lot 예약을 유지한다. 사용자 중지 상태와
  중지시각도 commit하며, 재시작이나 오류 소멸만으로 자동 재개하지 않는다.

## 다중 프로세스·강제 종료·재시작

- SQLite는 `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=FULL`, bounded
  `busy_timeout`을 사용한다. writer는 `BEGIN IMMEDIATE`와 revision compare-and-swap으로
  직렬화한다. `SQLITE_BUSY` 재시도는 아직 durable event가 없는 transaction 시작 전으로
  제한하며 횟수·총시간을 고정한다.
- commit 전 강제 종료는 transaction 전체가 보이지 않아야 한다. commit 뒤 응답 전 종료는
  idempotency key를 재조회해 기존 결과를 돌려주며 새 fill·승인 소비를 만들지 않는다.
- commit 성공 여부를 확인할 수 없거나 attempt 결과가 불명확하면 `UNKNOWN`을 유지한다.
  reservation을 해제하거나 성공/실패를 추정하지 않는다.
- 시작 시 schema/checksum, `PRAGMA integrity_check`, owner, revision 연속성, 원장 대비
  cash/lot/reservation/daily-limit projection, 열린 run·attempt·reconciliation을 검사한다.
  불일치는 PAPER write를 잠그고 명시적 대조 case를 만든다.
- `USER_STOPPED`는 영속 terminal/blocked 의미를 그대로 복구한다. 명시적 사용자 재활성화와
  전체 gate 재검사 없이는 READY/RUNNING으로 돌아가지 않는다.

## migration·backup·파일 보호

P2 최초 구현은 비어 있는 별도 DB의 schema v1 생성만 허용하고 기존 실제/SAMPLE/시장/web
자료를 자동 import하지 않는다. 이후 migration은 원본 온라인 backup, schema/hash/row-count
사전 점검, 단일 transaction, 사후 integrity·projection 대조, 새 임시 위치 복원 시험을 거친다.
지원 범위보다 새 schema면 읽기·쓰기를 모두 거부하고, 오래된 schema는 검증된 순방향 migration
없이는 열지 않는다. down migration이나 기존 DB 덮어쓰기는 기본 제공하지 않는다.

DB parent는 0700, 본체·WAL·SHM은 0600과 현재 UID를 요구한다. 첫 SQLite 연결 전에는
`O_NOFOLLOW`·`fstat` 기반 검사와 생성 권한을 적용하고, 연결이 열린 동안에는 X1 회귀를 막기
위해 같은 inode를 별도 `open/close`하지 않는다. 실행 중에는 `lstat(follow_symlinks=False)`와
경로 metadata/chmod만 사용하고 dispose 후에만 descriptor 검사를 다시 한다. online backup은
SQLite API로 일관된 snapshot을 만들고 기존 암호화 bundle 계약을 확장하되, 실제 사용자 DB와
동일 위치·nonempty target·symlink·overwrite를 거부한다.

현행 market/web 두 entry backup 형식은 V1로 계속 읽을 수 있어야 한다. PAPER를 포함하는
bundle은 V1 의미를 조용히 바꾸지 않고 market/web/paper 세 entry와 각 snapshot 시각·revision을
가진 새 V2 형식으로 만든다. 세 SQLite snapshot이 자동으로 동일 순간이라고 주장하지 않으며,
전환용 일관 backup은 PAPER writer를 명시적으로 quiesce한 뒤 생성한다.

## 디스크 부족·쓰기 실패

P2 시작 전 여유 공간은 DB 크기, 최대 WAL, 일관된 backup, 복원본, 실패 주입 산출물을 함께
수용해야 한다. 이번 환경에서는 root 사용자 가용 공간 **최소 1 GiB, 권장 2 GiB**를 확보하기
전 구현·대규모 시험을 시작하지 않는다. 실제 운영 preflight의 정확한 식은 구현 전에 DB/WAL
상한과 backup 정책을 확정해 `max(승인된 고정 floor, 예상 DB+WAL+backup+복원 여유)`로 정한다.

`SQLITE_FULL`, I/O 오류, permission 오류, busy timeout은 성공으로 바꾸지 않는다. transaction을
rollback하고 신규 PAPER write를 차단하며, DB 자체에 오류 기록을 쓸 수 없다는 점을 고려해
민감값 없는 process 상태 code를 표시한다. 공간이 돌아와도 integrity·revision·reservation 대조
전 자동 재개하지 않는다. 부분 파일·임시 backup은 원본과 구분하고 자동으로 운영 DB를 대체하지
않는다.

ENOSPC 시험은 현재 루트 파일시스템을 실제로 채우지 않는다. 크기가 제한된 새 `/dev/shm`
fixture, SQLite `max_page_count`, VFS/error injection을 사용하고 상한을 명시한다.

## P2 구현 예정 파일과 순서

별도 승인 뒤 예상 변경은 신규 `asset_assistant/paper_sqlite.py`, 신규
`tests/test_paper_sqlite.py` 및 강제 종료용 비제품 test helper, 필요할 때만 repository port를
확장하는 `asset_assistant/paper.py`, release exact allowlist/test, 위 설계·API·추적·backup
문서다. `wealthy_server` API/UI 연결, scheduler, 공급자, broker/order는 P2 범위가 아니다.

구현 순서는 (1) 디스크 여유 확보와 보호 파일 baseline, (2) empty schema·owner/path guard,
(3) aggregate load/commit+revision, (4) 승인·목적·감사 원자성, (5) cash/SELL/daily 예약,
(6) attempt/fill idempotency와 UNKNOWN 복구, (7) migration/online backup/restore,
(8) 별도 프로세스·kill-point·ENOSPC 회귀, (9) P1·기존 SQLite 보안 전체 회귀다. 각 단계 실패는
다음 단계 진행과 운영 활성화를 차단한다.

## P2 독립 검증 계획

| 시험 | 독립 재현과 기대 결과 |
|---|---|
| 승인 동시 소비 | 별도 프로세스 8개가 같은 승인 ID를 소비해 정확히 1개 commit, 나머지 type-safe conflict; purpose·audit도 1개 |
| 동시 예약 | 별도 프로세스에서 현금, 동일 SELL lot 수량, buy+sell 일일 gross 경계를 경쟁시켜 합계가 한도를 넘지 않음 |
| 중복 fill | 같은 idempotency key·같은 payload를 process/restart 경계에서 반복해 fill·ledger 1건; 다른 payload는 차단 |
| owner 격리 | 두 owner fixture로 모든 IDOR 읽기·쓰기·복합 FK·backup restore 교차 접근 거부; 로컬 다중 사용자 상태는 fail-closed |
| transaction 실패 | 승인 소비 뒤 purpose 전, ledger 뒤 projection 전 등 각 failpoint와 `SIGKILL`에서 전부 rollback 또는 완전 commit만 관측 |
| commit 응답 유실 | commit 직후 process kill 뒤 같은 key 재시도 시 기존 결과 반환, 추가 승인 소비·fill·예약 없음 |
| 재시작 일치 | `_PaperState` 21필드 전체와 원장·cash·lot·daily limit·활성/해제 reservation·approval·strategy application·rank clearance·price check·KST/US claim·audit·USER_STOPPED·UNKNOWN을 재구성해 종료 직전과 동일 |
| 저장공간 부족 | 제한 filesystem/SQLite fault injection으로 `SQLITE_FULL`·부분 쓰기·WAL 실패를 재현해 기존 commit 보존, 신규 write 잠금, 자동 재개 0 |
| busy/경합 | bounded timeout·제한 retry 뒤 명확한 retryable 상태; 중복 command/fill 생성 0 |
| backup/복원 | online encrypted backup을 새 빈 위치에 복원하고 schema/hash/count/Decimal/UTC µs/owner/revision/UNKNOWN 예약 일치 |
| SQLite 보안 | DB·WAL·SHM 0600, symlink/hardlink·타 UID·시장/web DB 경로 충돌 거부, 실행 중 보조 fd open/close 0, X1/W2/W4 회귀 |

메모리 `RLock` unit test는 위 다중 프로세스·강제 종료·복원 시험의 대체 증거로 인정하지 않는다.
