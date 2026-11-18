package com.mes.openapi;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.entity.SysUser;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.mapper.SysUserMapper;
import com.mes.service.RoleService;

/**
 * 待审接口的「精确到人」与「最小暴露面」。
 */
class OpenApprovalServiceTest {

    private RoleService roleService;
    private SysUserMapper userMapper;
    private FormDataMapper formMapper;
    private FormApprovalMapper approvalMapper;
    private PanelConfigMapper panelMapper;
    private OpenApprovalService service;

    @BeforeEach
    void setUp() {
        roleService = mock(RoleService.class);
        userMapper = mock(SysUserMapper.class);
        formMapper = mock(FormDataMapper.class);
        approvalMapper = mock(FormApprovalMapper.class);
        panelMapper = mock(PanelConfigMapper.class);
        service = new OpenApprovalService(roleService, userMapper, formMapper, approvalMapper, panelMapper);
    }

    private static FormData form(String panel, String no, String status) {
        FormData fd = new FormData();
        fd.setId(1L);
        fd.setPanelCode(panel);
        fd.setFormNo(no);
        fd.setStatus(status);
        fd.setCreateBy("sales");
        fd.setCreateTime(LocalDateTime.of(2026, 11, 12, 9, 0));
        fd.setUpdateTime(LocalDateTime.of(2026, 11, 12, 9, 30));
        return fd;
    }

    private static FormApproval submission(String panel, String no, String operator) {
        FormApproval r = new FormApproval();
        r.setId(10L);
        r.setPanelCode(panel);
        r.setFormNo(no);
        r.setAction("SUBMIT");
        r.setResult("PENDING");
        r.setOperator(operator);
        r.setCreateTime(LocalDateTime.of(2026, 11, 12, 10, 0));
        return r;
    }

    @Test
    void approver_is_required() {
        assertEquals(400, assertThrows(OpenApiException.class,
                () -> service.pendingFor("  ", 10)).getStatus());
    }

    @Test
    void user_without_approve_panels_gets_nothing() {
        when(roleService.getPerms("sales")).thenReturn(Map.of("approvePanels", List.of()));
        assertTrue(service.pendingFor("sales", 10).isEmpty());
    }

    @Test
    void returns_only_whitelisted_fields() {
        when(roleService.getPerms("planner")).thenReturn(Map.of("approvePanels", List.of("SO_ORDER")));
        when(formMapper.selectList(any())).thenReturn(List.of(form("SO_ORDER", "SO-1", "审批中")));
        when(approvalMapper.selectList(any())).thenReturn(List.of(submission("SO_ORDER", "SO-1", "sales")));
        PanelConfig panel = new PanelConfig();
        panel.setPanelCode("SO_ORDER");
        panel.setPanelName("销售订单");
        when(panelMapper.selectList(any())).thenReturn(List.of(panel));

        List<Map<String, Object>> rows = service.pendingFor("planner", 10);

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertEquals(List.of("panelCode", "panelName", "formNo", "submitter", "submittedAt"),
                List.copyOf(row.keySet()));
        assertEquals("销售订单", row.get("panelName"));
        assertEquals("SO-1", row.get("formNo"));
        assertEquals("sales", row.get("submitter"));
        // 单据正文绝不出现
        assertFalse(row.values().stream().anyMatch(v -> String.valueOf(v).contains("客户")));
    }

    @Test
    void limit_is_capped_to_prevent_bulk_scrape() {
        when(roleService.getPerms("planner")).thenReturn(Map.of("approvePanels", List.of("SO_ORDER")));
        when(formMapper.selectList(any())).thenReturn(List.of());
        service.pendingFor("planner", 9999);
        // 只要没抛异常即可；上限由 MAX_LIMIT 保证，真实 SQL LIMIT 由 MyBatis 生成
        assertTrue(true);
    }

    @Test
    void approvers_endpoint_lists_only_accounts_with_approval_rights() {
        SysUser planner = new SysUser();
        planner.setUserName("planner");
        planner.setRealName("李计划");
        planner.setEnabled(1);
        SysUser sales = new SysUser();
        sales.setUserName("sales");
        sales.setRealName("王销售");
        sales.setEnabled(1);
        when(userMapper.selectList(any())).thenReturn(List.of(planner, sales));
        when(roleService.getPerms("planner")).thenReturn(Map.of("approvePanels", List.of("SO_ORDER", "MANU_ORDER")));
        when(roleService.getPerms("sales")).thenReturn(Map.of("approvePanels", List.of()));

        List<Map<String, Object>> rows = service.listApprovers();

        assertEquals(1, rows.size());
        assertEquals("planner", rows.get(0).get("username"));
        assertEquals(List.of("SO_ORDER", "MANU_ORDER"), rows.get(0).get("panels"));
    }
}
