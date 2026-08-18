package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.dto.ApiResult;
import com.mes.entity.SysDept;
import com.mes.entity.SysUser;
import com.mes.mapper.SysDeptMapper;
import com.mes.mapper.SysUserMapper;
import com.mes.service.RoleService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 组织架构-部门管理（仅管理员）：多级部门树、增删改 */
@RestController
@RequestMapping("/api/sys/dept")
public class SysDeptController {

    private final SysDeptMapper deptMapper;
    private final SysUserMapper userMapper;
    private final RoleService roleService;

    public SysDeptController(SysDeptMapper deptMapper, SysUserMapper userMapper, RoleService roleService) {
        this.deptMapper = deptMapper;
        this.userMapper = userMapper;
        this.roleService = roleService;
    }

    private void guard(Authentication auth) {
        if (auth == null || !roleService.isAdminUser(auth.getName())) {
            throw new IllegalStateException("仅管理员可访问组织架构");
        }
    }

    @GetMapping("/tree")
    public ApiResult<List<Map<String, Object>>> tree(Authentication auth) {
        guard(auth);
        List<SysDept> all = deptMapper.selectList(new LambdaQueryWrapper<SysDept>().orderByAsc(SysDept::getSort).orderByAsc(SysDept::getId));
        Map<Long, Map<String, Object>> nodes = new LinkedHashMap<>();
        for (SysDept d : all) {
            Map<String, Object> n = new LinkedHashMap<>();
            n.put("id", d.getId());
            n.put("parentId", d.getParentId());
            n.put("deptName", d.getDeptName());
            n.put("sort", d.getSort());
            nodes.put(d.getId(), n);
        }
        List<Map<String, Object>> roots = new ArrayList<>();
        for (SysDept d : all) {
            Map<String, Object> n = nodes.get(d.getId());
            if (d.getParentId() == null || d.getParentId() == 0) {
                roots.add(n);
            } else {
                Map<String, Object> parent = nodes.get(d.getParentId());
                if (parent != null) {
                    Object children = parent.computeIfAbsent("children", k -> new ArrayList<Object>());
                    ((List<Object>) children).add(n);
                }
            }
        }
        return ApiResult.ok(roots);
    }

    @PostMapping("/save")
    public ApiResult<SysDept> save(@RequestBody SysDept dept, Authentication auth) {
        guard(auth);
        if (dept.getDeptName() == null || dept.getDeptName().isBlank()) throw new IllegalArgumentException("部门名称不能为空");
        if (dept.getParentId() == null) dept.setParentId(0L);
        if (dept.getSort() == null) dept.setSort(0);
        if (dept.getId() == null) deptMapper.insert(dept);
        else deptMapper.updateById(dept);
        return ApiResult.ok(dept);
    }

    @DeleteMapping("/{id}")
    public ApiResult<Void> delete(@PathVariable Long id, Authentication auth) {
        guard(auth);
        Long hasChild = deptMapper.selectCount(new LambdaQueryWrapper<SysDept>().eq(SysDept::getParentId, id));
        if (hasChild > 0) throw new IllegalArgumentException("存在子部门，请先删除子部门");
        Long hasUser = userMapper.selectCount(new LambdaQueryWrapper<SysUser>().eq(SysUser::getDeptId, id));
        if (hasUser > 0) throw new IllegalArgumentException("该部门下存在用户，请先调整用户部门");
        deptMapper.deleteById(id);
        return ApiResult.ok(null);
    }
}