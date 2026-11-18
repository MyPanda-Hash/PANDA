package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 序列号管理落地（2026-11-12）：把序列号挂在出入库单据上，不再靠一张孤立的登记单。
 *
 * <p>1）存货档案（INV）加「序列号管理 / 管理方式」，由档案决定哪些存货要管序列号；
 * 2）入库单（采购入库/产成品入库/委外入库/其他入库）加「序列号」明细页签 → 入库登记；
 * 3）出库单（销售出库/材料出库/其他出库）加同名页签 → 出库核销。
 *
 * <p>用 {@link ApplicationRunner} 而不是 {@code @PostConstruct}：{@code MenuPanelRegistry} 会在启动时
 * 用种子文件整体覆盖 OUTSOURCE_IN 等面板配置，必须等它写完再补页签，否则会被冲掉。
 */
@Component
public class SerialPanelUpgrade implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(SerialPanelUpgrade.class);

    /** 入库登记序列号的单据。 */
    static final List<String> INBOUND = List.of("PURCHASE_IN", "FINISH_IN", "OUTSOURCE_IN", "OTHER_IN");
    /** 出库核销序列号的单据。 */
    static final List<String> OUTBOUND = List.of("SALE_OUT", "MATERIAL_OUT", "OTHER_OUT");
    /** 手工补录的登记单：明细本身就是序列号行。 */
    static final String MANUAL = "SERIAL_NO";

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public SerialPanelUpgrade(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @Override
    public void run(ApplicationArguments args) {
        try {
            upgradeInventory();
            for (String code : INBOUND) upgradeDocument(code, true);
            for (String code : OUTBOUND) upgradeDocument(code, false);
        } catch (Exception e) {
            log.warn("序列号面板升级失败（不影响启动）: {}", e.getMessage());
        }
    }

    /** 存货档案：加「序列号管理」开关列与字段。 */
    void upgradeInventory() {
        Map<String, Object> config = load("INV");
        if (config == null) return;
        boolean changed = ensureDetailField(config, "序列号管理", "是否", false, "否");
        changed |= ensureDetailField(config, "序列号管理方式", "下拉框", true,
                "入库登记", List.of("入库登记", "出库核销"));
        changed |= ensureGridColumn(config, "序列号管理");
        if (changed) save("INV", config);
    }

    /** 出入库单据：补「序列号」页签（入库带仓库/入库日期，出库只带存货+序列号）。 */
    void upgradeDocument(String panelCode, boolean inbound) {
        Map<String, Object> config = load(panelCode);
        if (config == null) return;
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        String tabKey = SerialNumberService.TAB;
        if (tabs.stream().noneMatch(tab -> tabKey.equals(tab.get("key")))) {
            tabs.add(serialTab(inbound));
            detail.put("tabs", tabs);
            config.put("detail", detail);
            save(panelCode, config);
        }
    }

    private Map<String, Object> serialTab(boolean inbound) {
        List<Map<String, Object>> fields = new ArrayList<>();
        fields.add(field("存货编码", "文本", true));
        fields.add(field("存货名称", "文本", false));
        fields.add(field("序列号", "文本", true));
        if (inbound) {
            fields.add(field("规格型号", "文本", false));
            fields.add(field("计量单位", "文本", false));
            fields.add(reference("仓库", "WH", "仓库名称"));
        }
        fields.add(field("备注", "文本", false));
        Map<String, Object> tab = new LinkedHashMap<>();
        tab.put("key", SerialNumberService.TAB);
        tab.put("label", inbound ? "序列号（入库登记）" : "序列号（出库核销）");
        tab.put("fields", fields);
        return tab;
    }

    // ---------- 配置读写 ----------

    Map<String, Object> load(String panelCode) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (row == null || row.getConfig() == null) return null;
        try {
            return json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return null;
        }
    }

    private void save(String panelCode, Map<String, Object> config) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, panelCode));
        if (row == null) return;
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception ignored) {
        }
    }

    private boolean ensureGridColumn(Map<String, Object> config, String column) {
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> pages = listOfMaps(pageDto.get("tablePages"));
        boolean changed = false;
        for (Map<String, Object> page : pages) {
            List<Map<String, Object>> grids = listOfMaps(page.get("gridTabs"));
            for (Map<String, Object> grid : grids) {
                if (!(grid.get("columns") instanceof List<?> columns)) continue;
                List<Object> merged = new ArrayList<>(columns);
                if (!merged.contains(column)) merged.add(column);
                grid.put("columns", merged);
                changed = true;
            }
            page.put("gridTabs", grids);
        }
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
        return changed;
    }

    private boolean ensureDetailField(Map<String, Object> config, String name, String type,
                                      boolean required, Object defaultValue) {
        return ensureDetailField(config, name, type, required, defaultValue, null);
    }

    private boolean ensureDetailField(Map<String, Object> config, String name, String type,
                                      boolean required, Object defaultValue, List<String> options) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = listOfMaps(detail.get("tabs"));
        for (Map<String, Object> tab : tabs) {
            List<Map<String, Object>> fields = listOfMaps(tab.get("fields"));
            boolean exists = fields.stream().anyMatch(f -> name.equals(f.get("dataName")));
            if (exists) return false;
            Map<String, Object> f = field(name, type, required);
            if (defaultValue != null) f.put("defaultValue", defaultValue);
            if (options != null) f.put("options", options);
            fields.add(f);
            tab.put("fields", fields);
            detail.put("tabs", tabs);
            config.put("detail", detail);
            return true;
        }
        return false;
    }

    private static Map<String, Object> field(String name, String type, boolean required) {
        Map<String, Object> f = new LinkedHashMap<>();
        f.put("dataName", name);
        f.put("dataType", type);
        f.put("isRequired", required);
        return f;
    }

    private static Map<String, Object> reference(String name, String refPanel, String displayField) {
        Map<String, Object> f = field(name, "参照", false);
        f.put("refPanel", refPanel);
        f.put("refField", displayField);
        f.put("displayField", displayField);
        return f;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> map(Object value) {
        return value instanceof Map<?, ?> raw ? (Map<String, Object>) raw : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> listOfMaps(Object value) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (value instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Map<?, ?> raw) out.add((Map<String, Object>) raw);
            }
        }
        return out;
    }

    static List<String> inboundPanels() {
        return Arrays.asList(INBOUND.toArray(String[]::new));
    }
}
