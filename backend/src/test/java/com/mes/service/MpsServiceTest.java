package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.mapper.FormDataMapper;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;

/**
 * 主生产计划 / 物料需求汇总：订单行 → 计划行、跨订单缺口聚合、建议动作。
 *
 * <p>盯的是两件容易出错的事：同一订单行重复生成不能把计划量翻倍；
 * 多张订单汇总时同一个物料的库存不能相加（它本来就是同一个数）。
 */
class MpsServiceTest {

    private final FormDataMapper formMapper = mock(FormDataMapper.class);
    private final PlanAnalyzeQueryService analyzeQuery = mock(PlanAnalyzeQueryService.class);
    private final MpsService service = new MpsService(formMapper, new ObjectMapper(), analyzeQuery);

    @Test
    void planRowsSkipAlreadyPlannedQuantity() {
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒", "数量", 100, "销售单位", "件"),
                Map.of("存货编码", "CP002", "存货名称", "法兰", "数量", 30, "销售单位", "件")));

        // CP001 已经计划过 40，只剩 60 可计划
        List<MpsService.PlanRow> rows = service.rowsOf("SO-1", detail,
                Map.of("SO-1|1", new BigDecimal("40")), LocalDate.of(2026, 11, 12), LocalDate.of(2026, 11, 19));

        assertEquals(2, rows.size());
        assertEquals(0, new BigDecimal("60").compareTo(rows.get(0).qty()), "已计划量要从订单量里扣掉");
        assertEquals("SO-1", rows.get(0).sourceOrder());
        assertEquals(1, rows.get(0).sourceRow());
        assertEquals(0, new BigDecimal("30").compareTo(rows.get(1).qty()));
        assertEquals("2026-11-19", rows.get(1).endDate());
    }

    @Test
    void planRowsSkipFullyPlannedAndZeroQuantity() {
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒", "数量", 10),
                Map.of("存货编码", "CP002", "存货名称", "法兰", "数量", 0)));

        assertTrue(service.rowsOf("SO-1", detail, Map.of("SO-1|1", new BigDecimal("10")), null, null).isEmpty(),
                "计划满了就不再出计划行，数量为 0 的行也不出");
    }

    @Test
    void aggregateSumsGapAcrossOrdersWithoutDoublingStock() {
        Map<String, Object> first = Map.of(
                "salesOrderNo", "SO-1",
                "products", List.of(Map.of("code", "CP001", "name", "铝棒", "unit", "件",
                        "demandQty", 100, "stockQty", 80, "shortQty", 20)),
                "materials", List.of(
                        Map.of("level", 1, "code", "CL001", "name", "45#圆钢", "unit", "kg",
                                "grossQty", 200, "stockQty", 5740.47, "netQty", 0, "supplyType", "外购"),
                        Map.of("level", 1, "code", "CL004", "name", "切削液", "unit", "kg",
                                "grossQty", 2, "stockQty", 300, "netQty", 2, "supplyType", "外购")));
        Map<String, Object> second = Map.of(
                "salesOrderNo", "SO-2",
                "products", List.of(Map.of("code", "CP001", "name", "铝棒", "unit", "件",
                        "demandQty", 50, "stockQty", 80, "shortQty", 10)),
                "materials", List.of(
                        Map.of("level", 1, "code", "CL004", "name", "切削液", "unit", "kg",
                                "grossQty", 3, "stockQty", 300, "netQty", 3, "supplyType", "外购")));

        List<Map<String, Object>> rows = MpsService.aggregate(List.of(first, second));

        Map<String, Map<String, Object>> byCode = new java.util.LinkedHashMap<>();
        for (Map<String, Object> row : rows) byCode.put(String.valueOf(row.get("存货编码")), row);
        assertEquals(2, rows.size(), "零缺口的圆钢不进清单");
        Map<String, Object> coolant = byCode.get("CL004");
        assertEquals(0, new BigDecimal("5").compareTo((BigDecimal) coolant.get("毛需求")), "毛需求跨单相加");
        assertEquals(0, new BigDecimal("5").compareTo((BigDecimal) coolant.get("净需求")), "净需求跨单相加");
        assertEquals(0, new BigDecimal("300").compareTo((BigDecimal) coolant.get("现存量")), "现存量取最大值，不能相加");
        assertEquals("请购", coolant.get("建议"));
        assertEquals(2, ((List<?>) coolant.get("来源订单")).size());
        Map<String, Object> product = byCode.get("CP001");
        assertEquals(0, new BigDecimal("150").compareTo((BigDecimal) product.get("毛需求")),
                "成品行的需求量字段是 demandQty，别取成 0");
        assertEquals(0, new BigDecimal("30").compareTo((BigDecimal) product.get("净需求")));
        assertEquals("生产", product.get("建议"), "成品缺口走生产");
    }

    @Test
    void suggestionFollowsSupplyType() {
        assertEquals("请购", MpsService.suggestOf("外购"));
        assertEquals("委外加工", MpsService.suggestOf("委外"));
        assertEquals("生产", MpsService.suggestOf("自制"));
        assertEquals("生产", MpsService.suggestOf(""), "没填属性按自制处理");
    }

    @Test
    void emptyAnalysisProducesEmptyList() {
        assertTrue(MpsService.aggregate(List.of()).isEmpty());
        assertTrue(MpsService.aggregate(null).isEmpty());
    }
}
