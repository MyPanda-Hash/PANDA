package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Set;
import java.util.List;
import java.util.Map;

@Service
public class PxService {

    private final PanelConfigMapper panelMapper;
    private final FormDataMapper formMapper;
    private final FormApprovalMapper approvalMapper;
    private final ReportQueryService reportQueryService;
    private final ObjectMapper json = new ObjectMapper();

    public PxService(PanelConfigMapper panelMapper, FormDataMapper formMapper,
                     FormApprovalMapper approvalMapper, ReportQueryService reportQueryService) {
        this.panelMapper = panelMapper;
        this.formMapper = formMapper;
        this.approvalMapper = approvalMapper;
        this.reportQueryService = reportQueryService;
    }

    // ---------- 配置 ----------

    @SuppressWarnings("unchecked")
    private Map<String, Object> loadConfig(String panelCode) {
        PanelConfig pc = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (pc == null) throw new IllegalArgumentException("面板不存在：" + panelCode);
        try {
            Map<String, Object> config = json.readValue(pc.getConfig(), new TypeReference<Map<String, Object>>() {});
            applyRuntimeConfigUpgrades(panelCode, config);
            return config;
        } catch (Exception e) {
            throw new IllegalStateException("面板配置解析失败：" + e.getMessage());
        }
    }

    @SuppressWarnings("unchecked")
    private void applyRuntimeConfigUpgrades(String panelCode, Map<String, Object> config) {
        normalizeFieldDefinitions(config);
        ensureSelectAction(config);
        if ("MANU_ORDER".equals(panelCode)) {
            upgradeDetailReference(config, "products", "产品编码",
                    List.of("存货编码", "存货名称", "规格型号", "所属类别", "品牌", "计量单位", "属性", "停用"),
                    List.of(
                            Map.of("from", "存货名称", "to", "产品名称"),
                            Map.of("from", "规格型号", "to", "规格型号"),
                            Map.of("from", "计量单位", "to", "生产单位")));
        } else if ("SO_ORDER".equals(panelCode)) {
            upgradeDetailReference(config, "items", "存货编码",
                    List.of("存货编码", "存货名称", "规格型号", "品牌", "计量单位", "参考成本", "停用"),
                    List.of(
                            Map.of("from", "存货名称", "to", "存货名称"),
                            Map.of("from", "规格型号", "to", "规格型号"),
                            Map.of("from", "品牌", "to", "存货名称.品牌"),
                            Map.of("from", "计量单位", "to", "销售单位"),
                            Map.of("from", "参考成本", "to", "单价")));
            ensureSelectConfig(config, "QUOTE_ORDER", "选报价单",
                    "仅显示已审核且未中止的报价单，选中后产品明细带入销售订单（对齐 T+ 选单前提）",
                    List.of("单据编号", "单据日期", "客户", "存货名称", "数量", "报价单价", "有效期至"),
                    "items",
                    List.of(Map.of("from", "单据编号", "to", "来源单号")),
                    List.of(
                            Map.of("from", "存货名称", "to", "存货名称"),
                            Map.of("from", "计量单位", "to", "销售单位"),
                            Map.of("from", "数量", "to", "数量"),
                            Map.of("from", "报价单价", "to", "单价")));
        }
        // 存货类参照字段 refMap 互带（编码↔名称 + 规格/单位带出），修复参照选择后字段缺失（2026-08-25）
        upgradeInvPairRef(config, "products", "产品编码", "产品名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "materials", "材料编码", "材料名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "items", "存货编码", "存货名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "items", "材料编码", "材料名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, "items", "产品编码", "产品名称", "规格型号", "计量单位");
        upgradeInvPairRef(config, null, "存货编码", "存货", "规格型号", "计量单位");
    }

    /** 为既有面板注入拉式选单 selectConfig（运行时升级，缺才注入；同时补齐「选单」工具栏动作） */
    @SuppressWarnings("unchecked")
    private void ensureSelectConfig(Map<String, Object> config, String sourcePanel, String title, String tip,
                                    List<String> columns, String detailKey,
                                    List<Map<String, String>> headerMap, List<Map<String, String>> detailMap) {
        Object existing = config.get("selectConfig");
        if (existing instanceof Map<?, ?> && !((Map<?, ?>) existing).isEmpty()) return;
        Map<String, Object> sc = new HashMap<>();
        sc.put("source", sourcePanel);
        sc.put("title", title);
        sc.put("tip", tip);
        sc.put("columns", columns);
        sc.put("detailKey", detailKey);
        sc.put("headerMap", headerMap);
        sc.put("detailMap", detailMap);
        config.put("selectConfig", sc);
        ensureSelectAction(config);
    }

    /**
     * 为存货类参照字段注入 refMap 互带（编码↔名称 + 规格/单位带出）。
     * 修复：参照选择只写 displayField（如只有材料名称没材料编码，或反之）——2026-08-25。
     * tabKey 为 null 时作用于表头 dataSchema.fields（如序列号登记单的 存货/存货编码）。
     */
    @SuppressWarnings("unchecked")
    private void upgradeInvPairRef(Map<String, Object> config, String tabKey,
                                   String codeField, String nameField, String specField, String unitField) {
        List<Map<String, Object>> fields = new ArrayList<>();
        if (tabKey != null) {
            Object detailObj = config.get("detail");
            if (detailObj instanceof Map<?, ?> detail) {
                Object tabsObj = detail.get("tabs");
                if (tabsObj instanceof List<?> tabs) {
                    for (Object t : tabs) {
                        if (t instanceof Map<?, ?> tab && tabKey.equals(tab.get("key"))) {
                            Object f = tab.get("fields");
                            if (f instanceof List<?>) fields.addAll((List<Map<String, Object>>) f);
                        }
                    }
                }
            }
        } else {
            Object schemaObj = config.get("dataSchema");
            if (schemaObj instanceof Map<?, ?> schema) {
                Object f = schema.get("fields");
                if (f instanceof List<?>) fields.addAll((List<Map<String, Object>>) f);
            }
        }
        for (Map<String, Object> field : fields) {
            if (!"参照".equals(field.get("dataType")) || field.containsKey("refMap")) continue;
            String dn = String.valueOf(field.get("dataName"));
            if (codeField.equals(dn)) {
                field.put("refMap", List.of(
                        Map.of("from", "存货名称", "to", nameField),
                        Map.of("from", "规格型号", "to", specField),
                        Map.of("from", "计量单位", "to", unitField)));
            } else if (nameField.equals(dn)) {
                field.put("refMap", List.of(
                        Map.of("from", "存货编码", "to", codeField),
                        Map.of("from", "规格型号", "to", specField),
                        Map.of("from", "计量单位", "to", unitField)));
            }
        }
    }

    /** 旧配置存在 selectConfig 但遗漏工具栏动作时，补齐可用的选单入口。 */
    @SuppressWarnings("unchecked")
    private void ensureSelectAction(Map<String, Object> config) {
        Object selectObject = config.get("selectConfig");
        if (!(selectObject instanceof Map<?, ?> selectConfig) || selectConfig.isEmpty()) return;
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?>)) return;
        Map<String, Object> metadata = (Map<String, Object>) metadataObject;

        List<Map<String, Object>> groups = new ArrayList<>();
        Object groupsObject = metadata.get("buttonGroups");
        if (groupsObject instanceof List<?> values) {
            for (Object value : values) {
                if (value instanceof Map<?, ?>) groups.add((Map<String, Object>) value);
            }
        }
        boolean hasSelectAction = groups.stream()
                .flatMap(group -> {
                    Object actions = group.get("actions");
                    return actions instanceof List<?> list ? list.stream() : java.util.stream.Stream.empty();
                })
                .anyMatch(action -> String.valueOf(action).startsWith("选"));
        if (!hasSelectAction) {
            Object configuredTitle = selectConfig.get("title");
            String candidate = configuredTitle == null ? "选单" : String.valueOf(configuredTitle);
            final String action = candidate.isBlank() || !candidate.startsWith("选") ? "选单" : candidate;
            Map<String, Object> group = new HashMap<>();
            group.put("name", "选单");
            group.put("actions", List.of(action));
            int insertAt = groups.isEmpty() ? 0 : Math.min(1, groups.size());
            groups.add(insertAt, group);
            metadata.put("buttonGroups", groups);

            List<Map<String, Object>> buttons = new ArrayList<>();
            Object buttonsObject = metadata.get("panelButtons");
            if (buttonsObject instanceof List<?> values) {
                for (Object value : values) {
                    if (value instanceof Map<?, ?>) buttons.add((Map<String, Object>) value);
                }
            }
            boolean exists = buttons.stream().anyMatch(button -> action.equals(String.valueOf(button.get("buttonName"))));
            if (!exists) buttons.add(Map.of("buttonName", action));
            metadata.put("panelButtons", buttons);
        }
    }

    /**
     * 展平旧配置中误写的嵌套字段数组。库存面板种子曾生成
     * fields: [公共字段, [扩展字段]]，导致创建表单元数据时发生 ArrayList -> Map 转换异常。
     */
    @SuppressWarnings("unchecked")
    private void normalizeFieldDefinitions(Map<String, Object> config) {
        Object schemaObject = config.get("dataSchema");
        if (!(schemaObject instanceof Map<?, ?>)) return;
        Map<String, Object> schema = (Map<String, Object>) schemaObject;
        schema.put("fields", flattenFieldDefinitions(schema.get("fields")));
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> flattenFieldDefinitions(Object value) {
        List<Map<String, Object>> fields = new ArrayList<>();
        if (!(value instanceof List<?> values)) return fields;
        for (Object item : values) {
            if (item instanceof Map<?, ?>) {
                fields.add((Map<String, Object>) item);
            } else if (item instanceof List<?>) {
                fields.addAll(flattenFieldDefinitions(item));
            }
        }
        return fields;
    }

    @SuppressWarnings("unchecked")
    private void upgradeDetailReference(Map<String, Object> config, String tabKey, String fieldName,
                                        List<String> columns, List<Map<String, String>> mappings) {
        Object detailObj = config.get("detail");
        if (!(detailObj instanceof Map<?, ?> detail)) return;
        Object tabsObj = detail.get("tabs");
        if (!(tabsObj instanceof List<?> tabs)) return;

        for (Object tabObj : tabs) {
            if (!(tabObj instanceof Map<?, ?> tab) || !tabKey.equals(tab.get("key"))) continue;
            Object fieldsObj = tab.get("fields");
            if (!(fieldsObj instanceof List<?> fields)) return;
            for (Object fieldObj : fields) {
                if (!(fieldObj instanceof Map<?, ?> rawField) || !fieldName.equals(rawField.get("dataName"))) continue;
                Map<String, Object> field = (Map<String, Object>) rawField;
                field.put("dataType", "参照");
                field.remove("options");
                field.put("refPanel", "INV");
                field.put("refField", "存货编码");
                field.put("displayField", "存货编码");
                field.put("filter", Map.of("停用", false));
                field.put("refTrigger", "dblclick");
                field.put("refStatuses", List.of("草稿"));
                field.put("refColumns", columns);
                field.put("refMap", mappings);
                return;
            }
        }
    }

    public Map<String, Object> getPanelConfig(String panelCode) {
        return loadConfig(panelCode);
    }

    // ---------- 权限矩阵 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> getPermMatrix(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        List<Map<String, Object>> buttons = (List<Map<String, Object>>) metadata.get("panelButtons");
        List<Map<String, Object>> actions = new ArrayList<>();
        for (Map<String, Object> b : buttons) {
            Map<String, Object> a = new HashMap<>();
            a.put("name", String.valueOf(b.get("buttonName")));
            a.put("visible", true);
            a.put("operatable", true);
            actions.add(a);
        }
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actions);
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        Map<String, Object> out = new HashMap<>();
        out.put("privilege", privilege);
        return out;
    }

    // ---------- 字段 meta ----------

    @SuppressWarnings("unchecked")
    private Map<String, Object> fieldsOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> schema = (Map<String, Object>) cfg.get("dataSchema");
        List<Map<String, Object>> fields = flattenFieldDefinitions(schema == null ? null : schema.get("fields"));
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        Object stateObject = metadata == null ? null : metadata.get("panelState");
        String stateFieldName = "单据状态";
        if (stateObject instanceof Map<?, ?> state) {
            Object configuredName = state.get("dataName");
            if (configuredName != null && !String.valueOf(configuredName).isBlank()) {
                stateFieldName = String.valueOf(configuredName);
            }
        }
        Map<String, Object> out = new HashMap<>();
        out.put("fields", fields);
        out.put("detail", cfg.get("detail"));
        out.put("stateFieldName", stateFieldName);
        return out;
    }

    private List<Map<String, Object>> buildMeta(String panelCode) {
        List<Map<String, Object>> meta = new ArrayList<>();
        List<Map<String, Object>> fields = (List<Map<String, Object>>) fieldsOf(panelCode).get("fields");
        for (Map<String, Object> f : fields) {
            Map<String, Object> m = new HashMap<>();
            m.put("code", String.valueOf(f.get("dataName")));
            m.put("name", String.valueOf(f.get("dataName")));
            m.put("dataType", f.getOrDefault("dataType", "文本"));
            m.put("isNotNull", Boolean.TRUE.equals(f.get("isRequired")));
            m.put("defaultValue", f.getOrDefault("defaultValue", ""));
            if (f.containsKey("options")) m.put("options", f.get("options"));
            // 参照字段信息（2026-08-20 补：对齐前端 mock buildMeta 的 ref 结构——弹窗拉取面板数据勾选导入）
            if ("参照".equals(f.get("dataType")) && f.get("refPanel") != null) {
                Map<String, Object> ref = new HashMap<>();
                ref.put("panel", f.get("refPanel"));
                ref.put("field", f.get("refField"));
                ref.put("display", f.get("displayField"));
                ref.put("filter", f.get("filter"));
                ref.put("map", f.get("refMap"));
                ref.put("multi", Boolean.TRUE.equals(f.get("refMulti")));
                ref.put("columns", f.get("refColumns"));
                m.put("ref", ref);
            }
            meta.add(m);
        }
        return meta;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> fieldPrivileges() {
        List<Map<String, Object>> fps = new ArrayList<>();
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("fieldPrivileges", fps);
        return privilege;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> actionPrivileges(String panelCode, String formPage) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        Object pageDtoObject = metadata == null ? null : metadata.get("panelPageDto");
        if (!(pageDtoObject instanceof Map<?, ?>)) return new ArrayList<>();
        Map<String, Object> pageDto = (Map<String, Object>) pageDtoObject;
        Object pagesObject = pageDto.get("formPages".equals(formPage) ? "formPages" : "tablePages");
        if (!(pagesObject instanceof List<?> pages) || pages.isEmpty() || !(pages.get(0) instanceof Map<?, ?>)) {
            return new ArrayList<>();
        }
        String key = "formPages".equals(formPage) ? "bottomOperationBarBtn" : "topBarBtn";
        Object buttonsObject = ((Map<String, Object>) pages.get(0)).get(key);
        if (!(buttonsObject instanceof List<?> btns)) return new ArrayList<>();
        List<Map<String, Object>> actions = new ArrayList<>();
        for (Object button : btns) {
            if (!(button instanceof Map<?, ?>)) continue;
            Map<String, Object> b = (Map<String, Object>) button;
            Map<String, Object> a = new HashMap<>();
            a.put("name", String.valueOf(b.get("buttonName")));
            a.put("visible", true);
            a.put("operatable", true);
            actions.add(a);
        }
        return actions;
    }

    @SuppressWarnings("unchecked")
    private Object buttonGroupsOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Map<String, Object> metadata = (Map<String, Object>) cfg.get("metadata");
        return metadata.get("buttonGroups");
    }

    // ---------- 表单描述器 / 新建元数据 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> getNewFormPermMatrix(String panelCode, String operationName) {
        Map<String, Object> stateField = fieldsOf(panelCode);
        String stateName = (String) stateField.get("stateFieldName");
        String af = autoCodeFieldOf(panelCode);
        Map<String, Object> data = new HashMap<>();
        data.put(stateName, "草稿");
        for (Map<String, Object> f : (List<Map<String, Object>>) stateField.get("fields")) {
            Object dv = f.get("defaultValue");
            if (dv != null && !"".equals(String.valueOf(dv))) {
                data.putIfAbsent(String.valueOf(f.get("dataName")), dv);
            }
        }
        // T+ 单据：单据日期默认系统登录日期（所有面板新建即填，保证草稿数据完整可见）
        if (!data.containsKey("单据日期")) data.put("单据日期", LocalDate.now().toString());
        // 自动编码字段（autoCodeField，如工序汇报单「单据编号」）预填单号，表单页可显示
        if (af != null && !af.isBlank() && !data.containsKey(af)) data.put(af, generateFormNo(panelCode));
        // T+ 锭号 = 单据编号（自动预览单号）
        if (!data.containsKey("锭号")) data.put("锭号", generateFormNo(panelCode));
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actionPrivileges(panelCode, "formPages"));
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        Map<String, Object> out = new HashMap<>();
        out.put("data", data);
        out.put("meta", buildMeta(panelCode));
        out.put("privilege", privilege);
        out.put("detail", stateField.get("detail"));
        out.put("buttonGroups", buttonGroupsOf(panelCode));
        out.put("panelName", panelNameOf(panelCode));
        out.put("selectConfig", selectConfigOf(panelCode));
        return out;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> getFormDescriptor(String panelCode, String code) {
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, code));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + code);
        Map<String, Object> data = parseData(fd.getData());
        data.put("编号", fd.getFormNo());
        data.put("单据状态", fd.getStatus());
        data.put("创建时间", fd.getCreateTime());
        data.put("更新时间", fd.getUpdateTime());
        data.put("发起人编号", fd.getCreateBy());
        Map<String, Object> out = new HashMap<>();
        out.put("data", data);
        out.put("meta", buildMeta(panelCode));
        Map<String, Object> privilege = new HashMap<>();
        privilege.put("actionPrivileges", actionPrivileges(panelCode, "formPages"));
        privilege.put("fieldPrivileges", new ArrayList<>());
        privilege.put("groupPrivileges", new ArrayList<>());
        out.put("privilege", privilege);
        out.put("detail", fieldsOf(panelCode).get("detail"));
        out.put("detailData", parseData(fd.getDetailData()));
        out.put("buttonGroups", buttonGroupsOf(panelCode));
        out.put("panelName", panelNameOf(panelCode));
        out.put("selectConfig", selectConfigOf(panelCode));
        return out;
    }

    private String panelNameOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object name = ((Map<String, Object>) cfg.get("metadata")).get("panelName");
        return name == null ? "表单" : String.valueOf(name);
    }

    @SuppressWarnings("unchecked")
    private Object selectConfigOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object sc = cfg.get("selectConfig");
        return sc == null ? new HashMap<>() : sc;
    }

    // ---------- 列表 ----------

    @SuppressWarnings("unchecked")
    public Map<String, Object> queryFormDataList(String panelCode, String keyword,
                                                 Map<String, Object> condition, int pageNo, int pageSize) {
        if (ReportPanelRegistry.isReport(panelCode)) {
            return reportQueryService.query(panelCode, keyword, condition, pageNo, pageSize);
        }
        // 物料清单查询面板（BOM_FWD 正向 / BOM_REV 反向）：从 BOM 面板 form_data 的 children 明细展开为展平行
        if ("BOM_FWD".equals(panelCode) || "BOM_REV".equals(panelCode)) {
            return queryBomFlatten(panelCode, keyword, condition);
        }
        // 采购需求分析（PU_REQ_ANALYSIS）：聚合生产加工单材料需求 - 现存量 - 已请购 - 已出库 = 建议请购数量
        if ("PU_REQ_ANALYSIS".equals(panelCode)) {
            return queryPurchaseReqAnalysis(keyword, condition);
        }
        // 序列号状况表（SERIAL_STATUS）/序列号跟踪表（SERIAL_TRACE）：从 SERIAL_NO 面板明细展平查询
        if ("SERIAL_STATUS".equals(panelCode) || "SERIAL_TRACE".equals(panelCode)) {
            return querySerialFlatten(panelCode, keyword, condition);
        }
        LambdaQueryWrapper<FormData> qw = new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .orderByDesc(FormData::getCreateTime)
                .orderByDesc(FormData::getId);
        List<FormData> all = formMapper.selectList(qw);
        // 单据级行（一单一行，单号不重复）：明细挂在 detail 对象，key 与 detail.tabs 一致。
        // 对齐 docs/页面开发规范.md 数据契约；前端渲染器/选单弹窗按此结构消费。
        List<Map<String, Object>> flat = new ArrayList<>();
        for (FormData fd : all) {
            Map<String, Object> head = parseData(fd.getData());
            head.put("编号", fd.getFormNo());
            head.put("单据状态", fd.getStatus());
            head.put("创建时间", fd.getCreateTime());
            head.put("更新时间", fd.getUpdateTime());
            head.put("发起人编号", fd.getCreateBy());
            Map<String, Object> detail = parseData(fd.getDetailData());
            if (!detail.isEmpty()) head.put("detail", detail);
            flat.add(head);
        }
        // 条件过滤（仅标量字段参与比较，detail 对象跳过）
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    // 缺键（null）：该行不匹配条件（修复：缺 单据日期/单据编号 的行不再被查询条件漏过）
                    if (rv == null) { hit = false; break; }
                    if (rv instanceof Map || rv instanceof List) continue;
                    if (!String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = false;
                for (Object v : row.values()) {
                    if (v != null && !(v instanceof Map) && !(v instanceof List)
                            && String.valueOf(v).contains(keyword)) { hit = true; break; }
                }
            }
            if (hit) rows.add(row);
        }
        int from = Math.min((pageNo - 1) * pageSize, rows.size());
        int to = Math.min(from + pageSize, rows.size());
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", rows.size());
        out.put("list", rows.subList(from, to));
        return out;
    }

    /**
     * 物料清单查询面板数据：把 BOM 面板（panelCode=BOM）form_data 的 children 明细
     * 展开为「父件-子件」展平行，供 BOM_FWD（正向）/BOM_REV（反向）查询面板使用。
     * 数据量小，不参与分页截断（前端父件表格需全量去重）。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> queryBomFlatten(String panelCode, String keyword, Map<String, Object> condition) {
        List<FormData> boms = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "BOM"));
        List<Map<String, Object>> flat = new ArrayList<>();
        for (FormData fd : boms) {
            Map<String, Object> detail = parseData(fd.getDetailData());
            Object children = detail.get("children");
            if (!(children instanceof List)) continue;
            for (Object o : (List<?>) children) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> row = new HashMap<>((Map<String, Object>) o);
                row.put("单据编号", fd.getFormNo());
                flat.add(row);
            }
        }
        // 条件过滤（父件编码/子件编码等标量字段）
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || !String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = false;
                for (Object v : row.values()) {
                    if (v != null && String.valueOf(v).contains(keyword)) { hit = true; break; }
                }
            }
            if (hit) rows.add(row);
        }
        // 排序：正向按父件、反向按子件（保证前端分组去重顺序稳定）
        String sortKey = "BOM_REV".equals(panelCode) ? "子件编码" : "父件编码";
        rows.sort((a, b) -> String.valueOf(a.get(sortKey)).compareTo(String.valueOf(b.get(sortKey))));
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", rows.size());
        out.put("list", rows);
        return out;
    }

    /**
     * 采购需求分析（对齐真实 T+ 生产管理相关单据「采购需求分析」）：
     * 遍历已审核/生产中的生产加工单 materials 明细，逐材料汇总：
     * 需用数量（计划数量） - 现存量 - 已请购数量（PU_REQ items 数量合计） - 已出库数量（MATERIAL_OUT items 数量合计）
     * = 建议请购数量；已全部满足的材料行不输出。结果按材料编码分组聚合。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> queryPurchaseReqAnalysis(String keyword, Map<String, Object> condition) {
        Map<String, Map<String, Object>> agg = new java.util.LinkedHashMap<>();
        List<FormData> mos = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .in(FormData::getStatus, List.of("已审核", "生产中")));
        for (FormData mo : mos) {
            Map<String, Object> head = parseData(mo.getData());
            Map<String, Object> dm = parseData(mo.getDetailData());
            Object mats = dm.get("materials");
            if (!(mats instanceof List)) continue;
            for (Object o : (List<?>) mats) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> m = (Map<String, Object>) o;
                String code = String.valueOf(m.getOrDefault("材料编码", ""));
                String name = String.valueOf(m.getOrDefault("材料名称", ""));
                if (code.isEmpty() && name.isEmpty()) continue;
                String key = code.isEmpty() ? name : code;
                Map<String, Object> row = agg.computeIfAbsent(key, k -> {
                    Map<String, Object> r = new HashMap<>();
                    r.put("材料编码", code);
                    r.put("材料名称", name);
                    r.put("规格型号", m.getOrDefault("规格型号", ""));
                    r.put("计量单位", m.getOrDefault("计量单位", "kg"));
                    r.put("需用数量", 0.0);
                    r.put("现存量", 0.0);
                    r.put("已请购数量", 0.0);
                    r.put("已出库数量", 0.0);
                    r.put("建议请购数量", 0.0);
                    r.put("需求加工单", new java.util.LinkedHashSet<>());
                    return r;
                });
                double need = num(m.getOrDefault("计划数量", m.getOrDefault("需用数量", 0)));
                row.put("需用数量", num(row.get("需用数量")) + need);
                row.put("现存量", Math.max(num(row.get("现存量")), num(m.getOrDefault("现存量", 0))));
                if (!"".equals(String.valueOf(head.getOrDefault("单据编号", "")))) {
                    ((java.util.Set<String>) row.get("需求加工单")).add(String.valueOf(head.get("单据编号")));
                }
            }
        }
        // 已请购：PU_REQ items 数量合计（按材料名称/编码匹配）
        sumDetailQty("PU_REQ", "items", "已请购数量", agg, "存货名称", "存货编码", "数量");
        // 已出库：MATERIAL_OUT items 数量合计
        sumDetailQty("MATERIAL_OUT", "items", "已出库数量", agg, "材料名称", "材料编码", "数量");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : agg.values()) {
            double need = num(row.get("需用数量"));
            double stock = num(row.get("现存量"));
            double purchased = num(row.get("已请购数量"));
            double issued = num(row.get("已出库数量"));
            double suggest = Math.max(0, Math.round((need - stock - purchased - issued) * 100) / 100.0);
            if (suggest <= 0) continue;
            row.put("建议请购数量", suggest);
            Set<String> src = (Set<String>) row.remove("需求加工单");
            row.put("需求来源", String.join("、", src));
            rows.add(row);
        }
        // 条件过滤（材料名称/材料编码/关键字）
        List<Map<String, Object>> hit = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            boolean ok = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || !String.valueOf(rv).contains(String.valueOf(v))) { ok = false; break; }
                }
            }
            if (ok && keyword != null && !keyword.isBlank()) {
                ok = String.valueOf(row.get("材料名称")).contains(keyword)
                        || String.valueOf(row.get("材料编码")).contains(keyword);
            }
            if (ok) hit.add(row);
        }
        hit.sort((a, b) -> String.valueOf(a.get("材料编码")).compareTo(String.valueOf(b.get("材料编码"))));
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", hit.size());
        out.put("list", hit);
        return out;
    }

    /**
     * 序列号状况表（SERIAL_STATUS）/序列号跟踪表（SERIAL_TRACE）（对齐真实 T+ 序列号管理 SN 模块）：
     * 从 SERIAL_NO 面板 form_data 的 items 明细展平为序列号行；状况表按序列号状态（在库/已出库）输出，
     * 跟踪表携带入库单号/出库单号/出入库日期供追溯。
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> querySerialFlatten(String panelCode, String keyword, Map<String, Object> condition) {
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SERIAL_NO")
                .orderByDesc(FormData::getCreateTime));
        List<Map<String, Object>> flat = new ArrayList<>();
        for (FormData fd : docs) {
            Map<String, Object> head = parseData(fd.getData());
            Map<String, Object> dm = parseData(fd.getDetailData());
            Object items = dm.get("items");
            if (!(items instanceof List)) continue;
            String status = fd.getStatus() == null ? "" : fd.getStatus();
            for (Object o : (List<?>) items) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> row = new HashMap<>((Map<String, Object>) o);
                row.put("登记单号", fd.getFormNo());
                row.put("单据状态", status);
                row.putIfAbsent("存货编码", head.getOrDefault("存货编码", ""));
                row.putIfAbsent("存货", head.getOrDefault("存货", ""));
                row.putIfAbsent("规格型号", head.getOrDefault("规格型号", ""));
                row.putIfAbsent("计量单位", head.getOrDefault("计量单位", ""));
                row.putIfAbsent("仓库", head.getOrDefault("仓库", ""));
                row.putIfAbsent("入库单号", head.getOrDefault("入库单号", ""));
                row.putIfAbsent("出库单号", head.getOrDefault("出库单号", ""));
                flat.add(row);
            }
        }
        // 状况表：仅展示在库（未出库）序列号；跟踪表：全部（携带出入库追溯信息）
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> row : flat) {
            String snStatus = String.valueOf(row.getOrDefault("状态", "在库"));
            if ("SERIAL_STATUS".equals(panelCode) && !"在库".equals(snStatus)) continue;
            boolean hit = true;
            if (condition != null) {
                for (Map.Entry<String, Object> e : condition.entrySet()) {
                    Object v = e.getValue();
                    if (v == null || "".equals(String.valueOf(v))) continue;
                    Object rv = row.get(e.getKey());
                    if (rv == null || !String.valueOf(rv).contains(String.valueOf(v))) { hit = false; break; }
                }
            }
            if (hit && keyword != null && !keyword.isBlank()) {
                hit = row.values().stream().anyMatch(value -> value != null
                        && String.valueOf(value).contains(keyword));
            }
            if (hit) rows.add(row);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("totalSize", rows.size());
        out.put("list", rows);
        return out;
    }

    /** 累加某面板明细行某数量字段到聚合行（材料编码优先，其次材料名称） */
    @SuppressWarnings("unchecked")
    private void sumDetailQty(String panelCode, String tabKey, String targetKey,
                              Map<String, Map<String, Object>> agg, String nameField, String codeField, String qtyField) {
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode));
        for (FormData fd : docs) {
            Map<String, Object> dm = parseData(fd.getDetailData());
            Object items = dm.get(tabKey);
            if (!(items instanceof List)) continue;
            for (Object o : (List<?>) items) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> it = (Map<String, Object>) o;
                String code = String.valueOf(it.getOrDefault(codeField, ""));
                String name = String.valueOf(it.getOrDefault(nameField, ""));
                String key = code.isEmpty() ? name : code;
                Map<String, Object> row = agg.get(key);
                if (row == null) {
                    // 名称回退匹配：无编码的明细行按名称聚合
                    row = agg.get(name);
                    if (row == null) continue;
                }
                row.put(targetKey, num(row.get(targetKey)) + num(it.getOrDefault(qtyField, 0)));
            }
        }
    }

    // ---------- 按钮（业务流转） ----------

    @SuppressWarnings("unchecked")
    @Transactional
    public Map<String, Object> callButton(String panelCode, String buttonName,
                                          Map<String, Object> formData, Map<String, Object> buttonParam) {
        switch (buttonName) {
            case "刷新":
                return new HashMap<>();
            case "新增流程":
                return create(panelCode, formData, null);
            case "保存":
            case "保存新增":
            case "保存为草稿":
            case "提交":
                return save(panelCode, formData, null);
            case "删除": {
                Object no = formData.get("编号");
                if (no == null) throw new IllegalArgumentException("缺少表单编号");
                delete(panelCode, String.valueOf(no));
                return new HashMap<>();
            }
            case "审核":
                return changeStatus(panelCode, formData, "审核");
            case "弃审":
                return changeStatus(panelCode, formData, "弃审");
            case "关闭":
                return changeStatus(panelCode, formData, "关闭");
            case "手工完工":
                return changeStatus(panelCode, formData, "手工完工");
            case "中止":
                return changeStatus(panelCode, formData, "中止");
            case "整单中止":
                return changeStatus(panelCode, formData, "中止");
            case "取消中止":
                return changeStatus(panelCode, formData, "取消中止");
            case "生成生产加工单":   // 推式生单：销售订单 → 生产加工单（对齐真实 T+「生单-生成生产加工单」）
                return createMoFromSo(panelCode, formData);
            case "生成工序汇报单（自制汇报）":   // 推式生单：生产加工单 → 工序汇报单（拉取加工单工序明细）
                return createProcReportFromMo(panelCode, formData);
            case "生成采购订单":       // 推式生单：请购单 → 采购订单
                return createPuOrderFromReq(panelCode, formData);
            case "生成采购入库单":     // 推式生单：采购订单 → 采购入库单
                return createPurchaseInFromPo(panelCode, formData);
            case "生成进货单":         // 推式生单：采购订单 → 进货单（对齐真实 T+ 采购订单「生单-生成进货单」）
                return createPuInFromPo(panelCode, formData);
            case "生成产成品入库单":   // 推式生单：生产加工单 → 产成品入库单
                return createFinishInFromMo(panelCode, formData);
            case "生成材料出库单":     // 推式生单：领料申请单/生产加工单/调拨单 → 材料出库单
                return createMaterialOut(panelCode, formData);
            case "生成调拨单":         // 推式生单：领料申请单 → 调拨单（对齐真实 T+ 领料申请单「生单-生成调拨单」）
            case "生成调拨单(分单)":
                return createTransferFromMaterialReq(panelCode, formData);
            case "生成委外发料单":     // 推式生单：委外加工单/领料申请单 → 委外发料单
            case "生成委外发料单(分单)":
                return createOutsourceIssue(panelCode, formData);
            case "生成委外入库单":     // 推式生单：委外加工单 → 委外入库单
                return createOutsourceInFromOrder(panelCode, formData);
            case "生成委外加工费用单": // 推式生单：委外加工单 → 委外加工费用单
                return createOutsourceFeeFromOrder(panelCode, formData);
            case "生成销售订单":       // 推式生单：报价单 → 销售订单（对齐真实 T+ 报价单「生单-生成销售订单」）
            case "生成销售订单(普通销售)":
                return createSaleOrderFromQuote(panelCode, formData);
            case "生成销售发票":       // 推式生单：销货单 → 销售发票（对齐真实 T+ 销货单「生单-生成销售发票」）
            case "生成销售发票(普通销售)":
            case "生成销售发票(销售退货)":
                return createSaleInvoiceFromSaleInv(panelCode, formData);
            case "生成销售费用分摊单": // 推式生单：费用单 → 销售费用分摊单
                return createSaleCostAllocFromExpense(panelCode, formData);
            case "生成采购发票":       // 推式生单：进货单/采购订单 → 采购发票（对齐真实 T+ 进货单/采购订单「生单-生成采购发票」）
            case "生成采购发票（普通采购）":
            case "生成采购发票（采购退货）":
                return createPuInvoice(panelCode, formData);
            case "生成采购费用分摊单": // 推式生单：费用单（采购费用）→ 采购费用分摊单
                return createPuCostAllocFromExpense(panelCode, formData);
            case "生成销售出库单":     // 推式生单：销售订单 → 销售出库单
            case "生成销售出库单(普通销售)":
                return createSaleOutFromSo(panelCode, formData);
            // 审批流
            case "提交审批":
                return submitApproval(panelCode, formData);
            case "审批通过":
                return approveApproval(panelCode, formData);
            case "审批驳回":
                return rejectApproval(panelCode, formData);
            case "审批情况":
                return approvalHistory(panelCode, formData);
            default:
                throw new IllegalStateException("未定义按钮规则：" + buttonName + "（可在 PxService 扩展）");
        }
    }

    /**
     * 推式生单：销售订单（SO_ORDER）→ 生成生产加工单（MANU_ORDER）草稿
     * 字段对应：单据日期→合同号、单据编号→销售订单号、客户/客户编码→客户/客户编码、
     *           结算客户→生产订单客户、业务员→负责人、预计交货日期→预完工日；
     *           明细行→产成品明细（存货编码→产品编码、存货名称→产品名称、销售单位→生产单位）
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createMoFromSo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData so = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER")
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (so == null) throw new IllegalArgumentException("销售订单不存在：" + no);
        if (!"已审核".equals(so.getStatus())) throw new IllegalStateException("仅已审核销售订单可生成生产加工单");

        Map<String, Object> head = parseData(so.getData());
        Map<String, Object> detailMap = parseData(so.getDetailData());
        Object itemsObj = detailMap.get("items");
        List<Map<String, Object>> items = itemsObj instanceof List ? (List<Map<String, Object>>) itemsObj : new ArrayList<>();

        String newNo = generateFormNo("MANU_ORDER");
        Map<String, Object> moData = new HashMap<>();
        moData.put("合同号", head.getOrDefault("单据日期", ""));
        // 锭号=单据编号：审批面板草稿不预填，提交审核时自动填写（ensureAuditStamp）
        moData.put("批号", "正常");
        moData.put("生产车间", "熔铸车间");
        moData.put("预开工日", LocalDate.now().toString());
        moData.put("预完工日", head.getOrDefault("预计交货日期", ""));
        moData.put("销售订单号", head.getOrDefault("单据编号", ""));
        moData.put("客户编码", head.getOrDefault("客户编码", ""));
        moData.put("客户", head.getOrDefault("客户", ""));
        moData.put("测试程序", "光谱分析");
        moData.put("生产订单客户", head.getOrDefault("结算客户", ""));
        moData.put("机构", "总部");
        moData.put("重量", 0);
        moData.put("开工日期", "");
        moData.put("完工日期", "");
        moData.put("启用派工", false);
        moData.put("自动转移", false);
        moData.put("产品自动添加到材料", false);
        moData.put("是否手工修改单据编码", false);
        moData.put("外部单据号", "");
        moData.put("负责人", head.getOrDefault("业务员", ""));
        moData.put("启用领料申请", false);
        moData.put("对方仓库", "");

        List<Map<String, Object>> products = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> p = new HashMap<>();
            p.put("生产类型", "自制");
            p.put("产品编码", it.getOrDefault("存货编码", ""));
            p.put("存货图片", "");
            p.put("产品名称", it.getOrDefault("存货名称", ""));
            p.put("规格型号", it.getOrDefault("规格型号", ""));
            p.put("型号", "");
            p.put("适用BOM", "BOM-001");
            p.put("BOM展开方式", "单阶");
            p.put("生产单位", it.getOrDefault("销售单位", "件"));
            p.put("数量", it.getOrDefault("数量", 0));
            p.put("齐套数量(主)", 0);
            p.put("累计汇报套数(工序单位)", 0);
            p.put("可用量", 0);
            p.put("可用量说明", "");
            p.put("现存量", it.getOrDefault("现存量", 0));
            p.put("现存量说明", "");
            p.put("产品字符公用自定义项1", "");
            p.put("图号", "");
            p.put("单重", 0);
            p.put("总重", 0);
            p.put("需求令号", head.getOrDefault("单据编号", ""));
            products.add(p);
        }
        // 生成材料明细：按产成品的存货 BOM（INV 类别单据物品 _bom）自动带出
        List<Map<String, Object>> materials = new ArrayList<>();
        Map<String, List<Map<String, Object>>> bomByItem = new HashMap<>();
        for (FormData idoc : formMapper.selectList(new LambdaQueryWrapper<FormData>().eq(FormData::getPanelCode, "INV"))) {
            Map<String, Object> idetail = parseData(idoc.getDetailData());
            Object iitems = idetail.get("items");
            if (iitems instanceof List) {
                for (Object o : (List<?>) iitems) {
                    if (!(o instanceof Map)) continue;
                    Map<String, Object> it = (Map<String, Object>) o;
                    Object bomObj = it.get("_bom");
                    if (bomObj == null || String.valueOf(bomObj).isBlank()) continue;
                    try {
                        List<Map<String, Object>> bom = json.readValue(String.valueOf(bomObj),
                                new TypeReference<List<Map<String, Object>>>() {});
                        bomByItem.put(String.valueOf(it.get("存货编码")), bom);
                    } catch (Exception ignore) {}
                }
            }
        }
        Set<String> matKeys = new HashSet<>();
        for (Map<String, Object> p : products) {
            String pcode = String.valueOf(p.getOrDefault("产品编码", ""));
            List<Map<String, Object>> bom = bomByItem.get(pcode);
            if (bom == null) continue;
            for (Map<String, Object> b : bom) {
                String mcode = String.valueOf(b.getOrDefault("材料编码", ""));
                if (mcode.isEmpty() || !matKeys.add(pcode + "|" + mcode)) continue;
                Map<String, Object> m = new HashMap<>();
                m.put("材料编码", mcode);
                m.put("材料名称", b.getOrDefault("材料名称", ""));
                m.put("规格型号", b.getOrDefault("规格型号", ""));
                m.put("计量单位", b.getOrDefault("计量单位", "kg"));
                m.put("定额需用数量", b.getOrDefault("定额需用数量", 0));
                m.put("损耗率%", b.getOrDefault("损耗率%", 0));
                m.put("子件BOM", pcode);
                m.put("预出仓库", "原料仓");
                m.put("材料倒冲方式", "按定额倒冲");
                m.put("领料工序", "下料");
                m.put("允许循环", false);
                m.put("行中止", false);
                m.put("定额生产数量", 1);
                m.put("需用数量", 0);
                m.put("损耗数量", 0);
                m.put("计划数量", 0);
                m.put("累计领用数量", 0);
                m.put("可用量", 0);
                m.put("可用量说明", "");
                m.put("现存量", 0);
                m.put("现存量说明", "");
                m.put("单重", 0);
                m.put("总重", 0);
                m.put("存货图片", "");
                materials.add(m);
            }
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("products", products);
        detail.put("materials", materials);
        detail.put("processes", defaultProcesses(products, String.valueOf(moData.getOrDefault("生产车间", "熔铸车间"))));

        // 新建兜底：单据日期=当天 + 锭号=form_no（修复：销售订单生单生成的加工单锭号缺失，必填校验无法保存草稿）
        fillNewDefaults("MANU_ORDER", moData, newNo);

        FormData mo = new FormData();
        mo.setPanelCode("MANU_ORDER");
        mo.setFormNo(newNo);
        mo.setData(toJson(moData));
        mo.setDetailData(toJson(detail));
        mo.setStatus("草稿");
        mo.setCreateBy("admin");
        mo.setCreateTime(LocalDateTime.now());
        mo.setUpdateTime(LocalDateTime.now());
        formMapper.insert(mo);

        Map<String, Object> out = new HashMap<>();
        out.put("编号", newNo);
        out.put("单据状态", "草稿");
        out.put("gotoPanel", "MANU_ORDER");
        return out;
    }
    /**
     * 推式生单：生产加工单（MANU_ORDER）→ 工序汇报单（PROCESS_REPORT）
     * 表头：加工单号/生产车间/产品/客户/销售订单号；明细 = 加工单工序明细（工序编码/车间/工作中心/设备/班组/工人/计划数量→报工数量/工价）
     */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createProcReportFromMo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData mo = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER")
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (mo == null) throw new IllegalArgumentException("生产加工单不存在：" + no);
        String moStatus = mo.getStatus();
        if (!"已审核".equals(moStatus) && !"生产中".equals(moStatus)) {
            throw new IllegalStateException("仅已审核/生产中的生产加工单可生成工序汇报单");
        }

        Map<String, Object> head = parseData(mo.getData());
        Map<String, Object> detailMap = parseData(mo.getDetailData());
        List<Map<String, Object>> products = detailMap.get("products") instanceof List
                ? (List<Map<String, Object>>) detailMap.get("products") : new ArrayList<>();
        List<Map<String, Object>> procs = detailMap.get("processes") instanceof List
                ? (List<Map<String, Object>>) detailMap.get("processes") : new ArrayList<>();
        if (products.isEmpty()) throw new IllegalStateException("生产加工单无产成品明细：" + no);
        // 工序兜底：加工单无工序明细时按默认 3 道工序生成（下料/机加工/检验）
        if (procs.isEmpty()) {
            procs = defaultProcesses(products, String.valueOf(head.getOrDefault("生产车间", "熔铸车间")));
        }
        Map<String, Object> p0 = products.get(0);

        String newNo = generateFormNo("PROCESS_REPORT");
        Map<String, Object> prData = new HashMap<>();
        // 单据日期/单据编号：审批面板草稿不填，提交审核时自动填写（ensureAuditStamp）
        prData.put("业务类型", "工序汇报");
        prData.put("加工单号", String.valueOf(no));
        prData.put("生产车间", head.getOrDefault("生产车间", ""));
        prData.put("产品编码", p0.getOrDefault("产品编码", ""));
        prData.put("产品名称", p0.getOrDefault("产品名称", ""));
        prData.put("规格型号", p0.getOrDefault("规格型号", ""));
        prData.put("销售订单号", head.getOrDefault("销售订单号", ""));
        prData.put("客户", head.getOrDefault("客户", ""));
        prData.put("匹配来源单号", String.valueOf(no));
        // 表头带出（部门=加工单车间即部门，测试程序=加工单表头）；
        // 单据编号/单据日期：审批面板草稿不填，提交审核时自动填写（ensureAuditStamp）
        prData.put("部门", head.getOrDefault("生产车间", ""));
        prData.put("测试程序", head.getOrDefault("测试程序", ""));

        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> pr : procs) {
            Map<String, Object> it = new HashMap<>();
            it.put("加工单号", String.valueOf(no));
            it.put("产品编码", p0.getOrDefault("产品编码", ""));
            it.put("产品名称", p0.getOrDefault("产品名称", ""));
            it.put("规格型号", p0.getOrDefault("规格型号", ""));
            it.put("工艺类型", pr.getOrDefault("工艺类型", "自制"));
            it.put("工艺序号", pr.getOrDefault("工艺序号", 0));
            it.put("加工顺序", pr.getOrDefault("加工顺序", 0));
            it.put("工艺路线", "");
            it.put("工序编码", pr.getOrDefault("工序编码", ""));
            it.put("工序名称", pr.getOrDefault("工序名称", ""));
            it.put("工序备注", pr.getOrDefault("工序备注", ""));
            it.put("生产车间", pr.getOrDefault("生产车间", ""));
            it.put("工作中心", pr.getOrDefault("工作中心", ""));
            it.put("设备", pr.getOrDefault("设备", ""));
            it.put("班组名称", pr.getOrDefault("班组", ""));
            it.put("工人名称", pr.getOrDefault("工人", ""));
            it.put("工序单位", pr.getOrDefault("工序单位", "件"));
            it.put("报工数量", pr.getOrDefault("计划数量", 0));
            it.put("可报工数量", pr.getOrDefault("计划数量", 0));
            it.put("合格数量", 0);
            it.put("不合格数量", 0);
            it.put("工资类型", pr.getOrDefault("工资类型", "计件"));
            it.put("工价", pr.getOrDefault("工价", 0));
            it.put("计时/计件金额", 0);
            it.put("调整工资", 0);
            it.put("金额", 0);
            it.put("单位标准工时", pr.getOrDefault("单位标准工时", 0));
            it.put("实际工时", 0);
            it.put("计划时间", pr.getOrDefault("计划时间", ""));
            it.put("完成时间", pr.getOrDefault("完成时间", ""));
            it.put("手工完工", false);
            it.put("委外供应商", pr.getOrDefault("委外供应商", ""));
            it.put("委外单价", pr.getOrDefault("委外单价", 0));
            it.put("税率%", pr.getOrDefault("税率%", 13));
            it.put("委外含税单价", 0);
            it.put("委外金额", 0);
            it.put("委外税额", 0);
            it.put("委外含税金额", 0);
            it.put("累计汇报数量", 0);
            it.put("需求令号", pr.getOrDefault("需求令号", p0.getOrDefault("需求令号", "")));
            it.put("图号", p0.getOrDefault("图号", ""));
            it.put("备注", "");
            items.add(it);
        }
        if (items.isEmpty()) throw new IllegalStateException("生产加工单无工序明细：" + no);

        Map<String, Object> detail = new HashMap<>();
        detail.put("items", items);

        // 新建兜底：单据日期=当天 + 单据编号=form_no（修复：生单生成的汇报单编号缺失，必填校验无法保存草稿）
        fillNewDefaults("PROCESS_REPORT", prData, newNo);

        FormData pr = new FormData();
        pr.setPanelCode("PROCESS_REPORT");
        pr.setFormNo(newNo);
        pr.setData(toJson(prData));
        pr.setDetailData(toJson(detail));
        pr.setStatus("草稿");
        pr.setCreateBy("admin");
        pr.setCreateTime(LocalDateTime.now());
        formMapper.insert(pr);

        Map<String, Object> out = new HashMap<>();
        out.put("单据状态", "草稿");
        out.put("gotoPanel", "PROCESS_REPORT");
        out.put("编号", newNo);
        return out;
    }

    // ══════════ 推式生单（业务流程图流转，2026-08-24 全量补全） ══════════

    private double num(Object v) {
        if (v == null) return 0;
        try { return Double.parseDouble(String.valueOf(v)); } catch (Exception e) { return 0; }
    }

    /** 通用：读取源单据（必须已审核），插入目标草稿单，返回 {编号, gotoPanel} */
    @SuppressWarnings("unchecked")
    private Map<String, Object> insertGenerated(String targetPanel, String sourcePanel, String sourceNo,
                                                Map<String, Object> head, Map<String, Object> detail) {
        String newNo = generateFormNo(targetPanel);
        // 新建兜底：字段默认值 + 单据日期=当天 + 单据编号/锭号=form_no（修复：生单目标单必填字段缺失无法保存草稿）
        fillNewDefaults(targetPanel, head, newNo);
        FormData fd = new FormData();
        fd.setPanelCode(targetPanel);
        fd.setFormNo(newNo);
        head.put("来源单据", sourcePanel);
        head.put("来源单号", sourceNo);
        fd.setData(toJson(head));
        fd.setDetailData(toJson(detail));
        fd.setStatus("草稿");
        fd.setCreateBy("admin");
        fd.setCreateTime(LocalDateTime.now());
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.insert(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("单据状态", "草稿");
        out.put("gotoPanel", targetPanel);
        out.put("编号", newNo);
        return out;
    }

    /** 推式生单：请购单（PU_REQ）→ 采购订单（PU_ORDER）；表头带建议供应商，明细带存货/数量/价格 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuOrderFromReq(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_REQ").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("请购单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核请购单可生成采购订单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("请购单无明细：" + no);

        Map<String, Object> poData = new HashMap<>();
        poData.put("单据日期", LocalDate.now().toString());
        poData.put("项目", head.getOrDefault("项目", ""));
        poData.put("供应商", head.getOrDefault("建议供应商", ""));
        poData.put("供应商编码", head.getOrDefault("建议供应商编码", ""));
        poData.put("币种", "人民币");
        poData.put("汇率", 1);
        poData.put("到货地址", head.getOrDefault("到货地址", ""));
        poData.put("交货日期", head.getOrDefault("需求日期", ""));
        poData.put("发货状态", "未发货");
        poData.put("合同号", String.valueOf(no));
        poData.put("订金金额", 0);
        poData.put("付款方式", "现付");
        poData.put("数据来源", "请购单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("物料编码", it.getOrDefault("存货编码", ""));
            r.put("物料名称", it.getOrDefault("存货名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("单位", it.getOrDefault("采购单位", "件"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("含税金额", it.getOrDefault("含税金额", 0));
            r.put("预计到货日期", it.getOrDefault("需求日期", ""));
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_ORDER", "PU_REQ", String.valueOf(no), poData, detail);
    }

    /** 推式生单：采购订单（PU_ORDER）→ 采购入库单（PURCHASE_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPurchaseInFromPo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("采购订单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核采购订单可生成采购入库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("采购订单无明细：" + no);

        Map<String, Object> piData = new HashMap<>();
        piData.put("单据日期", LocalDate.now().toString());
        piData.put("供应商编码", head.getOrDefault("供应商编码", ""));
        piData.put("供应商", head.getOrDefault("供应商", ""));
        piData.put("采购订单号", String.valueOf(no));
        piData.put("数据来源", "采购订单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", "原料仓");
            r.put("存货名称", it.getOrDefault("物料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("实收数量", it.getOrDefault("数量", 0));
            r.put("计量单位", it.getOrDefault("单位", "件"));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            r.put("含税单价", it.getOrDefault("含税单价", 0));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("含税金额", it.getOrDefault("含税金额", 0));
            r.put("现存量", it.getOrDefault("现存量", 0));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PURCHASE_IN", "PU_ORDER", String.valueOf(no), piData, detail);
    }

    /** 推式生单：采购订单（PU_ORDER）→ 进货单（PU_IN）：明细按采购订单物料带入，含税金额拆分税额 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuInFromPo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "PU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("采购订单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核采购订单可生成进货单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("采购订单无明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "进货");
        data.put("供应商编码", head.getOrDefault("供应商编码", ""));
        data.put("供应商", head.getOrDefault("供应商", ""));
        data.put("供应商简称", head.getOrDefault("供应商", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("仓库", "原料仓");
        data.put("来源单据", "PU_ORDER");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", it.getOrDefault("单价", 0)));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", "原料仓");
            r.put("存货编码", it.getOrDefault("物料编码", ""));
            r.put("存货名称", it.getOrDefault("物料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("数量", qty);
            r.put("采购单位", it.getOrDefault("单位", "件"));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("税率%", taxRate);
            r.put("含税单价", price);
            r.put("金额", Math.round(qty * num(it.getOrDefault("单价", 0)) * 100) / 100.0);
            r.put("税额", tax);
            r.put("含税金额", gross);
            r.put("现存量", it.getOrDefault("现存量", 0));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_IN", "PU_ORDER", String.valueOf(no), data, detail);
    }

    /** 推式生单：生产加工单（MANU_ORDER）→ 产成品入库单（FINISH_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createFinishInFromMo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MANU_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("生产加工单不存在：" + no);
        if (!"已审核".equals(src.getStatus()) && !"生产中".equals(src.getStatus())) {
            throw new IllegalStateException("仅已审核/生产中的生产加工单可生成产成品入库单");
        }
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> products = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        if (products.isEmpty()) throw new IllegalStateException("生产加工单无产成品明细：" + no);

        Map<String, Object> fiData = new HashMap<>();
        fiData.put("单据日期", LocalDate.now().toString());
        fiData.put("加工单号", String.valueOf(no));
        fiData.put("业务类型", "产成品入库");
        fiData.put("入库类别", "自制加工入库");
        fiData.put("生产车间", head.getOrDefault("生产车间", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> p : products) {
            Map<String, Object> r = new HashMap<>();
            r.put("产品名称", p.getOrDefault("产品名称", ""));
            r.put("仓库", "成品仓");
            r.put("规格型号", p.getOrDefault("规格型号", ""));
            r.put("计量单位", p.getOrDefault("生产单位", "件"));
            r.put("实收数量", p.getOrDefault("数量", 0));
            r.put("单价", 0);
            r.put("金额", 0);
            r.put("现存量", p.getOrDefault("现存量", 0));
            r.put("图号", p.getOrDefault("图号", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("FINISH_IN", "MANU_ORDER", String.valueOf(no), fiData, detail);
    }

    /** 推式生单：领料申请单（MATERIAL_REQ）或生产加工单（MANU_ORDER）→ 材料出库单（MATERIAL_OUT） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createMaterialOut(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode).eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("来源单据不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核单据可生成材料出库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> rows = new ArrayList<>();
        Map<String, Object> moData = new HashMap<>();
        moData.put("单据日期", LocalDate.now().toString());
        moData.put("业务类型", "材料出库");
        moData.put("出库类别", "直接领料");
        moData.put("生产车间", head.getOrDefault("生产车间", ""));
        moData.put("领用人", head.getOrDefault("领料申请人", ""));
        moData.put("加工单号", head.getOrDefault("加工单号", ""));
        if ("TRANSFER".equals(panelCode)) {
            // 推式生单：调拨单 → 材料出库单（调拨出库，T+ 调拨单出库方向）
            List<Map<String, Object>> items = dm.get("items") instanceof List
                    ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
            for (Map<String, Object> it : items) {
                Map<String, Object> r = new HashMap<>();
                r.put("仓库", head.getOrDefault("调出仓库", "原料仓"));
                r.put("加工单号", head.getOrDefault("来源单号", ""));
                r.put("材料名称", it.getOrDefault("存货名称", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                r.put("数量", it.getOrDefault("数量", 0));
                r.put("单价", it.getOrDefault("单价", 0));
                r.put("金额", it.getOrDefault("金额", 0));
                r.put("规格型号", it.getOrDefault("规格型号", ""));
                r.put("现存量", it.getOrDefault("现存量", 0));
                r.put("现存量说明", it.getOrDefault("现存量说明", ""));
                rows.add(r);
            }
            moData.put("出库类别", "调拨出库");
        } else if ("MATERIAL_REQ".equals(panelCode)) {
            List<Map<String, Object>> items = dm.get("items") instanceof List
                    ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
            for (Map<String, Object> it : items) {
                Map<String, Object> r = new HashMap<>();
                r.put("仓库", it.getOrDefault("仓库", "原料仓"));
                r.put("加工单号", it.getOrDefault("加工单号", ""));
                r.put("材料名称", it.getOrDefault("材料名称", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                r.put("数量", it.getOrDefault("数量", 0));
                r.put("单价", it.getOrDefault("单价", 0));
                r.put("金额", it.getOrDefault("金额", 0));
                r.put("规格型号", it.getOrDefault("规格型号", ""));
                r.put("现存量", it.getOrDefault("现存量", 0));
                r.put("现存量说明", it.getOrDefault("现存量说明", ""));
                rows.add(r);
            }
        } else {
            List<Map<String, Object>> mats = dm.get("materials") instanceof List
                    ? (List<Map<String, Object>>) dm.get("materials") : new ArrayList<>();
            for (Map<String, Object> m : mats) {
                Map<String, Object> r = new HashMap<>();
                r.put("仓库", m.getOrDefault("预出仓库", "原料仓"));
                r.put("加工单号", String.valueOf(no));
                r.put("材料名称", m.getOrDefault("材料名称", ""));
                r.put("计量单位", m.getOrDefault("计量单位", "kg"));
                r.put("数量", m.getOrDefault("计划数量", m.getOrDefault("需用数量", 0)));
                r.put("单价", 0);
                r.put("金额", 0);
                r.put("规格型号", m.getOrDefault("规格型号", ""));
                r.put("现存量", m.getOrDefault("现存量", 0));
                rows.add(r);
            }
        }
        if (rows.isEmpty()) throw new IllegalStateException("来源单据无材料明细：" + no);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("MATERIAL_OUT", panelCode, String.valueOf(no), moData, detail);
    }

    /** 推式生单：领料申请单（MATERIAL_REQ）→ 调拨单（TRANSFER）：明细材料转入调拨单，调出仓库=领料申请单仓库 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createTransferFromMaterialReq(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "MATERIAL_REQ").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("领料申请单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核领料申请单可生成调拨单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("领料申请单无材料明细：" + no);

        Map<String, Object> trData = new HashMap<>();
        trData.put("单据日期", LocalDate.now().toString());
        trData.put("业务类型", "调拨");
        trData.put("调出仓库", head.getOrDefault("仓库", "原料仓"));
        trData.put("调入仓库", "");
        trData.put("经手人", head.getOrDefault("领料申请人", ""));
        trData.put("生产车间", head.getOrDefault("生产车间", ""));
        trData.put("部门", head.getOrDefault("部门", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", it.getOrDefault("材料编码", ""));
            r.put("存货名称", it.getOrDefault("材料名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("计量单位", "kg"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("单价", 0));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            r.put("加工单号", it.getOrDefault("加工单号", ""));
            r.put("行中止", false);
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("TRANSFER", "MATERIAL_REQ", String.valueOf(no), trData, detail);
    }

    /** 推式生单：委外加工单（OUTSOURCE_ORDER）/领料申请单（MATERIAL_REQ）→ 委外发料单（OUTSOURCE_ISSUE） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createOutsourceIssue(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        String source = "OUTSOURCE_ORDER".equals(panelCode) ? "OUTSOURCE_ORDER" : "MATERIAL_REQ";
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, source).eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("来源单据不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核单据可生成委外发料单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> rows = new ArrayList<>();
        if ("OUTSOURCE_ORDER".equals(source)) {
            List<Map<String, Object>> mats = dm.get("materials") instanceof List
                    ? (List<Map<String, Object>>) dm.get("materials") : new ArrayList<>();
            for (Map<String, Object> m : mats) {
                Map<String, Object> r = new HashMap<>();
                r.put("材料编码", m.getOrDefault("材料编码", ""));
                r.put("材料名称", m.getOrDefault("材料名称", ""));
                r.put("规格型号", m.getOrDefault("规格型号", ""));
                r.put("计量单位", m.getOrDefault("计量单位", "kg"));
                r.put("数量", m.getOrDefault("计划数量", m.getOrDefault("需用数量", 0)));
                r.put("单价", 0);
                r.put("金额", 0);
                r.put("仓库", m.getOrDefault("预出仓库", "原料仓"));
                rows.add(r);
            }
        } else {
            List<Map<String, Object>> items = dm.get("items") instanceof List
                    ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
            for (Map<String, Object> it : items) {
                Map<String, Object> r = new HashMap<>();
                r.put("材料编码", it.getOrDefault("材料编码", ""));
                r.put("材料名称", it.getOrDefault("材料名称", ""));
                r.put("规格型号", it.getOrDefault("规格型号", ""));
                r.put("计量单位", it.getOrDefault("计量单位", "kg"));
                r.put("数量", it.getOrDefault("数量", 0));
                r.put("单价", it.getOrDefault("单价", 0));
                r.put("金额", it.getOrDefault("金额", 0));
                r.put("仓库", it.getOrDefault("仓库", "原料仓"));
                rows.add(r);
            }
        }
        if (rows.isEmpty()) throw new IllegalStateException("来源单据无材料明细：" + no);
        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "委外发料");
        data.put("委外供应商", head.getOrDefault("委外供应商", ""));
        data.put("委外加工单号", "OUTSOURCE_ORDER".equals(source) ? String.valueOf(no) : "");
        data.put("仓库", rows.get(0).getOrDefault("仓库", "原料仓"));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("OUTSOURCE_ISSUE", source, String.valueOf(no), data, detail);
    }

    /** 推式生单：委外加工单（OUTSOURCE_ORDER）→ 委外入库单（OUTSOURCE_IN） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createOutsourceInFromOrder(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "OUTSOURCE_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("委外加工单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核委外加工单可生成委外入库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("委外加工单无产成品明细：" + no);
        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "委外入库");
        data.put("委外供应商", head.getOrDefault("委外供应商", ""));
        data.put("委外加工单号", String.valueOf(no));
        data.put("仓库", "原料仓");
        data.put("经手人", head.getOrDefault("经手人", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("产品编码", it.getOrDefault("产品编码", ""));
            r.put("产品名称", it.getOrDefault("产品名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("计量单位", "件"));
            r.put("实收数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("委外单价", it.getOrDefault("单价", 0)));
            r.put("金额", it.getOrDefault("金额", 0));
            r.put("现存量", it.getOrDefault("现存量", 0));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("OUTSOURCE_IN", "OUTSOURCE_ORDER", String.valueOf(no), data, detail);
    }

    /** 推式生单：委外加工单（OUTSOURCE_ORDER）→ 委外加工费用单（OUTSOURCE_FEE）：按产成品明细委外单价×数量汇总 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createOutsourceFeeFromOrder(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "OUTSOURCE_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("委外加工单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核委外加工单可生成委外加工费用单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("products") instanceof List
                ? (List<Map<String, Object>>) dm.get("products") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("委外加工单无产成品明细：" + no);
        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "委外加工费用");
        data.put("委外供应商", head.getOrDefault("委外供应商", ""));
        data.put("委外加工单号", String.valueOf(no));
        data.put("经手人", head.getOrDefault("经手人", ""));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("委外单价", it.getOrDefault("单价", 0)));
            double amount = Math.round(qty * price * 100) / 100.0;
            total += amount;
            Map<String, Object> r = new HashMap<>();
            r.put("费用项目", "委外加工费");
            r.put("产品名称", it.getOrDefault("产品名称", ""));
            r.put("计量单位", it.getOrDefault("计量单位", "件"));
            r.put("数量", qty);
            r.put("委外单价", price);
            r.put("费用金额", amount);
            r.put("备注", "");
            rows.add(r);
        }
        data.put("费用合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("OUTSOURCE_FEE", "OUTSOURCE_ORDER", String.valueOf(no), data, detail);
    }

    /** 推式生单：销售订单（SO_ORDER）→ 销售出库单（SALE_OUT） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleOutFromSo(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SO_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("销售订单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核销售订单可生成销售出库单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("销售订单无明细：" + no);

        Map<String, Object> soData = new HashMap<>();
        soData.put("单据日期", LocalDate.now().toString());
        soData.put("客户", head.getOrDefault("客户", ""));
        soData.put("客户编码", head.getOrDefault("客户编码", ""));
        soData.put("结算客户", head.getOrDefault("结算客户", ""));
        soData.put("业务员", head.getOrDefault("业务员", ""));
        soData.put("销售订单号", String.valueOf(no));
        soData.put("数据来源", "销售订单");
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("仓库", "成品仓");
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("销售单位", "件"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            double tax = num(it.getOrDefault("税率%", 13));
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", 0));
            double amount = qty * price;
            r.put("售价", price);
            r.put("含税售价", price);
            r.put("销售金额", Math.round(amount * 100) / 100.0);
            r.put("含税销售金额", Math.round(amount * 100) / 100.0);
            r.put("税额", Math.round(amount * tax / (100 + tax) * 100) / 100.0);
            r.put("折扣金额", 0);
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("现存量说明", it.getOrDefault("现存量说明", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_OUT", "SO_ORDER", String.valueOf(no), soData, detail);
    }

    /** 推式生单：报价单（QUOTE_ORDER）→ 销售订单（SO_ORDER）；表头带客户/部门/业务员，明细带报价单价/含税单价 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleOrderFromQuote(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "QUOTE_ORDER").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("报价单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核报价单可生成销售订单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("报价单无明细：" + no);

        Map<String, Object> soData = new HashMap<>();
        soData.put("单据日期", LocalDate.now().toString());
        soData.put("客户", head.getOrDefault("客户", ""));
        soData.put("客户编码", head.getOrDefault("客户编码", ""));
        soData.put("结算客户", head.getOrDefault("结算客户", ""));
        soData.put("部门", head.getOrDefault("部门", ""));
        soData.put("业务员", head.getOrDefault("业务员", ""));
        soData.put("项目", head.getOrDefault("项目", ""));
        soData.put("预计交货日期", head.getOrDefault("有效期至", ""));
        soData.put("联系人", head.getOrDefault("联系人", ""));
        soData.put("来源单据", "QUOTE_ORDER");
        soData.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Map<String, Object> it : items) {
            Map<String, Object> r = new HashMap<>();
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("销售单位", it.getOrDefault("销售单位", "件"));
            r.put("数量", it.getOrDefault("数量", 0));
            r.put("单价", it.getOrDefault("报价单价", 0));
            r.put("税率%", it.getOrDefault("税率%", 13));
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", it.getOrDefault("报价单价", 0)));
            r.put("含税单价", price);
            r.put("含税金额", Math.round(qty * price * 100) / 100.0);
            r.put("金额", Math.round(qty * num(it.getOrDefault("报价单价", 0)) * 100) / 100.0);
            r.put("折扣金额", it.getOrDefault("折扣金额", 0));
            r.put("预计交货日期", it.getOrDefault("预计交货日期", ""));
            r.put("现存量", it.getOrDefault("现存量", 0));
            r.put("备注", it.getOrDefault("备注", ""));
            rows.add(r);
        }
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SO_ORDER", "QUOTE_ORDER", String.valueOf(no), soData, detail);
    }

    /** 推式生单：销货单（SALE_INV）→ 销售发票（SALE_INVOICE）；明细按 数量×含税单价 拆分税额/价税合计 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleInvoiceFromSaleInv(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "SALE_INV").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("销货单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核销货单可生成销售发票");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("销货单无明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "销售发票");
        data.put("客户", head.getOrDefault("客户", ""));
        data.put("客户编码", head.getOrDefault("客户编码", ""));
        data.put("结算客户", head.getOrDefault("结算客户", ""));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("业务员", head.getOrDefault("经手人", ""));
        data.put("开票类型", "增值税专用发票");
        data.put("销货单号", String.valueOf(no));
        data.put("来源单据", "SALE_INV");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double totalAmount = 0;
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            double price = num(it.getOrDefault("含税单价", it.getOrDefault("单价", 0)));
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            double net = Math.round((gross - tax) * 100) / 100.0;
            totalAmount += gross;
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", it.getOrDefault("存货编码", ""));
            r.put("存货名称", it.getOrDefault("存货名称", ""));
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", it.getOrDefault("销售单位", "件"));
            r.put("数量", qty);
            r.put("无税单价", Math.round(net / Math.max(qty, 1) * 100) / 100.0);
            r.put("税率%", taxRate);
            r.put("税额", tax);
            r.put("价税合计", gross);
            rows.add(r);
        }
        data.put("价税合计", Math.round(totalAmount * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_INVOICE", "SALE_INV", String.valueOf(no), data, detail);
    }

    /** 推式生单：费用单（EXPENSE）→ 销售费用分摊单（SALE_COST_ALLOC）；费用明细按费用项目带入 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createSaleCostAllocFromExpense(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "EXPENSE").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("费用单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核费用单可生成销售费用分摊单");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("费用单无费用明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "销售费用分摊");
        data.put("费用类型", head.getOrDefault("费用类型", "销售费用"));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        data.put("项目", head.getOrDefault("项目", ""));
        data.put("来源单据", "EXPENSE");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double amount = num(it.getOrDefault("金额", 0));
            total += amount;
            Map<String, Object> r = new HashMap<>();
            r.put("费用单号", String.valueOf(no));
            r.put("费用项目", it.getOrDefault("费用项目", ""));
            r.put("分摊金额", amount);
            r.put("备注", it.getOrDefault("备注", ""));
            rows.add(r);
        }
        data.put("分摊合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("SALE_COST_ALLOC", "EXPENSE", String.valueOf(no), data, detail);
    }

    /** 推式生单：进货单（PU_IN）/采购订单（PU_ORDER）→ 采购发票（PU_INVOICE）；明细按含税单价拆分税额/价税合计 */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuInvoice(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        String source = "PU_IN".equals(panelCode) ? "PU_IN" : "PU_ORDER";
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, source).eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("来源单据不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核单据可生成采购发票");
        Map<String, Object> head = parseData(src.getData());
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("来源单据无明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "采购发票");
        data.put("供应商", String.valueOf(head.getOrDefault("供应商", head.getOrDefault("供应商简称", ""))));
        data.put("供应商编码", head.getOrDefault("供应商编码", ""));
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("业务员", String.valueOf(head.getOrDefault("业务员", head.getOrDefault("经手人", ""))));
        data.put("开票类型", "增值税专用发票");
        data.put("进货单号", "PU_IN".equals(source) ? String.valueOf(no) : "");
        data.put("采购订单号", "PU_ORDER".equals(source) ? String.valueOf(no) : "");
        data.put("来源单据", source);
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double qty = num(it.getOrDefault("数量", 0));
            Object priceVal = it.getOrDefault("含税单价", it.getOrDefault("单价", 0));
            double price = num(priceVal);
            double taxRate = num(it.getOrDefault("税率%", 13));
            double gross = Math.round(qty * price * 100) / 100.0;
            double tax = Math.round(gross * taxRate / (100 + taxRate) * 100) / 100.0;
            double net = Math.round((gross - tax) * 100) / 100.0;
            total += gross;
            Object invCode = it.getOrDefault("存货编码", it.getOrDefault("物料编码", ""));
            Object invName = it.getOrDefault("存货名称", it.getOrDefault("物料名称", ""));
            Object unit = it.getOrDefault("采购单位", it.getOrDefault("单位", it.getOrDefault("计量单位", "件")));
            Map<String, Object> r = new HashMap<>();
            r.put("存货编码", invCode);
            r.put("存货名称", invName);
            r.put("规格型号", it.getOrDefault("规格型号", ""));
            r.put("计量单位", unit);
            r.put("数量", qty);
            r.put("无税单价", Math.round(net / Math.max(qty, 1) * 100) / 100.0);
            r.put("税率%", taxRate);
            r.put("税额", tax);
            r.put("价税合计", gross);
            rows.add(r);
        }
        data.put("价税合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_INVOICE", source, String.valueOf(no), data, detail);
    }

    /** 推式生单：费用单（EXPENSE，费用类型=采购费用）→ 采购费用分摊单（PU_COST_ALLOC） */
    @SuppressWarnings("unchecked")
    private Map<String, Object> createPuCostAllocFromExpense(String panelCode, Map<String, Object> formData) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData src = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "EXPENSE").eq(FormData::getFormNo, String.valueOf(no)));
        if (src == null) throw new IllegalArgumentException("费用单不存在：" + no);
        if (!"已审核".equals(src.getStatus())) throw new IllegalStateException("仅已审核费用单可生成采购费用分摊单");
        Map<String, Object> head = parseData(src.getData());
        if (!"采购费用".equals(String.valueOf(head.getOrDefault("费用类型", "")))) {
            throw new IllegalStateException("仅费用类型为「采购费用」的费用单可生成采购费用分摊单");
        }
        Map<String, Object> dm = parseData(src.getDetailData());
        List<Map<String, Object>> items = dm.get("items") instanceof List
                ? (List<Map<String, Object>>) dm.get("items") : new ArrayList<>();
        if (items.isEmpty()) throw new IllegalStateException("费用单无费用明细：" + no);

        Map<String, Object> data = new HashMap<>();
        data.put("单据日期", LocalDate.now().toString());
        data.put("业务类型", "采购费用分摊");
        data.put("供应商", head.getOrDefault("往来单位", ""));
        data.put("费用类型", "采购费用");
        data.put("部门", head.getOrDefault("部门", ""));
        data.put("经手人", head.getOrDefault("经手人", ""));
        data.put("来源单据", "EXPENSE");
        data.put("来源单号", String.valueOf(no));
        List<Map<String, Object>> rows = new ArrayList<>();
        double total = 0;
        for (Map<String, Object> it : items) {
            double amount = num(it.getOrDefault("金额", 0));
            total += amount;
            Map<String, Object> r = new HashMap<>();
            r.put("费用单号", String.valueOf(no));
            r.put("费用项目", it.getOrDefault("费用项目", ""));
            r.put("分摊金额", amount);
            r.put("备注", it.getOrDefault("备注", ""));
            rows.add(r);
        }
        data.put("分摊合计", Math.round(total * 100) / 100.0);
        Map<String, Object> detail = new HashMap<>();
        detail.put("items", rows);
        return insertGenerated("PU_COST_ALLOC", "EXPENSE", String.valueOf(no), data, detail);
    }

    /**
     * 默认工序（3 道：下料/机加工/检验）：推式生单生成加工单时附带，工序汇报单按此生成待汇报行
     */
    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> defaultProcesses(List<Map<String, Object>> products, String workshop) {
        List<Map<String, Object>> out = new ArrayList<>();
        Map<String, Object> p0 = products.isEmpty() ? new HashMap<>() : products.get(0);
        Object qty = p0.getOrDefault("数量", 0);
        Object req = p0.getOrDefault("需求令号", "");
        Object fig = p0.getOrDefault("图号", "");
        addDefaultProc(out, 1, "PX001", "下料", workshop, "锯床-01", "下料班", "王强", qty, 2.5, 0.05, req, fig);
        addDefaultProc(out, 2, "PX002", "机加工", workshop, "数控车床-01", "车工班", "李强", qty, 3.5, 0.1, req, fig);
        addDefaultProc(out, 3, "PX007", "检验", workshop, "检测台-01", "质检班", "赵敏", qty, 1.5, 0.03, req, fig);
        return out;
    }

    private void addDefaultProc(List<Map<String, Object>> out, int seq, String code, String name, String workshop,
                                String equip, String team, String worker, Object qty, double price, double stdHour,
                                Object req, Object fig) {
        Map<String, Object> it = new HashMap<>();
        it.put("工序行码", "");
        it.put("工艺类型", "自制");
        it.put("工艺序号", 0);
        it.put("加工顺序", seq);
        it.put("加工类型", "自制");
        it.put("工序编码", code);
        it.put("工序名称", name);
        it.put("工序备注", "");
        it.put("生产车间", workshop);
        it.put("工作中心", workshop + "中心");
        it.put("设备", equip);
        it.put("班组", team);
        it.put("工人", worker);
        it.put("委外供应商", "");
        it.put("委外单价", 0);
        it.put("税率%", 13);
        it.put("委外金额", 0);
        it.put("按辅单位计价", false);
        it.put("计价辅单位", "件");
        it.put("换算率(辅单位)", 1);
        it.put("计价辅数量", 0);
        it.put("工序完工状态", "未完工");
        it.put("手工完工", false);
        it.put("行中止", false);
        it.put("工价（辅单位）", 0);
        it.put("工废工价", 0);
        it.put("工废工价（辅单位）", 0);
        it.put("料废工价", 0);
        it.put("料废工价（辅单位）", 0);
        it.put("工序单位", "件");
        it.put("计划数量", qty);
        it.put("工资类型", "计件");
        it.put("工价", price);
        it.put("金额", 0);
        it.put("关键工序", false);
        it.put("单位标准工时", stdHour);
        it.put("待返修数量-本序发现", 0);
        it.put("待返修数量-他序发现", 0);
        it.put("计划时间", "");
        it.put("完成时间", "");
        it.put("单重", 0);
        it.put("总重", 0);
        it.put("需求令号", req);
        it.put("子表材料", new ArrayList<>());
        it.put("工序字符专用自定义项1", "");
        out.add(it);
    }

    private Map<String, Object> create(String panelCode, Map<String, Object> formData, String userName) {
        return save(panelCode, formData, userName);
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> save(String panelCode, Map<String, Object> formData, String userName) {
        Object detail = formData.remove("detail");
        FormData fd;
        Object no = formData.get("编号");
        String stateField = (String) fieldsOf(panelCode).get("stateFieldName");
        formData.remove("编号");
        formData.remove("创建时间");
        formData.remove("更新时间");
        formData.remove("发起人编号");
        if (no != null && !"".equals(String.valueOf(no))) {
            fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, panelCode)
                    .eq(FormData::getFormNo, String.valueOf(no)));
            if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
            // 单据=草稿可保存；基础档案（存货/部门等）状态为 启用/停用 同样允许保存
            String st0 = fd.getStatus() == null ? "" : fd.getStatus();
            if (!"草稿".equals(st0) && !"启用".equals(st0) && !"停用".equals(st0)) {
                throw new IllegalStateException("仅草稿状态可保存");
            }
            fd.setData(toJson(formData));
            // 明细兜底：未传 detail（如列表页仅保存表头）时保留原明细，禁止用 null 覆盖清空
            fd.setDetailData(detail == null || "null".equals(String.valueOf(detail)) ? fd.getDetailData() : toJson(detail));
            // 档案面板：保存即生效（草稿/停用保存后置「启用」）
            if (isArchivePanel(panelCode)) fd.setStatus("启用");
            fd.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(fd);
        } else {
            fd = new FormData();
            fd.setPanelCode(panelCode);
            // 存货新单：按类别前缀编号（产成品 CP-、原材料 YL-、辅助材料 FZ-、包装物 BZ-、半成品 BC-）
            // 编号规则：基础档案 / 自编码面板（autoCodeField 非「单据编号」，如 工艺路线编码/物料清单编码/期初*号）→ 面板代码-3位序号；
            //          标准单据（单据编号 或 无 autoCodeField）→ 日期序号（SO-/MO-/FI-/RK-/CK-/GX-）
            String newNo;
            if ("INV".equals(panelCode)) {
                newNo = invNo(formData.get("类别") == null ? "" : String.valueOf(formData.get("类别")));
            } else if (isArchivePanel(panelCode)) {
                newNo = archNo(panelCode);
            } else {
                newNo = generateFormNo(panelCode);
            }
            fd.setFormNo(newNo);
            // 新建保存兜底（2026-08-20 定稿）：所有面板补 字段默认值 + 单据日期=当天 + 单据编号/锭号=form_no
            // （缺才填）——手动新增/选单生成/推式生单的草稿表头数据完整可见；ensureAuditStamp 审核时幂等兜底
            fillNewDefaults(panelCode, formData, newNo);
            fd.setData(toJson(formData));
            fd.setDetailData(detail == null ? "{}" : toJson(detail));
            // 档案/自编码面板（EMP/DEPT/ROUTE/BOM…）新建即「启用」（保存即生效）；单据类初始「草稿」
            fd.setStatus("INV".equals(panelCode) || isArchivePanel(panelCode) ? "启用" : "草稿");
            fd.setCreateBy(userName == null ? "admin" : userName);
            fd.setCreateTime(LocalDateTime.now());
            fd.setUpdateTime(LocalDateTime.now());
            formMapper.insert(fd);
        }
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", fd.getStatus());
        out.put(stateField, fd.getStatus());
        return out;
    }

    @SuppressWarnings("unchecked")
    /** 当前登录用户（JwtAuthFilter 写入 SecurityContext；未认证时兜底 system） */
    private String currentUserName() {
        var auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getName() != null && !auth.getName().isBlank()) return auth.getName();
        return "system";
    }

    private Map<String, Object> changeStatus(String panelCode, Map<String, Object> formData, String action) {
        Object no = formData.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
        if ("审核".equals(action)) {
            if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可审核");
            // 人工审核：审核人 = 当前登录人（不再硬编码 admin）
            String operator = currentUserName();
            fd.setStatus("已审核");
            fd.setAuditBy(operator);
            fd.setAuditTime(LocalDateTime.now());
            // 审核人/审核时间/审核意见 写入表头 JSON（前端表尾/列表直接展示）
            Map<String, Object> head = parseData(fd.getData());
            head.put("审核人", operator);
            head.put("审核时间", fd.getAuditTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
            Object opinion = formData.get("审核意见");
            if (opinion != null && !String.valueOf(opinion).isBlank()) head.put("审核意见", String.valueOf(opinion));
            // 审批面板：审核时自动填写 单据日期 + 单据编号（缺才填，见 ensureAuditStamp）
            ensureAuditStamp(head, fd.getFormNo(), panelCode);
            fd.setData(toJson(head));
        } else if ("弃审".equals(action)) {
            if (!"已审核".equals(fd.getStatus())) throw new IllegalStateException("仅已审核状态可弃审");
            Map<String, Object> head = parseData(fd.getData());
            head.remove("审核人");
            head.remove("审核时间");
            head.remove("审核意见");
            // 显式 set(null)：MyBatis-Plus 默认 NOT_NULL 策略不会把 null 字段写进 UPDATE，
            // 必须用 LambdaUpdateWrapper.set 强制清空 audit_by/audit_time
            formMapper.update(null, new LambdaUpdateWrapper<FormData>()
                    .eq(FormData::getId, fd.getId())
                    .set(FormData::getStatus, "草稿")
                    .set(FormData::getAuditBy, null)
                    .set(FormData::getAuditTime, null)
                    .set(FormData::getData, toJson(head))
                    .set(FormData::getUpdateTime, LocalDateTime.now()));
            return new HashMap<String, Object>() {{
                put("编号", fd.getFormNo());
                put("单据状态", "草稿");
            }};
        } else if ("关闭".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中状态可关闭");
            }
            fd.setStatus("已关闭");
            fd.setCloseTime(LocalDateTime.now());
        } else if ("手工完工".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中状态可手工完工");
            }
            fd.setStatus("已完工");
        } else if ("中止".equals(action)) {
            if (!"已审核".equals(fd.getStatus()) && !"生产中".equals(fd.getStatus()) && !"已完工".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已审核/生产中/已完工状态可中止");
            }
            fd.setStatus("已中止");
        } else if ("取消中止".equals(action)) {
            if (!"已中止".equals(fd.getStatus())) {
                throw new IllegalStateException("仅已中止状态可取消中止");
            }
            fd.setStatus("已审核");
        }
        fd.setUpdateTime(LocalDateTime.now());
        formMapper.updateById(fd);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", fd.getStatus());
        return out;
    }

    // ---------- 审批流：提交审批 → 审批中 → 审批通过/审批驳回（全留痕，可扩展多级） ----------

    private FormData formOf(String panelCode, Object no) {
        if (no == null) throw new IllegalArgumentException("缺少表单编号");
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, String.valueOf(no)));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + no);
        return fd;
    }

    private String opinionOf(Map<String, Object> formData) {
        Object v = formData == null ? null : formData.get("审批意见");
        return v == null ? "" : String.valueOf(v).trim();
    }

    private void recordApproval(String panelCode, String formNo, String action, String result, String opinion) {
        FormApproval rec = new FormApproval();
        rec.setPanelCode(panelCode);
        rec.setFormNo(formNo);
        rec.setAction(action);
        rec.setResult(result);
        rec.setNodeNo(1);
        rec.setOperator(currentUserName());
        rec.setOpinion(opinion.isEmpty() ? null : opinion);
        rec.setCreateTime(LocalDateTime.now());
        approvalMapper.insert(rec);
    }

    /** 提交审批：仅草稿 → 审批中 */
    private Map<String, Object> submitApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"草稿".equals(fd.getStatus())) throw new IllegalStateException("仅草稿状态可提交审批");
        String operator = currentUserName();
        String now = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        fd.setStatus("审批中");
        fd.setUpdateTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "审批中");
        head.put("提交人", operator);
        head.put("提交时间", now);
        // 审批面板：提交审批时自动填写 单据日期 + 单据编号（缺才填）
        ensureAuditStamp(head, fd.getFormNo(), panelCode);
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "SUBMIT", "PENDING", opinionOf(formData));
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "审批中");
        return out;
    }

    /** 审批通过：仅审批中 → 已审核（审核人=当前登录人，写审核人/时间/意见） */
    private Map<String, Object> approveApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"审批中".equals(fd.getStatus())) throw new IllegalStateException("仅审批中状态可审批通过");
        String operator = currentUserName();
        String opinion = opinionOf(formData);
        fd.setStatus("已审核");
        fd.setAuditBy(operator);
        fd.setAuditTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "已通过");
        head.put("审核人", operator);
        head.put("审核时间", fd.getAuditTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
        if (!opinion.isEmpty()) head.put("审核意见", opinion);
        // 审批面板：审批通过时自动填写 单据日期 + 单据编号（缺才填，幂等）
        ensureAuditStamp(head, fd.getFormNo(), panelCode);
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "APPROVE", "APPROVED", opinion);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "已审核");
        return out;
    }

    /** 审批驳回：仅审批中 → 草稿（意见必填，驳回后修改可重新提交） */
    private Map<String, Object> rejectApproval(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        if (!"审批中".equals(fd.getStatus())) throw new IllegalStateException("仅审批中状态可审批驳回");
        String opinion = opinionOf(formData);
        if (opinion.isEmpty()) throw new IllegalStateException("审批驳回必须填写审批意见");
        fd.setStatus("草稿");
        fd.setUpdateTime(LocalDateTime.now());
        Map<String, Object> head = parseData(fd.getData());
        head.put("审批状态", "已驳回");
        head.remove("提交人");
        head.remove("提交时间");
        fd.setData(toJson(head));
        formMapper.updateById(fd);
        recordApproval(panelCode, fd.getFormNo(), "REJECT", "REJECTED", opinion);
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("单据状态", "草稿");
        return out;
    }

    /** 审批情况：返回该单据全部审批记录（按时间升序） */
    private Map<String, Object> approvalHistory(String panelCode, Map<String, Object> formData) {
        FormData fd = formOf(panelCode, formData.get("编号"));
        Map<String, Object> out = new HashMap<>();
        out.put("编号", fd.getFormNo());
        out.put("list", getApprovalHistory(panelCode, fd.getFormNo()));
        return out;
    }

    public List<Map<String, Object>> getApprovalHistory(String panelCode, String formNo) {
        List<FormApproval> recs = approvalMapper.selectList(new LambdaQueryWrapper<FormApproval>()
                .eq(FormApproval::getPanelCode, panelCode)
                .eq(FormApproval::getFormNo, formNo)
                .orderByAsc(FormApproval::getId));
        List<Map<String, Object>> out = new ArrayList<>();
        for (FormApproval r : recs) {
            Map<String, Object> m = new HashMap<>();
            m.put("action", r.getAction());
            m.put("result", r.getResult());
            m.put("operator", r.getOperator());
            m.put("opinion", r.getOpinion());
            m.put("nodeNo", r.getNodeNo());
            m.put("createTime", r.getCreateTime() == null ? ""
                    : r.getCreateTime().format(DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")));
            out.add(m);
        }
        return out;
    }

    @Transactional
    public void deleteForms(String panelCode, List<String> rowCodes) {
        for (String code : rowCodes) delete(panelCode, code);
    }

    private void delete(String panelCode, String code) {
        FormData fd = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, code));
        if (fd == null) throw new IllegalArgumentException("表单数据不存在：" + code);
        // 单据=草稿可删；基础档案（存货/部门等）状态为 启用/停用 同样允许删除
        String st1 = fd.getStatus() == null ? "" : fd.getStatus();
        if (!"草稿".equals(st1) && !"启用".equals(st1) && !"停用".equals(st1)) {
            throw new IllegalStateException("仅草稿状态可删除");
        }
        formMapper.deleteById(fd.getId());
    }

    // ---------- 工具 ----------

    // 存货类别 → 单据编号前缀（5 类别缩写）
    private static final Map<String, String> INV_PRE = new HashMap<>();
    static {
        INV_PRE.put("产成品", "CP");
        INV_PRE.put("原材料", "YL");
        INV_PRE.put("辅助材料", "FZ");
        INV_PRE.put("包装物", "BZ");
        INV_PRE.put("半成品", "BC");
    }

    /** 存货新类别单据编号：类别前缀-最大序号+1（如 CP-002 / BC-001），按最大序号递增并查重 */
    private String invNo(String cat) {
        String pre = INV_PRE.getOrDefault(cat == null ? "" : cat, "INV");
        long max = maxSeq("INV", pre + "-");
        String no;
        do {
            max++;
            no = pre + "-" + String.format("%03d", max);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "INV")
                .eq(FormData::getFormNo, no)) > 0);
        return no;
    }

    private String generateFormNo(String panelCode) {
        // 单据编号：前缀-yyyy-MM-dd+2位当日序号（如 MO-2026-08-1701 / SO-2026-08-1701）
        // 每天从 01 顺序排；精确查重确保单据号不允许重复（全局唯一）
        String biz;
        if ("SO_ORDER".equals(panelCode)) biz = "SO-";
        else if ("MANU_ORDER".equals(panelCode)) biz = "MO-";
        else if ("FINISH_IN".equals(panelCode)) biz = "FI-";       // 产成品入库单
        else if ("PURCHASE_IN".equals(panelCode)) biz = "RK-";     // 采购入库单（T+ 入库惯例）
        else if ("SALE_OUT".equals(panelCode)) biz = "CK-";        // 销售出库单（T+ 出库惯例）
        else if ("MATERIAL_OUT".equals(panelCode)) biz = "CL-";    // 材料出库单（2026-08-24 修复：原默认 MO- 前缀错误）
        else if ("PROCESS_REPORT".equals(panelCode)) biz = "GX-";  // 工序汇报单
        else if ("PU_IN".equals(panelCode)) biz = "PU-";           // 进货单
        else if ("PU_ORDER".equals(panelCode)) biz = "PO-";        // 采购订单（2026-08-24 修复：原默认 MO- 前缀错误）
        else if ("PU_REQ".equals(panelCode)) biz = "CG-";          // 请购单（T+ 采购惯例 CG）
        else if ("SALE_INV".equals(panelCode)) biz = "XS-";        // 销货单
        else if ("PICK_ORDER".equals(panelCode)) biz = "PH-";      // 配货单
        else if ("MATERIAL_REQ".equals(panelCode)) biz = "LL-";    // 领料申请单
        else if ("ARRIVAL_IN".equals(panelCode)) biz = "DH-";        // 到货单
        else if ("FINISH_INSPECT".equals(panelCode)) biz = "BJ-";   // 成品报检单
        else if ("INSPECTION".equals(panelCode)) biz = "JY-";       // 来料成品检验单
        else if ("DISPATCH".equals(panelCode)) biz = "PG-";         // 工序派工单
        else if ("TRANSFER".equals(panelCode)) biz = "DB-";        // 调拨单（T+ 调拨惯例 DB）
        else if ("OUTSOURCE_ORDER".equals(panelCode)) biz = "WW-"; // 委外加工单（T+ 委外惯例 WW）
        else if ("OUTSOURCE_ISSUE".equals(panelCode)) biz = "WF-"; // 委外发料单
        else if ("OUTSOURCE_IN".equals(panelCode)) biz = "WR-";    // 委外入库单
        else if ("OUTSOURCE_FEE".equals(panelCode)) biz = "WY-";   // 委外加工费用单
        else if ("QUOTE_ORDER".equals(panelCode)) biz = "BJ-";     // 报价单（T+ 报价惯例 BJ）
        else if ("SALE_INVOICE".equals(panelCode)) biz = "FP-";    // 销售发票（T+ 发票惯例 FP）
        else if ("EXPENSE".equals(panelCode)) biz = "FY-";         // 费用单（T+ 费用惯例 FY）
        else if ("SALE_COST_ALLOC".equals(panelCode)) biz = "FT-"; // 销售费用分摊单（T+ 分摊惯例 FT）
        else if ("PU_INVOICE".equals(panelCode)) biz = "PI-";     // 采购发票（进项发票）
        else if ("PU_COST_ALLOC".equals(panelCode)) biz = "PC-";  // 采购费用分摊单
        else if ("STOCK_CHECK".equals(panelCode)) biz = "PD-";    // 库存盘点单（T+ 盘点惯例 PD）
        else if ("LOCATION_ADJUST".equals(panelCode)) biz = "HW-"; // 货位调整单（T+ 货位惯例 HW）
        else if ("SERIAL_NO".equals(panelCode)) biz = "XL-";     // 序列号登记（T+ 序列号惯例 XL）
        else if ("INV".equals(panelCode)) biz = "INV-"; // 存货类别单据
        else biz = "MO-";
        String base = biz + LocalDate.now().format(DateTimeFormatter.ofPattern("yyyy-MM-dd"));
        int seq = 1;
        String no;
        do {
            no = base + String.format("%02d", seq++);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, no)) > 0);
        return no;
    }

    private String categoryOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object cat = cfg.get("metadata") == null ? null : ((Map<String, Object>) cfg.get("metadata")).get("panelCategory");
        return cat == null ? "" : String.valueOf(cat);
    }

    private String autoCodeFieldOf(String panelCode) {
        Map<String, Object> cfg = loadConfig(panelCode);
        Object f = cfg.get("metadata") == null ? null : ((Map<String, Object>) cfg.get("metadata")).get("autoCodeField");
        return f == null ? "" : String.valueOf(f);
    }

    /** 启用审批流的面板（与前端 APPROVAL_PANELS 一致）：单据日期/单据编号不在新建时预填，提交审核时自动填写 */
    private static final Set<String> APPROVAL_PANELS = Set.of(
            "SO_ORDER", "PURCHASE_IN", "FINISH_IN", "OTHER_IN", "SALE_OUT", "MATERIAL_OUT", "OTHER_OUT",
            "MANU_ORDER", "PROCESS_REPORT", "INIT_AP", "INIT_AR", "INIT_BALANCE", "BOM", "ROUTE", "PU_REQ",
            "TRANSFER", "OUTSOURCE_ORDER", "OUTSOURCE_ISSUE", "OUTSOURCE_IN", "OUTSOURCE_FEE",
            "QUOTE_ORDER", "SALE_INVOICE", "EXPENSE", "SALE_COST_ALLOC", "PU_INVOICE", "PU_COST_ALLOC",
            "STOCK_CHECK", "LOCATION_ADJUST", "SERIAL_NO");

    /**
     * 审批面板审核类动作（审核/提交审批/审批通过）时自动补表头：
     * 单据日期 = 当天；单据编号（autoCodeField，无则 MANU 的 锭号）= form_no。缺才填（幂等，驳回再提交不覆盖）。
     */
    private void ensureAuditStamp(Map<String, Object> head, String formNo, String panelCode) {
        if (!APPROVAL_PANELS.contains(panelCode)) return;
        Object d = head.get("单据日期");
        if (d == null || String.valueOf(d).isBlank()) head.put("单据日期", LocalDate.now().toString());
        String af = autoCodeFieldOf(panelCode);
        if (af == null || af.isBlank()) {
            Object v = head.get("锭号");
            if (v == null || String.valueOf(v).isBlank()) head.put("锭号", formNo);
        } else {
            Object v = head.get(af);
            if (v == null || String.valueOf(v).isBlank()) head.put(af, formNo);
        }
    }

    /** 新建保存兜底：字段默认值（与 getNewFormPermMatrix 一致）+ 单据日期=当天 + 单据编号/锭号=form_no（缺才填） */
    @SuppressWarnings("unchecked")
    private void fillNewDefaults(String panelCode, Map<String, Object> formData, String newNo) {
        List<String> names = new ArrayList<>();
        for (Map<String, Object> f : (List<Map<String, Object>>) fieldsOf(panelCode).get("fields")) {
            String dn = String.valueOf(f.get("dataName"));
            names.add(dn);
            Object dv = f.get("defaultValue");
            if (dv != null && !"".equals(String.valueOf(dv))) formData.putIfAbsent(dn, dv);
        }
        if (names.contains("单据日期")) {
            // 新建即填当天（忽略面板配置里的历史默认日期，2026-08-24 修复：配置 defaultValue 为旧日期导致新单日期错误）
            formData.put("单据日期", LocalDate.now().toString());
        }
        String codeKey = names.contains("单据编号") ? "单据编号" : (names.contains("锭号") ? "锭号" : null);
        if (codeKey != null) {
            Object cv = formData.get(codeKey);
            if (cv == null || String.valueOf(cv).isBlank()) formData.put(codeKey, newNo);
        }
    }

    /** 档案/自编码面板（基础档案类别 或 自编码字段）：EMP/DEPT/WH/ROUTE/BOM/INIT_* 等——保存即生效（状态=启用） */
    private boolean isArchivePanel(String panelCode) {
        if ("INV".equals(panelCode)) return true;
        // 审批面板（BOM/ROUTE/INIT_* 等）：保存保留草稿，走 草稿→审核 链路（2026-08-20 单单据改造连带修复）
        if (APPROVAL_PANELS.contains(panelCode)) return false;
        if ("基础档案".equals(categoryOf(panelCode))) return true;
        String autoField = autoCodeFieldOf(panelCode);
        if (autoField == null || autoField.isBlank() || "单据编号".equals(autoField)) return false;
        return true; // 自编码面板（工艺路线编码/物料清单编码/期初*号）
    }

    /** 基础档案编号：面板代码-最大序号+1（如 EMP-011），COUNT 法在删除/断号后会产生重复编号，改为按最大序号递增并查重 */
    private String archNo(String panelCode) {
        long max = maxSeq(panelCode, panelCode + "-");
        String no;
        do {
            max++;
            no = panelCode + "-" + String.format("%03d", max);
        } while (formMapper.selectCount(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .eq(FormData::getFormNo, no)) > 0);
        return no;
    }

    /** 取面板已有编号中的最大数字序号（解析「前缀-序号」末尾的数字），无则 0 */
    private long maxSeq(String panelCode, String prefix) {
        List<FormData> rows = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .likeRight(FormData::getFormNo, prefix)
                .select(FormData::getFormNo));
        long max = 0;
        for (FormData fd : rows) {
            String no = fd.getFormNo();
            if (no == null) continue;
            int idx = no.lastIndexOf('-');
            String suffix = idx >= 0 ? no.substring(idx + 1) : no;
            try {
                max = Math.max(max, Long.parseLong(suffix.trim()));
            } catch (NumberFormatException ignored) {
            }
        }
        return max;
    }

    private Map<String, Object> parseData(String s) {
        if (s == null || s.isBlank()) return new HashMap<>();
        try {
            return json.readValue(s, new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return new HashMap<>();
        }
    }

    private String toJson(Object o) {
        try {
            return json.writeValueAsString(o == null ? new ArrayList<>() : o);
        } catch (Exception e) {
            return "[]";
        }
    }
}
