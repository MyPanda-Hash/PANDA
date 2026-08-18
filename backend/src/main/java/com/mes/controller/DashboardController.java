package com.mes.controller;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.dto.ApiResult;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.entity.SysDept;
import com.mes.entity.SysRole;
import com.mes.entity.SysUser;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.mapper.SysDeptMapper;
import com.mes.mapper.SysRoleMapper;
import com.mes.mapper.SysUserMapper;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 我的桌面看板：真实 MES 数据聚合（单据/状态/待办/档案统计） */
@RestController
@RequestMapping("/api/dashboard")
public class DashboardController {

    private static final Map<String, String> DOC_TITLES = new LinkedHashMap<>();
    static {
        DOC_TITLES.put("MANU_ORDER", "生产加工单");
        DOC_TITLES.put("SO_ORDER", "销售订单");
        DOC_TITLES.put("PROCESS_REPORT", "工序汇报单");
        DOC_TITLES.put("FINISH_IN", "产成品入库单");
        DOC_TITLES.put("SALE_OUT", "销售出库单");
    }

    private final FormDataMapper formMapper;
    private final PanelConfigMapper panelConfigMapper;
    private final SysUserMapper userMapper;
    private final SysDeptMapper deptMapper;
    private final SysRoleMapper roleMapper;
    private final ObjectMapper json = new ObjectMapper();

    public DashboardController(FormDataMapper formMapper, PanelConfigMapper panelConfigMapper,
                               SysUserMapper userMapper, SysDeptMapper deptMapper, SysRoleMapper roleMapper) {
        this.formMapper = formMapper;
        this.panelConfigMapper = panelConfigMapper;
        this.userMapper = userMapper;
        this.deptMapper = deptMapper;
        this.roleMapper = roleMapper;
    }

    @GetMapping("/stats")
    public ApiResult<Map<String, Object>> stats() {
        Map<String, Object> out = new LinkedHashMap<>();

        // 1) 单据统计：按面板分组 + 状态分布
        Map<String, Map<String, Object>> docAgg = new LinkedHashMap<>();
        for (Map.Entry<String, String> e : DOC_TITLES.entrySet()) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("panelCode", e.getKey());
            m.put("panelName", e.getValue());
            m.put("count", 0);
            m.put("status", new LinkedHashMap<String, Object>());
            docAgg.put(e.getKey(), m);
        }
        int moTotal = 0, moActive = 0, approvePending = 0, prTotal = 0, fiTotal = 0, soTotal = 0;
        List<Map<String, Object>> todos = new ArrayList<>();
        List<Map<String, Object>> progress = new ArrayList<>();
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .orderByDesc(FormData::getCreateTime).last("LIMIT 500"))) {
            String code = fd.getPanelCode();
            Map<String, Object> agg = docAgg.get(code);
            if (agg == null) continue;
            agg.put("count", (Integer) agg.get("count") + 1);
            @SuppressWarnings("unchecked")
            Map<String, Object> st = (Map<String, Object>) agg.get("status");
            String s = fd.getStatus() == null ? "" : fd.getStatus();
            st.put(s, ((Number) st.getOrDefault(s, 0)).intValue() + 1);
            // 进度/待办
            if ("MANU_ORDER".equals(code)) {
                moTotal++;
                if ("草稿".equals(s) || "生产中".equals(s) || "已审核".equals(s)) moActive++;
                Map<String, Object> head = parse(fd.getData());
                String product = firstProductName(fd.getDetailData());
                Map<String, Object> p = new LinkedHashMap<>();
                p.put("编号", fd.getFormNo());
                p.put("产品", product);
                p.put("数量", head.getOrDefault("数量", "-"));
                p.put("状态", s);
                p.put("时间", fd.getCreateTime() == null ? "" : String.valueOf(fd.getCreateTime()).substring(0, 16));
                progress.add(p);
                if ("草稿".equals(s)) {
                    Map<String, Object> t = new LinkedHashMap<>();
                    t.put("编号", fd.getFormNo());
                    t.put("类型", "生产加工单");
                    t.put("状态", "待提交");
                    t.put("时间", p.get("时间"));
                    todos.add(t);
                }
            } else if ("PROCESS_REPORT".equals(code)) prTotal++;
            else if ("FINISH_IN".equals(code)) fiTotal++;
            else if ("SO_ORDER".equals(code)) soTotal++;
            if ("审批中".equals(s)) {
                approvePending++;
                Map<String, Object> t = new LinkedHashMap<>();
                t.put("编号", fd.getFormNo());
                t.put("类型", docAgg.get(code).get("panelName"));
                t.put("状态", "待审批");
                t.put("时间", fd.getCreateTime() == null ? "" : String.valueOf(fd.getCreateTime()).substring(0, 16));
                todos.add(t);
            }
        }
        out.put("docStats", new ArrayList<>(docAgg.values()));
        out.put("kpis", kpis(docAgg, moTotal, moActive, approvePending, prTotal, fiTotal, soTotal));
        out.put("progress", progress.subList(0, Math.min(6, progress.size())));
        out.put("todos", todos.subList(0, Math.min(6, todos.size())));

        // 2) 档案与组织概览
        Map<String, Object> archives = new LinkedHashMap<>();
        archives.put("invItems", countInvItems());
        archives.put("empCount", countByPanel("EMP"));
        archives.put("deptCount", deptMapper.selectCount(null).intValue());
        archives.put("whCount", countByPanel("WH"));
        archives.put("roleCount", roleMapper.selectCount(null).intValue());
        out.put("archives", archives);

        // 3) 最新单据
        List<Map<String, Object>> latest = new ArrayList<>();
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .orderByDesc(FormData::getCreateTime).last("LIMIT 8"))) {
            String name = DOC_TITLES.getOrDefault(fd.getPanelCode(), fd.getPanelCode());
            Map<String, Object> l = new LinkedHashMap<>();
            l.put("panel", name);
            l.put("编号", fd.getFormNo());
            l.put("状态", fd.getStatus());
            l.put("时间", fd.getCreateTime() == null ? "" : String.valueOf(fd.getCreateTime()).substring(0, 16));
            latest.add(l);
        }
        out.put("latest", latest);
        return ApiResult.ok(out);
    }

    private Map<String, Object> kpis(Map<String, Map<String, Object>> agg, int moTotal, int moActive,
                                     int approvePending, int prTotal, int fiTotal, int soTotal) {
        Map<String, Object> k = new LinkedHashMap<>();
        k.put("moActive", moActive);
        k.put("moTotal", moTotal);
        k.put("approvePending", approvePending);
        k.put("prTotal", prTotal);
        k.put("fiTotal", fiTotal);
        k.put("soTotal", soTotal);
        return k;
    }

    private String firstProductName(String detailJson) {
        try {
            Map<String, Object> d = json.readValue(detailJson, new TypeReference<Map<String, Object>>() {});
            Object ps = d.get("products");
            if (ps instanceof List && !((List<?>) ps).isEmpty()) {
                Object p0 = ((List<?>) ps).get(0);
                if (p0 instanceof Map) {
                    Object n = ((Map<?, ?>) p0).get("产品名称");
                    if (n != null) return String.valueOf(n);
                }
            }
        } catch (Exception ignore) {}
        return "";
    }

    private Map<String, Object> parse(String s) {
        try { return json.readValue(s, new TypeReference<Map<String, Object>>() {}); }
        catch (Exception e) { return new LinkedHashMap<>(); }
    }

    private int countInvItems() {
        int n = 0;
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "INV"))) {
            try {
                Map<String, Object> d = json.readValue(fd.getDetailData(), new TypeReference<Map<String, Object>>() {});
                Object items = d.get("items");
                if (items instanceof List) n += ((List<?>) items).size();
            } catch (Exception ignore) {}
        }
        return n;
    }

    private int countByPanel(String panelCode) {
        return formMapper.selectCount(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, panelCode)).intValue();
    }
}