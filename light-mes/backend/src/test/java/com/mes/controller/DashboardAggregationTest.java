package com.mes.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.StockBalance;
import com.mes.entity.StockTxn;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 桌面看板的分模块聚合。
 *
 * 这些方法是纯函数（入参是已查出的单据/余额/流水，不碰数据库），
 * 用真实字段名（中文数据键）构造样本，防止改了键名或字段名后图表静默变空。
 */
class DashboardAggregationTest {

    private static final ObjectMapper JSON = new ObjectMapper();

    private FormData doc(String panel, String no, String status, Map<String, Object> head,
                         List<Map<String, Object>> items) throws Exception {
        FormData fd = new FormData();
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        fd.setData(JSON.writeValueAsString(head));
        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("items", items);
        fd.setDetailData(JSON.writeValueAsString(detail));
        fd.setCreateTime(LocalDateTime.of(2026, 11, 1, 9, 0));
        return fd;
    }

    private Map<String, Object> head(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put(String.valueOf(kv[i]), kv[i + 1]);
        return m;
    }

    private Map<String, Object> item(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put(String.valueOf(kv[i]), kv[i + 1]);
        return m;
    }

    @SuppressWarnings("unchecked")
    private static int intOf(Map<String, Object> m, String key) {
        Object v = m.get(key);
        return v instanceof Number ? ((Number) v).intValue() : 0;
    }

    @SuppressWarnings("unchecked")
    private static double numOf(Map<String, Object> m, String key) {
        Object v = m.get(key);
        return v instanceof Number ? ((Number) v).doubleValue() : 0;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOf(Map<String, Object> m, String key) {
        Object v = m.get(key);
        assertTrue(v instanceof List, key + " 应该是数组，实际：" + v);
        return (List<Map<String, Object>>) v;
    }

    @SuppressWarnings("unchecked")
    private static double valueOf(List<Map<String, Object>> rows, String name) {
        for (Map<String, Object> row : rows) {
            if (name.equals(row.get("name"))) return ((Number) row.get("value")).doubleValue();
        }
        return 0;
    }

    /** 采购：单量、含税金额、供应商分布、物料采购量、到货与入库单量。 */
    @Test
    void aggregatesPurchaseOrdersBySupplierAndMaterial() throws Exception {
        List<FormData> orders = List.of(
                doc("PU_ORDER", "CG-001", "已审核",
                        head("供应商", "华东铝业", "单据状态", "已审核"),
                        List.of(item("物料名称", "6063铝棒", "数量", 100, "含税金额", 1200),
                                item("物料名称", "切削液", "数量", 5, "含税金额", 300))),
                doc("PU_ORDER", "CG-002", "草稿",
                        head("供应商", "华南钢构", "单据状态", "草稿"),
                        List.of(item("物料名称", "6063铝棒", "数量", 50, "含税金额", 600))));
        List<FormData> arrivals = List.of(doc("ARRIVAL_IN", "DH-001", "已审核", head("供应商", "华东铝业"), List.of(item("报检数量", 100))));
        List<FormData> ins = List.of(doc("PURCHASE_IN", "RK-001", "已审核", head("供应商", "华东铝业"), List.of(item("实收数量", 100))));

        Map<String, Object> p = DashboardController.aggregatePurchase(orders, arrivals, ins);

        assertEquals(2, intOf(p, "total"), "采购订单数");
        assertEquals(2100.0, numOf(p, "amount"), 0.001, "含税金额合计");
        assertEquals(1, intOf(p, "arrivalTotal"), "到货单数");
        assertEquals(1, intOf(p, "inTotal"), "入库单数");
        assertEquals(1, valueOf(listOf(p, "bySupplier"), "华东铝业"), 0.001, "供应商订单数");
        assertEquals(1, valueOf(listOf(p, "bySupplier"), "华南钢构"), 0.001, "供应商订单数");
        // 6063铝棒 100+50=150 件，切削液 5
        assertEquals(150, valueOf(listOf(p, "topItems"), "6063铝棒"), 0.001, "物料采购量");
        assertEquals(5, valueOf(listOf(p, "topItems"), "切削液"), 0.001, "物料采购量");
        assertEquals(7, listOf(p, "trend7").size(), "近 7 天趋势固定 7 个点");
    }

    /** 设计/PDM：档案覆盖计数 + BOM 子件数排行。 */
    @Test
    void aggregatesDesignArchivesAndBomSize() throws Exception {
        FormData bomWide = new FormData();
        bomWide.setPanelCode("BOM");
        bomWide.setFormNo("BOM-001");
        bomWide.setStatus("启用");
        bomWide.setData("{}");
        bomWide.setDetailData(JSON.writeValueAsString(Map.of("children", List.of(
                Map.of("父件编码", "CP001", "父件名称", "铝型材-散热片", "子件编码", "YL001"),
                Map.of("父件编码", "CP001", "父件名称", "铝型材-散热片", "子件编码", "YL002"),
                Map.of("父件编码", "CP001", "父件名称", "铝型材-散热片", "子件编码", "YL003")))));

        FormData bomNarrow = new FormData();
        bomNarrow.setPanelCode("BOM");
        bomNarrow.setFormNo("BOM-002");
        bomNarrow.setStatus("启用");
        bomNarrow.setData("{}");
        bomNarrow.setDetailData(JSON.writeValueAsString(Map.of("children", List.of(
                Map.of("父件编码", "CP002", "父件名称", "减速箱体 A", "子件编码", "YL001")))));

        List<FormData> routes = List.of(doc("ROUTE", "GY-001", "启用", head(), List.of()));
        List<FormData> ops = List.of(doc("OP", "GX-001", "启用", head(), List.of()),
                doc("OP", "GX-002", "启用", head(), List.of()));
        List<FormData> invs = List.of(doc("INV", "INV-001", "启用", head(), List.of()));

        Map<String, Object> d = DashboardController.aggregateDesign(
                List.of(bomNarrow, bomWide), routes, ops, invs);

        assertEquals(2, intOf(d, "bom"), "BOM 张数");
        assertEquals(1, intOf(d, "route"), "工艺路线数");
        assertEquals(2, intOf(d, "op"), "工序数");
        assertEquals(1, intOf(d, "inv"), "存货档案数");
        List<Map<String, Object>> top = listOf(d, "bomTop");
        assertEquals("铝型材-散热片", top.get(0).get("name"), "子件最多的 BOM 排第一");
        assertEquals(3, ((Number) top.get(0).get("value")).intValue(), "该 BOM 子件数");
    }

    /** 库存：仓库结存 + 近 7 天出入库流水。 */
    @Test
    void aggregatesStockBalanceByWarehouseAndTxnTrend() {
        StockBalance a = new StockBalance();
        a.setWarehouse("原料仓");
        a.setItemCode("YL001");
        a.setQuantity(new BigDecimal("100"));
        a.setAmount(new BigDecimal("5000"));
        StockBalance b = new StockBalance();
        b.setWarehouse("原料仓");
        b.setItemCode("YL002");
        b.setQuantity(new BigDecimal("50"));
        b.setAmount(new BigDecimal("1000"));
        StockBalance c = new StockBalance();
        c.setWarehouse("成品仓");
        c.setItemCode("CP001");
        c.setQuantity(new BigDecimal("20"));
        c.setAmount(new BigDecimal("8000"));

        StockTxn in = new StockTxn();
        in.setDirection("IN");
        in.setQuantity(new BigDecimal("120"));
        in.setAmount(new BigDecimal("6000"));
        in.setBizDate(LocalDate.now());
        StockTxn out = new StockTxn();
        out.setDirection("OUT");
        out.setQuantity(new BigDecimal("30"));
        out.setAmount(new BigDecimal("1500"));
        out.setBizDate(LocalDate.now());

        Map<String, Object> s = DashboardController.stockExtra(List.of(a, b, c), List.of(in, out));

        assertEquals(3, intOf(s, "balanceRows"), "结存行数");
        assertEquals(170.0, numOf(s, "balanceQty"), 0.001, "结存数量合计");
        assertEquals(14000.0, numOf(s, "balanceAmount"), 0.001, "结存金额合计");
        assertEquals(150.0, valueOf(listOf(s, "warehouseQty"), "原料仓"), 0.001, "原料仓结存");
        assertEquals(20.0, valueOf(listOf(s, "warehouseQty"), "成品仓"), 0.001, "成品仓结存");
        List<Map<String, Object>> trend = listOf(s, "txnTrend");
        assertEquals(7, trend.size(), "近 7 天流水固定 7 个点");
        Map<String, Object> today = trend.get(trend.size() - 1);
        assertEquals(120.0, ((Number) today.get("in")).doubleValue(), 0.001, "今日入库量");
        assertEquals(30.0, ((Number) today.get("out")).doubleValue(), 0.001, "今日出库量");
    }

    /** 销售：近 7 天订单趋势 + 客户金额排行。 */
    @Test
    void aggregatesSalesTrendAndCustomerAmount() throws Exception {
        FormData o1 = doc("SO_ORDER", "SO-001", "已审核", head("客户", "华东机械"),
                List.of(item("金额", 3000), item("金额", 1000)));
        FormData o2 = doc("SO_ORDER", "SO-002", "草稿", head("客户", "南方电器"),
                List.of(item("金额", 8000)));

        Map<String, Object> s = DashboardController.salesExtra(List.of(o1, o2));

        List<Map<String, Object>> trend = listOf(s, "trend7");
        assertEquals(7, trend.size(), "近 7 天趋势固定 7 个点");
        List<Map<String, Object>> top = listOf(s, "topCustomers");
        assertEquals("南方电器", top.get(0).get("name"), "金额高的客户排第一");
        assertEquals(8000.0, ((Number) top.get(0).get("value")).doubleValue(), 0.001, "客户金额");
        assertEquals(4000.0, valueOf(top, "华东机械"), 0.001, "同客户多行金额累加");
    }

    /** 生产：报工数量汇总（报工 / 合格 / 不合格）。 */
    @Test
    void aggregatesProcessReportQuantities() throws Exception {
        List<FormData> reports = List.of(
                doc("PROCESS_REPORT", "GX-001", "已审核", head(), List.of(
                        item("报工数量", 340, "合格数量", 330, "不合格数量", 10),
                        item("报工数量", 160, "合格数量", 158, "不合格数量", 2))));

        Map<String, Object> p = DashboardController.processReportTotals(reports);

        assertEquals(1, intOf(p, "docCount"), "汇报单数");
        assertEquals(500.0, numOf(p, "reportQty"), 0.001, "报工数量合计");
        assertEquals(488.0, numOf(p, "passQty"), 0.001, "合格数量合计");
        assertEquals(12.0, numOf(p, "failQty"), 0.001, "不合格数量合计");
    }

    /** 质量：不合格处理方式分布（来自 unqualified 明细）。 */
    @Test
    void aggregatesUnqualifiedTreatmentMethods() throws Exception {
        FormData fd = new FormData();
        fd.setPanelCode("INSPECTION");
        fd.setFormNo("JY-001");
        fd.setStatus("已审核");
        fd.setData("{}");
        fd.setDetailData(JSON.writeValueAsString(Map.of(
                "items", List.of(item("检验结果判定", "让步接收")),
                "unqualified", List.of(
                        item("处理方式", "让步接收", "处理数量", 3),
                        item("处理方式", "让步接收", "处理数量", 2),
                        item("处理方式", "报废", "处理数量", 1)))));

        List<Map<String, Object>> byMethod = DashboardController.unqualifiedMethods(List.of(fd));

        assertNotNull(byMethod);
        assertEquals(5.0, valueOf(byMethod, "让步接收"), 0.001, "让步接收数量");
        assertEquals(1.0, valueOf(byMethod, "报废"), 0.001, "报废数量");
    }
}