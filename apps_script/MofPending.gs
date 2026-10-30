/* MOF source-diff previews are not approvals and cannot update master. */
function mofReviewTextCell_(value) {
  const text = String(value == null ? '' : value);
  return /^[\\s]*[=+@-]/.test(text) ? "'" + text : text;
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

function ensureMofCandidateStructure_(ss) {
  [
    [TAB.MOF_PENDING, EXPECTED.mofPending, '財務省：原本差分候補',
     '取得済み原本の追加・削除・情報改訂候補です。「削除」は指定解除の確定ではありません。手動確認後もこのシートからmasterへの自動反映はしません。'],
    [TAB.MOF_LEGACY, EXPECTED.mofLegacy, '財務省：旧登録の出典変更',
     '既存の旧登録と更新候補の出典差分です。財務省側の掲載終了と、OFAC等での指定継続を区別してください。'],
  ].forEach(item => {
    const [tab, headers, title, warning] = item;
    const sheet = ss.getSheetByName(tab) || ss.insertSheet(tab);
    ensureColumns_(sheet, headers.length + 2);
    sheet.getRange('A1').setValue(title);
    sheet.getRange('A2').setValue(warning);
    sheet.getRange(4, 1, 1, headers.length + 2)
      .setValues([headers.concat('確認状況', 'メモ')]);
    sheet.setFrozenRows(4);
  });
}

function syncMofCandidateSheet_(ss, tab, incoming, fields, prefix, state) {
  if (incoming === null) return;
  const sheet = requireSheet_(ss, tab);
  const width = fields.length;
  const last = sheet.getLastRow();
  const oldCount = Math.max(0, last - 4);
  const existing = oldCount
    ? sheet.getRange(5, 1, oldCount, width + 2).getValues() : [];
  // Validate every new row and stable event identifier BEFORE modifying cells.
  const result = mofCandidateCells_(incoming, existing, width, prefix, state);
  const count = Math.max(oldCount, result.length);
  if (!count) return;
  ensureRows_(sheet, 4 + count);
  const padded = result.concat(
    Array.from({length: count - result.length},
      () => Array(width + 2).fill('')));
  sheet.getRange(5, 1, count, width + 2)
    .setNumberFormat('@').setValues(padded);
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
