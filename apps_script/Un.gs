/* UN review projection. A local memo never approves a snapshot or delisting. */
function unRowsForSheet_(incoming, existing) {
  const memo = new Map(existing.map(r => [String(r[0]), r[11] || '']));
  const seen = new Set();
  return incoming.map(row => {
    if (row.length !== 11 || !row[0] || seen.has(String(row[0]))) {
      throw new Error('国連レビューCSVの列またはイベントIDが不正');
    }
    const id = String(row[0]);
    seen.add(id);
    return row.map(mofaTextCell_).concat(mofaTextCell_(memo.get(id) || ''));
  });
}
function ensureUnStructure_(ss) {
  const sheet = ss.getSheetByName(TAB.UN) || ss.insertSheet(TAB.UN);
  ensureColumns_(sheet, 12);
  sheet.getRange('A1').setValue('国連制裁レビュー');
  sheet.getRange('A2').setValue('公式XML・委員会発表の候補。メモは承認ではありません。名簿反映はGitHub側で原本ハッシュを指定して承認・適用します。指定船舶は統合XMLの対象外。');
  sheet.getRange(4, 1, 1, 12).setValues([EXPECTED.unReviews.concat('メモ')]);
  sheet.setFrozenRows(4);
}
function syncUnReviews_(ss, incoming) {
  const sheet = requireSheet_(ss, TAB.UN);
  const oldCount = Math.max(0, sheet.getLastRow() - 4);
  const existing = oldCount ? sheet.getRange(5, 1, oldCount, 12).getValues() : [];
  const rows = unRowsForSheet_(incoming, existing);
  const count = Math.max(oldCount, rows.length);
  if (!count) return;
  ensureRows_(sheet, 4 + count);
  const padded = rows.concat(Array.from({length: count - rows.length}, () => Array(12).fill('')));
  sheet.getRange(5, 1, count, 12).setNumberFormat('@').setValues(padded);
}
