/* MOF source-diff previews are not approvals and cannot update master. */
// Map the CSV + review columns to the review view. Columns J:S hold evidence.
const MOF_PENDING_COLUMN_ORDER = [1, 2, 3, 4, 7, 8, 9, 17, 18, 0, 5, 6, 10, 11, 12, 13, 14, 15, 16];
const MOF_PENDING_KINDS = new Map([
  ['原本レコード追加', {label: '追加候補', color: '#d9ead3', order: 0}],
  ['情報改訂', {label: '変更候補', color: '#fff2cc', order: 1}],
  ['原本レコード削除', {label: '削除候補', color: '#f4cccc', order: 2}],
]);

function mofReviewTextCell_(value) {
  const text = String(value == null ? '' : value);
  return /^\s*[=+@-]/.test(text) ? "'" + text : text;
}
function mofCandidateCells_(incoming, old, width, prefix, allowedState) {
  const remembered = new Map(old.map(row =>
    [String(row[0] || ''), [row[width] || '未確認', row[width + 1] || '']]));
  const seen = new Set();
  return incoming.map(row => {
    if (!Array.isArray(row) || row.length !== width) {
      throw new Error('財務省レビューCSVの列数が想定と異なる');
    }
    const id = String(row[0] || '');
    if (!id.startsWith(prefix) || seen.has(id)) {
      throw new Error('財務省レビュー候補IDの欠落・重複');
    }
    if (String(row[prefix === 'MOF-PENDING-' ? 15 : 2]) !== allowedState) {
      throw new Error('財務省レビューの未承認状態が不正');
    }
    seen.add(id);
    const user = remembered.get(id) || ['未確認', ''];
    return row.map(mofReviewTextCell_).concat(
      mofReviewTextCell_(user[0]), mofReviewTextCell_(user[1]));
  });
}

/*
 * Never call insertColumnsAfter on a populated legacy sheet: resizing the
 * entire grid (possibly many thousands of rows) can time out. Preserve it
 * and build a fresh, normally pre-sized Sheet instead.
 *
 * The recovery name is stable across retries. We deliberately do NOT rename
 * or delete the old sheet; that would destroy the audit/review notes.
 */
function mofReviewTargetSheet_(ss, tab, width) {
  const original = ss.getSheetByName(tab);
  const required = width + 2;
  if (!original) {
    const created = ss.insertSheet(tab);
    if (created.getMaxColumns() < required || created.getMaxRows() < 4) {
      throw new Error(tab + ' を作成しましたが列/行数が足りません。'
        + 'Google Sheets側のグリッドを確認してください。');
    }
    return created;
  }
  if (original.getMaxColumns() >= required && original.getMaxRows() >= 4) {
    return original;
  }
  // Leave the original exactly as it was. A fresh Sheet normally has 26
  // columns, so a slow insertColumnsAfter on the old grid is unnecessary.
  const backupTab = tab + '_復旧';
  const replacement = ss.getSheetByName(backupTab) || ss.insertSheet(backupTab);
  if (replacement.getMaxColumns() < required || replacement.getMaxRows() < 4) {
    throw new Error('復旧シート ' + backupTab + ' の列/行が不足。'
      + '自動列追加は行いません。会社のシート管理者に確認してください。');
  }
  Logger.log('財務省レビュー: ' + tab + ' の列数が ' +
             original.getMaxColumns() + ' のため、旧タブを保存して ' +
             backupTab + ' に表示します。');
  return replacement;
}

/* Read only populated rows. Never discard pre-existing reviewer notes. */
function mofExistingRows_(sheet, width, pending) {
  if (!sheet) return [];
  const count = Math.max(0, sheet.getLastRow() - 4);
  if (!count) return [];
  if (count > 5000) {
    throw new Error('財務省レビュータブ ' + sheet.getName() +
      ' に5000行を超える既存データがあります。上書きを停止します。');
  }
  const cols = Math.min(width + 2, sheet.getMaxColumns());
  if (cols < 1) throw new Error('財務省レビュータブの列数が0です');
  const rows = sheet.getRange(4, 1, count + 1, cols).getValues();
  const headers = rows.shift();
  const reordered = pending && headers[MOF_PENDING_COLUMN_ORDER.indexOf(0)] === '候補ID';
  return rows.map(row => {
    const padded = row.concat(Array(width + 2 - cols).fill(''));
    if (!reordered) return padded;
    const original = Array(width + 2);
    MOF_PENDING_COLUMN_ORDER.forEach((csvIndex, viewIndex) => {
      original[csvIndex] = padded[viewIndex];
    });
    return original;
  });
}

function ensureMofCandidateStructure_(ss) {
  [
    [TAB.MOF_PENDING, EXPECTED.mofPending, '財務省：原本差分候補',
     'master未反映のレビュー用。「削除候補」は財務省原本からの削除で、指定解除の確定ではありません。J列以降を再表示すると原本URLなどの証跡を確認できます。'],
    [TAB.MOF_LEGACY, EXPECTED.mofLegacy, '財務省：旧登録の出典変更',
     '既存の旧登録と更新候補の出典差分です。財務省側の掲載終了と、OFAC等での指定継続を区別してください。'],
  ].forEach(item => {
    const [tab, headers, title, warning] = item;
    try {
      const sheet = mofReviewTargetSheet_(ss, tab, headers.length);
      // Do not call ensureColumns_/insertColumnsAfter. The target is already
      // wide enough and only a few header cells need to be written.
      sheet.getRange('A1').setValue(title);
      sheet.getRange('A2').setValue(warning);
      // The pending view writes its header together with its reordered rows,
      // so the next retry can always locate existing notes by candidate ID.
      if (tab !== TAB.MOF_PENDING) {
        sheet.getRange(4, 1, 1, headers.length + 2)
          .setValues([headers.concat('確認状況', 'メモ')]);
      }
      sheet.setFrozenRows(4);
    } catch (err) {
      throw new Error('財務省レビュー構造確認 [' + tab + ']: ' +
        String(err && err.stack || err));
    }
  });
}

function syncMofCandidateSheet_(ss, tab, incoming, fields, prefix, state) {
  if (incoming === null) return;
  const width = fields.length;
  const sheet = mofReviewTargetSheet_(ss, tab, width);
  const original = ss.getSheetByName(tab);
  // Existing notes in the old undersized sheet are considered first;
  // notes already written to the recovery tab take precedence.
  const pending = tab === TAB.MOF_PENDING;
  const old = original && original !== sheet
    ? mofExistingRows_(original, width, pending) : [];
  const existing = old.concat(mofExistingRows_(sheet, width, pending));
  const result = mofCandidateCells_(incoming, existing, width, prefix, state);
  const oldCount = Math.max(0, sheet.getLastRow() - 4);
  const count = Math.max(oldCount, result.length);
  if (pending) {
    writeMofPendingView_(sheet, fields, result, count);
    return;
  }
  if (!count) return;
  ensureRows_(sheet, 4 + count);
  const padded = result.concat(
    Array.from({length: count - result.length},
      () => Array(width + 2).fill('')));
  sheet.getRange(5, 1, count, width + 2)
    .setNumberFormat('@').setValues(padded);
}

function writeMofPendingView_(sheet, fields, rows, count) {
  const columns = MOF_PENDING_COLUMN_ORDER;
  const visible = columns.indexOf(0);
  const headers = fields.concat('確認状況', 'メモ');
  headers[1] = '検知日時（JST）';
  const counts = new Map();
  const ordered = rows.map(row => {
    const kind = MOF_PENDING_KINDS.get(row[3]);
    counts.set(row[3], (counts.get(row[3]) || 0) + 1);
    return {row, kind, timestamp: Date.parse(row[1])};
  });
  ordered.sort((a, b) => b.timestamp - a.timestamp
    || (a.kind?.order ?? 3) - (b.kind?.order ?? 3)
    || a.row[4].localeCompare(b.row[4], 'ja'));

  const values = [columns.map(index => headers[index])];
  const colors = [];
  ordered.forEach(({row, kind, timestamp}) => {
    const viewRow = columns.map(index => row[index]);
    if (Number.isFinite(timestamp)) viewRow[0] = formatJst_(new Date(timestamp));
    if (kind) viewRow[2] = kind.label;
    values.push(viewRow);
    colors.push([kind?.color || '#ffffff']);
  });
  for (let i = rows.length; i < count; i++) {
    values.push(Array(columns.length).fill(''));
    colors.push(['#ffffff']);
  }

  ensureRows_(sheet, 4 + count);
  sheet.getRange(4, 1, values.length, columns.length)
    .setNumberFormat('@').setValues(values);
  const summary = Array.from(MOF_PENDING_KINDS, ([key, kind]) =>
    kind.label + ' ' + (counts.get(key) || 0) + '行');
  sheet.getRange('A3').setValue('候補 ' + rows.length + '行（' + summary.join(' / ') + '）');
  sheet.getRange(4, 1, 1, visible)
    .setBackground('#1f4e78').setFontColor('#ffffff').setFontWeight('bold');
  if (count) {
    sheet.getRange(5, 4, count, visible - 3).setWrap(true).setVerticalAlignment('top');
    sheet.getRange(5, 3, count, 1).setBackgrounds(colors);
  }
  [155, 70, 105, 220, 125, 245, 245, 105, 175].forEach((pixels, i) =>
    sheet.setColumnWidth(i + 1, pixels));
  sheet.showColumns(1, visible);
  sheet.hideColumns(visible + 1, columns.length - visible);
}

/*
 * Safe, small, manual review-only sync. This entrypoint intentionally does
 * NOT call syncAll_, ensureStructure_, the 5,000-row history/queue rebuild,
 * the audit or anomaly sheets, or any master-import function.
 *
 * Production master and screening files remain untouched. "確認状況" and
 * "メモ" are local working notes, not a sanctions approval.
 */
function syncMofReviewOnly() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(LOCK_WAIT_MS)) {
    throw new Error('財務省レビュー専用同期: 他の同期実行中です。多重実行せず、実行履歴を確認してください。');
  }
  const props = PropertiesService.getScriptProperties();
  let stage = '開始';
  const counts = {};
  try {
    props.setProperty('MOF_REVIEW_ATTEMPT_AT', new Date().toISOString());
    props.setProperty('MOF_REVIEW_STATUS', 'RUNNING');

    stage = '財務省差分CSV・出典変更CSVを取得';
    const metrics = {http200: 0, http304: 0, bytes: 0, retries: 0};
    const feeds = fetchCsvBatch_([
      {key: 'mof_pending', url: MOF_PENDING_URL,
       expected: EXPECTED.mofPending, optional: false, conditional: false},
      {key: 'mof_legacy', url: MOF_LEGACY_URL,
       expected: EXPECTED.mofLegacy, optional: false, conditional: false},
    ], metrics);

    if (feeds.mof_pending.notModified || feeds.mof_legacy.notModified) {
      throw new Error('財務省レビューCSVの検証前に304応答。同期を停止します。');
    }
    // Validate source rows BEFORE touching any sheet. A malformed source
    // must never be acknowledged as a successful "zero changes" sync.
    mofCandidateCells_(feeds.mof_pending.rows, [], EXPECTED.mofPending.length,
                       'MOF-PENDING-', '要レビュー・master未反映');
    mofCandidateCells_(feeds.mof_legacy.rows, [], EXPECTED.mofLegacy.length,
                       'MOF-LEGACY-', 'REVIEW_REQUIRED');

    stage = '対象スプレッドシートを開く';
    const ss = getSpreadsheet_();
    stage = '財務省レビュー専用2タブの構造確認';
    ensureMofCandidateStructure_(ss);

    stage = '12_財務省差分候補へ反映';
    syncMofCandidateSheet_(ss, TAB.MOF_PENDING, feeds.mof_pending.rows,
                           EXPECTED.mofPending, 'MOF-PENDING-',
                           '要レビュー・master未反映');
    counts.pending = feeds.mof_pending.rows.length;

    stage = '13_財務省出典変更へ反映';
    syncMofCandidateSheet_(ss, TAB.MOF_LEGACY, feeds.mof_legacy.rows,
                           EXPECTED.mofLegacy, 'MOF-LEGACY-', 'REVIEW_REQUIRED');
    counts.legacy = feeds.mof_legacy.rows.length;

    stage = '2タブの書き込みを確定';
    SpreadsheetApp.flush();

    props.setProperty('MOF_REVIEW_STATUS', 'SUCCESS');
    props.setProperty('MOF_REVIEW_LAST_SUCCESS_AT', new Date().toISOString());
    props.deleteProperty('MOF_REVIEW_LAST_ERROR');
    props.deleteProperty('MOF_REVIEW_LAST_FAILED_STAGE');
    Logger.log('財務省レビュー同期成功：原本差分候補=' + counts.pending +
               '行、出典変更=' + counts.legacy + '行。master未更新。');
    return counts;
  } catch (err) {
    const reason = String(err && err.stack || err);
    props.setProperty('MOF_REVIEW_STATUS', 'ERROR');
    props.setProperty('MOF_REVIEW_LAST_FAILED_STAGE', stage);
    props.setProperty('MOF_REVIEW_LAST_ERROR', reason.slice(0, 1500));
    Logger.log('財務省レビュー同期失敗 [' + stage + '] ' + reason);
    throw new Error('財務省レビュー専用同期 [' + stage + '] ' + reason);
  } finally {
    lock.releaseLock();
  }
}
