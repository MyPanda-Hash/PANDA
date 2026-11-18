package com.mes.service;

import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.panel.PanelActionRegistry;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.Arrays;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PxServiceAuthorizationTest {

    @Mock private PanelConfigMapper panelMapper;
    @Mock private FormDataMapper formMapper;
    @Mock private FormApprovalMapper approvalMapper;
    @Mock private ReportQueryService reportQueryService;
    @Mock private RoleService roleService;
    @Mock private JdbcTemplate jdbcTemplate;
    @Mock private StockTxnService stockTxnService;

    private PxService service;

    @BeforeEach
    void setUp() {
        service = new PxService(panelMapper, formMapper, approvalMapper, reportQueryService,
                new PanelActionRegistry(List.of()), roleService, jdbcTemplate, stockTxnService);
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("worker", null, List.of()));
    }

    @AfterEach
    void clearContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void deniesListQueryWhenOnlyViewIsGranted() {
        grant("view");

        assertThrows(AccessDeniedException.class,
                () -> service.queryFormDataList("SO_ORDER", "", Map.of(), 1, 20));
        verify(formMapper, never()).selectList(any());
    }

    @Test
    void deniesDeleteWhenDeletePermissionIsMissing() {
        grant("view", "query");

        assertThrows(AccessDeniedException.class,
                () -> service.deleteForms("SO_ORDER", List.of("SO-001")));
        verify(formMapper, never()).selectOne(any());
    }

    @Test
    void deniesApprovalHistoryWithoutAuditPermission() {
        grant("view", "query");

        assertThrows(AccessDeniedException.class,
                () -> service.getApprovalHistory("SO_ORDER", "SO-001"));
        verify(approvalMapper, never()).selectList(any());
    }

    @Test
    void allowsQueryWithQueryPermission() {
        grant("view", "query");
        when(formMapper.selectList(any())).thenReturn(List.of());

        assertDoesNotThrow(() -> service.queryFormDataList("SO_ORDER", "", Map.of(), 1, 20));
    }

    @Test
    void administratorBypassesPanelPermissions() {
        when(roleService.getPerms("worker")).thenReturn(Map.of("isAdmin", true));
        when(formMapper.selectList(any())).thenReturn(List.of());

        assertDoesNotThrow(() -> service.queryFormDataList("SO_ORDER", "", Map.of(), 1, 20));
    }

    private void grant(String... permissions) {
        when(roleService.getPerms("worker")).thenReturn(Map.of(
                "isAdmin", false,
                "panelPerms", Map.of("SO_ORDER", Arrays.asList(permissions))));
    }
}
