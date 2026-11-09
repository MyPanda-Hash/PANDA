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
 * 工艺路线（ROUTE）改为主从结构：一条工艺路线 = 一张单据。
 *
 * <p>改造前 ROUTE 是 {@code singleDoc:true} 的单单据面板，三条路线的全部工序混在一张单据的
 * {@code detail.processes} 里，靠行内「工艺路线编码」区分。这带来三个问题：编码/名称每行重复、
 * 工序没有可靠先后、一条路线无法作为整体被 BOM 引用。改造后：
 * <ul>
 *   <li>工艺路线编码 / 名称 / 停用 升为<b>表头</b>字段（用户可自填）；</li>
 *   <li>明细只放工序，顺序由行序决定（界面不再显示「加工顺序」列，
 *       保存时由 {@code PxService#normalizeRouteDetail} 按行序写回）；</li>
 *   <li>取消 singleDoc，列表一行一条路线。</li>
 * </ul>
 *
 * <p>与 {@link BomRoutePanelUpgrade} 同一模式：启动时幂等修正运行库面板配置，
 * 避免存量库与新 {@code init.sql} 漂移。已改造过的库重复启动不会重复写入。
 */
@Component
public class RoutePanelUpgrade {

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public RoutePanelUpgrade(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void upgrade() {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "ROUTE"));
        if (row == null || row.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return;
        }
        boolean changed = promoteHeaderFields(config);
        changed |= hideProcessSequenceColumn(config);
        changed |= rewriteDetailColumns(config);
        if (!changed) return;
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception ignored) {
        }
    }

    /** 编码 / 名称 / 停用 从明细字段升为表头字段；取消 singleDoc。 */
    private boolean promoteHeaderFields(Map<String, Object> config) {
        boolean changed = false;
        Map<String, Object> metadata = map(config.get("metadata"));
        if (metadata.remove("singleDoc") != null) changed = true;
        // 工艺路线编码改为用户自填：autoCodeField 不再指向它，否则表单页会预填 ROUT-日期-序号
        // （预填逻辑在 PxService#getNewFormPermMatrix）。单据身份改用后端生成的 form_no。
        if (!"单据编号".equals(String.valueOf(metadata.get("autoCodeField")))) {
            metadata.put("autoCodeField", "单据编号");
            changed = true;
        }

        Map<String, Object> schema = map(config.get("dataSchema"));
        List<Map<String, Object>> fields = maps(schema.get("fields"));
        if (findField(fields, "工艺路线编码") == null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("dataName", "工艺路线编码");
            f.put("dataType", "文本");
            f.put("isRequired", true);
            fields.add(0, f);
            changed = true;
        }
        if (findField(fields, "工艺路线名称") == null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("dataName", "工艺路线名称");
            f.put("dataType", "文本");
            f.put("isRequired", true);
            fields.add(findField(fields, "工艺路线编码") == null ? 0 : 1, f);
            changed = true;
        }
        if (findField(fields, "停用") == null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("dataName", "停用");
            f.put("dataType", "是否");
            f.put("defaultValue", false);
            fields.add(f);
            changed = true;
        }
        // 单据身份展示：编号由后端按日期序号生成，只读
        if (findField(fields, "单据编号") == null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("dataName", "单据编号");
            f.put("dataType", "文本");
            f.put("readonly", true);
            fields.add(f);
            changed = true;
        }
        schema.put("fields", fields);
        config.put("dataSchema", schema);

        // 表单页要能看到这三个字段
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> pages = maps(pageDto.get("formPages"));
        for (Map<String, Object> page : pages) {
            String text = page.get("fieldNames") == null ? "" : String.valueOf(page.get("fieldNames"));
            List<String> missing = new ArrayList<>();
            for (String name : List.of("工艺路线编码", "工艺路线名称", "停用", "单据编号")) {
                if (!text.contains(name)) missing.add(name);
            }
            if (!missing.isEmpty()) {
                page.put("fieldNames", String.join(",", missing) + (text.isEmpty() ? "" : "," + text));
                changed = true;
            }
        }
        pageDto.put("formPages", pages);
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
        return changed;
    }

    /** 明细字段里去掉已升为表头的三个字段，避免同一信息两处维护；「加工顺序」标记 hidden（数据保留，界面不显示）。 */
    private boolean rewriteDetailColumns(Map<String, Object> config) {
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = maps(detail.get("tabs"));
        boolean changed = false;
        for (Map<String, Object> tab : tabs) {
            List<Map<String, Object>> fields = maps(tab.get("fields"));
            int before = fields.size();
            fields.removeIf((f) -> List.of("工艺路线编码", "工艺路线名称", "停用")
                    .contains(String.valueOf(f.get("dataName"))));
            if (fields.size() != before) {
                tab.put("fields", fields);
                changed = true;
            }
            // 加工顺序：数据仍要写（排产依赖），但顺序由行序决定，表单里不给用户改
            for (Map<String, Object> f : fields) {
                if (!"加工顺序".equals(String.valueOf(f.get("dataName")))) continue;
                if (Boolean.TRUE.equals(f.get("hidden"))) continue;
                f.put("hidden", true);
                tab.put("fields", fields);
                changed = true;
            }
        }
        detail.put("tabs", tabs);
        config.put("detail", detail);
        return changed;
    }

    /**
     * 列表明细列：去掉已升为表头的三个字段与「加工顺序」。
     *
     * <p>不补「道次」之类的派生列——工序先后由明细行序表达（表格自带行号），
     * 补一个没有数据字段支撑的列只会渲染成空白。
     */
    private boolean hideProcessSequenceColumn(Map<String, Object> config) {
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> tablePages = maps(pageDto.get("tablePages"));
        boolean changed = false;
        for (Map<String, Object> tp : tablePages) {
            List<Map<String, Object>> gridTabs = maps(tp.get("gridTabs"));
            for (Map<String, Object> gt : gridTabs) {
                if (!(gt.get("columns") instanceof List<?> rawCols)) continue;
                List<Object> cols = new ArrayList<>();
                boolean dropped = false;
                for (Object c : rawCols) {
                    String name = String.valueOf(c);
                    if (name.equals("工艺路线编码") || name.equals("工艺路线名称")
                            || name.equals("停用") || name.equals("加工顺序")) {
                        dropped = true;
                        continue;
                    }
                    cols.add(c);
                }
                if (!dropped) continue;
                gt.put("columns", cols);
                changed = true;
            }
            tp.put("gridTabs", gridTabs);
        }
        pageDto.put("tablePages", tablePages);
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
        return changed;
    }

    private Map<String, Object> findField(List<Map<String, Object>> fields, String name) {
        for (Map<String, Object> f : fields) {
            if (name.equals(String.valueOf(f.get("dataName")))) return f;
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> map(Object o) {
        return o instanceof Map<?, ?> m ? new LinkedHashMap<>((Map<String, Object>) m) : new LinkedHashMap<>();
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> maps(Object o) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (o instanceof List<?> list) {
            for (Object item : list) if (item instanceof Map<?, ?> m) out.add(new LinkedHashMap<>((Map<String, Object>) m));
        }
        return out;
    }
}
