package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.PanelConfig;
import com.mes.mapper.PanelConfigMapper;
import jakarta.annotation.PostConstruct;
import org.springframework.core.io.ClassPathResource;
import org.springframework.stereotype.Component;

import java.io.InputStream;
import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Keeps menu-backed SQL panels available after a clean database initialization.
 */
@Component
public class MenuPanelRegistry {

    static final String RESOURCE = "panels/missing-panel-configs.json";
    static final String VERSION = "MP1.0";
    static final Set<String> PANEL_CODES = Set.of(
            "LOCATION_ADJUST", "OUTSOURCE_FEE", "OUTSOURCE_IN", "OUTSOURCE_ISSUE", "OUTSOURCE_ORDER",
            "PU_COST_ALLOC", "PU_INVOICE", "PU_REQ_ANALYSIS", "SERIAL_NO", "SERIAL_STATUS",
            "SERIAL_TRACE", "STOCK_CHECK", "TRANSFER");

    private final PanelConfigMapper panelMapper;
    private final ObjectMapper json;

    public MenuPanelRegistry(PanelConfigMapper panelMapper, ObjectMapper json) {
        this.panelMapper = panelMapper;
        this.json = json;
    }

    @PostConstruct
    public void syncPanelConfigs() {
        for (PanelDefinition definition : loadDefinitions()) {
            upsert(definition);
        }
    }

    List<PanelDefinition> loadDefinitions() {
        try (InputStream input = new ClassPathResource(RESOURCE).getInputStream()) {
            List<PanelDefinition> definitions = json.readValue(input, new TypeReference<>() {});
            validate(definitions);
            return definitions;
        } catch (Exception e) {
            throw new IllegalStateException("加载菜单面板配置失败: " + RESOURCE, e);
        }
    }

    private void validate(List<PanelDefinition> definitions) {
        Set<String> codes = new HashSet<>();
        for (PanelDefinition definition : definitions) {
            if (!codes.add(definition.panelCode())) {
                throw new IllegalStateException("面板配置重复: " + definition.panelCode());
            }
            Object metadataValue = definition.config().get("metadata");
            if (!(metadataValue instanceof Map<?, ?> metadata)
                    || !definition.panelCode().equals(metadata.get("panelCode"))) {
                throw new IllegalStateException("面板编码与 metadata 不一致: " + definition.panelCode());
            }
        }
        if (!codes.equals(PANEL_CODES)) {
            Set<String> missing = new HashSet<>(PANEL_CODES);
            missing.removeAll(codes);
            Set<String> unexpected = new HashSet<>(codes);
            unexpected.removeAll(PANEL_CODES);
            throw new IllegalStateException("菜单面板配置集合不完整, missing=" + missing + ", unexpected=" + unexpected);
        }
    }

    private void upsert(PanelDefinition definition) {
        PanelConfig row = panelMapper.selectOne(new LambdaQueryWrapper<PanelConfig>()
                .eq(PanelConfig::getPanelCode, definition.panelCode()));
        LocalDateTime now = LocalDateTime.now();
        if (row == null) {
            row = new PanelConfig();
            row.setPanelCode(definition.panelCode());
            row.setCreateTime(now);
        }
        row.setPanelName(definition.panelName());
        row.setCategory(definition.category());
        row.setConfig(toJson(definition.config()));
        row.setVersion(VERSION);
        row.setUpdateTime(now);
        if (row.getId() == null) {
            panelMapper.insert(row);
        } else {
            panelMapper.updateById(row);
        }
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("菜单面板配置序列化失败", e);
        }
    }

    record PanelDefinition(String panelCode, String panelName, String category,
                           String version, Map<String, Object> config) {}
}
