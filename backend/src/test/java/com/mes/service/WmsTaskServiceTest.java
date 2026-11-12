package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.WmsTask;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.WmsTaskMapper;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 仓内作业任务：单据 → 任务行、状态推进、波次、复核。
 *
 * <p>这一层原来只有一张只读报表，拣了多少/上架到哪/复核没有都没有落地；
 * 用例盯的是「现场数据必须存得下、状态不能倒推错」。
 */
class WmsTaskServiceTest {

    private final WmsTaskMapper taskMapper = mock(WmsTaskMapper.class);
    private final FormDataMapper formMapper = mock(FormDataMapper.class);
    private final WmsTaskService service = new WmsTaskService(taskMapper, formMapper, new ObjectMapper());

    @Test
    void pickRowsComeFromOutboundDetail() {
        Map<String, Object> head = Map.of("单据编号", "XS-001", "仓库", "成品仓", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒 Φ80", "规格型号", "Φ80", "计量单位", "件",
                        "数量", 10, "库位", "A-01", "仓库", "成品仓"),
                Map.of("存货编码", "CP005", "存货名称", "轴套 C", "计量单位", "件", "数量", 4)));

        List<WmsTaskService.TaskRow> rows = service.buildRows("PICK", "SALE_OUT", "XS-001", head, detail);

        assertEquals(2, rows.size());
        assertEquals("A-01", rows.get(0).fromLocation(), "拣货要带来源货位，现场才知道去哪拿");
        assertEquals("成品仓", rows.get(1).warehouse(), "明细没写仓库时回退表头仓库");
        assertEquals(0, new BigDecimal("4").compareTo(rows.get(1).planQty()));
        assertEquals(2, rows.get(1).rowNo(), "行号要稳定，重复同步靠它去重");
    }

    @Test
    void pickRowsIgnoreWrongPanelAndZeroQty() {
        Map<String, Object> head = Map.of("仓库", "成品仓");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CP001", "存货名称", "铝棒", "数量", 0),
                Map.of("存货编码", "CP002", "存货名称", "法兰", "数量", 3)));

        assertTrue(service.buildRows("PICK", "PURCHASE_IN", "RK-001", head, detail).isEmpty(),
                "入库单不出拣货任务");
        assertEquals(1, service.buildRows("PICK", "SALE_OUT", "XS-002", head, detail).size(),
                "数量为 0 的行不生成任务");
    }

    @Test
    void putawayRowsUseInboundDetail() {
        Map<String, Object> head = Map.of("仓库", "原料仓", "单据日期", "2026-11-12");
        Map<String, Object> detail = Map.of("items", List.of(
                Map.of("存货编码", "CL001", "存货名称", "45#圆钢", "计量单位", "kg", "实收数量", 200,
                        "库位", "收货区")));

        List<WmsTaskService.TaskRow> rows = service.buildRows("PUTAWAY", "PURCHASE_IN", "RK-001", head, detail);

        assertEquals(1, rows.size());
        assertEquals("收货区", rows.get(0).fromLocation(), "上架从收货暂存位出发");
        assertEquals(0, new BigDecimal("200").compareTo(rows.get(0).planQty()), "入库按实收数量");
    }

    @Test
    void statusFollowsDoneQuantity() {
        assertEquals(WmsTaskService.PENDING, WmsTaskService.statusOf(new BigDecimal("10"), BigDecimal.ZERO));
        assertEquals(WmsTaskService.PARTIAL, WmsTaskService.statusOf(new BigDecimal("10"), new BigDecimal("4")));
        assertEquals(WmsTaskService.DONE, WmsTaskService.statusOf(new BigDecimal("10"), new BigDecimal("10")));
        assertEquals(WmsTaskService.DONE, WmsTaskService.statusOf(new BigDecimal("10"), new BigDecimal("12")),
                "超量也按已完成（确认时已拦，这里只兜底）");
    }

    @Test
    void waveNumberIsPerDay() {
        assertEquals("W-20261112-001", WmsTaskService.waveNo(LocalDate.of(2026, 11, 12), 1));
        assertEquals("W-20261112-042", WmsTaskService.waveNo(LocalDate.of(2026, 11, 12), 42));
    }

    @Test
    void confirmAdvancesStatusAndKeepsLocation() {
        WmsTask task = task(9L, WmsTaskService.PICK, "10");
        when(taskMapper.selectById(9L)).thenReturn(task);

        Map<String, Object> partial = service.confirm(9L, new BigDecimal("4"), "", "仓管A");

        assertEquals(WmsTaskService.PARTIAL, partial.get("状态"));
        assertEquals("仓管A", partial.get("作业人"));
        assertEquals(0, new BigDecimal("6").compareTo((BigDecimal) partial.get("差异数量")));

        Map<String, Object> done = service.confirm(9L, new BigDecimal("10"), "", "仓管A");
        assertEquals(WmsTaskService.DONE, done.get("状态"));
        assertTrue(task.getDoneTime() != null, "完成要记时间");
    }

    @Test
    void confirmRejectsOverPick() {
        when(taskMapper.selectById(9L)).thenReturn(task(9L, WmsTaskService.PICK, "10"));
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.confirm(9L, new BigDecimal("11"), "", "仓管A"));
        assertTrue(error.getMessage().contains("超过计划量"));
    }

    @Test
    void putawayNeedsTargetLocationWhenComplete() {
        WmsTask task = task(11L, WmsTaskService.PUTAWAY, "200");
        when(taskMapper.selectById(11L)).thenReturn(task);

        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.confirm(11L, new BigDecimal("200"), "", "仓管A"));
        assertTrue(error.getMessage().contains("目标货位"));

        Map<String, Object> done = service.confirm(11L, new BigDecimal("200"), "A-01", "仓管A");
        assertEquals(WmsTaskService.DONE, done.get("状态"));
        assertEquals("A-01", done.get("目标货位"));
    }

    @Test
    void missingTaskFailsLoudly() {
        when(taskMapper.selectById(any())).thenReturn(null);
        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> service.confirm(404L, BigDecimal.ONE, "", ""));
        assertTrue(error.getMessage().contains("作业任务不存在"));
    }

    @Test
    void reviewStampsEveryTaskOfTheSource() {
        WmsTask a = task(1L, WmsTaskService.PICK, "3");
        WmsTask b = task(2L, WmsTaskService.PICK, "5");
        when(taskMapper.selectList(any())).thenReturn(List.of(a, b));

        Map<String, Object> out = service.review("XS-001", "有差异", "少 1 件");

        assertEquals(2, out.get("任务数"));
        assertEquals("有差异", a.getReviewStatus());
        assertEquals("少 1 件", a.getReviewRemark());
        ArgumentCaptor<WmsTask> saved = ArgumentCaptor.forClass(WmsTask.class);
        verify(taskMapper, times(2)).updateById(saved.capture());
        assertEquals(2, saved.getAllValues().size());
    }

    @Test
    void reviewWithoutTasksFails() {
        when(taskMapper.selectList(any())).thenReturn(List.of());
        IllegalStateException error = assertThrows(IllegalStateException.class,
                () -> service.review("XS-404", "已复核", ""));
        assertTrue(error.getMessage().contains("没有作业任务"));
    }

    private static WmsTask task(Long id, String type, String plan) {
        WmsTask task = new WmsTask();
        task.setId(id);
        task.setTaskType(type);
        task.setPlanQty(new BigDecimal(plan));
        task.setDoneQty(BigDecimal.ZERO);
        task.setStatus(WmsTaskService.PENDING);
        task.setSourceNo("XS-001");
        task.setFromLocation("");
        task.setToLocation("");
        task.setReviewStatus("");
        task.setBizDate(LocalDate.of(2026, 11, 12));
        assertNull(task.getDoneTime());
        return task;
    }
}
