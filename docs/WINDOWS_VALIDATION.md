# Windows 10/11 x64 이전·검증 절차 — 비활성 legacy

> **현재 개발·검증 범위가 아닙니다.** 현행 운영은 Ubuntu 24.04 localhost 웹입니다.
> Windows 전용 구현·패키징은 중단되었고 실제 Windows 결과는 계속 `NOT RUN`입니다. 아래
> 절차는 과거 이식성 기록 보존용이며 현행 완료 조건이 아닙니다.

상태: **NOT RUN** — 이 문서는 실행 계획이며 Windows 통과 기록이 아닙니다.

## 웹 앱 Windows Docker 검증

웹 앱의 1차 Windows 경로는 Windows 10/11 x64 + Docker Desktop(WSL2 backend) +
Chrome/Edge이다. 실제 Windows에서 아직 실행하지 않았으며 Ubuntu image, `.venv`,
`node_modules`, secret이나 DB volume을 복사하지 않는다. 저장소를 새로 받은 뒤 PowerShell에서
다음처럼 준비한다.

```powershell
cd "C:\사용자\작업 폴더\program"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[web]"
.\.venv\Scripts\python.exe -B -m tools.web_secret_setup --directory deploy\secrets
docker compose -f deploy\compose.yaml config --quiet
docker compose -f deploy\compose.yaml build
docker compose -f deploy\compose.yaml up -d postgres backend
docker compose -f deploy\compose.yaml run --rm backend wealthy-admin create-admin
docker compose -f deploy\compose.yaml up -d
docker compose -f deploy\compose.yaml ps
```

관리자 이름·비밀번호는 TTY prompt에만 입력하고 PowerShell history, `.env`, source, 로그에
넣지 않는다. 브라우저에서 `http://127.0.0.1:8080`을 열어 다음을 확인한다.

1. Chrome/Edge에서 로그인, SAMPLE import, 실제 엔진의 4,000,000원 투자자산과 7절 보고서
2. 다른 browser profile/session의 자료 격리, logout, 비밀번호 변경 뒤 session 폐기
3. 목표 전략 99.9/100.1 차단, 100 저장, 새 version, route 이동 뒤 미저장 초안, 복원
4. dashboard/보고서/market/risk/automation은 실제 API 응답이고, 미구현 화면은 조건만 표시
5. 16개 menu, light/dark 복원, keyboard focus, 100%/125%/150%/200% 표시 배율과 scroll
6. 개발자 도구에서 `/api` 응답이 Service Worker Cache Storage에 없고 private response가
   `no-store`인지 확인
7. KIS 비밀 등록 화면은 원문을 다시 반환하지 않고 계좌 조회·주문 기능이 없는지 확인
8. 컨테이너 재시작 뒤 DB volume 자료 보존과 worker job 상태. `down -v`는 실행하지 않음

Windows local HTTP는 loopback 개발 전용이다. LAN bind, 실제 DNS/HTTPS와 Web Push 수신은
이 시험 범위 밖이다. 실제 Windows 결과와 Docker Desktop/브라우저 version을 새 검증 로그에
기록한다.

```powershell
docker compose -f deploy\compose.yaml restart
docker compose -f deploy\compose.yaml ps
docker compose -f deploy\compose.yaml stop
```

공개 운영은 `docs/DEPLOYMENT.md`의 production override를 실제 DNS·인증서가 준비된 서버에서
별도로 검증한다. Windows Docker 로컬 PASS를 공개 HTTPS, Android 또는 iPhone PASS로
확대해 기록하지 않는다.

## 전제

- 저장소 루트(`README.md`와 `pyproject.toml`이 있는 폴더)에서 실행합니다.
- Python 3.12 x64와 `py` 런처 설치를 권장합니다. 프로젝트 명세는 Python 3.11 이상을
  허용하지만 현재 Ubuntu 검증은 3.12.3에서만 수행했습니다.
- Ubuntu의 `.venv`, 캐시, 바이너리, 개인자료를 Windows로 복사하지 않습니다.
- 관리자 권한, WSL, Docker, PowerShell 실행정책 완화를 요구하지 않습니다.

## PowerShell 재현

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m tkinter
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q asset_assistant tests tools
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m asset_assistant.gui
.\.venv\Scripts\python.exe -B -m asset_assistant.automation status
.\.venv\Scripts\python.exe -B -m asset_assistant.scheduler windows-spec --output-dir ".\windows 작업 명세"
.\.venv\Scripts\python.exe -B -m asset_assistant.scheduler status
$UnicodeDir = ".\windows 검증\한글 공백 폴더"
New-Item -ItemType Directory -Force $UnicodeDir | Out-Null
.\.venv\Scripts\python.exe -m asset_assistant --input .\examples\offline_sample.json --output (Join-Path $UnicodeDir "샘플 보고서.md")

$LongDir = $UnicodeDir
1..12 | ForEach-Object { $LongDir = Join-Path $LongDir ("long-path-segment-{0:D2}-한글" -f $_) }
New-Item -ItemType Directory -Force $LongDir | Out-Null
$LongOutput = Join-Path $LongDir "장경로 보고서.md"
$AbsoluteLongOutput = [System.IO.Path]::GetFullPath($LongOutput)
$AbsoluteLongOutput.Length
.\.venv\Scripts\python.exe -m asset_assistant --input .\examples\offline_sample.json --output $LongOutput
```

첫 명령은 Tk 창이 열리는지 확인합니다. 실패하면 `pip install tkinter`를 실행하지 말고 공식
Python 설치 관리자의 Tcl/Tk 구성요소를 확인합니다. GUI 창에서는 아래를 독립 확인합니다.

1. 샘플 불러오기 → 투자자산 4,000,000원과 샘플 배너 확인 → 7개 절 보고서 생성 → 저장
2. 잘못된 JSON 선택 시 기존 결과 제거와 일반 오류문 확인 → 정상 샘플로 다시 복구
3. 열기/저장 취소 시 기존 유효 결과 유지
4. 입력 JSON 자체와 그 하드링크/동일 파일에 저장 시도 차단 및 원본 바이트 보존
5. 한글·공백 폴더의 UTF-8 Markdown 저장과 재열람
6. 창 크기 변경, 대시보드 경고 및 보고서 가로/세로 스크롤
7. 별칭·종목명·제목·가정의 계좌번호 형태가 화면·오류·저장 보고서에 노출되지 않음
8. `SAMPLE`, `MANUAL_PAPER_SNAPSHOT`, `MANUAL_ACTUAL` 표시 분리와 결측값의 미입력 표시
9. 공급자 설정의 보호 저장소 상태, 등록·교체·삭제 시 비밀값 비노출, BLS/Fed 갱신 뒤 기준·수집시각
10. 자동 시장자료 보고서 7개 절, 원장과 미합산, 종가·뉴스 미연결 항목의 확인 필요 표시
11. 공급자 대상/활성·KIS paper/live 분리·전략 혼합 100% 저장과 잘못된 합계 거부
12. GUI 실행 중 새 정기 보고서 자동 감지(첫 확인 약 10초, 이후 약 30초), 수동 원장
    열람 중에는 화면 유지·알림 표시

두 번째 실행은 실제 한글·공백 **폴더**를 검증합니다. 세 번째 실행은 절대경로 길이도 함께
기록합니다. 장경로 생성이 운영체제 정책 때문에 실패하면 설정을 임의로 완화하지 말고
`BLOCKED`와 실제 경로 길이·오류 종류만 기록합니다.

확인 항목:

- 현재 작업본의 전체 테스트 PASS와 실행 시점의 정확한 개수
- 한글·공백 폴더의 UTF-8 입력/출력
- 장경로의 생성·보고서 저장 또는 운영체제 정책에 따른 `BLOCKED` 근거
- 입력과 출력 동일 경로 차단 및 입력 보존
- `SAMPLE`, `MANUAL_PAPER_SNAPSHOT`, `MANUAL_ACTUAL` 분리
- Windows 줄바꿈에서도 보고서 7개 절 순서 유지
- 기본 BLS/Fed 실제 읽기와 KIS/SEC/OpenDART/ECOS 미설정 상태를 구분. 실제 개인 키 없이
  인증 공급자 실연결 `PASS`로 쓰지 않음
- OS 보호 저장소가 없으면 평문 fallback 없이 등록 실패. KIS 모의/실전 비밀 항목 분리
- 계좌 조회·주문 API가 존재하지 않고 전략 혼합이 정책 비중을 변경하지 않음
- KIS 현재가를 종가·일봉으로 오인하지 않고, 추세·변동성은 검증된 이력이 없어 평가 불가로 표시
- 전략 간 중복 노출 함수는 단위 테스트 대상일 뿐 GUI·자동 보고서와 미연결임을 확인
- 월별 요약과 예약 XML 생성은 구현되었으나 Task Scheduler 실제 등록은 별도 Windows 검증
- 사용자 프로필 밖 관리자 경로나 ROS/WSL에 의존하지 않음

## 사용자 자료와 OS 보호 저장소

현재 자동 수집 DB와 생성 보고서는 `%LOCALAPPDATA%\LongTermAssetAssistant` 아래에
저장합니다. 수동 입력 `user_data/`는 저장소 밖에 두거나 Git 제외 폴더에서 관리합니다.
공급자 비밀은 `keyring`의 허용된 Windows backend에만 저장하고 DB·JSON·`.env`·보고서에
넣지 않습니다. 실제 Windows Credential Manager backend의 저장·교체·재로그인·잠금·삭제는
Windows에서 검증해야 합니다. 앱 DB에는 `credential_ref`나 비밀을 저장하지 않으며 keyring의
비민감 고정 service/항목명만 사용합니다. 자동 자료 DB에는 공급자별 기준시각·수집시각과
관측값이 포함되므로 사용자별
백업·삭제 정책은 별도로 결정해야 합니다.

## Windows 예약 XML 검토와 등록

위 `windows-spec` 명령은 두 XML 파일만 만들며 **등록하지 않습니다**. Windows 시간대를
`Asia/Seoul`로 설정했는지, XML의 15:00 수집과 16:00 보고서 경로·작업 폴더가 현재 Windows
폴더를 가리키는지 검토합니다. 테스트용 자료와 OS 보안 저장소가 준비된 뒤 PowerShell에서
사용자 권한으로 다음 명령을 직접 실행합니다. 작업 스케줄러의 `StartWhenAvailable`과
`IgnoreNew`, 앱 SQLite 날짜별 claim을 확인하세요.

```powershell
schtasks.exe /Create /TN LongTermAssetAssistant-Collect /XML ".\windows 작업 명세\asset-assistant-collect.xml" /F
schtasks.exe /Create /TN LongTermAssetAssistant-Report /XML ".\windows 작업 명세\asset-assistant-report.xml" /F
schtasks.exe /Query /TN LongTermAssetAssistant-Collect
schtasks.exe /Query /TN LongTermAssetAssistant-Report
schtasks.exe /Delete /TN LongTermAssetAssistant-Collect /F
schtasks.exe /Delete /TN LongTermAssetAssistant-Report /F
```

위 등록·실행·절전 복귀는 아직 **NOT RUN**입니다. PC 전원이 꺼진 동안 실행되지 않습니다.
Windows 작업 스케줄러가 늦은 작업을 재시작 때 어떻게 처리하는지 실제 기록으로 확인하세요.
현재 GUI와 `scheduler status`는 Windows에서 고정된 두 작업을 `schtasks.exe /Query`로
읽기 전용 질의해 등록 여부를 표시하도록 구현되었습니다. 실제 Windows 동작은 **NOT RUN**
이므로 위 수동 `schtasks.exe /Query` 결과와 대조하세요. 표시 언어에 따라 달라지는 상세
출력을 파싱하지 않아 작업 활성화·현재 실행 상태는 여전히 별도로 확인해야 합니다.

앱의 slot claim과 수집 lease는 같은 SQLite DB를 쓰는 프로세스에만 적용됩니다. 다른 DB나
동일 자격을 쓰는 외부 앱과 호출예산을 조정하지 않습니다. 놓친 작업을 재개해도 여러 날의
공시·RSS·고정기간 통계를 완전 backfill하지 않습니다. GUI의 `STALE`은 Asia/Seoul 기준 오늘
수집하지 않았다는 뜻일 뿐 공급자 발표주기까지 반영한 경제적 최신성 판정이 아닙니다.

## 실행 파일

Windows용 `.exe`는 Windows에서 패키징 도구·서명·백신 오탐·한글 경로·업데이트/복구를
검증한 뒤에만 배포 상태로 기록합니다. Ubuntu에서 만든 실행 파일을 Windows 산출물로
간주하지 않습니다.

## 소스 ZIP 무결성 범위

`asset_assistant.release` validator는 허용목록·멤버 유형·암호화 플래그·CRC·읽기
가능 여부를 확인하는 **구조 검사**입니다. 정상 CRC로 다시 패키징된 내용 변조와
배포자 진본성은 보증하지 않습니다. 현재 ZIP 내부 manifest·서명은 미구현입니다.

PowerShell의 `Get-FileHash -Algorithm SHA256 <ZIP>` 결과는 별도의 신뢰할 수 있는
채널로 제공된 **외부 SHA-256** 기준값과 비교할 때만 변조 확인 근거가 됩니다. 향후
배포자 **서명**을 도입하기 전까지는 내장 validator 통과를 출처 인증으로 간주하지 마세요.
