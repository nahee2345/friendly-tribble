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
      observations.push({ test: label, status: metrics.hasFocus ? 'PASS' : 'FAIL', ...metrics });
    }
  }
}
await page.evaluate(() => { document.body.style.zoom = '1'; });
for (const route of ['/accounts','/dashboard']) {
  await page.goto(root + route, { waitUntil: 'domcontentloaded' });
  await page.screenshot({ path: resolve(out,route.slice(1)+'-logged-in.png'),fullPage:true });
  const title = await page.title();
  observations.push({ test: route, status: 'PASS', title });
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
