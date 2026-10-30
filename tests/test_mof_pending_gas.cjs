const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function ctx() {
  const c = vm.createContext({console, Date});
  vm.runInContext([
    'apps_script/Code.gs',
    'apps_script/Mofa.gs',
    'apps_script/Un.gs',
    'apps_script/MofPending.gs',
  ].map(p => fs.readFileSync(p, 'utf8')).join('\n'), c);
  return c;
}
const candidate = id => [
  id, '2026-10-30T03:00:00Z', '財務省', '原本レコード削除',
  '=HYPERLINK("evil")', '002-000799', 'QDi.399', '', '', '',
  '掲載終了候補・要レビュー', 'a'.repeat(64), 'b'.repeat(64),
  '20261030T030000Z__shisantouketsu20261030.csv.gz',
  'https://www.mof.go.jp/test.csv', '要レビュー・master未反映',
  'https://github.com/x/actions/runs/11',
];
const legacy = id => [
  id, '2026-10-30T03:00:00Z', 'REVIEW_REQUIRED',
  'ALIAS NAME', 'aliasname', 'sources;categories', 'OFAC;財務省',
  'OFAC', '財務省:タリバーン関係者等', '', '有効', '有効',
  'a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64),
  'https://www.mof.go.jp/test.csv', 'https://github.com/x/actions/runs/11',
];

test('MOF candidate rows preserve notes by stable ID, never approve master', () => {
  const c=ctx();
  const old=c.mofCandidateCells_([candidate('MOF-PENDING-a')], [], 17,
    'MOF-PENDING-', '要レビュー・master未反映');
  assert.equal(old.length, 1);
  assert.equal(old[0][4].charAt(0), "'");
  assert.equal(old[0][17], '未確認');
  old[0][17] = '確認中';
  old[0][18] = '担当者メモ';
  const next=c.mofCandidateCells_([candidate('MOF-PENDING-a')], old, 17,
    'MOF-PENDING-', '要レビュー・master未反映');
  assert.equal(next[0][17], '確認中');
  assert.equal(next[0][18], '担当者メモ');
  assert.equal(next[0][15], '要レビュー・master未反映');
});
test('MOF candidate CSV duplicates and mutated approval status are blocked', () => {
  const c=ctx();
  assert.throws(() => c.mofCandidateCells_(
    [candidate('MOF-PENDING-a'), candidate('MOF-PENDING-a')], [], 17,
    'MOF-PENDING-', '要レビュー・master未反映'));
  const altered=candidate('MOF-PENDING-a');
  altered[15]='APPROVED';
  assert.throws(() => c.mofCandidateCells_(
    [altered], [], 17, 'MOF-PENDING-', '要レビュー・master未反映'));
});
test('MOF legacy evidence stays a separate review queue', () => {
  const c=ctx();
  const rows=c.mofCandidateCells_(
    [legacy('MOF-LEGACY-a')], [], 17, 'MOF-LEGACY-', 'REVIEW_REQUIRED');
  assert.equal(rows[0][2], 'REVIEW_REQUIRED');
  assert.equal(rows[0][17], '未確認');
  assert.equal(rows[0][7], 'OFAC');
});
test('MOF candidate fetch remains mandatory with official headers', () => {
  const c=ctx();
  let specs=[];
  c.monthKeys_=()=>[];
  c.fetchCsvBatch_=(incoming)=>{specs=incoming;throw Error('forced');};
  assert.throws(()=>c.fetchSyncPayload_({'Heartbeat Base':'https://x/'},{}));
  for(const [key,count] of [['mof_pending',17],['mof_legacy',17]]) {
    const spec=specs.find(s=>s.key===key);
    assert.equal(spec.optional,false);
    assert.equal(spec.conditional,false);
    assert.equal(spec.expected.length,count);
  }
});
