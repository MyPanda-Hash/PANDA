package com.mes.init;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
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
 * 新增的三个基础数据模块：往来单位（客户/供应商）、计量单位、仓库。
 */
class InitApplyMasterDataTest {

    private FormDataMapper formMapper;
    private InitApplyService service;
    private final ObjectMapper json = new ObjectMapper();

    @BeforeEach
    void setUp() {
        formMapper = mock(FormDataMapper.class);
        service = new InitApplyService(null, formMapper, json,
                new org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder(), null);
    }

    private static Map<String, String> row(String... kv) {
        Map<String, String> row = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) row.put(kv[i], kv[i + 1]);
        row.put("__row", "3");
        return row;
    }

    private FormData doc(String panel, String no, Map<String, Object> head, Map<String, Object> detail)
            throws Exception {
        FormData d = new FormData();
        d.setPanelCode(panel);
        d.setFormNo(no);
        d.setStatus("启用");
        d.setData(json.writeValueAsString(head));
        d.setDetailData(json.writeValueAsString(detail));
        return d;
    }

    @Test
    @DisplayName("往来单位：一个编码一张单，性质写客户/供应商；已存在跳过")
    void applyPartner() throws Exception {
        FormData existing = doc("PARTNER", "KH001", Map.of("往来单位编码", "KH001"), Map.of());
        when(formMapper.selectList(any())).thenReturn(List.of(existing));
        List<FormData> inserted = new ArrayList<>();
        doAnswer(inv -> { inserted.add(inv.getArgument(0)); return 1; })
                .when(formMapper).insert(any(FormData.class));

        Map<String, Object> out = service.apply(InitModule.PARTNER, List.of(
                row("往来单位编码", "KH001", "往来单位名称", "重复客户", "性质", "客户"),
                row("往来单位编码", "GYS001", "往来单位名称", "测试供应商", "性质", "供应商",
                        "分管部门", "采购部")), "admin");

        assertEquals(1, out.get("inserted"));
        assertEquals(1, out.get("skipped"));
        FormData created = inserted.get(0);
        assertEquals("PARTNER", created.getPanelCode());
        assertEquals("GYS001", created.getFormNo(), "单号用编码，便于反查");
        Map<?, ?> head = json.readValue(created.getData(), Map.class);
        assertEquals("供应商", head.get("性质"));
        assertEquals(false, head.get("结算客户"), "供应商不算结算客户");
    }

    @Test
    @DisplayName("仓库：合并进 WH.stores，允许零库存出库按是/否解析")
    void applyWh() throws Exception {
        FormData whDoc = doc("WH", "仓库", Map.of(), Map.of("stores", List.of()));
        when(formMapper.selectOne(any())).thenReturn(whDoc);

        Map<String, Object> out = service.apply(InitModule.WH, List.of(
                row("仓库编码", "CK09", "仓库名称", "测试仓", "仓库类别", "成品仓",
                        "负责人", "王五", "允许零库存出库", "是")), "admin");

        assertEquals(1, out.get("inserted"));
        ArgumentCaptor<FormData> captor = ArgumentCaptor.forClass(FormData.class);
        verify(formMapper).updateById(captor.capture());
        Map<?, ?> detail = json.readValue(captor.getValue().getDetailData(), Map.class);
        Map<?, ?> store = (Map<?, ?>) ((List<?>) detail.get("stores")).get(0);
        assertEquals("CK09", store.get("仓库编码"));
        assertEquals(true, store.get("允许零库存出库"));
        assertEquals(false, store.get("停用"));
        assertTrue(String.valueOf(store.get("编号")).startsWith("WH-"));
    }
}
