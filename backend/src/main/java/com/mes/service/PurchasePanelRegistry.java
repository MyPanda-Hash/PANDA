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
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 采购面板动作与字段补齐：采购订单→到货单、采购入库→采购退货（库存退料），
 * 并补齐 P1 契约中的采购累计/下单字段与状态按钮。
 * 幂等：已有动作/字段跳过，只补缺失项或升级既有属性，不覆盖管理员手工调整的其他配置。
 */
@Component
public class PurchasePanelRegistry {

    static final String VERSION = "PUR1.0";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public PurchasePanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        ensureAction("PU_ORDER", "生单", "生成到货单");
        ensureAction("PURCHASE_IN", "生单", "生成采购发票（普通采购）");
        ensureAction("PURCHASE_IN", "生单", "生成采购退货单");
        ensureAction("ARRIVAL_IN", "生单", "生成检验单");
        ensureAction("ARRIVAL_IN", "生单", "生成采购入库单");
        // 委外回厂的到货单要能直接转委外入库（来源类型=委外的才允许，见 requireArrivalInboundTarget）
        ensureAction("ARRIVAL_IN", "生单", "生成委外入库单");
        upgrade("PU_REQ", PurchasePanelRegistry::upgradePuReq);
        upgrade("PU_ORDER", PurchasePanelRegistry::upgradePuOrder);
        upgrade("ARRIVAL_IN", PurchasePanelRegistry::upgradeArrivalIn);
        upgrade("PURCHASE_IN", PurchasePanelRegistry::upgradePurchaseIn);
    }

    static void upgradePuReq(Map<String, Object> config) {
        ensureDetailField(config, "已下单数量", "小数", true, true);
        ensureDetailField(config, "未下单数量", "小数", true, true);
        ensureAction(config, "修改", "中止");
        ensureAction(config, "修改", "取消中止");
    }

    static void upgradePuOrder(Map<String, Object> config) {
        ensureDetailField(config, "累计入库数量", "小数", true, true);
        // 先票后货入口：采购订单预开发票，货到后按入库单行勾稽
        ensureAction(config, "生单", "生成采购发票");
        ensureAction(config, "修改", "中止");
        ensureAction(config, "修改", "取消中止");
        ensureAction(config, "修改", "关闭");
        ensurePanelStateOption(config, "已关闭");
        ensurePuOrderSelectConfig(config);
        ensureMaterialReference(config);
        // 进货单已废弃（2026-11-04）：发票统一由采购入库单/采购退货单生成
        removeActionLike(config, "生成进货单");
    }

    /**
     * 采购订单「物料编码」参照：编码列显示编码本身，选中后带出名称/规格/单位。
     * displayField=存货名称 会让已选值在编码列显示成名称；没有 refMap 时物料名称/规格型号/单位
     * 都不会回填（与请购单 2026-11-03 的修复同口径）。
     */
    private static void ensureMaterialReference(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        for (Map<String, Object> tab : tabs) {
            List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
            for (Map<String, Object> field : fields) {
                if ("物料编码".equals(String.valueOf(field.get("dataName")))) {
                    String display = String.valueOf(field.getOrDefault("displayField", ""));
                    if (display.isBlank() || "存货名称".equals(display)) {
                        field.put("displayField", "存货编码");
                    }
                    if (!(field.get("refColumns") instanceof List<?> columns) || columns.isEmpty()) {
                        field.put("refColumns", List.of("存货编码", "存货名称", "规格型号", "计量单位", "停用"));
                    }
                    mergeRefMap(field, maps(
                            "存货名称", "物料名称", "规格型号", "规格型号", "计量单位", "单位"));
                } else if ("物料名称".equals(String.valueOf(field.get("dataName")))) {
                    mergeRefMap(field, maps(
                            "存货编码", "物料编码", "规格型号", "规格型号", "计量单位", "单位"));
                }
            }
            tab.put("fields", fields);
        }
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    /** refMap 按 to 合并：保留已有映射（可能被业务改过），只补缺失的带出字段。 */
    private static void mergeRefMap(Map<String, Object> field, List<Map<String, String>> additions) {
        List<Map<String, Object>> refMap = listOfMaps(field.get("refMap"));
        for (Map<String, String> add : additions) {
            boolean exists = refMap.stream().anyMatch(item -> add.get("to").equals(item.get("to")));
            if (!exists) refMap.add(new LinkedHashMap<>(add));
        }
        field.put("refMap", refMap);
    }

    static void upgradePurchaseIn(Map<String, Object> config) {
        ensureAction(config, "修改", "中止");
        ensureAction(config, "修改", "取消中止");
        ensureAction(config, "生单", "生成采购发票（普通采购）");
        // 来源链展示：检验单/到货单/采购订单在入库单上可追溯，来源戳字段保留在明细里
        ensureHeadField(config, "到货单号", "文本", null, "");
        ensureHeadField(config, "检验单号", "文本", null, "");
        ensureHeadField(config, "入库类别", "文本", null, "采购入库");
        ensureHeadField(config, "数据来源", "文本", null, "");
        for (String name : List.of("到货单号", "检验单号", "入库类别", "数据来源",
                "供应商", "供应商编码", "仓库", "采购订单号")) {
            ensureFormField(config, name);
        }
        ensureDetailField(config, "批号", "文本", false, true);
        for (String name : List.of("来源面板", "来源单号", "来源数量")) {
            ensureDetailField(config, name, "来源数量".equals(name) ? "小数" : "文本", false, false);
        }
        ensureDetailField(config, "来源行号", "整数", false, false);
        // 进货单已废弃：清掉历史配置里的「生成进货单(...)」系列按钮
        removeActionLike(config, "生成进货单");
    }

    /**
     * 到货单字段补齐：采购订单下推后在到货单上留痕（采购订单号/检验状态/数据来源），
     * 并把质检结果与累计执行量字段显式建模，避免生单后数据在 JSON 里却没有字段可展示。
     */
    static void upgradeArrivalIn(Map<String, Object> config) {
        ensureHeadField(config, "采购订单号", "文本", null, "");
        ensureHeadField(config, "检验状态", "下拉框", List.of("未检", "部分检", "已检"), "未检");
        ensureHeadField(config, "数据来源", "文本", null, "");
        // 来源类型：区分采购到货与委外回厂，决定后续只能生成哪种入库单
        ensureHeadField(config, "来源类型", "下拉框", List.of("采购", "委外"), "采购");
        ensureHeadField(config, "业务类型", "下拉框", List.of("普通采购", "采购退货", "委外回厂"), "普通采购");
        ensureDetailField(config, "检验数量", "小数", true, true);
        ensureDetailField(config, "合格数量", "小数", true, true);
        ensureDetailField(config, "不合格数量", "小数", true, true);
        ensureDetailField(config, "让步接收数量", "小数", true, true);
        ensureDetailField(config, "报废数量", "小数", true, true);
        ensureDetailField(config, "累计入库数量", "小数", true, true);
        ensureDetailField(config, "累计退货数量", "小数", true, true);
        // 到货单是收料通知、不是结算单据：单价由采购订单带入，历史/手工行允许留空，不能卡住保存
        makeDetailFieldOptional(config, "单价");
    }

    /** 明细字段改为非必填（幂等，只改 isRequired，不动默认值与显示）。 */
    private static void makeDetailFieldOptional(Map<String, Object> config, String name) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        for (Map<String, Object> tab : tabs) {
            List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
            for (Map<String, Object> field : fields) {
                if (name.equals(field.get("dataName"))) field.put("isRequired", false);
            }
            tab.put("fields", fields);
        }
        detail.put("tabs", tabs);
        config.put("detail", detail);
    }

    /** 选单：采购订单「选请购单」直接生成采购订单（拉式选单 → 生单）。 */
    private static void ensurePuOrderSelectConfig(Map<String, Object> config) {
        Map<String, Object> selectConfigs = map(config.get("selectConfigs"));
        if (!(selectConfigs.get("选请购单") instanceof Map<?, ?>)) {
            Map<String, Object> select = new LinkedHashMap<>();
            select.put("source", "PU_REQ");
            select.put("title", "选请购单");
            select.put("tip", "仅显示已审核且未中止的请购单，选中后按请购明细生成采购订单");
            select.put("detailKey", "items");
            select.put("columns", List.of("单据编号", "单据日期", "建议供应商", "请购人", "部门", "需求日期"));
            select.put("headerMap", maps("单据编号", "来源单号", "单据编号", "合同号",
                    "建议供应商", "供应商", "项目", "项目"));
            select.put("detailMap", maps(
                    "存货编码", "物料编码", "存货名称", "物料名称", "规格型号", "规格型号",
                    "采购单位", "单位", "数量", "数量", "单价", "单价", "金额", "金额",
                    "税率%", "税率%", "含税单价", "含税单价", "含税金额", "含税金额",
                    "需求日期", "预计到货日期", "现存量", "现存量"));
            select.put("generateButton", "生成采购订单");
            select.put("generateLabel", "生成采购订单");
            selectConfigs.put("选请购单", select);
            config.put("selectConfigs", selectConfigs);
        }
        if (!(config.get("selectConfig") instanceof Map<?, ?>)
                && selectConfigs.get("选请购单") instanceof Map<?, ?> select) {
            config.put("selectConfig", new LinkedHashMap<>((Map<String, Object>) select));
        }
    }

    private void upgrade(String code, java.util.function.Consumer<Map<String, Object>> mutator) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, code));
        if (row == null || row.getConfig() == null) return;
        try {
            Map<String, Object> config = json.readValue(row.getConfig(), new TypeReference<>() {});
            mutator.accept(config);
            row.setConfig(json.writeValueAsString(config));
            row.setVersion(VERSION);
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception e) {
            throw new IllegalStateException("采购面板配置升级失败：" + code, e);
        }
    }

    private void ensureAction(String panelCode, String groupName, String action) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (row == null || row.getConfig() == null) return;
        try {
            Map<String, Object> config = json.readValue(row.getConfig(), new TypeReference<>() {});
            ensureAction(config, groupName, action);
            row.setConfig(json.writeValueAsString(config));
            row.setVersion(VERSION);
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception e) {
            throw new IllegalStateException("采购面板动作补齐失败：" + panelCode, e);
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
        if (!groupFound) groups.add(Map.of("name", groupName, "actions", List.of(action)));
        metadata.put("buttonGroups", groups);

        List<Map<String, Object>> buttons = listOfMaps(metadata.get("panelButtons"));
        if (buttons.stream().noneMatch(button -> action.equals(String.valueOf(button.get("buttonName"))))) {
            buttons.add(Map.of("buttonName", action));
        }
        metadata.put("panelButtons", buttons);
        config.put("metadata", metadata);
    }

    /** 移除按钮名前缀匹配的动作（含 panelButtons），用于淘汰已废弃单据入口。 */
    private static void removeActionLike(Map<String, Object> config, String prefix) {
        Map<String, Object> metadata = map(config.get("metadata"));
        List<Map<String, Object>> groups = listOfMaps(metadata.get("buttonGroups"));
        for (Map<String, Object> group : groups) {
            if (!(group.get("actions") instanceof List<?> list)) continue;
            List<Object> kept = new ArrayList<>();
            for (Object action : list) {
                if (!String.valueOf(action).startsWith(prefix)) kept.add(action);
            }
            group.put("actions", kept);
        }
        metadata.put("buttonGroups", groups);
        List<Map<String, Object>> buttons = listOfMaps(metadata.get("panelButtons"));
        buttons.removeIf(button -> String.valueOf(button.get("buttonName")).startsWith(prefix));
        metadata.put("panelButtons", buttons);
        config.put("metadata", metadata);
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
            target = field(name, type);
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

    /** 表头字段补齐/升级：按 dataName 查找，既有字段改属性，缺失才追加。 */
    private static void ensureHeadField(Map<String, Object> config, String name, String type,
                                        List<String> options, Object defaultValue) {
        Map<String, Object> schema = map(config.get("dataSchema"));
        List<Map<String, Object>> fields = listOfMaps(schema.get("fields"));
        Map<String, Object> target = fields.stream()
                .filter(f -> name.equals(f.get("dataName"))).findFirst().orElse(null);
        if (target == null) {
            target = field(name, type);
            fields.add(target);
        }
        target.put("dataType", type);
        if (options != null) target.put("options", new ArrayList<>(options));
        if (defaultValue != null) target.put("defaultValue", defaultValue);
        schema.put("fields", fields);
        config.put("dataSchema", schema);
    }

    /** 表头字段进入表单页 fieldNames（逗号分隔字符串），已有不重复。 */
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

    private static void ensurePanelStateOption(Map<String, Object> config, String option) {
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> state = map(metadata.get("panelState"));
        List<Object> options = new ArrayList<>();
        if (state.get("defaultOptions") instanceof List<?> list) options.addAll(list);
        if (!options.contains(option)) options.add(option);
        state.put("defaultOptions", options);
        metadata.put("panelState", state);
        config.put("metadata", metadata);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> m ? new LinkedHashMap<>((Map<String, Object>) m) : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOfMaps(Object value) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Map<?, ?>) out.add(new LinkedHashMap<>((Map<String, Object>) item));
            }
        }
        return out;
    }

    private static Map<String, Object> field(String name, String type) {
        Map<String, Object> field = new LinkedHashMap<>();
        field.put("dataName", name);
        field.put("dataType", type);
        field.put("isRequired", false);
        field.put("defaultValue", type.equals("小数") || type.equals("整数") ? 0 : "");
        return field;
    }

    private static List<Map<String, String>> maps(String... values) {
        List<Map<String, String>> out = new ArrayList<>();
        for (int i = 0; i + 1 < values.length; i += 2) out.add(Map.of("from", values[i], "to", values[i + 1]));
        return out;
    }
}
