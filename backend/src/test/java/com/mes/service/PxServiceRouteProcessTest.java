package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** 加工单工序按 BOM 上配置的工艺路线展开；没有路线时回退默认三道。 */
@ExtendWith(MockitoExtension.class)
class PxServiceRouteProcessTest {

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

    private FormData doc(String panel, String no, Map<String, Object> head, Map<String, Object> detail) throws Exception {
        FormData d = new FormData();
        d.setId(1L);
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus("已审核");
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code + "\",\"autoCodeField\":\"单据编号\"},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    @Test
    void processesFollowRouteConfiguredOnBom() throws Exception {
        FormData bom = doc("BOM", "BOM-1",
                Map.of("产品编码", "CP004", "工艺路线编码", "GY-002"),
                Map.of("children", List.of(Map.of("父件编码", "CP004", "子件编码", "CL001"))));
        FormData route = doc("ROUTE", "工艺路线", Map.of("备注", "工艺路线"),
                Map.of("processes", List.of(
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 1, "工序编码", "PX001", "工序名称", "下料", "生产车间", "熔铸车间"),
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 2, "工序编码", "PX003", "工序名称", "铣削", "生产车间", "精整车间"),
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 3, "工序编码", "PX005", "工序名称", "热处理", "生产车间", "轧制车间"),
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 4, "工序编码", "PX007", "工序名称", "检验", "生产车间", "熔铸车间"))));
        when(formMapper.selectList(any())).thenReturn(List.of(bom), List.of(route));

        List<Map<String, Object>> procs = service.resolveProcesses(
                List.of(Map.of("产品编码", "CP004", "数量", 2)), "熔铸车间");

        assertEquals(4, procs.size(), "应展开路线上的 4 道工序");
        assertEquals("PX003", procs.get(1).get("工序编码"));
        assertEquals("铣削", procs.get(1).get("工序名称"));
        assertEquals("精整车间", procs.get(1).get("生产车间"));
    }

    @Test
    void fallsBackToDefaultWhenNoRoute() throws Exception {
        FormData bom = doc("BOM", "BOM-2", Map.of("产品编码", "CP003"),
                Map.of("children", List.of(Map.of("父件编码", "CP003"))));
        when(formMapper.selectList(any())).thenReturn(List.of(bom), List.of());

        List<Map<String, Object>> procs = service.resolveProcesses(
                List.of(Map.of("产品编码", "CP003", "数量", 2)), "熔铸车间");

        assertEquals(3, procs.size(), "没有路线时回退下料/机加工/检验");
        assertEquals(List.of("PX001", "PX002", "PX007"),
                procs.stream().map((p) -> p.get("工序编码")).toList());
    }

    /** 手工建单：选路线后整体替换工序明细。 */
    @Test
    void applyRouteReplacesProcessesOnDraftOrder() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-9",
                Map.of("单据编号", "MO-9"),
                Map.of("products", List.of(Map.of("产品编码", "CP004", "数量", 2)),
                        "processes", List.of(Map.of("工序编码", "PX002", "工序名称", "机加工", "加工顺序", 1))));
        mo.setStatus("草稿");
        FormData route = doc("ROUTE", "工艺路线", Map.of("备注", "工艺路线"),
                Map.of("processes", List.of(
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 1, "工序编码", "PX001", "工序名称", "下料", "生产车间", "熔铸车间"),
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 2, "工序编码", "PX003", "工序名称", "铣削", "生产车间", "精整车间"))));
        when(formMapper.selectOne(any())).thenReturn(mo);
        when(formMapper.selectList(any())).thenReturn(List.of(route));

        Map<String, Object> out = service.applyRouteProcesses("MANU_ORDER",
                Map.of("编号", "MO-9", "工艺路线编码", "GY-002"));

        assertEquals(2, out.get("工序数"));
        verify(formMapper).updateById(any(FormData.class));
        Map<?, ?> detail = json.readValue(mo.getDetailData(), Map.class);
        List<?> procs = (List<?>) detail.get("processes");
        assertEquals(2, procs.size());
        assertEquals("PX003", ((Map<?, ?>) procs.get(1)).get("工序编码"));
        Map<?, ?> head = json.readValue(mo.getData(), Map.class);
        assertEquals("GY-002", head.get("工艺路线编码"));
    }

    // ==================== 工艺路线主从结构（一单一条路线） ====================

    /**
     * 新结构：工艺路线编码在表头，一张单据只放一条路线的工序。
     * routeRowsOf 必须能从表头取到编码，返回该单全部工序行。
     */
    @Test
    void routeRowsResolveFromHeaderWhenRouteIsOwnDocument() throws Exception {
        FormData route = doc("ROUTE", "ROUTE-002",
                Map.of("工艺路线编码", "GY-002", "工艺路线名称", "减速箱体 A 加工路线"),
                Map.of("processes", List.of(
                        Map.of("加工顺序", 1, "工序编码", "PX001", "工序名称", "下料"),
                        Map.of("加工顺序", 2, "工序编码", "PX003", "工序名称", "铣削"))));
        when(formMapper.selectList(any())).thenReturn(List.of(route));

        List<Map<String, Object>> rows = service.routeRowsOf("GY-002");

        assertEquals(2, rows.size(), "表头带 工艺路线编码 的单据应整单取行");
        assertEquals("PX001", rows.get(0).get("工序编码"));
        assertEquals("PX003", rows.get(1).get("工序编码"));
    }

    /** 新结构下，别的路线的单据不能被误命中。 */
    @Test
    void routeRowsIgnoreOtherRouteDocuments() throws Exception {
        FormData gy1 = doc("ROUTE", "ROUTE-001",
                Map.of("工艺路线编码", "GY-001"), Map.of("processes", List.of(
                        Map.of("加工顺序", 1, "工序编码", "PX001", "工序名称", "下料"))));
        FormData gy2 = doc("ROUTE", "ROUTE-002",
                Map.of("工艺路线编码", "GY-002"), Map.of("processes", List.of(
                        Map.of("加工顺序", 1, "工序编码", "PX003", "工序名称", "铣削"))));
        when(formMapper.selectList(any())).thenReturn(List.of(gy1, gy2));

        List<Map<String, Object>> rows = service.routeRowsOf("GY-002");

        assertEquals(1, rows.size());
        assertEquals("PX003", rows.get(0).get("工序编码"));
    }

    /** 加工顺序缺失时按行序兜底排序，不得抛异常、不得乱序。 */
    @Test
    void routeRowsFallBackToRowOrderWhenSequenceMissing() throws Exception {
        FormData route = doc("ROUTE", "ROUTE-003",
                Map.of("工艺路线编码", "GY-003"),
                Map.of("processes", List.of(
                        Map.of("工序编码", "PX001", "工序名称", "下料"),
                        Map.of("工序编码", "PX002", "工序名称", "车削"),
                        Map.of("工序编码", "PX007", "工序名称", "检验"))));
        when(formMapper.selectList(any())).thenReturn(List.of(route));

        List<Map<String, Object>> rows = service.routeRowsOf("GY-003");

        assertEquals(3, rows.size());
        assertEquals(List.of("PX001", "PX002", "PX007"),
                rows.stream().map((r) -> r.get("工序编码")).toList());
    }

    /** 新旧结构混存时不能重复返回同一道工序。 */
    @Test
    void routeRowsDeduplicateLegacyAndHeaderStorage() throws Exception {
        FormData legacy = doc("ROUTE", "工艺路线", Map.of("备注", "工艺路线"),
                Map.of("processes", List.of(
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 1, "工序编码", "PX001", "工序名称", "下料"),
                        Map.of("工艺路线编码", "GY-002", "加工顺序", 2, "工序编码", "PX003", "工序名称", "铣削"))));
        FormData current = doc("ROUTE", "ROUTE-002",
                Map.of("工艺路线编码", "GY-002"),
                Map.of("processes", List.of(
                        Map.of("加工顺序", 1, "工序编码", "PX001", "工序名称", "下料"),
                        Map.of("加工顺序", 2, "工序编码", "PX003", "工序名称", "铣削"))));
        when(formMapper.selectList(any())).thenReturn(List.of(legacy, current));

        List<Map<String, Object>> rows = service.routeRowsOf("GY-002");

        assertEquals(2, rows.size(), "同一路线同一工序跨新旧结构只能算一次");
        assertEquals(List.of("PX001", "PX003"),
                rows.stream().map((r) -> r.get("工序编码")).toList());
    }

    /** 保存工艺路线时按行序重排加工顺序，并清掉行内的表头冗余字段。 */
    @Test
    void saveRouteRenumbersProcessesAndDropsHeaderCopies() throws Exception {
        Map<String, Object> row1 = new java.util.HashMap<>(Map.of(
                "工序编码", "PX001", "工序名称", "下料", "加工顺序", 7,
                "工艺路线编码", "GY-OLD", "工艺路线名称", "旧名称", "停用", true));
        Map<String, Object> row2 = new java.util.HashMap<>(Map.of(
                "工序编码", "PX002", "工序名称", "车削", "加工顺序", 3,
                "工艺路线编码", "GY-OLD", "工艺路线名称", "旧名称", "停用", true));
        Map<String, Object> formData = new java.util.HashMap<>();
        formData.put("工艺路线编码", "GY-100");
        formData.put("工艺路线名称", "新名称");
        formData.put("detail", Map.of("processes", List.of(row1, row2)));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(config("ROUTE"));

        service.callButton("ROUTE", "保存", formData, null);

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).insert(captor.capture());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        List<?> procs = (List<?>) detail.get("processes");
        assertEquals(1, ((Map<?, ?>) procs.get(0)).get("加工顺序"), "第一行顺序归 1");
        assertEquals(2, ((Map<?, ?>) procs.get(1)).get("加工顺序"), "第二行顺序归 2");
        for (Object p : procs) {
            Map<?, ?> m = (Map<?, ?>) p;
            assertTrue(!m.containsKey("工艺路线编码"), "行内不应再冗余存工艺路线编码");
            assertTrue(!m.containsKey("工艺路线名称"), "行内不应再冗余存工艺路线名称");
            assertTrue(!m.containsKey("停用"), "行内不应再冗余存停用");
        }
        Map<?, ?> head = json.readValue(captor.getValue().getData(), Map.class);
        assertEquals("GY-100", head.get("工艺路线编码"), "编码保留在表头");
        assertEquals("新名称", head.get("工艺路线名称"), "名称保留在表头");
    }

    /** 列表接口：工序必须按加工顺序返回，并给出 1..N 的工序顺序（排产依据要看得到先后）。 */
    @Test
    void listReturnsRouteProcessesInSequenceOrder() throws Exception {
        FormData route = doc("ROUTE", "ROUTE-009",
                Map.of("工艺路线编码", "GY-009", "工艺路线名称", "乱序存储的路线"),
                Map.of("processes", List.of(
                        Map.of("工序编码", "PX007", "工序名称", "检验", "加工顺序", 3),
                        Map.of("工序编码", "PX001", "工序名称", "下料", "加工顺序", 1),
                        Map.of("工序编码", "PX002", "工序名称", "车削", "加工顺序", 2))));
        when(formMapper.selectList(any())).thenReturn(List.of(route));

        Map<String, Object> out = service.queryFormDataList("ROUTE", "", Map.of(), 1, 20);

        List<?> list = (List<?>) out.get("list");
        Map<?, ?> first = (Map<?, ?>) list.get(0);
        Map<?, ?> detail = (Map<?, ?>) first.get("detail");
        List<?> procs = (List<?>) detail.get("processes");
        assertEquals(List.of("PX001", "PX002", "PX007"),
                procs.stream().map((r) -> ((Map<?, ?>) r).get("工序编码")).toList(),
                "工序必须按加工顺序升序返回，不能依赖存储时的数组顺序");
        assertEquals(List.of(1, 2, 3),
                procs.stream().map((r) -> ((Map<?, ?>) r).get("工序顺序")).toList(),
                "每道工序要带 1..N 的工序顺序，界面据此显示先后");
    }

    /** 已审核的加工单不能改工序（要先弃审）。 */
    @Test
    void rejectsRouteApplyOnApprovedOrder() throws Exception {
        FormData mo = doc("MANU_ORDER", "MO-10", Map.of("单据编号", "MO-10"), Map.of("processes", List.of()));
        mo.setStatus("已审核");
        when(formMapper.selectOne(any())).thenReturn(mo);

        IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(IllegalStateException.class,
                () -> service.applyRouteProcesses("MANU_ORDER", Map.of("编号", "MO-10", "工艺路线编码", "GY-002")));
        assertTrue(e.getMessage().contains("弃审"), e.getMessage());
    }
}