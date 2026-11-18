package com.mes.openapi;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.SysDept;
import com.mes.entity.SysRole;
import com.mes.entity.SysUser;
import com.mes.mapper.SysDeptMapper;
import com.mes.mapper.SysRoleMapper;
import com.mes.mapper.SysUserMapper;

/**
 * 组织架构只读视图：部门树 + 每个人的部门路径与岗位。
 *
 * <p>为什么单开一个开放接口：AI 助理的「人力地图」需要按部门组织展示群成员，
 * 而 MES 这边才是部门数据的唯一来源（{@code sys_dept} / {@code sys_user.dept_id}）。
 * 让 AI 直接查库会把两个系统的表结构绑死，所以按项目既有约定走开放接口。
 *
 * <p>设计取舍：
 * <ul>
 *   <li><b>一次返回树和人</b>：人力地图要同时用到层级和归属，分两次调用只会多一次往返。</li>
 *   <li><b>路径在服务端拼</b>：部门路径（如「公司 / 生产部 / 熔铸车间」）涉及父链回溯，
 *       放前端拼会让每个调用方各写一遍，还容易踩到环。</li>
 *   <li><b>环与孤儿都兜住</b>：脏数据（父节点不存在、互相引用）不能让整个接口 500，
 *       退化成本级名字即可。</li>
 *   <li><b>只读</b>：本服务不写任何表，改部门仍在「系统设置 → 组织架构」页面。</li>
 * </ul>
 */
@Service
public class OpenOrgService {

    /** 父链回溯的最大深度：脏数据成环时不至于死循环。 */
    private static final int MAX_DEPTH = 16;

    private static final String SEPARATOR = " / ";

    private final SysDeptMapper deptMapper;
    private final SysUserMapper userMapper;
    private final SysRoleMapper roleMapper;

    public OpenOrgService(SysDeptMapper deptMapper, SysUserMapper userMapper, SysRoleMapper roleMapper) {
        this.deptMapper = deptMapper;
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
    }

    /** {@code {departments: [...], users: [...]}}，部门按 sort/id 升序，用户按 id 升序。 */
    public Map<String, Object> orgMap() {
        List<SysDept> depts = deptMapper.selectList(new LambdaQueryWrapper<SysDept>()
                .orderByAsc(SysDept::getSort)
                .orderByAsc(SysDept::getId));
        List<SysUser> users = userMapper.selectList(new LambdaQueryWrapper<SysUser>()
                .orderByAsc(SysUser::getId));
        Map<Long, SysRole> roles = new HashMap<>();
        for (SysUser user : users) {
            Long roleId = user.getRoleId();
            if (roleId != null && !roles.containsKey(roleId)) {
                SysRole role = roleMapper.selectById(roleId);
                if (role != null) {
                    roles.put(roleId, role);
                }
            }
        }
        return build(depts == null ? List.of() : depts, users == null ? List.of() : users, roles);
    }

    static Map<String, Object> build(List<SysDept> depts, List<SysUser> users, Map<Long, SysRole> roles) {
        Map<Long, String> paths = paths(depts);
        // 按树的先序遍历输出：父部门一定排在子部门前面。
        // 直接按 sort 排会把「熔铸车间」插到「销售部」前面，读起来不像一棵树。
        List<SysDept> ordered = preOrder(depts);
        Map<Long, SysDept> byId = new HashMap<>();
        for (SysDept dept : depts) {
            byId.put(dept.getId(), dept);
        }

        List<Map<String, Object>> departmentRows = new ArrayList<>();
        for (SysDept dept : ordered) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", dept.getId());
            row.put("parentId", dept.getParentId() == null ? 0L : dept.getParentId());
            row.put("name", dept.getDeptName());
            row.put("path", paths.getOrDefault(dept.getId(), dept.getDeptName()));
            row.put("depth", depthOf(dept, byId));
            row.put("sort", dept.getSort() == null ? 0 : dept.getSort());
            departmentRows.add(row);
        }

        List<Map<String, Object>> userRows = new ArrayList<>();
        for (SysUser user : users) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("userName", user.getUserName());
            row.put("realName", user.getRealName());
            row.put("enabled", user.getEnabled());
            row.put("deptId", user.getDeptId());
            SysDept dept = user.getDeptId() == null ? null : byId.get(user.getDeptId());
            row.put("deptName", dept == null ? "" : dept.getDeptName());
            row.put("deptPath", user.getDeptId() == null
                    ? ""
                    : paths.getOrDefault(user.getDeptId(), ""));
            SysRole role = user.getRoleId() == null ? null : roles.get(user.getRoleId());
            row.put("roleCode", role == null ? "" : role.getRoleCode());
            row.put("roleName", role == null ? "" : role.getRoleName());
            userRows.add(row);
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("departments", departmentRows);
        out.put("users", userRows);
        return out;
    }

    /**
     * 每个部门的完整路径（含自身）。父链缺失或成环时退化为已完成的那一段。
     */
    static Map<Long, String> paths(List<SysDept> depts) {
        Map<Long, SysDept> byId = new HashMap<>();
        for (SysDept dept : depts) {
            if (dept.getId() != null) {
                byId.put(dept.getId(), dept);
            }
        }
        Map<Long, String> out = new HashMap<>();
        for (SysDept dept : depts) {
            if (dept.getId() == null) {
                continue;
            }
            List<String> names = new ArrayList<>();
            Set<Long> seen = new HashSet<>();
            SysDept cursor = dept;
            int guard = 0;
            while (cursor != null && cursor.getId() != null && seen.add(cursor.getId()) && guard++ < MAX_DEPTH) {
                String name = cursor.getDeptName() == null ? "" : cursor.getDeptName().trim();
                if (!name.isEmpty()) {
                    names.add(0, name);
                }
                Long parentId = cursor.getParentId();
                // parentId 为 0 / null 就是根，停止回溯
                cursor = (parentId == null || parentId == 0L) ? null : byId.get(parentId);
            }
            out.put(dept.getId(), String.join(SEPARATOR, names));
        }
        return out;
    }

    /**
     * 先序遍历：父在子前，兄弟保持传入顺序（数据库已按 sort/id 排好）。
     * 父节点缺失的孤儿当根处理；成环的部门在遍历末尾兜底补上，保证一个都不丢。
     */
    static List<SysDept> preOrder(List<SysDept> depts) {
        Map<Long, List<SysDept>> children = new HashMap<>();
        Set<Long> ids = new HashSet<>();
        for (SysDept dept : depts) {
            if (dept.getId() != null) {
                ids.add(dept.getId());
            }
        }
        List<SysDept> roots = new ArrayList<>();
        for (SysDept dept : depts) {
            if (dept.getId() == null) {
                continue;
            }
            Long parentId = dept.getParentId();
            if (parentId == null || parentId == 0L || !ids.contains(parentId)) {
                roots.add(dept);
            } else {
                children.computeIfAbsent(parentId, key -> new ArrayList<>()).add(dept);
            }
        }

        List<SysDept> ordered = new ArrayList<>(depts.size());
        Set<Long> seen = new HashSet<>();
        for (SysDept root : roots) {
            walk(root, children, ordered, seen, 0);
        }
        for (SysDept dept : depts) {
            if (dept.getId() != null && !seen.contains(dept.getId())) {
                walk(dept, children, ordered, seen, 0);
            }
        }
        return ordered;
    }

    private static void walk(SysDept node, Map<Long, List<SysDept>> children,
                             List<SysDept> ordered, Set<Long> seen, int depth) {
        if (node.getId() == null || !seen.add(node.getId()) || depth > MAX_DEPTH) {
            return;
        }
        ordered.add(node);
        for (SysDept child : children.getOrDefault(node.getId(), List.of())) {
            walk(child, children, ordered, seen, depth + 1);
        }
    }

    private static int depthOf(SysDept dept, Map<Long, SysDept> byId) {
        int depth = 0;
        Set<Long> seen = new HashSet<>();
        SysDept cursor = dept;
        while (cursor != null && cursor.getId() != null && seen.add(cursor.getId())
                && cursor.getParentId() != null && cursor.getParentId() != 0L
                && depth < MAX_DEPTH) {
            cursor = byId.get(cursor.getParentId());
            if (cursor != null) {
                depth++;
            }
        }
        return depth;
    }
}
