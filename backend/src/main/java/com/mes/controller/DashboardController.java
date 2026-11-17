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
import com.mes.entity.StockBalance;
import com.mes.entity.StockTxn;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import com.mes.mapper.StockBalanceMapper;
import com.mes.mapper.StockTxnMapper;
import com.mes.mapper.SysDeptMapper;
import com.mes.mapper.SysRoleMapper;
import com.mes.mapper.SysUserMapper;
import com.mes.service.OnTimeService;
import com.mes.service.ProdBoardService;
import com.mes.service.RoleService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 我的桌面看板：真实 MES 数据聚合（单据/状态/待办/档案统计） */
@RestController
@RequestMapping("/api/dashboard")
public class DashboardController {

    /**
     * 单据面板编码 → 中文名兜底表。
     * 名称的权威源是 panel_config，运行时优先查库；查不到才落回这张表，
     * 避免新增面板在「实时业务事件流」里显示成英文编码。
     */
    private static final Map<String, String> DOC_TITLES = new LinkedHashMap<>();
    static {
        DOC_TITLES.put("MANU_ORDER", "生产加工单");
        DOC_TITLES.put("SO_ORDER", "销售订单");
        DOC_TITLES.put("PROCESS_REPORT", "工序汇报单");
        DOC_TITLES.put("PROCESS_INSPECT_APPLY", "工序报检单");
        DOC_TITLES.put("PROCESS_INSPECTION", "生产过程检验单");
        DOC_TITLES.put("FIRST_INSPECT", "首件报检单");
        DOC_TITLES.put("FINISH_INSPECT", "成品报检单");
        DOC_TITLES.put("INSPECTION", "来料/成品检验单");
        DOC_TITLES.put("FINISH_IN", "产成品入库单");
        DOC_TITLES.put("PURCHASE_IN", "采购入库单");
        DOC_TITLES.put("MATERIAL_OUT", "材料出库单");
        DOC_TITLES.put("SALE_OUT", "销售出库单");
        DOC_TITLES.put("SALE_INV", "销货单");
        DOC_TITLES.put("RECEIPT", "收款单");
    }

    /** 面板名称缓存：首次用到时从 panel_config 载入。 */
    private Map<String, String> panelNameCache = Map.of();

    /** 面板编码 → 展示名：先查库（权威源），再落回 DOC_TITLES，最后退回编码本身。 */
    private String docTitle(String panelCode) {
        if (panelCode == null || panelCode.isBlank()) return "";
        if (panelNameCache.isEmpty()) {
            Map<String, String> loaded = new LinkedHashMap<>();
            for (com.mes.entity.PanelConfig panel : panelConfigMapper.selectList(null)) {
                if (panel.getPanelCode() != null && panel.getPanelName() != null) {
                    loaded.put(panel.getPanelCode(), panel.getPanelName());
                }
            }
            panelNameCache = loaded;
        }
        String fromDb = panelNameCache.get(panelCode);
        if (fromDb != null && !fromDb.isBlank()) return fromDb;
        return DOC_TITLES.getOrDefault(panelCode, panelCode);
    }

    private final FormDataMapper formMapper;
    private final PanelConfigMapper panelConfigMapper;
    private final SysUserMapper userMapper;
    private final SysDeptMapper deptMapper;
    private final SysRoleMapper roleMapper;
    private final StockBalanceMapper stockBalanceMapper;
    private final StockTxnMapper stockTxnMapper;
    private final RoleService roleService;
    private final OnTimeService onTimeService;
    private final ProdBoardService prodBoardService;
    private static final ObjectMapper json = new ObjectMapper();

    public DashboardController(FormDataMapper formMapper, PanelConfigMapper panelConfigMapper,
                               SysUserMapper userMapper, SysDeptMapper deptMapper, SysRoleMapper roleMapper,
                               StockBalanceMapper stockBalanceMapper, StockTxnMapper stockTxnMapper,
                               RoleService roleService, OnTimeService onTimeService,
                               ProdBoardService prodBoardService) {
        this.formMapper = formMapper;
        this.panelConfigMapper = panelConfigMapper;
        this.userMapper = userMapper;
        this.deptMapper = deptMapper;
        this.roleMapper = roleMapper;
        this.stockBalanceMapper = stockBalanceMapper;
        this.stockTxnMapper = stockTxnMapper;
        this.roleService = roleService;
        this.onTimeService = onTimeService;
        this.prodBoardService = prodBoardService;
    }

    @GetMapping("/stats")
    public ApiResult<Map<String, Object>> stats(Authentication auth) {
        Map<String, Object> permissions = auth == null ? Map.of() : roleService.getPerms(auth.getName());
        boolean isAdmin = Boolean.TRUE.equals(permissions.get("isAdmin"));
        Set<String> visible = new LinkedHashSet<>();
        if (permissions.get("visiblePanels") instanceof Iterable<?> panels) {
            for (Object panel : panels) visible.add(String.valueOf(panel));
        }
        Map<String, Object> out = new LinkedHashMap<>();

        // 1) 单据统计：按面板分组 + 状态分布
        Map<String, Map<String, Object>> docAgg = new LinkedHashMap<>();
        for (Map.Entry<String, String> e : DOC_TITLES.entrySet()) {
            if (!visible.contains(e.getKey())) continue;
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
            if (!visible.contains(code)) continue;
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
        archives.put("invItems", visible.contains("INV") ? countInvItems() : 0);
        archives.put("empCount", visible.contains("EMP") ? countByPanel("EMP") : 0);
        archives.put("deptCount", isAdmin ? deptMapper.selectCount(null).intValue() : 0);
        archives.put("whCount", visible.contains("WH") ? countByPanel("WH") : 0);
        archives.put("roleCount", isAdmin ? roleMapper.selectCount(null).intValue() : 0);
        out.put("archives", archives);

        // 3) 最新单据
        List<Map<String, Object>> latest = new ArrayList<>();
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .orderByDesc(FormData::getCreateTime).last("LIMIT 8"))) {
            if (!visible.contains(fd.getPanelCode())) continue;
            String name = docTitle(fd.getPanelCode());
            Map<String, Object> l = new LinkedHashMap<>();
            // panel = 面板名称（给人看）；panelCode = 面板编码（给前端跳单据页用，名称不能当路由参数）
            l.put("panel", name);
            l.put("panelCode", fd.getPanelCode());
            l.put("编号", fd.getFormNo());
            l.put("状态", fd.getStatus());
            l.put("时间", fd.getCreateTime() == null ? "" : String.valueOf(fd.getCreateTime()).substring(0, 16));
            latest.add(l);
        }
        out.put("latest", latest);

        // 4) 分模块看板聚合（生产/库存/销售/采购/质量/设计，全部来自真实单据数据）
        out.put("production", productionStats(visible));
        out.put("stock", stockStats(visible));
        out.put("sales", salesStats(visible));
        out.put("purchase", purchaseStats(visible));
        out.put("quality", qualityStats(visible));
        out.put("design", designStats(visible));
        return ApiResult.ok(out);
    }

    /** 某面板的全部单据（无权限时返回空列表，避免越权读库）。 */
    private List<FormData> docsOf(Set<String> visible, String panelCode) {
        if (!visible.contains(panelCode)) return new ArrayList<>();
        return formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, panelCode));
    }

    private Map<String, Object> purchaseStats(Set<String> visible) {
        return aggregatePurchase(
                docsOf(visible, "PU_ORDER"),
                docsOf(visible, "ARRIVAL_IN"),
                docsOf(visible, "PURCHASE_IN"));
    }

    private Map<String, Object> designStats(Set<String> visible) {
        return aggregateDesign(
                docsOf(visible, "BOM"),
                docsOf(visible, "ROUTE"),
                docsOf(visible, "OP"),
                docsOf(visible, "INV"));
    }

    // ==================== 分模块看板聚合 ====================

    /** 生产看板：状态/车间分布、近 7 天新增与完工趋势、BOM 树（产品→材料） */
    private Map<String, Object> productionStats(Set<String> visible) {
        Map<String, Integer> statusDist = new LinkedHashMap<>();
        Map<String, Integer> workshopDist = new LinkedHashMap<>();
        Map<String, Integer> dayAdded = new LinkedHashMap<>();
        Map<String, Integer> dayDone = new LinkedHashMap<>();
        Map<String, Integer> productCount = new LinkedHashMap<>();
        Map<String, Map<String, Object>> bomProducts = new LinkedHashMap<>();

        String allowedCode = visible.contains("MANU_ORDER") ? "MANU_ORDER" : "__NO_PANEL_ACCESS__";
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, allowedCode))) {
            String s = fd.getStatus() == null ? "" : fd.getStatus();
            statusDist.put(s, statusDist.getOrDefault(s, 0) + 1);
            Map<String, Object> head = parse(fd.getData());
            String ws = str(head.get("生产车间"));
            if (!ws.isEmpty()) workshopDist.put(ws, workshopDist.getOrDefault(ws, 0) + 1);
            String productName = firstProductName(fd.getDetailData());
            if (!productName.isEmpty()) productCount.merge(productName, 1, Integer::sum);
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
        out.put("topProducts", toKv(productCount));
        out.put("processReport", processReportTotals(docsOf(visible, "PROCESS_REPORT")));
        return out;
    }

    /** 库存看板：出入库单据数 + 明细行数 */
    private Map<String, Object> stockStats(Set<String> visible) {
        String[] codes = {"PURCHASE_IN", "FINISH_IN", "OTHER_IN", "SALE_OUT", "MATERIAL_OUT", "OTHER_OUT"};
        String[] names = {"采购入库单", "产成品入库单", "其他入库单", "销售出库单", "材料出库单", "其他出库单"};
        List<Map<String, Object>> panels = new ArrayList<>();
        int totalIn = 0, totalOut = 0, totalLines = 0;
        for (int i = 0; i < codes.length; i++) {
            String code = codes[i];
            if (!visible.contains(code)) continue;
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
        // 四维结存与出入库流水来自结构化表，不再从单据反推
        out.putAll(stockExtra(stockBalanceMapper.selectList(null), stockTxnMapper.selectList(null)));
        return out;
    }

    /** 销售看板：客户/状态分布 + 明细金额合计 */
    private Map<String, Object> salesStats(Set<String> visible) {
        Map<String, Integer> byCustomer = new LinkedHashMap<>();
        Map<String, Integer> byStatus = new LinkedHashMap<>();
        double amount = 0;
        List<FormData> orders = new ArrayList<>();
        String allowedCode = visible.contains("SO_ORDER") ? "SO_ORDER" : "__NO_PANEL_ACCESS__";
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, allowedCode))) {
            orders.add(fd);
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
        out.putAll(salesExtra(orders));
        return out;
    }

    /** 质量看板：检验单结果分布与合格率 */
    private Map<String, Object> qualityStats(Set<String> visible) {
        Map<String, Integer> byResult = new LinkedHashMap<>();
        Map<String, Integer> byType = new LinkedHashMap<>();
        List<FormData> docs = new ArrayList<>();
        int total = 0, pass = 0;
        for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .in(FormData::getPanelCode, "ARRIVAL_IN", "FINISH_INSPECT", "FIRST_INSPECT",
                        "PROCESS_INSPECT_APPLY", "INSPECTION", "PROCESS_INSPECTION"))) {
            if (!visible.contains(fd.getPanelCode())) continue;
            docs.add(fd);
            try {
                Map<String, Object> head = parse(fd.getData());
                String type = str(head.get("业务类型"));
                if (type.isEmpty()) type = fd.getPanelCode();
                Map<String, Object> d = json.readValue(fd.getDetailData(), new TypeReference<Map<String, Object>>() {});
                Object items = d.get("items");
                if (items instanceof List) {
                    for (Object io : (List<?>) items) {
                        if (!(io instanceof Map)) continue;
                        byType.put(type, byType.getOrDefault(type, 0) + 1);
                        if (!"INSPECTION".equals(fd.getPanelCode()) && !"PROCESS_INSPECTION".equals(fd.getPanelCode())) continue;
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
        out.put("byType", toKv(byType));
        out.put("total", total);
        out.put("pass", pass);
        out.put("passRate", total == 0 ? 0 : Math.round(pass * 1000.0 / total) / 10.0);
        out.put("byMethod", unqualifiedMethods(docs));
        return out;
    }

    // ==================== 工具方法 ====================

    /**
     * 采购看板：订单量/含税金额、供应商分布、物料采购量、到货与入库单量、近 7 天趋势。
     * 金额优先取明细「含税金额」，缺了再退回「金额」。
     */
    static Map<String, Object> aggregatePurchase(List<FormData> orders, List<FormData> arrivals,
                                                 List<FormData> ins) {
        Map<String, Integer> bySupplier = new LinkedHashMap<>();
        Map<String, Double> supplierAmount = new LinkedHashMap<>();
        Map<String, Integer> byStatus = new LinkedHashMap<>();
        Map<String, Double> itemQty = new LinkedHashMap<>();
        Map<String, Integer> dayAdded = new LinkedHashMap<>();
        Map<String, Integer> dayArrived = new LinkedHashMap<>();
        double amount = 0;
        int itemLines = 0;
        for (FormData fd : orders) {
            Map<String, Object> head = parse(fd.getData());
            String supplier = str(head.get("供应商"));
            if (!supplier.isEmpty()) bySupplier.merge(supplier, 1, Integer::sum);
            byStatus.merge(statusOf(fd), 1, Integer::sum);
            String day = dayOf(fd);
            if (!day.isEmpty()) dayAdded.merge(day, 1, Integer::sum);
            for (Map<String, Object> it : itemsOf(fd.getDetailData())) {
                double amt = num(it.get("含税金额"));
                if (amt == 0) amt = num(it.get("金额"));
                amount += amt;
                if (!supplier.isEmpty()) supplierAmount.merge(supplier, amt, Double::sum);
                String name = str(it.get("物料名称"));
                if (name.isEmpty()) name = str(it.get("物料编码"));
                if (!name.isEmpty()) {
                    itemQty.merge(name, num(it.get("数量")), Double::sum);
                    itemLines++;
                }
            }
        }
        for (FormData fd : arrivals) {
            String day = dayOf(fd);
            if (!day.isEmpty()) dayArrived.merge(day, 1, Integer::sum);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("total", orders.size());
        out.put("amount", round2(amount));
        out.put("itemLines", itemLines);
        out.put("arrivalTotal", arrivals.size());
        out.put("inTotal", ins.size());
        out.put("bySupplier", toKv(bySupplier));
        out.put("supplierAmount", topN(toKvDouble(supplierAmount), 5));
        out.put("byStatus", toKv(byStatus));
        out.put("topItems", topN(toKvDouble(itemQty), 6));
        out.put("trend7", trend7(dayAdded, dayArrived));
        return out;
    }

    /** 设计/PDM 看板：BOM / 工艺路线 / 工序 / 存货 档案覆盖 + BOM 子件数排行。 */
    static Map<String, Object> aggregateDesign(List<FormData> boms, List<FormData> routes,
                                               List<FormData> ops, List<FormData> invs) {
        Map<String, Double> bomSize = new LinkedHashMap<>();
        for (FormData fd : boms) {
            List<Object> children = new ArrayList<>();
            try {
                Map<String, Object> d = json.readValue(fd.getDetailData(), new TypeReference<Map<String, Object>>() {});
                if (d.get("children") instanceof List<?> list) children.addAll(list);
            } catch (Exception ignore) {}
            String parent = "";
            for (Object co : children) {
                if (co instanceof Map<?, ?> child) {
                    parent = str(child.get("父件名称"));
                    if (parent.isEmpty()) parent = str(child.get("父件编码"));
                    if (!parent.isEmpty()) break;
                }
            }
            if (parent.isEmpty()) parent = fd.getFormNo();
            bomSize.put(parent, (double) children.size());
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("bom", boms.size());
        out.put("route", routes.size());
        out.put("op", ops.size());
        out.put("inv", invs.size());
        out.put("total", boms.size() + routes.size() + ops.size() + invs.size());
        List<Map<String, Object>> bomTop = toKvDouble(bomSize);
        bomTop.removeIf(row -> ((Number) row.get("value")).doubleValue() <= 0);
        out.put("bomTop", topN(bomTop, 6));
        return out;
    }

    /** 库存补充：四维结存按仓库聚合 + 近 7 天出入库流水。 */
    static Map<String, Object> stockExtra(List<StockBalance> balances, List<StockTxn> txns) {
        Map<String, Double> warehouseQty = new LinkedHashMap<>();
        double balanceQty = 0, balanceAmount = 0;
        for (StockBalance b : balances) {
            double qty = b.getQuantity() == null ? 0 : b.getQuantity().doubleValue();
            double amt = b.getAmount() == null ? 0 : b.getAmount().doubleValue();
            balanceQty += qty;
            balanceAmount += amt;
            String wh = b.getWarehouse() == null || b.getWarehouse().isBlank() ? "未指定仓库" : b.getWarehouse();
            warehouseQty.merge(wh, qty, Double::sum);
        }
        java.time.LocalDate today = java.time.LocalDate.now();
        Map<String, Double> dayIn = new LinkedHashMap<>();
        Map<String, Double> dayOut = new LinkedHashMap<>();
        double totalInQty = 0, totalOutQty = 0;
        for (StockTxn t : txns) {
            String day = t.getBizDate() != null ? t.getBizDate().toString()
                    : (t.getCreateTime() != null ? t.getCreateTime().toLocalDate().toString() : "");
            double qty = t.getQuantity() == null ? 0 : t.getQuantity().doubleValue();
            if ("IN".equals(t.getDirection())) {
                totalInQty += qty;
                if (!day.isEmpty()) dayIn.merge(day, qty, Double::sum);
            } else if ("OUT".equals(t.getDirection())) {
                totalOutQty += qty;
                if (!day.isEmpty()) dayOut.merge(day, qty, Double::sum);
            }
        }
        List<Map<String, Object>> trend = new ArrayList<>();
        for (int i = 6; i >= 0; i--) {
            String day = today.minusDays(i).toString();
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("date", day.substring(5));
            m.put("in", dayIn.getOrDefault(day, 0.0));
            m.put("out", dayOut.getOrDefault(day, 0.0));
            trend.add(m);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("balanceRows", balances.size());
        out.put("balanceQty", round2(balanceQty));
        out.put("balanceAmount", round2(balanceAmount));
        out.put("warehouseQty", toKvDouble(warehouseQty));
        out.put("totalInQty", round2(totalInQty));
        out.put("totalOutQty", round2(totalOutQty));
        out.put("txnTrend", trend);
        return out;
    }

    /** 销售补充：近 7 天订单/已审核趋势 + 客户金额排行。 */
    static Map<String, Object> salesExtra(List<FormData> orders) {
        Map<String, Integer> dayAdded = new LinkedHashMap<>();
        Map<String, Integer> dayDone = new LinkedHashMap<>();
        Map<String, Double> customerAmount = new LinkedHashMap<>();
        double amount = 0;
        for (FormData fd : orders) {
            String day = dayOf(fd);
            if (!day.isEmpty()) {
                dayAdded.merge(day, 1, Integer::sum);
                if ("已审核".equals(statusOf(fd))) dayDone.merge(day, 1, Integer::sum);
            }
            String customer = str(parse(fd.getData()).get("客户"));
            double rowAmount = 0;
            for (Map<String, Object> it : itemsOf(fd.getDetailData())) {
                double v = num(it.get("金额"));
                if (v == 0) v = num(it.get("含税金额"));
                rowAmount += v;
            }
            amount += rowAmount;
            if (!customer.isEmpty() && rowAmount != 0) customerAmount.merge(customer, rowAmount, Double::sum);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("trend7", trend7(dayAdded, dayDone));
        out.put("topCustomers", topN(toKvDouble(customerAmount), 5));
        out.put("avgAmount", orders.isEmpty() ? 0 : round2(amount / orders.size()));
        return out;
    }

    /** 生产补充：工序汇报的报工/合格/不合格数量合计。 */
    static Map<String, Object> processReportTotals(List<FormData> reports) {
        double reportQty = 0, passQty = 0, failQty = 0;
        for (FormData fd : reports) {
            for (Map<String, Object> it : itemsOf(fd.getDetailData())) {
                reportQty += num(it.get("报工数量"));
                passQty += num(it.get("合格数量"));
                failQty += num(it.get("不合格数量"));
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("docCount", reports.size());
        out.put("reportQty", round2(reportQty));
        out.put("passQty", round2(passQty));
        out.put("failQty", round2(failQty));
        return out;
    }

    /** 质量补充：不合格处理方式分布（取 detail.unqualified 的处理方式与处理数量）。 */
    static List<Map<String, Object>> unqualifiedMethods(List<FormData> docs) {
        Map<String, Double> byMethod = new LinkedHashMap<>();
        for (FormData fd : docs) {
            List<Object> rows = new ArrayList<>();
            try {
                Map<String, Object> d = json.readValue(fd.getDetailData(), new TypeReference<Map<String, Object>>() {});
                if (d.get("unqualified") instanceof List<?> list) rows.addAll(list);
            } catch (Exception ignore) {}
            for (Object ro : rows) {
                if (!(ro instanceof Map<?, ?> row)) continue;
                String method = str(row.get("处理方式"));
                if (method.isEmpty()) method = "未填写";
                byMethod.merge(method, num(row.get("处理数量")), Double::sum);
            }
        }
        return toKvDouble(byMethod);
    }

    private static String statusOf(FormData fd) {
        return fd.getStatus() == null ? "" : fd.getStatus();
    }

    /** 单据创建日期（yyyy-MM-dd），取不到返回空串。 */
    private static String dayOf(FormData fd) {
        if (fd.getCreateTime() == null) return "";
        return fd.getCreateTime().toLocalDate().toString();
    }

    private static double num(Object v) {
        if (v instanceof Number n) return n.doubleValue();
        if (v == null) return 0;
        try { return Double.parseDouble(String.valueOf(v).trim()); } catch (NumberFormatException e) { return 0; }
    }

    private static double round2(double v) {
        return Math.round(v * 100) / 100.0;
    }

    /** 明细 items 数组（解析失败或没有 items 时返回空列表）。 */
    static List<Map<String, Object>> itemsOf(String detailJson) {
        List<Map<String, Object>> rows = new ArrayList<>();
        try {
            Map<String, Object> d = json.readValue(detailJson, new TypeReference<Map<String, Object>>() {});
            if (d.get("items") instanceof List<?> items) {
                for (Object io : items) {
                    if (io instanceof Map<?, ?> m) {
                        Map<String, Object> row = new LinkedHashMap<>();
                        m.forEach((k, v) -> row.put(String.valueOf(k), v));
                        rows.add(row);
                    }
                }
            }
        } catch (Exception ignore) {}
        return rows;
    }

    private static List<Map<String, Object>> topN(List<Map<String, Object>> rows, int n) {
        return rows.size() <= n ? rows : new ArrayList<>(rows.subList(0, n));
    }

    /** Map<String,Double> → [{name,value}]，按值降序。 */
    private static List<Map<String, Object>> toKvDouble(Map<String, Double> m) {
        List<Map<String, Object>> out = new ArrayList<>();
        m.entrySet().stream()
                .sorted((a, b) -> Double.compare(b.getValue(), a.getValue()))
                .forEach(e -> {
                    Map<String, Object> kv = new LinkedHashMap<>();
                    kv.put("name", e.getKey());
                    kv.put("value", round2(e.getValue()));
                    out.add(kv);
                });
        return out;
    }

    private static String str(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    /** 明细行数：取 detail_data 中首个明细数组长度（detail/items/products 任一） */
    private static int detailLines(String detailJson) {
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
    private static double sumAmount(String detailJson, String field) {
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
    private static List<Map<String, Object>> toKv(Map<String, Integer> m) {
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
    private static List<Map<String, Object>> trend7(Map<String, Integer> added, Map<String, Integer> done) {
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

    private static String firstProductName(String detailJson) {
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

    private static Map<String, Object> parse(String s) {
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

    // ---------------------------------------------------------------- 周期对比

    /** 支持的周期粒度。周从周一开始，与看板「近 7 天」口径一致。 */
    private static final java.util.Set<String> PERIODS = java.util.Set.of("week", "month", "quarter");

    /**
     * 周期对比：当前周期 vs 上一周期（环比），以及 vs 去年同期（同比）。
     *
     * 为什么单独一个接口而不是塞进 /stats：
     * /stats 是「当前快照」，一次要算十几个业务域，调用频繁；
     * 对比要按周期反复扫表，两者负载差一个量级，混在一起会让首页变慢。
     *
     * 同比在本系统多数情况下取不到数据（业务数据 2026-11 才开始），
     * 这时返回 available=false 而不是 0，前端才能区分「没数据」和「真的是 0」。
     */
    /**
     * 按期率：订单按期交付率 + 工单按期完工率（管理者简报的两个核心指标）。
     *
     * @param period 月份 yyyy-MM；不传表示全部历史
     */
    @GetMapping("/on-time")
    public ApiResult<Map<String, Object>> onTime(@RequestParam(required = false) String period) {
        return ApiResult.ok(onTimeService.onTime(period));
    }

    /**
     * 车间大屏：当前生产要素（在制工单进度、车间分布、异常、最新报工）。
     *
     * <p>单独一个接口而不是复用 /stats：大屏要的是「少而大」的聚合值，
     * 和桌面看板的明细统计不是同一套口径与体量。
     */
    @GetMapping("/prod-board")
    public ApiResult<Map<String, Object>> prodBoard() {
        return ApiResult.ok(prodBoardService.board());
    }

    @GetMapping("/compare")
    public ApiResult<Map<String, Object>> compare(
            Authentication auth,
            @RequestParam(value = "period", defaultValue = "week") String period) {
        String p = PERIODS.contains(period) ? period : "week";
        Map<String, Object> permissions = auth == null ? Map.of() : roleService.getPerms(auth.getName());
        boolean isAdmin = Boolean.TRUE.equals(permissions.get("isAdmin"));
        Set<String> visible = new LinkedHashSet<>();
        if (permissions.get("visiblePanels") instanceof Iterable<?> panels) {
            for (Object panel : panels) visible.add(String.valueOf(panel));
        }

        LocalDate today = LocalDate.now();
        LocalDate curStart;
        LocalDate curEnd = today;
        if ("week".equals(p)) {
            curStart = today.with(java.time.DayOfWeek.MONDAY);
        } else if ("month".equals(p)) {
            curStart = today.withDayOfMonth(1);
        } else {
            curStart = today.withMonth(((today.getMonthValue() - 1) / 3) * 3 + 1).withDayOfMonth(1);
        }
        // 对比必须按「完整周期」对齐，不能用「本周已过几天」当前一期长度：
        // 周一当天算出来是 1 天，上期就退化成「上周日一天」，环比毫无意义。
        // 周比完整 7 天、月比整月、季比整季，再按同长度取上一期。
        long periodDays;
        if ("week".equals(p)) {
            periodDays = 7;
        } else if ("month".equals(p)) {
            periodDays = curStart.lengthOfMonth();
        } else {
            periodDays = curStart.lengthOfMonth() + curStart.plusMonths(1).lengthOfMonth()
                    + curStart.plusMonths(2).lengthOfMonth();
        }
        long spanDays = periodDays;
        // 上期取「同样天数」而不是整段：本周过了 1 天就对上期第 1 天，
        // 否则拿 1 天 vs 7 天做环比，等于必输。
        long elapsedDaysForCompare = java.time.temporal.ChronoUnit.DAYS.between(curStart, curEnd) + 1;
        LocalDate prevStart = curStart.minusDays(periodDays);
        LocalDate prevEnd = prevStart.plusDays(elapsedDaysForCompare - 1);
        LocalDate yoyStart = curStart.minusYears(1);
        LocalDate yoyEnd = curStart.minusYears(1).plusDays(periodDays - 1);

        // 数据实际覆盖区间：对比必须有基线才有意义。
        // 本系统业务数据从 2026-11-04 开始，如果不管这个，
        // 「上月」永远 0 单，环比会算成 +100%，比不显示更误导。
        LocalDate dataFirst = null;
        LocalDate dataLast = null;

        // 一次扫表在内存分桶：数据量小（数百条），比三次分组查询更省往返
        Map<String, Integer> cur = new LinkedHashMap<>();
        Map<String, Integer> prev = new LinkedHashMap<>();
        Map<String, Integer> yoy = new LinkedHashMap<>();
        Map<String, Double> curAmt = new LinkedHashMap<>();
        Map<String, Double> prevAmt = new LinkedHashMap<>();
        int curDocs = 0;
        int prevDocs = 0;
        int yoyDocs = 0;

        for (FormData fd : formMapper.selectList(null)) {
            String code = fd.getPanelCode();
            if (!visible.contains(code)) continue;
            LocalDate day = fd.getCreateTime() == null ? null : fd.getCreateTime().toLocalDate();
            if (day == null) continue;
            if (dataFirst == null || day.isBefore(dataFirst)) dataFirst = day;
            if (dataLast == null || day.isAfter(dataLast)) dataLast = day;
            double amount = readAmount(fd);
            if (!day.isBefore(curStart) && !day.isAfter(curEnd)) {
                cur.merge(code, 1, Integer::sum);
                curAmt.merge(code, amount, Double::sum);
                curDocs++;
            } else if (!day.isBefore(prevStart) && !day.isAfter(prevEnd)) {
                prev.merge(code, 1, Integer::sum);
                prevAmt.merge(code, amount, Double::sum);
                prevDocs++;
            } else if (!day.isBefore(yoyStart) && !day.isAfter(yoyEnd)) {
                yoy.merge(code, 1, Integer::sum);
                yoyDocs++;
            }
        }

        // 产出按业务域汇总：一线关心的不是「14 类单据各几张」，而是生产/销售/采购/质量各推进了多少
        Map<String, Object> domains = new LinkedHashMap<>();
        for (Map.Entry<String, String> g : DOMAIN_GROUPS.entrySet()) {
            String key = g.getKey();
            List<String> panels = DOMAIN_PANELS.getOrDefault(key, List.of());
            int c = 0, pr = 0, y = 0;
            double ca = 0, pa = 0;
            for (String panel : panels) {
                c += cur.getOrDefault(panel, 0);
                pr += prev.getOrDefault(panel, 0);
                y += yoy.getOrDefault(panel, 0);
                ca += curAmt.getOrDefault(panel, 0.0);
                pa += prevAmt.getOrDefault(panel, 0.0);
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("key", key);
            m.put("name", g.getValue());
            m.put("current", c);
            m.put("previous", pr);
            m.put("yoy", y);
            m.put("amountCurrent", round2(ca));
            m.put("amountPrevious", round2(pa));
            // 用 Object 显式声明：三元里混 0.0 与 null 时 Java 会按 double 拆箱，
            // 那正是上一次 500 的来源（NullPointerException: doubleValue）。
            Object delta = (pr == 0) ? (c == 0 ? (Object) 0.0 : null) : (Object) round2((c - pr) * 100.0 / pr);
            m.put("delta", delta);
            m.put("yoyAvailable", yoyDocs > 0);
            m.put("yoyDelta", y == 0 ? null : (Object) round2((c - y) * 100.0 / y));
            domains.put(key, m);
        }

        // 按天序列：当前周期与上一周期各一条，长度对齐才能画在同一张图上
        // 序列只画到「今天」为止：完整周期里还没到的日子画出来是一串 0，
        // 看起来像业务掉了，实际只是时间没到。
        long elapsedDays = java.time.temporal.ChronoUnit.DAYS.between(curStart, curEnd) + 1;
        List<Map<String, Object>> series = new ArrayList<>();
        for (int i = 0; i < elapsedDays; i++) {
            LocalDate d1 = curStart.plusDays(i);
            LocalDate d2 = prevStart.plusDays(i);
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("index", i);
            row.put("currentLabel", d1.getMonthValue() + "-" + d1.getDayOfMonth());
            row.put("prevLabel", d2.getMonthValue() + "-" + d2.getDayOfMonth());
            row.put("current", 0);
            row.put("previous", 0);
            series.add(row);
        }
        // 第二遍只统计总量，避免上面按面板分桶时重复加总
        for (FormData fd : formMapper.selectList(null)) {
            String code = fd.getPanelCode();
            if (!visible.contains(code)) continue;
            LocalDate day = fd.getCreateTime() == null ? null : fd.getCreateTime().toLocalDate();
            if (day == null) continue;
            if (!day.isBefore(curStart) && !day.isAfter(curEnd)) {
                int idx = (int) java.time.temporal.ChronoUnit.DAYS.between(curStart, day);
                if (idx >= 0 && idx < series.size()) {
                    Map<String, Object> row = series.get(idx);
                    row.put("current", ((Number) row.get("current")).intValue() + 1);
                }
            } else if (!day.isBefore(prevStart) && !day.isAfter(prevEnd)) {
                int idx = (int) java.time.temporal.ChronoUnit.DAYS.between(prevStart, day);
                if (idx >= 0 && idx < series.size()) {
                    Map<String, Object> row = series.get(idx);
                    row.put("previous", ((Number) row.get("previous")).intValue() + 1);
                }
            }
        }

        // 环比是否可信：上期必须真的落在数据覆盖区间内。
        // 否则「上期 0 单」不是业务事实，而是系统刚开始用。
        boolean prevCovered = dataFirst != null
                && !prevEnd.isBefore(dataFirst)
                && !prevStart.isAfter(dataLast == null ? today : dataLast);
        boolean yoyCovered = dataFirst != null
                && !yoyEnd.isBefore(dataFirst)
                && !yoyStart.isAfter(dataLast == null ? today : dataLast);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("period", p);
        out.put("currentRange", curStart + " ~ " + curEnd);
        out.put("previousRange", prevStart + " ~ " + prevEnd);
        out.put("yoyRange", yoyStart + " ~ " + yoyEnd);
        out.put("dataRange", (dataFirst == null ? "" : dataFirst + " ~ " + (dataLast == null ? "" : dataLast)));
        out.put("currentDocs", curDocs);
        out.put("previousDocs", prevDocs);
        out.put("yoyDocs", yoyDocs);
        // 无基线时不给百分比：0 → 4 单说成 +100% 会让人误以为暴涨
        Object totalDelta = !prevCovered ? null
                : (prevDocs == 0 ? (Object) (curDocs == 0 ? 0.0 : 100.0) : (Object) round2((curDocs - prevDocs) * 100.0 / prevDocs));
        out.put("totalDelta", totalDelta);
        out.put("prevAvailable", prevCovered);
        out.put("yoyAvailable", yoyCovered && yoyDocs > 0);
        Object yoyDelta = (!yoyCovered || yoyDocs == 0) ? null : (Object) round2((curDocs - yoyDocs) * 100.0 / yoyDocs);
        out.put("yoyDelta", yoyDelta);
        out.put("domains", domains);
        out.put("series", series);
        return ApiResult.ok(out);
    }

    /**
     * 业务域归口：与 AI 侧的 domain 口径保持一致，
     * 免得同一个业务在 MES 看板和 AI 工作台里被算进不同的框。
     */
    private static final Map<String, String> DOMAIN_GROUPS = new LinkedHashMap<>();
    private static final Map<String, List<String>> DOMAIN_PANELS = new LinkedHashMap<>();
    static {
        DOMAIN_GROUPS.put("production", "生产");
        DOMAIN_GROUPS.put("warehouse", "仓储");
        DOMAIN_GROUPS.put("sales", "销售");
        DOMAIN_GROUPS.put("purchase", "采购");
        DOMAIN_GROUPS.put("quality", "质量");
        DOMAIN_PANELS.put("production", List.of("MANU_ORDER", "PROCESS_REPORT", "DISPATCH"));
        DOMAIN_PANELS.put("warehouse", List.of("PURCHASE_IN", "MATERIAL_OUT", "SALE_OUT", "FINISH_IN", "OTHER_IN"));
        DOMAIN_PANELS.put("sales", List.of("SO_ORDER", "SALE_INV", "RECEIPT"));
        DOMAIN_PANELS.put("purchase", List.of("PU_ORDER", "ARRIVAL_IN", "PURCHASE_IN"));
        DOMAIN_PANELS.put("quality", List.of("FIRST_INSPECT", "FINISH_INSPECT", "INSPECTION",
                "PROCESS_INSPECT_APPLY", "PROCESS_INSPECTION"));
    }

    /** 从表单头里读金额；读不到按 0 计，不让一条坏数据把整张图带偏。 */
    private double readAmount(FormData fd) {
        try {
            Map<String, Object> head = parse(fd.getData());
            for (String key : new String[]{"含税金额", "金额", "价税合计", "总金额"}) {
                Object v = head.get(key);
                if (v != null) {
                    String s = String.valueOf(v).replace(",", "").trim();
                    if (!s.isEmpty()) return Double.parseDouble(s);
                }
            }
        } catch (Exception ignored) {
            // 金额不是关键路径，解析失败按 0 处理
        }
        return 0.0;
    }

}