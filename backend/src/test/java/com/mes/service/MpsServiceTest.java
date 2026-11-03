package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * MPS 主生产计划：只认销售订单（单厂按单生产）。
 * 计划行 = 已审核销售订单 - 已排产占用 = 待排产数量。
 */
class MpsServiceTest {

    private final MpsService service = new MpsService(null);

    @Test
    void salesOrderLineBecomesScheduleLine() {
        Map<String, Object> head = Map.of("单据编号", "SO-001", "客户", "中天精工",
                "单据日期", "2026-11-02", "预计交货日期", "2026-11-20");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80×3000",
                        "销售单位", "件", "数量", 100)));

        List<MpsService.PlanLine> lines = service.buildPlanLines("SO-001", head, detail);

        assertEquals(1, lines.size());
        MpsService.PlanLine line = lines.get(0);
        assertEquals("SO-001", line.sourceNo());
        assertEquals("CP001", line.itemCode());
        assertEquals("铝棒 Φ80", line.itemName());
        assertEquals(0, new BigDecimal("100").compareTo(line.planQty()));
        assertEquals("2026-11-20", line.dueDate());
        assertEquals("中天精工", line.customer());
    }

    @Test
    void scheduledQuantityIsDeducted() {
        Map<String, Object> head = Map.of("单据编号", "SO-002", "客户", "西部材料",
                "预计交货日期", "2026-11-25");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "销售单位", "件", "数量", 100)));

        List<MpsService.PlanLine> lines = service.buildPlanLines("SO-002", head, detail,
                Map.of("CP001", new BigDecimal("30")));

        assertEquals(0, new BigDecimal("70").compareTo(lines.get(0).planQty()));
    }

    @Test
    void fullyScheduledLineIsDropped() {
        Map<String, Object> head = Map.of("单据编号", "SO-003", "客户", "华东铝业");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "成品A", "数量", 50)));

        List<MpsService.PlanLine> lines = service.buildPlanLines("SO-003", head, detail,
                Map.of("CP001", new BigDecimal("50")));

        assertTrue(lines.isEmpty(), "已排满的行不应出现在计划里");
    }

    @Test
    void overScheduledDoesNotProduceNegative() {
        Map<String, Object> head = Map.of("单据编号", "SO-004", "客户", "南方重工");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "成品A", "数量", 10)));

        List<MpsService.PlanLine> lines = service.buildPlanLines("SO-004", head, detail,
                Map.of("CP001", new BigDecimal("30")));

        assertTrue(lines.isEmpty(), "超排不应产生负数计划量");
    }

    @Test
    void zeroQuantityLineIsDropped() {
        Map<String, Object> head = Map.of("单据编号", "SO-005", "客户", "客户A");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "成品A", "数量", 0)));

        assertTrue(service.buildPlanLines("SO-005", head, detail).isEmpty());
    }

    @Test
    void dueDateFallsBackToOrderDatePlusLeadDays() {
        Map<String, Object> head = Map.of("单据编号", "SO-006", "客户", "客户A",
                "单据日期", "2026-11-01");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "成品A", "数量", 10)));

        List<MpsService.PlanLine> lines = service.buildPlanLines("SO-006", head, detail);

        assertEquals(1, lines.size());
        assertEquals("2026-11-11", lines.get(0).dueDate(), "缺交货日期时按单据日期+10天推算");
    }

    @Test
    void suggestedStartIsDueDateMinusLeadTime() {
        String start = service.suggestStart("2026-11-20", 5);
        assertEquals("2026-11-15", start);
    }

    @Test
    void suggestedStartHandlesNullDueDate() {
        assertNull(service.suggestStart(null, 5));
    }

    @Test
    void priorityIsDerivedFromDueDate() {
        // 提前期 3 天：<=3 特急，<=6 紧急，其余正常
        assertEquals("特急", service.priorityOf("2026-11-03", "2026-11-02", 3));
        assertEquals("紧急", service.priorityOf("2026-11-07", "2026-11-02", 3));
        assertEquals("正常", service.priorityOf("2026-11-10", "2026-11-02", 3));
    }

    @Test
    void planLineCarriesSalesOrderReference() {
        Map<String, Object> head = Map.of("单据编号", "SO-007", "客户", "客户B");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP002", "存货名称", "成品B", "数量", 5, "销售单位", "套")));

        MpsService.PlanLine line = service.buildPlanLines("SO-007", head, detail).get(0);

        assertEquals("销售订单", line.sourceType());
        assertEquals("套", line.unit());
        assertEquals("SO-007", line.sourceNo());
    }
}
