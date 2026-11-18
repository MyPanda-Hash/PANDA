package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 采购退货单面板（2026-11-05）：
 * 采购退货走审批流、明细网格、可生成红字发票；销售退货保持直接审核。
 */
@ExtendWith(MockitoExtension.class)
class ReturnPanelRegistryTest {

    @Mock private PanelConfigMapper panelMapper;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void purchaseReturnUsesApprovalFlowAndInvoiceEntry() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(null);

        new ReturnPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(2)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(java.util.stream.Collectors.toMap(PanelConfig::getPanelCode, value -> value));
        Map<String, Object> purchase = parse(configs.get("PURCHASE_RETURN"));

        List<Map<String, Object>> groups = listOfMaps(map(purchase.get("metadata")).get("buttonGroups"));
        Map<String, Object> approval = groups.stream()
                .filter(group -> "审批".equals(group.get("name"))).findFirst().orElseThrow();
        assertEquals(List.of("提交审批", "审批通过", "审批驳回", "审批情况", "弃审"), approval.get("actions"));

        Map<String, Object> generate = groups.stream()
                .filter(group -> "生单".equals(group.get("name"))).findFirst().orElseThrow();
        assertEquals(List.of("生成采购发票（采购退货）"), generate.get("actions"));

        Map<String, Object> table = listOfMaps(
                map(map(purchase.get("metadata")).get("panelPageDto")).get("tablePages")).get(0);
        Map<String, Object> grid = listOfMaps(table.get("gridTabs")).get(0);
        assertEquals("detail", grid.get("rowSource"));
        assertEquals("明细", grid.get("label"));
        assertTrue(((List<?>) grid.get("columns")).contains("实收数量"));

        // 红字发票前置字段：入库单号 + 退货行含税金额/税额必须可存可显
        List<Map<String, Object>> headFields = listOfMaps(map(purchase.get("dataSchema")).get("fields"));
        assertTrue(headFields.stream().anyMatch(field -> "入库单号".equals(field.get("dataName"))));
        List<Map<String, Object>> detailFields = listOfMaps(
                listOfMaps(map(purchase.get("detail")).get("tabs")).get(0).get("fields"));
        assertTrue(detailFields.stream().anyMatch(field -> "含税金额".equals(field.get("dataName"))));
        assertTrue(detailFields.stream().anyMatch(field -> "税额".equals(field.get("dataName"))));
    }

    @Test
    void saleReturnKeepsDirectAudit() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(null);

        new ReturnPanelRegistry(panelMapper, json).syncPanelConfigs();

        ArgumentCaptor<PanelConfig> inserted = ArgumentCaptor.forClass(PanelConfig.class);
        verify(panelMapper, times(2)).insert(inserted.capture());
        Map<String, PanelConfig> configs = inserted.getAllValues().stream()
                .collect(java.util.stream.Collectors.toMap(PanelConfig::getPanelCode, value -> value));
        Map<String, Object> sale = parse(configs.get("SALE_RETURN"));

        List<Map<String, Object>> groups = listOfMaps(map(sale.get("metadata")).get("buttonGroups"));
        assertTrue(groups.stream().anyMatch(group -> "审核".equals(group.get("name"))));
        assertFalse(groups.stream().anyMatch(group -> "审批".equals(group.get("name"))));
        assertFalse(groups.stream().anyMatch(group -> "生单".equals(group.get("name"))));
    }

    private Map<String, Object> parse(PanelConfig row) throws Exception {
        return json.readValue(row.getConfig(), new com.fasterxml.jackson.core.type.TypeReference<>() {});
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> m ? (Map<String, Object>) m : new java.util.LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOfMaps(Object value) {
        List<Map<String, Object>> out = new java.util.ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) if (item instanceof Map<?, ?> m) out.add((Map<String, Object>) m);
        }
        return out;
    }
}
