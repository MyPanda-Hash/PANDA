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
 * 工序报工工作台：一行一道工序，按顺序放行。
 *
 * <p>放行规则（B）：本道有剩余量，且**前一道已有报工**（>0）即可开工，允许工序重叠。
 * 第一道没有前置。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceReportBoardTest {

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

    private FormData mo(String no, String status) throws Exception {
        return doc("MANU_ORDER", no, status,
                Map.of("单据编号", no, "生产车间", "熔铸车间", "客户", "华东铝业"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒", "规格型号", "Φ80",
                                "生产单位", "件", "数量", 100)),
                        "materials", List.of(),
                        "processes", List.of(
                                Map.of("工序编码", "PX001", "工序名称", "下料", "加工顺序", 1,
                                        "计划数量", 100, "工序单位", "件"),
                                Map.of("工序编码", "PX002", "工序名称", "机加工", "加工顺序", 2,
                                        "计划数量", 100, "工序单位", "件"),
                                Map.of("工序编码", "PX007", "工序名称", "检验", "加工顺序", 3,
                                        "计划数量", 100, "工序单位", "件"))));
    }

    private FormData report(String no, String procCode, double qty) throws Exception {
        return doc("PROCESS_REPORT", no, "已审核",
                Map.of("单据编号", no, "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "报工数量", qty))));
    }

    @Test
    void firstProcessIsReportableWithoutPredecessor() throws Exception {
        when(formMapper.selectList(any())).thenReturn(List.of(mo("MO-1", "已审核")));
        List<Map<String, Object>> rows = service.processReportBoard("MO-1");

        assertEquals(3, rows.size());
        Map<String, Object> first = rows.get(0);
        assertEquals("PX001", first.get("工序编码"));
        assertEquals(true, first.get("可否报工"), "第一道没有前置，可直接报");
        assertEquals(100.0, ((Number) first.get("剩余数量")).doubleValue(), 0.001);

        Map<String, Object> second = rows.get(1);
        assertEquals(false, second.get("可否报工"), "前道未报工，第二道不能开");
        assertTrue(String.valueOf(second.get("不可报工原因")).contains("前置工序"), second.toString());
    }

    @Test
    void predecessorWithAnyReportUnlocksNext() throws Exception {
        when(formMapper.selectList(any())).thenReturn(
                List.of(report("GX-1", "PX001", 30)),  // 先查已审核报工：下料 30
                List.of(mo("MO-1", "已审核")));         // 再查加工单
        List<Map<String, Object>> rows = service.processReportBoard("MO-1");

        assertEquals(true, rows.get(0).get("可否报工"));
        assertEquals(true, rows.get(1).get("可否报工"), "前道已报 30（>0），第二道可开工");
        assertEquals(30.0, ((Number) rows.get(0).get("已报数量")).doubleValue(), 0.001);
        assertEquals(70.0, ((Number) rows.get(0).get("剩余数量")).doubleValue(), 0.001);
    }

    @Test
    void fullyReportedProcessIsClosed() throws Exception {
        when(formMapper.selectList(any())).thenReturn(
                List.of(report("GX-1", "PX001", 100)),
                List.of(mo("MO-1", "已审核")));
        List<Map<String, Object>> rows = service.processReportBoard("MO-1");
        assertEquals(false, rows.get(0).get("可否报工"));
        assertEquals(0.0, ((Number) rows.get(0).get("剩余数量")).doubleValue(), 0.001);
    }

    @Test
    void draftOrdersAreExcluded() throws Exception {
        when(formMapper.selectList(any())).thenReturn(List.of(mo("MO-1", "草稿")));
        assertTrue(service.processReportBoard(null).isEmpty(), "未审核的加工单不进入报工工作台");
    }

    @Test
    void reportRejectsSkippedProcess() throws Exception {
        // 没有任何报工记录，但加工单存在 → 第二道应被前置规则拦下
        when(formMapper.selectList(any())).thenReturn(
                List.of(),
                List.of(mo("MO-1", "已审核")));
        // 直接做校验层面的判断：第二道前道无报工 → 拒绝
        IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(
                IllegalStateException.class,
                () -> service.validateProcessReportSequence("MO-1", "PX002"));
        assertTrue(e.getMessage().contains("前置工序"), e.getMessage());
    }
}
