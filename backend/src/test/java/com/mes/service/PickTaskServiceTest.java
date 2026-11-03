package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 拣货作业：从出库单生成拣货任务，按库位排序，记录实拣与差异。
 * 拣货是 WMS 的执行层，出库单是计划层，两者分开才能分工。
 */
class PickTaskServiceTest {

    private final PickTaskService service = new PickTaskService();

    @Test
    void buildsTasksFromOutboundDetail() {
        Map<String, Object> head = Map.of("单据编号", "CK-001", "仓库", "成品仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "数量", 10,
                        "仓库", "成品仓", "库位", "A-01"),
                Map.of("存货编码", "CP002", "存货名称", "铝板 6061", "数量", 5,
                        "仓库", "成品仓", "库位", "A-02")));

        List<PickTaskService.PickTask> tasks = service.buildTasks("CK-001", head, detail);

        assertEquals(2, tasks.size());
        assertEquals("CP001", tasks.get(0).itemCode());
        assertEquals(0, new BigDecimal("10").compareTo(tasks.get(0).planQty()));
        assertEquals("A-01", tasks.get(0).location());
        assertEquals("待拣", tasks.get(0).status());
    }

    @Test
    void tasksAreSortedByLocationForShortestWalk() {
        Map<String, Object> head = Map.of("单据编号", "CK-002", "仓库", "成品仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP003", "存货名称", "C料", "数量", 1, "库位", "C-01"),
                Map.of("存货编码", "CP001", "存货名称", "A料", "数量", 1, "库位", "A-01"),
                Map.of("存货编码", "CP002", "存货名称", "B料", "数量", 1, "库位", "B-01")));

        List<PickTaskService.PickTask> tasks = service.buildTasks("CK-002", head, detail);

        assertEquals("A-01", tasks.get(0).location());
        assertEquals("B-01", tasks.get(1).location());
        assertEquals("C-01", tasks.get(2).location());
    }

    @Test
    void locationFallsBackToHeadWarehouse() {
        Map<String, Object> head = Map.of("单据编号", "CK-003", "仓库", "原料仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "铝棒", "数量", 3)));

        List<PickTaskService.PickTask> tasks = service.buildTasks("CK-003", head, detail);

        assertEquals("原料仓", tasks.get(0).warehouse());
        assertEquals("", tasks.get(0).location());
    }

    @Test
    void zeroQuantityRowsAreSkipped() {
        Map<String, Object> head = Map.of("单据编号", "CK-004");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "数量", 0),
                Map.of("存货编码", "CP002", "数量", 7)));

        List<PickTaskService.PickTask> tasks = service.buildTasks("CK-004", head, detail);

        assertEquals(1, tasks.size());
        assertEquals("CP002", tasks.get(0).itemCode());
    }

    @Test
    void confirmRecordsActualAndDifference() {
        PickTaskService.PickTask task = new PickTaskService.PickTask("CK-005", "CP001", "铝棒",
                "成品仓", "A-01", new BigDecimal("10"), BigDecimal.ZERO, "待拣");

        PickTaskService.PickTask done = service.confirm(task, new BigDecimal("8"));

        assertEquals(0, new BigDecimal("8").compareTo(done.pickedQty()));
        assertEquals(0, new BigDecimal("2").compareTo(done.shortageQty()));
        assertEquals("部分拣货", done.status());
    }

    @Test
    void fullPickIsComplete() {
        PickTaskService.PickTask task = new PickTaskService.PickTask("CK-006", "CP001", "铝棒",
                "成品仓", "A-01", new BigDecimal("10"), BigDecimal.ZERO, "待拣");

        PickTaskService.PickTask done = service.confirm(task, new BigDecimal("10"));

        assertEquals("已拣货", done.status());
        assertEquals(0, BigDecimal.ZERO.compareTo(done.shortageQty()));
    }

    @Test
    void overPickIsCapped() {
        PickTaskService.PickTask task = new PickTaskService.PickTask("CK-007", "CP001", "铝棒",
                "成品仓", "A-01", new BigDecimal("10"), BigDecimal.ZERO, "待拣");

        PickTaskService.PickTask done = service.confirm(task, new BigDecimal("15"));

        assertEquals(0, new BigDecimal("10").compareTo(done.pickedQty()), "实拣不得超过计划");
        assertEquals("已拣货", done.status());
    }

    @Test
    void negativePickIsTreatedAsZero() {
        PickTaskService.PickTask task = new PickTaskService.PickTask("CK-008", "CP001", "铝棒",
                "成品仓", "A-01", new BigDecimal("10"), BigDecimal.ZERO, "待拣");

        PickTaskService.PickTask done = service.confirm(task, new BigDecimal("-5"));

        assertEquals(0, BigDecimal.ZERO.compareTo(done.pickedQty()));
        assertEquals("未拣", done.status());
    }

    @Test
    void stockCheckReportsSurplusAndShortage() {
        List<PickTaskService.PickTask> tasks = List.of(
                new PickTaskService.PickTask("CK-009", "A", "料A", "仓", "L1", new BigDecimal("10"), new BigDecimal("10"), "已拣货"),
                new PickTaskService.PickTask("CK-009", "B", "料B", "仓", "L2", new BigDecimal("10"), new BigDecimal("7"), "部分拣货"),
                new PickTaskService.PickTask("CK-009", "C", "料C", "仓", "L3", new BigDecimal("10"), BigDecimal.ZERO, "未拣"));

        PickTaskService.PickSummary summary = service.summarize(tasks);

        assertEquals(3, summary.total());
        assertEquals(1, summary.pickedCount());
        assertEquals(1, summary.partialCount());
        assertEquals(1, summary.pendingCount());
        // 缺口 = 部分拣货 3 + 未拣 10 = 13
        assertEquals(0, new BigDecimal("13").compareTo(summary.shortageTotal()));
        assertTrue(!summary.complete());
    }

    @Test
    void emptyTaskListIsComplete() {
        PickTaskService.PickSummary summary = service.summarize(List.of());

        assertEquals(0, summary.total());
        assertTrue(summary.complete(), "无任务视为已完成");
    }
}
