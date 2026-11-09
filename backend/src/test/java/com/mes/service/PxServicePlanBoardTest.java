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
 * 工单排产：按工序看可排产量，支持分批。
 *
 * <p>可排产量规则：
 * <ul>
 *   <li>第 1 道：加工单数量 − 本道已排产量</li>
 *   <li>第 k 道：**上道工序合格累计** − 本道已排产量（不合格品不流入下道）</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class PxServicePlanBoardTest {

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

    private FormData mo(int qty) throws Exception {
        return doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of(
                        "products", List.of(Map.of("产品编码", "CP001", "产品名称", "铝棒", "生产单位", "件", "数量", qty)),
                        "materials", List.of(),
                        "processes", List.of(
                                Map.of("工序编码", "PX001", "工序名称", "下料", "加工顺序", 1, "计划数量", qty, "工序单位", "件"),
                                Map.of("工序编码", "PX002", "工序名称", "机加工", "加工顺序", 2, "计划数量", qty, "工序单位", "件"))));
    }

    private FormData report(String procCode, double qty, double qualified) throws Exception {
        return doc("PROCESS_REPORT", "GX-1", "已审核",
                Map.of("单据编号", "GX-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "报工数量", qty, "合格数量", qualified))));
    }

    private FormData dispatch(String procCode, double qty, String status) throws Exception {
        return doc("DISPATCH", "PG-1", status,
                Map.of("单据编号", "PG-1", "加工单号", "MO-1"),
                Map.of("items", List.of(Map.of("工序编码", procCode, "派工数量", qty))));
    }

    @Test
    void firstProcessCanBePlannedUpToOrderQuantity() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(100));
        when(formMapper.selectList(any())).thenReturn(List.of(), List.of());
        List<Map<String, Object>> rows = service.planBoard("MO-1");

        assertEquals(2, rows.size());
        Map<String, Object> first = rows.get(0);
        assertEquals("PX001", first.get("工序编码"));
        assertEquals(100.0, ((Number) first.get("可排产量")).doubleValue(), 0.001);
        assertEquals(0.0, ((Number) first.get("已排产量")).doubleValue(), 0.001);

        Map<String, Object> second = rows.get(1);
        assertEquals(0.0, ((Number) second.get("可排产量")).doubleValue(), 0.001, "上道未完成，下道不可排");
    }

    @Test
    void nextProcessUnlocksByQualifiedQuantityOnly() throws Exception {
        // 下料报工 60，其中合格 55、不合格 5
        when(formMapper.selectOne(any())).thenReturn(mo(100));
        when(formMapper.selectList(any())).thenReturn(List.of(report("PX001", 60, 55)), List.of());
        List<Map<String, Object>> rows = service.planBoard("MO-1");

        Map<String, Object> second = rows.get(1);
        assertEquals(55.0, ((Number) second.get("可排产量")).doubleValue(), 0.001,
                "下道可排产量按上道合格数量，不合格的 5 件不流入下道");
        assertEquals(55.0, ((Number) second.get("上道合格量")).doubleValue(), 0.001);
    }

    @Test
    void plannedQuantityIsDeducted() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(100));
        when(formMapper.selectList(any())).thenReturn(List.of(), List.of(dispatch("PX001", 30, "已审核")));
        List<Map<String, Object>> rows = service.planBoard("MO-1");
        assertEquals(70.0, ((Number) rows.get(0).get("可排产量")).doubleValue(), 0.001);
        assertEquals(30.0, ((Number) rows.get(0).get("已排产量")).doubleValue(), 0.001);
    }

    @Test
    void draftDispatchDoesNotCountAsPlanned() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(100));
        when(formMapper.selectList(any())).thenReturn(List.of(), List.of(dispatch("PX001", 30, "草稿")));
        List<Map<String, Object>> rows = service.planBoard("MO-1");
        assertEquals(100.0, ((Number) rows.get(0).get("可排产量")).doubleValue(), 0.001,
                "草稿排产不占额度");
    }

    @Test
    void planningRejectsOverQuantity() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(mo(100));
        when(formMapper.selectList(any())).thenReturn(List.of(), List.of());
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.validatePlanQuantity("MO-1", "PX001", 120));
        assertTrue(e.getMessage().contains("可排产"), e.getMessage());
    }
}
