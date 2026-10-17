# 공식 자료 공급자·이용 경계

최초 기준일: **2026-10-13 (Asia/Seoul)**  
내부 PAPER 공급자 검토 갱신일: **2026-10-14**

이 문서는 공급자 사실과 실제 연결 상태를 기록합니다. 투자 정책의 단일 원본은
`PROJECT_RULES.md`입니다. `OK/PARTIAL` 실연결 기록은 이 Ubuntu 환경에서 앱의 읽기 요청과
응답 검증까지 실제 수행했다는 뜻일 뿐, 자료 정확성·지속 제공·투자성과를 보장하지 않습니다.
공식 예제 키만 쓴 연결은 `DEMO ONLY`, 인증정보가 없어 호출하지 않은 공급자는 fixture가
통과해도 `NOT RUN`입니다.

## UI-MARKET-01 미국 시장정보 공급자

아래 표는 `asset_assistant.market_information`이 사용하는 **고정 등록 엔드포인트**와
2026-10-13 제한 실연결 결과입니다. 이 경로는 제목·식별자·공표시각·수집시각·원문 URL만
저장하는 메타데이터 목록입니다. RSS의 `description`이나 링크된 본문은 저장하지 않으며,
번역·요약·감성 분석·추천·가격 전망·주문 또는 기존 16시 7절 보고서 입력으로 사용하지
않습니다. `OK`는 해당 시점의 한정 호출 성공일 뿐 장기 가용성이나 내용의 정확성을
보장하지 않습니다.

| 공급자 | 실제 등록 엔드포인트 / 공식 문서 | 자료·지원 범위 | 인증·계약·요금 | 호출 제한·지연·앱 주기 | 저장·가공·재배포 경계 | 2026-10-13 실제 연결 상태 |
|---|---|---|---|---|---|---|
| SEC EDGAR 최신 공시 | [`browse-edgar` Atom](https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom&count=100&owner=include) / [SEC Developer Resources](https://www.sec.gov/about/developer-resources), [EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [자동 접근 정책](https://www.sec.gov/about/webmaster-frequently-asked-questions) | 미국 EDGAR 최신 제출의 제목·accession·공표시각·원문 링크 | API 키·유료 계약은 필요하지 않지만, SEC 정책을 만족하는 **실제 연락 가능한** 로컬 User-Agent가 필수입니다. 연락처는 OS 보안 저장소에만 등록하며 앱이 임의 생성하지 않습니다. | SEC는 전체 요청을 초당 10회 이하로 제한합니다. SEC 두 소스가 같은 제한을 공유하도록 앱에서 요청 사이를 최소 0.11초 띄우고 EDGAR 목표 주기는 1,800초입니다. Atom 반영 지연의 보장 SLA는 확인하지 못했습니다. | 공개 공시의 최소 메타데이터와 원문 링크만 개인 로컬 DB에 저장합니다. 출처를 보존하며 본문·첨부를 복제하지 않습니다. 대량·제3자 재배포 권리는 이번 범위에서 확인 완료로 표시하지 않습니다. | 준수 User-Agent가 등록되지 않아 **NOT RUN / 사용자 설정 필요**. fixture 파싱·상태 처리는 검증 대상이며, 설정 누락이 다른 공급자를 막지 않습니다. |
| SEC 보도자료 | [`pressreleases.rss`](https://www.sec.gov/news/pressreleases.rss) / [SEC RSS 안내](https://www.sec.gov/files/about/secrss.shtml), [자동 접근 정책](https://www.sec.gov/about/webmaster-frequently-asked-questions) | SEC 공식 보도자료 제목·시각·원문 링크 | EDGAR와 동일하게 키·요금은 없지만 실제 연락 User-Agent가 필요합니다. | EDGAR와 초당 10회 제한을 공유하며 앱 목표 주기는 3,600초입니다. 피드 공표와 수신 사이의 보장 SLA는 확인하지 못했습니다. | RSS 제공을 본문 재배포 허가로 확대 해석하지 않습니다. 최소 메타데이터만 로컬 저장하고 원문은 사용자 클릭으로 엽니다. | 준수 User-Agent가 등록되지 않아 **NOT RUN / 사용자 설정 필요**. fixture만 검증 대상입니다. |
| Federal Reserve | [`press_all.xml`](https://www.federalreserve.gov/feeds/press_all.xml) / [공식 RSS 안내](https://www.federalreserve.gov/feeds/feeds.htm), [재사용 정책](https://www.federalreserve.gov/disclaimer.htm) | 연준 공식 보도자료·통화정책 발표 메타데이터 | 키·등록·별도 요금 없음. | RSS 정량 한도와 공표 지연 SLA는 공식 안내에서 확인하지 못했습니다. 앱 목표 주기는 3,600초입니다. ETag·Last-Modified 조건부 요청을 사용합니다. | Board 자체 자료와 링크된 제3자 자료의 권리를 구분합니다. 앱은 제목·시각·식별자·원문 링크만 저장하며 본문을 저장하거나 재배포하지 않습니다. | 제한 호출 **HTTP 200**, 14,897 bytes, 20개 항목 확인. 같은 validator의 조건부 요청은 **HTTP 304** 확인. |
| BLS 발표 RSS | [`empsit.rss`](https://www.bls.gov/feed/empsit.rss), [`cpi.rss`](https://www.bls.gov/feed/cpi.rss), [`ppi.rss`](https://www.bls.gov/feed/ppi.rss), [`jolts.rss`](https://www.bls.gov/feed/jolts.rss) / [BLS RSS 안내](https://www.bls.gov/help/hlp_rss.htm) | 미국 고용상황·CPI·PPI·JOLTS 발표 메타데이터 | 네 피드는 키·등록·별도 요금이 없는 공개 RSS입니다. 기존 BLS Public Data API 등록키와는 별도 경로입니다. | RSS 정량 한도·지연 SLA는 확인하지 못했습니다. 각 피드 앱 목표 주기는 3,600초입니다. 실패 시 피드별로 격리하고 대기시간을 늘립니다. | 기존 BLS 시계열 API의 이용 조건을 RSS 본문 재배포 허가로 대신하지 않습니다. 최소 메타데이터·원문 링크만 로컬 저장합니다. | 첫 사전 확인에서는 네 endpoint가 HTTP 403이었으나, 최종 앱 경로 제한 재검증에서는 네 endpoint 모두 **HTTP 200**으로 수집됨. 시점·요청 환경에 따라 접근 결과가 달라질 수 있으므로 장기 가용성 PASS가 아니며 403 fixture 회귀도 유지합니다. |
| BEA | [`rss.xml`](https://apps.bea.gov/rss/rss.xml) / [BEA Developers](https://www.bea.gov/resources/for-developers), [공표 정책](https://www.bea.gov/about/policies-and-information/priorities-and-schedules-posting-content) | 미국 경제분석국 보도자료 메타데이터 | RSS 읽기에 키·등록·별도 요금 없음. BEA Data API 인증과는 별도입니다. | RSS 정량 한도와 공표 지연 SLA는 확인하지 못했습니다. 앱 목표 주기는 3,600초이며 조건부 요청을 사용합니다. | 제목·시각·식별자·원문 링크만 개인 로컬 DB에 저장합니다. RSS 제공을 전체 본문 또는 링크된 제3자 자료의 재배포 허가로 해석하지 않습니다. | 제한 호출 **HTTP 200**, 88,554 bytes, 48개 항목 확인. 같은 validator의 조건부 요청은 **HTTP 304** 확인. |

SEC 연락 User-Agent는 인증 토큰이 아니지만 연락 가능한 개인정보이므로 화면·API 응답·로그·
SQLite·브라우저 저장소에 원문을 되돌려 보내지 않습니다. Ubuntu Secret Service를 사용할 수
없으면 평문 파일로 대체하지 않고 `설정 필요`로 둡니다. 사용자에게 채팅이나 명령행 인자로
연락처를 보내도록 요구하지 않습니다.

자동 갱신은 `wealthy_server.local_runtime serve` 백엔드가 실행 중일 때만 위 목표 주기를
확인합니다. Chrome을 닫아도 백엔드가 살아 있으면 계속될 수 있지만, 백엔드 종료·절전·PC
종료 중 실행을 보장하지 않습니다. 수동 새로고침도 동일한 공급자별 대기·중복 claim을 거쳐
호출 제한을 우회하지 않습니다. 응답의 ETag·Last-Modified, 304, `Retry-After`, 제한시간,
2 MiB 응답 상한과 실패별 backoff를 공급자 상태에 보존하고, 실패해도 마지막 정상 항목을
삭제하지 않습니다.

소스에 기록된 기관(`institution_id`)과 피드(`provider_id`)를 구분합니다. SEC의 EDGAR와
보도자료, BLS의 네 발표 피드는 각각 상태를 갖지만 기관 수를 여러 곳으로 중복 집계하지
않습니다. 원문 URL의 `SAFE` 표시는 등록 host·HTTPS·구조 검사 통과만 뜻하며, 목록을 읽을
때 목적지를 다시 요청하거나 현재 접속 성공을 보증한다는 뜻이 아닙니다.

이번 지시가 참조한 `TECH-20261013-MARKET-INFO-01`과
`DESIGN-20261013-MARKET-01` 원문 파일은 프로젝트 및 제공된 첨부에서 확인되지 않았습니다.
그 내용을 추측하지 않았고, 확인 가능한 총괄팀 지시와 현행 코드·공식 문서만 이 표에
반영했습니다. 원문이 제공되면 별도 차이 추적이 필요합니다.

## 기존 수집·후보 평가 공급자 표

아래 표는 미국 시장정보 목록 이전부터 존재한 시계열·현재가·후보 평가 공급자 상태입니다.
위 UI-MARKET-01 피드와 같은 기능 또는 같은 실연결 결과로 합치지 않습니다.

| 공급자 / 공식 문서 | 자료·시장 | 인증·비용 | 제한·지연 | 저장·가공·재배포 경계 | 구현·실제 상태 |
|---|---|---|---|---|---|
| [BLS Public Data API](https://www.bls.gov/developers/home.htm), [v2 서명](https://www.bls.gov/developers/api_signature_v2.htm), [제한 FAQ](https://www.bls.gov/developers/api_FAQs.htm), [약관](https://www.bls.gov/developers/termsOfService.htm) | 미국 CPI·고용 등 공식 경제 시계열 | v2 URL에 다중 시계열 POST 사용. 등록키가 없으면 v1 호환 서명·미등록 한도, 등록키가 있으면 v2 확장 한도 적용. 공개 API이며 별도 요금은 확인되지 않음 | 공식 FAQ: 미등록 25회/일·25개 시계열/요청·10년, 등록 500회/일·50개·20년, 공통 50회/10초. API 반영이 발표보다 하루 늦을 수 있음 | BLS 발행물은 원칙적으로 public domain. 출처·접근일 표시와 “내려받은 뒤 분석을 BLS가 보증하지 않음” 고지 필요 | v2 URL 다중 시계열 1회 POST와 선택 등록키 구현. 2026-10-11 Ubuntu 실제 호출은 63건 저장, 결측 관측 2건 때문에 **PARTIAL**(최신 기준 2026-09-01). 결측을 0으로 바꾸지 않음 |
| [Federal Reserve RSS](https://www.federalreserve.gov/feeds/feeds.htm), [재사용 정책](https://www.federalreserve.gov/disclaimer.htm) | 미국 연준 보도자료·통화정책 등 공식 발표 **메타데이터** | 키·등록·요금 없음 | 공개된 정량 한도 없음. 앱은 매일 1회 저빈도 조회 | 별도 표시가 없으면 Board 자료는 public domain. 출처 표시. 링크된 제3자 기사 본문은 별도 권리 | 공식 발표 RSS 어댑터 구현. 2026-10-11 최종 독립 DB 재검증은 20건으로 **OK**(최신 기준 2026-10-09 14:00Z). 기사 본문·광범위 기업 뉴스가 아님 |
| [SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [접근 정책](https://www.sec.gov/about/webmaster-frequently-asked-questions) | 미국 기업 제출 이력·XBRL 재무 | API 키·요금 없음. `조직명 연락이메일` 형식의 실제 연락 가능한 식별 User-Agent 필요; 앱은 길이·ASCII 헤더 안전성과 끝의 이메일 형식을 호출 전에 검사 | SEC 공정접근 정책상 전체 장비 합산 최대 10회/초. 공식 안내상 submissions는 통상 1초 미만, XBRL은 통상 1분 미만 지연이고 peak에는 더 길 수 있으며 bulk는 야간 재생성 | 공개 EDGAR 자료를 출처와 함께 정규화 저장. 자동 접근 정책을 지켜야 하며 EDGAR Next 제출 API·토큰은 범위 밖 | submissions/companyfacts 읽기 어댑터·fixture 구현. 준수 User-Agent 미등록이라 실제 호출 **NOT RUN / 설정 필요** |
| [KIS API 목록](https://apiportal.koreainvestment.com/apiservice-category), [이용 절차](https://apiportal.koreainvestment.com/about-howto), [공식 샘플](https://github.com/koreainvestment/open-trading-api) | 지원되는 국내 주식·ETF **현재가 관측**, 누적 거래량·거래대금. 거래소 일봉·수정주가 수집은 미구현 | 사용자 본인의 KIS 서비스 신청과 App Key/Secret 필요. 앱은 paper/live 자격을 분리 저장. 계좌·상품·시세권한별 계약·요금은 사용자 조건에서 재확인 | 모든 사용자·TR에 적용되는 안정적인 공식 수치를 이번 확인에서 확정하지 못함. 코드는 자체 간격(paper 1.05초, live 0.06초)과 제한 재시도를 두지만 이는 공식 한도 보증이 아님 | 로컬 저장·가공 및 제3자 제공·재배포 권리는 KIS·거래소 약관과 사용자 계약을 별도 확인해야 함. 확인 전 재배포하지 않음 | access token은 메모리에만 두는 국내 현재가 읽기 어댑터·fixture 구현. 실제 거래일/장상태 시각을 payload에서 확정하지 못해 최초 관측시각으로 기록. 키 없음: **NOT RUN / 설정 필요**. 계좌조회·주문 메서드 없음 |
| [OpenDART 공시검색](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001), [전체 재무](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020), [약관](https://opendart.fss.or.kr/intro/terms.do) | 국내 공시와 연결/별도·분기/누적 재무 | 발급된 40자 인증키 필요. 약관상 원칙적으로 무료이나 향후 일부 유료 전환 가능 | 공식 응답코드 안내는 일반적으로 20,000건 이상 요청 시 제한되며 사용자별 설정이 다를 수 있다고 명시. 공시·재무 반영 지연은 일정한 SLA로 확인되지 않음 | 공공데이터법·저작권·약관을 따르며 제공 공시의 정확성·완전성을 FSS가 보증하지 않음. 원문 대량 재배포는 사용 목적별 재확인 | 공시·재무 읽기 어댑터·fixture 구현. 키 없음: **NOT RUN / 설정 필요** |
| [한국은행 ECOS API](https://ecos.bok.or.kr/api/), [개발 명세](https://ecos.bok.or.kr/api/#/DevGuide/DevSpeciflcation/OA-1040), [저작권 정책](https://www.bok.or.kr/portal/main/contents.do?menuNo=200228) | 한국 금리·통화·물가·국민계정 등 공식 통계 | 운영은 사용자 인증키 필요. 발급 유형·유효기간·영리 이용·요금 조건은 사용 전 공식 포털의 현재 안내를 재확인 | 이번 확인에서 고정된 공식 정량 호출 한도는 확정하지 못함. 표별 공표·잠정·수정 주기가 다름 | ECOS 출처 표시. 제3자 저작물·상업 이용 제한 통계는 원 작성기관 조건을 별도 확인 | StatisticSearch 읽기 어댑터·fixture 구현. 공식 `sample` 키 연결검사 실제 성공은 **DEMO ONLY**; 운영 수집은 키 없어 **NOT RUN** |
| [SEC Form N-PORT 데이터](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets) | 미국 펀드/일부 ETF 월별 보유내역 | 공개·무인증 | 공개 bulk는 분기 갱신, 파일이 매우 큼 | 공개 filing이나 정확성 보증 없음. 원 filing 대조 필요 | 설계 후보. 대용량 다운로드·자동 적재 **NOT RUN** |
| [KRX Open API](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd), [약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp) | 국내 거래소 지수·주식·ETF/ETN 등 | 인증키와 서비스별 승인. 공개 API는 비상업 범위, 별도 유료 데이터 가능 | 키당 1일 10,000회로 안내됨 | 출처 표시, 제3자 제공 금지 등 서비스별 조건 | 승인·키 없음: **NOT RUN** |
| [GDELT](https://www.gdeltproject.org/about.html), [DOC API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) | 세계 뉴스 제목·URL·메타데이터 | 키·요금 없음 | 정량 quota 비공개, rate limited | GDELT 인용·링크 필요. 연결된 언론사 본문 권리는 별도 | 최소 실제 조회가 HTTP 429. **연결 성공 아님 / 미활성** |
| [NewsAPI 가격](https://newsapi.org/pricing), [약관](https://newsapi.org/terms) | 광범위 뉴스 헤드라인·메타데이터 | 키 필요. 무료는 개발용, 운영 요금제 별도 | 무료 100회/일·24시간 지연 등 요금제별 | 원문 미제공, 재게시·경쟁 DB 제한 | 계약·키 없음: **NOT RUN** |
| ETF 발행사·거래소 페이지 | 상품 문서·구성종목·보수 | 공급자별 계약·자동접근 허가 확인 필요 | 상품별 | 공개 열람을 자동수집·재배포 허가로 보지 않음. BlackRock/Vanguard 등 반복 자동접근 제한 확인 | 웹 크롤러 **미구현·차단**. 정식 API/파일 계약 뒤 별도 어댑터 필요 |

## localhost 웹 연결 상태

위 표의 과거 adapter/desktop 실연결 기록과 현재 localhost 운영 상태는 같지 않습니다.

- 기존 15시 수집·16시 보고서 예약은 Ubuntu user systemd의 one-shot이
  `wealthy_server.local_runtime run-task collect|report`를 호출합니다. 별도 cloud worker나
  PostgreSQL을 시작하지 않으며 정규화된 `CollectionRun`/`MarketRecord`는 기존 SQLite에
  저장합니다. UI-MARKET-01 자동 갱신은 이 systemd 슬롯이나 16시 보고서에 연결하지 않고,
  localhost 백엔드 프로세스 안에서만 별도 주기로 실행합니다.
- 2026-10-13 미국 시장정보 제한 실연결은 최종 앱 경로에서 Fed·BEA·BLS 네 피드가 HTTP
  200, SEC 두 피드는 설정 필요/무호출이었습니다(총 신규 메타데이터 116건). 그보다 앞선
  BLS 사전 확인의 HTTP 403과 Fed·BEA 조건부 304도 별도 사실로 보존합니다. 이는 장기 예약
  운영 PASS가 아닙니다. 아래 기존 표의 BLS API
  2026-10-11 `PARTIAL`과 Fed 기존 어댑터 `OK`는 서로 다른 실행 이력입니다.
- KIS paper/live는 사용자가 웹의 KIS 설정 화면에서 입력할 수 있습니다. OpenDART, ECOS,
  BLS 등록키는 기존 공급자 설정을 사용합니다. SEC 연락 User-Agent는 시장정보 화면에서
  기존 자격증명 API와 OS keyring 경계를 재사용해 등록·교체·삭제합니다. 원문은 Ubuntu
  Secret Service의 기존 `CredentialStore`
  항목에만 저장하며 SQLite·브라우저 저장소·API 응답·로그에는 두지 않습니다. 보안 저장소가
  없으면 평문으로 대체하지 않습니다.
- KIS credential은 현재가·거래량·거래대금 수집에만 사용할 수 있습니다. 계좌 조회·모의/실전
  주문 endpoint나 broker 전송 코드는 없습니다.
- `GET /api/v1/status`의 configured 표시는 필요한 비밀 항목이 등록되었다는 뜻일 뿐 실제
  provider 인증·자료 수신 성공이 아닙니다. 성공 여부는 provider별 최신 collection run과
  기준·공표·수집시각을 별도로 확인합니다.
- 과거 cloud/PostgreSQL worker와 server AES-GCM credential 구현은 삭제하지 않은 비활성
  legacy입니다. 현행 localhost runtime이 import·시작하거나 자료를 자동 이전하지 않습니다.

클라우드에 배포하지 않았으므로 서버 IP allowlist, region별 지연, 실제 장기 호출 quota와
운영 저장권리 검증은 아직 `NOT RUN`입니다.

## 구현된 공통 수집 계약

UI-MARKET-01 피드는 등록된 HTTPS 엔드포인트와 허용 host만 사용하고, 프록시 환경변수를
사용하지 않습니다. DNS·리다이렉트·원문 링크에서 loopback·사설·link-local·사용자정보·
제어문자·비 HTTP(S) 대상을 거부합니다. XML의 DOCTYPE/ENTITY, 과도한 깊이·노드·텍스트를
거부하고 제목 등 외부 문자열은 코드나 지시가 아닌 텍스트 데이터로만 렌더링합니다. 피드
본문 설명은 파서 입력에 포함되더라도 DB 항목으로 저장하지 않습니다.

- 고정된 HTTPS host만 허용하고, 연결·읽기 제한시간과 최대 응답 크기를 적용합니다.
- 기존 API 어댑터는 HTTP 429·5xx와 일시적 네트워크 오류만 최대 2회 제한적으로
  재시도합니다. UI-MARKET-01 피드는 한 실행에서 즉시 반복하지 않고 `Retry-After`와
  지수형 대기 뒤 다음 백엔드 poll에서 다시 시도합니다. KIS 국내
  시세 요청은 모의 최소 1.05초, 실전 최소 0.06초 간격으로 자체 제한합니다. 호출 제한을
  다른 키나 공급자로 우회하지 않습니다. 이 간격은 공식 할당량을 대신하거나 보장하지 않습니다.
- ECOS의 공식 정량 호출 한도는 확인하지 못했으므로 비공식 안전장치로 요청 attempt 사이를
  최소 0.25초 띄우고, 한 실행의 논리 호출을 100회(재시도 포함 최대 300 attempt), 같은 DB의
  하루 예약을 900 attempt로 제한합니다. 이는 ECOS의 공식 할당량이 아니며 다른 DB나 외부
  프로그램과 공유되지 않습니다.
- BLS는 대상 시계열을 v2 URL의 단일 POST로 묶고, 가능한 최대 재시도 횟수까지 미리 일일 예산에
  예약합니다. OpenDART도 회사별 최대 페이지와 재무 요청의 최악 재시도 수를 20,000 기준에서
  보수적으로 예약합니다. ECOS도 위 비공식 안전 상한의 최대 재시도 수를 미리 예약합니다.
  이 원자적 예산과 수집 lease는 **같은 SQLite DB를 쓰는 앱 인스턴스**만
  조정합니다. 다른 `--database`나 같은 키를 쓰는 외부 프로그램과 전역 할당량을 공유하지 않습니다.
- URL query/path에 키가 들어가는 공급자도 예외 원문·URL·헤더·본문을 로그나 화면에
  되풀이하지 않습니다.
- 공급자, 종류, 공급자 레코드ID, 종목 식별자, 시장, 통화, 기준·공표·수집시각, 단위,
  수정 여부, 공식 출처와 내용 해시를 SQLite에 보존합니다.
- 공식 공표시각을 응답에서 확인할 수 없으면 `FIRST_OBSERVED_AT_COLLECTION`으로 표시하고
  첫 수집시각 이전의 과거 분석에 소급하지 않습니다.
- 보고서 cutoff보다 기준·공표·수집시각이 뒤인 레코드는 제외합니다. 다만 자동 보고서는 여러
  짧은 SQLite 읽기를 사용하므로, 앱 밖에서 극히 좁은 시점에 DB가 바뀌는 상황까지 하나의 단일
  transaction snapshot으로 고정하지는 않습니다. 앱 수집 lease가 활성 상태면 부분 보고로 표시합니다.
- 결측·오래됨·부분 실패와 전체 실패를 구분합니다. 실패한 갱신은 마지막 정상 레코드를
  삭제하지 않습니다.
- 공시·공식 발표·뉴스 문자열은 비신뢰 데이터입니다. 그 안의 명령·URL로 앱 설정, 전략,
  파일, 네트워크 대상 또는 권한을 변경하지 않습니다.
- 계약형 뉴스 권리가 확인되기 전에는 기사 본문을 내려받거나 저장하지 않습니다.

UI-MARKET-01은 기존 `CollectionRun`/`MarketRecord` 후보 평가나 7절 보고서와 독립된
`market_information*` 테이블을 사용합니다. 발행시각이 없으면 `null`, 날짜만 있으면
`DATE` 정밀도로 보존하며 수집시각으로 대신하지 않습니다. 시각은 UTC epoch microseconds로
저장하고 화면에서 Asia/Seoul로 변환합니다. 같은 문서의 출처 수정은 source revision으로
보존하고 새 정보 수를 중복 증가시키지 않습니다. 제목만 보고 종목이나 실제 보유 여부를
추정하지 않습니다.

`STALE`은 공급자별 `last_success_at + (cadence_seconds × 2)`가 현재 시각보다 앞선 경우를
뜻합니다. 부분 실패 시에는 마지막 정상 자료와 마지막 성공 시각을 보존합니다. 통계의 기준월,
공시 대상기간, 시장 거래일이 경제적으로 오래되었는지를 판단하는 별도 임계값은 아직 없습니다.
따라서 `STALE`이 아니어도 각 레코드의 기준·공표·수집시각을 함께 확인해야 합니다.

## 장애 복구·소급 수집 한계

- systemd `Persistent=true`와 앱의 예약 슬롯 처리는 다시 실행될 때 가장 최근 놓친 호출을 한 번
  시도하지만, 여러 날의 시장자료를 자동으로 완전 복원하는 backfill 엔진은 아닙니다.
- OpenDART 공시 목록은 실행일 기준 최근 7일만 조회합니다. 그보다 긴 중단은 공시 누락 가능성이
  있으며 별도 기간 backfill 명령은 없습니다.
- ECOS 시계열과 OpenDART 재무의 기간·사업연도는 사용자가 저장한 고정 설정입니다. 새 기간으로
  자동 이동하지 않습니다.
- Fed 수집은 현재 RSS 피드에 남아 있는 항목만 저장합니다. 장기 중단 중 피드에서 빠진 발표를
  과거 archive에서 보완하지 않습니다.
- KIS는 실제 거래일·장상태 시각이 확정된 일봉이 아니라 요청 시점 현재가·누적량 관측입니다.
  앱은 최신 동일 run·기준/수집시각 관측 묶음만 후보 입력에 사용하고, 부분 응답을 같은 날짜의
  이전 관측으로 채우지 않습니다. 이를 쌓아 종가·수정주가·20일 상대거래량·6개월 모멘텀·
  변동성으로 계산하지 않습니다.

## 현재 확보하지 못한 운영 자료

- KIS 사용자 키와 서비스 신청, 시장별 실시간/지연 권한
- OpenDART·ECOS 사용자 키
- SEC 정책을 만족하는 사용자의 실제 연락 User-Agent와 분석 대상 CIK
- BLS 발표 RSS가 사전 확인에서 403, 최종 앱 경로에서 200으로 달라진 원인과 장기 접근 안정성
- 광범위 뉴스 운영 계약, 기사 본문 저장·요약·재배포 권리
- ETF 발행사 자동 파일 전송 또는 거래소 이용계약
- 사용자 보유 종목·예산과 자동 시장 식별자의 승인된 매핑

실제 실행 여부는 위 표와 검증 로그에서 `OK/PARTIAL`, `DEMO ONLY`, `NOT RUN`으로
구분합니다. 2026-10-11 최종 독립 DB 연결 시험에서는 BLS 요청이 `PARTIAL`, Fed RSS가
20건 `OK`, ECOS `sample` 연결검사가 4행을 관측했으나 `persistable=False`인
`DEMO ONLY`였습니다. KIS·OpenDART·SEC와 운영 ECOS는 **NOT RUN**입니다. 앱 공급자 설정
화면은 현재 활성·비활성·준비·최근 수집 상태를 표시합니다.
localhost 웹과 기존 desktop의 공급자 인증정보는 대화나 JSON이 아니라 같은 OS 보안 저장소
경계를 사용합니다. 웹 API는 등록·교체·삭제만 중계하고 원문을 응답하지 않습니다. SQLite,
브라우저 저장소, console, 보고서와 access log에는 비밀을 저장하지 않습니다.

현재 GUI의 대상 입력은 문자열 목록입니다. CLI에는 `ecos-series`(통계표·주기·기간),
`sec-concepts`(companyfacts 개념), `opendart-financial`(회사·사업연도·보고서·연결/별도)
구조화 설정 명령이 있지만 그 설정용 GUI 화면은 없습니다. 해당 어댑터의 fixture 통과를
실제 사용자 키로 운영 검증한 상태로 해석하지 마세요. 공개 자료를 단순히 볼 수 있다는 사실은 뉴스 기사 본문이나 ETF
발행사 페이지의 자동 수집·저장·재배포 허가가 아닙니다.

## 내부 PAPER용 미국 자료 확보 검토 — 설계 초안

확인일: **2026-10-14**. 이번 검토는 공식 문서 웹 열람만 했으며 API 호출·가입·로그인·키 발급은
0회다. 아래 `확보 가능`은 문서상 설계 후보라는 뜻이지 현행 앱 연결 PASS가 아니다. 실제
history 범위·지연·권리·quota를 사용자 계약과 별도 연결 시험으로 확인하기 전에는 PAPER
가상체결이나 전략 score에 사용하지 않는다.

| 요구자료 | 공식 공급자·문서 | 무료·갱신·history / 인증 | 원시·조정·권리 경계 | 판정과 현행 상태 |
|---|---|---|---|---|
| 미국 주식·ETF 현재가·일봉 OHLCV·거래량 | [KIS 공식 API portal](https://apiportal.koreainvestment.com/docs), [공식 해외주식 구성](https://github.com/koreainvestment/open-trading-api/blob/main/MCP/KIS%20Trading%20MCP/configs/overseas_stock.json), [해외 기간별 시세 예](https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py) | 본인 KIS 계정·Open API 신청·App Key/Secret 필요. 해외 일/주/월과 현재가 endpoint는 확인했으나 모든 TR의 공식 정량 quota·비용·history 길이는 확정 못함. 미국 무료 현재가는 제공 범위가 유료 통합시세와 다를 수 있음 | `MODP` 수정주가 flag가 있어도 배당 포함 total return과 모든 기업행동 정합성을 보장하지 않음. 샘플 코드 공개는 시세 저장·가공·재배포 허가가 아님 | **조건부 핵심 후보 / 미연결**. 현행 adapter는 국내 현재가뿐. 사용자 권리 확인 뒤 해외 조회 전용 adapter를 주문과 분리해야 함 |
| 미국 공시·재무 | [SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), [Fair Access/FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions) | key·요금 없음; 실제 연락 가능한 식별 User-Agent 필요; 총 10 req/s 이하. submissions 통상 1초 미만, XBRL 통상 1분 미만이지만 peak 지연 가능; bulk는 야간 갱신 | 공개 filing 재사용 가능. filing accepted/published 시각, amendment, taxonomy/context, 통화·단위·연결/분기/누적을 보존하고 SEC가 정확성을 보증한다고 표현하지 않음 | **확보 가능 / adapter 일부 구현, 실제 연결 NOT RUN**. point-in-time factor 변환과 전략 연결은 미구현 |
| ETF 지연 보유내역 | [SEC Form N-PORT datasets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets), [README](https://www.sec.gov/files/nport_readme.pdf) | key·요금 없음; dataset은 분기 갱신. 월말 filing이 공개·dataset 반영되므로 당일 구성이 아님 | 공개 제출자료지만 as-filed 오류·정정 가능. filing date와 보유 기준일 분리 | **확보 가능하지만 지연**. 최신 전체 구성·일중 NAV·당일 리밸런스·실시간 중복노출 근거로 사용 불가; 적재 미구현 |
| 최신 ETF 전체 구성·산업비중·분배 | 각 발행사 공식 download/API·계약 | 발행사별 상이. 일부 파일은 공개 열람 가능해도 공통 API·quota가 없고 인증/전문가 계약이 필요한 feed가 있음 | 반복 자동접근·로컬 저장·화면 표시·재배포 권리는 발행사별 확인. 공개 페이지를 무단 crawl하지 않음 | **조건부/범용 무료 공급자 미지원**. N-PORT를 지연 대안으로만 사용; 권리 확인 전 ETF 관련 score 차단 |
| 노동·물가 | [BLS API](https://www.bls.gov/developers/home.htm), [FAQ](https://www.bls.gov/developers/api_FAQs.htm), [약관](https://www.bls.gov/developers/termsOfService.htm) | v1 무등록 25회/일·25 series/요청·10년, v2 무료 등록 500회/일·50 series·20년, 공통 50회/10초. API 반영이 발표보다 늦을 수 있음 | 접근일·출처·면책을 표시하고 BLS 자료를 변조·오인 표시하지 않음 | **확보 가능 / 기존 adapter·과거 제한 실연결**. 발표시각 이후만 전략 macro context에 사용 |
| GDP·소득·산업 | [BEA API guide](https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf), [재사용 FAQ](https://www.bea.gov/help/faq/147) | 무료 UserID 등록. 공식 guide의 일반 제한: 100 req/min, 100 MB/min, 오류 30회/min; 초과 시 일시 timeout | 별도 표시가 없으면 public domain, 출처 표기. 발표일과 개정 vintage 보존 | **확보 가능 / 시장정보 metadata 일부 구현, 정량 feature 미구현** |
| 금리·연준 발표 | [Federal Reserve feeds](https://www.federalreserve.gov/feeds/feeds.htm), [H.15](https://www.federalreserve.gov/releases/h15/), [재사용 정책](https://www.federalreserve.gov/disclaimer.htm) | key·요금 없음; 공개 고정 quota 없음. H.15는 평일 16:15 ET 게시 안내 | 별도 표시가 없으면 Board 자료는 public domain, 출처 표시. 링크된 제3자 권리는 별도 | **확보 가능 / 발표 metadata 구현**. FRED보다 Board 직접 파일 우선; 전략 정량 feature는 미구현 |
| FRED 경제지표 | [API key](https://fred.stlouisfed.org/docs/api/api_key.html), [API terms](https://fred.stlouisfed.org/docs/api/terms_of_use.html), [2024 약관 변경](https://fred.stlouisfed.org/news/2024/06/weve-updated-our-terms-of-use-action-requested) | 개인 API key 필요. 고정 공개 quota는 없고 공급자가 제한 가능 | 현 약관은 API 콘텐츠의 caching/archiving 및 database 편입을 금지하며 제3자 series 권리도 있음 | **Wealthy 영구 이력 DB 미지원**. 서면 허가 없이 사용하지 않고 BLS/BEA/Fed 등 원기관으로 대체 |
| USD/KRW 참고 환율 | [ECB SDMX API](https://data.ecb.europa.eu/help/api/data), [환율 설명](https://data.ecb.europa.eu/key-figures/ecb-interest-rates-and-exchange-rates/exchange-rates), [재사용/면책](https://www.ecb.europa.eu/services/using-our-site/disclaimer/html/index.en.html) | key·요금 없음; 평일 약 16:00 CET 갱신; 고정 quota 미기재; full history query 가능 | EUR/USD와 EUR/KRW 교차 참고값. actual FX trade price가 아니며 출처·단위·기준시각 표시 | **참고 valuation 조건부 가능 / 미구현**. PAPER 환전 체결값으로 사용 금지. ECOS direct USD/KRW도 사용자 key/권리 확인 필요 |
| 거래일·조기폐장 | [NYSE hours/calendar](https://www.nyse.com/trade/hours-calendars), [Nasdaq holiday schedule](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule) | 공개 연간 페이지, key 없음; 비상 변경 가능; 공식 machine API/SLA는 확인 못함 | version·확인일을 저장하고 거래 당일 변경을 재확인. 페이지 자동 저장·재배포 권리 미확정 | **수동 검증 기준으로 조건부 / 자동 calendar 미구현**. 검증 calendar 없으면 PAPER 실행 차단 |
| 거래정지 | [Nasdaq Trader Trade Halt RSS](https://m.nasdaqtrader.com/Trader.aspx?id=TradeHaltRSS), [feed terms](https://www.nasdaqtrader.com/content/administrationsupport/agreementstrading/THRSSFeedTermsCond.pdf) | 무료·무인증; 거래일 1분 갱신, 1분보다 자주 조회 금지 | feed 편집·왜곡·오인 결합 금지; 장기 보존/재배포 범위는 추가 확인 | **현재상태 filter에 조건부 / 미구현**. 최소 metadata·원문 link만 검토 |
| 배당·분할·복잡 기업행동 | [Nasdaq Daily List](https://classic.nasdaqtrader.com/Trader.aspx?id=DailyListPD), [NYSE Market Event Feed](https://www.nyse.com/market-data/corporate-actions/market-event-feed), KIS 해외 권리 API | Nasdaq/NYSE 정식 feed는 구매·계약/승인이 필요; KIS/ICE 권리자료도 사용자 인증·제3자 권리 확인 필요 | 미래 예정 event는 변경 가능. split/dividend/merger/spin-off와 cost basis correction을 version 보존 | **무료 범위 미지원**. 승인 없는 유료 사용 금지; 없으면 배당·모멘텀·원장 기업행동 자동화 차단 |
| 비교지수·총수익률 | KIS 일부 지수/수정주가, 거래소·지수사업자 공식 상품 | 지수별 라이선스·사용권 상이; 무료 보편 API 미확인 | price index와 dividend-included total-return index를 구분. 자체 재구성에도 배당·기업행동 완전성 필요 | **미지원**. price return을 total return으로 표시하거나 tracking error를 계산하지 않음 |
| 공식 경제·규제 뉴스 | [SEC press releases](https://www.sec.gov/newsroom/press-releases), [Fed feeds](https://www.federalreserve.gov/feeds/feeds.htm), [BLS feeds](https://www.bls.gov/feed/), [BEA releases](https://www.bea.gov/news/current-releases) | 공개·무인증, 기관별 fair access/cadence 준수 | 제목·발행시각·공식 URL 중심. 본문·제3자 link 권리는 별도 | **범위 제한 확보 가능 / 현행 metadata 일부 구현**. 정부·규제·거시 발표이며 포괄적 기업뉴스가 아님 |
| 포괄적 일반 뉴스 | [NewsAPI 가격](https://newsapi.org/pricing), [약관](https://newsapi.org/terms) 등 계약형 서비스 | 확인된 예의 무료 plan은 개발용·24시간 지연·100회/일이고 운영은 유료. 승인된 월비용 없음 | 제3자 저작권, 전문 재게시·DB화 제한 | **무료 운영 미지원**. 무단 crawl 금지; 뉴스 부족은 `UNAVAILABLE`, 다른 feature로 숨겨 재정규화하지 않음 |

현실적인 순서는 SEC/BLS/BEA/Fed/ECB와 N-PORT의 **fixture 기반 point-in-time 정규화**,
Nasdaq 거래정지의 약관 고정 검토, 사용자 권리 확인 뒤 KIS 해외 조회 전용 연결이다. 최신
기업행동·ETF 구성·총수익·일반뉴스를 확보하지 못하면 관련 전략은 `UNAVAILABLE`이고 예산을
다른 전략으로 자동 이전하지 않는다. KIS 주문·잔고 함수가 공식 sample에 존재해도 Wealthy에
import하거나 활성화하지 않는다.
