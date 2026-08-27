package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import jakarta.annotation.PostConstruct;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Registers quality master data and voucher panels that are not present in the
 * original SQL seed, and upgrades the three captured T+ quality panels without
 * replacing their captured field definitions.
 */
@Component
public class QualityPanelRegistry {

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public QualityPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        upsert("QC_ITEM", "检验项目", "基础档案", archiveConfig("QC_ITEM", "检验项目",
                fields(
                        field("项目编码", "文本", true), field("项目名称", "文本", true),
                        field("检验内容", "文本", true), field("检验标准", "文本", true),
                        select("数据类型", "定性", "定量"), field("计量单位", "文本", false),
                        select("判定规则", "符合标准", "区间判定", "上限判定", "下限判定"),
                        field("标准下限", "小数", false), field("标准上限", "小数", false),
                        bool("停用"), field("备注", "文本", false))), "1.0");

        upsert("QC_PLAN", "检验方案", "基础档案", qualityPlanConfig(), "1.0");
        upsert("COMPANY_TRACE_SETTINGS", "企业移动追溯设置", "设置",
                companyTraceSettingsConfig(), "1.0-live-QT0101");
        upsert("CUSTOMER_TRACE_SETTINGS", "客户移动追溯设置", "设置",
                customerTraceSettingsConfig(), "1.0-live-QT0102");
        upsert("TRACE_PRINT_TEMPLATE", "追溯打印模板设置", "设置",
                tracePrintTemplateConfig(), "1.0-live-QT0103");
        upsert("FIRST_INSPECT", "首件报检单", "单据",
                reportVoucherConfig("FIRST_INSPECT", "首件报检单", "首件报检", "首件检验",
                        selectConfigs(
                                "选生产加工单", selectConfig("MANU_ORDER", "选生产加工单", "products", "数量", "报检数量",
                                        maps("单据编号", "来源单号", "生产车间", "生产车间"),
                                        maps("产品编码", "存货编码", "产品名称", "存货名称", "规格型号", "规格型号", "生产单位", "计量单位", "数量", "报检数量")))), "1.0-menu-baseline");
        upsert("PROCESS_INSPECT_APPLY", "工序报检单", "单据",
                reportVoucherConfig("PROCESS_INSPECT_APPLY", "工序报检单", "工序报检", "工序检验",
                        selectConfigs(
                                "选工序汇报单", selectConfig("PROCESS_REPORT", "选工序汇报单", "items", "报工数量", "报检数量",
                                        maps("单据编号", "来源单号", "生产车间", "生产车间", "加工单号", "加工单号"),
                                        maps("产品编码", "存货编码", "产品名称", "存货名称", "规格型号", "规格型号", "工序编码", "工序编码", "工序名称", "工序名称", "工序单位", "计量单位", "报工数量", "报检数量")),
                                "选工序派工单", selectConfig("DISPATCH", "选工序派工单", "items", "派工数量", "报检数量",
                                        maps("单据编号", "来源单号", "生产车间", "生产车间", "加工单号", "加工单号"),
                                        maps("产品编码", "存货编码", "产品名称", "存货名称", "规格型号", "规格型号", "工序编码", "工序编码", "工序名称", "工序名称", "计量单位", "计量单位", "派工数量", "报检数量")))), "1.0-menu-baseline");
        upsert("PROCESS_INSPECTION", "生产过程检验单", "单据",
                inspectionVoucherConfig("PROCESS_INSPECTION", "生产过程检验单", "生产过程检验",
                        selectConfigs(
                                "选工序报检单", selectConfig("PROCESS_INSPECT_APPLY", "选工序报检单", "items", "报检数量", "报检数量",
                                        maps("单据编号", "来源单号", "生产车间", "生产车间", "加工单号", "加工单号"),
                                        maps("存货编码", "存货编码", "存货名称", "存货名称", "规格型号", "规格型号", "工序编码", "工序编码", "工序名称", "工序名称", "计量单位", "计量单位", "报检数量", "报检数量")))), "1.0-menu-baseline");

        upgradeCapturedPanels();
    }

    private void upgradeCapturedPanels() {
        upgrade("ARRIVAL_IN", config -> {
            Map<String, Object> legacy = map(config.get("selectConfig"));
            enrichSelectConfig(legacy, "数量", "到货数量");
            config.put("selectConfigs", selectConfigs(
                    "选采购订单", legacy,
                    "选检验单", selectConfig("INSPECTION", "选检验单", "items", "检验数量", "到货数量",
                            maps("单据编号", "来源单号", "供应商", "供应商", "仓库", "仓库"),
                            maps("存货编码", "存货编码", "存货名称", "存货名称", "规格型号", "规格型号", "计量单位", "采购单位", "检验数量", "到货数量"))));
            addSourceFields(config);
        });
        upgrade("FINISH_INSPECT", config -> {
            Map<String, Object> legacy = map(config.get("selectConfig"));
            enrichSelectConfig(legacy, "数量", "报检数量");
            config.put("selectConfigs", selectConfigs(
                    "选生产加工单", legacy,
                    "选委外加工单", selectConfig("OUTSOURCE_ORDER", "选委外加工单", "products", "数量", "报检数量",
                            maps("单据编号", "加工单号", "委外供应商", "委外供应商"),
                            maps("产品编码", "存货编码", "产品名称", "存货名称", "规格型号", "规格型号", "计量单位", "计量单位", "数量", "报检数量")),
                    "选检验单", selectConfig("INSPECTION", "选检验单", "items", "检验数量", "报检数量",
                            maps("单据编号", "来源单号"),
                            maps("存货编码", "存货编码", "存货名称", "存货名称", "规格型号", "规格型号", "计量单位", "计量单位", "检验数量", "报检数量"))));
            addSourceFields(config);
        });
        upgrade("INSPECTION", config -> {
            Map<String, Object> legacy = map(config.get("selectConfig"));
            enrichSelectConfig(legacy, "到货数量", "报检数量");
            config.put("selectConfigs", selectConfigs(
                    "选到货单", legacy,
                    "选成品报检单", selectConfig("FINISH_INSPECT", "选成品报检单", "items", "报检数量", "报检数量",
                            maps("单据编号", "来源单号", "生产车间", "部门"),
                            maps("存货编码", "存货编码", "存货名称", "存货名称", "规格型号", "规格型号", "计量单位", "计量单位", "报检数量", "报检数量"))));
            addSourceFields(config);
            upgradeInspectionReferences(config);
            addUnqualifiedTab(config);
        });
    }

    @SuppressWarnings("unchecked")
    private void addSourceFields(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        List<Map<String, Object>> fields = listOfMaps(tabs.get(0).get("fields"));
        for (String name : List.of("来源面板", "来源单号", "来源行号", "来源数量", "累计执行数量", "累计检验数量", "累计入库数量")) {
            if (fields.stream().noneMatch(f -> name.equals(f.get("dataName")))) {
                Map<String, Object> sourceField = field(name, name.contains("数量") ? "小数" : "文本", false);
                sourceField.put("hidden", true);
                fields.add(sourceField);
            }
        }
        tabs.get(0).put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    private void addUnqualifiedTab(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.stream().anyMatch(tab -> "unqualified".equals(tab.get("key")))) return;
        List<Map<String, Object>> fields = fields(
                field("关联明细行号", "整数", true), field("存货编码", "文本", false), field("存货名称", "文本", true),
                reference("不合格原因", "REJECT", "不合格原因"),
                select("处理方式", "退回", "返工", "让步接收", "报废"),
                field("处理数量", "小数", true), select("报废类型", "工废", "料废", "其他"),
                reference("仓库", "WH", "仓库名称"), field("处理说明", "文本", false));
        tabs.add(tab("unqualified", "不合格处理", fields, "处理数量"));
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    private void upgradeInspectionReferences(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        List<Map<String, Object>> fields = listOfMaps(tabs.get(0).get("fields"));
        boolean hasPlan = false;
        for (Map<String, Object> field : fields) {
            String name = String.valueOf(field.get("dataName"));
            if ("检验方案".equals(name)) hasPlan = true;
            if ("检验项目".equals(name)) {
                field.putAll(reference("检验项目", "QC_ITEM", "项目名称"));
            }
        }
        if (!hasPlan) fields.add(reference("检验方案", "QC_PLAN", "方案名称"));
        tabs.get(0).put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    private Map<String, Object> qualityPlanConfig() {
        List<Map<String, Object>> head = fields(
                field("方案编码", "文本", true), field("方案名称", "文本", true),
                reference("适用存货", "INV", "存货名称"), field("适用存货类别", "文本", false),
                select("检验方式", "全检", "抽检"), field("抽检比例%", "小数", false),
                bool("停用"), field("备注", "文本", false));
        List<Map<String, Object>> items = fields(
                reference("检验项目", "QC_ITEM", "项目名称"), field("检验内容", "文本", false),
                field("检验标准", "文本", true), field("标准下限", "小数", false),
                field("标准上限", "小数", false), select("判定规则", "符合标准", "区间判定", "上限判定", "下限判定"),
                field("必检", "是否", false));
        return config("QC_PLAN", "检验方案", "基础档案", head, List.of(tab("items", "检验项目", items, null)),
                List.of(group("新增", "新增"), group("保存", "保存"), group("删除", "删除"), group("更多", "刷新")), Map.of());
    }

    private Map<String, Object> companyTraceSettingsConfig() {
        List<Map<String, Object>> head = fields(
                defaultValue(field("设置名称", "文本", true), "默认移动追溯设置"),
                boolDefault("启用产品正向追溯", true), boolDefault("产品基本信息", true),
                boolDefault("材料信息", true), boolDefault("产品检验信息", true),
                boolDefault("生产过程信息", true), boolDefault("销售出库信息", true),
                boolDefault("产品库存信息", true), boolDefault("启用材料反向追溯", true),
                boolDefault("材料基本信息", true), boolDefault("产品信息", true),
                boolDefault("材料检验信息", true), boolDefault("材料出库信息", true),
                boolDefault("材料库存信息", true), field("备注", "文本", false));
        return settingsConfig("COMPANY_TRACE_SETTINGS", "企业移动追溯设置", "QT0101", head, List.of(), true,
                List.of(group("新增", "新增"), group("保存", "保存"), group("更多", "刷新")));
    }

    private Map<String, Object> customerTraceSettingsConfig() {
        List<Map<String, Object>> head = fields(
                defaultValue(field("设置名称", "文本", true), "默认客户追溯模板"),
                defaultValue(field("追溯模板标题", "文本", true), "产品质量追溯"),
                boolDefault("产品基本信息", true), boolDefault("材料信息", true),
                boolDefault("产品检验信息", true), boolDefault("检验项目信息", true),
                boolDefault("生产过程信息", true), boolDefault("销售出库信息", true),
                boolDefault("产品库存信息", true), boolDefault("企业信息", true),
                field("企业联系电话", "文本", false), field("备注", "文本", false));
        return settingsConfig("CUSTOMER_TRACE_SETTINGS", "客户移动追溯设置", "QT0102", head, List.of(), true,
                List.of(group("新增", "新增"), group("保存", "保存"), group("更多", "刷新")));
    }

    private Map<String, Object> tracePrintTemplateConfig() {
        List<Map<String, Object>> head = fields(
                field("模板名称", "文本", true), select("追溯类型", "产品正向追溯", "材料反向追溯"),
                select("纸张大小", "A4", "A5", "自定义"), select("纸张方向", "纵向", "横向"),
                select("默认字体", "宋体", "微软雅黑", "黑体"), defaultValue(field("默认字号", "整数", true), 10),
                select("默认边框", "无边框", "实线", "虚线"), select("默认文本样式", "常规", "粗体", "斜体", "下划线"),
                select("字体方向", "横向", "纵向"), defaultValue(field("默认行高", "小数", false), 1.5),
                select("布局", "自由布局", "网格布局"), field("备注", "文本", false));
        List<Map<String, Object>> controls = fields(
                select("控件类型", "直线", "矩形", "静态文本", "文本框", "图片", "明细"),
                field("字段名称", "文本", false), field("显示文本", "文本", false),
                field("X坐标", "整数", true), field("Y坐标", "整数", true),
                defaultValue(field("宽度", "整数", true), 120), defaultValue(field("高度", "整数", true), 24),
                select("字体", "宋体", "微软雅黑", "黑体"), defaultValue(field("字号", "整数", false), 10),
                select("边框", "无边框", "实线", "虚线"), select("文本样式", "常规", "粗体", "斜体", "下划线"),
                select("字体方向", "横向", "纵向"), defaultValue(field("行高", "小数", false), 1.5));
        return settingsConfig("TRACE_PRINT_TEMPLATE", "追溯打印模板设置", "QT0103", head,
                List.of(tab("controls", "打印控件", controls, null)), false,
                List.of(group("新建", "新建"), group("保存", "保存"), group("恢复", "恢复"), group("预览", "预览")));
    }

    private Map<String, Object> settingsConfig(String code, String name, String tplusCode,
                                                List<Map<String, Object>> head, List<Map<String, Object>> tabs,
                                                boolean singleDoc, List<Map<String, Object>> groups) {
        Map<String, Object> cfg = config(code, name, "设置", head, tabs, groups, Map.of());
        Map<String, Object> metadata = map(cfg.get("metadata"));
        metadata.put("singleDoc", singleDoc);
        metadata.put("tplusCode", tplusCode);
        metadata.put("evidenceLevel", "2026-08-26 T+机械行业真实页面取证");
        cfg.put("metadata", metadata);
        return cfg;
    }

    private Map<String, Object> archiveConfig(String code, String name, List<Map<String, Object>> head) {
        return config(code, name, "基础档案", head, List.of(),
                List.of(group("新增", "新增"), group("保存", "保存"), group("删除", "删除"), group("更多", "刷新")), Map.of());
    }

    private Map<String, Object> reportVoucherConfig(String code, String name, String businessType,
                                                     String inspectCategory, Map<String, Object> selects) {
        List<Map<String, Object>> head = voucherHead(businessType);
        List<Map<String, Object>> items = reportFields();
        List<Map<String, Object>> groups = voucherGroups(new ArrayList<>(selects.keySet()), "生成检验单");
        return config(code, name, "单据", head, List.of(tab("items", "明细", items, "报检数量")), groups, selects);
    }

    private Map<String, Object> inspectionVoucherConfig(String code, String name, String businessType,
                                                         Map<String, Object> selects) {
        List<Map<String, Object>> head = voucherHead(businessType);
        List<Map<String, Object>> items = inspectionFields();
        List<Map<String, Object>> groups = voucherGroups(new ArrayList<>(selects.keySet()), "生成产成品入库单");
        Map<String, Object> cfg = config(code, name, "单据", head, List.of(tab("items", "明细", items, "检验数量")), groups, selects);
        addUnqualifiedTab(cfg);
        return cfg;
    }

    private List<Map<String, Object>> voucherHead(String businessType) {
        List<Map<String, Object>> out = fields(
                date("单据日期"), autoCode(), selectDefault("业务类型", businessType, businessType),
                field("生产车间", "文本", false), field("加工单号", "文本", false),
                reference("检验员", "EMP", "员工名称"), reference("部门", "DEPT", "部门名称"),
                field("来源单号", "文本", false), field("备注", "文本", false));
        return out;
    }

    private List<Map<String, Object>> reportFields() {
        List<Map<String, Object>> out = fields(
                field("存货编码", "文本", false), reference("存货名称", "INV", "存货名称"), field("规格型号", "文本", false),
                field("工序编码", "文本", false), field("工序名称", "文本", false), field("计量单位", "文本", false),
                field("报检数量", "小数", true), field("累计检验数量", "小数", false), field("备注", "文本", false));
        addSourceFieldsTo(out);
        return out;
    }

    private List<Map<String, Object>> inspectionFields() {
        List<Map<String, Object>> out = fields(
                field("存货编码", "文本", false), reference("存货名称", "INV", "存货名称"), field("规格型号", "文本", false),
                field("工序编码", "文本", false), field("工序名称", "文本", false), field("计量单位", "文本", false),
                select("检验方式", "全检", "抽检"), field("报检数量", "小数", true), field("检验数量", "小数", true),
                field("合格数量", "小数", true), field("不合格数量", "小数", true),
                select("检验结果判定", "合格", "不合格", "让步接收", "报废"),
                reference("检验方案", "QC_PLAN", "方案名称"), reference("检验项目", "QC_ITEM", "项目名称"),
                reference("检验员", "EMP", "员工名称"), date("检验日期"));
        addSourceFieldsTo(out);
        return out;
    }

    private void addSourceFieldsTo(List<Map<String, Object>> fields) {
        for (String name : List.of("来源面板", "来源单号", "来源行号", "来源数量", "累计执行数量", "累计检验数量", "累计入库数量")) {
            Map<String, Object> field = field(name, name.contains("数量") ? "小数" : "文本", false);
            field.put("hidden", true);
            fields.add(field);
        }
    }

    private List<Map<String, Object>> voucherGroups(List<String> selectActions, String generateAction) {
        return List.of(
                group("新增", "新增"), group("选单", selectActions.toArray(String[]::new)),
                group("保存", "保存", "保存新增", "保存为草稿"), group("删除", "删除", "删除单据"),
                group("审核", "审核", "弃审"), group("生单", generateAction),
                group("工具", "生单流程联查", "执行情况"), group("更多", "刷新", "放弃"));
    }

    private Map<String, Object> config(String code, String name, String category, List<Map<String, Object>> head,
                                       List<Map<String, Object>> tabs, List<Map<String, Object>> groups,
                                       Map<String, Object> selects) {
        List<Map<String, Object>> buttons = new ArrayList<>();
        for (Map<String, Object> group : groups) {
            Object actions = group.get("actions");
            if (actions instanceof List<?> list) for (Object action : list) buttons.add(Map.of("buttonName", action));
        }
        List<String> columns = tabs.isEmpty() ? head.stream().map(f -> String.valueOf(f.get("dataName"))).toList()
                : listOfMaps(tabs.get(0).get("fields")).stream().filter(f -> !Boolean.TRUE.equals(f.get("hidden")))
                .map(f -> String.valueOf(f.get("dataName"))).toList();
        Map<String, Object> table = new LinkedHashMap<>();
        table.put("tableName", name + "列表");
        table.put("queryFields", head.stream().limit(6).toList());
        table.put("gridTabs", List.of(Map.of("label", "明细", "rowSource", tabs.isEmpty() ? "head" : "items", "columns", columns)));
        table.put("topBarBtn", buttons);
        table.put("rowOperationBarBtn", List.of());
        table.put("events", List.of());
        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", code);
        metadata.put("panelName", name);
        metadata.put("panelCategory", category);
        metadata.put("evidenceLevel", code.startsWith("QC_") ? "T+菜单与档案语义" : "T+菜单级证据，字段待实页复核");
        boolean effectiveOnSave = "基础档案".equals(category) || "设置".equals(category);
        if (!effectiveOnSave) metadata.put("autoCodeField", "单据编号");
        metadata.put("panelState", Map.of("dataName", "单据状态", "dataType", "STRING",
                "defaultOptions", effectiveOnSave ? List.of("启用", "停用") : List.of("草稿", "已审核", "已中止")));
        metadata.put("panelPageDto", Map.of("tablePages", List.of(table), "formPages", List.of(Map.of(
                "formName", name, "fieldNames", String.join(",", head.stream().map(f -> String.valueOf(f.get("dataName"))).toList()),
                "bottomOperationBarBtn", buttons, "events", List.of()))));
        metadata.put("panelButtons", buttons);
        metadata.put("buttonGroups", groups);
        metadata.put("version", "1.0");
        Map<String, Object> cfg = new LinkedHashMap<>();
        cfg.put("metadata", metadata);
        cfg.put("dataSchema", Map.of("type", "object", "fields", head));
        cfg.put("detail", Map.of("tabs", tabs));
        if (!selects.isEmpty()) {
            cfg.put("selectConfigs", selects);
            cfg.put("selectConfig", selects.values().iterator().next());
        }
        return cfg;
    }

    private Map<String, Object> selectConfig(String source, String title, String detailKey,
                                             String sourceQuantityField, String targetQuantityField,
                                             List<Map<String, String>> headerMap, List<Map<String, String>> detailMap) {
        Map<String, Object> cfg = new LinkedHashMap<>();
        cfg.put("source", source);
        cfg.put("title", title);
        cfg.put("tip", "仅显示已审核来源单据；带入时保留来源单号和明细行号，并校验重复及超量执行");
        cfg.put("detailKey", detailKey);
        cfg.put("sourceQuantityField", sourceQuantityField);
        cfg.put("targetQuantityField", targetQuantityField);
        cfg.put("columns", List.of("单据编号", "单据日期", "存货名称", sourceQuantityField));
        cfg.put("headerMap", headerMap);
        cfg.put("detailMap", detailMap);
        return cfg;
    }

    private void enrichSelectConfig(Map<String, Object> cfg, String sourceQty, String targetQty) {
        if (cfg.isEmpty()) return;
        cfg.put("sourceQuantityField", sourceQty);
        cfg.put("targetQuantityField", targetQty);
    }

    private void upgrade(String code, java.util.function.Consumer<Map<String, Object>> mutator) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>().eq(PanelConfig::getPanelCode, code));
        if (row == null || row.getConfig() == null) return;
        try {
            Map<String, Object> config = json.readValue(row.getConfig(), new TypeReference<>() {});
            mutator.accept(config);
            row.setConfig(json.writeValueAsString(config));
            row.setVersion("quality-flow-1.0");
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception e) {
            throw new IllegalStateException("质量面板配置升级失败：" + code, e);
        }
    }

    private void upsert(String code, String name, String category, Map<String, Object> config, String version) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>().eq(PanelConfig::getPanelCode, code));
        if (row == null) {
            row = new PanelConfig();
            row.setPanelCode(code);
            row.setCreateTime(LocalDateTime.now());
        }
        row.setPanelName(name);
        row.setCategory(category);
        row.setConfig(toJson(config));
        row.setVersion(version);
        row.setUpdateTime(LocalDateTime.now());
        if (row.getId() == null) panelMapper.insert(row); else panelMapper.updateById(row);
    }

    private String toJson(Object value) {
        try { return json.writeValueAsString(value); }
        catch (Exception e) { throw new IllegalStateException("质量面板配置序列化失败", e); }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> ? new LinkedHashMap<>((Map<String, Object>) value) : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOfMaps(Object value) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (value instanceof List<?> list) for (Object item : list) if (item instanceof Map<?, ?>) out.add(new LinkedHashMap<>((Map<String, Object>) item));
        return out;
    }

    @SafeVarargs
    private static List<Map<String, Object>> fields(Map<String, Object>... values) { return new ArrayList<>(Arrays.asList(values)); }

    private static Map<String, Object> field(String name, String type, boolean required) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name); field.put("dataType", type); field.put("isRequired", required);
        field.put("defaultValue", type.equals("小数") || type.equals("整数") ? 0 : "");
        return field;
    }

    private static Map<String, Object> defaultValue(Map<String, Object> field, Object value) {
        field.put("defaultValue", value);
        return field;
    }

    private static Map<String, Object> autoCode() {
        Map<String, Object> field = field("单据编号", "文本", true); field.put("autoCode", true); return field;
    }

    private static Map<String, Object> date(String name) { return field(name, "日期", true); }
    private static Map<String, Object> bool(String name) { Map<String, Object> f = field(name, "是否", false); f.put("defaultValue", false); return f; }
    private static Map<String, Object> boolDefault(String name, boolean value) { return defaultValue(bool(name), value); }

    private static Map<String, Object> select(String name, String... options) {
        Map<String, Object> field = field(name, "下拉框", true); field.put("options", Arrays.asList(options));
        if (options.length > 0) field.put("defaultValue", options[0]); return field;
    }

    private static Map<String, Object> selectDefault(String name, String defaultValue, String... options) {
        Map<String, Object> field = select(name, options); field.put("defaultValue", defaultValue); return field;
    }

    private static Map<String, Object> reference(String name, String panel, String refField) {
        Map<String, Object> field = field(name, "参照", false);
        field.put("refPanel", panel); field.put("refField", refField); field.put("displayField", refField);
        field.put("filter", Map.of("停用", false)); return field;
    }

    private static Map<String, Object> tab(String key, String label, List<Map<String, Object>> fields, String summaryField) {
        Map<String, Object> tab = new LinkedHashMap<>(); tab.put("key", key); tab.put("label", label); tab.put("fields", fields);
        if (summaryField != null) tab.put("summaryItems", List.of(Map.of("label", summaryField + "合计", "field", summaryField)));
        return tab;
    }

    private static Map<String, Object> group(String name, String... actions) { return Map.of("name", name, "actions", Arrays.asList(actions)); }

    private static Map<String, Object> selectConfigs(Object... values) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i + 1 < values.length; i += 2) if (values[i + 1] instanceof Map<?, ?>) out.put(String.valueOf(values[i]), values[i + 1]);
        return out;
    }

    private static List<Map<String, String>> maps(String... values) {
        List<Map<String, String>> out = new ArrayList<>();
        for (int i = 0; i + 1 < values.length; i += 2) out.add(Map.of("from", values[i], "to", values[i + 1]));
        return out;
    }
}
