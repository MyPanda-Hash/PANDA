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
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 工序完成判定：某道工序累计报工（合格 + 不合格）= 加工单数量 才算完成。
 * 全部工序完成 → 加工单完工；末道首次完成 → 触发首检。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceProcessCompletionTest {

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

    private FormData mo() throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核", Map.of("单据编号", "MO-1"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "数量", 100)),
                        "materials", List.of(),
                        "processes", List.of(
                                Map.of("工序编码", "PX001", "工序名称", "下料", "加工顺序", 1, "计划数量", 100),
                                Map.of("工序编码", "PX002", "工序名称", "机加工", "加工顺序", 2, "计划数量", 100))));
    }

    private FormData report(String procCode, double qty, double qualified) throws Exception {
        return doc("PROCESS_REPORT", "GX-" + procCode, "已审核",
                Map.of("单据编号", "GX-" + procCode, "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "报工数量", qty, "合格数量", qualified))));
    }

    @Test
    void processCompletionCountsQualifiedPlusRejected() throws Exception {
        // 报工 100 = 合格 95 + 不合格 5 → 工序完成（按报工总量，不是合格量）
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(report("PX001", 100, 95)));

        List<Map<String, Object>> rows = service.processCompletion("MO-1");
        Map<String, Object> first = rows.get(0);
        assertEquals(100.0, ((Number) first.get("累计报工")).doubleValue(), 0.001);
        assertEquals(true, first.get("是否完成"));

        Map<String, Object> second = rows.get(1);
        assertEquals(false, second.get("是否完成"), "第二道还没报工");
    }

    @Test
    void orderIsNotFinishedUntilAllProcessesComplete() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(report("PX001", 100, 100)));

        Map<String, Object> summary = service.completionSummary("MO-1");
        assertEquals(false, summary.get("全部完成"));
        assertEquals(1, summary.get("已完成工序数"));
        assertEquals(2, summary.get("工序数"));
        assertEquals(false, summary.get("首检应触发"), "末道未完成，不触发首检");
    }

    @Test
    void lastProcessCompletionTriggersFirstInspectionOnce() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(
                report("PX001", 100, 100),
                report("PX002", 100, 98)));

        Map<String, Object> summary = service.completionSummary("MO-1");
        assertEquals(true, summary.get("全部完成"));
        assertEquals(true, summary.get("首检应触发"), "末道首次完成 → 触发首检");
        assertEquals("PX002", summary.get("末道工序编码"));
    }

    /** 首检只生成一次：已有同加工单首检单时不再重复建。 */
    @Test
    void firstInspectionIsNotDuplicated() throws Exception {
        org.mockito.Mockito.lenient().when(formMapper.selectOne(any())).thenReturn(mo());
        org.mockito.Mockito.lenient().when(formMapper.selectList(any())).thenReturn(
                List.of(report("PX001", 100, 100), report("PX002", 100, 100)),
                List.of(doc("FIRST_INSPECT", "SJ-1", "草稿",
                        Map.of("单据编号", "SJ-1", "加工单号", "MO-1"), Map.of("items", List.of()))));

        service.advanceCompletion("MO-1");

        // 已存在首检单 → 不应再 insert
        org.mockito.Mockito.verify(formMapper, org.mockito.Mockito.never())
                .insert(org.mockito.ArgumentMatchers.<com.mes.entity.FormData>argThat(
                        d -> "FIRST_INSPECT".equals(d.getPanelCode())));
    }

    @Test
    void partialReportDoesNotCompleteProcess() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo());
        when(formMapper.selectList(any())).thenReturn(List.of(report("PX001", 60, 60)));

        List<Map<String, Object>> rows = service.processCompletion("MO-1");
        assertFalse((Boolean) rows.get(0).get("是否完成"));
        assertEquals(40.0, ((Number) rows.get(0).get("未报数量")).doubleValue(), 0.001);
    }
}
