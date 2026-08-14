package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.dto.ApiResult;
import com.mes.entity.SysMenu;
import com.mes.mapper.SysMenuMapper;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/sys/menu")
public class MenuController {

    private final SysMenuMapper menuMapper;

    public MenuController(SysMenuMapper menuMapper) {
        this.menuMapper = menuMapper;
    }

    @GetMapping("/tree")
    public ApiResult<List<Map<String, Object>>> tree() {
        List<SysMenu> all = menuMapper.selectList(
                new LambdaQueryWrapper<SysMenu>().orderByAsc(SysMenu::getSort));
        Map<Long, Map<String, Object>> nodes = new HashMap<>();
        for (SysMenu m : all) {
            Map<String, Object> n = new HashMap<>();
            n.put("code", m.getCode());
            n.put("title", m.getTitle());
            n.put("path", m.getPath());
            n.put("icon", m.getIcon());
            nodes.put(m.getId(), n);
        }
        List<Map<String, Object>> roots = new ArrayList<>();
        for (SysMenu m : all) {
            Map<String, Object> n = nodes.get(m.getId());
            if (m.getParentId() == null || m.getParentId() == 0) {
                roots.add(n);
            } else {
                Map<String, Object> parent = nodes.get(m.getParentId());
                if (parent != null) {
                    Object children = parent.computeIfAbsent("children", k -> new ArrayList<Object>());
                    ((List<Object>) children).add(n);
                }
            }
        }
        return ApiResult.ok(roots);
    }
}
