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

function lightweightEnv(c) {
  const events = [];
  const properties = {};
  c.LockService = {getScriptLock: () => ({
    tryLock: () => {events.push('lock');return true;},
    releaseLock: () => events.push('unlock')
  })};
  c.PropertiesService = {getScriptProperties: () => ({
    setProperty: (key, value) => {properties[key] = value;},
    deleteProperty: (key) => {delete properties[key];}
  })};
  c.Logger = {log: () => {}};
  c.SpreadsheetApp = {flush: () => events.push('flush')};
  c.fetchCsvBatch_ = () => ({
    mof_pending: {rows: [candidate('MOF-PENDING-a')], notModified: false},
    mof_legacy: {rows: [legacy('MOF-LEGACY-b')], notModified: false},
  });
  c.getSpreadsheet_ = () => {events.push('open');return {};};
  c.ensureMofCandidateStructure_ = () => events.push('init');
  c.syncMofCandidateSheet_ = (ss, tab) => events.push(tab);
  for(const forbidden of ['syncAll_', 'ensureStructure_', 'appendAudit_', 'updateAnomalies_']) {
    c[forbidden] = () => {throw Error('wrong heavy code path: ' + forbidden);};
  }
  return {events, properties};
}
test('lightweight sync touches only two review tabs, never full-screening paths', () => {
  const c = ctx();
  const {events, properties} = lightweightEnv(c);
  const result = c.syncMofReviewOnly();
  assert.equal(result.pending, 1);
  assert.equal(result.legacy, 1);
  assert.deepEqual(events, ['lock', 'open', 'init',
    '12_財務省差分候補', '13_財務省出典変更', 'flush', 'unlock']);
  assert.equal(properties.MOF_REVIEW_STATUS, 'SUCCESS');
});
test('lightweight sync keeps exact failing stage on Sheets service timeout', () => {
  const c = ctx();
  const {events, properties} = lightweightEnv(c);
  c.syncMofCandidateSheet_ = (ss, tab) => {
    events.push(tab);
    if (tab === '13_財務省出典変更') throw Error('Spreadsheet service timed out');
  };
  assert.throws(() => c.syncMofReviewOnly(), /13_財務省出典変更へ反映.*Spreadsheet service timed out/);
  assert.equal(properties.MOF_REVIEW_STATUS, 'ERROR');
  assert.equal(properties.MOF_REVIEW_LAST_FAILED_STAGE, '13_財務省出典変更へ反映');
  assert.equal(events[events.length - 1], 'unlock');
});
test('invalid source feed stops before Sheets service gets opened', () => {
  const c = ctx();
  const {events, properties} = lightweightEnv(c);
  c.fetchCsvBatch_ = () => ({
    mof_pending: {rows: [candidate('MOF-PENDING-a'), candidate('MOF-PENDING-a')], notModified: false},
    mof_legacy: {rows: [legacy('MOF-LEGACY-b')], notModified: false},
  });
  assert.throws(() => c.syncMofReviewOnly(), /候補IDの欠落・重複/);
  assert.equal(properties.MOF_REVIEW_STATUS, 'ERROR');
  assert.equal(events.includes('open'), false);
  assert.equal(events[events.length - 1], 'unlock');
});

function fakeSheet(name, columns, existing=[]) {
  const sheet={name, cols:columns, data:existing.map(r=>r.slice()), frozen:0,
    getName(){return this.name;},
    getMaxRows(){return 1000;},
    getMaxColumns(){return this.cols;},
    getLastRow(){return this.data.length ? this.data.length+4 : 4;},
    setFrozenRows(n){this.frozen=n;},
    insertColumnsAfter(){throw Error('unsafe insertColumnsAfter called');},
    getRange(row,col,height,width){
      if(typeof row==='string') return {
        setValue(){return this;}, setValues(){return this;},
      };
      const target=this;
      return {
        setValues(rows){
          if(row>=5) rows.forEach((cells,i)=>sheet.data[row-5+i]=cells.slice());
          return this;
        },
        setNumberFormat(){return this;},
        getValues(){
          return Array.from({length:height},(_,i) => {
            const item=sheet.data[row-5+i] || [];
            return Array.from({length:width},(_,j)=>item[j]||'');
          });
        },
      };
    },
  };
  return sheet;
}
function fakeSpreadsheet(tabs) {
  const byName=new Map(tabs.map(s=>[s.name,s]));
  const created=[];
  const ss={
    getSheetByName(name){return byName.get(name)||null;},
    insertSheet(name){
      if(byName.has(name))throw Error('duplicate sheet');
      const item=fakeSheet(name,26);byName.set(name,item);
      created.push(name);return item;
    },
  };
  return {ss,byName,created};
}
test('undersized old MOF tabs are never resized: create fresh recovery tabs',()=>{
  const c=ctx();
  c.Logger={log:()=>{}};
  c.ensureColumns_=()=>{throw Error('never expand old columns')};
  c.ensureRows_=()=>{};
  const oldPending=fakeSheet('12_財務省差分候補',18);
  const oldLegacy=fakeSheet('13_財務省出典変更',18);
  const {ss,byName,created}=fakeSpreadsheet([oldPending,oldLegacy]);
  c.ensureMofCandidateStructure_(ss);
  assert.deepEqual(created,[
    '12_財務省差分候補_復旧','13_財務省出典変更_復旧']);
  assert.equal(oldPending.cols,18);
  assert.equal(oldLegacy.cols,18);
  c.syncMofCandidateSheet_(ss,'12_財務省差分候補',
    [candidate('MOF-PENDING-1')],vm.runInContext('EXPECTED.mofPending',c),
    'MOF-PENDING-','要レビュー・master未反映');
  c.syncMofCandidateSheet_(ss,'13_財務省出典変更',
    [legacy('MOF-LEGACY-1')],vm.runInContext('EXPECTED.mofLegacy',c),
    'MOF-LEGACY-','REVIEW_REQUIRED');
  assert.equal(byName.get('12_財務省差分候補_復旧').data[0][0],'MOF-PENDING-1');
  assert.equal(byName.get('13_財務省出典変更_復旧').data[0][0],'MOF-LEGACY-1');
  c.ensureMofCandidateStructure_(ss);
  assert.equal(created.length,2,'reuse same recovery tabs on repeated runs');
});
test('undersized legacy sheets retain confirmation text during recovery',()=>{
  const c=ctx();c.Logger={log:()=>{}};
  c.ensureRows_=()=>{};
  const initial=Array(18).fill('');
  initial[0]='MOF-PENDING-1';initial[17]='確認中';
  const old=fakeSheet('12_財務省差分候補',18,[initial]);
  const {ss,byName}=fakeSpreadsheet([old]);
  c.ensureMofCandidateStructure_(ss);
  c.syncMofCandidateSheet_(ss,'12_財務省差分候補',
    [candidate('MOF-PENDING-1')],vm.runInContext('EXPECTED.mofPending',c),
    'MOF-PENDING-','要レビュー・master未反映');
  const replacement=byName.get('12_財務省差分候補_復旧');
  assert.equal(replacement.data[0][17],'確認中');
  replacement.data[0][17]='確認済（メモのみ）';
  replacement.data[0][18]='要法務照会';
  c.syncMofCandidateSheet_(ss,'12_財務省差分候補',
    [candidate('MOF-PENDING-1')],vm.runInContext('EXPECTED.mofPending',c),
    'MOF-PENDING-','要レビュー・master未反映');
  assert.equal(replacement.data[0][17],'確認済（メモのみ）');
  assert.equal(replacement.data[0][18],'要法務照会');
  assert.equal(old.data[0][17],'確認中','original remains intact');
});

test('MOF audit hold is explicit and cannot become healthy or a fetch error', () => {
  const c=ctx();
  const checked=new Date(Date.now()+9*60*60*1000-60*1000).toISOString().slice(0,19).replace('T',' ');
  const health=c.freshness_('財務省','取得済・監査停止（master未反映）',checked,{});
  assert.equal(health.severity,3);
  assert.equal(health.anomalyType,'反映停止');
  assert.match(health.note,/原本取得・差分生成は成功/);
  assert.doesNotMatch(health.note,/GitHub側で取得エラー/);
});
test('MOF review hold preserves stale-fetch alarms', () => {
  const c=ctx();
  const fresh=c.freshness_('財務省','取得済・要レビュー（master未反映）',
    '', {});
  assert.equal(fresh.severity,3); // Missing check time is never healthy.
});
test('detailed MOF feed must match the exact failed generation before display', () => {
  const c=ctx();
  const base=[['財務省','エラー','2026-10-30 11:55:46','令和8年10月21日','15284','f8e1038419ab']];
  const detail=[['財務省','取得済・監査停止（master未反映）',...base[0].slice(2)]];
  assert.equal(c.mergeStatusDetail_(base,detail)[0][1],detail[0][1]);
  for (const index of [2,3,4,5]) {
    const stale=detail.map(r=>r.slice());stale[0][index]='different';
    assert.equal(c.mergeStatusDetail_(base,stale)[0][1],'エラー');
  }
  const recovered=base.map(r=>r.slice());recovered[0][1]='変更なし';
  assert.equal(c.mergeStatusDetail_(recovered,detail)[0][1],'変更なし');
});
test('fresh approval hold stays warning and stale hold includes monitoring delay', () => {
  const c=ctx();
  const fmt=age=>new Date(Date.now()+9*60*60*1000-age*60000).toISOString().slice(0,19).replace('T',' ');
  const fresh=c.freshness_('財務省','取得済・要レビュー（master未反映）',fmt(1),{});
  assert.equal(fresh.severity,2);
  const stale=c.freshness_('財務省','取得済・監査停止（master未反映）',fmt(700),{});
  assert.equal(stale.severity,3);
  assert.equal(stale.anomalyType,'監視遅延・反映停止');
});
