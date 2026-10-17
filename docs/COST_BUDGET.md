# localhost 운영 비용 통제

확인 기준일: 2026-10-11

## 현재 기준

- 기본 인프라 비용 목표: 0원
- 전체 월 운영비 상한: 30,000원
- 이 상한은 결제 승인 금액이 아님
- 유료 서비스·요금제·cloud 자원·도메인 구매는 별도 사용자 승인 전 금지

현재 React/FastAPI/SQLite/systemd는 사용자 소유 Ubuntu PC에서 실행하므로 별도 서버·DB·도메인
비용은 없습니다. PC 전기·인터넷·저장장치 비용을 앱이 정확히 측정하지 않으므로 “전체 운영비
0원 보장”이라고 표현하지 않습니다.

## 항목별 상태

| 항목 | 현재 상태 | 비용 처리 |
|---|---|---|
| React/FastAPI/SQLite | localhost open-source runtime | 별도 구독 없음 |
| Ubuntu user systemd | OS 기능 | 별도 구독 없음 |
| Chrome | 사용자 설치 browser | 별도 앱 비용 없음 |
| Secret Service | Ubuntu 사용자 세션 | 별도 구독 없음 |
| KIS/OpenDART/ECOS 등 | 공급자별 인증·조건 확인 필요 | 요금·호출한도 확인 전 0원으로 단정 금지 |
| SEC/BLS/Fed | 공식 이용 조건·rate limit 준수 | 실호출 상태와 별도 기록 |
| 뉴스 | 허가 공급자 미선정 가능 | 공개 페이지 무단 crawl 금지, 유료 연결 승인 필요 |
| backup 저장장치 | 사용자 선택 | 외장/추가 저장공간 비용 확인 필요 |
| 외부 AI | 핵심 runtime에 불필요 | 금융자료 외부 전송·유료 호출 기본 비활성 |

## 차단 경계

- 앱에는 cloud billing·자원 생성·domain 구매 API가 없습니다.
- 비용 미확인 공급자는 `설정 필요/비용 확인 필요`로 표시합니다.
- 월 30,000원을 넘지 않는다는 이유만으로 자동 가입·결제하지 않습니다.
- 사용자 승인 없는 paid news, market data, AI, backup upload를 활성화하지 않습니다.
- 공급자 무료 정책이 변경될 수 있으므로 공식 조건·확인일을 `DATA_SOURCES.md`에 남깁니다.

## 비활성 이전 계획

Oracle/GCP/Cloudflare/DuckDNS 후보 조사와 21,000/27,000원 cloud 경고 설계는 이전 통합 지시
기록입니다. 현행 운영 기준이 아니며 `docs/FREE_INFRASTRUCTURE.md`에 legacy로 보존합니다.
cloud 계정·결제수단·DNS·공개 VM은 만들지 않았고 더 진행하지 않습니다.
