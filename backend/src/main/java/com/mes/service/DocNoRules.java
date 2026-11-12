package com.mes.service;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/**
 * 单据编号规则（全系统唯一口径）。
 *
 * <pre>
 *   普通单据：{前缀}-{yyyyMMdd}-{NNN}          例：SO-20261105-001
 *   批次单据：{前缀}-{yyyyMMdd}-{批号}-{NNN}    例：GX-20261105-P2026A-001
 * </pre>
 *
 * 本类只负责「规则」，不碰数据库：序号递增与查重由调用方（PxService）完成，
 * 以便编号规则能被单测覆盖，也避免规则散落在 if-else 链里。
 */
public final class DocNoRules {

    private static final DateTimeFormatter DATE = DateTimeFormatter.ofPattern("yyyyMMdd");

    /** 序号位宽：3 位，单日单类型上限 999 张。 */
    public static final int SEQ_WIDTH = 3;

    /**
     * 面板编码 → 单号前缀。
     *
     * <p>两条硬约束（均有单测守护）：
     * <ol>
     *   <li>前缀不得重复——旧实现里 BJ- 同时给了成品报检单和报价单，两张单据会撞号；</li>
     *   <li>只能是大写字母数字——单号要能被条码、外部系统、URL 安全承载。</li>
     * </ol>
     */
    private static final Map<String, String> PREFIXES;

    static {
        Map<String, String> m = new LinkedHashMap<>();
        // 销售
        m.put("QUOTE_ORDER", "QUO");     // 报价单（原 BJ-，与成品报检单撞号）
        m.put("SO_ORDER", "SO");         // 销售订单
        m.put("SALE_INV", "XS");         // 销货单
        m.put("SALE_OUT", "CK");         // 销售出库单
        m.put("SALE_RETURN", "XT");      // 销售退货单
        m.put("SALE_INVOICE", "FP");     // 销售发票
        m.put("SALE_COST_ALLOC", "FT");  // 销售费用分摊单
        // 采购
        m.put("PU_REQ", "CG");           // 请购单
        m.put("MPS_PLAN", "MPS");        // 主生产计划
        m.put("PU_ORDER", "PO");         // 采购订单
        m.put("ARRIVAL_IN", "DH");       // 到货单
        m.put("PURCHASE_IN", "RK");       // 采购入库单
        m.put("PU_IN", "PU");            // 进货单
        m.put("PURCHASE_RETURN", "CT");  // 采购退货单
        m.put("PU_INVOICE", "PI");       // 采购发票
        m.put("PU_COST_ALLOC", "PC");    // 采购费用分摊单
        m.put("EXPENSE", "FY");          // 费用单
        // 生产
        m.put("MANU_ORDER", "MO");            // 生产加工单
        m.put("PROCESS_REPORT", "GX");        // 工序汇报单
        m.put("DISPATCH", "PG");              // 工序派工单
        m.put("MATERIAL_REQ", "LL");          // 领料申请单
        m.put("MATERIAL_OUT", "CL");          // 材料出库单
        m.put("FINISH_IN", "FI");             // 产成品入库单
        m.put("TRANSFER", "DB");              // 调拨单
        // 委外
        m.put("OUTSOURCE_ORDER", "WW");  // 委外加工单
        m.put("OUTSOURCE_ISSUE", "WF");  // 委外发料单
        m.put("OUTSOURCE_IN", "WR");     // 委外入库单
        m.put("OUTSOURCE_FEE", "WY");    // 委外加工费用单
        // 质量
        m.put("FINISH_INSPECT", "BJ");          // 成品报检单
        m.put("INSPECTION", "JY");              // 来料/成品检验单
        m.put("FIRST_INSPECT", "SJ");           // 首件报检单
        m.put("PROCESS_INSPECT_APPLY", "GB");   // 工序报检单
        m.put("PROCESS_INSPECTION", "GCJ");     // 生产过程检验单
        // 仓储
        m.put("STOCK_CHECK", "PD");       // 库存盘点单
        m.put("LOCATION_ADJUST", "HW");   // 货位调整单
        m.put("SERIAL_NO", "XL");         // 序列号登记单
        m.put("PICK_ORDER", "PH");        // 配货单
        // 财务
        m.put("RECEIPT", "SK");           // 收款单
        m.put("RECEIPT_PAYABLE", "FK");   // 付款单
        // 基础 / 设计
        m.put("BOM", "BOM");              // 物料清单
        m.put("INV", "INV");              // 存货
        PREFIXES = Collections.unmodifiableMap(m);
    }

    /** 带批次的单据：单号追加批号段，便于按批次追溯与扫码。 */
    private static final Set<String> BATCH_PANELS = Set.of("MANU_ORDER", "PROCESS_REPORT");

    /** 批号段允许的字符：只接受 ASCII 编码，避免把「正常/加急」这类中文优先级写进单号。 */
    private static final String BATCH_PATTERN = "[A-Za-z0-9_-]{1,20}";

    private static final String[] BATCH_FIELDS = {"批次号", "批号"};

    private DocNoRules() {
    }

    /** 全部「面板编码 → 前缀」映射，供唯一性与字符集自检。 */
    public static Map<String, String> prefixes() {
        return PREFIXES;
    }

    public static boolean isBatchPanel(String panelCode) {
        return panelCode != null && BATCH_PANELS.contains(panelCode);
    }

    /**
     * 解析前缀。未登记面板按编码推导，<b>绝不兜底成 MO-</b>（旧实现让任何新面板都叫 MO-，
     * 与生产加工单直接撞号）。
     */
    public static String prefixOf(String panelCode) {
        if (panelCode == null || panelCode.isBlank()) return "GEN";
        String mapped = PREFIXES.get(panelCode);
        if (mapped != null) return mapped;
        String letters = panelCode.toUpperCase().replaceAll("[^A-Z0-9]", "");
        if (letters.isEmpty()) return "GEN";
        String derived = letters.length() <= 4 ? letters : letters.substring(0, 4);
        if (derived.length() < 2) derived = (derived + "XX").substring(0, 2);
        // 推导结果若与加工单前缀相同，继续退让，保证不撞 MO
        if ("MO".equals(derived)) derived = derived + "X";
        return derived;
    }

    /**
     * 从表头取批号段。只接受 ASCII 编码；取不到或格式不合法时返回空串（单号不带批次段）。
     * 这样「批号=正常/加急」的历史数据不会污染单号。
     */
    public static String batchSegment(Map<String, Object> data) {
        if (data == null) return "";
        for (String field : BATCH_FIELDS) {
            Object raw = data.get(field);
            if (raw == null) continue;
            String value = String.valueOf(raw).trim();
            if (value.isEmpty()) continue;
            if (!value.matches(BATCH_PATTERN)) continue;
            return value;
        }
        return "";
    }

    /** 拼装单号。批次段为空时自动省略，不产生 `--` 之类的脏格式。 */
    public static String format(String prefix, LocalDate date, String batch, int seq) {
        LocalDate d = date == null ? LocalDate.now() : date;
        String datePart = d.format(DATE);
        StringBuilder sb = new StringBuilder(prefix).append('-').append(datePart);
        // 批号等于当天日期时不再重复：批次是按天生成的，单号里已经有日期了，
        // 否则会出现 GX-20261105-20261105-001 这种重复段。
        if (batch != null && !batch.isBlank() && !batch.equals(datePart)) sb.append('-').append(batch);
        sb.append('-').append(String.format("%0" + SEQ_WIDTH + "d", seq));
        return sb.toString();
    }
}