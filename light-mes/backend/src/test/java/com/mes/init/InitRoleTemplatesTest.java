package com.mes.init;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 岗位 → 角色 → 面板权限：给多了是数据事故，给少了用户干不了活，两边都要钉住。
 */
class InitRoleTemplatesTest {

    @Test
    @DisplayName("按职务/职责归类到对应岗位模板")
    void classifiesByTitleAndDuties() {
        assertEquals(InitRoleTemplates.Role.QC, InitRoleTemplates.classify("质检员", "来料检验"));
        assertEquals(InitRoleTemplates.Role.WMS, InitRoleTemplates.classify("仓管员", "收发货"));
        assertEquals(InitRoleTemplates.Role.PURCHASE, InitRoleTemplates.classify("采购专员", "下单跟单"));
        assertEquals(InitRoleTemplates.Role.SALES, InitRoleTemplates.classify("业务员", "接单报价"));
        assertEquals(InitRoleTemplates.Role.PLANNING, InitRoleTemplates.classify("计划员", "排产"));
        assertEquals(InitRoleTemplates.Role.PRODUCTION, InitRoleTemplates.classify("车间主任", "生产管理"));
        assertEquals(InitRoleTemplates.Role.FINANCE, InitRoleTemplates.classify("会计", "对账"));
        assertEquals(InitRoleTemplates.Role.GENERAL, InitRoleTemplates.classify("行政", "后勤"));
    }

    @Test
    @DisplayName("主管判定：主任/经理/主管等给审批，普通岗位不给")
    void detectsSupervisor() {
        assertTrue(InitRoleTemplates.isSupervisor("车间主任"));
        assertTrue(InitRoleTemplates.isSupervisor("采购经理"));
        assertTrue(InitRoleTemplates.isSupervisor("仓储主管"));
        assertFalse(InitRoleTemplates.isSupervisor("质检员"));
        assertFalse(InitRoleTemplates.isSupervisor("仓管员"));
    }

    @Test
    @DisplayName("普通岗位没有删除/价格/调整；主管才有审批与价格")
    void grantsRespectDangerActions() {
        Map<String, List<String>> clerk = InitRoleTemplates.grants(InitRoleTemplates.Role.SALES, false);
        assertTrue(clerk.containsKey("SO_ORDER"));
        List<String> perms = clerk.get("SO_ORDER");
        assertFalse(perms.contains("delete"), "普通销售不该有删除：" + perms);
        assertFalse(perms.contains("price"), "普通销售不该改价：" + perms);
        assertFalse(perms.contains("audit"), "普通销售不该有审批：" + perms);
        assertTrue(perms.contains("add") && perms.contains("edit"));

        Map<String, List<String>> lead = InitRoleTemplates.grants(InitRoleTemplates.Role.SALES, true);
        List<String> leadPerms = lead.get("SO_ORDER");
        assertTrue(leadPerms.contains("audit"));
        assertTrue(leadPerms.contains("price"));
    }

    @Test
    @DisplayName("仓管只有主管能盘点调账（adjust）")
    void onlySupervisorCanAdjustStock() {
        assertFalse(InitRoleTemplates.grants(InitRoleTemplates.Role.WMS, false)
                .get("STOCK_CHECK").contains("adjust"));
        assertTrue(InitRoleTemplates.grants(InitRoleTemplates.Role.WMS, true)
                .get("STOCK_CHECK").contains("adjust"));
    }

    @Test
    @DisplayName("质检拿到的是检验类面板，不该拿到销售采购单")
    void qcOnlyGetsInspectionPanels() {
        Map<String, List<String>> qc = InitRoleTemplates.grants(InitRoleTemplates.Role.QC, false);
        assertTrue(qc.containsKey("INSPECTION"));
        assertTrue(qc.containsKey("OUTSOURCE_INSPECTION"));
        assertFalse(qc.containsKey("SO_ORDER"));
        assertFalse(qc.containsKey("PU_ORDER"));
    }

    @Test
    @DisplayName("角色编码稳定且不含管理员")
    void roleCodesAreStableAndNonAdmin() {
        assertEquals("INIT_R_QC", InitRoleTemplates.roleCode(InitRoleTemplates.Role.QC, false));
        assertEquals("INIT_R_QC_LEAD", InitRoleTemplates.roleCode(InitRoleTemplates.Role.QC, true));
        assertEquals("质检", InitRoleTemplates.roleName(InitRoleTemplates.Role.QC, false));
        assertEquals("质检主管", InitRoleTemplates.roleName(InitRoleTemplates.Role.QC, true));
        for (InitRoleTemplates.Role role : InitRoleTemplates.Role.values()) {
            assertFalse(InitRoleTemplates.roleCode(role, true).toLowerCase().contains("admin"));
        }
    }
}
