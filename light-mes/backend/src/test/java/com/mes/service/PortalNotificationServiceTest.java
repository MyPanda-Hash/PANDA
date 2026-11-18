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
import static org.mockito.Mockito.never;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.contains;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PortalNotificationServiceTest {

    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private PanelConfigMapper panelMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private RoleService roleService;
    @Mock private AiNoticeClient aiNotices;
    @Mock private org.springframework.jdbc.core.JdbcTemplate jdbc;

    private PortalNotificationService service;

    @BeforeEach
    void setUp() {
        service = new PortalNotificationService(
                formMapper, approvalMapper, panelMapper, reportQueryService, roleService, aiNotices, jdbc);
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
    void messagesOnlyCarryUnreadGroupChatFromDigitalEmployee() {
        // 口径变更（2026-11-13）：消息不再混入 MES 审批结果，只显示数字员工群聊未读，
        // 并且按已读游标过滤（游标之后的消息才算未读）
        // 群游标查询：假库没有数据，等于「全部未读」
        org.mockito.Mockito.doNothing().when(jdbc).query(
                anyString(), any(org.springframework.jdbc.core.RowCallbackHandler.class), eq("alice"));
        when(aiNotices.fetch(eq("alice"), isNull())).thenReturn(Map.of("bound", true, "todos", List.of()));
        when(aiNotices.groupUrl("grp_1")).thenReturn("http://localhost:18091/group?group=grp_1");
        when(aiNotices.messages(any())).thenReturn(List.of(Map.of(
                "id", "gmsg_1",
                "groupId", "grp_1",
                "groupName", "AI 调度台",
                "text", "今天的 MES 待办汇总",
                "createdAt", "2026-11-13 08:03:08")));

        List<Map<String, Object>> result = service.list("alice", "message");

        // 消息按群聊分类：一个群一条，带未读条数与消息明细
        assertEquals(1, result.size());
        Map<String, Object> group = result.get(0);
        assertEquals("ai-group:grp_1", group.get("id"));
        assertEquals("AI 调度台", group.get("title"));
        assertEquals(1, group.get("unreadCount"));
        assertTrue(String.valueOf(group.get("content")).startsWith("未读 1 条"));
        assertEquals("grp_1", group.get("groupId"));
        assertEquals(Boolean.TRUE, group.get("external"));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> messages = (List<Map<String, Object>>) group.get("messages");
        assertEquals(1, messages.size());
        assertEquals("今天的 MES 待办汇总", messages.get(0).get("text"));
        // 不再查审批流水：消息只有一个来源
        verify(approvalMapper, never()).selectList(any());
    }

    @Test
    void markMessageReadPushesPerGroupCursorAndReturnsClearedCount() {
        // 群游标查询：假库没有数据，等于「全部未读」
        org.mockito.Mockito.doNothing().when(jdbc).query(
                anyString(), any(org.springframework.jdbc.core.RowCallbackHandler.class), eq("alice"));
        when(aiNotices.fetch(eq("alice"), isNull())).thenReturn(Map.of("bound", true, "todos", List.of()));
        when(aiNotices.messages(any())).thenReturn(List.of(
                Map.of("id", "gmsg_1", "groupId", "grp_1", "groupName", "群", "text", "a", "createdAt", "2026-11-13 08:00:00"),
                Map.of("id", "gmsg_2", "groupId", "grp_1", "groupName", "群", "text", "b", "createdAt", "2026-11-13 08:01:00")));

        int cleared = service.markMessageRead("alice", "grp_1");

        assertEquals(2, cleared);
        verify(jdbc).update(contains("insert into portal_notice_read_group"),
                eq("alice"), eq("grp_1"), any(Object.class));
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
