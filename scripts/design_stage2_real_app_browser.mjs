import { chromium } from '/tmp/wealthy-design-playwright/node_modules/playwright/index.mjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const root = process.env.DESIGN_E2E_URL || 'http://127.0.0.1:8000';
const out = resolve('design-artifacts/real-app');
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
await page.getByRole('button',{name:'로그아웃'}).click();
await page.getByRole('heading',{name:'Wealthy에 로그인'}).waitFor();
const unauth = await page.evaluate(async()=>{
  const r = await fetch('/api/v1/portfolio');
  return r.status;
});
assert(unauth === 401, 'Logged-out financial portfolio API was not 401');
observations.push({test:'logout-401-protected-view',status:'PASS',http:unauth});
await login(page,'design_b','design test account bravo 2026');
const switched = await page.evaluate(async()=>{
  const session = await (await fetch('/api/v1/session')).json();
  const portfolio = await (await fetch('/api/v1/portfolio')).json();
  return {user:session.user?.username, data_scope:portfolio.data_scope,accounts:portfolio.accounts?.length??0};
});
assert(switched.user === 'design_b', 'User switch failed');
observations.push({test:'second-user-session',status:'PASS',...switched});
await page.screenshot({path:resolve(out,'second-user-assets.png'),fullPage:true});
await browser.close();
writeFileSync(resolve(out,'results.json'),JSON.stringify({kind:'authenticated actual FastAPI+React temporary database, not production',app_source:'PR32 candidate plus design CSS',errors,observations},null,2));
console.log('Actual browser E2E complete:',observations.length,'checks; JS errors:',errors.length);
