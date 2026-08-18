package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.dto.ApiResult;
import com.mes.entity.SysDept;
import com.mes.entity.SysRole;
import com.mes.entity.SysUser;
import com.mes.mapper.SysDeptMapper;
import com.mes.mapper.SysRoleMapper;
import com.mes.mapper.SysUserMapper;
import com.mes.service.RoleService;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 组织架构-用户管理（仅管理员可访问）：账号增改、分配角色、启停用 */
@RestController
@RequestMapping("/api/sys/user")
public class SysUserController {

    private final SysUserMapper userMapper;
    private final SysRoleMapper roleMapper;
    private final SysDeptMapper deptMapper;
    private final RoleService roleService;
    private final PasswordEncoder passwordEncoder;

    public SysUserController(SysUserMapper userMapper, SysRoleMapper roleMapper, SysDeptMapper deptMapper,
                             RoleService roleService, PasswordEncoder passwordEncoder) {
        this.userMapper = userMapper;
        this.roleMapper = roleMapper;
        this.deptMapper = deptMapper;
        this.roleService = roleService;
        this.passwordEncoder = passwordEncoder;
    }

    private void guard(Authentication auth) {
        if (auth == null || !roleService.isAdminUser(auth.getName())) {
            throw new IllegalStateException("仅管理员可访问组织架构");
        }
    }

    @GetMapping("/list")
    public ApiResult<List<Map<String, Object>>> list(Authentication auth) {
        guard(auth);
        List<Map<String, Object>> out = new ArrayList<>();
        for (SysUser u : userMapper.selectList(new LambdaQueryWrapper<SysUser>().orderByAsc(SysUser::getId))) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", u.getId());
            m.put("userName", u.getUserName());
            m.put("realName", u.getRealName());
            m.put("phone", u.getPhone());
            m.put("factoryCode", u.getFactoryCode());
            m.put("enabled", u.getEnabled());
            m.put("roleId", u.getRoleId());
            if (u.getRoleId() != null) {
                SysRole r = roleMapper.selectById(u.getRoleId());
                m.put("roleCode", r != null ? r.getRoleCode() : "");
                m.put("roleName", r != null ? r.getRoleName() : "");
            } else {
                m.put("roleCode", "");
                m.put("roleName", "");
            }
            m.put("deptId", u.getDeptId());
            if (u.getDeptId() != null) {
                SysDept d = deptMapper.selectById(u.getDeptId());
                m.put("deptName", d != null ? d.getDeptName() : "");
            } else {
                m.put("deptName", "");
            }
            out.add(m);
        }
        return ApiResult.ok(out);
    }

    @PostMapping("/save")
    public ApiResult<Void> save(@RequestBody Map<String, Object> body, Authentication auth) {
        guard(auth);
        String userName = String.valueOf(body.getOrDefault("userName", ""));
        if (userName.isBlank()) throw new IllegalArgumentException("账号不能为空");
        Long id = body.get("id") == null ? null : Long.valueOf(String.valueOf(body.get("id")));
        SysUser user;
        if (id != null) {
            user = userMapper.selectById(id);
            if (user == null) throw new IllegalArgumentException("用户不存在");
        } else {
            SysUser ex = userMapper.selectOne(new LambdaQueryWrapper<SysUser>().eq(SysUser::getUserName, userName));
            if (ex != null) throw new IllegalArgumentException("账号已存在：" + userName);
            user = new SysUser();
            user.setUserName(userName);
            String pwd = String.valueOf(body.getOrDefault("password", "123456"));
            user.setPassword(passwordEncoder.encode(pwd));
        }
        user.setRealName(String.valueOf(body.getOrDefault("realName", "")));
        user.setPhone(String.valueOf(body.getOrDefault("phone", "")));
        user.setFactoryCode(String.valueOf(body.getOrDefault("factoryCode", "F01")));
        user.setEnabled(body.get("enabled") == null ? 1 : Integer.valueOf(String.valueOf(body.get("enabled"))));
        user.setRoleId(body.get("roleId") == null ? null : Long.valueOf(String.valueOf(body.get("roleId"))));
        user.setDeptId(body.get("deptId") == null ? null : Long.valueOf(String.valueOf(body.get("deptId"))));
        if (id != null) userMapper.updateById(user);
        else userMapper.insert(user);
        return ApiResult.ok(null);
    }
}