-- =====================================================================
-- 2026-11-06 历史 AP 清理：票到确认口径上线后的重复应付清理
-- ---------------------------------------------------------------------
-- 背景：票到确认（2026-11-06）之前，采购入库（PURCHASE_IN）审核即生成 AP 应付，
--       采购发票（PU_INVOICE）审核又生成一次应付，形成「入库 + 发票」双记。
--       新口径只由采购发票 / 委外入库 / 委外费用生成应付，采购入库只落库存。
--
-- 使用前提（缺一不可）：
--   1) 新口径代码已上线，并验证过「采购入库不再生成 AP、采购发票审核生成 AP」；
--   2) 已执行第 1 段备份，且备份表行数与源表一致；
--   3) 第 2 段统计中 settled_amount <> 0 或存在核销记录的旧 AP，必须先人工核对：
--      已实际付款核销的旧 AP 直接删除会丢失核销痕迹，需与财务确认保留/冲销方式后再执行第 3 段。
--
-- 幂等与可审计：
--   - 备份表用 IF NOT EXISTS，首次执行后不再覆盖，保全原始快照；
--   - 删除条件只针对 PURCHASE_IN 来源，重复执行不会误删 PU_INVOICE / PURCHASE_RETURN 记录；
--   - 不删除 PU_INVOICE、PURCHASE_RETURN 的往来单据与会计分录。
-- 本脚本不自动执行：必须由运维/财务在目标库确认后手工运行。
-- =====================================================================

SET NAMES utf8mb4;

-- ---------- 1. 先备份（DDL 会隐式提交，必须放在事务之前） ----------
CREATE TABLE IF NOT EXISTS finance_voucher_backup_20261106 AS SELECT * FROM finance_voucher;
CREATE TABLE IF NOT EXISTS accounting_entry_backup_20261106 AS SELECT * FROM accounting_entry;

SELECT 'finance_voucher 备份' AS 检查项, COUNT(*) AS 行数 FROM finance_voucher_backup_20261106
UNION ALL
SELECT 'finance_voucher 源表' AS 检查项, COUNT(*) AS 行数 FROM finance_voucher
UNION ALL
SELECT 'accounting_entry 备份' AS 检查项, COUNT(*) AS 行数 FROM accounting_entry_backup_20261106
UNION ALL
SELECT 'accounting_entry 源表' AS 检查项, COUNT(*) AS 行数 FROM accounting_entry;

-- ---------- 2. 清理前统计（留档，便于审计） ----------
-- 2.1 PURCHASE_IN 来源 AP：本次要删除的重复应付
SELECT 'PURCHASE_IN 来源 AP' AS 统计项, COUNT(*) AS 笔数,
       COALESCE(SUM(amount), 0) AS 金额,
       COALESCE(SUM(settled_amount), 0) AS 已核销金额
FROM finance_voucher
WHERE voucher_type = 'AP' AND source_panel = 'PURCHASE_IN';

-- 2.2 PU_INVOICE 来源 AP：票到确认后的正确应付，保留
SELECT 'PU_INVOICE 来源 AP' AS 统计项, COUNT(*) AS 笔数,
       COALESCE(SUM(amount), 0) AS 金额,
       COALESCE(SUM(settled_amount), 0) AS 已核销金额
FROM finance_voucher
WHERE voucher_type = 'AP' AND source_panel = 'PU_INVOICE';

-- 2.3 accounting_entry 中 PURCHASE_IN 来源的分录
SELECT 'PURCHASE_IN 来源会计分录' AS 统计项, COUNT(*) AS 分录笔数,
       COALESCE(SUM(amount), 0) AS 金额
FROM accounting_entry
WHERE source_panel = 'PURCHASE_IN';

-- 2.4 待删除旧 AP 中已核销或存在核销记录的单据清单：先人工核对，再执行第 3 段
SELECT v.id AS 往来单据ID, v.partner AS 往来单位, v.source_no AS 来源单号,
       v.amount AS 原始金额, v.settled_amount AS 已核销金额, v.status AS 结算状态,
       COUNT(w.id) AS 核销记录数
FROM finance_voucher v
LEFT JOIN finance_write_off w ON w.voucher_id = v.id
WHERE v.voucher_type = 'AP' AND v.source_panel = 'PURCHASE_IN'
GROUP BY v.id, v.partner, v.source_no, v.amount, v.settled_amount, v.status
HAVING v.settled_amount <> 0 OR COUNT(w.id) > 0;

-- ---------- 3. 删除重复 AP（事务包裹，失败整体回滚） ----------
START TRANSACTION;

-- 3.1 删除「票到确认」前由采购入库产生的重复应付
DELETE FROM finance_voucher
WHERE voucher_type = 'AP' AND source_panel = 'PURCHASE_IN';

-- 3.2 清理失去往来单据的核销记录（只删除已无对应 AP 的孤儿行）
DELETE FROM finance_write_off
WHERE voucher_id NOT IN (SELECT id FROM finance_voucher);

-- 3.3 删除采购入库对应的会计凭证分录（新口径由采购发票分录替代）
DELETE FROM accounting_entry
WHERE source_panel = 'PURCHASE_IN';

COMMIT;

-- ---------- 4. 清理后验证 ----------
-- 4.1 剩余 AP 必须只来自 PU_INVOICE / OUTSOURCE_IN / OUTSOURCE_FEE 等有效来源
SELECT source_panel AS 来源面板, voucher_type AS 往来类型, COUNT(*) AS 笔数,
       COALESCE(SUM(amount), 0) AS 金额,
       COALESCE(SUM(settled_amount), 0) AS 已核销金额
FROM finance_voucher
WHERE voucher_type LIKE 'AP%'
GROUP BY source_panel, voucher_type
ORDER BY source_panel, voucher_type;

-- 4.2 断言口径：PURCHASE_IN 来源 AP 残留必须为 0
SELECT COUNT(*) AS 残留_PURCHASE_IN_AP
FROM finance_voucher
WHERE voucher_type = 'AP' AND source_panel = 'PURCHASE_IN';

-- 4.3 核销记录必须全部能关联到往来单据
SELECT COUNT(*) AS 孤儿核销记录
FROM finance_write_off
WHERE voucher_id NOT IN (SELECT id FROM finance_voucher);
