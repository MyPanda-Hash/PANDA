package com.mes.openapi;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.entity.StockBalance;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.mapper.StockBalanceMapper;
import com.mes.service.PxService;

/**
 * 只读业务接口的暴露面：只回面板配置里声明过的字段，成本/口令类一律不出。
 */
class OpenBizServiceTest {

    private static final String CONFIG = """
            {
              "dataSchema": {"fields": [
                {"dataName": "单据编号"}, {"dataName": "客户"}, {"dataName": "成本价"}, {"dataName": "密码"}
              ]},
              "detail": {"tabs": [
                {"key": "items", "label": "明细", "fields": [
                  {"dataName": "存货名称"}, {"dataName": "数量"}, {"dataName": "成本价"}
                ]}
              ]}
            }
            """;

    private PanelConfigMapper panelMapper;
    private FormDataMapper formMapper;
    private StockBalanceMapper stockMapper;
    private PxService pxService;
    private OpenBizService service;

    @BeforeEach
    void setUp() {
        panelMapper = mock(PanelConfigMapper.class);
        formMapper = mock(FormDataMapper.class);
        stockMapper = mock(StockBalanceMapper.class);
        pxService = mock(PxService.class);
        service = new OpenBizService(panelMapper, formMapper, stockMapper, pxService);
    }

    @SuppressWarnings("unchecked")
    private PanelConfig panel(String code, String config) {
        PanelConfig pc = new PanelConfig();
        pc.setPanelCode(code);
        pc.setPanelName("销售出库单");
        pc.setConfig(config);
        when(panelMapper.selectOne(any(Wrapper.class))).thenReturn(pc);
        return pc;
    }

    private FormData form() {
        FormData fd = new FormData();
        fd.setPanelCode("SALE_OUT");
        fd.setFormNo("SO-20261110-001");
        fd.setStatus("已审核");
        fd.setCreateBy("sales");
        fd.setCreateTime(LocalDateTime.of(2026, 11, 10, 9, 0));
        fd.setData("{\"单据编号\":\"SO-20261110-001\",\"客户\":\"华东铝业\",\"成本价\":4.59,\"密码\":\"x\",\"未知字段\":1}");
        fd.setDetailData("{\"items\":[{\"存货名称\":\"轴套 C\",\"数量\":10,\"成本价\":4.59,\"密码\":\"y\"}]}");
        return fd;
    }

    @Test
    @SuppressWarnings("unchecked")
    void form_only_exposes_fields_declared_in_panel_config() {
        panel("SALE_OUT", CONFIG);
        when(formMapper.selectOne(any(Wrapper.class))).thenReturn(form());

        Map<String, Object> out = service.form("SALE_OUT", "SO-20261110-001");
        Map<String, Object> header = (Map<String, Object>) out.get("header");

        assertTrue(header.containsKey("客户"), "配置里声明过的业务字段要返回");
        assertTrue(header.containsKey("单据编号"));
        assertFalse(header.containsKey("成本价"), "成本类字段不出开放接口");
        assertFalse(header.containsKey("密码"), "口令类字段不出开放接口");
        assertFalse(header.containsKey("未知字段"), "没在面板配置里声明的字段不返回");

        List<Map<String, Object>> tabs = (List<Map<String, Object>>) out.get("tabs");
        assertEquals(1, tabs.size());
        Map<String, Object> tab = tabs.get(0);
        assertEquals("items", tab.get("key"));
        List<Map<String, Object>> rows = (List<Map<String, Object>>) tab.get("rows");
        assertEquals(1, rows.size());
        assertTrue(rows.get(0).containsKey("存货名称"));
        assertTrue(rows.get(0).containsKey("数量"));
        assertFalse(rows.get(0).containsKey("成本价"));
        assertFalse(rows.get(0).containsKey("密码"));
    }

    @Test
    void form_carries_document_state_and_mes_link() {
        panel("SALE_OUT", CONFIG);
        when(formMapper.selectOne(any(Wrapper.class))).thenReturn(form());

        Map<String, Object> out = service.form("SALE_OUT", "SO-20261110-001");

        assertEquals("已审核", out.get("status"));
        assertEquals("sales", out.get("createBy"));
        assertEquals("/panelx/form/SALE_OUT?code=SO-20261110-001", out.get("link"));
        assertEquals("销售出库单", out.get("panelName"));
    }

    @Test
    void unknown_panel_is_404() {
        when(panelMapper.selectOne(any(Wrapper.class))).thenReturn(null);

        OpenApiException error = assertThrows(OpenApiException.class, () -> service.form("NOPE", "X-1"));
        assertEquals(404, error.getStatus());
    }

    @Test
    void unknown_document_is_404() {
        panel("SALE_OUT", CONFIG);
        when(formMapper.selectOne(any(Wrapper.class))).thenReturn(null);

        OpenApiException error = assertThrows(OpenApiException.class, () -> service.form("SALE_OUT", "NOPE-1"));
        assertEquals(404, error.getStatus());
        assertTrue(error.getMessage().contains("NOPE-1"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void stock_returns_rows_and_totals() {
        StockBalance a = new StockBalance();
        a.setItemCode("CP001");
        a.setItemName("铝棒");
        a.setWarehouse("原料仓");
        a.setQuantity(new BigDecimal("10.0000"));
        a.setAmount(new BigDecimal("42.0000"));
        StockBalance b = new StockBalance();
        b.setItemCode("CP001");
        b.setItemName("铝棒");
        b.setWarehouse("成品仓");
        b.setQuantity(new BigDecimal("5.5"));
        b.setAmount(new BigDecimal("23.1000"));
        when(stockMapper.selectList(any(Wrapper.class))).thenReturn(List.of(a, b));

        Map<String, Object> out = service.stock("CP001", null, 50);

        List<Map<String, Object>> rows = (List<Map<String, Object>>) out.get("rows");
        assertEquals(2, rows.size());
        assertEquals(0, new BigDecimal("15.5").compareTo((BigDecimal) out.get("数量合计")));
        assertEquals(0, new BigDecimal("65.1").compareTo((BigDecimal) out.get("金额合计")));
        assertEquals("CP001", out.get("存货编码"));
    }

    @Test
    void trace_delegates_to_process_trace() {
        when(pxService.traceProcess("SO-20261110-001")).thenReturn(Map.of("环节数", 39, "卡点单号", ""));

        Map<String, Object> out = service.trace("SO-20261110-001");

        assertEquals(39, out.get("环节数"));
        assertEquals("SO-20261110-001", out.get("单号"));
    }
}
