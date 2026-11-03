/**
 * 主从视图的父件行来源：把一个列表页里所有单据的明细行摊平成一份父件清单。
 *
 * 用途：物料清单（BOM）只读浏览时，不再靠「第 N 张单据」翻页，而是把所有 BOM 的
 * 父件行放到同一张表里，点哪行就在下方显示那一行的子件。行上带 `_docNo` 记住
 * 它属于哪张单据，点行时把当前单据同步过去，表头的「单据：xxx」才不会错。
 */

/**
 * @param {Array} docs 列表接口返回的单据行
 * @param {string} tabKey 明细页签键（BOM 为 children，工序类为 processes…）
 * @returns {Array} 摊平后的明细行，每行多一个 `_docNo`
 */
export function flattenDocDetailRows(docs, tabKey) {
  if (!Array.isArray(docs) || !tabKey) return [];
  const out = [];
  for (const doc of docs) {
    if (!doc || typeof doc !== "object") continue;
    const detail = doc.detail;
    const rows = detail && Array.isArray(detail[tabKey]) ? detail[tabKey] : null;
    if (!rows || !rows.length) continue;
    const docNo = doc["编号"] || doc["单据编号"] || doc.form_no || "";
    for (const row of rows) {
      if (!row || typeof row !== "object") continue;
      out.push({ ...row, _docNo: docNo });
    }
  }
  return out;
}
