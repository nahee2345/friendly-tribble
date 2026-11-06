import { chromium } from '/tmp/wealthy-design-playwright/node_modules/playwright/index.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = process.env.DESIGN_E2E_URL || 'http://127.0.0.1:8000';
const out = resolve('design-artifacts/real-app', process.env.DESIGN_E2E_TWO_USERS === '1' ? 'two-users-blocked' : 'single-user');
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ headless: true });
const observations = [];
const errors = [];
function assert(condition, description) {
  if (!condition) throw Error(description);
}
async function login(page, username, password) {
  await page.goto(root + '/assets', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.getByRole('textbox', { name: '사용자 이름' }).fill(username);
  await page.getByLabel('비밀번호').fill(password);
  await page.getByRole('button', { name: '로그인' }).click();
  await page.getByRole('heading', { name: '내 자산 · 월별 정리' }).waitFor({ timeout: 30000 });
}
const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, colorScheme: 'light' });
const page = await ctx.newPage();
page.on('pageerror', e => errors.push(String(e)));
if (process.env.DESIGN_E2E_TWO_USERS === '1') {
  for (const [username,password] of [
    ['design_a','design test account alpha 2026'],
    ['design_b','design test account bravo 2026']
  ]) {
    await page.goto(root + '/assets', { waitUntil: 'domcontentloaded' });
    await page.getByRole('textbox', { name: '사용자 이름' }).fill(username);
    await page.getByLabel('비밀번호').fill(password);
    const [response] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/api/v1/login') && r.request().method() === 'POST'),
      page.getByRole('button',{name:'로그인'}).click()
    ]);
    assert(response.status() === 400, 'Two-user local runtime must block ' + username + ', got ' + response.status());
    await page.getByRole('heading',{name:'Wealthy에 로그인'}).waitFor();
    assert(await page.getByRole('heading',{name:'내 자산 · 월별 정리'}).count() === 0, 'Finance page exposed for ' + username);
    const finance = await page.evaluate(async () => {
      const r = await fetch('/api/v1/portfolio');
      return { status: r.status, text: await r.text() };
    });
    assert(finance.status !== 200, 'Protected financial API allowed second-user database');
    assert(!finance.text.includes('design_a') && !finance.text.includes('design_b'), 'Protected account identity leaked');
    observations.push({test:'single-owner-fail-closed-'+username,status:'PASS',loginHttp:400,portfolioHttp:finance.status});
  }
  await page.screenshot({path:resolve(out,'two-users-blocked.png'),fullPage:true});
  await browser.close();
  writeFileSync(resolve(out,'results.json'),JSON.stringify({kind:'actual local-runtime two-user fail-closed',observations,errors},null,2));
  console.log('Two-user fail-closed Chrome checks:',observations.length);
  process.exit(0);
}
await login(page, 'design_a', 'design test account alpha 2026');
const token = await page.evaluate(async () => {
  const r = await fetch('/api/v1/session', { credentials: 'same-origin' });
  return { status: r.status, user: (await r.json()).user?.username ?? null };
});
assert(token.status === 200 && token.user === 'design_a', 'Real FastAPI session login failed');
const initial = await page.evaluate(async () => {
  const r = await fetch('/api/v1/portfolio', { credentials: 'same-origin' });
  return { status: r.status, body: r.ok ? await r.json() : null };
});
assert(initial.status === 200, 'Actual portfolio API did not authenticate');
observations.push({ test: 'auth-login-and-portfolio', status: 'PASS', data_scope: initial.body?.data_scope, included: initial.body?.total_scope?.included_account_ids ?? [] });
// Real API↔React scope consistency on the same isolated authenticated session.
await page.getByRole('status', { name: '투자자산 합계 범위' }).waitFor({ timeout: 15000 });
const scopeProof = await page.evaluate(() => {
  const panel = document.querySelector('[aria-label="투자자산 합계 범위"]');
  const metric = [...document.querySelectorAll('article.metric')].find(node => node.textContent?.includes('투자자산 합계'));
  return { scopeText: panel?.textContent || '', metricText: metric?.textContent || '', metricStrong: metric?.querySelector('strong')?.textContent || '' };
});
assert(scopeProof.scopeText.length > 0, 'Scope banner not visible');
if (initial.body?.total_scope?.status !== 'LEDGER_COMPLETE' || initial.body?.total_scope?.realm !== initial.body?.data_scope) {
  assert(scopeProof.scopeText.includes('미검증 · 합계 사용 보류') || scopeProof.scopeText.includes('자료 영역 확인 필요'),
    'Unverified account scope not explained');
  assert(!/₩[0-9]/.test(scopeProof.metricStrong), 'Unverified portfolio amount rendered as verified KRW');
}
observations.push({test:'real-api-assets-scope-guard',status:'PASS',scope:initial.body?.data_scope,scopeStatus:initial.body?.total_scope?.status,renderedAmount:scopeProof.metricStrong});

// A real authenticated mutation without a CSRF header must be rejected, never
// represented as an authorized trading-control change.
const csrfRejection = await page.evaluate(async () => {
  const response = await fetch('/api/v1/stage1/trade-control', {
    method: 'POST', credentials: 'same-origin', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({paused:false,reason:'CSRF forbidden design E2E'})
  });
  return {status:response.status};
});
assert(csrfRejection.status === 403, 'Missing CSRF allowed mutation: HTTP '+csrfRejection.status);
observations.push({test:'authenticated-mutation-missing-csrf-403',status:'PASS',http:403});

await page.goto(root + '/stage1', { waitUntil:'domcontentloaded' });
await page.getByRole('button',{name:'자산·설정·보고서'}).click();
try {
  await page.getByRole('heading',{name:'가상 투자 워크스페이스'}).waitFor({timeout:8000});
} catch (error) {
  const snapshot = await page.evaluate(() => ({
    title:document.title, route:location.pathname,
    bodyText:document.body.innerText.slice(0,1700)
  }));
  await page.screenshot({path:resolve(out,'stage1-page-missing-heading.png'),fullPage:true});
  console.error('Stage1 real-app visibility diagnostic',JSON.stringify({snapshot,jsErrors:errors,error:String(error)}));
  throw error;
}
const readiness = await page.evaluate(async () => {
  const response = await fetch('/api/v1/stage1/paper-execution-readiness');
  return {status:response.status, data:response.ok?await response.json():null};
});
assert(readiness.status === 200, 'Readiness endpoint unavailable: '+readiness.status);
if (readiness.data?.verified !== true) {
  await page.getByRole('button',{name:/통합 계획으로 가상 모의투자 1회 실행/}).waitFor({timeout:20000});
  assert(await page.getByRole('button',{name:/통합 계획으로 가상 모의투자 1회 실행/}).isDisabled(),
    'Stage4 execution enabled despite unverified readiness');
  observations.push({test:'real-nav-unverified-stage4-disabled',status:'PASS',readiness:readiness.data?.verified,code:readiness.data?.code});
}
await page.screenshot({path:resolve(out,'stage1-real-readiness.png'),fullPage:true});
await page.goto(root + '/assets', {waitUntil:'domcontentloaded'});
await page.getByRole('status', { name: '투자자산 합계 범위' }).waitFor({ timeout: 15000 });

const dimensions = [1366, 1600, 1920];
const themes = ['light','dark'];
const zooms = [100,125,150,200];
for (const width of dimensions) {
  await page.setViewportSize({ width, height: 900 });
  for (const theme of themes) {
    await page.evaluate(t => { document.documentElement.dataset.theme = t; }, theme);
    for (const zoom of zooms) {
      await page.evaluate(z => { document.body.style.zoom = String(z / 100); }, zoom);
      await page.keyboard.press('Tab');
      const metrics = await page.evaluate(() => {
        const active = document.activeElement;
        return { hasFocus: !!active && active !== document.body, horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 2, documentWidth: document.documentElement.scrollWidth };
      });
      const label = 'assets-' + width + '-' + theme + '-' + zoom;
      await page.screenshot({ path: resolve(out,label+'.png'), fullPage: true, animations: 'disabled' });
      observations.push({ test: label, status: metrics.hasFocus && !metrics.horizontalOverflow ? 'PASS' : 'FAIL', ...metrics });
    }
  }
}
await page.evaluate(() => { document.body.style.zoom = '1'; });
// PR40 /dashboard is the single Stage1 integrated dashboard, including a
// separate no-broker-order boundary and evidence of missing input.
await page.goto(root+'/dashboard',{waitUntil:'domcontentloaded'});
await page.getByRole('heading',{name:'통합 대시보드'}).waitFor();
assert(await page.getByRole('link',{name:'통합 대시보드'}).count()===1,
  'Duplicate integrated dashboard sidebar navigation');
await page.getByLabel('실제 주문 및 입력자료 상태').getByText('주문 기능 없음').waitFor();
observations.push({test:'pr40-dashboard-accessible-heading-and-no-orders',status:'PASS'});
await page.screenshot({path:resolve(out,'pr40-dashboard-boundary.png'),fullPage:true});
await page.goto(root+'/stage1',{waitUntil:'domcontentloaded'});
await page.getByRole('heading',{name:'통합 대시보드'}).waitFor();
observations.push({test:'pr40-stage1-deep-link',status:'PASS'});

// Capture the true browser computed foreground/background contrast rather
// than claiming human visual review. Samples with transparent backgrounds
// are reported as unverified, never treated as high contrast.
const contrast = await page.evaluate(()=>{
  const rgb = value => {
    const m = value.match(/rgba?\\(([^)]+)\\)/);
    if(!m)return null;
    const p = m[1].split(',').map(Number);
    return {v:p.slice(0,3),a:p.length===4?p[3]:1};
  };
  const linear = x => {x/=255;return x<=0.04045?x/12.92:Math.pow((x+0.055)/1.055,2.4)};
  const lum = v => 0.2126*linear(v[0])+0.7152*linear(v[1])+0.0722*linear(v[2]);
  return ['.wh-caveat','.wh-header','.wh-stage8-strip'].map(sel=>{
    const node=document.querySelector(sel);
    if(!node)return {selector:sel,status:'MISSING'};
    const style=getComputedStyle(node);
    const fg=rgb(style.color),bg=rgb(style.backgroundColor);
    return {selector:sel,foreground:style.color,background:style.backgroundColor,
      ratio:fg&&bg&&fg.a===1&&bg.a===1?
        (Math.max(lum(fg.v),lum(bg.v))+0.05)/(Math.min(lum(fg.v),lum(bg.v))+0.05):null};
  });
});
observations.push({test:'pr40-computed-contrast-samples',status:'MEASURED_NOT_VISUALLY_APPROVED',contrast});
for (const route of ['/accounts','/dashboard']) {
  await page.goto(root + route, { waitUntil: 'domcontentloaded' });
  await page.screenshot({ path: resolve(out,route.slice(1)+'-logged-in.png'),fullPage:true });
  const title = await page.title();
  observations.push({ test: route, status: 'PASS', title });
}
// Exercise an in-flight former-session financial response across an actual
// logout and a fresh same-owner login. Only the *late* HTTP status is synthetic;
// session cookies, React navigation, login/logout and protected fresh GETs are real.
for (const lateStatus of [200, 401, 403]) {
  await page.goto(root+'/accounts',{waitUntil:'domcontentloaded'});
  let releaseFirst;
  const holdFirst = new Promise(resolve => { releaseFirst=resolve; });
  let seenFirst;
  const firstRequestSeen = new Promise(resolve => { seenFirst=resolve; });
  let held = false;
  const intercept = async route => {
    if (held) { await route.continue(); return; }
    held=true; seenFirst();
    await holdFirst;
    if (lateStatus===200) {
      const stale = {...initial.body, investable_total_krw:'99999999999',
        total_scope:{status:'LEDGER_COMPLETE',realm:initial.body?.data_scope,
          included_account_ids:['STALE_FINANCIAL_OWNER_MARKER'],excluded_account_ids:[]}};
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(stale)});
    } else {
      await route.fulfill({status:lateStatus,contentType:'application/json',
        body:JSON.stringify({error:{code:'SYNTHETIC_DELAYED_AUTH',message:'fixture only'}})});
    }
  };
  await page.route('**/api/v1/portfolio',intercept);
  await page.getByRole('link',{name:'내 자산'}).click();
  await Promise.race([
    firstRequestSeen,
    new Promise((_,reject)=>setTimeout(()=>reject(Error('No in-flight portfolio request')),8000))
  ]);
  const [logoutHttp]=await Promise.all([
    page.waitForResponse(r=>r.url().includes('/api/v1/logout')&&r.request().method()==='POST'),
    page.getByRole('button',{name:'로그아웃'}).click()
  ]);
  assert(logoutHttp.status()===200,'Logout failed during delayed '+lateStatus);
  await page.getByRole('heading',{name:'Wealthy에 로그인'}).waitFor();
  await login(page,'design_a','design test account alpha 2026');
  releaseFirst();
  await page.waitForTimeout(350);
  const proof=await page.evaluate(async()=>{
    const response=await fetch('/api/v1/session');
    return {sessionStatus:response.status,body:document.body.innerText};
  });
  assert(proof.sessionStatus===200,'Late '+lateStatus+' invalidated new authenticated session');
  assert(!proof.body.includes('STALE_FINANCIAL_OWNER_MARKER'),'Former-session finance data resurfaced');
  observations.push({test:'former-session-delayed-'+lateStatus+'-no-leak',status:'PASS',newSessionHttp:200});
  await page.screenshot({path:resolve(out,'delayed-'+lateStatus+'-after-relogin.png'),fullPage:true});
  await page.unroute('**/api/v1/portfolio',intercept);
}
await page.goto(root+'/assets',{waitUntil:'domcontentloaded'});
const [logoutResponse] = await Promise.all([
  page.waitForResponse(response => response.url().includes('/api/v1/logout') && response.request().method() === 'POST', { timeout: 15000 }),
  page.getByRole('button',{name:'로그아웃'}).click()
]);
observations.push({ test: 'logout-http', status: logoutResponse.ok() ? 'PASS' : 'FAIL', http: logoutResponse.status() });
await page.getByRole('heading',{name:'Wealthy에 로그인'}).waitFor();
const unauth = await page.evaluate(async()=>{
  const r = await fetch('/api/v1/portfolio');
  return r.status;
});
observations.push({test:'logout-portfolio-status',status:unauth===401?'PASS':'FAIL',http:unauth});
assert(unauth === 401, 'Logged-out financial portfolio API was ' + unauth + ', not 401');
observations.push({test:'logout-401-protected-view',status:'PASS',http:unauth});
await browser.close();
writeFileSync(resolve(out,'results.json'),JSON.stringify({kind:'authenticated single-user local Wealthy FastAPI+React temporary database',errors,observations},null,2));
assert(errors.length === 0, 'Browser JS errors: ' + errors.length);
assert(observations.filter(o => o.status === 'FAIL').length === 0, 'One or more browser checks failed');
console.log('Single-user authenticated browser E2E:',observations.length,'checks; JS errors:',errors.length);
