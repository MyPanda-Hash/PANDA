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

    /**
     * 排产顺序必须来自工艺路线：路线里存成乱序（加工顺序 3,1,2），
     * 加工单工序明细与排产看板都要按 1,2,3 给出，而不是按数组顺序。
     */
    @Test
    void planBoardFollowsRouteSequence() throws Exception {
        // 工艺路线：存储乱序，加工顺序才是权威
        FormData route = doc("ROUTE", "ROUTE-001", "已审核",
                Map.of("工艺路线编码", "GY-010", "工艺路线名称", "三段路线"),
                Map.of("processes", List.of(
                        Map.of("工序编码", "PX007", "工序名称", "检验", "加工顺序", 3, "生产车间", "测试车间"),
                        Map.of("工序编码", "PX001", "工序名称", "下料", "加工顺序", 1, "生产车间", "熔铸车间"),
                        Map.of("工序编码", "PX002", "工序名称", "车削", "加工顺序", 2, "生产车间", "熔铸车间"))));
        // BOM 把产品绑到这条路线
        FormData bom = doc("BOM", "BOM-1", "已审核",
                Map.of("产品编码", "CP001", "工艺路线编码", "GY-010"), Map.of("children", List.of()));
        when(formMapper.selectList(any())).thenReturn(List.of(route), List.of(bom));

        // 1) 加工单按路线展开：顺序 = 下料 → 车削 → 检验
        List<Map<String, Object>> procs = service.resolveProcesses(
                List.of(Map.of("产品编码", "CP001", "数量", 100)), "熔铸车间");
        assertEquals(List.of("PX001", "PX002", "PX007"),
                procs.stream().map((r) -> r.get("工序编码")).toList(),
                "加工单工序必须按路线加工顺序展开");
        assertEquals(List.of(1, 2, 3),
                procs.stream().map((r) -> (int) ((Number) r.get("加工顺序")).doubleValue()).toList(),
                "加工顺序按路线重新编号");

        // 2) 排产看板：同样的顺序，且第 2 道额度取上道合格量
        FormData mo = doc("MANU_ORDER", "MO-1", "已审核",
                Map.of("单据编号", "MO-1", "生产车间", "熔铸车间"),
                Map.of("products", List.of(Map.of("产品编码", "CP001", "数量", 100)),
                        "processes", procs));
        FormData qualified = report("PX001", 100, 90);   // 下料合格 90
        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(qualified), List.of());

        List<Map<String, Object>> board = service.planBoard("MO-1");

        assertEquals(List.of("PX001", "PX002", "PX007"),
                board.stream().map((r) -> r.get("工序编码")).toList(),
                "排产看板顺序必须与工艺路线一致");
        assertEquals(List.of(1, 2, 3),
                board.stream().map((r) -> (int) ((Number) r.get("工序顺序")).doubleValue()).toList());
        assertEquals(100.0, ((Number) board.get(0).get("可排产量")).doubleValue(), 0.001,
                "第 1 道按加工单数量");
        assertEquals(90.0, ((Number) board.get(1).get("可排产量")).doubleValue(), 0.001,
                "第 2 道按上道合格量（不合格不流入下道）");
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
