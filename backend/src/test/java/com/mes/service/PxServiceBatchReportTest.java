package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 按排产批次报工：强制先排产，累计报工不得超过批次数量。
 *
 * <p>报工数量 = 合格数量 + 不合格数量；两者都要落在批次剩余量之内。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceBatchReportTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private StockTxnService stockTxnService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), null, null, stockTxnService);
    }

    private FormData doc(String panel, String no, String status, Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData d = new FormData();
        d.setId(1L);
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus(status);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private FormData batch(String no, String procCode, double qty, String status) throws Exception {
        return doc("DISPATCH", no, status,
                Map.of("单据编号", no, "加工单号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("items", List.of(Map.of(
                        "工序编码", procCode, "工序名称", "下料", "派工数量", qty,
                        "班组", "1班", "设备", "锯床-01"))));
    }

    private FormData report(String dispatchNo, double qty, double qualified) throws Exception {
        return doc("PROCESS_REPORT", "GX-1", "已审核",
                Map.of("单据编号", "GX-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of(
                        "排产单号", dispatchNo, "工序编码", "PX001",
                        "报工数量", qty, "合格数量", qualified))));
    }

    @Test
    void batchBoardListsApprovedBatchesWithProgress() throws Exception {
        when(formMapper.selectList(any())).thenReturn(
                List.of(report("PG-1", 600, 580)),
                List.of(batch("PG-1", "PX001", 2000, "已审核")));

        List<Map<String, Object>> rows = service.reportBatchBoard();
        assertEquals(1, rows.size());
        Map<String, Object> r = rows.get(0);
        assertEquals("PG-1", r.get("排产单号"));
        assertEquals(2000.0, ((Number) r.get("批次数量")).doubleValue(), 0.001);
        assertEquals(600.0, ((Number) r.get("已报数量")).doubleValue(), 0.001);
        assertEquals(1400.0, ((Number) r.get("剩余数量")).doubleValue(), 0.001);
        assertEquals(true, r.get("可否报工"));
    }

    @Test
    void draftBatchIsNotReportable() throws Exception {
        when(formMapper.selectList(any())).thenReturn(
                List.of(),
                List.of(batch("PG-9", "PX001", 100, "草稿")));
        assertTrue(service.reportBatchBoard().isEmpty(), "草稿排产不能报工（强制先排产且要审批）");
    }

    @Test
    void overBatchQuantityIsRejected() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(null);
        when(formMapper.selectList(any())).thenReturn(
                List.of(report("PG-1", 90, 90)),
                List.of(batch("PG-1", "PX001", 100, "已审核")));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateBatchReport("PG-1", 20, 18));
        assertTrue(e.getMessage().contains("剩余") || e.getMessage().contains("超过"), e.getMessage());
    }

    /** 报工数量由 合格+不合格 组成，全为 0 时没有可报的量，必须拒绝。 */
    @Test
    void rejectsZeroReport() {
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service.validateBatchReport("PG-1", 0, 0));
        assertTrue(e.getMessage().contains("大于 0"), e.getMessage());
    }

    @Test
    void canReportWithinRemaining() throws Exception {
        when(formMapper.selectList(any())).thenReturn(
                List.of(report("PG-1", 90, 90)),
                List.of(batch("PG-1", "PX001", 100, "已审核")));
        // 剩余 10，本次 10（合格 9 + 不合格 1）应通过
        double qty = service.validateBatchReport("PG-1", 9, 1);
        assertEquals(10.0, qty, 0.001, "报工数量 = 合格 + 不合格");
    }
}
