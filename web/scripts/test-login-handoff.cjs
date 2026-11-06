// 用真实 IndexedDB 验证隔离，不读取真实账号、草稿或模型。
const assert = require('node:assert/strict'), fs = require('node:fs'), http = require('node:http'), path = require('node:path');
const ts = require('typescript'), { chromium } = require('playwright');
const source = fs.readFileSync(path.join(__dirname, '../lib/workspace-drafts.ts'), 'utf8');
const code = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText;
(async () => {
 const server = http.createServer((_, r) => r.end('<html><body>isolated test</body></html>'));
 await new Promise(r => server.listen(0, '127.0.0.1', r)); let browser;
 try {
  browser = await chromium.launch({channel: 'chrome', headless: true}); const context = await browser.newContext();
  async function tab(account) {
   const page = await context.newPage(); await page.goto(`http://127.0.0.1:${server.address().port}/deeptutor/chat?dt_workspace=A`);
   await page.evaluate(({code, account}) => {
    window.identity = {authenticated: true, user_id: account}; window.owner = account; const exports = {};
    const require = name => {
     if (name === './workspace-scope') return {activeWorkspaceId: () => new URL(location.href).searchParams.get('dt_workspace') || ''};
     if (name === './momorain-guest') return {lastAuthenticatedUserId: () => window.owner};
     if (name === './auth') return {fetchAuthStatus: async () => window.identity};
     throw new Error(`Unexpected import: ${name}`);
    };
    new Function('exports', 'require', code)(exports, require); window.drafts = exports;
   }, {code, account}); return page;
  }
  const a = await tab('account-A'), b = await tab('account-B');
  const save = p => p.evaluate(() => window.drafts.saveLoginHandoffDraft({text: 'synthetic private draft', attachments: []}));
  const take = p => p.evaluate(() => window.drafts.takeLoginHandoffDraft());
  const copyToken = async () => b.evaluate(x => sessionStorage.setItem('momorain:login-handoff', x), await a.evaluate(() => sessionStorage.getItem('momorain:login-handoff')));
  await save(a); assert.equal(await take(b), undefined, 'tab isolation');
  await copyToken(); assert.equal(await take(b), undefined, 'account isolation even if nonce copied');
  await a.evaluate(() => history.replaceState({}, '', '/deeptutor/chat?dt_workspace=B'));
  assert.equal(await take(a), undefined, 'workspace isolation');
  await a.evaluate(() => history.replaceState({}, '', '/deeptutor/chat?dt_workspace=A'));
  assert.equal((await take(a)).text, 'synthetic private draft'); assert.equal(await take(a), undefined, 'consume once');
  await save(a); await b.evaluate(() => {window.identity.user_id = 'account-A'}); await copyToken();
  assert.equal((await Promise.all([take(a), take(b)])).filter(Boolean).length, 1, 'atomic consume');
  await a.evaluate(() => {window.owner = null}); await save(a); await a.evaluate(() => {window.identity.user_id = 'new-account'});
  assert.equal((await take(a)).text, 'synthetic private draft', 'guest continuation');
  await save(a); await a.evaluate(() => window.drafts.saveLoginHandoffDraft({text: '', attachments: []}));
  assert.equal(await take(a), undefined, 'empty input clears stale marker');
  await save(a); await a.evaluate(() => {const t = Date.now(); Date.now = () => t + 31 * 60 * 1000});
  assert.equal(await take(a), undefined, 'expiry');
  console.log('PASS: tab/account/workspace isolation, atomic consumption, guest continuation, empty draft and expiry');
 } finally {if (browser) await browser.close(); await new Promise(r => server.close(r))}
})().catch(e => {console.error(e); process.exitCode = 1});
