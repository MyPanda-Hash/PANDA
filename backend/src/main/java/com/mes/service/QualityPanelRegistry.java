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
                companyTraceSettingsConfig(), "QT0101");
        upsert("CUSTOMER_TRACE_SETTINGS", "客户移动追溯设置", "设置",
                customerTraceSettingsConfig(), "QT0102");
        upsert("TRACE_PRINT_TEMPLATE", "追溯打印模板设置", "设置",
                tracePrintTemplateConfig(), "QT0103");
        upsert("FIRST_INSPECT", "首件报检单", "单据", firstInspectConfig(), "MB1.0");
        upsert("PROCESS_INSPECT_APPLY", "工序报检单", "单据",
                reportVoucherConfig("PROCESS_INSPECT_APPLY", "工序报检单", "工序报检", "工序检验",
                        selectConfigs(
                                "选工序汇报单", selectConfig("PROCESS_REPORT", "选工序汇报单", "items", "报工数量", "报检数量",
                                        maps("单据编号", "来源单号", "生产车间", "生产车间", "加工单号", "加工单号"),
                                        maps("产品编码", "存货编码", "产品名称", "存货名称", "规格型号", "规格型号", "工序编码", "工序编码", "工序名称", "工序名称", "工序单位", "计量单位", "报工数量", "报检数量")),
                                "选工序派工单", selectConfig("DISPATCH", "选工序派工单", "items", "派工数量", "报检数量",
                                        maps("单据编号", "来源单号", "生产车间", "生产车间", "加工单号", "加工单号"),
                                        maps("产品编码", "存货编码", "产品名称", "存货名称", "规格型号", "规格型号", "工序编码", "工序编码", "工序名称", "工序名称", "计量单位", "计量单位", "派工数量", "报检数量")))), "MB1.0");
        upsert("PROCESS_INSPECTION", "生产过程检验单", "单据",
                inspectionVoucherConfig("PROCESS_INSPECTION", "生产过程检验单", "生产过程检验",
                        selectConfigs(
                                "选工序报检单", selectConfig("PROCESS_INSPECT_APPLY", "选工序报检单", "items", "报检数量", "报检数量",
                                        maps("单据编号", "来源单号", "生产车间", "生产车间", "加工单号", "加工单号"),
                                        maps("存货编码", "存货编码", "存货名称", "存货名称", "规格型号", "规格型号", "工序编码", "工序编码", "工序名称", "工序名称", "计量单位", "计量单位", "报检数量", "报检数量")))), "MB1.0");

        upgradeCapturedPanels();
    }

    private void upgradeCapturedPanels() {
        upgrade("ARRIVAL_IN", QualityPanelRegistry::upgradeArrivalIn);
        upgrade("FINISH_INSPECT", QualityPanelRegistry::upgradeFinishInspect);
        upgrade("INSPECTION", QualityPanelRegistry::upgradeInspection);
    }

    static void upgradeArrivalIn(Map<String, Object> config) {
        Map<String, Object> legacy = map(config.get("selectConfig"));
        enrichSelectConfig(legacy, "数量", "到货数量");
        ensureHeaderMap(legacy, "单据编号", "采购订单号");
        if (!legacy.isEmpty()) {
            legacy.putIfAbsent("generateButton", "生成到货单");
            legacy.putIfAbsent("generateLabel", "生成到货单");
            config.put("selectConfig", legacy);
        }
        config.put("selectConfigs", selectConfigs(
                "选采购订单", legacy,
                "选检验单", selectConfig("INSPECTION", "选检验单", "items", "检验数量", "到货数量",
                        maps("单据编号", "来源单号", "供应商", "供应商", "仓库", "仓库"),
                        maps("存货编码", "存货编码", "存货名称", "存货名称", "规格型号", "规格型号", "计量单位", "采购单位", "检验数量", "到货数量"))));
        addSourceFields(config);
        for (String name : List.of("检验数量", "合格数量", "不合格数量", "让步接收数量", "报废数量",
                "累计入库数量", "累计退货数量")) {
            ensureDetailField(config, name, "小数", true, true);
        }
        for (String name : List.of("累计执行数量", "累计检验数量")) {
            ensureDetailField(config, name, "小数", true, false);
        }
        ensureHeadField(config, "采购订单号", "文本", null, null);
        ensureHeadField(config, "数据来源", "文本", null, null);
        ensureHeadField(config, "检验状态", "下拉框", List.of("未检", "部分检", "已检"), "未检");
        for (String name : List.of("采购订单号", "检验状态", "数据来源")) ensureFormField(config, name);
        ensureAction(config, "修改", "中止");
        ensureAction(config, "修改", "取消中止");
    }

    static void upgradeFinishInspect(Map<String, Object> config) {
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
        // T+ QM10 表头 + 开源 MES 常用报检字段（保持当前表格形态）
        ensureHeadReference(config, "委外供应商", "PARTNER", "往来单位名称");
        ensureHeadReference(config, "部门", "DEPT", "部门名称");
        ensureHeadReference(config, "负责人", "EMP", "员工名称");
        ensureHeadField(config, "供应商编码", "文本", null, "");
        for (String name : List.of("供应商编码", "委外供应商", "部门", "负责人")) ensureFormField(config, name);
        // 明细：BOM/库存供应商/价格/抽检/累计质量结果/不合格原因/检验方案
        ensureDetailField(config, "BOM版本号", "文本", false, true);
        ensureColumnAfter(config, "规格型号", "BOM版本号");
        ensureDetailField(config, "供应商(库存)", "文本", false, true);
        ensureColumnAfter(config, "BOM版本号", "供应商(库存)");
        for (String name : List.of("单价", "含税单价", "金额", "税额", "含税金额")) {
            ensureDetailField(config, name, "小数", false, true);
        }
        ensureDetailField(config, "税率%", "小数", false, true);
        String[] priceOrder = {"报检数量", "单价", "税率%", "含税单价", "金额", "税额", "含税金额"};
        for (int index = 1; index < priceOrder.length; index++) {
            ensureColumnAfter(config, priceOrder[index - 1], priceOrder[index]);
        }
        ensureDetailField(config, "抽检数量", "小数", false, true);
        ensureColumnAfter(config, "抽检比例%", "抽检数量");
        ensureDetailField(config, "最大不合格数", "整数", false, true);
        ensureColumnAfter(config, "抽检数量", "最大不合格数");
        String[] cumulative = {"累计检验数量", "累计合格数量", "累计验收退回数量", "累计让步接收数量",
                "累计报废数量", "累计入库数量", "累计已退数量"};
        for (String name : cumulative) ensureDetailField(config, name, "小数", true, true);
        String previous = "检验要求";
        for (String name : cumulative) {
            ensureColumnAfter(config, previous, name);
            previous = name;
        }
        ensureDetailReference(config, "不合格原因", "REJECT", "不合格原因");
        ensureColumnAfter(config, "累计已退数量", "不合格原因");
        ensureDetailReference(config, "检验方案", "QC_PLAN", "方案名称");
        ensureColumnAfter(config, "不合格原因", "检验方案");
    }

    static void upgradeInspection(Map<String, Object> config) {
        Map<String, Object> legacy = map(config.get("selectConfig"));
        // 选到货单：拒收数量不送检，报检数量取实收；检验方式/抽检比例/编码/仓库必须带全，
        // 否则拉式选单生成的检验行缺存货编码，后续入库取不到价也扣不到库存。
        enrichSelectConfig(legacy, "实收数量", "报检数量");
        retargetDetailMap(legacy, "报检数量", "实收数量", "到货数量");
        retargetDetailMap(legacy, "计量单位", "采购单位", "计量单位");
        ensureHeaderMap(legacy, "供应商", "供应商");
        ensureHeaderMap(legacy, "仓库", "仓库");
        ensureHeaderMap(legacy, "采购订单号", "采购订单号");
        for (String[] mapping : List.of(
                new String[]{"实收数量", "报检数量"},
                new String[]{"存货编码", "存货编码"}, new String[]{"规格型号", "规格型号"},
                new String[]{"仓库", "仓库"}, new String[]{"检验方式", "检验方式"},
                new String[]{"抽检比例%", "抽检比例%"}, new String[]{"检验数量", "检验数量"})) {
            ensureDetailMap(legacy, mapping[0], mapping[1]);
        }
        if (!legacy.isEmpty()) {
            legacy.put("columns", List.of("单据编号", "单据日期", "供应商", "存货名称",
                    "实收数量", "检验方式", "抽检比例%"));
            // 选到货单直接走后端「生成检验单」：免检跳过、来源戳、检验方式/抽检比例一次带全，
            // 不再由客户端按字段映射拼一张不完整的检验草稿。
            legacy.putIfAbsent("generateButton", "生成检验单");
            legacy.putIfAbsent("generateLabel", "生成检验单");
            config.put("selectConfig", legacy);
        }
        // 检验单只从到货单来：来料与委外回厂都是到货单收回公司；
        // 成品检验由成品报检单自己承载（报检单自带检验结论），不再提供「选成品报检单」入口
        config.put("selectConfigs", selectConfigs("选到货单", legacy));
        // 「选单」下拉是从 buttonGroups 渲染的，光改 selectConfigs 不够，按钮也要摘掉
        removeAction(config, "选单", "选成品报检单");
        addSourceFields(config);
        upgradeInspectionReferences(config);
        addUnqualifiedTab(config);
        addProjectTab(config);
        // T+ QM15 表头（保持当前表格形态，不新增页签）
        ensureHeadReference(config, "检验部门", "DEPT", "部门名称");
        ensureHeadField(config, "采购订单号", "文本", null, "");
        ensureHeadField(config, "加工单号", "文本", null, "");
        // 来源类型跟随到货单：采购到货的检验单只能转采购入库，委外回厂只能转委外入库
        ensureHeadField(config, "来源类型", "下拉框", List.of("采购", "委外"), "采购");
        for (String name : List.of("检验部门", "采购订单号", "加工单号", "来源类型")) ensureFormField(config, name);
        // T+ QM15 明细字段：应检数量、验收退回、行级不合格原因、让步接收含税单价、样本记录
        ensureDetailField(config, "应检数量", "小数", false, true);
        ensureColumnAfter(config, "报检数量", "应检数量");
        ensureDetailField(config, "验收退回数量", "小数", true, true);
        ensureColumnAfter(config, "不合格数量", "验收退回数量");
        ensureDetailReference(config, "不合格原因", "REJECT", "不合格原因");
        ensureColumnAfter(config, "报废数量", "不合格原因");
        ensureDetailField(config, "让步接收含税单价", "小数", false, true);
        ensureColumnAfter(config, "不合格原因", "让步接收含税单价");
        // 原价/原含税单价是折价与让步结算的底数，只存不占列
        ensureDetailField(config, "单价", "小数", false, false);
        ensureDetailField(config, "含税单价", "小数", false, false);
        ensureDetailField(config, "记录样本明细", "是否", false, true);
        ensureDetailField(config, "样本明细个数", "整数", false, true);
        ensureColumnAfter(config, "检验日期", "检验方案");
        ensureColumnAfter(config, "检验方案", "记录样本明细");
        ensureColumnAfter(config, "记录样本明细", "样本明细个数");
        setDetailDefault(config, "记录样本明细", false);
        setDetailDefault(config, "样本明细个数", 0);
        // 生单：判定合格 → 采购入库 / 委外入库；判定退回 → 采购退货。
        // 检验单不做产成品入库（成品走成品报检单），成品报检退回单也不属于来料/委外这条线
        ensureAction(config, "生单", "生成采购入库单");
        ensureAction(config, "生单", "生成委外入库单");
        ensureAction(config, "生单", "生成采购退货单");
        removeAction(config, "生单", "生成产成品入库单");
        removeAction(config, "生单", "生成成品报检退回单（生产报检）");
        removeAction(config, "生单", "生成成品报检退回单（委外报检）");
        // 到货退回单没有对应的目标单据/路由，T+ 抓取配置里的这个入口在运行库中一并摘掉
        removeAction(config, "生单", "生成到货退回单");
        // 业务类型/检验类别只留来料与委外：成品检验在成品报检单上，不在这张单
        ensureHeadField(config, "业务类型", "下拉框", List.of("来料检验", "委外检验"), "来料检验");
        ensureHeadField(config, "检验类别", "下拉框", List.of("来料检验", "委外检验"), "来料检验");
        for (String name : List.of("让步接收数量", "报废数量", "判定退货数量")) {
            ensureDetailField(config, name, "小数", true, true);
        }
        // 批次处置标记（2026-11-05）：质量处置 + 商务结算分层，确认后才允许生单
        ensureDispositionFields(config);
        setDetailOptions(config, "检验结果判定", List.of("合格", "让步接收", "部分报废", "全批报废", "全批退回"));
        // 判定退货数量保留为内部兼容字段，不再占主表列（对外显示验收退回数量）
        ensureDetailField(config, "判定退货数量", "小数", true, false);
        removeColumn(config, "判定退货数量");
        ensureDetailField(config, "返工数量", "小数", true, false);
        ensureAction(config, "处置", "确认处置");
        ensureAction(config, "修改", "中止");
        ensureAction(config, "修改", "取消中止");
    }

    /** 检验明细的批次处置字段（幂等：已有字段补属性，不覆盖管理员配置）。 */
    private static void ensureDispositionFields(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        Map<String, Object> tab = tabs.stream().filter(t -> "items".equals(t.get("key"))).findFirst()
                .orElse(tabs.get(0));
        List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
        mergeField(fields, optionalSelect("处置方式",
                "合格接收", "让步接收", "降价接收", "部分退回", "全批退回", "返工", "返修", "挑选接收", "报废"));
        mergeField(fields, field("处置数量", "小数", false));
        mergeField(fields, optionalSelect("结算方式", "原价", "折价", "索赔", "免费补货", "退款"));
        mergeField(fields, field("折价率%", "小数", false));
        mergeField(fields, computedField("折价金额"));
        mergeField(fields, computedField("调整后单价"));
        mergeField(fields, computedField("调整后含税单价"));
        mergeField(fields, optionalSelect("责任归属", "供应商", "运输", "我方", "待定"));
        mergeField(fields, defaultValue(optionalSelect("处置状态",
                "待确认", "已确认", "已执行", "已结算", "已结案"), "待确认"));
        mergeField(fields, bool("复检要求"));
        mergeField(fields, field("复检结果", "文本", false));
        tab.put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    private static Map<String, Object> optionalSelect(String name, String... options) {
        Map<String, Object> field = field(name, "下拉框", false);
        field.put("options", Arrays.asList(options));
        if (options.length > 0) field.put("defaultValue", options[0]);
        return field;
    }

    private static Map<String, Object> computedField(String name) {
        Map<String, Object> field = field(name, "小数", false);
        field.put("computed", true);
        return field;
    }

    /** 按 dataName 合并字段：缺失追加；既有字段只补缺失属性，选项/默认值/计算标记以定义为准。 */
    private static void mergeField(List<Map<String, Object>> fields, Map<String, Object> definition) {
        String name = String.valueOf(definition.get("dataName"));
        Map<String, Object> target = fields.stream()
                .filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
        if (target == null) {
            fields.add(new LinkedHashMap<>(definition));
            return;
        }
        for (Map.Entry<String, Object> entry : definition.entrySet()) {
            if (!target.containsKey(entry.getKey())) target.put(entry.getKey(), entry.getValue());
        }
        if (definition.containsKey("options")) target.put("options", definition.get("options"));
        if (definition.containsKey("defaultValue")) target.put("defaultValue", definition.get("defaultValue"));
        if (definition.containsKey("computed")) target.put("computed", definition.get("computed"));
    }

    @SuppressWarnings("unchecked")
    private static void addSourceFields(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        List<Map<String, Object>> fields = listOfMaps(tabs.get(0).get("fields"));
        addSourceFieldsTo(fields);
        tabs.get(0).put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    /** 检验项目结果页签：按检验方案逐项记录实测值/单项判定（对齐主流检验单的检测项结果区）。 */
    private static void addProjectTab(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.stream().anyMatch(tab -> "projects".equals(tab.get("key")))) return;
        List<Map<String, Object>> fields = fields(
                field("关联明细行号", "整数", true),
                field("检验项目编码", "文本", false),
                reference("检验项目", "QC_ITEM", "项目名称"),
                field("检验内容", "文本", false),
                field("检验标准", "文本", false),
                field("标准下限", "小数", false),
                field("标准上限", "小数", false),
                field("检验结果", "文本", false),
                select("单项判定", "合格", "不合格"),
                field("检验时间", "日期", false),
                reference("检验员", "EMP", "员工名称"),
                field("备注", "文本", false));
        int at = 0;
        for (int index = 0; index < tabs.size(); index++) {
            if ("items".equals(tabs.get(index).get("key"))) {
                at = index + 1;
                break;
            }
        }
        tabs.add(at, tab("projects", "检验项目", fields, null));
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    private static void addUnqualifiedTab(Map<String, Object> config) {
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

    private static void upgradeInspectionReferences(Map<String, Object> config) {
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

    /**
     * 首件报检单（对齐 T+ QM07 + 开源 MES 首件/首检字段）：
     * 来源工序汇报单，明细带工序/加工类型/班组/工人/检验方式/抽检数量/累计结果，保持当前表格式。
     */
    private Map<String, Object> firstInspectConfig() {
        List<Map<String, Object>> head = fields(
                date("单据日期"), autoCode(), selectDefault("业务类型", "首件报检", "首件报检"),
                reference("委外供应商", "PARTNER", "往来单位名称"),
                reference("部门", "DEPT", "部门名称"), reference("负责人", "EMP", "员工名称"),
                reference("检验员", "EMP", "员工名称"),
                field("生产车间", "文本", false), field("加工单号", "文本", false),
                field("来源单号", "文本", false), field("备注", "文本", false));
        List<Map<String, Object>> items = firstInspectFields();
        Map<String, Object> select = selectConfig("PROCESS_REPORT", "选工序汇报单", "items", "报工数量", "报检数量",
                maps("单据编号", "来源单号", "生产车间", "生产车间", "加工单号", "加工单号", "部门", "部门"),
                maps("产品编码", "存货编码", "产品名称", "存货名称", "规格型号", "规格型号",
                        "BOM版本号", "BOM版本号", "工序编码", "工序编码", "工序名称", "工序名称",
                        "工序单位", "计量单位", "报工数量", "报检数量",
                        "加工单号", "加工单号", "单据编号", "工序汇报单号",
                        "加工类型", "加工类型", "生产车间", "生产车间"));
        select.put("columns", List.of("单据编号", "单据日期", "生产车间", "加工单号",
                "工序编码", "工序名称", "报工数量"));
        Map<String, Object> selects = selectConfigs("选工序汇报单", select);
        // 首件报检单自带判定与处置，不再往「来料/成品检验单」推单：检验单只由到货单生成
        List<Map<String, Object>> groups = withApprovalGroup(
                voucherGroups(new ArrayList<>(selects.keySet()), null));
        return config("FIRST_INSPECT", "首件报检单", "单据", head,
                List.of(tab("items", "明细", items, "报检数量")), groups, selects);
    }

    private List<Map<String, Object>> firstInspectFields() {
        List<Map<String, Object>> out = fields(
                field("存货编码", "文本", false), reference("存货名称", "INV", "存货名称"),
                field("规格型号", "文本", false), field("BOM版本号", "文本", false),
                field("计量单位", "文本", false), field("加工单号", "文本", false),
                field("工序汇报单号", "文本", false),
                field("工序编码", "文本", false), field("工序名称", "文本", false),
                // 检验判定紧跟工序：这是这张单的结论，不能压在十几列之后滚动才看见
                // （不预置默认值，质检员必须自己判，避免"审核即合格"）
                verdictField(),
                dispositionField(), field("处置数量", "小数", false), field("判定说明", "文本", false),
                selectDefault("加工类型", "自制加工", "自制加工", "委外加工"),
                field("报检数量", "小数", true),
                selectDefault("检验方式", "全检", "全检", "抽检"),
                defaultValue(field("抽检比例%", "小数", false), 100),
                defaultValue(field("抽检数量", "小数", false), 0),
                defaultValue(field("最大不合格数", "整数", false), 0),
                field("检验要求", "文本", false), field("生产车间", "文本", false),
                optionalSelect("班组", "下料班", "车工班", "铣工班", "热处理班", "质检班"),                field("工人", "文本", false), reference("检验方案", "QC_PLAN", "方案名称"),
                computedField("累计检验数量"), computedField("累计合格数量"),
                computedField("累计不合格数量"), computedField("累计检验返工数量"),
                reference("不合格原因", "REJECT", "不合格原因"),
                defaultValue(field("数值公用自定义项1", "小数", false), 0),
                field("板号", "文本", false),
                // 重新首检时回填原单号：反查得到"这行是返工后重检的"
                field("原首检单号", "文本", false), field("备注", "文本", false));
        addSourceFieldsTo(out);
        return out;
    }

    private Map<String, Object> inspectionVoucherConfig(String code, String name, String businessType,
                                                         Map<String, Object> selects) {
        List<Map<String, Object>> head = voucherHead(businessType);
        head.add(reference("检验部门", "DEPT", "部门名称"));
        head.add(field("数据来源", "文本", false));
        List<Map<String, Object>> items = processInspectionFields();
        // 生产过程检验单当前只做单据本身；下游退回/入库转换未实现，不挂错误生单入口
        List<Map<String, Object>> groups = voucherGroups(new ArrayList<>(selects.keySet()), null);
        Map<String, Object> cfg = config(code, name, "单据", head,
                List.of(tab("items", "明细", items, "检验数量")), groups, selects);
        addUnqualifiedTab(cfg);
        addProjectTab(cfg);
        return cfg;
    }

    /** 生产过程检验单明细：T+ QM16 工序结果 + 主流 MES 的抽样/不良分解字段。 */
    private List<Map<String, Object>> processInspectionFields() {
        List<Map<String, Object>> out = fields(
                field("存货编码", "文本", false), reference("存货名称", "INV", "存货名称"),
                field("规格型号", "文本", false), field("产品批号", "文本", false),
                selectDefault("加工类型", "自制加工", "自制加工", "委外加工"),
                field("加工单号", "文本", false), field("工序汇报单号", "文本", false),
                field("工序编码", "文本", false), field("工序名称", "文本", false),
                field("计量单位", "文本", false), field("工序辅单位", "文本", false),
                field("报检数量", "小数", true), field("应检数量", "小数", false),
                field("检验数量", "小数", true), field("合格数量", "小数", false),
                field("不合格数量", "小数", false),
                field("检验返工数量", "小数", false), field("工废数量", "小数", false),
                field("料废数量", "小数", false),
                selectDefault("检验方式", "全检", "全检", "抽检"),
                defaultValue(field("抽检比例%", "小数", false), 100),
                defaultValue(field("抽检数量", "小数", false), 0),
                defaultValue(field("最大不合格数", "整数", false), 0),
                select("检验结果判定", "合格", "返工", "让步接收", "报废"),
                reference("检验方案", "QC_PLAN", "方案名称"),
                reference("检验员", "EMP", "员工名称"), field("检验日期", "日期", false),
                field("生产车间", "文本", false), field("工作中心", "文本", false),
                field("设备", "文本", false),
                optionalSelect("班组", "下料班", "车工班", "铣工班", "热处理班", "质检班"),
                field("工人", "文本", false),
                reference("不合格原因", "REJECT", "不合格原因"),
                field("备注", "文本", false));
        addSourceFieldsTo(out);
        return out;
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

    /**
     * 来源追踪字段：既有 hidden 字段按 dataName 升级为 computed（不再只做「缺失才追加」），
     * 否则老配置里的累计数量永远显示 0。
     */
    private static void addSourceFieldsTo(List<Map<String, Object>> fields) {
        for (String name : List.of("来源面板", "来源单号", "来源行号", "来源数量", "累计执行数量", "累计检验数量", "累计入库数量")) {
            Map<String, Object> sourceField = fields.stream()
                    .filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
            if (sourceField == null) {
                sourceField = field(name, name.contains("数量") ? "小数" : "文本", false);
                sourceField.put("hidden", true);
                fields.add(sourceField);
            }
            if (name.startsWith("累计")) {
                sourceField.put("dataType", "小数");
                sourceField.put("computed", true);
            }
        }
    }

    private List<Map<String, Object>> voucherGroups(List<String> selectActions, String generateAction) {
        List<Map<String, Object>> groups = new ArrayList<>(List.of(
                group("新增", "新增"), group("选单", selectActions.toArray(String[]::new)),
                group("保存", "保存", "保存新增", "保存为草稿"), group("删除", "删除", "删除单据"),
                group("审核", "审核", "弃审")));
        // 无下游转换时（如生产过程检验单当前只做单据本身）不生成空的「生单」组
        if (generateAction != null && !generateAction.isBlank()) {
            groups.add(group("生单", generateAction));
        }
        groups.add(group("工具", "生单流程联查", "执行情况"));
        groups.add(group("更多", "刷新", "放弃"));
        return groups;
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

    private static Map<String, Object> selectConfig(String source, String title, String detailKey,
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

    private static void enrichSelectConfig(Map<String, Object> cfg, String sourceQty, String targetQty) {
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
            row.setVersion("QF1.0");
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception e) {
            throw new IllegalStateException("质量面板配置升级失败：" + code, e);
        }
    }

    /** 幂等追加面板动作：分组和顶部按钮各补一份，已有则跳过。 */
    private static void ensureAction(Map<String, Object> config, String groupName, String action) {
        Map<String, Object> metadata = map(config.get("metadata"));
        List<Map<String, Object>> groups = listOfMaps(metadata.get("buttonGroups"));
        boolean groupFound = false;
        for (Map<String, Object> group : groups) {
            if (!groupName.equals(String.valueOf(group.get("name")))) continue;
            groupFound = true;
            List<Object> actions = new ArrayList<>();
            if (group.get("actions") instanceof List<?> list) actions.addAll(list);
            if (actions.stream().noneMatch(value -> action.equals(String.valueOf(value)))) actions.add(action);
            group.put("actions", actions);
        }
        if (!groupFound) {
            groups.add(Map.of("name", groupName, "actions", List.of(action)));
        }
        metadata.put("buttonGroups", groups);

        List<Map<String, Object>> buttons = listOfMaps(metadata.get("panelButtons"));
        if (buttons.stream().noneMatch(button -> action.equals(String.valueOf(button.get("buttonName"))))) {
            buttons.add(Map.of("buttonName", action));
        }
        metadata.put("panelButtons", buttons);
        config.put("metadata", metadata);
    }

    /** 幂等摘除面板动作（分组 + 顶部按钮），用于清理 T+ 抓取配置里已废弃/无实现的入口。 */
    private static void removeAction(Map<String, Object> config, String groupName, String action) {
        Map<String, Object> metadata = map(config.get("metadata"));
        List<Map<String, Object>> groups = listOfMaps(metadata.get("buttonGroups"));
        for (Map<String, Object> group : groups) {
            if (!groupName.equals(String.valueOf(group.get("name")))) continue;
            List<Object> actions = new ArrayList<>();
            if (group.get("actions") instanceof List<?> list) {
                for (Object value : list) {
                    if (!action.equals(String.valueOf(value))) actions.add(value);
                }
            }
            group.put("actions", actions);
        }
        metadata.put("buttonGroups", groups);

        List<Map<String, Object>> buttons = listOfMaps(metadata.get("panelButtons"));
        buttons.removeIf(button -> action.equals(String.valueOf(button.get("buttonName"))));
        metadata.put("panelButtons", buttons);
        config.put("metadata", metadata);
    }

    /** 选单表头映射补一条；已有同 from/to 时不重复。 */
    private static void ensureHeaderMap(Map<String, Object> selectConfig, String from, String to) {
        List<Map<String, String>> headerMap = new ArrayList<>();
        if (selectConfig.get("headerMap") instanceof List<?> list) {
            for (Object item : list) {
                if (!(item instanceof Map<?, ?> raw)) continue;
                Map<String, String> mapping = new LinkedHashMap<>();
                raw.forEach((key, value) -> mapping.put(String.valueOf(key), value == null ? "" : String.valueOf(value)));
                headerMap.add(mapping);
            }
        }
        boolean exists = headerMap.stream()
                .anyMatch(mapping -> from.equals(mapping.get("from")) && to.equals(mapping.get("to")));
        if (!exists) headerMap.add(maps(from, to).get(0));
        selectConfig.put("headerMap", headerMap);
    }

    /** 明细映射补一条；已有同 to 映射时不重复。 */
    private static void ensureDetailMap(Map<String, Object> selectConfig, String from, String to) {
        List<Map<String, String>> detailMap = new ArrayList<>();
        if (selectConfig.get("detailMap") instanceof List<?> list) {
            for (Object item : list) {
                if (!(item instanceof Map<?, ?> raw)) continue;
                Map<String, String> mapping = new LinkedHashMap<>();
                raw.forEach((key, value) -> mapping.put(String.valueOf(key), value == null ? "" : String.valueOf(value)));
                detailMap.add(mapping);
            }
        }
        boolean exists = detailMap.stream().anyMatch(mapping -> to.equals(mapping.get("to")));
        if (!exists) detailMap.add(maps(from, to).get(0));
        selectConfig.put("detailMap", detailMap);
    }

    /** 只改历史遗留映射的 from（如 到货数量→实收数量），管理员改过的其它来源不覆盖。 */
    private static void retargetDetailMap(Map<String, Object> selectConfig, String to,
                                          String from, String legacyFrom) {
        if (!(selectConfig.get("detailMap") instanceof List<?> list)) return;
        List<Map<String, String>> detailMap = new ArrayList<>();
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> raw)) continue;
            Map<String, String> mapping = new LinkedHashMap<>();
            raw.forEach((key, value) -> mapping.put(String.valueOf(key), value == null ? "" : String.valueOf(value)));
            if (to.equals(mapping.get("to")) && legacyFrom.equals(mapping.get("from"))) mapping.put("from", from);
            detailMap.add(mapping);
        }
        selectConfig.put("detailMap", detailMap);
    }

    /** 明细字段补齐/升级：按 dataName 查找，既有字段改属性，缺失才追加。 */
    private static void ensureDetailField(Map<String, Object> config, String name, String type,
                                          boolean computed, boolean visible) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        Map<String, Object> tab = tabs.stream().filter(t -> "items".equals(t.get("key"))).findFirst()
                .orElse(tabs.get(0));
        List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
        Map<String, Object> target = fields.stream()
                .filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
        if (target == null) {
            target = field(name, type, false);
            fields.add(target);
        }
        target.put("dataType", type);
        if (computed) target.put("computed", true); else target.remove("computed");
        if (visible) target.remove("hidden"); else target.put("hidden", true);
        tab.put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
        if (visible) ensureColumn(config, name);
    }

    /** 列表列插到指定列之后；列不存在时追加，已存在时先摘除再插，保证顺序稳定。 */
    private static void ensureColumnAfter(Map<String, Object> config, String after, String name) {
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> tablePages = listOfMaps(pageDto.get("tablePages"));
        if (tablePages.isEmpty()) return;
        Map<String, Object> table = tablePages.get(0);
        List<Map<String, Object>> gridTabs = listOfMaps(table.get("gridTabs"));
        if (gridTabs.isEmpty()) return;
        Map<String, Object> grid = gridTabs.get(0);
        List<Object> columns = new ArrayList<>();
        if (grid.get("columns") instanceof List<?> list) columns.addAll(list);
        columns.remove(name);
        int index = columns.indexOf(after);
        if (index >= 0) columns.add(index + 1, name); else columns.add(name);
        grid.put("columns", columns);
        table.put("gridTabs", gridTabs);
        pageDto.put("tablePages", tablePages);
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
    }

    /** 列表列摘除（字段仍保留在明细里，只从主表列隐藏）。 */
    private static void removeColumn(Map<String, Object> config, String name) {
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> tablePages = listOfMaps(pageDto.get("tablePages"));
        if (tablePages.isEmpty()) return;
        Map<String, Object> table = tablePages.get(0);
        List<Map<String, Object>> gridTabs = listOfMaps(table.get("gridTabs"));
        if (gridTabs.isEmpty()) return;
        Map<String, Object> grid = gridTabs.get(0);
        List<Object> columns = new ArrayList<>();
        if (grid.get("columns") instanceof List<?> list) columns.addAll(list);
        columns.remove(name);
        grid.put("columns", columns);
        table.put("gridTabs", gridTabs);
        pageDto.put("tablePages", tablePages);
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
    }

    /** 明细字段默认值（新字段口径由注册表统一，避免空字符串被当成 false/0）。 */
    private static void setDetailDefault(Map<String, Object> config, String name, Object value) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        Map<String, Object> tab = tabs.stream().filter(t -> "items".equals(t.get("key"))).findFirst()
                .orElse(tabs.get(0));
        List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
        for (Map<String, Object> field : fields) {
            if (name.equals(field.get("dataName"))) field.put("defaultValue", value);
        }
        tab.put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    /** 明细参照字段：缺失时按参照定义补入，并补主表列。 */
    private static void ensureDetailReference(Map<String, Object> config, String name, String panel, String refField) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        Map<String, Object> tab = tabs.stream().filter(t -> "items".equals(t.get("key"))).findFirst()
                .orElse(tabs.get(0));
        List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
        mergeField(fields, reference(name, panel, refField));
        tab.put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
        ensureColumn(config, name);
    }

    /** 列表列补一列（tablePages[0].gridTabs[0].columns），已有不重复。 */
    private static void ensureColumn(Map<String, Object> config, String name) {
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> tablePages = listOfMaps(pageDto.get("tablePages"));
        if (tablePages.isEmpty()) return;
        Map<String, Object> table = tablePages.get(0);
        List<Map<String, Object>> gridTabs = listOfMaps(table.get("gridTabs"));
        if (gridTabs.isEmpty()) return;
        Map<String, Object> grid = gridTabs.get(0);
        List<Object> columns = new ArrayList<>();
        if (grid.get("columns") instanceof List<?> list) columns.addAll(list);
        if (!columns.contains(name)) columns.add(name);
        grid.put("columns", columns);
        table.put("gridTabs", gridTabs);
        pageDto.put("tablePages", tablePages);
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
    }

    /** 明细下拉字段的选项对齐（如检验结果判定四态）。 */
    private static void setDetailOptions(Map<String, Object> config, String name, List<String> options) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        if (tabs.isEmpty()) return;
        Map<String, Object> tab = tabs.stream().filter(t -> "items".equals(t.get("key"))).findFirst()
                .orElse(tabs.get(0));
        List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
        Map<String, Object> target = fields.stream()
                .filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
        if (target == null) {
            target = select(name, options.toArray(String[]::new));
            fields.add(target);
        } else {
            target.put("dataType", "下拉框");
            target.put("options", new ArrayList<>(options));
            Object current = target.get("defaultValue");
            if (current == null || !options.contains(String.valueOf(current))) {
                target.put("defaultValue", options.get(0));
            }
        }
        tab.put("fields", fields);
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    /** 表头字段补齐/升级（dataSchema.fields）。 */
    private static void ensureHeadField(Map<String, Object> config, String name, String type,
                                        List<String> options, Object defaultValue) {
        Map<String, Object> schema = map(config.get("dataSchema"));
        List<Map<String, Object>> fields = listOfMaps(schema.get("fields"));
        Map<String, Object> target = fields.stream()
                .filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
        if (target == null) {
            target = field(name, type, false);
            fields.add(target);
        }
        target.put("dataType", type);
        if (options != null) {
            target.put("options", new ArrayList<>(options));
            if (defaultValue != null) target.put("defaultValue", defaultValue);
        }
        schema.put("fields", fields);
        config.put("dataSchema", schema);
    }

    /** 表头参照字段补齐（如检验部门）；已有字段只补参照属性，不覆盖其它配置。 */
    private static void ensureHeadReference(Map<String, Object> config, String name, String panel, String refField) {
        Map<String, Object> schema = map(config.get("dataSchema"));
        List<Map<String, Object>> fields = listOfMaps(schema.get("fields"));
        Map<String, Object> target = fields.stream()
                .filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
        Map<String, Object> definition = reference(name, panel, refField);
        if (target == null) {
            fields.add(new LinkedHashMap<>(definition));
        } else {
            for (Map.Entry<String, Object> entry : definition.entrySet()) {
                target.putIfAbsent(entry.getKey(), entry.getValue());
            }
            target.put("dataType", "参照");
        }
        schema.put("fields", fields);
        config.put("dataSchema", schema);
    }

    /** 表头字段进入表单页 fieldNames（逗号分隔字符串）。 */
    private static void ensureFormField(Map<String, Object> config, String name) {
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> formPages = listOfMaps(pageDto.get("formPages"));
        if (formPages.isEmpty()) return;
        Map<String, Object> form = formPages.get(0);
        String fieldNames = form.get("fieldNames") == null ? "" : String.valueOf(form.get("fieldNames"));
        List<String> names = new ArrayList<>();
        for (String part : fieldNames.split(",")) {
            String trimmed = part.trim();
            if (!trimmed.isEmpty() && !names.contains(trimmed)) names.add(trimmed);
        }
        if (!names.contains(name)) names.add(name);
        form.put("fieldNames", String.join(",", names));
        pageDto.put("formPages", formPages);
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
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

    /** 首件检验判定：合格 / 不合格 / 让步接收，无默认值（必须人判）。 */
    private static Map<String, Object> verdictField() {
        Map<String, Object> field = field("检验判定", "下拉框", true);
        field.put("options", Arrays.asList("合格", "不合格", "让步接收"));
        field.put("defaultValue", "");
        return field;
    }

    /** 不合格处置：返工 / 返修 / 让步接收 / 报废，无默认值（人判后才选）。 */
    private static Map<String, Object> dispositionField() {
        Map<String, Object> field = field("处置方式", "下拉框", false);
        field.put("options", Arrays.asList("返工", "返修", "让步接收", "报废"));
        field.put("defaultValue", "");
        return field;
    }

    /** 把「审核/弃审」组换成「审批」组：提交审批 → 审批通过/驳回，提交人与审批人分离留痕。 */
    private static List<Map<String, Object>> withApprovalGroup(List<Map<String, Object>> groups) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> g : groups) {
            if ("审核".equals(String.valueOf(g.get("name")))) {
                out.add(group("审批", "提交审批", "审批通过", "审批驳回", "审批情况", "弃审"));
            } else {
                out.add(g);
            }
        }
        return out;
    }

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
