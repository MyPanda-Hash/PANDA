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

import java.util.*;

/**
 * 角色与面板权限：角色 CRUD、角色-面板授权（含审批权限）、当前用户权限计算。
 * 权限模型：admin（is_admin=1）拥有全部面板与全部审批；普通角色按 sys_role_panel 分配可见面板，
 * can_approve=1 的面板才拥有审批通过/驳回权限；提交审批与查看审批历史为公开权限（所有登录用户）。
 */
@Service
public class RoleService {

    private final SysRoleMapper roleMapper;
    private final SysRolePanelMapper rolePanelMapper;
    private final SysUserMapper userMapper;
    private final PanelConfigMapper panelConfigMapper;
    private final ObjectMapper json = new ObjectMapper();

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
    }

    // ---------- 面板清单（含是否有审批流） ----------

    public List<Map<String, Object>> listPanels() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (PanelConfig pc : panelConfigMapper.selectList(
                new LambdaQueryWrapper<PanelConfig>().orderByAsc(PanelConfig::getId))) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("panelCode", pc.getPanelCode());
            m.put("panelName", pc.getPanelName());
            m.put("hasApproval", hasApproval(pc));
            out.add(m);
        }
        return out;
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

    // ---------- 角色-面板授权 ----------

    public Map<String, Object> getRolePanels(Long roleId) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("allPanels", listPanels());
        List<Map<String, Object>> granted = new ArrayList<>();
        for (SysRolePanel rp : rolePanelMapper.selectList(
                new LambdaQueryWrapper<SysRolePanel>().eq(SysRolePanel::getRoleId, roleId))) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("panelCode", rp.getPanelCode());
            m.put("canApprove", rp.getCanApprove() != null && rp.getCanApprove() == 1);
            granted.add(m);
        }
        out.put("granted", granted);
        return out;
    }

    public void saveRolePanels(Long roleId, List<Map<String, Object>> panels) {
        SysRole role = roleMapper.selectById(roleId);
        if (role == null) throw new IllegalArgumentException("角色不存在");
        if (role.getIsAdmin() != null && role.getIsAdmin() == 1) throw new IllegalArgumentException("管理员角色无需配置（默认全权限）");
        rolePanelMapper.delete(new LambdaQueryWrapper<SysRolePanel>().eq(SysRolePanel::getRoleId, roleId));
        if (panels != null) {
            for (Map<String, Object> p : panels) {
                String code = String.valueOf(p.getOrDefault("panelCode", ""));
                if (code.isBlank()) continue;
                SysRolePanel rp = new SysRolePanel();
                rp.setRoleId(roleId);
                rp.setPanelCode(code);
                rp.setCanApprove(Boolean.TRUE.equals(p.get("canApprove")) ? 1 : 0);
                rolePanelMapper.insert(rp);
            }
        }
    }

    // ---------- 当前用户权限 ----------

    public Map<String, Object> getPerms(String userName) {
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
        if (isAdmin) {
            for (Map<String, Object> p : listPanels()) {
                String code = String.valueOf(p.get("panelCode"));
                visible.add(code);
                if (Boolean.TRUE.equals(p.get("hasApproval"))) approve.add(code);
            }
        } else if (user != null && user.getRoleId() != null) {
            for (SysRolePanel rp : rolePanelMapper.selectList(
                    new LambdaQueryWrapper<SysRolePanel>().eq(SysRolePanel::getRoleId, user.getRoleId()))) {
                visible.add(rp.getPanelCode());
                if (rp.getCanApprove() != null && rp.getCanApprove() == 1) approve.add(rp.getPanelCode());
            }
        }
        out.put("isAdmin", isAdmin);
        out.put("roleCode", roleCode);
        out.put("visiblePanels", visible);
        out.put("approvePanels", approve);
        return out;
    }

    public boolean isAdminUser(String userName) {
        Map<String, Object> p = getPerms(userName);
        return Boolean.TRUE.equals(p.get("isAdmin"));
    }
}