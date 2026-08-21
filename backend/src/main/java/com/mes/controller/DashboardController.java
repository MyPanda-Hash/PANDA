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

        // 4) 分模块看板聚合（生产/库存/销售/质量，全部来自真实单据数据）
        out.put("production", productionStats());
        out.put("stock", stockStats());
        out.put("sales", salesStats());
        out.put("quality", qualityStats());
        return ApiResult.ok(out);
    }

    // ==================== 分模块看板聚合 ====================

    /** 生产看板：状态/车间分布、近 7 天新增与完工趋势、BOM 树（产品→材料） */
    private Map<String, Object> productionStats() {
        Map<String, Integer> statusDist = new LinkedHashMap<>();
        Map<String, Integer> workshopDist = new LinkedHashMap<>();
        Map<String, Integer> dayAdded = new LinkedHashMap<>();
        Map<String, Integer> dayDone = new LinkedHashMap<>();
        Map<String, Map<String, Object>> bomProducts = new LinkedHashMap<>();

        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "MANU_ORDER"))) {
            String s = fd.getStatus() == null ? "" : fd.getStatus();
            statusDist.put(s, statusDist.getOrDefault(s, 0) + 1);
            Map<String, Object> head = parse(fd.getData());
            String ws = str(head.get("生产车间"));
            if (!ws.isEmpty()) workshopDist.put(ws, workshopDist.getOrDefault(ws, 0) + 1);
            String day = fd.getCreateTime() == null ? "" : String.valueOf(fd.getCreateTime()).substring(0, 10);
            if (!day.isEmpty()) {
                dayAdded.put(day, dayAdded.getOrDefault(day, 0) + 1);
                if ("已完工".equals(s)) dayDone.put(day, dayDone.getOrDefault(day, 0) + 1);
            }
            // BOM 树：products（产品）→ materials（材料清单）
            try {
                Map<String, Object> d = json.readValue(fd.getDetailData(), new TypeReference<Map<String, Object>>() {});
                List<?> products = d.get("products") instanceof List ? (List<?>) d.get("products") : null;
                List<?> materials = d.get("materials") instanceof List ? (List<?>) d.get("materials") : null;
                if (products != null && materials != null) {
                    for (Object po : products) {
                        if (!(po instanceof Map)) continue;
                        Map<?, ?> pm = (Map<?, ?>) po;
                        String pn = str(pm.get("产品名称"));
                        if (pn.isEmpty()) continue;
                        Map<String, Object> node = bomProducts.computeIfAbsent(pn, k -> {
                            Map<String, Object> m = new LinkedHashMap<>();
                            m.put("产品", pn);
                            m.put("规格型号", "");
                            m.put("materials", new ArrayList<Map<String, Object>>());
                            return m;
                        });
                        node.put("规格型号", str(pm.get("规格型号")));
                        @SuppressWarnings("unchecked")
                        List<Map<String, Object>> mats = (List<Map<String, Object>>) node.get("materials");
                        for (Object mo : materials) {
                            if (!(mo instanceof Map)) continue;
                            Map<?, ?> mm = (Map<?, ?>) mo;
                            String mn = str(mm.get("材料名称"));
                            if (mn.isEmpty()) continue;
                            boolean exists = false;
                            for (Map<String, Object> ex : mats) {
                                if (mn.equals(ex.get("名称"))) { exists = true; break; }
                            }
                            if (!exists) {
                                Map<String, Object> mat = new LinkedHashMap<>();
                                mat.put("名称", mn);
                                mat.put("数量", mm.get("定额需用数量") == null ? "-" : String.valueOf(mm.get("定额需用数量")));
                                mat.put("单位", str(mm.get("计量单位")));
                                mats.add(mat);
                            }
                        }
                    }
                }
            } catch (Exception ignore) {}
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("statusDist", toKv(statusDist));
        out.put("workshopDist", toKv(workshopDist));
        out.put("trend7", trend7(dayAdded, dayDone));
        out.put("bomTree", new ArrayList<>(bomProducts.values()));
        return out;
    }

    /** 库存看板：出入库单据数 + 明细行数 */
    private Map<String, Object> stockStats() {
        String[] codes = {"PURCHASE_IN", "FINISH_IN", "OTHER_IN", "SALE_OUT", "MATERIAL_OUT", "OTHER_OUT"};
        String[] names = {"采购入库单", "产成品入库单", "其他入库单", "销售出库单", "材料出库单", "其他出库单"};
        List<Map<String, Object>> panels = new ArrayList<>();
        int totalIn = 0, totalOut = 0, totalLines = 0;
        for (int i = 0; i < codes.length; i++) {
            String code = codes[i];
            int count = 0, lines = 0;
            for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, code))) {
                count++;
                lines += detailLines(fd.getDetailData());
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("panelCode", code);
            m.put("panelName", names[i]);
            m.put("count", count);
            m.put("lines", lines);
            panels.add(m);
            if (code.endsWith("_IN")) totalIn += count;
            else totalOut += count;
            totalLines += lines;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("panels", panels);
        out.put("totalIn", totalIn);
        out.put("totalOut", totalOut);
        out.put("totalLines", totalLines);
        return out;
    }

    /** 销售看板：客户/状态分布 + 明细金额合计 */
    private Map<String, Object> salesStats() {
        Map<String, Integer> byCustomer = new LinkedHashMap<>();
        Map<String, Integer> byStatus = new LinkedHashMap<>();
        double amount = 0;
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "SO_ORDER"))) {
            Map<String, Object> head = parse(fd.getData());
            String c = str(head.get("客户"));
            if (!c.isEmpty()) byCustomer.put(c, byCustomer.getOrDefault(c, 0) + 1);
            String s = fd.getStatus() == null ? "" : fd.getStatus();
            byStatus.put(s, byStatus.getOrDefault(s, 0) + 1);
            amount += sumAmount(fd.getDetailData(), "金额");
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("byCustomer", toKv(byCustomer));
        out.put("byStatus", toKv(byStatus));
        out.put("amount", Math.round(amount * 100) / 100.0);
        out.put("total", byStatus.values().stream().mapToInt(Integer::intValue).sum());
        return out;
    }

    /** 质量看板：检验单结果分布与合格率 */
    private Map<String, Object> qualityStats() {
        Map<String, Integer> byResult = new LinkedHashMap<>();
        int total = 0, pass = 0;
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .in(FormData::getPanelCode, "INSPECTION", "FINISH_INSPECT"))) {
            try {
                Map<String, Object> d = json.readValue(fd.getDetailData(), new TypeReference<Map<String, Object>>() {});
                Object items = d.get("items");
                if (items instanceof List) {
                    for (Object io : (List<?>) items) {
                        if (!(io instanceof Map)) continue;
                        String r = str(((Map<?, ?>) io).get("检验结果判定"));
                        if (r.isEmpty()) r = str(((Map<?, ?>) io).get("检验结果"));
                        if (r.isEmpty()) r = "待检";
                        byResult.put(r, byResult.getOrDefault(r, 0) + 1);
                        total++;
                        if ("合格".equals(r)) pass++;
                    }
                }
            } catch (Exception ignore) {}
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("byResult", toKv(byResult));
        out.put("total", total);
        out.put("pass", pass);
        out.put("passRate", total == 0 ? 0 : Math.round(pass * 1000.0 / total) / 10.0);
        return out;
    }

    // ==================== 工具方法 ====================

    private String str(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    /** 明细行数：取 detail_data 中首个明细数组长度（detail/items/products 任一） */
    private int detailLines(String detailJson) {
        try {
            Map<String, Object> d = json.readValue(detailJson, new TypeReference<Map<String, Object>>() {});
            for (String k : new String[]{"detail", "items", "products", "materials"}) {
                Object v = d.get(k);
                if (v instanceof List) return ((List<?>) v).size();
            }
        } catch (Exception ignore) {}
        return 0;
    }

    /** 明细金额合计（按指定字段名，如 金额/含税金额） */
    private double sumAmount(String detailJson, String field) {
        double sum = 0;
        try {
            Map<String, Object> d = json.readValue(detailJson, new TypeReference<Map<String, Object>>() {});
            Object v = d.get("items");
            if (v instanceof List) {
                for (Object io : (List<?>) v) {
                    if (io instanceof Map) {
                        Object a = ((Map<?, ?>) io).get(field);
                        if (a instanceof Number) sum += ((Number) a).doubleValue();
                    }
                }
            }
        } catch (Exception ignore) {}
        return sum;
    }

    /** Map<String,Integer> → List<{name,value}>（保持插入序，按值降序） */
    private List<Map<String, Object>> toKv(Map<String, Integer> m) {
        List<Map<String, Object>> out = new ArrayList<>();
        m.entrySet().stream()
                .sorted((a, b) -> Integer.compare(b.getValue(), a.getValue()))
                .forEach(e -> {
                    Map<String, Object> kv = new LinkedHashMap<>();
                    kv.put("name", e.getKey());
                    kv.put("value", e.getValue());
                    out.add(kv);
                });
        return out;
    }

    /** 近 7 天（含今天）新增/完工数，缺失日期补 0 */
    private List<Map<String, Object>> trend7(Map<String, Integer> added, Map<String, Integer> done) {
        List<Map<String, Object>> out = new ArrayList<>();
        java.time.LocalDate today = java.time.LocalDate.now();
        for (int i = 6; i >= 0; i--) {
            String day = today.minusDays(i).toString();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("date", day.substring(5)); // MM-dd
            m.put("added", added.getOrDefault(day, 0));
            m.put("done", done.getOrDefault(day, 0));
            out.add(m);
        }
        return out;
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