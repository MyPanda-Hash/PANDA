package com.mes.service;

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

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PortalNotificationServiceTest {

    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private PanelConfigMapper panelMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private RoleService roleService;

    private PortalNotificationService service;

    @BeforeEach
    void setUp() {
        service = new PortalNotificationService(
                formMapper, approvalMapper, panelMapper, reportQueryService, roleService);
    }

    @Test
    void todoUsesCurrentAccountsApprovalPanelsAndPendingDocuments() {
        when(roleService.getPerms("manager")).thenReturn(Map.of("approvePanels", List.of("SO_ORDER")));
        FormData form = form("SO_ORDER", "SO-001", "creator");
        when(formMapper.selectList(any())).thenReturn(List.of(form));
        when(approvalMapper.selectList(any())).thenReturn(List.of(
                approval(1L, "SO_ORDER", "SO-001", "SUBMIT", "PENDING", "creator")));
        when(panelMapper.selectList(any())).thenReturn(List.of(panel("SO_ORDER", "销售订单")));

        List<Map<String, Object>> result = service.list("manager", "todo");

        assertEquals(1, result.size());
        assertEquals("销售订单 SO-001 待审批", result.get(0).get("title"));
        assertEquals("creator", result.get(0).get("submitter"));
        assertEquals("/panelx/list/SO_ORDER", result.get(0).get("targetPath"));
        assertEquals("SO-001", result.get(0).get("formNo"));
        verify(roleService).getPerms("manager");
    }

    @Test
    void approvalMessagesReturnOnlyToTheSubmittingAccount() {
        when(approvalMapper.selectList(any())).thenReturn(List.of(
                approval(1L, "SO_ORDER", "SO-001", "SUBMIT", "PENDING", "alice"),
                approval(2L, "SO_ORDER", "SO-001", "APPROVE", "APPROVED", "manager"),
                approval(3L, "SO_ORDER", "SO-002", "SUBMIT", "PENDING", "bob"),
                approval(4L, "SO_ORDER", "SO-002", "REJECT", "REJECTED", "manager")));
        when(panelMapper.selectList(any())).thenReturn(List.of(panel("SO_ORDER", "销售订单")));

        List<Map<String, Object>> result = service.list("alice", "message");

        assertEquals(1, result.size());
        assertTrue(String.valueOf(result.get(0).get("title")).contains("SO-001 审批通过"));
        assertEquals("APPROVED", result.get(0).get("result"));
    }

    @Test
    void stockAlarmTriggersOnlyWhenCurrentQuantityIsBelowFiveHundred() {
        when(reportQueryService.currentStockRows()).thenReturn(List.of(
                stock("原料仓", "CL001", "铝锭", "499.99"),
                stock("成品仓", "CP001", "铝棒", "500"),
                stock("辅料仓", "FL001", "切削液", "750")));

        List<Map<String, Object>> result = service.list("alice", "alarm");

        assertEquals(1, result.size());
        assertEquals("低库存：铝锭（原料仓）", result.get(0).get("title"));
        assertEquals(new BigDecimal("499.99"), result.get(0).get("quantity"));
        assertEquals(new BigDecimal("500"), result.get(0).get("threshold"));
        assertEquals("/panelx/list/STOCK_STATUS", result.get(0).get("targetPath"));
    }

    private FormData form(String panelCode, String formNo, String createBy) {
        FormData form = new FormData();
        form.setId(10L);
        form.setPanelCode(panelCode);
        form.setFormNo(formNo);
        form.setStatus("审批中");
        form.setCreateBy(createBy);
        form.setCreateTime(LocalDateTime.of(2026, 8, 27, 9, 0));
        form.setUpdateTime(LocalDateTime.of(2026, 8, 27, 9, 5));
        return form;
    }

    private FormApproval approval(long id, String panelCode, String formNo,
                                        String action, String result, String operator) {
        FormApproval approval = new FormApproval();
        approval.setId(id);
        approval.setPanelCode(panelCode);
        approval.setFormNo(formNo);
        approval.setAction(action);
        approval.setResult(result);
        approval.setOperator(operator);
        approval.setCreateTime(LocalDateTime.of(2026, 8, 27, 10, (int) id));
        return approval;
    }

    private PanelConfig panel(String code, String name) {
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode(code);
        panel.setPanelName(name);
        return panel;
    }

    private Map<String, Object> stock(String warehouse, String code, String name, String quantity) {
        return Map.of(
                "仓库", warehouse,
                "存货编码", code,
                "存货", name,
                "主计量", "件",
                "现存量(主)", new BigDecimal(quantity));
    }
}
