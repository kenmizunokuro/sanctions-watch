const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const OLD = '2026-11-09 16:18:45';
const FIRST = '2026-11-07 02:31:25';
const EVENT = ['OFAC', '追加', 'LOURDES', '', '制裁リスト（OFAC：SDN）'];
const key = row => row.slice(0, 6).join('|');
const historyRow = (stamp = OLD, status = '未確認', note = '') => {
  const row = [stamp, ...EVENT, status, note, ''];
  row[8] = key(row);
  return row;
};

function environment() {
  const c = vm.createContext({console, Date,
    Utilities: {
      getUuid: () => 'fixed-run-id',
      formatDate: (date, zone, pattern) => {
        assert.equal(zone, 'Asia/Tokyo');
        assert.equal(pattern, 'yyyy-MM-dd HH:mm:ss');
        return new Date(date.getTime() + 9 * 3600000).toISOString().slice(0, 19).replace('T', ' ');
      },
    },
  });
  vm.runInContext(fs.readFileSync('apps_script/Code.gs', 'utf8'), c);
  return c;
}

function sheet(name, rows = []) {
  const out = {name, data: rows.map(row => row.slice()), reads: 0, writes: 0,
    getLastRow() {return this.data.length + 4;},
    getMaxRows() {return 10000;},
    hideColumns() {},
    getRange(row, col, height, width) {
      return {
        getValues() {
          out.reads++;
          return Array.from({length: height}, (_, i) =>
            Array.from({length: width}, (_, j) => out.data[row - 5 + i]?.[col - 1 + j] ?? ''));
        },
        setValues(values) {
          out.writes++;
          assert.equal(values.length, height);
          values.forEach((cells, i) => {
            assert.equal(cells.length, width);
            const index = row - 5 + i;
            out.data[index] ||= [];
            cells.forEach((value, j) => {out.data[index][col - 1 + j] = value;});
          });
          return this;
        },
        clearContent() {
          for (let i = 0; i < height; i++) {
            const target = out.data[row - 5 + i];
            if (target) for (let j = 0; j < width; j++) target[col - 1 + j] = '';
          }
          while (out.data.length && out.data.at(-1).every(v => v === '')) out.data.pop();
          return this;
        },
        clearDataValidations() {return this;},
        setDataValidation() {return this;},
        setNumberFormat() {return this;},
      };
    },
  };
  return out;
}

function spreadsheet(history, queue = []) {
  const tabs = new Map([
    ['03_変更履歴', sheet('03_変更履歴', history)],
    ['08_最新差分', sheet('08_最新差分', queue)],
    ['09_再審査分', sheet('09_再審査分', [['unchanged']])],
  ]);
  return {tabs, getSheetByName: name => tabs.get(name)};
}

function configureSync(c, ss, incoming) {
  c.PropertiesService = {getScriptProperties: () => ({setProperty() {}, deleteProperty() {}})};
  c.LockService = {getScriptLock: () => ({tryLock: () => true, releaseLock() {}})};
  c.Logger = {log() {}};
  c.SpreadsheetApp = {flush() {}, newDataValidation: () => ({
    requireValueInList() {return this;}, setAllowInvalid() {return this;}, build() {return {};},
  })};
  c.consumePendingLockSkips_ = () => 0;
  c.getSpreadsheet_ = () => ss;
  c.readSettings_ = () => ({'Changes保持件数': 5000});
  c.fetchSyncPayload_ = () => ({status: {rows: []}, changes: {rows: incoming, notModified: false},
    heartbeat: [], mofaDocuments: {rows: [], notModified: true}, unReviews: {rows: []},
    mofPending: {rows: []}, mofLegacy: {rows: []}, reReview: {rows: [], notModified: true}});
  c.updateDashboard_ = () => [];
  c.evaluateAutoSyncHealth_ = () => ({});
  for (const fn of ['ensureTimezone_', 'ensureStructure_', 'syncOfacCandidateFeed_',
    'syncMofaDocuments_', 'commitMofaHttpMeta_', 'syncUnReviews_', 'syncMofCandidateSheet_',
    'refreshReReviewVisibility_', 'refreshRecentChanges_', 'updateAutoSyncDashboard_',
    'updateAnomalies_', 'resolveScriptErrorAnomaly_', 'appendAudit_']) c[fn] = () => {};
}

test('real sync archives 08 human status before correcting 03 and merging the source', () => {
  const c = environment();
  const old = historyRow(OLD, '未確認', '03担当者メモ');
  const queue = [...old.slice(0, 6), '対応中', old[8]];
  const ss = spreadsheet([old], [queue]);
  configureSync(c, ss, [[FIRST, ...EVENT]]);
  c.syncAll_('手動');
  c.syncAll_('手動');
  const rows = ss.tabs.get('03_変更履歴').data;
  assert.equal(rows.length, 1, 'server timestamp repair must not leave both old and corrected events');
  assert.equal(rows[0][0], FIRST);
  assert.equal(rows[0][6], '対応中');
  assert.equal(rows[0][7], '03担当者メモ');
  assert.equal(rows[0][8], key([FIRST, ...EVENT]));
  assert.equal(ss.tabs.get('08_最新差分').data.length, 1);
  assert.equal(ss.tabs.get('08_最新差分').data[0][0], FIRST);
  assert.equal(ss.tabs.get('08_最新差分').data[0][6], '対応中');
  assert.deepEqual(ss.tabs.get('09_再審査分').data, [['unchanged']]);
});

test('08 edit after an interrupted migration survives retry against the corrected 03 key', () => {
  const c = environment();
  const old = historyRow(OLD, '未確認', '中断前の根拠');
  const ss = spreadsheet([old], [[...old.slice(0, 6), '対応中', old[8]]]);
  configureSync(c, ss, [[FIRST, ...EVENT]]);
  const fetch = c.fetchSyncPayload_;
  c.fetchSyncPayload_ = () => {throw Error('injected fetch failure after migration');};
  assert.throws(() => c.syncAll_('手動'), /injected fetch failure/);
  assert.equal(ss.tabs.get('03_変更履歴').data[0][0], FIRST);
  assert.equal(ss.tabs.get('08_最新差分').data[0][0], OLD);
  ss.tabs.get('08_最新差分').data[0][6] = '対応済';
  c.fetchSyncPayload_ = fetch;
  c.syncAll_('手動');
  const rows = ss.tabs.get('03_変更履歴').data;
  assert.equal(rows.length, 1);
  assert.equal(rows[0][6], '対応済', 'retry must archive the human edit through the proven old/new identity');
  assert.equal(rows[0][7], '中断前の根拠');
  assert.equal(ss.tabs.get('08_最新差分').data.length, 0);
});

test('retry aliases use actual five event fields and preserve conflicting stale/current queue statuses', () => {
  const c = environment();
  const corrected = historyRow(FIRST, '対応中', '監査メモ');
  const stale = [...historyRow().slice(0, 6), '対応済', key(historyRow())];
  const current = [...corrected.slice(0, 6), '保留', corrected[8]];
  const ss = spreadsheet([corrected], [stale, current]);
  c.syncQueueStatusesToHistory_(ss);
  const row = ss.tabs.get('03_変更履歴').data[0];
  assert.equal(row[6], '要確認');
  for (const value of ['対応済', '保留', '監査メモ']) assert.ok(row[7].includes(value));
  const changed = stale.slice(); changed[4] = 'different before';
  const other = spreadsheet([corrected], [changed]);
  c.syncQueueStatusesToHistory_(other);
  assert.equal(other.tabs.get('03_変更履歴').data[0][6], '対応中');
  assert.equal(other.tabs.get('03_変更履歴').data[0][7], '監査メモ');
});

test('archival-first sync keeps completion across old/new 08 duplicates in either order', () => {
  for (const historyVariant of ['old', 'corrected', 'both']) {
    for (const reverse of [false, true]) {
      const c = environment();
      const old = historyRow(OLD, '未確認', '旧履歴メモ');
      const corrected = historyRow(FIRST, '未確認', '補正履歴メモ');
      const history = historyVariant === 'both' ? [old, corrected] :
        [historyVariant === 'old' ? old : corrected];
      const queue = [[...old.slice(0, 6), '対応済', old[8]],
                     [...corrected.slice(0, 6), '未確認', corrected[8]]];
      if (reverse) queue.reverse();
      const ss = spreadsheet(history, queue);
      configureSync(c, ss, [[FIRST, ...EVENT]]);
      c.syncAll_('手動');
      const rows = ss.tabs.get('03_変更履歴').data;
      assert.equal(rows.length, 1);
      assert.equal(rows[0][6], '対応済', historyVariant + '/' + reverse);
      history.forEach(row => assert.ok(rows[0][7].includes(row[7])));
      assert.equal(ss.tabs.get('08_最新差分').data.length, 0);
    }
  }
});

test('bulk migration corrects old key and date while preserving every review field', () => {
  const c = environment();
  const ss = spreadsheet([historyRow(OLD, '対応済', '完了根拠')]);
  assert.equal(c.migrateOfacFirstDetectionHistory_(ss), 1);
  const history = ss.tabs.get('03_変更履歴');
  assert.deepEqual(history.data[0], historyRow(FIRST, '対応済', '完了根拠'));
  assert.equal(history.reads, 1);
  assert.equal(history.writes, 1);
  assert.equal(c.migrateOfacFirstDetectionHistory_(ss), 0);
  assert.equal(history.writes, 1, 'already-corrected history must not be rewritten');
});

test('exact mapping excludes changed before/after values, other source/kind, and recurrence', () => {
  const c = environment();
  const rows = [historyRow(FIRST), historyRow('2026-11-10 16:18:45')];
  for (const [index, value] of [[1, '財務省'], [2, '変更'], [3, 'Lourdes'], [4, 'different'], [5, 'different']]) {
    const row = historyRow(); row[index] = value; row[8] = key(row); rows.push(row);
  }
  const ss = spreadsheet(rows);
  assert.equal(c.migrateOfacFirstDetectionHistory_(ss), 0);
  assert.deepEqual(ss.tabs.get('03_変更履歴').data, rows);
  assert.equal(ss.tabs.get('03_変更履歴').writes, 0);
});

test('Date and slash-formatted cells identify the approved instant without formatting unrelated rows', () => {
  const c = environment();
  const oldDate = new Date('2026-11-09T07:18:45Z');
  const first = historyRow(oldDate, '保留', 'Dateメモ');
  const slash = historyRow('2026/11/09 16:18:45');
  const unrelated = historyRow(new Date('2026-11-10T07:18:45Z'));
  const ss = spreadsheet([first, slash, unrelated]);
  c.migrateOfacFirstDetectionHistory_(ss);
  const rows = ss.tabs.get('03_変更履歴').data;
  assert.equal(rows.length, 2);
  assert.equal(rows[0][0], FIRST);
  assert.equal(rows[0][6], '保留');
  assert.equal(rows[0][7], 'Dateメモ');
  assert.equal(rows[1][0], unrelated[0]);
  assert.equal(rows[1][8], unrelated[8]);
});

test('proven old/new duplicates retain completion and all notes rather than default status', () => {
  const c = environment();
  const ss = spreadsheet([historyRow(OLD, '対応済', '旧キーで完了'), historyRow(FIRST, '未確認', '新キーのメモ')]);
  c.migrateOfacFirstDetectionHistory_(ss);
  const rows = ss.tabs.get('03_変更履歴').data;
  assert.equal(rows.length, 1);
  assert.equal(rows[0][6], '対応済');
  assert.match(rows[0][7], /旧キーで完了/);
  assert.match(rows[0][7], /新キーのメモ/);
  const before = JSON.stringify(rows);
  assert.equal(c.migrateOfacFirstDetectionHistory_(ss), 0);
  assert.equal(JSON.stringify(rows), before);
});

test('conflicting nondefault statuses stay visible and preserve both statuses and notes', () => {
  const c = environment();
  const ss = spreadsheet([historyRow(OLD, '対応済', '旧根拠'), historyRow(FIRST, '保留', '保留根拠')]);
  c.migrateOfacFirstDetectionHistory_(ss);
  const row = ss.tabs.get('03_変更履歴').data[0];
  assert.equal(row[6], '要確認');
  for (const text of ['対応済', '保留', '旧根拠', '保留根拠']) assert.ok(row[7].includes(text));
  const queue = c.reconcileUnresolvedQueueRows_([row], []).queueRows;
  assert.equal(queue.length, 1, 'conflicting completion must remain reviewable in 08');
  assert.equal(queue[0][6], '要確認');
});

test('deduplication applies only to the pinned corrected events', () => {
  const c = environment();
  const unrelated = historyRow('2026-11-10 16:18:45', '保留', 'unrelated');
  const ss = spreadsheet([historyRow(), unrelated, unrelated.slice()]);
  c.migrateOfacFirstDetectionHistory_(ss);
  assert.equal(ss.tabs.get('03_変更履歴').data.length, 3);
  assert.deepEqual(ss.tabs.get('03_変更履歴').data.slice(1), [unrelated, unrelated]);
});

test('already-corrected Date cell with canonical key remains untouched', () => {
  const c = environment();
  const row = historyRow(new Date('2026-11-06T17:31:25Z'), '保留', 'original');
  row[8] = key([FIRST, ...EVENT]);
  const ss = spreadsheet([row]);
  assert.equal(c.migrateOfacFirstDetectionHistory_(ss), 0);
  assert.equal(ss.tabs.get('03_変更履歴').writes, 0);
  assert.equal(ss.tabs.get('03_変更履歴').data[0][0], row[0]);
});

function parseCsv(text) {
  const rows = []; let row = [], cell = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (char === '"') {
      if (quoted && text[i + 1] === '"') {cell += '"'; i++;}
      else quoted = !quoted;
    } else if (char === ',' && !quoted) {row.push(cell); cell = '';}
    else if (char === '\n' && !quoted) {row.push(cell); rows.push(row); row = []; cell = '';}
    else if (char !== '\r' || quoted) cell += char;
  }
  if (cell || row.length) {row.push(cell); rows.push(row);}
  assert.equal(quoted, false);
  return rows;
}

test('all 107 pinned mappings exactly reproduce the committed evidence ledger and authentic 82+25 first times', () => {
  const c = environment();
  const evidence = parseCsv(fs.readFileSync('data/monitoring/ofac_promotion_history.csv', 'utf8'))
    .slice(1).filter(row => row[1] === '2026-11-09T07:18:45Z' &&
      row[8] === 'https://github.com/kenmizunokuro/sanctions-watch/actions/runs/42577795235');
  assert.equal(evidence.length, 107);
  const expected = evidence.map(row => [row[0], OLD, ...row.slice(2, 7)]);
  const pinned = Array.from(c.approvedOfacFirstDetectionCorrections_(), row => Array.from(row));
  assert.deepEqual(pinned.map(JSON.stringify).sort(), expected.map(JSON.stringify).sort());
  assert.equal(pinned.filter(row => row[0] === FIRST).length, 82);
  assert.equal(pinned.filter(row => row[0] === '2026-11-07 04:51:36').length, 25);
  const oldRows = evidence.map(row => {
    const out = [OLD, ...row.slice(2, 7), '対応中', '根拠:' + row[4], ''];
    out[8] = key(out); return out;
  });
  const ss = spreadsheet(oldRows);
  assert.equal(c.migrateOfacFirstDetectionHistory_(ss), 107);
  const corrected = ss.tabs.get('03_変更履歴').data;
  corrected.forEach((row, i) => {
    assert.deepEqual(row.slice(0, 6), [evidence[i][0], ...evidence[i].slice(2, 7)]);
    assert.equal(row[6], oldRows[i][6]);
    assert.equal(row[7], oldRows[i][7]);
    assert.equal(row[8], key(row));
  });
  assert.equal(c.migrateOfacFirstDetectionHistory_(ss), 0);
  assert.equal(ss.tabs.get('03_変更履歴').writes, 1);
});
