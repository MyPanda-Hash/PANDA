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
 * 领料申请单（MATERIAL_REQ）明细补「材料编码」列。
 *
 * <p>生单已经把材料编码写进明细了，但面板字段里没有它，仓库看不到子件编码，
 * 同名不同规格的料分不清。这里在「材料名称」前面插一列（只读），字段与列都补上。
 * 幂等：已经有的库重复启动不会重复插入。
 */
@Component
public class MaterialReqPanelUpgrade {

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public MaterialReqPanelUpgrade(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void upgrade() {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, "MATERIAL_REQ"));
        if (row == null || row.getConfig() == null) return;
        Map<String, Object> config;
        try {
            config = json.readValue(row.getConfig(), new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return;
        }
        boolean changed = addDetailField(config) | addColumns(config);
        if (!changed) return;
        try {
            row.setConfig(json.writeValueAsString(config));
            row.setUpdateTime(LocalDateTime.now());
            panelMapper.updateById(row);
        } catch (Exception ignored) {
        }
    }

    /** 明细字段：材料编码（参照看存货），摆在材料名称前面。 */
    @SuppressWarnings("unchecked")
    private boolean addDetailField(Map<String, Object> config) {
        Object detailObject = config.get("detail");
        if (!(detailObject instanceof Map<?, ?> detailRaw)) return false;
        Map<String, Object> detail = (Map<String, Object>) detailRaw;
        Object tabsObject = detail.get("tabs");
        if (!(tabsObject instanceof List<?> tabsRaw)) return false;
        for (Object tabObject : tabsRaw) {
            if (!(tabObject instanceof Map<?, ?> tabRaw)) continue;
            Map<String, Object> tab = (Map<String, Object>) tabRaw;
            if (!"items".equals(String.valueOf(tab.get("key")))) continue;
            Object fieldsObject = tab.get("fields");
            if (!(fieldsObject instanceof List<?> fieldsRaw)) return false;
            List<Map<String, Object>> fields = (List<Map<String, Object>>) fieldsRaw;
            for (Map<String, Object> field : fields) {
                if ("材料编码".equals(String.valueOf(field.get("dataName")))) return false;
            }
            Map<String, Object> code = new LinkedHashMap<>();
            code.put("dataName", "材料编码");
            code.put("dataType", "参照");
            code.put("refPanel", "INV");
            code.put("refField", "存货编码");
            code.put("displayField", "存货名称");
            code.put("isReadonly", true);
            int index = -1;
            for (int i = 0; i < fields.size(); i++) {
                if ("材料名称".equals(String.valueOf(fields.get(i).get("dataName")))) {
                    index = i;
                    break;
                }
            }
            if (index < 0) fields.add(code);
            else fields.add(index, code);
            tab.put("fields", fields);
            return true;
        }
        return false;
    }

    /** 列表列：同样把材料编码补在材料名称前（直接在原结构上改，避免改到副本）。 */
    @SuppressWarnings("unchecked")
    private boolean addColumns(Map<String, Object> config) {
        Object metadataObject = config.get("metadata");
        if (!(metadataObject instanceof Map<?, ?> metadataRaw)) return false;
        Map<String, Object> metadata = (Map<String, Object>) metadataRaw;
        Object pageDtoObject = metadata.get("panelPageDto");
        if (!(pageDtoObject instanceof Map<?, ?> pageDtoRaw)) return false;
        Map<String, Object> pageDto = (Map<String, Object>) pageDtoRaw;
        boolean changed = false;
        for (Object tableObject : listOf(pageDto.get("tablePages"))) {
            if (!(tableObject instanceof Map<?, ?> tableRaw)) continue;
            for (Object gridTabObject : listOf(((Map<String, Object>) tableRaw).get("gridTabs"))) {
                if (!(gridTabObject instanceof Map<?, ?> gridTabRaw)) continue;
                Map<String, Object> gridTab = (Map<String, Object>) gridTabRaw;
                Object columnsObject = gridTab.get("columns");
                if (!(columnsObject instanceof List<?> columnsRaw)) continue;
                List<String> columns = new ArrayList<>();
                for (Object value : columnsRaw) columns.add(String.valueOf(value));
                if (columns.contains("材料编码")) continue;
                int index = columns.indexOf("材料名称");
                if (index < 0) continue;
                columns.add(index, "材料编码");
                gridTab.put("columns", columns);
                changed = true;
            }
        }
        return changed;
    }

    private static List<?> listOf(Object value) {
        return value instanceof List<?> list ? list : List.of();
    }

}
