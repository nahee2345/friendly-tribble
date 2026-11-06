import { chromium } from '/tmp/wealthy-design-playwright/node_modules/playwright/index.mjs';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const output = resolve('design-artifacts');
mkdirSync(output, { recursive: true });
const cssPath = resolve('web/frontend/src/stage1.css');
const css = readFileSync(cssPath, 'utf8');
const browser = await chromium.launch({ headless: true });
const widths = [1366, 1600, 1920];
const themes = ['light', 'dark'];
const zooms = [100, 125, 150, 200];
const results = [];
const html = `<!doctype html><html lang="ko"><meta charset="utf-8">
<style>
*{box-sizing:border-box}body{margin:0;font-family:Arial,'Noto Sans KR',sans-serif;background:var(--bg);color:var(--text)}
:root{--bg:#f7f8fa;--card:#ffffff;--subtle-card:#f0f3f6;--text:#152232;--muted:#536275;--border:#d7dfe8;--blue:#1955c6;--danger:#ad2934;--success:#176b48;--selected:#e8f0ff;--shadow:0 2px 12px #172c4010}
:root[data-theme=dark]{--bg:#111a27;--card:#1a2635;--subtle-card:#263345;--text:#eef3fa;--muted:#b2c2d4;--border:#445369;--blue:#88b2ff;--danger:#ff8792;--success:#8edfc3;--selected:#263956;--shadow:0 2px 12px #0003}
</style><body><div class="s1-shell">
<div class="s1-heading"><div><div class="s1-eyebrow">WEALTHY / PAPER INVESTING</div><h1>모의거래 상태 및 오류 안내 검수용 고정 화면</h1><p>시각 검수 전용 예시 · 실제 API와 계좌·거래 원장을 사용하지 않습니다.</p></div><span class="s1-pill">거래 실행 보류</span></div>
<div class="s1-columns">
<section class="s1-panel"><div class="s1-panelhead"><h2>모의거래 긴급 중지</h2><span role="status">상태 확인 불가 · 거래 실행 보류 · 세션 시작자산 및 환율의 검증이 완료되지 않았습니다.</span></div><p class="s1-desc" role="status">현재 상태: 서버에서 상태를 확인할 수 없습니다. 가상 거래 실행을 보류합니다.</p><button class="s1-primary">긴급 중지 요청</button><button class="s1-outline" disabled>가상매매 시작 불가 · 검증 대기</button></section>
<section class="s1-panel"><div class="s1-panelhead"><h2>모의거래 승인 대기</h2><span role="status">원장 대조 필요</span></div><div class="s1-candidate"><h3>가상 주문 · 승인됨 · 체결 전</h3><p role="alert">처리 결과 UNKNOWN: 예약된 금액은 원장 대조 전 자동으로 해제되지 않으며, 같은 요청을 다시 실행하지 않습니다.</p><div class="s1-candidate-actions"><button class="s1-primary" disabled>승인된 가상 주문 실행 · 시세 재검증</button></div></div></section>
</div><section class="s1-panel" style="margin-top:20px"><div class="s1-panelhead"><h2>자산 정보 검증 상태</h2><span role="status">시작 NAV 검증 대기 · 실제 보유 직접기록과 SAMPLE은 PAPER 합계에 포함하지 않습니다.</span></div><div class="s1-balances"><div><small>세션 시작 NAV</small><strong>검증 대기</strong></div><div><small>일일 20% 잔여 한도</small><strong>검증 대기</strong></div></div><p class="s1-desc">설정된 시작금 4,000,000원은 계획값이며, 현재 세션의 확인된 거래 가능 금액이 아닙니다.</p></section>
</div></body></html>`;
for (const width of widths) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  for (const theme of themes) {
    for (const zoom of zooms) {
      await page.setContent(html);
      await page.addStyleTag({ content: css });
      await page.evaluate(({ theme, zoom }) => {
        document.documentElement.dataset.theme = theme;
        document.body.style.zoom = String(zoom / 100);
      }, { theme, zoom });
      await page.keyboard.press('Tab');
      const data = await page.evaluate(() => {
        const focused = document.activeElement;
        const style = focused ? getComputedStyle(focused) : null;
        const horizontalOverflow = document.documentElement.scrollWidth > innerWidth + 2;
        const visibleFocus = !!style && style.outlineStyle !== 'none' && parseFloat(style.outlineWidth || '0') > 0;
        return { horizontalOverflow, visibleFocus, focusedTag: focused?.tagName, viewport: innerWidth, documentWidth: document.documentElement.scrollWidth };
      });
      const key = `${width}-${theme}-zoom${zoom}`;
      await page.screenshot({ path: resolve(output, key + '.png'), fullPage: true });
      results.push({ key, ...data });
    }
  }
  await page.close();
}
await browser.close();
writeFileSync(resolve(output, 'fixture-results.json'), JSON.stringify({ note: 'Isolated static design fixture, NOT authenticated Wealthy app Chrome E2E', results }, null, 2));
const focusFailures = results.filter(x => !x.visibleFocus);
const overflows = results.filter(x => x.horizontalOverflow);
if (focusFailures.length || overflows.length) {
  console.error(JSON.stringify({ focusFailures, overflows }, null, 2));
  process.exitCode = 1;
} else console.log(`Static fixture checks passed: ${results.length} viewport/theme/zoom cases`);
