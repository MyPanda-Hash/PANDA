package com.mes.init;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 初始化的两条硬规则（决策 D3 / D8）与逐行校验。
 */
class InitServiceRulesTest {

    private final org.springframework.jdbc.core.JdbcTemplate jdbc =
            org.mockito.Mockito.mock(org.springframework.jdbc.core.JdbcTemplate.class);
    private final InitService service;

    InitServiceRulesTest() {
        org.mockito.Mockito.when(jdbc.queryForList(org.mockito.ArgumentMatchers.anyString()))
                .thenReturn(List.of());
        service = new InitService(jdbc, new InitTemplateService());
    }

    private static Map<String, String> row(int line, String... kv) {
        Map<String, String> row = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) row.put(kv[i], kv[i + 1]);
        row.put("__row", String.valueOf(line));
        return row;
    }

    // ---------- D3 管理员保护 ----------

    @Test
    @DisplayName("员工表里出现 admin 账号 → 整批拒绝，并指出行号与谁")
    void rejectsAdminAccount() {
        List<Map<String, String>> rows = List.of(
                row(3, "员工编码", "E001", "姓名", "张三", "登录账号", "Admin"));
        List<String> errors = new ArrayList<>();

        InitService.guardAdmin(InitModule.EMPLOYEE, rows, errors);

        assertEquals(1, errors.size());
        assertTrue(errors.get(0).contains("第 3 行"), errors.get(0));
        assertTrue(errors.get(0).contains("张三"), errors.get(0));
        assertTrue(errors.get(0).contains("admin"), errors.get(0));
    }

    @Test
    @DisplayName("职务或职责写「管理员」也拒绝：初始化不创建管理员")
    void rejectsAdminTitleOrDuty() {
        List<Map<String, String>> rows = List.of(
                row(2, "员工编码", "E001", "姓名", "李四", "登录账号", "lisi", "职务", "系统管理员"),
                row(3, "员工编码", "E002", "姓名", "王五", "登录账号", "wangwu", "岗位职责", "负责管理员权限分配"));
        List<String> errors = new ArrayList<>();

        InitService.guardAdmin(InitModule.EMPLOYEE, rows, errors);

        assertEquals(2, errors.size(), errors.toString());
        assertTrue(errors.get(0).contains("李四"));
        assertTrue(errors.get(1).contains("王五"));
    }

    @Test
    @DisplayName("普通业务岗位不受影响")
    void allowsBusinessRoles() {
        List<Map<String, String>> rows = List.of(
                row(2, "员工编码", "E001", "姓名", "张伟", "登录账号", "zhangwei", "职务", "车间主任",
                        "岗位职责", "排产、报工审核"));
        List<String> errors = new ArrayList<>();

        InitService.guardAdmin(InitModule.EMPLOYEE, rows, errors);

        assertTrue(errors.isEmpty(), errors.toString());
    }

    @Test
    @DisplayName("非员工模块不做管理员检查（存货里填「管理员」当类别也不该被拦）")
    void guardOnlyForEmployees() {
        List<Map<String, String>> rows = List.of(row(2, "存货编码", "CP001", "所属类别", "管理员"));
        List<String> errors = new ArrayList<>();

        InitService.guardAdmin(InitModule.ITEM, rows, errors);

        assertTrue(errors.isEmpty());
    }

    // ---------- 逐行校验 ----------

    @Test
    @DisplayName("必填为空、编码重复、数字列非数字都报出来，且带行号")
    void validatesRows() {
        List<Map<String, String>> rows = List.of(
                row(2, "存货编码", "CP001", "存货名称", "铝棒", "计量单位", "件", "参考成本", "20"),
                row(3, "存货编码", "CP001", "存货名称", "", "计量单位", "件", "参考成本", "abc"));
        List<String> errors = new ArrayList<>();
        List<String> warnings = new ArrayList<>();

        service.validate(InitModule.ITEM, rows, errors, warnings);

        assertTrue(errors.stream().anyMatch(e -> e.contains("第 3 行") && e.contains("存货名称")), errors.toString());
        assertTrue(errors.stream().anyMatch(e -> e.contains("CP001") && e.contains("重复")), errors.toString());
        assertTrue(errors.stream().anyMatch(e -> e.contains("参考成本") && e.contains("不是数字")), errors.toString());
    }

    @Test
    @DisplayName("期初库存上传时提示「要核对确认才落库」")
    void openingStockWarnsAboutConfirmation() {
        List<Map<String, String>> rows = List.of(
                row(2, "仓库", "原料仓", "存货编码", "CL001", "数量", "500", "主单价", "4.2"));
        List<String> errors = new ArrayList<>();
        List<String> warnings = new ArrayList<>();

        service.validate(InitModule.OPENING_STOCK, rows, errors, warnings);

        assertTrue(errors.isEmpty(), errors.toString());
        assertTrue(warnings.stream().anyMatch(w -> w.contains("核对")), warnings.toString());
    }

    // ---------- 模块契约 ----------

    @Test
    @DisplayName("落库顺序：BOM 依赖存货与工艺路线，期初依赖存货，员工依赖部门")
    void moduleDependencies() {
        assertEquals(List.of(), InitModule.DEPT.requires());
        assertEquals(List.of(InitModule.DEPT), InitModule.EMPLOYEE.requires());
        assertEquals(List.of(InitModule.DEPT), InitModule.ITEM.requires());
        assertEquals(List.of(InitModule.PROCESS), InitModule.ROUTING.requires());
        assertEquals(List.of(InitModule.ITEM, InitModule.ROUTING), InitModule.BOM.requires());
        assertEquals(List.of(InitModule.ITEM), InitModule.OPENING_STOCK.requires());
    }

    @Test
    @DisplayName("期初库存是唯一需要「核对确认」的模块，不能直接 apply")
    void onlyOpeningStockNeedsConfirmation() {
        assertTrue(InitModule.OPENING_STOCK.needsConfirmation());
        for (InitModule m : InitModule.values()) {
            if (m != InitModule.OPENING_STOCK) assertFalse(m.needsConfirmation(), m + " 不该走确认流程");
        }
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.apply("openingStock", List.of(), "admin"));
        assertTrue(e.getMessage().contains("核对"), e.getMessage());
    }

    @Test
    @DisplayName("模块编码可反查，未知编码明确报错")
    void moduleLookup() {
        assertEquals(InitModule.ITEM, InitModule.of("item"));
        assertEquals(InitModule.ITEM, InitModule.of("存货"));
        assertThrows(IllegalArgumentException.class, () -> InitModule.of("nope"));
    }
}
