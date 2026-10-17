# 디자인 시안 대비 localhost 웹 구현

확인 기준일: 2026-10-11 21:52 Asia/Seoul  
현행 실행 기준: Ubuntu 24.04 · Google Chrome · `http://localhost:8000` ·
FastAPI `127.0.0.1` · React/TypeScript · SQLite

승인 시안 원본은 `design/references/`에 있으며 archive SHA-256은
`48f1d2a5241654d579521e254600fbfaf44931cf4c379d152dc9f5ccbb291a08`이다.
개별 hash는 `design/references/SHA256SUMS`를 따른다. 원본의 이름·종목·금액·수익률은
mock이며 실제 사용자 자료처럼 표시하지 않는다.

## 비교 원칙

- 제품 정책, 자료 신뢰와 보안이 시안보다 우선한다. 연결되지 않은 계좌·세금·배당·추천은
  시안 숫자를 옮기지 않고 `미입력`, `자료 필요`, `계산 불가` 상태를 표시한다.
- 시각적 차이는 `의도적 정책 차이`, `반응형 차이`, `부분 구현`, `미구현`, `결함`으로
  구분한다. 미구현을 의도적 차이라고 숨기지 않는다.
- 구현 캡처는 SAMPLE과 완전한 합성 fixture만 사용한다. 실제 금융정보가 있는 캡처는
  저장소·release·검증 로그에 넣지 않는다.
- `--visible`로 DISPLAY에 연 실제 Chrome, headless Chrome, 실제 OS 배율·실기기 결과를
  서로 구분한다. viewport/CDP 결과를 Windows·모바일 또는 물리 DPI 결과로 확대 해석하지 않는다.
- 이전 Docker/PostgreSQL/Caddy `127.0.0.1:8080` 캡처는 구현 재사용의 과거 증거일 뿐 현재
  운영 검증이 아니다. 현행 비교는 같은 origin `127.0.0.1:8000` localhost 캡처만 사용한다.

## 원본별 대응

| 원본 | 현재 web route | 구현된 대응 | 차이 분류와 이유 |
|---|---|---|---|
| `00_전체_화면_구성도_시안.png` | 전체 | sidebar/topbar/status rail/content, light/dark, 반응형 menu | 화면군 구조 반영. mock 수치 제거는 의도적 정책 차이 |
| `01_전체_대시보드.png` | `/dashboard` | 자료영역·기준시각·투자자산·초기자산·경고·공급자·최근 보고서 | 검증 자료가 없는 수익률·배당·보유종목 추정은 표시하지 않음 |
| `02_투자전략_비중_설정.png` | `/strategies` | 8전략 slider+숫자, 합계, 초안·저장 목표·실제 비중, 불변 version | 목표 금액은 완전한 서버 원장 합계가 있을 때만. 후보 평가 가중치와 별도 |
| `03_비중_오류와_저장_차단.png` | `/strategies` | exact Decimal 합 100%, 부족/초과 안내, disabled save | server도 독립 검증; 반올림으로 100 처리 안 함 |
| `04_전략명_클릭_가치투자_상세.png` | `/strategy-library`, `/strategies/:id` | 8종 설명·선정기준·장점·위험·engine mapping·필요자료 | 검증 자료가 없어 예시 종목·전망·추천 포트폴리오를 만들지 않음 |
| `05_계좌_ISA_관리.png` | `/accounts` | portfolio의 검증 원장과 계좌/ISA 권한 경계 | 계좌 조회·ISA 자동 판정·주문은 승인/공식 규칙 없어 부분 구현·비활성 |
| `06_종목_ETF_분석.png` | `/search`, `/analysis` | 공급자·cutoff·후보평가 자료 요구 조건 | 종목 master/통합 검색 UI는 부분 구현; 시안 mock 종목 미표시 |
| `07_매도_세금_시뮬레이터.png` | `/tax` | 공식 근거와 사용자 입력 필요 안내 | 세금 계산 미구현; 미검증 세율을 전 상품에 적용하지 않음 |
| `08_배당_현금흐름.png` | `/dividends` | 실제/예상/전환 능력 구분 안내 | 지급·세금·환율 자료 미연결, 임의 배당값 없음 |
| `09_자동_보고서.png` | `/reports`, `/automation` | 보고서 이력/본문, 15/16시, job 상태 | 기존 7절 engine 연결. 실제 정시 발화는 별도 검증 필요 |
| `10_KIS_API_연동_설정.png` | `/providers/kis` | paper/live 구분, Secret Service 상태·등록/교체/삭제 경계 | 실제 key connection은 NOT RUN; 계좌·주문 endpoint 없음 |
| `11_리밸런싱_일정_관리.png` | `/strategies` 일부 | 초안·저장 목표·실제, history와 전환 자료 요구 상태 | 실제 보유 귀속이 없어 진행률·예상일·다음 변경일은 계산 불가 |
| `12_반응형_화면_가이드.png` | 전체 | desktop, 1000×680, 390×844, zoom-equivalent 재배치 | Chrome viewport PASS; Windows DPI·모바일 실기기 NOT RUN |

## 현행 시각·정책 차이

- 승인 web token은 background `#F8FAFC`, card `#FFFFFF`, primary `#2563EB`, border
  `#E5E7EB`이다. 기존 Tk native theme와 같은 색을 강제하지 않는다.
- 좁은 화면에서는 sidebar를 menu button으로 여는 overlay로 전환하고 카드·요약을 한 열로
  배치한다. 전체 문서는 세로 scroll한다.
- 확대 배율 대응은 CDP device metric으로 1000×680의 125/150/200% equivalent를 검사했다.
  이 결과는 실제 GNOME/Windows OS 배율 검증이 아니다.
- SAMPLE fixture는 상단 자료 상태와 화면 banner에 계속 표시된다. 초기자산 4,000,000원
  선언값과 검증된 상세 원장 합계는 서로 자동 합산하지 않는다.
- 목표 전략은 전체 투자자산을 분모로 하는 사용자 목표이며 후보 평가 점수 가중치나
  코어/위성 위험 배분이 아니다. 실제 귀속 자료가 없으면 `UNAVAILABLE`을 표시한다.
- 보고서의 Markdown/HTML을 실행하지 않고 제한된 안전 renderer로 7개 절을 표시한다.
- 좌측 하단과 상단 rail에 `LOCALHOST_ONLY`, SQLite, 주문 기능 없음, KIS 설정 필요가
  표시된다. cloud·PWA·Web Push를 현재 기능처럼 표시하지 않는다.

## 2026-10-11 localhost 캡처 결과

SAMPLE fixture 전용 임시 DB와 fixture 관리자만 사용해 FastAPI가 built React를
`127.0.0.1:8000`에서 같은 origin으로 제공했다. Google Chrome `151.0.7922.137`의 실제
DISPLAY에서 `tools/web_browser_smoke.mjs --visible`을 실행해 로그인, SAMPLE 생성,
대시보드, 전략 100% 불변 저장, 전략 상세, portfolio, 설정, 7절 보고서, 반응형·확대 화면을
통과했다. 이 테스트는 실제 계좌·키·개인자료·공급자 network·주문을 사용하지 않았다.

- visible 캡처: `/dev/shm/wealthy-local-smoke.6_3pl1jg/visible/`
- visible PNG: 18개, 총 2,218,576 bytes
- visible manifest: `/dev/shm/wealthy-local-smoke.6_3pl1jg/VISIBLE_SHA256SUMS`
- visible manifest SHA-256:
  `65cf000a03c32a0563dd9c763ea47e675f70dd5b93135649ce2f3ce67a02ef5e`
- headless 캡처: `/dev/shm/wealthy-local-smoke.6_3pl1jg/headless/`
- headless PNG: 18개, 총 2,127,778 bytes
- headless manifest SHA-256:
  `21eb3085bb32778c8eddd3ba3a5c9e6355084f34433b97e1a86b0f0c30888786`

visible과 headless 결과는 별도 manifest로 보존했다. 저장소와 release ZIP에는 캡처,
fixture password, 임시 DB를 넣지 않는다. 캡처 폴더는 `/dev/shm`의 임시 산출물이므로
재부팅 뒤 유지된다고 보장하지 않는다.

| 확인 대상 | 캡처 파일 | 결과와 한계 |
|---|---|---|
| desktop dashboard | `dashboard-1920x1080.png`, `dashboard-1600x900.png` | SAMPLE·기준시각·경고·공급자·주문 비활성 표시 PASS |
| 작은 desktop | `dashboard-1366x768.png`, `dashboard-1280x720.png`, `dashboard-1024x768.png`, `dashboard-1000x680.png` | sidebar/content/세로 scroll PASS |
| 확대 equivalent | `dashboard-1000x680-zoom-125.png`, `-zoom-150.png`, `-zoom-200.png` | 본문 수평 overflow 차단과 menu 접근 PASS; 실제 OS 배율 NOT RUN |
| portfolio | `portfolio-1600x900.png` | 합성 SAMPLE 원장·월별 미입력·별도 위험 축 표시 PASS |
| 전략 혼합 | `strategy-mix-1600x900.png`, `strategy-mix-390x844.png` | 8종·100%·초안/저장/실제·정책 안내 PASS |
| 전략 상세 | `strategy-detail-1600x900.png` | 선정기준·장점/위험·필요자료 결측 표시 PASS |
| 보고서 | `reports-desktop.png`, `reports-mobile.png` | 이력과 7절 안전 renderer·scroll PASS |
| 설정 | `settings-1600x900.png` | local/Secret Service/주문 없음 경계 PASS; 실제 key 연결 NOT RUN |
| dark mode | `settings-dark-1600x900.png` | theme 전환과 dark token 표시 PASS |
| Windows 125/150/200% | 없음 | NOT RUN |
| Android/iPhone 실기기 | 없음 | 현행 범위 제외·NOT RUN |
| PWA 설치/Web Push | 없음 | 현행 비활성·NOT RUN |

## 시안 대비 남은 사항

- 월별 실자료 history, 계좌·ISA, 세금, 배당, 종목 검색·분석은 화면 경계와 재개 조건이
  있으나 운영 자료/확정 정책이 없어 PARTIAL 또는 미구현이다.
- 실제 보유자산의 전략 귀속과 점진적 전환 계획은 중복 합산 없는 원장이 준비될 때까지
  계산 불가다. 목표를 실제 비중으로 복사하지 않는다.
- 실제 KIS/OpenDART/ECOS/SEC 인증 연결과 실제 공급자 기준시각은 이번 디자인 fixture 검증
  범위 밖이다.
- `systemd --user` 실제 설치, 15:00/16:00 정시 발화, 절전 복귀는 UI 표시와 static/fake-clock
  검증과 구분해 NOT RUN으로 유지한다.
- 독립 Claude 디자인·기능 검증은 **PENDING**이다. Codex 캡처 PASS를 독립 승인으로 쓰지 않는다.
