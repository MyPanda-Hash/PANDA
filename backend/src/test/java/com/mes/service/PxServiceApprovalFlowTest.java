package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PxServiceApprovalFlowTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;

    private final ObjectMapper json = new ObjectMapper();
    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService);
    }

    @Test
    void legacyAuditActionRunsCompleteApprovalFlowWithHistory() throws Exception {
        FormData document = document("SO_ORDER", "SO-TEST-001", "草稿");
        List<FormApproval> history = new ArrayList<>();
        when(formMapper.selectOne(any())).thenReturn(document);
        when(panelMapper.selectOne(any())).thenReturn(approvalPanelConfig());
        when(approvalMapper.selectOne(any())).thenAnswer(invocation ->
                history.isEmpty() ? null : history.get(history.size() - 1));
        doAnswer(invocation -> {
            FormApproval record = invocation.getArgument(0);
            record.setId((long) history.size() + 1);
            history.add(record);
            return 1;
        }).when(approvalMapper).insert(any(FormApproval.class));

        Map<String, Object> submitted = service.callButton(
                "SO_ORDER", "审核", Map.of("编号", "SO-TEST-001"), Map.of());

        assertEquals("审批中", submitted.get("单据状态"));
        assertEquals("审批中", document.getStatus());
        assertNull(document.getAuditBy());
        assertEquals(List.of("SUBMIT"), history.stream().map(FormApproval::getAction).toList());

        Map<String, Object> approved = service.callButton(
                "SO_ORDER", "审批通过", Map.of("编号", "SO-TEST-001", "审批意见", "通过"), Map.of());

        assertEquals("已审核", approved.get("单据状态"));
        assertEquals("已审核", document.getStatus());
        assertEquals(List.of("SUBMIT", "APPROVE"), history.stream().map(FormApproval::getAction).toList());
        assertEquals(List.of("PENDING", "APPROVED"), history.stream().map(FormApproval::getResult).toList());
    }

    @Test
    void cannotApproveStatusOnlyDocumentWithoutSubmissionRecord() throws Exception {
        FormData document = document("SO_ORDER", "SO-TEST-002", "审批中");
        when(formMapper.selectOne(any())).thenReturn(document);
        when(approvalMapper.selectOne(any())).thenReturn(null);

        IllegalStateException error = assertThrows(IllegalStateException.class, () ->
                service.callButton("SO_ORDER", "审批通过", Map.of("编号", "SO-TEST-002"), Map.of()));

        assertTrue(error.getMessage().contains("尚未提交审批"));
        assertEquals("审批中", document.getStatus());
    }

    @Test
    void normalizesDuplicateAuditAndApprovalGroups() throws Exception {
        when(panelMapper.selectOne(any())).thenReturn(approvalPanelConfig());

        Map<String, Object> config = service.getPanelConfig("SO_ORDER");
        Map<?, ?> metadata = (Map<?, ?>) config.get("metadata");
        List<?> groups = (List<?>) metadata.get("buttonGroups");
        List<?> workflowGroups = groups.stream()
                .filter(value -> value instanceof Map<?, ?> group && "审批".equals(group.get("name")))
                .toList();

        assertEquals(1, workflowGroups.size());
        Map<?, ?> workflow = (Map<?, ?>) workflowGroups.get(0);
        assertEquals(List.of("提交审批", "审批通过", "审批驳回", "审批情况", "弃审"), workflow.get("actions"));
        assertTrue(groups.stream().noneMatch(value ->
                value instanceof Map<?, ?> group && "审核".equals(group.get("name"))));
    }

    @Test
    void normalizesRegisteredApprovalPanelEvenWhenLegacyConfigOnlyHasAudit() throws Exception {
        PanelConfig panel = approvalPanelConfig();
        @SuppressWarnings("unchecked")
        Map<String, Object> config = json.readValue(panel.getConfig(), Map.class);
        @SuppressWarnings("unchecked")
        Map<String, Object> metadata = (Map<String, Object>) config.get("metadata");
        metadata.put("buttonGroups", List.of(Map.of("name", "审核", "actions", List.of("审核", "弃审"))));
        panel.setConfig(json.writeValueAsString(config));
        when(panelMapper.selectOne(any())).thenReturn(panel);

        Map<String, Object> upgraded = service.getPanelConfig("SO_ORDER");
        Map<?, ?> upgradedMetadata = (Map<?, ?>) upgraded.get("metadata");
        List<?> groups = (List<?>) upgradedMetadata.get("buttonGroups");

        assertEquals(2, groups.size());
        Map<?, ?> workflow = (Map<?, ?>) groups.stream()
                .filter(value -> value instanceof Map<?, ?> group && "审批".equals(group.get("name")))
                .findFirst().orElseThrow();
        assertEquals("审批", workflow.get("name"));
        assertEquals(List.of("提交审批", "审批通过", "审批驳回", "审批情况", "弃审"), workflow.get("actions"));
    }

    private FormData document(String panelCode, String formNo, String status) throws Exception {
        FormData document = new FormData();
        document.setId(1L);
        document.setPanelCode(panelCode);
        document.setFormNo(formNo);
        document.setStatus(status);
        document.setData(json.writeValueAsString(Map.of("单据编号", formNo)));
        document.setDetailData("{}");
        document.setCreateTime(LocalDateTime.now());
        return document;
    }

    private PanelConfig approvalPanelConfig() throws Exception {
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode("SO_ORDER");
        panel.setPanelName("销售订单");
        panel.setCategory("单据");
        panel.setConfig(json.writeValueAsString(Map.of(
                "metadata", Map.of(
                        "panelCode", "SO_ORDER",
                        "panelName", "销售订单",
                        "panelCategory", "单据",
                        "autoCodeField", "单据编号",
                        "panelButtons", List.of(),
                        "buttonGroups", List.of(
                                Map.of("name", "保存", "actions", List.of("保存")),
                                Map.of("name", "审核", "actions", List.of(
                                        "审核", "提交审批", "审批通过", "审批驳回", "审批情况", "弃审")),
                                Map.of("name", "审批", "actions", List.of("提交审批", "审批通过", "驳回审批"))),
                        "panelPageDto", Map.of("tablePages", List.of(), "formPages", List.of())),
                "dataSchema", Map.of("fields", List.of(
                        Map.of("dataName", "单据日期", "dataType", "日期"),
                        Map.of("dataName", "单据编号", "dataType", "文本", "autoCode", true))),
                "detail", Map.of("tabs", List.of()))));
        return panel;
    }
}
