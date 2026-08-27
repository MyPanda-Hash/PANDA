package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
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
import java.util.Set;

/**
 * T+ report panel metadata. These definitions are persisted to panel_config so
 * the regular metadata-driven frontend can load reports through /api/px.
 */
@Component
public class ReportPanelRegistry {

    public record Definition(String code, String name, List<String> columns,
                             List<Map<String, Object>> queryFields,
                             List<Map<String, Object>> columnGroups) {}

    private static final Map<String, Definition> DEFINITIONS = definitions();
    private static final Set<String> NUMERIC_WORDS = Set.of(
            "数量", "金额", "单价", "成本", "现存量", "结存", "调整", "税额",
            "损耗", "重量", "换算率", "余额", "工价", "工时", "进度", "合格率", "率%",
            "合计", "单数", "已完工", "进行中", "未开工");

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public ReportPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    public static boolean isReport(String panelCode) {
        return DEFINITIONS.containsKey(panelCode);
    }

    public static Definition definition(String panelCode) {
        return DEFINITIONS.get(panelCode);
    }

    public static Set<String> codes() {
        return DEFINITIONS.keySet();
    }

    @PostConstruct
    public void syncPanelConfigs() {
        for (Definition def : DEFINITIONS.values()) {
            PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                    .eq(PanelConfig::getPanelCode, def.code()));
            if (row == null) {
                row = new PanelConfig();
                row.setPanelCode(def.code());
                row.setCreateTime(LocalDateTime.now());
            }
            row.setPanelName(def.name());
            row.setCategory("报表");
            row.setConfig(toJson(buildConfig(def)));
            row.setVersion("2.1");
            row.setUpdateTime(LocalDateTime.now());
            if (row.getId() == null) panelMapper.insert(row);
            else panelMapper.updateById(row);
        }
    }

    private Map<String, Object> buildConfig(Definition def) {
        List<Map<String, Object>> buttons = buttonMaps("查询", "打印", "导出", "发送邮件", "刷新", "退出");
        List<Map<String, Object>> buttonGroups = List.of(
                group("查询", "查询"), group("打印", "打印", "预览"), group("导出", "导出"),
                group("发送邮件", "发送邮件"), group("刷新", "刷新"), group("退出", "退出"));

        Map<String, Object> grid = new LinkedHashMap<>();
        grid.put("label", "报表");
        grid.put("rowSource", "rows");
        grid.put("columns", def.columns());
        if (!def.columnGroups().isEmpty()) grid.put("columnGroups", def.columnGroups());

        Map<String, Object> tablePage = new LinkedHashMap<>();
        tablePage.put("tableName", def.name());
        tablePage.put("queryFields", def.queryFields());
        tablePage.put("gridTabs", List.of(grid));
        tablePage.put("topBarBtn", buttons);
        tablePage.put("rowOperationBarBtn", List.of());
        tablePage.put("pageSize", 50);
        tablePage.put("events", List.of());

        Map<String, Object> metadata = new LinkedHashMap<>();
        metadata.put("panelCode", def.code());
        metadata.put("panelName", def.name());
        metadata.put("panelCategory", "报表");
        metadata.put("report", true);
        metadata.put("panelState", Map.of("dataName", "报表状态", "dataType", "STRING", "defaultOptions", List.of()));
        metadata.put("panelPageDto", Map.of("tablePages", List.of(tablePage), "formPages", List.of()));
        metadata.put("panelButtons", buttons);
        metadata.put("buttonGroups", buttonGroups);
        metadata.put("version", "2.1");

        List<Map<String, Object>> fields = new ArrayList<>();
        for (String column : def.columns()) {
            fields.add(Map.of("dataName", column, "dataType", dataType(column)));
        }

        Map<String, Object> config = new LinkedHashMap<>();
        config.put("metadata", metadata);
        config.put("dataSchema", Map.of("type", "object", "fields", fields));
        config.put("detail", Map.of("tabs", List.of()));
        return config;
    }

    private String dataType(String column) {
        if (column.contains("日期") || column.contains("时间") || column.endsWith("日")) return "日期";
        for (String word : NUMERIC_WORDS) if (column.contains(word)) return "小数";
        return "文本";
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("报表配置序列化失败", e);
        }
    }

    private static Map<String, Object> group(String name, String... actions) {
        return Map.of("name", name, "actions", Arrays.asList(actions));
    }

    private static List<Map<String, Object>> buttonMaps(String... names) {
        return Arrays.stream(names).map(name -> Map.<String, Object>of("buttonName", name)).toList();
    }

    private static Map<String, Object> q(String name, String type) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", type);
        return field;
    }

    private static Map<String, Object> qs(String name, String... options) {
        Map<String, Object> field = q(name, "下拉框");
        field.put("options", Arrays.asList(options));
        return field;
    }

    private static Map<String, Object> qr(String name, String panel, String fieldName,
                                          Map<String, Object> filter, String... columns) {
        Map<String, Object> field = q(name, "参照");
        field.put("refPanel", panel);
        field.put("refField", fieldName);
        field.put("displayField", fieldName);
        field.put("filter", filter);
        field.put("refColumns", Arrays.asList(columns));
        return field;
    }

    private static Map<String, Object> queryField(String name) {
        return switch (name) {
            case "仓库" -> qr(name, "WH", "仓库名称", Map.of("停用", false),
                    "仓库编码", "仓库名称", "仓库地址", "负责人", "允许零库存出库");
            case "存货", "材料名称", "产品名称" -> qr(name, "INV", "存货名称", Map.of("停用", false),
                    "存货编码", "存货名称", "规格型号", "计量单位", "属性", "停用");
            case "客户" -> qr(name, "PARTNER", "往来单位名称",
                    Map.of("停用", false, "性质", List.of("客户", "两者")),
                    "往来单位编码", "往来单位名称", "性质", "分管部门", "分管人员", "停用");
            case "供应商" -> qr(name, "PARTNER", "往来单位名称",
                    Map.of("停用", false, "性质", List.of("供应商", "两者")),
                    "往来单位编码", "往来单位名称", "性质", "分管部门", "分管人员", "停用");
            case "业务员" -> qr(name, "EMP", "员工名称", Map.of("停用", false, "业务员", true),
                    "员工编码", "员工名称", "所属部门", "职务", "手机", "停用");
            case "人员", "经手人", "领用人", "工人", "工人名称" ->
                    qr(name, "EMP", "员工名称", Map.of("停用", false),
                            "员工编码", "员工名称", "所属部门", "职务", "手机", "停用");
            default -> null;
        };
    }

    private static List<Map<String, Object>> query(String... names) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (String name : names) {
            String type = name.contains("日期") ? "日期" : "文本";
            Map<String, Object> reference = queryField(name);
            if (reference != null) out.add(reference);
            else if ("单据状态".equals(name)) out.add(qs(name, "草稿", "审批中", "已审核", "生产中", "已完工", "已中止"));
            else if ("生产车间".equals(name)) out.add(qs(name, "熔铸车间", "轧制车间", "精整车间", "测试车间"));
            else out.add(q(name, type));
        }
        return out;
    }

    private static List<Map<String, Object>> groups(Object... definitions) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (int i = 0; i < definitions.length; i += 2) {
            out.add(Map.of("label", definitions[i], "columns", definitions[i + 1]));
        }
        return out;
    }

    private static Definition d(String code, String name, List<String> columns,
                                List<Map<String, Object>> queries) {
        return new Definition(code, name, columns, queries, List.of());
    }

    private static Definition d(String code, String name, List<String> columns,
                                List<Map<String, Object>> queries,
                                List<Map<String, Object>> columnGroups) {
        return new Definition(code, name, columns, queries, columnGroups);
    }

    private static List<String> c(String... columns) {
        return Arrays.asList(columns);
    }

    private static Map<String, Definition> definitions() {
        Map<String, Definition> out = new LinkedHashMap<>();

        out.put("PURCHASE_IN_DETAIL", d("PURCHASE_IN_DETAIL", "采购入库单明细表",
                c("单据日期", "创建时间", "单据编号", "业务类型", "仓库编码", "仓库", "入库类别", "供应商编码", "供应商", "部门编码", "部门", "经手人编码", "经手人", "备注", "制单人", "审核人", "存货编码", "存货", "规格型号", "计量单位", "实收数量", "单价", "金额", "计量单位2", "实收数量2", "入库调整", "费用调整", "总成本", "费用金额"),
                query("开始日期", "结束日期", "单据编号", "仓库", "供应商", "存货")));
        out.put("FINISH_IN_DETAIL", d("FINISH_IN_DETAIL", "产成品入库单明细表",
                c("单据日期", "创建时间", "单据编号", "业务类型", "仓库编码", "仓库", "入库类别", "生产车间编码", "生产车间", "经手人编码", "经手人", "备注", "制单人", "审核人", "存货编码", "存货", "规格型号", "计量单位", "实收数量", "单价", "金额", "计量单位2", "实收数量2"),
                query("开始日期", "结束日期", "单据编号", "仓库", "生产车间", "存货")));
        out.put("OTHER_IN_DETAIL", d("OTHER_IN_DETAIL", "其他入库单明细表",
                c("单据日期", "创建时间", "单据编号", "业务类型", "仓库编码", "仓库", "入库类别", "部门编码", "部门", "经手人编码", "经手人", "备注", "制单人", "审核人", "存货编码", "存货", "规格型号", "计量单位", "数量", "单价", "金额", "计量单位2", "数量2"),
                query("开始日期", "结束日期", "单据编号", "仓库", "存货")));
        out.put("SALE_OUT_DETAIL", d("SALE_OUT_DETAIL", "销售出库单明细表",
                c("单据日期", "创建时间", "单据编号", "业务类型", "仓库编码", "仓库", "出库类别", "客户编码", "客户", "部门编码", "部门", "经手人编码", "经手人", "制单人", "审核人", "存货编码", "存货", "规格型号", "计量单位", "应发数量", "数量", "计量单位2", "应发数量2", "数量2", "成本价", "成本金额", "出库调整", "销售订单号", "入库单号"),
                query("开始日期", "结束日期", "单据编号", "仓库", "客户", "存货")));
        out.put("MATERIAL_OUT_DETAIL", d("MATERIAL_OUT_DETAIL", "材料出库单明细表",
                c("单据日期", "创建时间", "单据编号", "业务类型", "仓库编码", "仓库", "出库类别", "生产车间编码", "生产车间", "领用人编码", "领用人", "制单人", "审核人", "材料编码", "材料名称", "材料规格", "明细.生产车间", "工作中心", "班组", "工人", "设备", "计量单位", "数量", "单价", "金额", "计量单位2", "数量2", "出库调整"),
                query("开始日期", "结束日期", "单据编号", "仓库", "生产车间", "材料名称")));
        out.put("OTHER_OUT_DETAIL", d("OTHER_OUT_DETAIL", "其他出库单明细表",
                c("单据日期", "创建时间", "单据编号", "业务类型", "仓库编码", "仓库", "出库类别", "部门编码", "部门", "经手人编码", "经手人", "备注", "制单人", "审核人", "存货编码", "存货", "规格型号", "计量单位", "数量", "单价", "金额", "计量单位2", "数量2", "出库调整", "累计调拨入库量", "合理损耗数量", "入库单号"),
                query("开始日期", "结束日期", "单据编号", "仓库", "存货")));

        out.put("PURCHASE_IN_STATS", d("PURCHASE_IN_STATS", "采购入库单统计表",
                c("仓库编码", "仓库", "供应商编码", "供应商", "存货编码", "存货", "规格型号", "主单位", "辅单位", "实收数量(主单位)", "单价(主单位)", "金额", "单价(辅单位)", "入库调整", "费用调整", "总成本", "费用金额"),
                query("开始日期", "结束日期", "仓库", "供应商", "存货")));
        out.put("FINISH_IN_STATS", d("FINISH_IN_STATS", "产成品入库单统计表",
                c("单据日期", "项目", "存货编码", "存货", "规格型号", "计量单位", "辅单位", "实收数量(主单位)", "单价", "金额", "实收数量(辅单位)", "单价(辅单位)"),
                query("开始日期", "结束日期", "仓库", "生产车间", "存货")));
        out.put("OTHER_IN_STATS", d("OTHER_IN_STATS", "其他入库单统计表",
                c("仓库编码", "仓库", "存货编码", "存货", "规格型号", "主单位", "辅单位", "数量(主单位)", "单价", "金额", "数量(辅单位)", "单价(辅单位)"),
                query("开始日期", "结束日期", "仓库", "存货")));
        out.put("SALE_OUT_STATS", d("SALE_OUT_STATS", "销售出库单统计表",
                c("单据日期（周）", "存货编码", "存货", "规格型号", "主单位", "辅单位", "数量(主单位)", "成本价(主单位)", "数量(辅单位)", "成本价(辅单位)", "成本金额", "出库调整"),
                query("开始日期", "结束日期", "仓库", "客户", "存货")));
        out.put("MATERIAL_OUT_STATS", d("MATERIAL_OUT_STATS", "材料出库单统计表",
                c("仓库编码", "仓库", "材料编码", "材料名称", "材料规格", "主单位", "计量单位(辅单位)", "数量(主单位)", "单价(主单位)", "金额", "数量(辅单位)", "单价(辅单位)", "出库调整"),
                query("开始日期", "结束日期", "仓库", "生产车间", "材料名称")));
        out.put("OTHER_OUT_STATS", d("OTHER_OUT_STATS", "其他出库单统计表",
                c("仓库编码", "仓库", "存货编码", "存货", "规格型号", "主单位", "辅单位", "数量(主单位)", "单价", "金额", "数量(辅单位)", "单价(辅单位)", "出库调整"),
                query("开始日期", "结束日期", "仓库", "存货")));

        out.put("COST_MAINTAIN", d("COST_MAINTAIN", "成本手工维护",
                c("单据类型", "单据编号", "单据日期", "仓库", "存货编码", "存货类别", "存货", "规格型号", "计量单位", "数量", "原成本", "调整后成本", "成本金额", "制单人", "单据状态"),
                query("开始日期", "结束日期", "仓库", "存货", "单据类型")));
        out.put("STOCK_STATUS", d("STOCK_STATUS", "库存状况表",
                c("仓库编码", "仓库", "存货编码", "存货", "规格型号", "主计量", "现存量(主)", "结存单价(主)", "结存金额"),
                query("仓库", "存货")));

        List<String> summaryColumns = c("仓库编码", "仓库", "存货编码", "存货", "规格型号", "主单位", "辅单位",
                "期初数量", "期初平均单价", "期初金额", "本期入库数量", "入库平均单价", "本期入库金额",
                "本期出库数量", "出库平均单价", "本期出库金额", "期末结存数量", "期末平均单价", "期末结存金额");
        out.put("STOCK_SUMMARY", d("STOCK_SUMMARY", "收发存汇总表", summaryColumns,
                query("开始日期", "结束日期", "仓库", "存货"), groups(
                        "期初结存", c("期初数量", "期初平均单价", "期初金额"),
                        "本期入库", c("本期入库数量", "入库平均单价", "本期入库金额"),
                        "本期出库", c("本期出库数量", "出库平均单价", "本期出库金额"),
                        "期末结存", c("期末结存数量", "期末平均单价", "期末结存金额"))));

        List<String> ledgerColumns = c("单据日期", "单据类型", "单据编号", "业务类型", "往来单位", "项目",
                "收入数量", "收入单价", "收入金额", "发出数量", "发出单价", "发出金额", "结存数量", "结存平均单价", "结存金额");
        out.put("STOCK_LEDGER", d("STOCK_LEDGER", "库存台账", ledgerColumns,
                query("开始日期", "结束日期", "仓库", "存货", "单据类型"), groups(
                        "收入", c("收入数量", "收入单价", "收入金额"),
                        "发出", c("发出数量", "发出单价", "发出金额"),
                        "结存", c("结存数量", "结存平均单价", "结存金额"))));

        out.put("SALES_ORDER_DETAIL", d("SALES_ORDER_DETAIL", "销售订单明细表",
                c("单据日期", "单据编号", "单据状态", "客户编码", "客户", "结算客户", "部门", "业务员", "项目", "存货编码", "存货", "规格型号", "计量单位", "数量", "单价", "税率%", "含税单价", "金额", "含税金额", "折扣金额", "预计交货日期", "现存量", "制单人", "审核人"),
                query("开始日期", "结束日期", "单据编号", "单据状态", "客户", "存货", "业务员")));
        out.put("SALES_ORDER_STATS", d("SALES_ORDER_STATS", "销售订单统计表",
                c("客户编码", "客户", "部门", "业务员", "存货编码", "存货", "规格型号", "主单位", "单据数", "数量(主单位)", "单价", "金额", "含税金额", "折扣金额", "预计交货日期"),
                query("开始日期", "结束日期", "客户", "存货")));
        out.put("SALES_ORDER_EXEC", d("SALES_ORDER_EXEC", "销售订单执行表",
                c("单据编号", "单据日期", "客户编码", "客户", "部门", "业务员", "存货编码", "存货", "规格型号", "订单数量", "已出库数量", "出库执行率%", "已生产数量", "生产进度%", "未执行数量", "预计交货日期", "单据状态"),
                query("单据编号", "开始日期", "结束日期", "客户", "存货", "单据状态")));
        out.put("SALES_ORDER_PROGRESS", d("SALES_ORDER_PROGRESS", "销售订单生产进度表",
                c("单据编号", "单据日期", "客户", "存货编码", "存货", "规格型号", "订单数量", "加工单号", "加工单数量", "已汇报数量", "完工数量", "生产进度%", "预完工日", "单据状态"),
                query("单据编号", "开始日期", "结束日期", "客户", "存货", "单据状态")));
        out.put("MANU_ORDER_DETAIL", d("MANU_ORDER_DETAIL", "生产加工单明细表",
                c("单据编号", "单据状态", "生产车间", "客户编码", "客户", "产品编码", "产品名称", "规格型号", "生产单位", "数量", "齐套数量(主)", "累计汇报套数(工序单位)", "可用量", "现存量", "图号", "单重", "总重", "需求令号", "预开工日", "预完工日"),
                query("单据编号", "单据状态", "生产车间", "客户", "产品名称")));
        out.put("MANU_ORDER_STATS", d("MANU_ORDER_STATS", "生产加工单统计表",
                c("产品编码", "产品名称", "规格型号", "生产单位", "加工单数", "计划数量", "累计汇报数量", "完工数量", "生产进度%"),
                query("开始日期", "结束日期", "生产车间", "客户", "产品名称")));
        out.put("MANU_PROC_STATS", d("MANU_PROC_STATS", "生产加工单工序统计表",
                c("工序编码", "工序名称", "生产车间", "工作中心", "班组", "设备", "加工单数", "计划数量", "金额", "已完工", "进行中", "未开工", "单位标准工时"),
                query("单据编号", "生产车间", "工序名称", "单据状态")));
        out.put("PROC_DETAIL", d("PROC_DETAIL", "工序明细表",
                c("单据编号", "单据状态", "单据日期", "加工单号", "生产车间", "产品编码", "产品名称", "规格型号", "工序编码", "工序名称", "工作中心", "设备", "班组名称", "工人名称", "报工数量", "合格数量", "不合格数量", "合格率%", "工资类型", "工价", "金额", "开工日期", "完工日期", "备注"),
                query("开始日期", "结束日期", "单据编号", "单据状态", "工序名称", "工人名称")));
        out.put("PROC_STATS", d("PROC_STATS", "工序统计表",
                c("工序编码", "工序名称", "生产车间", "班组名称", "工人名称", "报工单数", "报工数量", "合格数量", "不合格数量", "合格率%", "金额"),
                query("开始日期", "结束日期", "单据状态", "工序名称", "工人名称")));
        out.put("SALARY_DETAIL", d("SALARY_DETAIL", "工资明细表",
                c("单据编号", "单据日期", "单据状态", "加工单号", "工序编码", "工序名称", "班组名称", "工人名称", "工资类型", "计件数量", "工价", "计件金额", "金额"),
                query("开始日期", "结束日期", "单据编号", "工人名称", "班组名称")));
        out.put("SALARY_STATS", d("SALARY_STATS", "工资统计表",
                c("工人名称", "班组名称", "工资类型", "报工单数", "计件数量", "计件金额", "调整工资", "工资合计"),
                query("开始日期", "结束日期", "工人名称", "班组名称")));
        out.put("REWORK_REPORT", d("REWORK_REPORT", "返修工序汇报单",
                c("单据编号", "单据日期", "单据状态", "加工单号", "产品编码", "产品名称", "规格型号", "客户", "工序编码", "工序名称", "工作中心", "设备", "班组", "工人", "待返修数量-本序发现", "待返修数量-他序发现", "待返修合计", "返修责任工序", "返修状态"),
                query("开始日期", "结束日期", "单据编号", "加工单号", "工序名称", "工人")));

        // ---------- 委外管理报表（对齐真实 T+ 委外管理 OM 模块 2026-08-25） ----------
        out.put("OUTSOURCE_ORDER_PRODUCT_DETAIL", d("OUTSOURCE_ORDER_PRODUCT_DETAIL", "委外加工单产成品明细表",
                c("单据编号", "单据状态", "单据日期", "委外供应商", "生产车间", "经手人", "交货日期", "产品编码", "产品名称", "规格型号", "计量单位", "数量", "委外单价", "金额", "预完工日", "制单人", "审核人"),
                query("开始日期", "结束日期", "单据编号", "单据状态", "供应商", "产品名称")));
        out.put("OUTSOURCE_ORDER_MATERIAL_DETAIL", d("OUTSOURCE_ORDER_MATERIAL_DETAIL", "委外加工单材料明细表",
                c("单据编号", "单据状态", "单据日期", "委外供应商", "材料编码", "材料名称", "规格型号", "计量单位", "计划数量", "预出仓库", "现存量", "可用量"),
                query("开始日期", "结束日期", "单据编号", "供应商", "材料名称")));
        out.put("OUTSOURCE_FEE_DETAIL", d("OUTSOURCE_FEE_DETAIL", "委外加工费用单明细表",
                c("单据编号", "单据日期", "单据状态", "委外供应商", "委外加工单号", "费用项目", "产品名称", "计量单位", "数量", "委外单价", "费用金额", "费用合计", "经手人", "备注"),
                query("开始日期", "结束日期", "单据编号", "供应商", "委外加工单号")));
        out.put("OUTSOURCE_ORDER_EXEC", d("OUTSOURCE_ORDER_EXEC", "委外加工单执行表",
                c("单据编号", "单据日期", "委外供应商", "产品编码", "产品名称", "规格型号", "订单数量", "已入库数量", "入库执行率%", "已发料数量", "未入库数量", "交货日期", "单据状态"),
                query("单据编号", "开始日期", "结束日期", "供应商", "产品名称", "单据状态")));
        out.put("OUTSOURCE_ISSUE_BALANCE", d("OUTSOURCE_ISSUE_BALANCE", "委外发料耗用结存表",
                c("单据编号", "单据日期", "委外供应商", "委外加工单号", "材料编码", "材料名称", "规格型号", "计量单位", "发料数量", "耗用数量", "结存数量"),
                query("开始日期", "结束日期", "单据编号", "供应商", "材料名称")));
        out.put("OUTSOURCE_ORDER_PRODUCT_STATS", d("OUTSOURCE_ORDER_PRODUCT_STATS", "委外加工单产成品统计表",
                c("委外供应商", "产品编码", "产品名称", "规格型号", "计量单位", "加工单数", "订单数量", "已入库数量", "未入库数量", "委外金额"),
                query("开始日期", "结束日期", "供应商", "产品名称")));
        out.put("OUTSOURCE_ORDER_MATERIAL_STATS", d("OUTSOURCE_ORDER_MATERIAL_STATS", "委外加工单材料统计表",
                c("委外供应商", "材料编码", "材料名称", "规格型号", "计量单位", "加工单数", "计划数量", "已发料数量", "未发料数量"),
                query("开始日期", "结束日期", "供应商", "材料名称")));
        out.put("OUTSOURCE_FEE_STATS", d("OUTSOURCE_FEE_STATS", "委外加工费用单统计表",
                c("委外供应商", "费用项目", "单据数", "数量", "委外单价", "费用金额", "费用合计"),
                query("开始日期", "结束日期", "供应商", "费用项目")));

        // ---------- 生产管理执行/跟踪/产成品材料报表（对齐真实 T+ 生产管理 MP 模块 2026-08-25） ----------
        out.put("MANU_ORDER_EXEC", d("MANU_ORDER_EXEC", "生产加工单执行表",
                c("单据编号", "单据状态", "生产车间", "客户", "产品编码", "产品名称", "规格型号", "计划数量", "累计汇报数量", "完工数量", "累计入库数量", "未完工数量", "生产进度%", "预完工日"),
                query("单据编号", "开始日期", "结束日期", "生产车间", "客户", "产品名称", "单据状态")));
        out.put("MANU_ORDER_TRACKER", d("MANU_ORDER_TRACKER", "生产加工单跟踪工具",
                c("单据编号", "单据状态", "产品编码", "产品名称", "规格型号", "数量", "工序编码", "工序名称", "生产车间", "工作中心", "设备", "班组", "工人", "计划数量", "报工数量", "合格数量", "工序完工状态", "工序进度%"),
                query("单据编号", "生产车间", "工序名称", "单据状态")));
        out.put("MANU_ORDER_PRODUCT_DETAIL", d("MANU_ORDER_PRODUCT_DETAIL", "生产加工单产成品明细表",
                c("单据编号", "单据状态", "生产车间", "客户编码", "客户", "产品编码", "产品名称", "规格型号", "生产单位", "数量", "齐套数量(主)", "累计汇报套数(工序单位)", "可用量", "现存量", "图号", "单重", "总重", "需求令号", "预开工日", "预完工日", "销售订单号"),
                query("单据编号", "单据状态", "生产车间", "客户", "产品名称")));
        out.put("MANU_ORDER_MATERIAL_DETAIL", d("MANU_ORDER_MATERIAL_DETAIL", "生产加工单材料明细表",
                c("单据编号", "单据状态", "生产车间", "产品编码", "产品名称", "材料编码", "材料名称", "规格型号", "计量单位", "计划数量", "需用数量", "预出仓库", "现存量", "可用量"),
                query("单据编号", "生产车间", "材料名称", "单据状态")));
        out.put("MANU_ORDER_PRODUCT_STATS", d("MANU_ORDER_PRODUCT_STATS", "生产加工单产成品统计表",
                c("产品编码", "产品名称", "规格型号", "生产单位", "加工单数", "计划数量", "已完工数量", "生产进度%"),
                query("开始日期", "结束日期", "生产车间", "客户", "产品名称")));
        out.put("MANU_ORDER_MATERIAL_STATS", d("MANU_ORDER_MATERIAL_STATS", "生产加工单材料统计表",
                c("材料编码", "材料名称", "规格型号", "计量单位", "加工单数", "计划数量", "已领料数量", "未领料数量"),
                query("开始日期", "结束日期", "生产车间", "材料名称")));

        // ---------- 配货管理报表（对齐真实 T+ 配货管理 DIM 模块 2026-08-25） ----------
        out.put("PICK_ORDER_DETAIL", d("PICK_ORDER_DETAIL", "配货单明细表",
                c("单据编号", "单据日期", "单据状态", "客户", "仓库", "存货编码", "存货名称", "规格型号", "计量单位", "数量", "单价", "金额", "销售订单号", "制单人", "审核人"),
                query("开始日期", "结束日期", "单据编号", "客户", "存货")));
        out.put("PICK_ORDER_STATS", d("PICK_ORDER_STATS", "配货单统计表",
                c("客户", "存货编码", "存货名称", "规格型号", "主单位", "配货单数", "数量(主单位)", "金额"),
                query("开始日期", "结束日期", "客户", "存货")));
        out.put("PICK_ORDER_SUMMARY", d("PICK_ORDER_SUMMARY", "配货综合统计表",
                c("仓库", "存货编码", "存货名称", "规格型号", "配货单数", "配货数量", "销售出库数量", "未出库数量"),
                query("开始日期", "结束日期", "仓库", "存货")));

        // ---------- 质检管理报表（T+ QM 菜单码基线） ----------
        List<String> qualityDetailColumns = c("单据日期", "单据编号", "单据状态", "业务类型", "来源单号",
                "供应商", "生产车间", "加工单号", "存货编码", "存货名称", "规格型号", "工序编码", "工序名称",
                "计量单位", "报检数量", "检验数量", "合格数量", "不合格数量", "检验结果判定", "检验员", "检验日期");
        List<String> qualityStatsColumns = c("业务类型", "存货编码", "存货名称", "规格型号", "计量单位", "单据数",
                "报检数量", "检验数量", "合格数量", "不合格数量", "合格率%");
        List<String> qualityExecColumns = c("单据编号", "单据日期", "单据状态", "存货编码", "存货名称", "规格型号",
                "计量单位", "报检数量", "累计检验数量", "未检验数量", "检验执行率%", "累计入库数量", "入库执行率%");
        out.put("ARRIVAL_IN_DETAIL", d("ARRIVAL_IN_DETAIL", "到货单明细表", qualityDetailColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "供应商", "存货")));
        out.put("ARRIVAL_IN_STATS", d("ARRIVAL_IN_STATS", "到货单统计表", qualityStatsColumns,
                query("开始日期", "结束日期", "供应商", "存货")));
        out.put("ARRIVAL_IN_EXEC", d("ARRIVAL_IN_EXEC", "到货单执行表", qualityExecColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "供应商", "存货")));
        out.put("FINISH_INSPECT_DETAIL", d("FINISH_INSPECT_DETAIL", "成品报检单明细表", qualityDetailColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "生产车间", "存货")));
        out.put("FINISH_INSPECT_STATS", d("FINISH_INSPECT_STATS", "成品报检单统计表", qualityStatsColumns,
                query("开始日期", "结束日期", "生产车间", "存货")));
        out.put("FINISH_INSPECT_EXEC", d("FINISH_INSPECT_EXEC", "成品报检单执行表", qualityExecColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "生产车间", "存货")));
        out.put("FIRST_INSPECT_DETAIL", d("FIRST_INSPECT_DETAIL", "首件报检单明细表", qualityDetailColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "生产车间", "存货")));
        out.put("FIRST_INSPECT_STATS", d("FIRST_INSPECT_STATS", "首件报检单统计表", qualityStatsColumns,
                query("开始日期", "结束日期", "生产车间", "存货")));
        out.put("FIRST_INSPECT_EXEC", d("FIRST_INSPECT_EXEC", "首件报检单执行表", qualityExecColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "生产车间", "存货")));
        out.put("PROCESS_INSPECT_APPLY_DETAIL", d("PROCESS_INSPECT_APPLY_DETAIL", "工序报检单明细表", qualityDetailColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "生产车间", "工序名称")));
        out.put("PROCESS_INSPECT_APPLY_STATS", d("PROCESS_INSPECT_APPLY_STATS", "工序报检单统计表", qualityStatsColumns,
                query("开始日期", "结束日期", "生产车间", "工序名称", "存货")));
        out.put("PROCESS_INSPECT_APPLY_EXEC", d("PROCESS_INSPECT_APPLY_EXEC", "工序报检单执行表", qualityExecColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "生产车间", "工序名称")));
        out.put("INSPECTION_DETAIL", d("INSPECTION_DETAIL", "检验单综合明细表", qualityDetailColumns,
                query("开始日期", "结束日期", "单据编号", "单据状态", "供应商", "生产车间", "存货")));
        out.put("INSPECTION_STATS", d("INSPECTION_STATS", "检验单综合统计表", qualityStatsColumns,
                query("开始日期", "结束日期", "业务类型", "供应商", "生产车间", "存货")));
        out.put("QUALITY_STATS_ANALYSIS", d("QUALITY_STATS_ANALYSIS", "质量统计分析表", qualityStatsColumns,
                query("开始日期", "结束日期", "业务类型", "存货")));
        out.put("QC_ITEM_LIST", d("QC_ITEM_LIST", "检验项目列表",
                c("项目编码", "项目名称", "检验内容", "检验标准", "数据类型", "计量单位", "判定规则", "标准下限", "标准上限", "停用"),
                query("项目编码", "项目名称")));
        out.put("QC_ITEM_STATS", d("QC_ITEM_STATS", "检验项目综合统计表",
                c("检验项目", "检验单数", "检验数量", "合格数量", "不合格数量", "合格率%"),
                query("开始日期", "结束日期", "检验项目")));
        List<String> traceColumns = c("方向", "来源面板", "来源单号", "来源行号", "目标面板", "目标单号", "目标状态",
                "存货编码", "存货名称", "规格型号", "批号", "数量", "检验结果判定", "单据日期");
        out.put("PRODUCT_FORWARD_TRACE", d("PRODUCT_FORWARD_TRACE", "产品正向追溯报表", traceColumns,
                query("开始日期", "结束日期", "来源单号", "目标单号", "存货")));
        out.put("MATERIAL_REVERSE_TRACE", d("MATERIAL_REVERSE_TRACE", "材料反向追溯报表", traceColumns,
                query("开始日期", "结束日期", "来源单号", "目标单号", "存货")));

        return out;
    }
}
