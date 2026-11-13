package com.mes.init;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;

/**
 * 初始化落库（P1）：把上传解析出的行写进业务主数据。
 *
 * <p>已支持的模块：组织架构（sys_dept）、存货（INV 单文档 items）、工序（OP 单文档 operations）、
 * 工艺路线（ROUTE 每路线一张单）、BOM（每个产品一张单）。
 *
 * <p>幂等口径：一律按**自然键**判重（部门名 / 存货编码 / 工序编码 / 工艺路线编码 / 产品编码+子件编码），
 * 已存在则跳过并记为 `skipped`，重复上传同一份表不会产生重复行。
 *
 * <p>单文档面板（INV/OP）的行直接合并进那一张单据的 detail JSON；多单据面板（ROUTE/BOM）
 * 每个业务对象一张 form_data，单号直接用业务编码（GY-xxx / 产品编码），便于反查。
 */
@Service
public class InitApplyService {

    private static final Logger log = LoggerFactory.getLogger(InitApplyService.class);
    /** 单文档档案的单号（与运行库现有数据一致：INV=INV、OP=工序） */
    private static final String INV_DOC_NO = "INV";
    private static final String OP_DOC_NO = "工序";
    private static final String ARCHIVE_STATUS = "启用";
    private static final String DOC_STATUS = "已审核";

    private final JdbcTemplate jdbc;
    private final FormDataMapper formMapper;
    private final ObjectMapper json;

    public InitApplyService(JdbcTemplate jdbc, FormDataMapper formMapper, ObjectMapper json) {
        this.jdbc = jdbc;
        this.formMapper = formMapper;
        this.json = json;
    }

    /** 落库结果：逐行明细 + 计数。 */
    public Map<String, Object> apply(InitModule module, List<Map<String, String>> rows, String operator) {
        List<Map<String, Object>> items = switch (module) {
            case DEPT -> applyDept(rows, operator);
            case ITEM -> applyItem(rows, operator);
            case PROCESS -> applyProcess(rows, operator);
            case ROUTING -> applyRouting(rows, operator);
            case BOM -> applyBom(rows, operator);
            case EMPLOYEE, OPENING_STOCK -> throw new IllegalStateException(
                    "模块「" + module.label() + "」的落库在后续阶段实现（当前支持 组织架构/存货/工序/工艺路线/BOM）");
        };
        int inserted = 0;
        int skipped = 0;
        int failed = 0;
        for (Map<String, Object> item : items) {
            switch (String.valueOf(item.get("action"))) {
                case "insert" -> inserted++;
                case "skip" -> skipped++;
                default -> failed++;
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("module", module.code());
        out.put("moduleLabel", module.label());
        out.put("inserted", inserted);
        out.put("skipped", skipped);
        out.put("failed", failed);
        out.put("items", items);
        return out;
    }

    // ───────────────────────── 组织架构 ─────────────────────────

    /** 部门：按「部门名称」判重；上级按模板里的编码在本批内解析，找不到就挂根。 */
    List<Map<String, Object>> applyDept(List<Map<String, String>> rows, String operator) {
        Map<String, Long> idByCode = new LinkedHashMap<>();
        List<Map<String, Object>> items = new ArrayList<>();

        // 第一遍：建部门（记录编码 → id）
        for (Map<String, String> row : rows) {
            String code = value(row, "部门编码");
            String name = value(row, "部门名称");
            if (name.isEmpty()) {
                items.add(item("dept", code, "skip", "部门名称为空，跳过"));
                continue;
            }
            Long existing = jdbc.query("select id from sys_dept where dept_name = ? limit 1",
                    rs -> rs.next() ? rs.getLong(1) : null, name);
            if (existing != null) {
                idByCode.put(code, existing);
                items.add(item("dept", name, "skip", "部门已存在（id=" + existing + "）"));
                continue;
            }
            Long id = jdbc.queryForObject(
                    "insert into sys_dept (parent_id, dept_name, sort, create_time) values (0, ?, ?, now()) returning id",
                    Long.class, name, intOf(row, "排序", 0));
            idByCode.put(code, id);
            items.add(item("dept", name, "insert", "新建部门 id=" + id));
        }

        // 第二遍：挂上级（父编码可能排在后行，所以单独一遍）
        for (Map<String, String> row : rows) {
            String code = value(row, "部门编码");
            String parentCode = value(row, "上级部门编码");
            if (parentCode.isEmpty() || code.isEmpty()) continue;
            Long self = idByCode.get(code);
            Long parent = idByCode.get(parentCode);
            if (self == null || parent == null || self.equals(parent)) continue;
            jdbc.update("update sys_dept set parent_id = ? where id = ?", parent, self);
        }
        return items;
    }

    // ───────────────────────── 存货 ─────────────────────────

    /** 存货：单文档 INV.items，按存货编码判重合并。 */
    List<Map<String, Object>> applyItem(List<Map<String, String>> rows, String operator) {
        FormData doc = archiveDoc("INV", INV_DOC_NO);
        Map<String, Object> detail = parse(doc.getDetailData());
        List<Map<String, Object>> items = rowsOf(detail, "items");
        Set<String> existing = new LinkedHashSet<>();
        for (Map<String, Object> row : items) existing.add(String.valueOf(row.get("存货编码")).trim());

        List<Map<String, Object>> result = new ArrayList<>();
        int seq = items.size();
        for (Map<String, String> row : rows) {
            String code = value(row, "存货编码");
            if (code.isEmpty()) {
                result.add(item("item", "", "skip", "存货编码为空"));
                continue;
            }
            if (!existing.add(code)) {
                result.add(item("item", code, "skip", "存货已存在，跳过"));
                continue;
            }
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("存货编码", code);
            r.put("存货名称", value(row, "存货名称"));
            r.put("规格型号", value(row, "规格型号"));
            r.put("计量单位", value(row, "计量单位"));
            r.put("计价方式", value(row, "计价方式").isEmpty() ? "全月平均" : value(row, "计价方式"));
            r.put("所属类别", value(row, "所属类别"));
            r.put("参考成本", number(value(row, "参考成本"), 0));
            r.put("最新成本", number(value(row, "参考成本"), 0));
            r.put("工艺路线", value(row, "工艺路线"));
            r.put("品牌", "");
            r.put("属性", "");
            r.put("序列号管理", false);
            r.put("停用", false);
            r.put("建档日期", LocalDateTime.now().toLocalDate().toString());
            r.put("备注", "");
            items.add(r);
            seq++;
            result.add(item("item", code, "insert", "新增存货"));
        }
        if (result.stream().anyMatch(r -> "insert".equals(r.get("action")))) {
            detail.put("items", items);
            saveDetail(doc, detail, operator);
        }
        return result;
    }

    // ───────────────────────── 工序 ─────────────────────────

    /** 工序：单文档 OP.operations，按工序编码判重。 */
    List<Map<String, Object>> applyProcess(List<Map<String, String>> rows, String operator) {
        FormData doc = archiveDoc("OP", OP_DOC_NO);
        Map<String, Object> detail = parse(doc.getDetailData());
        List<Map<String, Object>> operations = rowsOf(detail, "operations");
        Set<String> existing = new LinkedHashSet<>();
        for (Map<String, Object> row : operations) existing.add(String.valueOf(row.get("工序编码")).trim());

        List<Map<String, Object>> result = new ArrayList<>();
        int seq = operations.size();
        for (Map<String, String> row : rows) {
            String code = value(row, "工序编码");
            if (code.isEmpty()) {
                result.add(item("process", "", "skip", "工序编码为空"));
                continue;
            }
            if (!existing.add(code)) {
                result.add(item("process", code, "skip", "工序已存在，跳过"));
                continue;
            }
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("编号", "OP-" + String.format("%03d", ++seq));
            r.put("工序编码", code);
            r.put("工序名称", value(row, "工序名称"));
            r.put("默认车间", value(row, "默认车间"));
            r.put("加工方式", value(row, "加工方式").isEmpty() ? "自制" : value(row, "加工方式"));
            r.put("标准合格率%", number(value(row, "标准合格率%"), 100));
            r.put("关键工序", false);
            r.put("按辅单位计价", false);
            r.put("辅单位", "件");
            r.put("换算率", 1);
            r.put("默认工资类型", "计件");
            r.put("计件依据", value(row, "计件依据").isEmpty() ? "合格数量" : value(row, "计件依据"));
            r.put("是否停用", false);
            r.put("备注", "");
            operations.add(r);
            result.add(item("process", code, "insert", "新增工序"));
        }
        if (result.stream().anyMatch(r -> "insert".equals(r.get("action")))) {
            detail.put("operations", operations);
            saveDetail(doc, detail, operator);
        }
        return result;
    }

    // ───────────────────────── 工艺路线 ─────────────────────────

    /** 工艺路线：一个路线编码一张单，明细挂在 processes；按路线编码判重。 */
    List<Map<String, Object>> applyRouting(List<Map<String, String>> rows, String operator) {
        Map<String, List<Map<String, String>>> grouped = new LinkedHashMap<>();
        for (Map<String, String> row : rows) {
            String code = value(row, "工艺路线编码");
            if (code.isEmpty()) continue;
            grouped.computeIfAbsent(code, k -> new ArrayList<>()).add(row);
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map.Entry<String, List<Map<String, String>>> entry : grouped.entrySet()) {
            String code = entry.getKey();
            FormData existing = formDoc("ROUTE", code);
            if (existing == null) existing = routeDocByCode(code);
            if (existing != null) {
                result.add(item("routing", code, "skip", "工艺路线已存在（" + existing.getFormNo() + "），跳过"));
                continue;
            }
            String name = value(entry.getValue().get(0), "工艺路线名称");
            Map<String, Object> head = new LinkedHashMap<>();
            head.put("备注", "工艺路线");
            head.put("工艺路线编码", code);
            head.put("工艺路线名称", name);
            head.put("停用", false);
            List<Map<String, Object>> processes = new ArrayList<>();
            int order = 0;
            for (Map<String, String> row : entry.getValue()) {
                Map<String, Object> p = new LinkedHashMap<>();
                order++;
                int seq = (int) number(value(row, "工序顺序"), order * 10);
                p.put("工序顺序", seq);
                p.put("加工顺序", seq);
                p.put("工序编码", value(row, "工序编码"));
                p.put("工序名称", processNameOf(value(row, "工序编码")));
                p.put("加工方式", "自制");
                p.put("生产车间", "");
                p.put("工资类型", value(row, "工资类型").isEmpty() ? "计件" : value(row, "工资类型"));
                p.put("计件依据", "合格数量");
                p.put("委外供应商", value(row, "委外供应商"));
                p.put("按辅单位计价", false);
                p.put("辅单位", "件");
                p.put("换算率", 1);
                p.put("默认报工数量", 0);
                p.put("关键工序", false);
                processes.add(p);
            }
            insertDoc("ROUTE", code, head, Map.of("processes", processes), operator);
            result.add(item("routing", code, "insert", "新增工艺路线，工序 " + processes.size() + " 道"));
        }
        return result;
    }

    // ───────────────────────── BOM ─────────────────────────

    /** BOM：一个产品一张单，明细挂在 children；按「产品编码 + 子件编码」判重。 */
    List<Map<String, Object>> applyBom(List<Map<String, String>> rows, String operator) {
        Map<String, List<Map<String, String>>> grouped = new LinkedHashMap<>();
        for (Map<String, String> row : rows) {
            String product = value(row, "产品编码");
            if (product.isEmpty()) continue;
            grouped.computeIfAbsent(product, k -> new ArrayList<>()).add(row);
        }
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map.Entry<String, List<Map<String, String>>> entry : grouped.entrySet()) {
            String product = entry.getKey();
            FormData doc = formDoc("BOM", product);
            Map<String, Object> detail = doc == null ? new LinkedHashMap<>() : parse(doc.getDetailData());
            List<Map<String, Object>> children = rowsOf(detail, "children");
            Set<String> existing = new LinkedHashSet<>();
            for (Map<String, Object> c : children) existing.add(String.valueOf(c.get("子件编码")).trim());

            int added = 0;
            for (Map<String, String> row : entry.getValue()) {
                String child = value(row, "子件编码");
                if (child.isEmpty() || !existing.add(child)) {
                    result.add(item("bom", product + "/" + child, "skip",
                            child.isEmpty() ? "子件编码为空" : "该父件下子件已存在，跳过"));
                    continue;
                }
                Map<String, Object> c = new LinkedHashMap<>();
                String route = routeOfProduct(product);
                c.put("物料清单编码", product);
                c.put("父件编码", product);
                c.put("父件名称", productNameOf(product));
                c.put("虚拟件", false);
                c.put("版本号", "V1.0");
                c.put("计量单位", "件");
                c.put("生产数量", number(value(row, "生产数量"), 1));
                c.put("生产车间", "");
                c.put("预入仓库", value(row, "预入仓库"));
                c.put("默认BOM", true);
                c.put("子件编码", child);
                c.put("子件名称", itemNameOf(child));
                c.put("规格型号", itemSpecOf(child));
                c.put("子件计量单位", itemUnitOf(child));
                c.put("定额数量", number(value(row, "定额数量"), 0));
                c.put("损耗率%", number(value(row, "损耗率%"), 0));
                c.put("领料工序", "");
                c.put("领料工序编码", "");
                children.add(c);
                added++;
            }
            if (added > 0) {
                if (doc == null) {
                    Map<String, Object> head = new LinkedHashMap<>();
                    head.put("备注", "物料清单");
                    head.put("产品编码", product);
                    head.put("产品名称", productNameOf(product));
                    head.put("工艺路线编码", routeOfProduct(product));
                    head.put("工艺路线名称", routeNameOf(routeOfProduct(product)));
                    head.put("版本号", "V1.0");
                    insertDoc("BOM", product, head, Map.of("children", children), operator);
                    result.add(item("bom", product, "insert", "新建 BOM，" + added + " 行子件"));
                } else {
                    detail.put("children", children);
                    saveDetail(doc, detail, operator);
                    result.add(item("bom", product, "insert", "追加 " + added + " 行子件"));
                }
            }
        }
        return result;
    }

    // ───────────────────────── 共用 ─────────────────────────

    private FormData archiveDoc(String panelCode, String formNo) {
        FormData doc = formDoc(panelCode, formNo);
        if (doc != null) return doc;
        FormData created = new FormData();
        created.setPanelCode(panelCode);
        created.setFormNo(formNo);
        created.setData("{}");
        created.setDetailData("{}");
        created.setStatus(ARCHIVE_STATUS);
        created.setCreateTime(LocalDateTime.now());
        created.setUpdateTime(LocalDateTime.now());
        formMapper.insert(created);
        return formDoc(panelCode, formNo);
    }

    private FormData formDoc(String panelCode, String formNo) {
        return formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, formNo));
    }

    /** 老数据可能用 ROUT-xxxx 当单号、业务编码在表头里，这里按表头再找一次。 */
    private FormData routeDocByCode(String code) {
        for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "ROUTE"))) {
            if (code.equals(String.valueOf(parse(doc.getData()).getOrDefault("工艺路线编码", "")).trim())) {
                return doc;
            }
        }
        return null;
    }

    private void saveDetail(FormData doc, Map<String, Object> detail, String operator) {
        doc.setDetailData(toJson(detail));
        doc.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(doc);
    }

    private void insertDoc(String panelCode, String formNo, Map<String, Object> head,
                           Map<String, Object> detail, String operator) {
        FormData doc = new FormData();
        doc.setPanelCode(panelCode);
        doc.setFormNo(formNo);
        doc.setData(toJson(head));
        doc.setDetailData(toJson(detail));
        doc.setStatus(DOC_STATUS);
        doc.setCreateBy(operator);
        doc.setCreateTime(LocalDateTime.now());
        doc.setUpdateTime(LocalDateTime.now());
        doc.setAuditBy(operator);
        doc.setAuditTime(LocalDateTime.now());
        formMapper.insert(doc);
        log.info("初始化落库：{} {} 由 {}", panelCode, formNo, operator);
    }

    private String processNameOf(String code) {
        FormData op = formDoc("OP", OP_DOC_NO);
        if (op == null) return "";
        for (Map<String, Object> row : rowsOf(parse(op.getDetailData()), "operations")) {
            if (code.equals(String.valueOf(row.get("工序编码")).trim())) {
                return String.valueOf(row.getOrDefault("工序名称", ""));
            }
        }
        return "";
    }

    private String productNameOf(String code) {
        return itemNameOf(code);
    }

    private String itemNameOf(String code) {
        return itemField(code, "存货名称");
    }

    private String itemSpecOf(String code) {
        return itemField(code, "规格型号");
    }

    private String itemUnitOf(String code) {
        return itemField(code, "计量单位");
    }

    private String itemField(String code, String field) {
        FormData inv = formDoc("INV", INV_DOC_NO);
        if (inv == null) return "";
        for (Map<String, Object> row : rowsOf(parse(inv.getDetailData()), "items")) {
            if (code.equals(String.valueOf(row.get("存货编码")).trim())) {
                return String.valueOf(row.getOrDefault(field, ""));
            }
        }
        return "";
    }

    private String routeOfProduct(String product) {
        FormData inv = formDoc("INV", INV_DOC_NO);
        if (inv == null) return "";
        for (Map<String, Object> row : rowsOf(parse(inv.getDetailData()), "items")) {
            if (product.equals(String.valueOf(row.get("存货编码")).trim())) {
                return String.valueOf(row.getOrDefault("工艺路线", ""));
            }
        }
        return "";
    }

    private String routeNameOf(String code) {
        if (code.isEmpty()) return "";
        for (FormData doc : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "ROUTE"))) {
            Map<String, Object> head = parse(doc.getData());
            if (code.equals(String.valueOf(head.getOrDefault("工艺路线编码", "")).trim())) {
                return String.valueOf(head.getOrDefault("工艺路线名称", ""));
            }
        }
        return "";
    }

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(raw, new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            return new LinkedHashMap<>();
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> rowsOf(Map<String, Object> detail, String key) {
        Object value = detail.get(key);
        if (value instanceof List<?> list) {
            List<Map<String, Object>> out = new ArrayList<>();
            for (Object row : list) {
                if (row instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
            }
            return out;
        }
        return new ArrayList<>();
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("初始化落库序列化失败", e);
        }
    }

    private static Map<String, Object> item(String target, String key, String action, String message) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("target", target);
        out.put("key", key);
        out.put("action", action);
        out.put("message", message);
        return out;
    }

    private static String value(Map<String, String> row, String key) {
        String v = row == null ? null : row.get(key);
        return v == null ? "" : v.trim();
    }

    private static double number(String raw, double fallback) {
        String v = raw == null ? "" : raw.trim();
        if (v.isEmpty()) return fallback;
        try {
            return Double.parseDouble(v);
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    private static int intOf(Map<String, String> row, String key, int fallback) {
        return (int) number(value(row, key), fallback);
    }
}