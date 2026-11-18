package com.mes.init;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 「谁审批什么」：岗位模板可显式指定，审批面板可逐人写死。
 */
class InitRoleScopeTest {

    @Test
    @DisplayName("岗位模板列能按中文标签识别（不再只靠关键词猜）")
    void resolvesTemplateByLabel() {
        assertEquals(InitRoleTemplates.Role.QC, InitRoleTemplates.byLabel("质检"));
        assertEquals(InitRoleTemplates.Role.WMS, InitRoleTemplates.byLabel("仓管"));
        assertEquals(InitRoleTemplates.Role.PRODUCTION, InitRoleTemplates.byLabel("生产"));
        assertEquals(InitRoleTemplates.Role.GENERAL, InitRoleTemplates.byLabel("通用"));
        assertEquals(InitRoleTemplates.Role.QC, InitRoleTemplates.byLabel("QC"));
        assertNull(InitRoleTemplates.byLabel(""));
        assertNull(InitRoleTemplates.byLabel("不存在的岗位"));
    }

    @Test
    @DisplayName("显式审批面板：每个面板都给到 audit，who/审批什么 由表决定")
    void grantsExplicitPanelsWithAudit() {
        Map<String, List<String>> grants = InitRoleTemplates.grantsForPanels(
                List.of("SO_ORDER", "SALE_OUT"));

        assertEquals(2, grants.size());
        for (String panel : List.of("SO_ORDER", "SALE_OUT")) {
            List<String> perms = grants.get(panel);
            assertTrue(perms.contains("audit"), panel + " 应给审批权：" + perms);
            assertTrue(perms.contains("view") && perms.contains("query"));
        }
    }

    @Test
    @DisplayName("审批面板列支持逗号/顿号/分号/空格混写")
    void splitsPanelCodes() {
        assertEquals(List.of("SO_ORDER", "PU_ORDER", "INSPECTION"),
                InitApplyService.splitCodes("SO_ORDER,PU_ORDER、INSPECTION"));
        assertEquals(List.of("A", "B"), InitApplyService.splitCodes("A;B  A".replace("  A", "")));
        assertEquals(List.of(), InitApplyService.splitCodes(""));
        assertEquals(List.of(), InitApplyService.splitCodes("  ,  "));
    }
}
