package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.entity.SysRole;
import com.mes.entity.SysRolePanel;
import com.mes.entity.SysUser;
import com.mes.mapper.PanelConfigMapper;
import com.mes.mapper.SysRoleMapper;
import com.mes.mapper.SysRolePanelMapper;
import com.mes.mapper.SysUserMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 角色与面板权限：角色 CRUD、角色-面板授权（11 项权限矩阵）、当前用户权限计算。
 * 权限模型：admin（is_admin=1）拥有全部面板与全部权限；普通角色按 sys_role_panel 分配面板与
 * 11 项权限码（perms 逗号分隔，view/query/add/edit/delete/export/print/audit/price/review/adjust），
 * 含 audit 才拥有审批通过/驳回权限（can_approve 列由 audit 派生同步写，保持旧链路兼容）；
 * 提交审批与查看审批历史为公开权限（所有登录用户）。review/adjust 为预留权限位（暂无对应按钮）。
 */
@Service
public class RoleService {

    /** 11 项权限动作（LinkedHashMap 保序：顺序即权限矩阵列序） */
    public static final Map<String, String> PERMISSION_ACTIONS = buildPermissionActions();

    private static Map<String, String> buildPermissionActions() {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("view", "查看");
        m.put("query", "查询");
        m.put("add", "新增");
        m.put("edit", "修改");
        m.put("delete", "删除");
        m.put("export", "导出");
        m.put("print", "打印");
        m.put("audit", "审批");
        m.put("price", "价格");
        m.put("review", "复核");
        m.put("adjust", "调整");
        return Collections.unmodifiableMap(m);
    }

    /** 模块分组（有序，与业务总览模块对齐；other 为未映射面板兜底） */
    public static final Map<String, String> MODULE_GROUPS = buildModuleGroups();

    private static Map<String, String> buildModuleGroups() {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("prod", "生产管理");
        m.put("outsource", "委外管理");
        m.put("sales", "销售管理");
        m.put("purchase", "采购管理");
        m.put("distribution", "配货管理");
        m.put("inv", "库存核算");
        m.put("pda", "移动仓管");
        m.put("sn", "序列号管理");
        m.put("qc", "质量管理");
        m.put("archives", "基础档案");
        m.put("query", "查询分析");
        m.put("sys", "系统设置");
        m.put("other", "其他");
        return Collections.unmodifiableMap(m);
    }

    private final SysRoleMapper roleMapper;
    private final SysRolePanelMapper rolePanelMapper;
    private final SysUserMapper userMapper;
    private final PanelConfigMapper panelConfigMapper;
    private final ObjectMapper json = new ObjectMapper();

    // ---- 性能缓存：面板清单（TTL 60s）与用户权限结果（TTL 30s，写操作失效）----
    private static final long PANEL_TTL_MS = 60_000L;
    private static final long PERM_TTL_MS = 30_000L;
    private volatile List<Map<String, Object>> panelsCache = null;
    private volatile long panelsCacheAt = 0L;
    private final Map<String, Map<String, Object>> permCache = new ConcurrentHashMap<>();
    private final Map<String, Long> permCacheAt = new ConcurrentHashMap<>();

    public RoleService(SysRoleMapper roleMapper, SysRolePanelMapper rolePanelMapper,
                       SysUserMapper userMapper, PanelConfigMapper panelConfigMapper) {
        this.roleMapper = roleMapper;
        this.rolePanelMapper = rolePanelMapper;
        this.userMapper = userMapper;
        this.panelConfigMapper = panelConfigMapper;
    }

    // ---------- 角色 CRUD ----------

    public List<SysRole> listRoles() {
        return roleMapper.selectList(new LambdaQueryWrapper<SysRole>().orderByAsc(SysRole::getId));
    }

    public SysRole saveRole(SysRole role) {
        if (role.getRoleCode() == null || role.getRoleCode().isBlank()) throw new IllegalArgumentException("角色编码不能为空");
        if (role.getRoleName() == null || role.getRoleName().isBlank()) throw new IllegalArgumentException("角色名称不能为空");
        if (role.getId() == null) {
            SysRole ex = roleMapper.selectOne(new LambdaQueryWrapper<SysRole>().eq(SysRole::getRoleCode, role.getRoleCode()));
            if (ex != null) throw new IllegalArgumentException("角色编码已存在：" + role.getRoleCode());
            role.setIsAdmin(0);
            roleMapper.insert(role);
        } else {
            SysRole old = roleMapper.selectById(role.getId());
            if (old != null && old.getIsAdmin() != null && old.getIsAdmin() == 1) {
                throw new IllegalArgumentException("管理员角色不可修改");
            }
            role.setIsAdmin(0);
            roleMapper.updateById(role);
        }
        invalidatePermCache();
        return role;
    }

    public void deleteRole(Long id) {
        SysRole role = roleMapper.selectById(id);
        if (role == null) return;
        if (role.getIsAdmin() != null && role.getIsAdmin() == 1) throw new IllegalArgumentException("管理员角色不可删除");
        roleMapper.deleteById(id);
        rolePanelMapper.delete(new LambdaQueryWrapper<SysRolePanel>().eq(SysRolePanel::getRoleId, id));
        // 该角色下的用户 role_id 置空
        List<SysUser> us = userMapper.selectList(new LambdaQueryWrapper<SysUser>().eq(SysUser::getRoleId, id));
        for (SysUser u : us) {
            u.setRoleId(null);
            userMapper.updateById(u);
        }
        invalidatePermCache();
    }

    // ---------- 面板清单（含是否有审批流） ----------

    public List<Map<String, Object>> listPanels() {
        long now = System.currentTimeMillis();
        if (panelsCache == null || now - panelsCacheAt > PANEL_TTL_MS) {
            List<Map<String, Object>> out = new ArrayList<>();
            for (PanelConfig pc : panelConfigMapper.selectList(
                    new LambdaQueryWrapper<PanelConfig>().orderByAsc(PanelConfig::getId))) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("panelCode", pc.getPanelCode());
                m.put("panelName", pc.getPanelName());
                m.put("hasApproval", hasApproval(pc));
                m.put("moduleGroup", pc.getModuleGroup() == null ? "" : pc.getModuleGroup());
                out.add(m);
            }
            panelsCache = out;
            panelsCacheAt = now;
        }
        return panelsCache;
    }

    private boolean hasApproval(PanelConfig pc) {
        try {
            JsonNode cfg = json.readTree(pc.getConfig());
            JsonNode groups = cfg.path("metadata").path("buttonGroups");
            if (groups.isArray()) {
                for (JsonNode g : groups) {
                    JsonNode acts = g.path("actions");
                    if (acts.isArray()) {
                        for (JsonNode a : acts) {
                            String name = a.asText("");
                            if (name.contains("审批通过") || name.contains("提交审批")) return true;
                        }
                    }
                }
            }
        } catch (Exception ignore) {}
        return false;
    }

    // ---------- 角色-面板授权（11 项权限矩阵） ----------

    /** 权限动作清单（矩阵列）：[{code, name}]，顺序与 PERMISSION_ACTIONS 一致 */
    public static List<Map<String, String>> permissionActions() {
        List<Map<String, String>> out = new ArrayList<>();
        for (Map.Entry<String, String> e : PERMISSION_ACTIONS.entrySet()) {
            Map<String, String> m = new LinkedHashMap<>();
            m.put("code", e.getKey());
            m.put("name", e.getValue());
            out.add(m);
        }
        return out;
    }

    /** 模块分组清单（矩阵折叠组）：[{code, name}]，other 兜底置尾 */
    public static List<Map<String, String>> moduleGroups() {
        List<Map<String, String>> out = new ArrayList<>();
        for (Map.Entry<String, String> e : MODULE_GROUPS.entrySet()) {
            Map<String, String> m = new LinkedHashMap<>();
            m.put("code", e.getKey());
            m.put("name", e.getValue());
            out.add(m);
        }
        return out;
    }

    /** 规范化 perms：过滤合法码、去重、view 置首（有任意权限必含 view）；接受 List 或逗号分隔字符串 */
    public static List<String> normalizePerms(Object perms) {
        LinkedHashSet<String> set = new LinkedHashSet<>();
        if (perms instanceof Collection<?> coll) {
            for (Object o : coll) {
                String code = String.valueOf(o).trim();
                if (PERMISSION_ACTIONS.containsKey(code)) set.add(code);
            }
        } else if (perms instanceof String s && !s.isBlank()) {
            for (String code : s.split(",")) {
                if (PERMISSION_ACTIONS.containsKey(code.trim())) set.add(code.trim());
            }
        }
        if (set.isEmpty()) return List.of();
        set.remove("view");
        List<String> out = new ArrayList<>(set);
        out.add(0, "view");
        return out;
    }

    /** 读取授权行权限码：perms 为空（未迁移存量行）时按 can_approve 回退（view / view+audit） */
    public static List<String> permsOfRow(SysRolePanel rp) {
        if (rp == null) return List.of();
        if (rp.getPerms() != null && !rp.getPerms().isBlank()) {
            List<String> perms = normalizePerms(Arrays.asList(rp.getPerms().split(",")));
            if (!perms.isEmpty()) return perms;
        }
        return rp.getCanApprove() != null && rp.getCanApprove() == 1
                ? List.of("view", "audit")
                : List.of("view");
    }

    public Map<String, Object> getRolePanels(Long roleId) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("actions", permissionActions());
        out.put("modules", moduleGroups());
        out.put("allPanels", listPanels());
        List<Map<String, Object>> granted = new ArrayList<>();
        for (SysRolePanel rp : rolePanelMapper.selectList(
                new LambdaQueryWrapper<SysRolePanel>().eq(SysRolePanel::getRoleId, roleId))) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("panelCode", rp.getPanelCode());
            List<String> perms = permsOfRow(rp);
            m.put("perms", perms);
            // 兼容旧前端/旧调用：canApprove 由 audit 派生
            m.put("canApprove", perms.contains("audit"));
            granted.add(m);
        }
        out.put("granted", granted);
        return out;
    }

    @Transactional
    public void saveRolePanels(Long roleId, List<Map<String, Object>> panels) {
        SysRole role = roleMapper.selectById(roleId);
        if (role == null) throw new IllegalArgumentException("角色不存在");
        if (role.getIsAdmin() != null && role.getIsAdmin() == 1) throw new IllegalArgumentException("管理员角色无需配置（默认全权限）");
        rolePanelMapper.delete(new LambdaQueryWrapper<SysRolePanel>().eq(SysRolePanel::getRoleId, roleId));
        if (panels != null) {
            for (Map<String, Object> p : panels) {
                String code = String.valueOf(p.getOrDefault("panelCode", ""));
                if (code.isBlank()) continue;
                List<String> perms = normalizePerms(p.get("perms"));
                // 兼容旧载荷：无 perms 但 canApprove=true 时至少给 view+audit
                if (perms.isEmpty() && Boolean.TRUE.equals(p.get("canApprove"))) {
                    perms = List.of("view", "audit");
                }
                if (perms.isEmpty()) continue; // 无任何权限 = 不授权该面板
                SysRolePanel rp = new SysRolePanel();
                rp.setRoleId(roleId);
                rp.setPanelCode(code);
                rp.setPerms(String.join(",", perms));
                rp.setCanApprove(perms.contains("audit") ? 1 : 0);
                rolePanelMapper.insert(rp);
            }
        }
        invalidatePermCache();
    }

    // ---------- 当前用户权限 ----------

    public Map<String, Object> getPerms(String userName) {
        long now = System.currentTimeMillis();
        Map<String, Object> cached = permCache.get(userName);
        Long at = permCacheAt.get(userName);
        if (cached != null && at != null && now - at < PERM_TTL_MS) return cached;
        Map<String, Object> result = computePerms(userName);
        permCache.put(userName, result);
        permCacheAt.put(userName, now);
        return result;
    }

    /** 权限变更（角色/授权/用户）后调用，使权限缓存失效 */
    public void invalidatePermCache() {
        permCache.clear();
        permCacheAt.clear();
    }

    private Map<String, Object> computePerms(String userName) {
        Map<String, Object> out = new LinkedHashMap<>();
        SysUser user = userMapper.selectOne(new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, userName));
        boolean isAdmin = false;
        String roleCode = "";
        if (user != null && user.getRoleId() != null) {
            SysRole role = roleMapper.selectById(user.getRoleId());
            if (role != null) {
                roleCode = role.getRoleCode();
                isAdmin = role.getIsAdmin() != null && role.getIsAdmin() == 1;
            }
        }
        List<String> visible = new ArrayList<>();
        List<String> approve = new ArrayList<>();
        Map<String, List<String>> panelPerms = new LinkedHashMap<>();
        if (isAdmin) {
            for (Map<String, Object> p : listPanels()) {
                String code = String.valueOf(p.get("panelCode"));
                visible.add(code);
                if (Boolean.TRUE.equals(p.get("hasApproval"))) approve.add(code);
            }
        } else if (user != null && user.getRoleId() != null) {
            for (SysRolePanel rp : rolePanelMapper.selectList(
                    new LambdaQueryWrapper<SysRolePanel>().eq(SysRolePanel::getRoleId, user.getRoleId()))) {
                List<String> perms = permsOfRow(rp);
                visible.add(rp.getPanelCode());
                panelPerms.put(rp.getPanelCode(), perms);
                if (perms.contains("audit")) approve.add(rp.getPanelCode());
            }
        }
        out.put("isAdmin", isAdmin);
        out.put("roleCode", roleCode);
        out.put("visiblePanels", visible);
        out.put("approvePanels", approve);
        // 11 项权限矩阵（非 admin）：面板码 → 已授权权限码列表；admin 由 isAdmin 标识全权限，恒为空表
        out.put("panelPerms", panelPerms);
        return out;
    }

    public boolean isAdminUser(String userName) {
        Map<String, Object> p = getPerms(userName);
        return Boolean.TRUE.equals(p.get("isAdmin"));
    }
}