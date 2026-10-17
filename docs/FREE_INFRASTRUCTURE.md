# 무료 인프라 비교 및 선택 기록 — 비활성 legacy

> **현재 운영 기준이 아닙니다.** 2026-10-11 P0 범위 정정으로 cloud, 서버·도메인 가입,
> 공개 HTTPS, 터널, reverse proxy 작업을 중단했습니다. 현행은 Ubuntu PC 한 대의
> `http://localhost:8000`이며 이 문서는 과거 조사 근거 보존용입니다. 아래 후보를 배포
> 권고나 승인으로 사용하지 마세요.

확인 기준일: 2026-10-11. 이 문서는 공개된 **공식 문서**를 검토한
결과입니다. 이번 작업에서 계정 생성, 카드 등록, 유료 전환, 인스턴스
생성, DNS 등록, 클라우드 배포는 수행하지 않았습니다.

## 결론

조건부 1순위 후보는 OCI Always Free A1 VM 한 대에서 Caddy, React 정적
파일, FastAPI, worker, PostgreSQL을 같은 출처로 운영하고 DuckDNS를 DNS
보조로 사용하는 구조입니다. 상태는
`DOCUMENTATION_REVIEWED / ACCOUNT_NOT_CREATED / INSTANCE_NOT_PROVISIONED /
CAPACITY_NOT_VERIFIED / LIVE_TEST_NOT_RUN`입니다. 이는 무료 운영이나 서울
리전 자원 확보를 보장한다는 뜻이 아닙니다.

Oracle 공식 자료끼리 A1 및 Object Storage 한도 수치가 다릅니다. 장기
설계에는 보수적으로 A1 합계 2 OCPU/12GB, Object Storage 합계 20GB를
사용하고, 가입 뒤 home region의 실제 `Limits, Quotas and Usage`와
Always Free 라벨을 확인하기 전에는 더 큰 수치를 전제로 하지 않습니다.
idle 자원 회수와 계정 비활성 기준도 공식 페이지 간 차이가 있어 더
엄격한 30일 위험을 운영 문서에 적용합니다.

## 비교

| 후보 | 적합도 | 공식 무료 조건 요약 | 결제·과금 위험 | 실제 상태 |
|---|---|---|---|---|
| OCI A1 VM | 조건부 적합 | home region, ARM A1 시간/메모리 한도, block 200GB 범위 | 대부분 가입 시 카드와 소액 승인 필요. capacity·idle 회수·공인 IPv4 과금 여부는 tenancy에서 재확인 | 문서만 확인 |
| OCI E2 micro | 자원 부족 가능 | 최대 2대, 각 1/8 OCPU·1GB | 전체 스택에는 메모리가 빠듯 | 문서만 확인 |
| GCP e2-micro | 0원 목표에 부적합 | 미국 3개 리전, 월 1대 시간, 30GB standard disk | Billing 계정 필요, 공인 IPv4가 사실상 유료, 1GB RAM | 문서만 확인 |
| Cloudflare Pages | 정적 프론트 보조 | Free 정적 자산, 500 builds/month | Functions는 Workers 한도에 포함 | 문서만 확인 |
| Cloudflare Workers Free | worker 대체 부적합 | 100,000 requests/day, 10ms CPU/invocation, 5 cron triggers | 현 수집·분석 작업은 CPU 한도에 맞지 않음 | 문서만 확인 |
| DuckDNS | DNS 보조만 적합 | 무료 동적 DNS와 HTTPS update API | SLA 없음. token이 URL query에 들어가므로 로그 노출 위험 | 이름 미확보, 호출 안 함 |

Cloudflare Cron은 UTC만 사용하므로 KST 15:00/16:00은 각각 06:00/07:00
UTC입니다. 그러나 현 architecture는 지속 worker와 PostgreSQL이 필요해
Workers Free로 대체하지 않습니다. Google Cloud Scheduler는 계정당 월
3개 job의 무료 범위가 있지만 VM·IPv4·DB 총비용을 0원으로 만들지는
않습니다.

## 운영비 차단 규칙

- 목표: 확인 가능한 월 0원 범위. 알 수 없는 비용을 0원으로 표시하지 않습니다.
- 예상 월 21,000원: 사용자 경고와 선택 비용 기능 차단 검토.
- 예상 월 27,000원: 선택적 유료 기능 중단.
- 월 30,000원 초과 결제·증설: 프로그램과 운영 절차에서 차단하고 별도 사용자 승인이 없으면 진행하지 않습니다.
- 클라우드 budget alert는 청구 강제 상한이 아닙니다. 유료 전환을 하지 않고
  Always Free 라벨, compartment quota, 별도 비용 점검을 함께 사용합니다.

## 사용자만 수행할 수 있는 확인

1. 공식 사이트에서 본인 계정·약관·카드 인증을 직접 처리합니다. 비밀값은
   채팅이나 저장소에 입력하지 않습니다.
2. OCI home region은 생성 후 바꿀 수 없으므로 리전과 A1 실제 capacity를
   확인한 뒤 선택합니다. 서울/춘천의 free capacity는 계정 없이 확인되지
   않았습니다.
3. 생성 화면에서 각 자원의 Always Free 표기, 공인 IP SKU, boot/block
   storage 합계와 예상비용 0을 다시 확인합니다.
4. 무료 범위를 벗어나거나 유료 업그레이드를 요구하면 중단합니다.
5. DuckDNS token은 Caddy access log, shell history, process list에 남을 수
   있으므로 별도 secret file로만 전달합니다.

## 공식 근거

- OCI Free Tier: <https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier.htm>
- OCI Always Free resources: <https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm>
- OCI regions: <https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/managingregions.htm>
- OCI public IP management: <https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm>
- OCI 결제수단: <https://docs.oracle.com/en-us/iaas/Content/Billing/Tasks/changingpaymentmethod.htm>
- Oracle 한국 무료 클라우드 안내: <https://www.oracle.com/kr/cloud/free/>
- Google Cloud Free Program: <https://docs.cloud.google.com/free/docs/free-cloud-features>
- Google VPC 가격: <https://cloud.google.com/vpc/network-pricing>
- Google Compute machine types: <https://docs.cloud.google.com/compute/docs/general-purpose-machines>
- Cloud SQL PostgreSQL trial: <https://docs.cloud.google.com/sql/docs/postgres/free-trial-instance>
- Cloud Scheduler 가격/시간대: <https://cloud.google.com/scheduler/pricing>, <https://docs.cloud.google.com/scheduler/docs/configuring/cron-job-schedules>
- Cloudflare Pages limits: <https://developers.cloudflare.com/pages/platform/limits/>
- Cloudflare Workers 가격/한도: <https://developers.cloudflare.com/workers/platform/pricing/>, <https://developers.cloudflare.com/workers/platform/limits/>
- Cloudflare Cron: <https://developers.cloudflare.com/workers/configuration/cron-triggers/>
- DuckDNS 설명/API/약관: <https://www.duckdns.org/about.jsp>, <https://www.duckdns.org/spec.jsp>, <https://www.duckdns.org/tac.jsp>
