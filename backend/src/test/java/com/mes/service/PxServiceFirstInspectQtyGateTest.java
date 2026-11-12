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

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 首件报检单的报检数量上限。
 *
 * <p>2026-11-12 起首检是「报工前置」：该工序还没报过工是正常状态，上限退回首件样本量；
 * 已报过工的是返工重检，仍按累计报工量封顶。这里守的是「首检能提交、但不能乱报量」。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceFirstInspectQtyGateTest {

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

    private FormData doc(String panel, String no, String status, Map<String, Object> head, Map<String, Object> detail)
            throws Exception {
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

    private FormData mo(String procCode, double 首检数量) throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP005", "数量", 10)),
                        "processes", List.of(Map.of("工序编码", procCode, "工序名称", "车削",
                                "加工顺序", 1, "计划数量", 10, "首检数量", 首检数量))));
    }

    private FormData inspect(String procCode, double qty) throws Exception {
        return doc("FIRST_INSPECT", "SJ-1", "草稿",
                Map.of("单据编号", "SJ-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "工序名称", "车削",
                        "报检数量", qty, "检验判定", "合格"))));
    }

    private void givenReports(List<FormData> reports) {
        when(formMapper.selectList(any())).thenReturn(reports);
    }

    private void givenMo(FormData mo) {
        when(formMapper.selectOne(any())).thenReturn(mo);
    }

    private Map<String, Object> row(double qty) {
        return Map.of("工序编码", "PX002", "工序名称", "车削", "报检数量", qty);
    }

    /** 还没报过工：首件样本量以内可以提交（这正是首检前置的正常路径）。 */
    @Test
    void firstInspectAllowedBeforeAnyReport() throws Exception {
        givenReports(List.of());
        givenMo(mo("PX002", 1));
        assertDoesNotThrow(() -> service.validateReportAgainstUpstream(
                "FIRST_INSPECT", inspect("PX002", 1), List.of(row(1))));
    }

    @Test
    void firstInspectCappedByFirstArticleQty() throws Exception {
        givenReports(List.of());
        givenMo(mo("PX002", 1));
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateReportAgainstUpstream(
                        "FIRST_INSPECT", inspect("PX002", 5), List.of(row(5))));
        assertTrue(e.getMessage().contains("首件样本量"), e.getMessage());
    }

    /** 已经报过工：返工重检仍按累计报工量封顶。 */
    @Test
    void reInspectCappedByReportedQty() throws Exception {
        FormData report = doc("PROCESS_REPORT", "GX-1", "已审核",
                Map.of("单据编号", "GX-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", "PX002", "报工数量", 10, "合格数量", 10))));
        givenReports(List.of(report));
        assertDoesNotThrow(() -> service.validateReportAgainstUpstream(
                "FIRST_INSPECT", inspect("PX002", 3), List.of(row(3))));

        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validateReportAgainstUpstream(
                        "FIRST_INSPECT", inspect("PX002", 20), List.of(row(20))));
        assertTrue(e.getMessage().contains("累计报工量"), e.getMessage());
    }
}
