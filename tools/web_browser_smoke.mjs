#!/usr/bin/env node

/*
 * End-to-end loopback browser smoke test using Chrome DevTools Protocol.
 *
 * No browser automation dependency is required.  Start the backend and built
 * frontend/reverse proxy first, create a fixture-only user, and provide the
 * password through a mode-0600 file named by WEALTHY_SMOKE_PASSWORD_FILE.
 * The password is never placed in argv or printed.
 */

import { spawn } from "node:child_process";
import { mkdir, mkdtemp, readFile, realpath, rm, stat, writeFile } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

function fail(message) {
  throw new Error(message);
}

function parseArguments(argv) {
  const result = {
    baseUrl: "http://localhost:8000",
    chrome: process.env.CHROME_BIN || "/usr/bin/google-chrome",
    outputDir: process.env.WEALTHY_SMOKE_OUTPUT_DIR || "/dev/shm/wealthy-web-smoke",
    frontendDir: null,
    apiUrl: null,
    visible: false
  };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (key === "--help") {
      process.stdout.write(
        "사용법: WEALTHY_SMOKE_PASSWORD_FILE=/보안/파일 " +
        "WEALTHY_SMOKE_USERNAME=fixture-user node tools/web_browser_smoke.mjs " +
        "[--base-url http://localhost:8000] [--output-dir /dev/shm/wealthy-web-smoke] " +
        "[--chrome /usr/bin/google-chrome] [--visible] 또는 " +
        "[--frontend-dir web/frontend/dist --api-url http://127.0.0.1:8000]\n"
      );
      process.exit(0);
    }
    if (key === "--visible") {
      result.visible = true;
      continue;
    }
    if (!["--base-url", "--output-dir", "--chrome", "--frontend-dir", "--api-url"].includes(key) || index + 1 >= argv.length) {
      fail("지원하지 않는 실행 인자입니다.");
    }
    const value = argv[++index];
    if (key === "--base-url") result.baseUrl = value;
    if (key === "--output-dir") result.outputDir = value;
    if (key === "--chrome") result.chrome = value;
    if (key === "--frontend-dir") result.frontendDir = value;
    if (key === "--api-url") result.apiUrl = value;
  }
  return result;
}

function loopbackUrl(raw, label) {
  let value;
  try {
    value = new URL(raw);
  } catch {
    fail(`${label} 형식을 확인할 수 없습니다.`);
  }
  if (value.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(value.hostname)) {
    fail(`${label}는 HTTP loopback 주소만 사용할 수 있습니다.`);
  }
  return value;
}

async function startLocalGateway(frontendDirectory, apiAddress) {
  const root = await realpath(frontendDirectory);
  const rootInfo = await stat(root);
  if (!rootInfo.isDirectory()) fail("빌드된 프론트엔드 폴더를 확인할 수 없습니다.");
  await stat(path.join(root, "index.html"));
  const api = loopbackUrl(apiAddress, "로컬 API 주소");
  const mimeTypes = new Map([
    [".css", "text/css; charset=utf-8"],
    [".html", "text/html; charset=utf-8"],
    [".js", "text/javascript; charset=utf-8"],
    [".json", "application/json; charset=utf-8"],
    [".png", "image/png"],
    [".svg", "image/svg+xml"],
    [".webmanifest", "application/manifest+json; charset=utf-8"]
  ]);
  const server = http.createServer(async (request, response) => {
    try {
      const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
      if (requestUrl.pathname.startsWith("/api/")) {
        const upstream = http.request({
          protocol: api.protocol,
          hostname: api.hostname,
          port: api.port,
          method: request.method,
          path: `${requestUrl.pathname}${requestUrl.search}`,
          headers: { ...request.headers, host: request.headers.host }
        }, (upstreamResponse) => {
          response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
          upstreamResponse.pipe(response);
        });
        upstream.on("error", () => {
          if (!response.headersSent) response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
          response.end("local upstream unavailable");
        });
        request.pipe(upstream);
        return;
      }
      const relative = decodeURIComponent(requestUrl.pathname).replace(/^\/+/, "");
      const requested = path.resolve(root, relative || "index.html");
      const safePath = requested === root || requested.startsWith(`${root}${path.sep}`)
        ? requested : path.join(root, "index.html");
      let payload;
      let servedPath = safePath;
      try {
        payload = await readFile(safePath);
      } catch {
        servedPath = path.join(root, "index.html");
        payload = await readFile(servedPath);
      }
      response.writeHead(200, {
        "content-type": mimeTypes.get(path.extname(servedPath)) || "application/octet-stream",
        "cache-control": "no-store",
        "x-content-type-options": "nosniff"
      });
      response.end(payload);
    } catch {
      response.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
      response.end("invalid local request");
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") fail("로컬 검증 게이트웨이를 시작할 수 없습니다.");
  return { server, url: new URL(`http://127.0.0.1:${address.port}/`) };
}

async function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 0;
      server.close((error) => error ? reject(error) : resolve(port));
    });
  });
}

async function waitForJson(url, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
    } catch {
      // Chrome may not have opened the debugger socket yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  fail("Chrome 디버거가 제한 시간 안에 시작되지 않았습니다.");
}

class CdpClient {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.sequence = 0;
    this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", () => reject(new Error("브라우저 제어 연결 실패")), { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const payload = JSON.parse(String(event.data));
      if (!payload.id) return;
      const waiter = this.pending.get(payload.id);
      if (!waiter) return;
      this.pending.delete(payload.id);
      if (payload.error) waiter.reject(new Error("브라우저 명령 실패"));
      else waiter.resolve(payload.result || {});
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = ++this.sequence;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  close() {
    this.socket.close();
  }
}

async function evaluate(client, expression) {
  const result = await client.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true
  });
  if (result.exceptionDetails) fail("브라우저 화면 조작 중 안전한 검사가 실패했습니다.");
  return result.result?.value;
}

async function waitFor(client, expression, description, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await evaluate(client, expression)) return;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  fail(`${description} 상태를 제한 시간 안에 확인하지 못했습니다.`);
}

function buttonExpression(text) {
  return `(() => {
    const target = [...document.querySelectorAll('button')].find(
      (item) => item.textContent.trim() === ${JSON.stringify(text)}
    );
    if (!target) return false;
    target.click();
    return true;
  })()`;
}

async function screenshot(client, destination) {
  const result = await client.send("Page.captureScreenshot", {
    format: "png",
    fromSurface: true,
    captureBeyondViewport: false
  });
  await writeFile(destination, Buffer.from(result.data, "base64"), { mode: 0o600 });
}

async function setViewport(client, width, height, mobile = false) {
  await client.send("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile
  });
  await new Promise((resolve) => setTimeout(resolve, 150));
}

async function setDesktopZoomEquivalent(client, physicalWidth, physicalHeight, percent) {
  const scale = percent / 100;
  await client.send("Emulation.setDeviceMetricsOverride", {
    width: Math.floor(physicalWidth / scale),
    height: Math.floor(physicalHeight / scale),
    deviceScaleFactor: scale,
    mobile: false
  });
  await new Promise((resolve) => setTimeout(resolve, 200));
}

async function assertResponsiveLayout(client, label) {
  const state = await evaluate(client, `(() => ({
    viewportWidth: window.innerWidth,
    viewportHeight: window.innerHeight,
    documentWidth: document.documentElement.scrollWidth,
    mainWidth: document.querySelector('main')?.scrollWidth || 0,
    mainClientWidth: document.querySelector('main')?.clientWidth || 0,
    menuButtonVisible: (() => {
      const element = document.querySelector('.menu-button');
      if (!element) return false;
      const style = getComputedStyle(element);
      return style.display !== 'none' && style.visibility !== 'hidden';
    })()
  }))()`);
  if (!state || state.documentWidth > state.viewportWidth + 2) {
    fail(`${label}에서 페이지 수평 넘침이 발생했습니다.`);
  }
  if (state.mainWidth > state.mainClientWidth + 2) {
    fail(`${label}에서 본문 영역의 수평 넘침이 발생했습니다.`);
  }
  if (state.viewportWidth < 900 && !state.menuButtonVisible) {
    fail(`${label}에서 반응형 메뉴를 열 수 없습니다.`);
  }
}

async function navigateTo(client, baseUrl, route, expectedText) {
  await client.send("Page.navigate", { url: new URL(route, baseUrl).href });
  await waitFor(
    client,
    `document.readyState === 'complete' && document.body.innerText.includes(${JSON.stringify(expectedText)})`,
    `${expectedText} 화면`
  );
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  if (Boolean(options.frontendDir) !== Boolean(options.apiUrl)) {
    fail("--frontend-dir와 --api-url은 함께 지정해야 합니다.");
  }
  let gateway = null;
  let baseUrl = loopbackUrl(options.baseUrl, "로컬 앱 주소");
  if (options.frontendDir && options.apiUrl) {
    gateway = await startLocalGateway(options.frontendDir, options.apiUrl);
    baseUrl = gateway.url;
  }
  baseUrl.pathname = "/";
  baseUrl.search = "";
  baseUrl.hash = "";

  const username = process.env.WEALTHY_SMOKE_USERNAME || "fixture-admin";
  const passwordFile = process.env.WEALTHY_SMOKE_PASSWORD_FILE;
  if (!passwordFile) fail("WEALTHY_SMOKE_PASSWORD_FILE 설정이 필요합니다.");
  const passwordInfo = await stat(passwordFile);
  if (!passwordInfo.isFile() || (passwordInfo.mode & 0o077) !== 0) {
    fail("스모크 비밀번호 파일은 다른 사용자가 읽을 수 없는 일반 파일이어야 합니다.");
  }
  const password = (await readFile(passwordFile, "utf8")).trimEnd();
  if (!password) fail("스모크 비밀번호 파일이 비어 있습니다.");

  await mkdir(options.outputDir, { recursive: true, mode: 0o700 });
  const profile = await mkdtemp(path.join(os.tmpdir(), "wealthy-chrome-smoke-"));
  const debuggerPort = await freePort();
  const chromeArguments = [
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
    "--use-mock-keychain",
    `--remote-debugging-port=${debuggerPort}`,
    `--user-data-dir=${profile}`,
    "about:blank"
  ];
  if (!options.visible) chromeArguments.unshift("--headless=new");
  else chromeArguments.unshift("--window-size=1440,1000");
  const chrome = spawn(options.chrome, chromeArguments, {
    stdio: ["ignore", "ignore", "ignore"]
  });

  let client;
  try {
    await waitForJson(`http://127.0.0.1:${debuggerPort}/json/version`);
    const response = await fetch(
      `http://127.0.0.1:${debuggerPort}/json/new?${encodeURIComponent(baseUrl.href)}`,
      { method: "PUT" }
    );
    if (!response.ok) fail("브라우저 검사 탭을 열 수 없습니다.");
    const target = await response.json();
    client = new CdpClient(target.webSocketDebuggerUrl);
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await setViewport(client, 1440, 1000);
    await waitFor(
      client,
      `document.readyState === 'complete' && document.body.innerText.includes('Wealthy에 로그인')`,
      "로그인 화면"
    );

    const credentials = JSON.stringify({ username, password });
    const loginPrepared = await evaluate(client, `(() => {
      const credentials = ${credentials};
      const username = document.querySelector('input[autocomplete="username"]');
      const password = document.querySelector('input[autocomplete="current-password"]');
      const button = [...document.querySelectorAll('button')].find((item) => item.textContent.trim() === '로그인');
      if (!username || !password || !button) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(username, credentials.username);
      username.dispatchEvent(new Event('input', { bubbles: true }));
      setter.call(password, credentials.password);
      password.dispatchEvent(new Event('input', { bubbles: true }));
      button.click();
      return true;
    })()`);
    if (!loginPrepared) fail("로그인 입력 요소를 찾을 수 없습니다.");
    await waitFor(
      client,
      `document.body.innerText.includes('통합 대시보드') && !document.body.innerText.includes('Wealthy에 로그인')`,
      "인증된 대시보드"
    );
    await waitFor(
      client,
      `document.querySelector('.runtime-strip')?.innerText.includes('LOCALHOST_ONLY') === true &&
       document.querySelector('.runtime-strip')?.innerText.includes('SQLite') === true &&
       document.querySelector('.runtime-strip')?.innerText.includes('주문 비활성') === true`,
      "로컬 동일 출처 상태 표시"
    );
    await waitFor(
      client,
      `document.querySelector('link[rel="manifest"]') === null &&
       (!('serviceWorker' in navigator) || navigator.serviceWorker.getRegistrations().then((items) => items.length === 0))`,
      "PWA·Service Worker 비활성"
    );
    const notificationDrawerOpened = await evaluate(client, `(() => {
      const button = document.querySelector('button[aria-label^="알림함 열기"]');
      if (!button) return false;
      button.click();
      return true;
    })()`);
    if (!notificationDrawerOpened) fail("웹 내부 알림함을 열 수 없습니다.");
    await waitFor(
      client,
      `document.querySelector('#notification-drawer')?.innerText.includes('PAGE_OPEN_ONLY') === true &&
       document.querySelector('#notification-drawer')?.innerText.includes('페이지가 열려 있을 때만') === true`,
      "로컬 알림함 경계"
    );
    await evaluate(client, `document.querySelector('#notification-drawer button')?.click()`);

    if (!await evaluate(client, buttonExpression("샘플로 시작"))) {
      fail("샘플 시작 버튼을 찾을 수 없습니다.");
    }
    await waitFor(
      client,
      `document.body.innerText.includes('샘플 자료를 불러왔습니다') && document.body.innerText.includes('SAMPLE')`,
      "샘플 보고서 생성"
    );
    const dashboardPath = path.join(options.outputDir, "dashboard-desktop.png");
    await screenshot(client, dashboardPath);

    const screenshots = [dashboardPath];
    for (const [width, height] of [[1920, 1080], [1600, 900], [1366, 768], [1280, 720], [1024, 768], [1000, 680]]) {
      await setViewport(client, width, height);
      await assertResponsiveLayout(client, `${width}x${height}`);
      const destination = path.join(options.outputDir, `dashboard-${width}x${height}.png`);
      await screenshot(client, destination);
      screenshots.push(destination);
    }

    for (const zoom of [125, 150, 200]) {
      await setDesktopZoomEquivalent(client, 1000, 680, zoom);
      await assertResponsiveLayout(client, `1000x680 / ${zoom}% CDP 등가 확대`);
      const destination = path.join(options.outputDir, `dashboard-1000x680-zoom-${zoom}.png`);
      await screenshot(client, destination);
      screenshots.push(destination);
    }

    await setViewport(client, 1600, 900);
    await navigateTo(client, baseUrl, "/assets", "내 자산 · 월별 정리");
    await waitFor(
      client,
      `document.querySelector('.scope-banner')?.innerText.includes('SAMPLE') === true &&
       !document.body.innerText.includes('자료를 불러오는 중입니다')`,
      "포트폴리오 실제 응답·자료 영역"
    );
    const portfolioBoundaries = await evaluate(client, `(() => ({
      hasMonthlySection: document.body.innerText.includes('월별 자산 기록'),
      hasPositionsSection: document.body.innerText.includes('보유 원장'),
      hasMissingBoundary: document.body.innerText.includes('미입력') || document.body.innerText.includes('자료가 없습니다'),
      hasSampleScope: document.body.innerText.includes('SAMPLE')
    }))()`);
    if (!portfolioBoundaries?.hasMonthlySection || !portfolioBoundaries?.hasPositionsSection || !portfolioBoundaries?.hasSampleScope) {
      fail("포트폴리오 실제·결측 자료 경계를 확인할 수 없습니다.");
    }
    const portfolioPath = path.join(options.outputDir, "portfolio-1600x900.png");
    await screenshot(client, portfolioPath);
    screenshots.push(portfolioPath);

    await navigateTo(client, baseUrl, "/market", "미국 시장정보");
    await waitFor(
      client,
      `!document.body.innerText.includes('시장정보를 불러오는 중입니다') &&
       (document.body.innerText.includes('시장정보 목록') ||
        document.body.innerText.includes('실제 자료 없음') ||
        document.body.innerText.includes('공급자 미설정'))`,
      "미국 시장정보 목록 또는 명시적 빈 상태"
    );
    const providerPanelOpened = await evaluate(client, `(() => {
      const details = document.querySelector('.provider-status-details');
      if (!details) return false;
      details.open = true;
      return details.open && !details.innerText.includes('$');
    })()`);
    if (!providerPanelOpened) fail("공급자 상태 패널 표시 또는 '$' 비노출 검사가 실패했습니다.");
    await evaluate(client, `window.scrollTo(0, 0)`);
    const collectionSummaryPath = path.join(options.outputDir, "market-collection-summary-1600x900.png");
    await screenshot(client, collectionSummaryPath);
    screenshots.push(collectionSummaryPath);
    await evaluate(client, `(() => {
      const details = document.querySelector('.provider-status-details');
      if (!details) return false;
      window.scrollTo(0, Math.max(0, details.offsetTop - 145));
      return true;
    })()`);
    const providerStatusPath = path.join(options.outputDir, "market-provider-status-1600x900.png");
    await screenshot(client, providerStatusPath);
    screenshots.push(providerStatusPath);
    const marketState = await evaluate(client, `(async () => {
      const officialHosts = new Set([
        'sec.gov', 'www.sec.gov', 'data.sec.gov',
        'federalreserve.gov', 'www.federalreserve.gov',
        'bls.gov', 'www.bls.gov',
        'bea.gov', 'www.bea.gov', 'apps.bea.gov'
      ]);
      let providerStatus;
      try {
        const response = await fetch('/api/v1/market-information/status', { credentials: 'same-origin' });
        if (!response.ok) return { statusApiAvailable: false };
        providerStatus = await response.json();
      } catch {
        return { statusApiAvailable: false };
      }
      const links = [...document.querySelectorAll('.market-row a.external-link')];
      const rows = [...document.querySelectorAll('.market-row')];
      const metrics = [...document.querySelectorAll('.market-status-grid .metric')];
      const automaticMetric = metrics.find((metric) => metric.querySelector(':scope > span')?.textContent?.trim() === '자동 갱신');
      const expectedAutomatic = providerStatus.refreshing || providerStatus.automatic_state === 'RUNNING'
        ? '실행 중'
        : providerStatus.automatic_state === 'ENABLED_WHILE_BACKEND_RUNNING'
          ? '활성(백엔드 실행 중 자동)'
          : providerStatus.automatic_state === 'STOPPED' ? '중지' : '확인 필요';
      const providerRows = [...document.querySelectorAll('.provider-status-details li')];
      return {
        statusApiAvailable: true,
        heading: document.querySelector('h1')?.textContent?.trim() === '미국 시장정보',
        search: Boolean(document.querySelector('#market-search[maxlength="100"]')),
        categories: [...document.querySelectorAll('.market-categories button')].length === 5,
        selectors: document.querySelectorAll('.market-selectors select').length === 3,
        statusCards: document.querySelectorAll('.market-status-grid .metric').length === 8,
        splitRunAndDataState: ['마지막 시도', '마지막 수집 성공', '이번 수집 결과', '현재 자료 기준시각', '재시도'].every(
          (label) => metrics.some((metric) => metric.querySelector(':scope > span')?.textContent?.trim() === label)
        ),
        automaticStateMatchesApi: automaticMetric?.querySelector('strong')?.textContent?.trim() === expectedAutomatic,
        providerPanelExpanded: document.querySelector('.provider-status-details')?.open === true,
        providerPanelNoDollar: !document.querySelector('.provider-status-details')?.innerText.includes('$'),
        providerTimingLabelsMatchApi: providerStatus.providers.every((provider, index) => {
          const result = provider.this_collection_result || provider.current_result || provider.status;
          return !(provider.next_retry_at || provider.next_allowed_at) || providerRows[index]?.innerText.includes(
            ['OK', 'NOT_MODIFIED'].includes(result) ? '다음 확인' : '재시도'
          );
        }),
        timeoutNotReportedAsSuccess: (providerStatus.timed_out_provider_ids || []).length > 0 &&
          (providerStatus.timed_out_provider_ids || []).every((providerId) => {
          const row = document.querySelector('.provider-status-details li[data-provider-id="' + CSS.escape(providerId) + '"]');
          return Boolean(row) && row.innerText.includes('이번 수집 시간 초과') && !row.innerText.includes('이번 수집 전체 정상');
        }),
        fixtureCollectionStates: providerStatus.current_result === 'PARTIAL' &&
          document.body.innerText.includes('부분 완료(일부 실패)') &&
          providerStatus.providers.some((provider) => {
            const row = document.querySelector('.provider-status-details li[data-provider-id="' + CSS.escape(provider.provider_id) + '"]');
            return provider.this_collection_result === 'OK' && row?.innerText.includes('이번 수집 전체 정상');
          }) &&
          providerStatus.providers.some((provider) => {
            const row = document.querySelector('.provider-status-details li[data-provider-id="' + CSS.escape(provider.provider_id) + '"]');
            return provider.this_collection_result === 'FAILED' && row?.innerText.includes('이번 수집 전체 실패');
          }) &&
          providerStatus.providers.some((provider) => {
            const row = document.querySelector('.provider-status-details li[data-provider-id="' + CSS.escape(provider.provider_id) + '"]');
            return provider.this_collection_result === 'TIMED_OUT' && row?.innerText.includes('이번 수집 시간 초과');
          }) &&
          providerStatus.providers.some((provider) => {
            const row = document.querySelector('.provider-status-details li[data-provider-id="' + CSS.escape(provider.provider_id) + '"]');
            return provider.using_previous_data === true && row?.innerText.includes('이전 정상 자료 사용');
          }) &&
          providerStatus.providers.some((provider) => {
            const row = document.querySelector('.provider-status-details li[data-provider-id="' + CSS.escape(provider.provider_id) + '"]');
            return provider.success_recorded === false &&
              row?.innerText.includes('최근 성공 기록 없음') &&
              !row?.innerText.includes('최근 성공 성공 기록 없음');
          }),
        retentionPanel: Boolean(document.querySelector('.market-retention')) &&
          document.querySelector('.market-retention')?.innerText.includes('현재는 삭제 대상 확인만 지원합니다.') &&
          document.querySelector('.market-retention')?.innerText.includes('실제 삭제 및 자동 정리 기능은 활성화되어 있지 않습니다.'),
        retentionHasNoDeleteControl: ![...document.querySelectorAll('.market-retention button')].some(
          (button) => /^삭제(?! 대상 더 보기)/.test(button.textContent?.trim() || '')
        ),
        retentionUnitsSeparated: document.querySelector('.market-retention')?.innerText.includes('총 정보 항목') &&
          document.querySelector('.market-retention')?.innerText.includes('총 출처 기록'),
        secBoundary: document.body.innerText.includes('SEC 자동조회 설정'),
        metadataOnly: document.body.innerText.includes('기사 본문은 저장하거나 검색하지 않습니다'),
        structuralLinkNotice: document.body.innerText.includes('원문 링크는 구조 안전성만 확인합니다'),
        rowCount: rows.length,
        rowsHaveStableIds: rows.every((row) => Boolean(row.dataset.infoId)),
        safeLinks: links.every((link) => {
          try {
            const parsed = new URL(link.href);
            const rel = new Set(link.rel.split(/\\s+/));
            return link.target === '_blank' && rel.has('noopener') && rel.has('noreferrer') &&
              ['http:', 'https:'].includes(parsed.protocol) && officialHosts.has(parsed.hostname);
          } catch { return false; }
        })
      };
    })()`);
    const requiredMarketChecks = [
      "statusApiAvailable", "heading", "search", "categories", "selectors", "statusCards", "splitRunAndDataState", "secBoundary",
      "automaticStateMatchesApi", "providerPanelExpanded", "providerPanelNoDollar", "providerTimingLabelsMatchApi",
      "timeoutNotReportedAsSuccess", "fixtureCollectionStates", "retentionPanel", "retentionHasNoDeleteControl", "retentionUnitsSeparated",
      "metadataOnly", "structuralLinkNotice",
      "rowsHaveStableIds", "safeLinks"
    ];
    if (!marketState || requiredMarketChecks.some((key) => marketState[key] !== true)) {
      const missing = marketState
        ? requiredMarketChecks.filter((key) => marketState[key] !== true).join(', ')
        : "stateUnavailable";
      fail(`미국 시장정보 화면 경계 검사가 충족되지 않았습니다: ${missing}`);
    }
    await evaluate(client, `(() => {
      const target = document.querySelector('.provider-status-details li[data-provider-id="federal_reserve"]');
      if (!target) return false;
      target.scrollIntoView({ block: 'center' });
      return true;
    })()`);
    const providerFailuresPath = path.join(options.outputDir, "market-provider-failures-1600x900.png");
    await screenshot(client, providerFailuresPath);
    screenshots.push(providerFailuresPath);
    await evaluate(client, `document.querySelector('.market-retention')?.scrollIntoView({ block: 'start' })`);
    const retentionPath = path.join(options.outputDir, "market-retention-preview-1600x900.png");
    await screenshot(client, retentionPath);
    screenshots.push(retentionPath);
    await evaluate(client, `window.scrollTo(0, 0)`);
    for (const [width, height] of [[1920, 1080], [1600, 900], [1366, 768]]) {
      await setViewport(client, width, height);
      await assertResponsiveLayout(client, `시장정보 ${width}x${height}`);
      const destination = path.join(options.outputDir, `market-light-${width}x${height}.png`);
      await screenshot(client, destination);
      screenshots.push(destination);
    }
    for (const zoom of [125, 150, 200]) {
      await setDesktopZoomEquivalent(client, 1366, 768, zoom);
      await assertResponsiveLayout(client, `시장정보 1366x768 / ${zoom}% CDP 등가 확대`);
      const destination = path.join(options.outputDir, `market-light-1366x768-zoom-${zoom}.png`);
      await screenshot(client, destination);
      screenshots.push(destination);
    }

    await navigateTo(client, baseUrl, "/strategies", "투자전략 혼합 설정");
    await waitFor(
      client,
      `document.body.innerText.includes('후보 평가용 점수 가중치와 다르며')`,
      "전략 정책 안내"
    );
    const strategyPrepared = await evaluate(client, `(() => {
      const weights = [...document.querySelectorAll('.strategy-row input[inputmode="decimal"]')];
      const version = document.querySelector('.save-row input');
      if (weights.length !== 8 || !version) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      for (const weight of weights) {
        setter.call(weight, '0');
        weight.dispatchEvent(new Event('input', { bubbles: true }));
      }
      setter.call(weights[0], '100');
      weights[0].dispatchEvent(new Event('input', { bubbles: true }));
      const versionName = 'browser-smoke-' + Date.now().toString(36);
      setter.call(version, versionName);
      version.dispatchEvent(new Event('input', { bubbles: true }));
      return versionName;
    })()`);
    if (!strategyPrepared) fail("전략 목표 입력 요소를 찾을 수 없습니다.");
    await waitFor(
      client,
      `document.body.innerText.includes('100% / 100%') &&
       document.querySelector('.save-row button')?.disabled === false`,
      "전략 목표 서버 미리보기"
    );
    if (!await evaluate(client, buttonExpression("새 불변 버전 저장"))) {
      fail("전략 목표 저장 버튼을 찾을 수 없습니다.");
    }
    await waitFor(
      client,
      `document.body.innerText.includes('새 전략 목표 버전을 저장했습니다')`,
      "불변 전략 목표 저장"
    );
    await waitFor(
      client,
      `document.body.innerText.includes('불변 저장') &&
       document.body.innerText.includes('실제 비중') &&
       document.body.innerText.includes('점진적 포트폴리오 전환')`,
      "전략 초안·저장 목표·실제 비중·불변 이력"
    );
    const strategyState = await evaluate(client, `(() => ({
      savedVersionVisible: document.body.innerText.includes(${JSON.stringify(strategyPrepared)}),
      immutableHistoryVisible: document.body.innerText.includes('불변 저장'),
      actualBoundaryVisible: document.body.innerText.includes('실제 비중'),
      transitionBoundaryVisible: document.body.innerText.includes('점진적 포트폴리오 전환')
    }))()`);
    if (!strategyState || Object.values(strategyState).some((value) => value !== true)) {
      const missing = strategyState
        ? Object.entries(strategyState).filter(([, value]) => value !== true).map(([key]) => key).join(', ')
        : "stateUnavailable";
      fail(`전략 상태 표시 검사가 충족되지 않았습니다: ${missing}`);
    }
    await setViewport(client, 1600, 900);
    const strategyPath = path.join(options.outputDir, "strategy-mix-1600x900.png");
    await screenshot(client, strategyPath);
    screenshots.push(strategyPath);
    await setViewport(client, 390, 844, true);
    const strategyMobilePath = path.join(options.outputDir, "strategy-mix-390x844.png");
    await screenshot(client, strategyMobilePath);
    screenshots.push(strategyMobilePath);

    await navigateTo(client, baseUrl, "/strategies/VALUE", "가치투자");
    await waitFor(
      client,
      `document.body.innerText.includes('자료가 없으면 적합성·추천종목·전망을 만들지 않습니다')`,
      "전략 상세 자료 경계"
    );
    await setViewport(client, 1600, 900);
    const detailPath = path.join(options.outputDir, "strategy-detail-1600x900.png");
    await screenshot(client, detailPath);
    screenshots.push(detailPath);

    await navigateTo(client, baseUrl, "/settings", "공통 설정");
    await waitFor(
      client,
      `document.body.innerText.includes('브라우저 열린 상태 알림') &&
       document.body.innerText.includes('Service Worker·PWA·외부 Web Push는 사용하지 않습니다') &&
       document.body.innerText.includes('권한 요청 전')`,
      "페이지 열림 알림 경계"
    );
    await client.send("Browser.setPermission", {
      permission: { name: "notifications" },
      setting: "granted",
      origin: baseUrl.origin
    });
    if (!await evaluate(client, buttonExpression("페이지 알림 허용"))) {
      fail("페이지 알림 옵트인 버튼을 찾을 수 없습니다.");
    }
    await waitFor(
      client,
      `document.body.innerText.includes('허용됨 (페이지 열린 동안만)')`,
      "명시적 페이지 알림 허용"
    );
    const forbiddenPushUi = await evaluate(client, `(() => {
      const text = document.body.innerText;
      return text.includes('푸시 구독') || text.includes('VAPID') ||
        [...document.querySelectorAll('button')].some((button) => button.textContent.includes('PWA 설치'));
    })()`);
    if (forbiddenPushUi) fail("비활성화한 PWA·Web Push 조작이 화면에 남아 있습니다.");
    const settingsPath = path.join(options.outputDir, "settings-1600x900.png");
    await screenshot(client, settingsPath);
    screenshots.push(settingsPath);

    if (!await evaluate(client, buttonExpression("다크"))) {
      fail("다크 모드 선택 버튼을 찾을 수 없습니다.");
    }
    await waitFor(
      client,
      `document.documentElement.dataset.theme === 'dark' &&
       localStorage.getItem('wealthy-theme') === 'dark'`,
      "다크 모드 적용"
    );
    const darkSettingsPath = path.join(options.outputDir, "settings-dark-1600x900.png");
    await screenshot(client, darkSettingsPath);
    screenshots.push(darkSettingsPath);
    await navigateTo(client, baseUrl, "/market", "미국 시장정보");
    await setViewport(client, 1600, 900);
    await assertResponsiveLayout(client, "시장정보 다크 1600x900");
    const darkMarketPath = path.join(options.outputDir, "market-dark-1600x900.png");
    await screenshot(client, darkMarketPath);
    screenshots.push(darkMarketPath);
    await navigateTo(client, baseUrl, "/settings", "공통 설정");
    if (!await evaluate(client, buttonExpression("라이트"))) {
      fail("라이트 모드 복원 버튼을 찾을 수 없습니다.");
    }
    await waitFor(
      client,
      `document.documentElement.dataset.theme === 'light' &&
       localStorage.getItem('wealthy-theme') === 'light'`,
      "라이트 모드 복원"
    );

    const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
    const manualPayload = JSON.parse(
      await readFile(path.join(projectRoot, "examples", "offline_sample.json"), "utf8")
    );
    manualPayload.realm = "MANUAL_ACTUAL";
    manualPayload.scenario_label = "브라우저 수동 실제자료 검증";
    const manualUpload = await evaluate(client, `(async () => {
      const payload = ${JSON.stringify(manualPayload)};
      const csrfCookie = document.cookie.split(';').map((item) => item.trim())
        .find((item) => item.startsWith('wealthy_csrf='));
      if (!csrfCookie) return { ok: false, status: 0 };
      const csrf = decodeURIComponent(csrfCookie.slice('wealthy_csrf='.length));
      const body = new FormData();
      body.set('file', new Blob([JSON.stringify(payload)], { type: 'application/json' }), 'manual.json');
      const response = await fetch('/api/v1/scenarios/upload', {
        method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': csrf }, body
      });
      return { ok: response.ok, status: response.status };
    })()`);
    if (!manualUpload?.ok) fail("수동 실제자료 보고서 fixture를 생성할 수 없습니다.");

    await navigateTo(client, baseUrl, "/reports", "보고서 이력");
    await waitFor(
      client,
      `(() => {
        const text = document.querySelector('.report-list')?.innerText || '';
        const buttons = document.querySelectorAll('.report-list button');
        return buttons.length >= 2 && text.includes('SAMPLE · 샘플 자료') &&
          text.includes('MANUAL_ACTUAL · 수동 실제 자료') && text.includes('수동 생성') &&
          !text.includes('정기 보고서') && !text.includes('자료 미입력');
      })()`,
      "자료 영역·생성 종류가 구분된 보고서 목록"
    );
    const firstReport = await evaluate(client, `(() => {
      const button = document.querySelector('.report-list button');
      if (!button) return false;
      button.click();
      return true;
    })()`);
    if (!firstReport) fail("생성된 샘플 보고서를 찾을 수 없습니다.");
    await waitFor(
      client,
      `document.querySelector('.report-viewer')?.innerText.includes('1) 요약 3줄') === true &&
       document.querySelector('.report-viewer')?.innerText.includes('7) 위반·주의') === true`,
      "7개 절 보고서 본문"
    );
    const reportsPath = path.join(options.outputDir, "reports-desktop.png");
    await screenshot(client, reportsPath);
    screenshots.push(reportsPath);

    await setViewport(client, 390, 844, true);
    const mobilePath = path.join(options.outputDir, "reports-mobile.png");
    await screenshot(client, mobilePath);
    screenshots.push(mobilePath);

    const pageText = await evaluate(client, "document.body.innerText");
    if (!pageText.includes("주문 기능 없음")) fail("주문 비활성 경계를 화면에서 확인할 수 없습니다.");
    process.stdout.write(JSON.stringify({
      result: "PASS",
      chrome_mode: options.visible ? "VISIBLE_DISPLAY" : "HEADLESS",
      base_url: baseUrl.href,
      checks: [
        "login", "same_origin_runtime_status", "legacy_pwa_cleanup", "no_manifest",
        "internal_notification_drawer", "page_open_notification_opt_in",
        "sample_import", "dashboard", "required_desktop_viewports", "1000x680",
        "cdp_zoom_125_150_200", "portfolio_real_or_missing_boundaries",
        "strategy_mix", "strategy_preview", "strategy_immutable_save",
        "strategy_draft_saved_actual_history", "strategy_detail", "settings", "light_dark_theme",
        "market_information", "market_provider_status_panel", "market_metadata_only", "market_safe_links", "market_required_viewports",
        "market_zoom_125_150_200", "market_light_dark", "report_scope_generation_history", "report_open",
        "mobile_viewport", "orders_disabled", "no_push_ui"
      ],
      market_rows: marketState.rowCount,
      screenshots
    }, null, 2) + "\n");
  } finally {
    if (client) client.close();
    const waitForChromeExit = (timeoutMs) => new Promise((resolve) => {
      if (chrome.exitCode !== null) return resolve();
      const timer = setTimeout(resolve, timeoutMs);
      chrome.once("exit", () => { clearTimeout(timer); resolve(); });
    });
    if (chrome.exitCode === null) chrome.kill("SIGTERM");
    await waitForChromeExit(2_000);
    if (chrome.exitCode === null) {
      chrome.kill("SIGKILL");
      await waitForChromeExit(2_000);
    }
    await rm(profile, { recursive: true, force: true, maxRetries: 4, retryDelay: 100 });
    if (gateway) await new Promise((resolve) => gateway.server.close(resolve));
  }
}

main().catch((error) => {
  const detail = error instanceof Error
    ? String(error.message).replace(/\/(?:[^\s:]+\/?)+/g, "[경로]").slice(0, 240)
    : "알 수 없는 검사 단계";
  process.stderr.write(
    `브라우저 스모크 실패: ${detail}. 로컬 서버 상태와 fixture 계정을 확인해 주세요.\n`
  );
  process.exitCode = 1;
});
