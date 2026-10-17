# 다음 단계 계획 — 계좌 지원 검증, 위험성향 비교, 미연결 자료

확인 기준일: **2026-10-11 (Asia/Seoul)**

현재 A2 단계는 공식 시장자료를 읽기 전용으로 수집합니다. GUI의 공급자 설정 창과
`credential_store.py`는 사용자 본인의 **자료조회용** 인증정보를 OS 보호 저장소에
등록·교체·삭제할 수 있습니다. KIS 국내 시세 읽기 어댑터는 있으나 실제 사용자 키로 호출한
검증은 없고, 계좌 조회·모의/실전 주문 어댑터는 없습니다. 이 문서는 **아직 구현되지 않은**
계좌별 기능과 위험성향 정책 선택을 구분해 기록합니다. 투자 정책의 단일 원본은
`PROJECT_RULES.md`이며, 아래 비교안은 정책 변경이나 주문 승인이 아닙니다.

현재 A2 설정은 GUI의 KIS `paper/live` 시세 환경·전략 혼합 비중과 CLI의
`sec-concepts`, `ecos-series`, `opendart-financial` 고급 대상 명령까지 지원합니다.
고급 대상 설정 GUI와 계좌 조회·주문 기능은 아직 없습니다. KIS 요청에는 모의 1.05초,
실전 0.06초의 앱 자체 최소 간격을 두지만 이는 공식 할당량 보증이 아니며, 사용자별 실제
인증·서비스 권한은 아직 검증되지 않았습니다.

## 첫 실행과 사용자별 증권사 등록 흐름

향후 첫 실행 마법사는 다음 경계를 지킵니다.

1. 기존 수동 원장, 현재 읽기 전용 시장자료, 미래 계좌/주문 권한을 구분해 설명합니다.
2. 공식 지원을 검증한 증권사만 선택 목록에 활성화합니다. 초기 후보가 한국투자증권(KIS)
   하나뿐이라면 KIS만 표시하며 “모든 증권사 지원”이라고 표현하지 않습니다.
3. 증권사·계좌 유형별로 `UNKNOWN`, `UNSUPPORTED`, `VERIFIED` 상태와 인증·조회·모의·주문
   능력을 각각 보여 줍니다. `UNKNOWN`인 기능은 실행할 수 없습니다.
4. 사용자가 선택한 증권사의 **본인 인증정보만** 로컬 입력 화면에서 받습니다. 개발자 키,
   공용 키, 예제 키를 배포하거나 공유하지 않습니다. 현재 버전의 GUI 입력은 시장자료용으로
   한정되며 계좌 조회/주문 권한을 활성화하지 않습니다.
5. 키·시크릿은 운영체제 보호 저장소에 직접 기록합니다. 현재 구현은 고정된 비민감
   service명과 `provider:field` 항목명으로 keyring을 호출하고, 앱 DB에는 비밀이나
   `credential_ref`를 저장하지 않습니다. 미래 계좌 연결에서 참조가 필요하더라도 불투명한
   비민감 값만 별도 설계하며 JSON, `.env`, 로그, 오류, 보고서, fixture에 평문을 쓰지 않습니다.
6. 계좌 연결을 별도 승인하는 미래 단계에서는 주문 메서드가 없는 READ_ONLY 계좌 조회
   어댑터로 인증과 최소 조회를 검증합니다. 현재 시장자료 읽기 어댑터와 권한을 합치지 않습니다.
7. 교체·폐기·접근 실패 흐름과 보호 저장소 삭제 확인을 제공합니다. 저장소를 사용할 수 없는
   환경에서는 평문 파일로 대체하지 않고 등록을 중단합니다.

전체 계좌번호가 API에 필수인지와 안전한 보관 방식은 계좌 유형별 공식 문서 확인이
필요합니다. 확인 전에는 앱 DB·JSON·로그·보고서에 전체 계좌번호를 저장하거나 표시하지
않습니다. 화면에는 내부 별칭과 마스킹된 식별자만 사용합니다.

## 운영체제 보호 저장소 조사

### Windows 10/11 x64

현재 코드는 `keyring`의 Windows backend만 허용하도록 구현했지만 실제 Windows 동작은
**NOT RUN**입니다. OS 수준 근거는 Windows Credential Manager의 generic credential입니다.
Microsoft의
[`CredWriteW`](https://learn.microsoft.com/en-us/windows/win32/api/wincred/nf-wincred-credwritew)는
현재 토큰의 로그온 세션과 연결된 사용자 credential set에 항목을 만들거나 갱신하고,
[`CredReadW`를 포함한 wincred 함수 목록](https://learn.microsoft.com/en-us/windows/win32/api/wincred/)
은 읽기·삭제 API를 제공합니다. 앱은 `CRED_TYPE_GENERIC` 사용 가능성, 사용자 전환, 잠금,
삭제, Windows 패키징 후 동작을 실제 Windows에서 검증해야 합니다.
`TargetName`, `UserName` 같은 검색 메타데이터에도 키·시크릿·전체 계좌번호를 넣지 않습니다.
현재 keyring 항목명은 비민감한 `provider:field`이며 무작위 `credential_ref` 방식은 구현되어
있지 않습니다.

앱 관리 암호화 blob이 꼭 필요할 때만 사용자 범위
[`CryptProtectData`](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)를
대안으로 검토합니다. 기본적으로 같은 사용자 자격과 같은 컴퓨터에 묶이는 성질을 고려하고,
다른 로컬 사용자도 복호화할 수 있는 `CRYPTPROTECT_LOCAL_MACHINE`은 사용하지 않습니다.
Credential Manager와 DPAPI 중 최종 방식은 위협모델·백업/복구·삭제 시험 뒤 확정합니다.

### Ubuntu 24.04 LTS

현재 코드는 `keyring`의 Secret Service backend만 허용합니다. GNOME
[`libsecret`](https://gnome.pages.gitlab.gnome.org/libsecret/)과
[Secret Service API](https://specifications.freedesktop.org/secret-service/latest/)가
기반입니다. 비밀은 잠금 가능한 collection의 secret value에 넣고, 검색 attribute는
보호되지 않는 메타데이터이므로 키·시크릿·계좌번호를 넣지 않습니다. 데스크톱 세션의
D-Bus 서비스, collection 잠금/해제, 사용자 취소, 로그아웃, 삭제를 시험합니다. Secret
Service가 없거나 잠겨 있고 안전하게 해제할 수 없으면 연결 기능을 fail closed합니다.

### Python 연결 계층

[`keyring` 공식 문서](https://keyring.readthedocs.io/en/stable/)는 Windows와 Secret Service
백엔드를 제공합니다. 현재 구현은 backend allowlist로 `null`, `fail`, `keyrings.alt` 및
평문 파일 backend를 거부합니다. 저장→읽기→삭제 왕복과 패키징 환경은 OS별로 추가
시험해야 하며, 실패 시 `.env`나 JSON으로
fallback하지 않습니다. 같은 사용자 권한으로 실행되는 악성 코드까지 막는 저장소라고
과장하지 않습니다. keyring의 service/username도 검색 메타데이터이므로 비밀·전체 계좌번호
대신 현재의 비민감 고정 서비스명과 `provider:field`만 사용합니다.

## 증권사·계좌 유형별 공식 지원 확인표

2026-10-11에 KIS의
[공식 Open Trading API 저장소](https://github.com/koreainvestment/open-trading-api)와
[`kis_devlp.yaml` 예제](https://github.com/koreainvestment/open-trading-api/blob/main/kis_devlp.yaml)를
문서로 검토했습니다. 별도 시세 조회 endpoint는 구현했지만 아래 계좌 지원표를 검증한
것은 아닙니다. 예제에는 실전/모의 자격증명과 상품코드 `01`(종합), `03`(국내
선물옵션), `08`(해외 선물옵션), `22`(개인연금), `29`(퇴직연금)가 보이지만, 예제에 코드가
있다는 사실은 각 계좌·각 endpoint의 공식 지원 또는 모의 지원을 증명하지 않습니다.
실제 사용자 인증 호출은 하지 않았습니다.

공식 예제가 평문 YAML 설정을 사용하더라도 이 프로젝트에는 복사하지 않습니다. 예제의 저장
방식은 API 지원 범위의 참고일 뿐, `PROJECT_RULES.md`의 비밀 저장 금지를 완화하지 않습니다.

| 후보 계좌/환경 | 인증 | 잔고·거래 조회 | 모의 | 주문 | 현재 처리 |
|---|---|---|---|---|---|
| KIS 종합/위탁 `01` | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 공식 endpoint·약관·계좌 조건을 확인하기 전 비활성 |
| KIS 개인연금 `22` | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 조회/상품/주문 제한을 별도 확인 |
| KIS 퇴직연금 `29` | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 조회/상품/주문 제한을 별도 확인 |
| KIS ISA | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 공식 상품코드와 지원 범위부터 확인 |
| KIS 모의환경 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 실전과 다른 키·도메인·TR 지원표를 endpoint별 확인 |
| KIS 국내/해외 파생 `03`/`08` | UNKNOWN | UNKNOWN | UNKNOWN | **UNSUPPORTED(정책)** | 레버리지·직접 파생 금지로 기능 비활성 |
| 다른 증권사 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 별도 어댑터·약관·인증/계좌 검증 필요 |

다음 조사에서는 계좌 유형 × 국내주식/해외주식/ETF/채권 × 인증/잔고/체결/모의/주문별
공식 endpoint, TR ID, 운영·모의 차이, 요청 한도, 약관, 비용, 계좌 개설 조건을 근거 URL과
확인일로 저장합니다. `VERIFIED`는 문서 확인뿐 아니라 사용자 승인 아래 별도 안전 환경에서
실제 응답 스키마와 오류를 검증한 뒤에만 부여합니다. 주문은 READ_ONLY 검증과 별개 단계이며,
지원이 확인되어도 사용자 건별 승인 없이는 전송하지 않습니다.

## 어댑터와 권한 구조

```text
첫 실행 UI
  └─ BrokerRegistry (검증된 증권사/계좌 기능만 노출)
       └─ CredentialStore (OS 보호 저장소, 비밀·참조값을 앱 DB에 저장하지 않음)
            └─ ReadOnlyBrokerAdapter (인증·잔고·체결 조회만)
                 └─ 영역 분리 원장 → 일반 계산 코드 → 보고서

향후 별도 구성:
OrderDraft → 규칙 검사 → 사용자 일회성 승인 → OrderExecutor
```

조회 어댑터와 주문 어댑터는 인터페이스·패키지·권한을 분리합니다. 현 A2 빌드는 시장자료
조회만 포함하며, 계좌 조회/주문 어댑터는 모두 없습니다. LLM은 비밀을
받거나 계산·규칙 판정·승인·주문 상태를 변경하지 않습니다.

## 위험성향 비교 시나리오 — 미구현·미적용 제안

아래는 사용자 선택 화면을 설계하기 위한 비교 예시일 뿐 현행 `PROJECT_RULES.md`를 바꾸지
않습니다. 주식 -40%, 채권 -10%, 현금성 0%, 위성 -60%라는 동일한 단순 충격을 기계적으로
적용했으며 예측·보장·최대손실 추정이 아닙니다.

| 비교안 | 주식 | 채권 | 현금성 | 위성 | 단순 충격 영향 |
|---|---:|---:|---:|---:|---:|
| 안정형 제안 | 40% | 45% | 10% | 5% | -23.50% |
| 균형형 참고(2028 현행 전체 목표) | 63% | 22.5% | 4.5% | 10% | -33.45% |
| 고위험형 제안 | 75% | 10% | 5% | 10% | -37.00% |

위성 10%는 최대 상한이며 최소 의무가 아닙니다. 위험성향 UI와 선택 저장 기능은 아직
구현되지 않았고, 사용자가 비교안을 선택하고 정책 변경을 명시적으로 승인하기 전에는 현재
배분 계산이나 주문에 적용하지 않습니다. 레버리지·신용·공매도·직접 파생상품 등 절대 금지
규칙은 모든 비교안에서 유지합니다. 실제 선택 전에 최대 감내손실, 회복 대기기간, 자금 목적,
계좌·상품 제한을 추가 확인해야 합니다.

## 다음 단계의 완료 조건

- A2 자료조회·수동 원장 회귀 테스트와 Windows 독립 테스트를 각각 통과
- 지원 증권사/계좌/endpoint 근거표와 이용 조건을 최신 공식 문서로 재확인
- 현재 OS 보호 저장소 backend allowlist의 Windows 실기기 저장·읽기·교체·삭제·잠금 실패 테스트 통과
- 로그/오류/백업/보고서/크래시 자료에 비밀과 전체 계좌번호가 없음을 검사
- 별도 계좌 조회용 READ_ONLY 어댑터는 계좌 유형별 공식 지원 확인 뒤 승인된 범위에서 설계·시험
- 실제 주문은 `PROJECT_RULES.md` 단계 C/D 조건과 별도 사용자 승인이 있기 전까지 미구현·차단
