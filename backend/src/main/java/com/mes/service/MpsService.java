package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 主生产计划（MPS）与物料需求汇总（MRP）。
 *
 * <p>分工：MPS 回答「这个计划期要生产什么、多少、什么时候开工完工」；MRP 回答「按这些计划，
 * 物料缺口是多少、该采购还是该自己做」。计划分析（PlanAnalyzeQueryService）已经能按单张订单
 * 展开 BOM 与缺口，这里不重写展开算法：MPS 把多张订单收敛成计划期，MRP 把多张订单的分析结果
 * 按物料编码横向汇总。
 */
@Service
public class MpsService {

    private static final String PANEL = "MPS_PLAN";
    private static final String PANEL_SO = "SO_ORDER";
    private static final DateTimeFormatter PERIOD = DateTimeFormatter.ofPattern("yyyy-MM");

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final PlanAnalyzeQueryService analyzeQuery;

    public MpsService(FormDataMapper formMapper, ObjectMapper json,
                      PlanAnalyzeQueryService analyzeQuery) {
        this.formMapper = formMapper;
        this.json = json;
        this.analyzeQuery = analyzeQuery;
    }

    /** 一条 MPS 计划行。 */
    public record PlanRow(String itemCode, String itemName, String spec, String unit,
                          BigDecimal qty, String startDate, String endDate,
                          String sourceOrder, int sourceRow) {}

    // ---------- MPS：销售订单 → 计划期 ----------

    /**
     * 销售订单明细 → 计划行。
     *
     * <p>只取「还没计划过」的量：已计划量按「来源销售订单 + 行号」累计，同一行重复生成不会翻倍。
     */
    public List<PlanRow> rowsOf(String soNo, Map<String, Object> detail,
                                Map<String, BigDecimal> plannedByRow,
                                LocalDate start, LocalDate end) {
        List<PlanRow> out = new ArrayList<>();
        int rowNo = 0;
        for (Map<String, Object> row : rows(detail)) {
            rowNo += 1;
            String code = text(first(row, "存货编码", "产品编码"));
            if (code.isBlank()) continue;
            BigDecimal qty = number(first(row, "数量", "销售数量"));
            if (qty.signum() <= 0) continue;
            BigDecimal planned = plannedByRow.getOrDefault(key(soNo, rowNo), BigDecimal.ZERO);
            BigDecimal open = qty.subtract(planned);
            if (open.signum() <= 0) continue;
            out.add(new PlanRow(code,
                    text(first(row, "存货名称", "产品名称")),
                    text(first(row, "规格型号")),
                    text(first(row, "销售单位", "计量单位", "主单位")),
                    open,
                    start == null ? "" : start.toString(),
                    end == null ? "" : end.toString(),
                    soNo, rowNo));
        }
        return out;
    }

    /**
     * 用已审核销售订单生成（或追加到）计划期的 MPS：同一计划期只维护一张草稿计划，
     * 已有明细保留，只补未计划的部分。
     */
    @Transactional
    public Map<String, Object> generate(String period) {
        String planPeriod = period == null || period.isBlank() ? LocalDate.now().format(PERIOD) : period.trim();
        Map<String, BigDecimal> plannedByRow = plannedQuantity();
        LocalDate start = LocalDate.now();
        LocalDate end = start.plusDays(7);

        List<FormData> orders = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, PANEL_SO)
                .eq(FormData::getStatus, "已审核")
                .orderByAsc(FormData::getId));
        List<PlanRow> newRows = new ArrayList<>();
        Set<String> sources = new LinkedHashSet<>();
        for (FormData order : orders) {
            List<PlanRow> rows = rowsOf(order.getFormNo(), parse(order.getDetailData()),
                    plannedByRow, start, end);
            if (rows.isEmpty()) continue;
            newRows.addAll(rows);
            sources.add(order.getFormNo());
        }
        if (newRows.isEmpty()) {
            return result("", planPeriod, 0, 0, List.of());
        }

        FormData plan = openPlanOf(planPeriod);
        boolean created = plan == null;
        if (created) {
            plan = new FormData();
            plan.setPanelCode(PANEL);
            plan.setFormNo(nextNo());
            plan.setStatus("草稿");
            Map<String, Object> head = new LinkedHashMap<>();
            head.put("单据日期", LocalDate.now().toString());
            head.put("单据编号", plan.getFormNo());
            head.put("计划期", planPeriod);
            head.put("计划名称", planPeriod + " 主生产计划");
            plan.setData(toJson(head));
            plan.setDetailData(toJson(Map.of("items", List.of())));
            plan.setCreateTime(java.time.LocalDateTime.now());
        }
        Map<String, Object> head = parse(plan.getData());
        Map<String, Object> detail = parse(plan.getDetailData());
        List<Object> items = new ArrayList<>();
        for (Map<String, Object> row : rows(detail)) items.add(row);
        int added = 0;
        for (PlanRow row : newRows) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("存货编码", row.itemCode());
            item.put("存货名称", row.itemName());
            item.put("规格型号", row.spec());
            item.put("计量单位", row.unit());
            item.put("计划数量", row.qty());
            item.put("计划开工日期", row.startDate());
            item.put("计划完工日期", row.endDate());
            item.put("来源销售订单", row.sourceOrder());
            item.put("来源订单行号", row.sourceRow());
            items.add(item);
            added += 1;
        }
        detail.put("items", items);
        plan.setData(toJson(head));
        plan.setDetailData(toJson(detail));
        plan.setUpdateTime(java.time.LocalDateTime.now());
        if (created) formMapper.insert(plan);
        else formMapper.updateById(plan);
        return result(plan.getFormNo(), planPeriod, added, sources.size(), List.of());
    }

    /** 某计划期里还在维护的计划单（草稿优先；已确认的不再追加）。 */
    private FormData openPlanOf(String period) {
        for (FormData fd : plans()) {
            if (!"草稿".equals(fd.getStatus())) continue;
            if (period.equals(text(parse(fd.getData()).get("计划期")))) return fd;
        }
        return null;
    }

    /** 所有 MPS 单的「来源订单|行号 → 已计划数量」。 */
    private Map<String, BigDecimal> plannedQuantity() {
        Map<String, BigDecimal> planned = new LinkedHashMap<>();
        for (FormData fd : plans()) {
            if ("作废".equals(fd.getStatus())) continue;
            for (Map<String, Object> row : rows(parse(fd.getDetailData()))) {
                String soNo = text(first(row, "来源销售订单"));
                Object rowNo = first(row, "来源订单行号");
                if (soNo.isBlank() || rowNo == null) continue;
                String key = key(soNo, (int) number(rowNo).doubleValue());
                planned.merge(key, number(first(row, "计划数量")), BigDecimal::add);
            }
        }
        return planned;
    }

    private List<FormData> plans() {
        return formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, PANEL).orderByDesc(FormData::getId));
    }

    // ---------- MRP：计划 → 物料净需求 ----------

    /**
     * MRP 汇总：读一张 MPS 的来源订单，逐单走计划分析（BOM 展开 + 库存扣减），
     * 再按物料编码横向汇总毛需求/现存/净需求，给出「请购 / 生产 / 委外」建议。
     */
    public Map<String, Object> mrp(String mpsNo) {
        if (mpsNo == null || mpsNo.isBlank()) throw new IllegalArgumentException("缺少主生产计划编号");
        FormData plan = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, PANEL).eq(FormData::getFormNo, mpsNo));
        if (plan == null) throw new IllegalArgumentException("主生产计划不存在：" + mpsNo);
        Map<String, Object> head = parse(plan.getData());
        List<Map<String, Object>> analyses = new ArrayList<>();
        Set<String> orders = new LinkedHashSet<>();
        // 计划里引用的订单可能已被删除或中止（实测踩到：MPS 明细留着 SO-20261110-001，
        // 单据却已不存在）。原来直接 analyze 会抛错让整次 MRP 运算中断，
        // 一个坏引用拖垮全部计划。改成跳过并记录，其余订单照常算。
        List<String> missing = new ArrayList<>();
        for (Map<String, Object> row : rows(parse(plan.getDetailData()))) {
            String soNo = text(first(row, "来源销售订单"));
            if (soNo.isBlank() || !orders.add(soNo)) continue;
            try {
                analyses.add(analyzeQuery.analyze(soNo));
            } catch (RuntimeException ex) {
                missing.add(soNo + "（" + ex.getMessage() + "）");
            }
        }
        List<Map<String, Object>> lines = aggregate(analyses);
        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("计划编号", mpsNo);
        summary.put("计划期", text(head.get("计划期")));
        summary.put("来源订单", new ArrayList<>(orders));
        summary.put("物料行", lines.size());
        // 让调用方看得见「哪些订单没算进去」，而不是悄悄少算
        if (!missing.isEmpty()) summary.put("缺失订单", missing);
        summary.put("需采购", lines.stream().filter(r -> "请购".equals(r.get("建议"))).count());
        summary.put("需生产", lines.stream().filter(r -> "生产".equals(r.get("建议"))).count());
        summary.put("需委外", lines.stream().filter(r -> "委外加工".equals(r.get("建议"))).count());
        summary.put("缺口合计", lines.stream()
                .map(r -> (BigDecimal) r.get("净需求"))
                .reduce(BigDecimal.ZERO, BigDecimal::add));
        Map<String, Object> out = new LinkedHashMap<>(summary);
        out.put("rows", lines);
        return out;
    }

    /**
     * MRP 净需求 → 生成请购单（草稿）。
     *
     * <p>这是补上的链路断点：MRP 只算不写，AI 看到「建议请购 5 个物料」却没有任何入口
     * 把它变成单据，采购员得照着屏幕手工新建。这里把「建议=请购」且净需求>0 的行
     * 直接落成一张请购单草稿，下游 PU_ORDER 可以选单过来。
     *
     * <p>只生成草稿不自动审核：请购涉及预算与审批责任，必须由人确认后走审核流。
     *
     * @return 新单号；没有需要请购的行时返回 null
     */
    @Transactional
    public String createPurchaseReq(String mpsNo, String factoryCode) {
        Map<String, Object> mrp = mrp(mpsNo);
        List<Map<String, Object>> rows = castRows(mrp.get("rows"));
        List<Map<String, Object>> needBuy = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            if (!"请购".equals(text(row.get("建议")))) continue;
            BigDecimal net = decimal(row.get("净需求"));
            if (net.signum() <= 0) continue;
            needBuy.add(row);
        }
        if (needBuy.isEmpty()) return null;

        // 单号：CG + 日期 + 3 位流水，与 DocNoRules 的 PU_REQ 前缀一致
        String date = LocalDate.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"));
        String prefix = "CG-" + date + "-";
        long used = formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_REQ")
                .likeRight(FormData::getFormNo, prefix));
        String no = prefix + String.format("%03d", used + 1);

        List<Map<String, Object>> items = new ArrayList<>();
        BigDecimal total = BigDecimal.ZERO;
        int rowIndex = 0;
        for (Map<String, Object> row : needBuy) {
            rowIndex += 1;
            BigDecimal qty = decimal(row.get("净需求"));
            BigDecimal price = decimal(row.get("单价"));
            BigDecimal amount = price.signum() > 0
                    ? price.multiply(qty).setScale(2, java.math.RoundingMode.HALF_UP)
                    : BigDecimal.ZERO;
            total = total.add(amount);
            Map<String, Object> item = new LinkedHashMap<>();
            // 字段名以 MRP 返回的聚合行为准（存货编码/存货名称），
            // 之前按「物料编码」取，生成出来的请购单存货编码是空的。
            item.put("存货编码", text(first(row, "存货编码", "物料编码")));
            item.put("存货名称", text(first(row, "存货名称", "物料名称")));
            item.put("规格型号", text(row.get("规格型号")));
            item.put("采购单位", text(first(row, "计量单位", "采购单位")));
            item.put("数量", qty);
            item.put("单价", price);
            item.put("金额", amount);
            item.put("需求日期", LocalDate.now().plusDays(7).toString());
            // 这里刻意不写「来源面板/来源单号/来源行号」三件套。
            //
            // 原因：请购行是 BOM 展开后的子件（CL002×15102.8 kg），而 MPS/销售订单行是
            // 成品（CP002×100 件）。两者不是同一张单据的同一行，单位与数量口径都不同，
            // 硬挂上去必然触发「来源明细超量」校验（实测踩到：可执行 100 件 vs 本次 15102.8 kg）。
            //
            // validateSourceLinks 的规则是「来源面板与来源单号同时为空就跳过校验」，
            // 所以只保留人可读的来源说明，不伪造单据行引用。
            item.put("来源单据", "主生产计划");
            item.put("来源说明", "MRP 按 BOM 展开计算的净需求（计划 " + mpsNo + "）");
            Object rawSources = row.get("来源订单");
            if (rawSources instanceof List<?> list && !list.isEmpty()) {
                item.put("来源订单", String.join("、", list.stream().map(String::valueOf).toList()));
            }
            item.put("现存量", decimal(first(row, "现存量", "现存")));
            items.add(item);
        }

        Map<String, Object> head = new LinkedHashMap<>();
        head.put("单据编号", no);
        head.put("单据日期", LocalDate.now().toString());
        head.put("请购人", "系统（MRP）");
        head.put("需求日期", LocalDate.now().plusDays(7).toString());
        head.put("来源单据", "主生产计划");
        head.put("来源单号", mpsNo);
        head.put("含税总金额", total);
        head.put("备注", "由 MRP 运算按净需求自动生成");
        head.put("单据状态", "草稿");

        Map<String, Object> detail = new LinkedHashMap<>();
        detail.put("items", items);

        FormData fd = new FormData();
        fd.setPanelCode("PU_REQ");
        fd.setFormNo(no);
        try {
            fd.setData(json.writeValueAsString(head));
            fd.setDetailData(json.writeValueAsString(detail));
        } catch (Exception e) {
            throw new IllegalStateException("请购单序列化失败：" + e.getMessage(), e);
        }
        fd.setStatus("草稿");
        // 账套不在这里 set：FormData 没有该字段，由 MyBatis-Plus 租户拦截器
        // 按当前请求的 X-Factory-Code 自动注入 factory_code。
        fd.setCreateTime(java.time.LocalDateTime.now());
        fd.setUpdateTime(java.time.LocalDateTime.now());
        formMapper.insert(fd);
        return no;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> castRows(Object value) {
        return value instanceof List<?> list ? (List<Map<String, Object>>) list : List.of();
    }

    private static BigDecimal decimal(Object value) {
        if (value == null) return BigDecimal.ZERO;
        if (value instanceof BigDecimal b) return b;
        if (value instanceof Number n) return BigDecimal.valueOf(n.doubleValue());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }

    /**
     * 多张订单的分析结果按物料编码汇总。纯函数：不碰库，便于单测。
     *
     * <p>成品行（level 0）缺口按「自制 → 生产」建议；物料行按供应类型给「请购 / 生产 / 委外加工」。
     * 现存量取各单里的最大值（同一个物料在不同订单分析里库存是同一个数，相加会翻倍）。
     */
    public static List<Map<String, Object>> aggregate(List<Map<String, Object>> analyses) {
        Map<String, Map<String, Object>> merged = new LinkedHashMap<>();
        for (Map<String, Object> analysis : analyses == null ? List.<Map<String, Object>>of() : analyses) {
            // 订单号在 analysis.order.no 里，顶层没有 salesOrderNo 这个键 ——
            // 之前取错键，导致聚合行「来源订单」永远是空数组，
            // 下游生成请购单时拿不到真实来源，只能瞎挂到 MPS 行上并触发超量校验。
            // 订单号在 analysis.order.no 里，顶层没有 salesOrderNo 这个键 ——
            // 之前取错键，导致聚合行「来源订单」永远是空数组，
            // 下游生成请购单时拿不到真实来源，只能瞎挂到 MPS 行上并触发超量校验。
            String soNo = text(analysis.get("salesOrderNo"));
            if (soNo.isBlank() && analysis.get("order") instanceof Map<?, ?> order) {
                soNo = text(order.get("no"));
            }
            for (Map<String, Object> product : listOf(analysis.get("products"))) {
                BigDecimal net = number(product.get("shortQty"));
                if (net.signum() <= 0) continue;
                merge(merged, product, soNo, "自制", net);
            }
            for (Map<String, Object> material : listOf(analysis.get("materials"))) {
                BigDecimal level = number(material.get("level"));
                if (level.signum() <= 0) continue;
                BigDecimal net = number(material.get("netQty"));
                if (net.signum() <= 0) continue;
                merge(merged, material, soNo, text(material.get("supplyType")), net);
            }
        }
        List<Map<String, Object>> out = new ArrayList<>(merged.values());
        out.sort((a, b) -> ((BigDecimal) b.get("净需求")).compareTo((BigDecimal) a.get("净需求")));
        return out;
    }

    private static void merge(Map<String, Map<String, Object>> merged, Map<String, Object> line,
                              String sourceOrder, String supplyType, BigDecimal net) {
        String code = text(line.get("code"));
        if (code.isBlank()) return;
        Map<String, Object> row = merged.computeIfAbsent(code, key -> {
            Map<String, Object> created = new LinkedHashMap<>();
            created.put("存货编码", code);
            created.put("存货名称", text(line.get("name")));
            created.put("规格型号", text(line.get("spec")));
            created.put("计量单位", text(line.get("unit")));
            created.put("毛需求", BigDecimal.ZERO);
            created.put("现存量", BigDecimal.ZERO);
            created.put("净需求", BigDecimal.ZERO);
            created.put("来源订单", new ArrayList<String>());
            return created;
        });
        // 成品行用 demandQty、物料行用 grossQty：口径不同，取值要兜底，否则成品毛需求恒为 0
        Object grossValue = line.get("grossQty") == null ? line.get("demandQty") : line.get("grossQty");
        row.put("毛需求", ((BigDecimal) row.get("毛需求")).add(number(grossValue)));
        BigDecimal stock = number(line.get("stockQty"));
        if (stock.compareTo((BigDecimal) row.get("现存量")) > 0) row.put("现存量", stock);
        row.put("净需求", ((BigDecimal) row.get("净需求")).add(net));
        String type = supplyType.isBlank() ? "自制" : supplyType;
        row.put("供应类型", type);
        row.put("建议", suggestOf(type));
        @SuppressWarnings("unchecked")
        List<String> sources = (List<String>) row.get("来源订单");
        if (!sourceOrder.isBlank() && !sources.contains(sourceOrder)) sources.add(sourceOrder);
    }

    /** 供应类型 → 动作。 */
    public static String suggestOf(String supplyType) {
        String type = supplyType == null ? "" : supplyType.trim();
        if (type.contains("外购")) return "请购";
        if (type.contains("委外")) return "委外加工";
        return "生产";
    }

    // ---------- 工具 ----------

    private Map<String, Object> result(String no, String period, int added, int orders, List<Object> rows) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("编号", no);
        out.put("计划期", period);
        out.put("新增计划行", added);
        out.put("涉及销售订单", orders);
        out.put("rows", rows);
        return out;
    }

    private String nextNo() {
        String prefix = DocNoRules.prefixOf(PANEL);
        LocalDate day = LocalDate.now();
        int seq = 1;
        String no;
        do {
            no = DocNoRules.format(prefix, day, "", seq++);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getFormNo, no)) > 0);
        return no;
    }

    private static String key(String soNo, int rowNo) {
        return soNo + "|" + rowNo;
    }

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return Map.of();
        try {
            return json.readValue(raw, new TypeReference<>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("主生产计划数据序列化失败", e);
        }
    }

    /** 明细行：多个页签按顺序合并（与库存事务/作业任务同一口径）。 */
    private static List<Map<String, Object>> rows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (detail == null) return out;
        for (Object value : detail.values()) {
            for (Map<String, Object> row : listOf(value)) out.add(row);
        }
        return out;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOf(Object value) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Map<?, ?> map) {
                    Map<String, Object> typed = new LinkedHashMap<>();
                    map.forEach((k, v) -> typed.put(String.valueOf(k), v));
                    out.add(typed);
                }
            }
        }
        return out;
    }

    private static Object first(Map<String, Object> row, String... keys) {
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return null;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }
}
