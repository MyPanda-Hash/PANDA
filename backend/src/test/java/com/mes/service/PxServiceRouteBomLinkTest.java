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
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 保存工艺路线时自动关联到对应产品的物料清单。
 *
 * <p>背景：工艺路线编码挂在 BOM 表头，不在工艺路线单据上。计划分析跳过来新建路线后，
 * 如果还要人手回 BOM 填一次编码，这条链路就断了 —— 所以按编码约定自动回写：
 * 「GY-&lt;产品编码&gt;」→ 该产品（BOM 父件）的那张 BOM。
 *
 * <p>只在 BOM 还没填过工艺路线时写，绝不覆盖人工已有的选择。
 */
@ExtendWith(MockitoExtension.class)
class PxServiceRouteBomLinkTest {

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

    private PanelConfig config(String code) {
        PanelConfig cfg = new PanelConfig();
        cfg.setPanelCode(code);
        cfg.setPanelName(code);
        cfg.setConfig("{\"metadata\":{\"panelCode\":\"" + code + "\",\"autoCodeField\":\"单据编号\"},"
                + "\"dataSchema\":{\"fields\":[]},\"detail\":{\"tabs\":[]}}");
        return cfg;
    }

    private FormData bom(String no, String productCode, String routeCode) throws Exception {
        FormData d = new FormData();
        d.setId(9L);
        d.setPanelCode("BOM");
        d.setFormNo(no);
        d.setStatus("已审核");
        Map<String, Object> head = new HashMap<>();
        head.put("备注", "物料清单");
        if (routeCode != null && !routeCode.isEmpty()) head.put("工艺路线编码", routeCode);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(Map.of("children", List.of(Map.of(
                "父件编码", productCode, "子件编码", "CL002")))));
        d.setCreateTime(LocalDateTime.now());
        return d;
    }

    private void saveRoute(String routeCode) {
        Map<String, Object> formData = new HashMap<>();
        formData.put("工艺路线编码", routeCode);
        formData.put("工艺路线名称", "测试路线");
        formData.put("detail", Map.of("processes", List.of(
                new HashMap<>(Map.of("工序编码", "PX001", "工序名称", "下料")))));
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(config("ROUTE"));
        service.callButton("ROUTE", "保存", formData, null);
    }

    @Test
    void savingRouteWritesCodeIntoMatchingBom() throws Exception {
        FormData target = bom("BOMF-001", "CP002", "");
        when(formMapper.selectList(any())).thenReturn(List.of(target));

        saveRoute("GY-CP002");

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).updateById(captor.capture());
        Map<?, ?> head = json.readValue(captor.getValue().getData(), Map.class);
        assertEquals("GY-CP002", head.get("工艺路线编码"), "BOM 表头应写回路线编码");
        assertEquals("BOMF-001", captor.getValue().getFormNo());
    }

    @Test
    void existingBomRouteIsNotOverwritten() throws Exception {
        FormData target = bom("BOMF-001", "CP002", "GY-EXISTING");
        when(formMapper.selectList(any())).thenReturn(List.of(target));

        saveRoute("GY-CP002");

        verify(formMapper, never()).updateById(any(FormData.class));
    }

    @Test
    void routeForAnotherProductIsIgnored() throws Exception {
        FormData other = bom("BOMF-003", "CP005", "");
        when(formMapper.selectList(any())).thenReturn(List.of(other));

        saveRoute("GY-CP002");

        verify(formMapper, never()).updateById(any(FormData.class));
    }

    @Test
    void nonConventionalCodeIsIgnored() throws Exception {
        FormData target = bom("BOMF-001", "CP002", "");
        when(formMapper.selectList(any())).thenReturn(List.of(target));

        saveRoute("GY-100");

        verify(formMapper, never()).updateById(any(FormData.class));
    }

    @Test
    void savingNonRoutePanelDoesNotTouchBom() {
        Map<String, Object> formData = new HashMap<>();
        formData.put("单据编号", "XX-1");
        when(formMapper.selectCount(any())).thenReturn(0L);
        when(panelMapper.selectOne(any())).thenReturn(config("OTHER"));
        service.callButton("OTHER", "保存", formData, null);

        verify(formMapper, never()).updateById(any(FormData.class));
        assertTrue(true);
    }
}
