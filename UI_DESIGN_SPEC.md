# Wealthy 전체 UI 구현 명세

확인 기준일: 2026-10-11

이 문서는 승인 시안 출처와, 웹 검증이 끝날 때까지 유지하는 Tk 데스크톱 전략 화면을
설명한다. React 현행 구현 규격은 `WEB_DESIGN_SPEC.md`가 우선한다. 투자·제품 정책은
`PROJECT_RULES.md`가 단일 원본이며 이 문서는 정책을 변경하지 않는다. 디자인의 예시
종목·금액·수익률은 실제 사용자 자료가 아니다.

## 웹 디자인 기준과 원본

- 원본은 `design/references/`에 관리하며 전체 archive SHA-256은
  `48f1d2a5241654d579521e254600fbfaf44931cf4c379d152dc9f5ccbb291a08`이다.
- 13개 PNG와 원본 가이드의 개별 hash는 `design/references/SHA256SUMS`가 단일 목록이다.
  `00_전체_화면_구성도_시안.png`에는 OpenAI Media Service/ChatGPT C2PA metadata가 있고,
  모두 mock 자료인 시각 참고물이다.
- 실제 구현 화면은 `web/frontend/src/App.tsx`, `pages.tsx`, `styles.css`를 기준으로 한다.
  시안과 구현 차이는 `docs/DESIGN_COMPARISON.md`에 기능 상태와 함께 기록한다.
- `design/approved/`는 승인된 파생 자산, `design/screenshots/`는 SAMPLE 전용 구현 캡처,
  `design/comparison/`은 비교 산출물 위치이다. 실제 금융자료·계좌·인증정보가 담긴 캡처는
  저장소와 release에 넣지 않는다.

## 웹 공통 셸과 토큰

- 넓은 화면은 접을 수 있는 왼쪽 navigation, 상단 검색·사용자·테마, 중앙 content와 필요한
  화면의 오른쪽 summary를 쓴다. 좁은 화면은 menu button과 overlay sidebar, 1열 content,
  하단 summary로 재배치한다.
- light 현행: 배경 `#F7F8FA`, 카드 `#FFFFFF`, 제목 `#161B23`, 본문 `#374151`, 보조
  `#6C7480`, 테두리 `#E5E8ED`, blue `#2563EB`, 선택 `#EFF6FF`, 이익 `#0E9873`,
  손실 `#D84B54`, 경고 `#B45309`.
- dark: 배경 `#0B1220`, 카드 `#111827`, 보조 카드 `#1F2937`, 제목 `#F9FAFB`, 본문
  `#D1D5DB`, 보조 `#9CA3AF`, 테두리 `#374151`, blue `#3B82F6`.
- blue는 선택 메뉴, 주요 action, 활성 control, focus와 검증된 보유 종목 강조에만 쓴다.
  손익 색과 보유 상태는 텍스트·badge를 함께 사용해야 하며 색만으로 의미를 전달하지 않는다.
- theme은 금융자료가 아닌 표시 설정으로만 localStorage의 `wealthy-theme`에 저장한다.
  token·구조가 아닌 보고서/잔고/API 응답은 브라우저 저장소에 저장하지 않는다.

## 웹 화면 ID와 기능 상태

`화면 구현`은 route와 상태 안내가 있다는 뜻이며 업무 기능 완료와 다르다.

| UI ID | route | 연결된 기능 | 현재 제한 |
|---|---|---|---|
| `WEB-DASH-01` | `/dashboard` | 사용자 dashboard, SAMPLE/JSON upload, 경고·공급자·최근 보고서 | upload 뒤 기존 자산과 자동 병합 안 함 |
| `WEB-ASSET-01` | `/assets` | 최신 투자자산·자료 영역 | 월별 web history API/차트 미구현 |
| `WEB-ACCOUNT-01` | `/accounts` | 안전 경계·필요 조건 표시 | 계좌·ISA 조회/주문 미구현 |
| `WEB-STRATEGY-MIX-01` | `/strategies` | 목표 전략 8종 Decimal 편집, exact 100%, preview, append-only 저장·복원 | 후보 점수 가중치와 별도; 주문 아님 |
| `WEB-STRATEGY-LIB-01` | `/strategy-library` | 8개 전략 engine mapping/자료 상태 | 일부 전략 engine·자료 미연결 |
| `WEB-STRATEGY-DETAIL-01` | `/strategies/:strategyId` | 설명·기준·장점·위험·필요 자료 상태 | 자료 없는 적합성·종목·전망 미생성 |
| `WEB-SEARCH-01` | `/search` | 조건 표시 | 종목 master/search API 미구현 |
| `WEB-ANALYSIS-01` | `/analysis` | 필요한 cutoff·자료 안내 | 종목 분석 API 미구현 |
| `WEB-TAX-01` | `/tax` | 공식 규칙 필요 안내 | 세금 계산 미구현 |
| `WEB-DIVIDEND-01` | `/dividends` | 실제/예상 구분 안내 | 배당 자료 미연결 |
| `WEB-MARKET-01` | `/market` | 실제 provider status/as-of | 후보/추천 화면 아님 |
| `WEB-RISK-01` | `/risk` | 기존 engine 정책 경고 | 미입력을 위험 없음으로 판정하지 않음 |
| `WEB-PERFORMANCE-01` | `/performance` | 불변 추천 기록의 필요 조건 표시 | 추천/성과 기록 미구현 |
| `WEB-REPORT-01` | `/reports` | 보고서 목록·안전한 Markdown text rendering | export UI와 월간 전용 API 미구현 |
| `WEB-AUTOMATION-01` | `/automation` | 다음 15/16시와 job history | worker 상시 실행을 UI 존재로 보장하지 않음 |
| `WEB-KIS-01` | `/providers/kis` | paper/live 비밀 입력·교체·삭제, configured status | 실제 연결 검사·계좌·주문 미구현 |
| `WEB-SETTINGS-01` | `/settings` | light/dark, 비밀번호, 내부 알림·열린 페이지 Chrome 알림 | PWA·외부 Web Push 비활성 |

위 목록의 “8개 전략 상세”는 library 1개와 parameterized detail 8개를 묶은 제품 화면군이다.
원 지시의 16개 기능 항목은 왼쪽 메뉴 16개와 모두 연결되지만, 현재 `CapabilityPage`인 메뉴는
빈 화면이 아니라 미구현 이유와 재개 조건을 표시하는 fail-closed 화면이다.

## 웹 전략 상호작용

1. 웹 일반 전략 비중은 `TargetStrategyMix`의 전체 투자자산 목표다. 기존 desktop
   `StrategyMix` 후보 점수 가중치와 table/scope를 공유하지 않는다.
2. slider와 Decimal text input을 동기화하고 합계가 정확히 100%인 경우에만 server preview와
   save를 허용한다. 99.9/100.1, 음수, 개별 100 초과, 비유한 값은 서버도 독립 거부한다.
3. 완전한 투자자산 합계가 있을 때만 server가 전략별 목표 금액을 계산한다. 보호저축·비상금,
   결측 자산과 초기 400만 원 선언값을 임의로 합산하지 않는다.
4. 전략명을 누르면 상세 route로 이동한다. slider 조작은 상세 이동을 일으키지 않고,
   `StrategyDraftProvider`의 저장하지 않은 초안은 route 전환 뒤에도 유지한다.
5. 저장은 새 불변 version만 만든다. restore는 마지막 저장 version을 초안으로 복원할 뿐
   주문이나 자동 전환을 실행하지 않는다.
6. 목표 전략은 코어/위성, 자산군, 종목·ETF 집중, 보호저축 제외, 금지상품 정책보다
   우선하지 않는다. 중복 종목 귀속과 실제 위험 노출 자료가 없으면 실제 전략 비중을 임의
   산출하지 않는다.

## 웹 반응형·접근성·안전 표시

- 검증 viewport는 1920×1080, 1600×900, 1366×768, 1280×720, 1024×768 및 스마트폰
  폭이다. browser viewport 검사는 Windows DPI 125/150/200%나 실제 Android/iPhone 검증을
  대신하지 않는다.
- 1200px 아래에서는 오른쪽 summary가 본문 아래로 이동하고, 900px 아래에서 다열 card를
  1열로 바꾸며, 600px 이하에서는 sidebar를 overlay로 접는다. content를 폰트 축소만으로
  맞추지 않고 문서 scroll을 유지한다.
- skip link, landmark, visible focus, 입력 label, `aria-live`/`role=status`, 오류 텍스트와
  keyboard 조작을 유지한다. 전략 slider에는 같은 값을 편집할 숫자 input이 있다.
- 보고서 Markdown은 raw HTML로 실행하지 않는다. 사용자 입력은 기존 공통 마스킹 뒤
  표시하며 외부 공급자 문자열도 code/data로만 취급한다.
- SAMPLE은 화면 전체 자료 영역 badge로 식별하고 mock 종목을 실제 보유로 표시하지 않는다.
  결측 금액은 0원이 아니라 `미입력/—`, 공급자 미연결은 `확인 필요/미구현`으로 표시한다.
- 상세 visual regression의 실제 screenshot hash와 pixel 차이는 독립 브라우저 검증 뒤
  `docs/DESIGN_COMPARISON.md`와 `VALIDATION_LOG.md`에 추가한다. 문서 작성 시 Windows와
  mobile 실기기 screenshot은 현행 범위 밖이며 NOT RUN이다.

## localhost 웹 이전 상태

- 현행 실행은 Ubuntu Chrome의 `http://localhost:8000`, FastAPI `127.0.0.1`, built
  React same-origin이다. Docker/Caddy/PostgreSQL/PWA/public cloud/Windows/mobile은 비활성
  legacy이며 이 문서의 이전 표현보다 `WEB_DESIGN_SPEC.md`와 `DOCS_INDEX.md`가 우선한다.
- 웹은 목표 전략을 전체 투자자산 기준으로 표시한다. 후보 평가 가중치와 코어/위성·자산군
  위험 축은 별도다. 실제 자산 귀속이 없으면 목표를 실제로 복사하지 않는다.
- 승인 PNG의 mock 목표 금액·종목·세금·수익률·예정일은 실제 자료가 없으면 표시하지 않는다.
- 기존 Tk 화면은 후보 평가 가중치 편집기이며 현재 웹 목표 전략 화면과 저장소·의미를
  공유하지 않는다. 총괄팀 승인 전 삭제하지 않는다.

## 레거시 Tk 전략 화면 부록

다음 규격은 웹 목표 전략이 아니라 기존 desktop의 **후보 평가용 가중치** 화면이다. 웹과
의미·저장소를 혼합하지 않는다.

### 시안 출처와 범위

- 감사 당시 원본 묶음: `/home/ww/Pictures/Wealthy_UI_Implementation_Pack.zip`
- 현재 저장소 기준본: `design/references/`와 그 안의 `SHA256SUMS`
- 확인한 파일: `README_디자인_구현_가이드.md`, `02_투자전략_비중_설정.png`,
  `03_비중_오류와_저장_차단.png`, `04_전략명_클릭_가치투자_상세.png`
- 과거 구현 중 확인한 임시 위치: `/tmp/wealthy-ui-stage2.NJMWuD` (일회성 검토 후 삭제)
- 사용자 승인에 따라 원본 PNG는 현재 소스 저장소에서 관리한다. 실제 사용자 금융자료가
  있는 구현 캡처는 배포 ZIP에 포함하지 않는다.

### 화면과 UI ID

| UI ID | Tk 속성/영역 | 역할 |
|---|---|---|
| `STRATEGY-MIX-01` | `strategy_content_frame` | 8개 전략의 슬라이더·숫자 입력과 요약 |
| `STRATEGY-TOTAL-01` | `strategy_total_var`, `strategy_guidance_var` | 정확한 Decimal 합계와 부족·초과 안내 |
| `STRATEGY-SAVE-01` | `save_strategy_button` | 새 불변 버전 저장; 기존 버전 덮어쓰기 금지 |
| `STRATEGY-RESTORE-01` | `strategy_restore_button` | 마지막 저장 버전 비중으로만 복원 |
| `STRATEGY-DETAIL-01` | `strategy_detail_buttons`, `strategy_detail_text` | 설명·선정 기준·장점·주의점·필요 자료 상태 |
| `STRATEGY-ASSESS-01` | `strategy_assessment_text` | 최근 저장 후보 평가와 출처 레코드·시각 |
| `STRATEGY-SCROLL-01` | `strategy_canvas` | 최소 창에서도 전체 화면 세로 접근 |

메인 창의 `투자전략` 버튼과 공급자 설정의 `투자전략 화면 열기` 버튼이 같은 전용
`Toplevel`을 연다. 상단에는 항상 “후보 평가용 가중치이며 실제 자산배분·매수 금액이
아닙니다.”를 표시한다.

### 요소 규격과 현재 적용 색상

- 기본 창 `1180×780`, 최소 창 `1000×680`.
- 현재 Ubuntu 24.04 / Tk 8.6 `clam` 렌더링에서 전용 스타일을 지정하지 않은
  ttk 컨테이너·패널·전략 행은 테마 기본 배경 `#DCDAD5`를 사용한다. Tk 창의
  native 배경은 `#D9D9D9`다. 운영체제와 Tk 테마에 따라 기본값은 달라질 수 있으며
  Windows 색상은 아직 검증하지 않았다.
- 명시 적용값은 Canvas 배경 `#F7F8FA`, Text·슬라이더 본체 `#FFFFFF`,
  슬라이더 트랙 `#E5E8ED`, 강조 블루 `#2563EB`이다. 정책 안내 배경/글자는
  `#DBEAFE`/`#1E3A8A`, 정상 합계는 `#E7F7F1`/`#0E765D`, 검증 경고는
  `#FFF4E8`/`#B45309`로 설정되어 있다.
- 시안의 전체 배경 `#F7F8FA`와 카드 `#FFFFFF`는 목표 토큰이며 현재 전체 ttk
  컨테이너에는 적용되지 않았다. 기본 글자 `#161B23`과 보조 글자 `#6C7480`도
  해당 색상을 명시한 위젯에만 적용된다. C-10 캡처에서 보고된 `#F5E8DF`는
  경고 영역 경계의 렌더링 혼합색이며, 위젯의 설정 토큰은 `#FFF4E8`이다.
- 전략 행은 전략명/상세보기 버튼, 0~100 슬라이더, 숫자 입력, `%` 단위 순이다.
- 화면 폰트는 OS에서 사용 가능한 한국어 글꼴을 우선하며 창 축소를 폰트 축소로
  대신하지 않는다.

### 클릭·편집·유효성 규칙

1. 전략명·`상세보기`를 누르면 같은 창의 상세 패널이 해당 전략으로 바뀐다.
2. 슬라이더 콜백과 입력칸은 문자열을 `Decimal`로 해석한다. Python `float`와 `bool`,
   빈칸, 문자, 비유한 값, 음수, 100 초과는 저장 입력으로 허용하지 않는다.
   화면을 멈출 수 있는 비정상 장문·극단 지수 입력도 거부한다(최대 유효숫자 128자리,
   Decimal 지수 절댓값 100). 정상 입력은 반올림하지 않는다.
3. 8개 전략의 합계가 정확히 100%일 때만 저장할 수 있다. 95/99.99는 각각
   5/0.01% 추가, 105/100.01은 각각 5/0.01% 감소 안내를 표시한다.
4. 화면 검증 뒤에도 `StrategyMix`가 다시 검증한다. 저장은
   `save_strategy_version`의 추가 전용 기록을 사용하며 같은 이름으로 내용을 덮어쓰지 않는다.
5. 창을 닫았다 다시 열어도 저장하지 않은 편집 비중은 유지한다. 비동기 DB 읽기가 늦게
   끝나도 그 사이의 사용자 편집을 덮어쓰지 않는다.
6. `비중 복원`만 마지막 저장 버전의 비중을 다시 적용한다. 복원 뒤에는 새 버전 이름이
   필요하다.
7. 버전명·후보 식별자·저장 근거 등 사용자 설정 유래 출력은 공통 계좌번호형 문자열
   마스킹과 길이 제한을 적용한다. 구조화된 기준·수집 시각은 시각 전용 검증 후 표시한다.

### 자료 연결 상태

상세 패널은 `AutomationService.strategy_workspace_snapshot()`의 로컬 읽기 결과만 표시하며
화면 열기로 네트워크 수집이나 후보 재평가를 시작하지 않는다.

- 상태 어휘는 `연결됨`, `미연결`, `결측`만 사용한다.
- KIS의 같은 수집 묶음에 가격·거래량·거래대금이 실제 저장된 경우에만 최신 스냅샷을
  `연결됨`으로 표시한다.
- 현재 평가는 단일 시점 묶음만 사용하므로 저변동성의 최소 21시점, 모멘텀의 최소
  126시점 가격은 `미연결` 또는 `결측`이다.
- 광범위 지수 범위, 재무 품질, 업종 내 가치, 배당 성장, 테마/뉴스 위험,
  채권·현금성 점수 연결이 구현되지 않은 경우 그대로 `미연결`로 표시한다.
- 상품유형·금지상품 정책 확인은 실제 설정 대상의 확인 상태에 따라 `연결됨` 또는
  `결측`으로 표시한다.
- 후보 평가가 없거나 필수 자료가 부족하면 `평가 불가`로 표시하고 0점으로 바꾸지 않는다.
- 기존 스키마는 찬성·반대 이유를 분리 보존하지 않으므로 “근거·반대 근거(저장 당시
  통합)”로 정직하게 표시하며 임의로 재분류하지 않는다.

### 반응형·스크롤·접근성

- 콘텐츠 폭 1200px 이상은 전략 편집/요약 2열, 미만은 요약·상세를 편집 영역 아래로
  이동하는 1열이다.
- 전체 본문은 `Canvas`와 세로 스크롤바를 사용하며 `1000×680`에서 마지막 후보 평가
  패널까지 접근할 수 있다.
- 모든 값은 숫자 입력칸으로도 조작할 수 있어 포인터 정밀도에만 의존하지 않는다.
- 버튼은 한국어 동작명을 가지며 Tab 순서로 접근할 수 있다. 읽기 전용 본문은 선택·복사가
  가능하고 별도 세로 스크롤바를 제공한다.
- 합계 상태는 색만으로 전달하지 않고 숫자와 “추가/줄여야 합니다/저장할 수 있습니다”
  문구를 함께 표시한다.
- 실제 DISPLAY 검증은 최소 크기, 슬라이더·입력, 저장 차단/허용, 상세 열기, 세로 스크롤,
  한글 렌더링을 확인한다. Windows의 DPI·키보드 탐색은 실제 Windows에서 별도 검증한다.

### 시안과 의도적으로 다른 부분

- 02의 총자산×전략 비중 `목표 금액`은 구현하지 않았다. 전략 비중은 전체 자산배분과
  별도라는 `PROJECT_RULES.md` 18절을 따른다.
- 02의 `점진적 전환`과 `다음 변경 예정일`은 이행 일정이 미확정이므로 표시하지 않는다.
- 04의 단일 종목 25%·20%, 가상 종목 목록, 도넛형 예시 포트폴리오는 개별 종목 5% 등
  정책 한도와 “샘플을 실제처럼 표시하지 않음” 원칙 때문에 구현하지 않았다.
- 04의 한 번 클릭 단일전략 전환 버튼은 사용자 편집·정확한 합계 확인을 우회할 수 있어
  넣지 않았다.
- 전체 사이드바 재설계나 웹 프레임워크 도입 없이 기존 교차 플랫폼 Tk 앱에 전용 창을
  추가했다.
- 1년 예상수익률·목표가·확률·매수 수량은 생성하지 않는다.

시안의 전략명 8개와 현재 `StrategyKind` 8개의 문구 대응은 사용자 결정 대기다. 승인 전에는
현재 코드 이름인 광범위 지수·퀄리티·가치·배당성장·저변동성·추세·모멘텀·위성
테마·섹터·채권·현금성을 유지한다. 이 화면은 코어 90/위성 최대 10, 개별종목 5%,
ETF 40/명시 승인 50%, 보호저축 제외, 금지상품 규칙을 변경하지 않는다.
