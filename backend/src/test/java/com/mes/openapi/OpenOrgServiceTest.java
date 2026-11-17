package com.mes.openapi;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

import com.mes.entity.SysDept;
import com.mes.entity.SysRole;
import com.mes.entity.SysUser;
import com.mes.mapper.SysDeptMapper;
import com.mes.mapper.SysRoleMapper;
import com.mes.mapper.SysUserMapper;

/**
 * 人力地图的数据形状：部门路径要拼对、脏数据不能把接口打挂。
 */
class OpenOrgServiceTest {

    private static SysDept dept(long id, long parentId, String name) {
        SysDept dept = new SysDept();
        dept.setId(id);
        dept.setParentId(parentId);
        dept.setDeptName(name);
        dept.setSort((int) id);
        return dept;
    }

    private static SysUser user(String userName, String realName, Long deptId, Long roleId) {
        SysUser user = new SysUser();
        user.setUserName(userName);
        user.setRealName(realName);
        user.setDeptId(deptId);
        user.setRoleId(roleId);
        user.setEnabled(1);
        return user;
    }

    private static List<SysDept> seededTree() {
        List<SysDept> depts = new ArrayList<>();
        depts.add(dept(1, 0, "公司"));
        depts.add(dept(5, 1, "生产部"));
        depts.add(dept(6, 5, "熔铸车间"));
        depts.add(dept(3, 1, "销售部"));
        return depts;
    }

    @Test
    void pathsJoinFullAncestorChain() {
        Map<Long, String> paths = OpenOrgService.paths(seededTree());

        assertEquals("公司", paths.get(1L));
        assertEquals("公司 / 生产部", paths.get(5L));
        assertEquals("公司 / 生产部 / 熔铸车间", paths.get(6L));
        assertEquals("公司 / 销售部", paths.get(3L));
    }

    @Test
    void orphanParentDegradesToOwnName() {
        List<SysDept> depts = new ArrayList<>();
        depts.add(dept(9, 404, "孤立车间"));

        Map<Long, String> paths = OpenOrgService.paths(depts);

        assertEquals("孤立车间", paths.get(9L), "父节点不存在时只显示本级名字，不能空着");
    }

    @Test
    void cycleTerminatesInsteadOfHanging() {
        List<SysDept> depts = new ArrayList<>();
        depts.add(dept(1, 2, "甲"));
        depts.add(dept(2, 1, "乙"));

        Map<Long, String> paths = OpenOrgService.paths(depts);

        String path = paths.get(1L);
        assertTrue(path.contains("甲") && path.contains("乙"), "成环时仍要给出可读路径，实际=" + path);
        // 每个节点只出现一次，避免无限重复
        assertEquals(path.split(" / ").length, 2, "成环时不能重复堆名字，实际=" + path);
    }

    @Test
    void buildCarriesDepartmentAndRolePerUser() {
        List<SysUser> users = List.of(
                user("worker", "赵车间", 6L, 5L),
                user("admin", "管理员", 1L, 1L),
                user("ghost", "未分配", null, null));
        Map<Long, SysRole> roles = new LinkedHashMap<>();
        SysRole role = new SysRole();
        role.setId(5L);
        role.setRoleCode("WORKER");
        role.setRoleName("车间员工");
        roles.put(5L, role);

        Map<String, Object> out = OpenOrgService.build(seededTree(), users, roles);

        assertEquals(4, ((List<?>) out.get("departments")).size());
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> userRows = (List<Map<String, Object>>) out.get("users");
        assertEquals(3, userRows.size());

        Map<String, Object> worker = userRows.get(0);
        assertEquals("熔铸车间", worker.get("deptName"));
        assertEquals("公司 / 生产部 / 熔铸车间", worker.get("deptPath"));
        assertEquals("车间员工", worker.get("roleName"));

        Map<String, Object> orphan = userRows.get(2);
        assertEquals("", orphan.get("deptName"), "没挂部门时给空串，前端好判断");
        assertEquals("", orphan.get("deptPath"));
    }

    @Test
    void departmentsComeOutParentBeforeChild() {
        // 库里按 sort 排：熔铸车间(sort=1) 会排在 销售部(sort=2) 前面；
        // 但界面要的是树序，父部门得先出现。
        List<SysDept> depts = new ArrayList<>();
        depts.add(dept(1, 0, "公司"));
        depts.add(dept(5, 1, "生产部"));
        depts.add(dept(6, 5, "熔铸车间"));
        depts.add(dept(3, 1, "销售部"));

        Map<String, Object> out = OpenOrgService.build(depts, List.of(), Map.of());

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) out.get("departments");
        List<String> names = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            names.add(String.valueOf(row.get("name")));
        }
        assertEquals(List.of("公司", "生产部", "熔铸车间", "销售部"), names,
                "子部门必须紧跟父部门，不能被 sort 插到前面");
    }

    @Test
    void departmentDepthIsZeroBased() {
        Map<String, Object> out = OpenOrgService.build(seededTree(), List.of(), Map.of());

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) out.get("departments");
        Map<String, Object> byName = new LinkedHashMap<>();
        for (Map<String, Object> row : rows) {
            byName.put(String.valueOf(row.get("name")), row.get("depth"));
        }
        assertEquals(0, byName.get("公司"));
        assertEquals(1, byName.get("生产部"));
        assertEquals(2, byName.get("熔铸车间"));
    }

    @Test
    void orgMapReadsMappersInStableOrder() {
        SysDeptMapper deptMapper = mock(SysDeptMapper.class);
        SysUserMapper userMapper = mock(SysUserMapper.class);
        SysRoleMapper roleMapper = mock(SysRoleMapper.class);
        when(deptMapper.selectList(any())).thenReturn(seededTree());
        when(userMapper.selectList(any())).thenReturn(List.of(user("qc", "孙质检", 6L, 6L)));
        SysRole role = new SysRole();
        role.setId(6L);
        role.setRoleName("质检员");
        when(roleMapper.selectById(6L)).thenReturn(role);

        Map<String, Object> out = new OpenOrgService(deptMapper, userMapper, roleMapper).orgMap();

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> userRows = (List<Map<String, Object>>) out.get("users");
        assertEquals("公司 / 生产部 / 熔铸车间", userRows.get(0).get("deptPath"));
        assertEquals("质检员", userRows.get(0).get("roleName"));
    }
}
