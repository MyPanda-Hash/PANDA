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
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 加工单「选工艺路线」按产成品逐个选：一张加工单可能同时生产多个产品，
 * 每个产品各走各的路线；选中的路线回填该产品对应的物料清单，再按路线重建工序。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceProductRouteTest {

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


    private FormData route(String no, String routeCode, String... processNames) throws Exception {
        FormData d = new FormData();
        d.setPanelCode("ROUTE");
        d.setFormNo(no);
        d.setStatus("已审核");
        d.setData(json.writeValueAsString(new HashMap<>(Map.of(
                "工艺路线编码", routeCode, "工艺路线名称", routeCode + " 路线"))));
        List<Map<String, Object>> rows = new ArrayList<>();
        int seq = 0;
        for (String name : processNames) {
            seq++;
            rows.add(new HashMap<>(Map.of(
                    "加工顺序", seq, "工序编码", "PX00" + seq, "工序名称", name, "生产车间", "熔铸车间")));
        }
        d.setDetailData(json.writeValueAsString(Map.of("processes", rows)));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private FormData bom(String no, String parent, String routeCode) throws Exception {
        FormData d = new FormData();
        d.setPanelCode("BOM");
        d.setFormNo(no);
        d.setStatus("已审核");
        Map<String, Object> head = new HashMap<>();
        head.put("备注", "物料清单");
        if (routeCode != null && !routeCode.isEmpty()) head.put("工艺路线编码", routeCode);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(Map.of("children", List.of(new HashMap<>(Map.of(
                "父件编码", parent, "子件编码", "CL002", "默认BOM", true))))));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private FormData manuOrder(String no, String status, String... products) throws Exception {
        FormData d = new FormData();
        d.setPanelCode("MANU_ORDER");
        d.setFormNo(no);
        d.setStatus(status);
        d.setData(json.writeValueAsString(new HashMap<>(Map.of("编号", no))));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (String p : products) {
            rows.add(new HashMap<>(Map.of("产品编码", p, "产品名称", p + "名称", "数量", 10)));
        }
        d.setDetailData(json.writeValueAsString(Map.of("products", rows, "processes", List.of())));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private Map<String, Object> call(FormData mo, FormData... others) {
        List<FormData> pool = new ArrayList<>();
        pool.add(mo);
        pool.addAll(List.of(others));
        // formOf(面板,单号) 与 docs() 都走 selectList，按面板+单号过滤
        lenient().when(formMapper.selectList(any())).thenAnswer(inv -> {
            List<FormData> out = new ArrayList<>();
            for (FormData f : pool) out.add(f);
            return out;
        });
        // 只有真正走到 formOf 的用例才会用到；「已审核拒绝」用例在取单据前就抛了
        lenient().when(formMapper.selectOne(any())).thenReturn(mo);

        Map<String, Object> formData = new HashMap<>();
        formData.put("编号", mo.getFormNo());
        formData.put("items", List.of(
                new HashMap<>(Map.of("产品编码", "CP001", "工艺路线编码", "GY-CP001")),
                new HashMap<>(Map.of("产品编码", "CP002", "工艺路线编码", "GY-CP002"))));
        return service.callButton("MANU_ORDER", "选工艺路线", formData, null);
    }

    @Test
    void pickedRouteIsWrittenBackIntoProductBom() throws Exception {
        FormData mo = manuOrder("MO-1", "草稿", "CP001", "CP002");
        FormData bom1 = bom("BOM-1", "CP001", "");
        FormData bom2 = bom("BOM-2", "CP002", "GY-OLD");

        call(mo, bom1, bom2, route("R-1", "GY-CP001", "下料", "车削"), route("R-2", "GY-CP002", "铣削"));

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper, atLeastOnce()).updateById(captor.capture());
        Map<String, String> routesOfBom = new HashMap<>();
        for (FormData saved : captor.getAllValues()) {
            if (!"BOM".equals(saved.getPanelCode())) continue;
            Map<?, ?> head = json.readValue(saved.getData(), Map.class);
            routesOfBom.put(saved.getFormNo(), String.valueOf(head.get("工艺路线编码")));
        }
        assertEquals("GY-CP001", routesOfBom.get("BOM-1"), "没配路线的产品要回填");
        assertEquals("GY-CP002", routesOfBom.get("BOM-2"), "手工选的路线应覆盖旧值");
    }

    @Test
    void processesAreBuiltPerProduct() throws Exception {
        FormData mo = manuOrder("MO-2", "草稿", "CP001", "CP002");
        call(mo, bom("BOM-1", "CP001", ""), bom("BOM-2", "CP002", ""),
                route("R-1", "GY-CP001", "下料", "车削"), route("R-2", "GY-CP002", "铣削"));

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper, atLeastOnce()).updateById(captor.capture());
        FormData savedMo = captor.getAllValues().stream()
                .filter(d -> "MANU_ORDER".equals(d.getPanelCode())).findFirst().orElseThrow();
        Map<?, ?> detail = json.readValue(savedMo.getDetailData(), Map.class);
        List<?> procs = (List<?>) detail.get("processes");
        assertEquals(3, procs.size(), "两个产品各自路线的工序合起来 3 道");
        List<String> owners = new ArrayList<>();
        for (Object o : procs) owners.add(String.valueOf(((Map<?, ?>) o).get("产品编码")));
        assertTrue(owners.contains("CP001") && owners.contains("CP002"), "工序行要带产品编码，否则报工分不清是哪件");
    }

    @Test
    void productWithoutRouteIsReported() throws Exception {
        FormData mo = manuOrder("MO-3", "草稿", "CP001", "CP003");
        Map<String, Object> out = call(mo, bom("BOM-1", "CP001", ""),
                route("R-1", "GY-CP001", "下料"));
        assertEquals(List.of("CP003"), out.get("未配置"), "没选路线的产品单独报出来，不能静默丢掉");
    }

    @Test
    void approvedOrderRejectsRouteChange() throws Exception {
        FormData mo = manuOrder("MO-4", "已审核", "CP001");
        assertThrows(RuntimeException.class, () -> call(mo, route("R-1", "GY-CP001", "下料")),
                "已审核的加工单不允许再改工序");
    }
}
