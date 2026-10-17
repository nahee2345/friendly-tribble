# Wealthy — Stage 1 local web

Ubuntu 호스트에서 실행하는 웹 기반 가상투자 도구입니다. 실제 증권계좌나 자동 실거래 주문과 연결되지 않습니다.

## 설치 / 증분 업데이트

```bash
cd /home/ww/wealthy-github
git pull --ff-only
bash start_wealthy.sh
```

첫 실행에는 가상환경 및 프런트엔드 패키지 설치가 필요합니다. 스크립트가 설치를 시도합니다. 프런트엔드 빌드는 소스 변경을 반영하도록 실행 때마다 진행합니다.

브라우저 주소: **http://localhost:8000/stage1**

이전 사용자 데이터 경로는 변하지 않습니다.

- 로그인과 관리자 계정: `/home/ww/wealthy-stage1-data/state/web-state.sqlite3`
- 가상 거래 원장: `/home/ww/wealthy-stage1-data/state/stage1-paper.sqlite3`
- 시장자료: `/home/ww/wealthy-stage1-data/market.sqlite3`

**최초 관리자만 1회 생성**합니다. 이미 계정이 하나 존재하면 시작 스크립트에서 재생성하지 않습니다. DB와 비밀번호, API 키는 절대 GitHub에 올리지 않습니다.

## 구현 범위

- 기본 계좌 1개, 추가 계좌 최대 10개, 계좌 간 가상 자금 이동
- 초기 가상자본 400만 원, 매월 25일 가상 입금 35만 원, 재투자 설정
- 검증된 미국 주식·ETF 지연 종가에 대한 수동 가상 매수·매도
- 미국 90%·한국 10%, ETF 80%·개별주 20% 기본 선호와 사용자 변경
- 공개 미국·한국 뉴스 RSS 조회, 미국 기사 제목의 외부 번역 시도 (실패 시 원문)
- 가상 원화→달러 환전 기록
- **ETF 가상 모의투자 1회 실행**: 사용자 버튼을 누르면 하루 최대 1회, 최초 자본의 5% 이내에서 서버가 검증한 미국 ETF 지연 종가 및 참고환율을 사용해 가상 환전·매수합니다. 시세가 없으면 거래를 건너뜁니다.

## 아직 완료되지 않은 범위

- 24시간/예약 무인 자동매매와 정량적 종목 선정·자동 매도
- 뉴스 감성 분석과 출처별 정확도 검증, 번역의 품질보장
- 국내 주식/국내 ETF의 검증된 가격 기반 모의체결
- ISA 계좌별 정교한 세후 수익률 계산과 수수료 정산
- 종합 포트폴리오 평가 및 실현/미실현 손익 보고서

이것들은 1단계 완료 판정 전 반드시 구현 및 검증해야 합니다. 새 기능을 실제 완성으로 오인하지 마십시오.

## 테스트

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -q tests/test_stage1_autorun.py tests/test_stage1_paper.py tests/test_stage1_portfolios.py tests/test_stage1_discovery.py
```

새 기능은 실제 브라우저/네트워크에 대한 수락시험을 통과하기 전까지 완성 처리하지 않습니다.
