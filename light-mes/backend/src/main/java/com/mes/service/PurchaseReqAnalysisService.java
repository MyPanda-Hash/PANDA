package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.PuReqSource;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PuReqSourceMapper;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.Font;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.springframework.stereotype.Service;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 采购需求分析（轻量工作台）：把计划分析的缺口快照 + 手工补录需求跨单汇总，
 * 并按「请购 → 采购 → 到货」实时回写进度。本服务不做二次净算，缺口直接取快照净需求。
 */
@Service
public class PurchaseReqAnalysisService {

    private static final Set<String> CLOSED = Set.of("已中止", "已关闭");
    private static final String ACTIVE = "有效";

    private final FormDataMapper formMapper;
    private final PuReqSourceMapper sourceMapper;
    private final PxService pxService;
    private final ObjectMapper json;

    public PurchaseReqAnalysisService(FormDataMapper formMapper, PuReqSourceMapper sourceMapper,
                                      PxService pxService, ObjectMapper json) {
        this.formMapper = formMapper;
        this.sourceMapper = sourceMapper;
        this.pxService = pxService;
        this.json = json;
    }

    /** 汇总查询：返回分页行 + 概览。 */
    public Map<String, Object> query(Map<String, Object> filters, int pageNo, int pageSize) {
        List<Row> all = aggregate(filters);
        Map<String, Object> summary = summaryOf(all);
        int p = Math.max(1, pageNo);
        int size = Math.min(Math.max(1, pageSize), 500);
        int from = Math.min(all.size(), (p - 1) * size);
        int to = Math.min(all.size(), from + size);
        List<Map<String, Object>> list = new ArrayList<>();
        for (Row row : all.subList(from, to)) list.add(row.toMap());
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("list", list);
        out.put("total", all.size());
        out.put("pageNo", p);
        out.put("pageSize", size);
        out.put("summary", summary);
        return out;
    }

    /** 某物料的来源明细。 */
    public List<Map<String, Object>> sources(String materialCode, String spec, String unit) {
        LambdaQueryWrapper<PuReqSource> qw = new LambdaQueryWrapper<PuReqSource>()
                .eq(PuReqSource::getStatus, ACTIVE)
                .eq(PuReqSource::getMaterialCode, materialCode)
                .orderByAsc(PuReqSource::getId);
        if (spec != null && !spec.isBlank()) qw.eq(PuReqSource::getSpec, spec);
        if (unit != null && !unit.isBlank()) qw.eq(PuReqSource::getUnit, unit);
        List<Map<String, Object>> out = new ArrayList<>();
        for (PuReqSource source : sourceMapper.selectList(qw)) out.add(sourceMap(source));
        return out;
    }

    /** 某物料的单据链路：请购 → 采购订单 → 采购入库。 */
    public List<Map<String, Object>> chain(String materialCode) {
        List<Map<String, Object>> out = new ArrayList<>();
        appendChain(out, "PU_REQ", "请购单", materialCode, "数量");
        appendChain(out, "PU_ORDER", "采购订单", materialCode, "数量");
        appendChain(out, "PURCHASE_IN", "采购入库单", materialCode, "实收数量");
        return out;
    }

    /** 生成请购单：单行/批量，同物料多来源合并为一行。 */
    public Map<String, Object> generate(List<Map<String, Object>> items) {
        if (items == null || items.isEmpty()) {
            throw new IllegalArgumentException("请选择要生成请购单的物料");
        }
        Map<String, Row> rows = index(aggregate(Map.of()));
        List<Map<String, Object>> prItems = new ArrayList<>();
        for (Map<String, Object> item : items) {
            String code = text(item, "materialCode");
            String key = key(code, text(item, "spec"), text(item, "unit"));
            Row row = rows.get(key);
            if (row == null) {
                throw new IllegalArgumentException("物料不在当前缺口清单：" + code);
            }
            if (row.requested >= row.gap - 0.0001) {
                throw new IllegalStateException("该物料已生成请购单，请勿重复生成：" + code);
            }
            double qty = item.containsKey("qty") ? num(item.get("qty")) : round2(row.gap - row.received);
            if (qty <= 0) {
                throw new IllegalArgumentException("请购数量必须大于 0：" + code);
            }
            Map<String, Object> line = new LinkedHashMap<>();
            line.put("存货编码", code);
            line.put("存货名称", row.name);
            line.put("规格型号", row.spec);
            line.put("采购单位", row.unit);
            line.put("数量", round2(qty));
            line.put("需求日期", row.earliestDue.isEmpty() ? LocalDate.now().toString() : row.earliestDue);
            line.put("需求来源", "采购需求分析");
            line.put("备注", "采购需求分析：缺口 " + fmt(row.gap) + "，未完成 " + fmt(row.remaining));
            List<Map<String, Object>> snapshot = new ArrayList<>();
            for (Map<String, Object> source : row.sources) {
                Map<String, Object> one = new LinkedHashMap<>();
                one.put("计划分析单号", text(source, "计划分析单号"));
                one.put("销售订单号", text(source, "销售订单号"));
                one.put("客户", text(source, "客户"));
                one.put("交期", text(source, "交期"));
                one.put("净需求", source.getOrDefault("净需求", BigDecimal.ZERO));
                one.put("来源链", text(source, "来源链"));
                one.put("来源类型", text(source, "来源类型"));
                snapshot.add(one);
            }
            try {
                line.put("来源快照", json.writeValueAsString(snapshot));
            } catch (Exception e) {
                line.put("来源快照", "[]");
            }
            prItems.add(line);
        }
        Map<String, Object> generated = pxService.generatePurchaseReqFromAnalysis(
                prItems, "由采购需求分析生成（" + prItems.size() + " 项物料）");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("编号", generated.getOrDefault("编号", ""));
        out.put("行数", prItems.size());
        return out;
    }

    /** 手工补录需求（备货/安全库存采购）：无来源单据，参与同一套汇总。 */
    public Map<String, Object> manual(Map<String, Object> body) {
        String code = text(body, "materialCode");
        if (code.isEmpty()) throw new IllegalArgumentException("物料编码不能为空");
        double qty = num(body.get("qty"));
        if (qty <= 0) throw new IllegalArgumentException("数量必须大于 0");
        PuReqSource row = new PuReqSource();
        row.setPlanNo("");
        row.setSoNo("");
        row.setCustomer("");
        row.setDueDate(text(body, "dueDate"));
        row.setMaterialCode(code);
        row.setMaterialName(text(body, "materialName"));
        row.setSpec(text(body, "spec"));
        row.setUnit(text(body, "unit"));
        row.setLevel(0);
        row.setPath("");
        row.setGrossQty(BigDecimal.valueOf(qty));
        row.setStockQty(BigDecimal.ZERO);
        row.setNetQty(BigDecimal.valueOf(qty));
        row.setSupplyType("外购");
        row.setSourceType("手工");
        row.setSupplier(text(body, "supplier"));
        row.setRemark(text(body, "remark"));
        row.setStatus(ACTIVE);
        row.setCreatedBy(text(body, "operator"));
        sourceMapper.insert(row);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", row.getId());
        return out;
    }

    /** 撤销手工需求：只允许撤手工来源，撤销后从汇总剔除。 */
    public Map<String, Object> revokeManual(Long id) {
        PuReqSource row = sourceMapper.selectById(id);
        if (row == null) throw new IllegalArgumentException("手工需求不存在：" + id);
        if (!"手工".equals(row.getSourceType())) {
            throw new IllegalArgumentException("只有手工补录需求可以撤销");
        }
        if (!ACTIVE.equals(row.getStatus())) {
            throw new IllegalArgumentException("该需求已撤销");
        }
        sourceMapper.update(null, new LambdaUpdateWrapper<PuReqSource>()
                .eq(PuReqSource::getId, id)
                .set(PuReqSource::getStatus, "撤销"));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        return out;
    }

    /** 手工需求列表（撤销入口用）。 */
    public List<Map<String, Object>> manualList() {
        List<PuReqSource> rows = sourceMapper.selectList(new LambdaQueryWrapper<PuReqSource>()
                .eq(PuReqSource::getSourceType, "手工")
                .orderByDesc(PuReqSource::getId));
        List<Map<String, Object>> out = new ArrayList<>();
        for (PuReqSource row : rows) out.add(sourceMap(row));
        return out;
    }

    /** 导出汇总（xlsx）。 */
    public byte[] export(Map<String, Object> filters) throws IOException {
        List<Row> rows = aggregate(filters);
        try (XSSFWorkbook wb = new XSSFWorkbook(); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            Sheet sheet = wb.createSheet("采购需求分析");
            CellStyle head = wb.createCellStyle();
            Font font = wb.createFont();
            font.setBold(true);
            head.setFont(font);
            String[] columns = {"物料编码", "物料名称", "规格型号", "单位", "缺口合计", "已请购量",
                    "未请购量", "请购差异", "请购状态", "已采购量", "已到货量", "未完成量",
                    "最早需求日期", "建议供应商", "状态", "来源"};
            org.apache.poi.ss.usermodel.Row title = sheet.createRow(0);
            for (int i = 0; i < columns.length; i++) {
                Cell cell = title.createCell(i);
                cell.setCellValue(columns[i]);
                cell.setCellStyle(head);
                sheet.setColumnWidth(i, 16 * 256);
            }
            int index = 1;
            for (Row row : rows) {
                org.apache.poi.ss.usermodel.Row line = sheet.createRow(index++);
                line.createCell(0).setCellValue(row.code);
                line.createCell(1).setCellValue(row.name);
                line.createCell(2).setCellValue(row.spec);
                line.createCell(3).setCellValue(row.unit);
                line.createCell(4).setCellValue(row.gap);
                line.createCell(5).setCellValue(row.requested);
                line.createCell(6).setCellValue(row.notRequestedQty);
                line.createCell(7).setCellValue(row.requestDiff);
                line.createCell(8).setCellValue(row.requestStatus);
                line.createCell(9).setCellValue(row.ordered);
                line.createCell(10).setCellValue(row.received);
                line.createCell(11).setCellValue(row.remaining);
                line.createCell(12).setCellValue(row.earliestDue);
                line.createCell(13).setCellValue(row.supplier);
                line.createCell(14).setCellValue(row.status);
                String source = row.sourceTypes.contains("计划") && row.sourceTypes.contains("手工")
                        ? "计划/手工" : row.sourceTypes.contains("手工") ? "手工" : "计划";
                line.createCell(15).setCellValue(source);
            }
            wb.write(out);
            return out.toByteArray();
        }
    }

    // ---------- 汇总 ----------

    private List<Row> aggregate(Map<String, Object> filters) {
        List<PuReqSource> sources = sourceMapper.selectList(new LambdaQueryWrapper<PuReqSource>()
                .eq(PuReqSource::getStatus, ACTIVE));
        Map<String, Double> requested = progress("PU_REQ", "数量");
        Map<String, Double> ordered = progress("PU_ORDER", "数量");
        Map<String, Double> received = progress("PURCHASE_IN", "实收数量");
        Map<String, Row> rows = new LinkedHashMap<>();
        for (PuReqSource source : sources) {
            String key = key(source.getMaterialCode(), source.getSpec(), source.getUnit());
            Row row = rows.get(key);
            if (row == null) {
                row = new Row(source);
                rows.put(key, row);
            }
            row.addSource(source);
        }
        List<Row> out = new ArrayList<>();
        for (Row row : rows.values()) {
            row.requested = round2(requested.getOrDefault(row.code, 0.0));
            row.ordered = round2(ordered.getOrDefault(row.code, 0.0));
            row.received = round2(received.getOrDefault(row.code, 0.0));
            row.remaining = round2(Math.max(0, row.gap - row.received));
            row.notRequestedQty = round2(Math.max(0, row.gap - row.requested));
            row.requestDiff = round2(row.requested - row.gap);
            row.requestStatus = requestStatusOf(row.gap, row.requested);
            row.status = statusOf(row.gap, row.requested, row.ordered, row.received);
            if (matches(row, filters)) out.add(row);
        }
        out.sort(Comparator
                .comparing((Row row) -> row.earliestDue.isEmpty() ? "9999-12-31" : row.earliestDue)
                .thenComparing(row -> row.code));
        return out;
    }

    private Map<String, Double> progress(String panelCode, String qtyField) {
        Map<String, Double> out = new HashMap<>();
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode));
        for (FormData doc : docs) {
            String status = doc.getStatus() == null ? "" : doc.getStatus();
            if (CLOSED.contains(status)) continue;
            if ("PURCHASE_IN".equals(panelCode) && !"已审核".equals(status)) continue;
            Map<String, Object> detail = parse(doc.getDetailData());
            for (Map<String, Object> item : rows(detail)) {
                String code = text(item, "存货编码");
                if (code.isEmpty()) code = text(item, "材料编码");
                if (code.isEmpty()) continue;
                out.merge(code, num(item.get(qtyField)), Double::sum);
            }
        }
        return out;
    }

    private Map<String, Object> summaryOf(List<Row> rows) {
        double gap = 0;
        double notRequestedQty = 0;
        int overRequested = 0;
        int notRequested = 0;
        int inProgress = 0;
        int completed = 0;
        int risk = 0;
        String todayPlus7 = LocalDate.now().plusDays(7).toString();
        for (Row row : rows) {
            gap += row.gap;
            notRequestedQty += row.notRequestedQty;
            if (row.requestDiff > 0.0001) overRequested++;
            switch (row.status) {
                case "未请购" -> notRequested++;
                case "部分请购", "已请购未采购", "已采购未到货" -> inProgress++;
                case "已完成", "超量" -> completed++;
                default -> { }
            }
            if (!row.earliestDue.isEmpty() && row.earliestDue.compareTo(todayPlus7) <= 0
                    && !"已完成".equals(row.status) && !"超量".equals(row.status)) {
                risk++;
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("materials", rows.size());
        out.put("gapTotal", round2(gap));
        out.put("notRequestedQty", round2(notRequestedQty));
        out.put("overRequested", overRequested);
        out.put("notRequested", notRequested);
        out.put("inProgress", inProgress);
        out.put("completed", completed);
        out.put("risk", risk);
        return out;
    }

    private boolean matches(Row row, Map<String, Object> filters) {
        String dueFrom = text(filters, "dueFrom");
        String dueTo = text(filters, "dueTo");
        if (!dueFrom.isEmpty() && row.earliestDue.compareTo(dueFrom) < 0) return false;
        if (!dueTo.isEmpty() && !row.earliestDue.isEmpty() && row.earliestDue.compareTo(dueTo) > 0) return false;
        String soNo = text(filters, "soNo");
        if (!soNo.isEmpty() && row.sources.stream().noneMatch(s -> text(s, "销售订单号").contains(soNo))) return false;
        String keyword = text(filters, "keyword");
        if (!keyword.isEmpty()) {
            String hay = (row.code + " " + row.name).toLowerCase();
            if (!hay.contains(keyword.toLowerCase())) return false;
        }
        String supplier = text(filters, "supplier");
        if (!supplier.isEmpty() && !row.supplier.contains(supplier)) return false;
        String status = text(filters, "status");
        if (!status.isEmpty() && !status.equals(row.status)) return false;
        String requestStatus = text(filters, "requestStatus");
        if (!requestStatus.isEmpty() && !requestStatus.equals(row.requestStatus)) return false;
        String sourceType = text(filters, "sourceType");
        if (!sourceType.isEmpty() && !row.sourceTypes.contains(sourceType)) return false;
        return true;
    }

    private Map<String, Row> index(List<Row> rows) {
        Map<String, Row> out = new LinkedHashMap<>();
        for (Row row : rows) out.put(key(row.code, row.spec, row.unit), row);
        return out;
    }

    private void appendChain(List<Map<String, Object>> out, String panelCode, String type,
                             String materialCode, String qtyField) {
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode));
        for (FormData doc : docs) {
            String status = doc.getStatus() == null ? "" : doc.getStatus();
            if (CLOSED.contains(status)) continue;
            double qty = 0;
            Map<String, Object> detail = parse(doc.getDetailData());
            for (Map<String, Object> item : rows(detail)) {
                String code = text(item, "存货编码");
                if (code.isEmpty()) code = text(item, "材料编码");
                if (materialCode.equals(code)) qty += num(item.get(qtyField));
            }
            if (qty <= 0) continue;
            Map<String, Object> head = parse(doc.getData());
            Map<String, Object> one = new LinkedHashMap<>();
            one.put("类型", type);
            one.put("单号", doc.getFormNo());
            one.put("状态", status);
            one.put("单据日期", text(head, "单据日期"));
            one.put("数量", round2(qty));
            out.add(one);
        }
    }

    // ---------- 状态 ----------

    /** 请购进度：只看请购单是否覆盖计划缺口。 */
    static String requestStatusOf(double gap, double requested) {
        if (requested > gap + 0.0001) return "超请购";
        if (gap > 0 && requested >= gap - 0.0001) return "已请购";
        if (requested > 0) return "部分请购";
        return "未请购";
    }

    static String statusOf(double gap, double requested, double ordered, double received) {
        if (gap <= 0) return "已完成";
        if (received > gap + 0.0001) return "超量";
        if (received >= gap - 0.0001) return "已完成";
        if (ordered > 0) return "已采购未到货";
        if (requested >= gap - 0.0001) return "已请购未采购";
        if (requested > 0) return "部分请购";
        return "未请购";
    }

    // ---------- 工具 ----------

    private static Map<String, Object> sourceMap(PuReqSource source) {
        Map<String, Object> one = new LinkedHashMap<>();
        one.put("id", source.getId());
        one.put("计划分析单号", source.getPlanNo());
        one.put("销售订单号", source.getSoNo());
        one.put("客户", source.getCustomer());
        one.put("交期", source.getDueDate());
        one.put("物料编码", source.getMaterialCode());
        one.put("物料名称", source.getMaterialName());
        one.put("规格型号", source.getSpec());
        one.put("单位", source.getUnit());
        one.put("层级", source.getLevel());
        one.put("来源链", source.getPath());
        one.put("毛需求", nz(source.getGrossQty()));
        one.put("现存量", nz(source.getStockQty()));
        one.put("净需求", nz(source.getNetQty()));
        one.put("来源类型", source.getSourceType());
        one.put("建议供应商", source.getSupplier());
        one.put("备注", source.getRemark());
        one.put("状态", source.getStatus());
        one.put("创建人", source.getCreatedBy());
        one.put("创建时间", source.getCreateTime() == null ? "" : source.getCreateTime().toString());
        return one;
    }

    private static String key(String code, String spec, String unit) {
        return (code == null ? "" : code) + "\u0001" + (spec == null ? "" : spec)
                + "\u0001" + (unit == null ? "" : unit);
    }

    private Map<String, Object> parse(String text) {
        if (text == null || text.isBlank()) return Map.of();
        try {
            return json.readValue(text, new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            return Map.of();
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> rows(Map<String, Object> detail) {
        Object value = detail.get("items");
        if (!(value instanceof List<?> list)) return List.of();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object one : list) {
            if (one instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
        }
        return out;
    }

    private static String text(Map<String, Object> map, String... keys) {
        for (String key : keys) {
            Object value = map.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return String.valueOf(value).trim();
        }
        return "";
    }

    private static double num(Object value) {
        if (value == null) return 0;
        try {
            return new BigDecimal(String.valueOf(value).trim().replace(",", "")).doubleValue();
        } catch (Exception e) {
            return 0;
        }
    }

    private static double round2(double value) {
        return BigDecimal.valueOf(value).setScale(2, RoundingMode.HALF_UP).doubleValue();
    }

    private static double nz(BigDecimal value) {
        return value == null ? 0 : value.doubleValue();
    }

    private static String fmt(double value) {
        if (Math.abs(value - Math.rint(value)) < 0.0001) return String.valueOf((long) Math.rint(value));
        return String.valueOf(round2(value));
    }

    /** 汇总行。 */
    static final class Row {
        final String code;
        final String name;
        final String spec;
        final String unit;
        double gap;
        double requested;
        double ordered;
        double received;
        double remaining;
        double notRequestedQty;
        double requestDiff;
        String requestStatus = "未请购";
        String earliestDue = "";
        String supplier = "";
        String status = "未请购";
        final Set<String> sourceTypes = new LinkedHashSet<>();
        final List<Map<String, Object>> sources = new ArrayList<>();

        Row(PuReqSource source) {
            this.code = source.getMaterialCode() == null ? "" : source.getMaterialCode();
            this.name = source.getMaterialName() == null ? "" : source.getMaterialName();
            this.spec = source.getSpec() == null ? "" : source.getSpec();
            this.unit = source.getUnit() == null ? "" : source.getUnit();
        }

        void addSource(PuReqSource source) {
            gap = round2(gap + nz(source.getNetQty()));
            String due = source.getDueDate() == null ? "" : source.getDueDate().trim();
            if (!due.isEmpty() && (earliestDue.isEmpty() || due.compareTo(earliestDue) < 0)) earliestDue = due;
            String sup = source.getSupplier() == null ? "" : source.getSupplier().trim();
            if (!sup.isEmpty() && supplier.isEmpty()) supplier = sup;
            sourceTypes.add(source.getSourceType() == null ? "计划" : source.getSourceType());
            sources.add(sourceMap(source));
        }

        Map<String, Object> toMap() {
            Map<String, Object> one = new LinkedHashMap<>();
            one.put("物料编码", code);
            one.put("物料名称", name);
            one.put("规格型号", spec);
            one.put("单位", unit);
            one.put("缺口合计", gap);
            one.put("已请购量", requested);
            one.put("未请购量", notRequestedQty);
            one.put("请购差异", requestDiff);
            one.put("请购状态", requestStatus);
            one.put("已采购量", ordered);
            one.put("已到货量", received);
            one.put("未完成量", remaining);
            one.put("未到货量", remaining);
            one.put("最早需求日期", earliestDue);
            one.put("建议供应商", supplier);
            one.put("状态", status);
            String source = sourceTypes.contains("计划") && sourceTypes.contains("手工")
                    ? "计划/手工" : sourceTypes.contains("手工") ? "手工" : "计划";
            one.put("来源", source);
            one.put("来源行数", sources.size());
            return one;
        }
    }
}
