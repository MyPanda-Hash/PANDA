package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PxServiceOcrActionTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;

    private final ObjectMapper json = new ObjectMapper();

    @Test
    void appendsScanFormToMoreWithoutMutatingConfiguredActions() throws Exception {
        List<String> configuredActions = List.of("复制");
        when(panelMapper.selectOne(any())).thenReturn(panel("单据", false, configuredActions));
        var service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);

        Map<String, Object> config = service.getPanelConfig("PU_REQ");

        List<Map<String, Object>> groups = groups(config);
        List<?> actions = (List<?>) groups.stream()
                .filter(group -> "更多".equals(group.get("name"))).findFirst().orElseThrow().get("actions");
        assertEquals(List.of("复制", "扫描填单"), actions);
        assertEquals(List.of("复制"), configuredActions);
    }

    @Test
    void doesNotExposeScanFormOnReadonlyDocument() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(panel("单据", true, List.of("复制")));
        var service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);

        Map<String, Object> config = service.getPanelConfig("READONLY_DOC");

        assertFalse(groups(config).stream().flatMap(group -> ((List<?>) group.get("actions")).stream())
                .anyMatch("扫描填单"::equals));
    }

    @Test
    void exposesScanFormOnOpeningBalanceDocument() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(panel("期初单据", false, List.of("刷新")));
        var service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);

        Map<String, Object> config = service.getPanelConfig("INIT_BALANCE");

        assertEquals(1, groups(config).stream().flatMap(group -> ((List<?>) group.get("actions")).stream())
                .filter("扫描填单"::equals).count());
    }

    private PanelConfig panel(String category, boolean readonly, List<String> actions) throws Exception {
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode("PU_REQ");
        panel.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCategory", category,
                        "readonly", readonly,
                        "buttonGroups", List.of(Map.of("name", "更多", "actions", actions)),
                        "panelButtons", List.of(),
                        "panelPageDto", Map.of("tablePages", List.of(), "formPages", List.of())),
                "dataSchema", Map.of("fields", List.of()),
                "detail", Map.of("tabs", List.of()))));
        return panel;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> groups(Map<String, Object> config) {
        return (List<Map<String, Object>>) ((Map<String, Object>) config.get("metadata")).get("buttonGroups");
    }
}
