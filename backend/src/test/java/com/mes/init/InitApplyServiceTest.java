package com.mes.init;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;

/**
 * 初始化落库：单文档面板合并、多单据面板按业务编码建单、自然键判重。
 */
class InitApplyServiceTest {

    private FormDataMapper formMapper;
    private InitApplyService service;
    private final ObjectMapper json = new ObjectMapper();

    @BeforeEach
    void setUp() {
        formMapper = mock(FormDataMapper.class);
        service = new InitApplyService(null, formMapper, json);
    }

    private static Map<String, String> row(String... kv) {
        Map<String, String> row = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) row.put(kv[i], kv[i + 1]);
        row.put("__row", "3");
        return row;
    }

    private FormData doc(String panel, String no, String status, Map<String, Object> head,
                         Map<String, Object> detail) throws Exception {
        FormData d = new FormData();
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus(status);
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        return d;
    }

    // ---------- 存货：单文档合并 ----------

    @Test
    @DisplayName("存货：新编码插入、已存在跳过，合并写回同一张 INV 单据")
    void mergesItemsIntoArchiveDoc() throws Exception {
        FormData inv = doc("INV", "INV", "启用", Map.of(),
                Map.of("items", List.of(Map.of("存货编码", "CP001", "存货名称", "老存货"))));
        when(formMapper.selectOne(any())).thenReturn(inv);

        Map<String, Object> out = service.apply(InitModule.ITEM, List.of(
                row("存货编码", "CP001", "存货名称", "重复的"),
                row("存货编码", "TST-001", "存货名称", "铝棒", "计量单位", "件", "参考成本", "20")), "admin");

        assertEquals(1, out.get("inserted"));
        assertEquals(1, out.get("skipped"));

        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).updateById(captor.capture());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        List<?> items = (List<?>) detail.get("items");
        assertEquals(2, items.size(), "老行保留 + 新增一行");
        Map<?, ?> added = (Map<?, ?>) items.get(1);
        assertEquals("TST-001", added.get("存货编码"));
        assertEquals(20.0, ((Number) added.get("参考成本")).doubleValue(), 0.001);
        assertEquals("全月平均", added.get("计价方式"), "计价方式缺省全月平均");
    }

    @Test
    @DisplayName("存货：全部已存在时不写库（不做无意义更新）")
    void doesNotTouchDocWhenNothingNew() throws Exception {
        FormData inv = doc("INV", "INV", "启用", Map.of(),
                Map.of("items", List.of(Map.of("存货编码", "CP001"))));
        when(formMapper.selectOne(any())).thenReturn(inv);

        Map<String, Object> out = service.apply(InitModule.ITEM,
                List.of(row("存货编码", "CP001")), "admin");

        assertEquals(0, out.get("inserted"));
        verify(formMapper, times(0)).updateById(any(FormData.class));
    }

    // ---------- 工艺路线：一个编码一张单 ----------

    @Test
    @DisplayName("工艺路线：同一路线多行折成一张单，工序按顺序写进 processes")
    void groupsRoutingRows() throws Exception {
        when(formMapper.selectOne(any())).thenReturn(null);
        when(formMapper.selectList(any())).thenReturn(List.of());
        List<FormData> inserted = new ArrayList<>();
        doAnswer(inv -> { inserted.add(inv.getArgument(0)); return 1; })
                .when(formMapper).insert(any(FormData.class));

        Map<String, Object> out = service.apply(InitModule.ROUTING, List.of(
                row("工艺路线编码", "TST-GY1", "工艺路线名称", "测试路线", "工序顺序", "10", "工序编码", "OP10"),
                row("工艺路线编码", "TST-GY1", "工艺路线名称", "测试路线", "工序顺序", "20", "工序编码", "OP20")), "admin");

        assertEquals(1, out.get("inserted"));
        assertEquals(1, inserted.size(), "两条工序行只建一张路线单");
        FormData created = inserted.get(0);
        assertEquals("ROUTE", created.getPanelCode());
        assertEquals("TST-GY1", created.getFormNo(), "单号直接用业务编码，便于反查");
        assertEquals("已审核", created.getStatus());
        Map<?, ?> detail = json.readValue(created.getDetailData(), Map.class);
        List<?> processes = (List<?>) detail.get("processes");
        assertEquals(2, processes.size());
        assertEquals("OP10", ((Map<?, ?>) processes.get(0)).get("工序编码"));
    }

    // ---------- BOM：按「产品 + 子件」判重 ----------

    @Test
    @DisplayName("BOM：已存在父子件跳过，新子件追加到同一张产品单")
    void appendsNewBomChildren() throws Exception {
        FormData bom = doc("BOM", "TST-P1", "已审核",
                Map.of("产品编码", "TST-P1"), Map.of("children", List.of(Map.of("子件编码", "TST-C1"))));
        when(formMapper.selectOne(any())).thenReturn(bom);

        Map<String, Object> out = service.apply(InitModule.BOM, List.of(
                row("产品编码", "TST-P1", "子件编码", "TST-C1", "定额数量", "1"),
                row("产品编码", "TST-P1", "子件编码", "TST-C2", "定额数量", "2.5")), "admin");

        assertEquals(1, out.get("skipped"));
        assertEquals(1, out.get("inserted"));
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).updateById(captor.capture());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        List<?> children = (List<?>) detail.get("children");
        assertEquals(2, children.size());
        assertTrue(children.stream().anyMatch(c -> "TST-C2".equals(((Map<?, ?>) c).get("子件编码"))));
    }

    // ---------- 未实现模块 ----------

    @Test
    @DisplayName("员工与期初库存不在 P1 范围，明确提示而不是静默成功")
    void unsupportedModulesFailLoudly() {
        for (InitModule module : List.of(InitModule.EMPLOYEE, InitModule.OPENING_STOCK)) {
            IllegalStateException e = org.junit.jupiter.api.Assertions.assertThrows(
                    IllegalStateException.class,
                    () -> service.apply(module, List.of(row("x", "1")), "admin"));
            assertTrue(e.getMessage().contains(module.label()), e.getMessage());
        }
    }
}
