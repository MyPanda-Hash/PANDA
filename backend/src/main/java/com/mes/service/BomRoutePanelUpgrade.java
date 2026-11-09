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
 * BOM 面板补齐「产品编码 / 工艺路线编码」两个表头字段。
 *
 * <p>产品与工艺路线的绑定配置在 BOM 上（路线档案本身在 PDM 的 ROUTE）；
 * 加工单生单按这两个字段展开工序，缺了它们只能回退默认三道工序。
 */
@Component
public class BomRoutePanelUpgrade {

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public BomRoutePanelUpgrade(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void upgrade() {
        ensureInvRouteColumn();
        ensureManualOrderRouteAction();
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "BOM"));
        if (row == null || row.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return;
        }
        boolean changed = false;
        Map<String, Object> schema = map(config.get("dataSchema"));
        List<Map<String, Object>> fields = maps(schema.get("fields"));
        if (findField(fields, "产品编码") == null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("dataName", "产品编码");
            f.put("dataType", "参照");
            f.put("refPanel", "INV");
            f.put("refField", "存货编码");
            f.put("displayField", "存货名称");
            f.put("isRequired", false);
            fields.add(f);
            changed = true;
        }
        Map<String, Object> routeField = findField(fields, "工艺路线编码");
        if (routeField == null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("dataName", "工艺路线编码");
            f.put("dataType", "参照");
            f.put("refPanel", "ROUTE");
            f.put("refField", "工艺路线编码");
            f.put("displayField", "工艺路线名称");
            f.put("isRequired", false);
            // 走专用弹窗：按顺序横向展示该路线的工序流程，并标注委外工序
            f.put("refDialog", "route-flow");
            fields.add(f);
            changed = true;
        } else if (!"route-flow".equals(routeField.get("refDialog"))) {
            routeField.put("refDialog", "route-flow");
            changed = true;
        }
        // 选完路线把名称一起带出（名称只读，改由编码决定；后端读出口还会再派生一次兜底）
        if (routeField != null && routeField.get("refMap") == null) {
            routeField.put("refMap", new ArrayList<>(List.of(Map.of("from", "工艺路线名称", "to", "工艺路线名称"))));
            changed = true;
        }
        // 只读展示列：BOM 上同时看到编码与名称，名称不允许手改
        if (findField(fields, "工艺路线名称") == null) {
            Map<String, Object> f = new LinkedHashMap<>();
            f.put("dataName", "工艺路线名称");
            f.put("dataType", "文本");
            f.put("isRequired", false);
            f.put("readonly", true);
            fields.add(f);
            changed = true;
        }
        schema.put("fields", fields);
        config.put("dataSchema", schema);
        // 表单也要能看到这两个字段
        Map<String, Object> metadata = map(config.get("metadata"));
        Map<String, Object> pageDto = map(metadata.get("panelPageDto"));
        List<Map<String, Object>> pages = maps(pageDto.get("formPages"));
        for (Map<String, Object> page : pages) {
            Object names = page.get("fieldNames");
            String text = names == null ? "" : String.valueOf(names);
            List<String> add = new ArrayList<>();
            if (!text.contains("产品编码")) add.add("产品编码");
            if (!text.contains("工艺路线编码")) add.add("工艺路线编码");
            if (!text.contains("工艺路线名称")) add.add("工艺路线名称");
            if (!add.isEmpty()) {
                page.put("fieldNames", (text.isEmpty() ? "" : text + ",") + String.join(",", add));
                changed = true;
            }
        }
        pageDto.put("formPages", pages);
        metadata.put("panelPageDto", pageDto);
        config.put("metadata", metadata);
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception ignored) {
        }
    }

    /** 手工建加工单：工具栏加「选工艺路线」（按路线带出工序）。 */
    private void ensureManualOrderRouteAction() {
        PanelConfig mo = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "MANU_ORDER"));
        if (mo == null || mo.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(mo.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return;
        }
        Map<String, Object> metadata = map(config.get("metadata"));
        List<Map<String, Object>> groups = maps(metadata.get("buttonGroups"));
        for (Map<String, Object> g : groups) {
            Object actions = g.get("actions");
            if (actions instanceof List<?> list && list.contains("选工艺路线")) return;
        }
        Map<String, Object> group = new LinkedHashMap<>();
        group.put("name", "选工艺路线");
        group.put("actions", List.of("选工艺路线"));
        int insertAt = groups.size();
        for (int i = 0; i < groups.size(); i++) {
            if ("生单".equals(String.valueOf(groups.get(i).get("name")))) { insertAt = i; break; }
        }
        groups.add(insertAt, group);
        metadata.put("buttonGroups", groups);
        config.put("metadata", metadata);
        try {
            mo.setConfig(json.writeValueAsString(config));
            mo.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(mo);
        } catch (Exception ignored) {
        }
    }

    /** 存货档案加只读列「工艺路线」（值由后端按默认 BOM 反查填充，不回写档案）。 */
    private void ensureInvRouteColumn() {
        PanelConfig inv = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "INV"));
        if (inv == null || inv.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(inv.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return;
        }
        boolean changed = false;
        Map<String, Object> detail = map(config.get("detail"));
        List<Map<String, Object>> tabs = maps(detail.get("tabs"));
        for (Map<String, Object> tab : tabs) {
            List<Map<String, Object>> fields = maps(tab.get("fields"));
            if (findField(fields, "工艺路线") == null) {
                Map<String, Object> f = new LinkedHashMap<>();
                f.put("dataName", "工艺路线");
                f.put("dataType", "文本");
                f.put("isRequired", false);
                f.put("readonly", true);
                fields.add(f);
                tab.put("fields", fields);
                changed = true;
            }
        }
        detail.put("tabs", tabs);
        config.put("detail", detail);
        if (!changed) return;
        try {
            inv.setConfig(json.writeValueAsString(config));
            inv.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(inv);
        } catch (Exception ignored) {
        }
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
            for (Object item : list) if (item instanceof Map<?, ?> m) out.add(new LinkedHashMap<>((Map<String, Object>) item));
        }
        return out;
    }
}
