# PWA와 Web Push 운영 범위 — 비활성 legacy

> **현재 기능 범위가 아닙니다.** 2026-10-11 P0 범위 정정으로 PWA와 외부 Web Push를
> 비활성화했습니다. 현행 알림은 웹 내부 알림함, 페이지가 열린 동안의 Chrome Notification,
> 가능한 Ubuntu 사용자 세션 알림입니다. 아래 내용은 과거 구현·검증 재현용이며 설치 또는
> 운영 절차가 아닙니다.

확인 기준일: 2026-10-11.

## 현재 구현 경계

React 앱에는 web app manifest와 service worker 등록 경로가 있습니다.
service worker는 versioned 정적 app shell만 cache하고 `/api/`, 로그인,
보고서, 금융자료 응답은 cache하지 않습니다. 로그아웃 때 앱이 소유한 정적
cache를 삭제합니다.

알림 권한은 페이지 로드 시 자동으로 요청하지 않습니다. 사용자가 알림 설정
버튼을 누른 경우에만 브라우저 권한을 요청합니다. 구독 endpoint와 공개키는
민감 데이터로 서버에 소유자와 함께 저장하며 UI와 로그에 원문을 반환하지
않습니다. 알림 payload 기본값에는 잔액, 계좌번호, 투자금액, 종목 수량을
넣지 않고 “보고서 준비 완료” 또는 “자료 수집 확인 필요”와 안전한 내부
경로만 사용합니다.

현재 단계의 VAPID 발송 서버 및 실제 기기 수신은 **미구현/NOT RUN**입니다.
구독 UI가 존재하는 것만으로 알림 성공이라고 표시하지 않습니다. 서버가
꺼져 있거나 무료 VM이 회수되면 push 발송도 수행되지 않습니다.

## 기기별 조건

- Ubuntu/Windows Chrome·Edge: 운영 HTTPS와 유효한 manifest/service worker,
  사용자 권한이 필요합니다. 브라우저별 설치 메뉴와 정책이 다릅니다.
- Android Chrome: 설치 가능한 manifest와 HTTPS가 필요하며 실제 기기 시험은
  아직 `NOT RUN`입니다.
- iPhone/iPad: Apple 공식 문서상 iOS/iPadOS 16.4 이상에서 **홈 화면에
  추가된 web app**이 Web Push를 지원합니다. `display: standalone` manifest와
  사용자 제스처에서의 알림 권한 요청이 필요합니다. Safari tab만 시험한
  결과를 홈 화면 앱 push 성공으로 간주하지 않습니다.

Apple은 iOS Home Screen web app이 브라우저와 별도 cookie/storage를 가질
수 있다고 설명합니다. 따라서 Safari 로그인과 설치 앱 로그인을 동일 세션으로
가정하지 않고 각각 확인합니다.

## 사용자 검증 절차(운영 HTTPS 준비 뒤)

1. 브라우저에서 HTTPS 주소를 열고 인증서 오류가 없는지 확인합니다.
2. 로그인하고 manifest/아이콘/독립 창 설치를 확인합니다.
3. 앱의 명시적 “알림 등록”을 누른 뒤에만 OS 권한 창이 나오는지 확인합니다.
4. 다른 사용자 세션에서 구독 상태나 endpoint를 볼 수 없는지 확인합니다.
5. fixture 이벤트 1개에 알림이 한 번만 오고 숫자·계좌·잔액이 잠금 화면에
   없는지 확인합니다.
6. 알림을 누르면 같은 origin의 보고서로 이동하며 만료 세션이면 로그인 후
   안전한 내부 경로로만 복귀하는지 확인합니다.
7. 구독 해제, 만료 endpoint 제거, 로그아웃 뒤 private cache 부재를 확인합니다.

## 공식 근거

- Apple Web Push: <https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers>
- Apple Home Screen web apps: <https://developer.apple.com/videos/play/wwdc2023/10120/>
- PWA 설치 개요: <https://web.dev/learn/pwa/installation>
- Push API 보안·구독 개요: <https://developer.mozilla.org/en-US/docs/Web/API/Push_API>
