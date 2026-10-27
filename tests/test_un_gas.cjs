const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function ctx() {
  const c = vm.createContext({console, Date});
  vm.runInContext(fs.readFileSync('apps_script/Code.gs','utf8') + '\n' + fs.readFileSync('apps_script/Mofa.gs','utf8') + '\n' + fs.readFileSync('apps_script/Un.gs','utf8'), c);
  return c;
}
const row = id => [id,'2026-10-27T01:00:00Z','国連XML差分','','=HYPERLINK("evil")','PENDING_REVIEW',1,0,0,'a'.repeat(64),'https://main.un.org/'];
test('UN memo survives by event; formula stays text; official state is read-only', () => {
  const c=ctx(); const rows=c.unRowsForSheet_([row('b'),row('a')],[row('a').concat('memo')]);
  assert.equal(rows[0][4][0], "'"); assert.equal(rows[0][11], '');
  assert.equal(rows[1][11], 'memo'); assert.equal(rows[1][5], 'PENDING_REVIEW');
  assert.throws(()=>c.unRowsForSheet_([row('a'),row('a')],[]));
});
test('UN stale observations warn and pending reviews are never green', () => {
  const c=ctx(); c.parseJst_=()=>new Date(Date.now()-10*60000);
  assert.equal(c.freshness_('国連（統合リスト）','更新候補・要レビュー','x',{}).label,'要確認');
  c.parseJst_=()=>new Date(Date.now()-60*60000);
  assert.equal(c.freshness_('国連（統合リスト）','更新候補・要レビュー','x',{}).label,'警告');
  c.parseJst_=()=>new Date(Date.now()-120*60000);
  assert.equal(c.freshness_('国連（委員会発表）','変更なし','x',{}).label,'重大');
});
test('eight source rows end before recent changes', () => {
  const c=ctx(); let sourceRange,recentRange;
  const sheet={getLastRow:()=>0,getRange(...a){return {setValues(){if(a.length===4&&a[0]===9)sourceRange=a;return this;},setValue(){return this;},clearContent(){if(typeof a[0]==='string')recentRange=a[0];return this;}};}};
  c.requireSheet_=()=>sheet; c.formatJst_=()=>''; c.freshness_=()=>({label:'正常',severity:0});
  c.updateDashboard_({},[],{}); c.refreshRecentChanges_({});
  assert.deepEqual(sourceRange,[9,1,8,8]); assert.equal(recentRange,'A22:F25');
});
