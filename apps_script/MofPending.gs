/* MOF source-diff previews are not approvals and cannot update master. */
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
    return row.map(mofaTextCell_).concat(
      mofaTextCell_(user[0]), mofaTextCell_(user[1]));
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
