package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 工程变更执行（ECO）：把一张已审核的工程变更单落到目标单据上。
 *
 * <p>变更单只描述「改哪张单、改多大、为什么」，真正生效是这里：
 * 找到目标物料清单/工艺路线，把版本号往上抬一版（小版本 V1.0→V1.1，大版本 V1.0→V2.0），
 * 盖上变更单号与最后变更日期。这样「这版 BOM 是谁改的」能一路查回去。
 */
@Service
public class EngChangeService {

    private static final String PANEL = "ENG_CHANGE";
    private static final String BOM = "BOM";
    private static final String ROUTE = "ROUTE";

    private final FormDataMapper formMapper;
    private final ObjectMapper json;

    public EngChangeService(FormDataMapper formMapper, ObjectMapper json) {
        this.formMapper = formMapper;
        this.json = json;
    }

    /**
     * 版本号进位：小版本 V1.0→V1.1；大版本 V1.0→V2.0。
     * 解析不出数字时按 V1.0 起步，不丢版本（宁可重编也不写空）。
     */
    public static String nextVersion(String current, String level) {
        int major = 1, minor = 0;
        String text = current == null ? "" : current.trim();
        if (text.matches("(?i)v?\\d+(\\.\\d+)?")) {
            String digits = text.replaceFirst("(?i)^v", "");
            String[] parts = digits.split("\\.");
            major = Integer.parseInt(parts[0]);
            if (parts.length > 1) minor = Integer.parseInt(parts[1]);
        } else if (!text.isEmpty()) {
            // 非标准版本号（如 "A"、"初版"）：不动原值，按 V1.0 起算，避免拼出畸形字符串
            major = 1;
            minor = 0;
        }
        boolean majorBump = "大版本".equals(level == null ? "" : level.trim());
        if (majorBump) return "V" + (major + 1) + ".0";
        return "V" + major + "." + (minor + 1);
    }

    /** 执行变更：逐行找到目标单据并改版；任何一行找不到目标就整单不动（事务回滚）。 */
    @Transactional
    public Map<String, Object> apply(String changeNo) {
        if (changeNo == null || changeNo.isBlank()) throw new IllegalArgumentException("缺少变更单号");
        FormData change = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, PANEL).eq(FormData::getFormNo, changeNo));
        if (change == null) throw new IllegalArgumentException("工程变更单不存在：" + changeNo);
        if (!"已审核".equals(change.getStatus())) {
            throw new IllegalStateException("工程变更单 " + changeNo + " 还没审核，审核后才能执行");
        }
        List<Map<String, Object>> rows = detailRows(parse(change.getDetailData()));
        if (rows.isEmpty()) throw new IllegalStateException("工程变更单没有变更对象");

        List<Map<String, Object>> applied = new ArrayList<>();
        for (Map<String, Object> row : rows) {
            String objectType = text(first(row, "对象类型"));
            String objectCode = text(first(row, "对象编码"));
            String level = text(first(row, "变更级别"));
            if (objectCode.isBlank()) continue;
            String panel = "工艺路线".equals(objectType) ? ROUTE : BOM;
            FormData target = findTarget(panel, objectCode);
            if (target == null) {
                throw new IllegalStateException("找不到" + objectType + "「" + objectCode + "」，变更未执行");
            }
            Map<String, Object> head = parse(target.getData());
            String oldVersion = text(head.get("版本号"));
            if (oldVersion.isBlank()) oldVersion = "V1.0";
            String newVersion = nextVersion(oldVersion, level);
            head.put("版本号", newVersion);
            head.put("变更单号", changeNo);
            head.put("最后变更日期", LocalDate.now().toString());
            target.setData(toJson(head));
            target.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(target);
            Map<String, Object> one = new LinkedHashMap<>();
            one.put("对象类型", objectType.isBlank() ? "物料清单" : objectType);
            one.put("对象编码", objectCode);
            one.put("原版本", oldVersion);
            one.put("新版本", newVersion);
            one.put("单据号", target.getFormNo());
            applied.add(one);
        }
        if (applied.isEmpty()) throw new IllegalStateException("变更明细里没有可执行的对象编码");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("变更单号", changeNo);
        out.put("执行条数", applied.size());
        out.put("rows", applied);
        return out;
    }

    /**
     * 找目标单据：工艺路线按表头「工艺路线编码」；物料清单先按表头产品编码，
     * 再按明细行的「物料清单编码」匹配（BOM 的版本本来就挂在明细行上）。
     */
    FormData findTarget(String panel, String code) {
        List<FormData> docs = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panel).orderByDesc(FormData::getId));
        String headField = ROUTE.equals(panel) ? "工艺路线编码" : "产品编码";
        for (FormData doc : docs) {
            if ("作废".equals(doc.getStatus())) continue;
            if (code.equals(text(parse(doc.getData()).get(headField)))) return doc;
            for (Map<String, Object> row : detailRows(parse(doc.getDetailData()))) {
                if (code.equals(text(first(row, "物料清单编码", "工艺路线编码")))) return doc;
            }
        }
        return null;
    }

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return Map.of();
        try {
            return json.readValue(raw, new TypeReference<>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    private String toJson(Object value) {
        try {
            return json.writeValueAsString(value);
        } catch (Exception e) {
            throw new IllegalStateException("变更数据序列化失败", e);
        }
    }

    private static List<Map<String, Object>> detailRows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (detail == null) return out;
        for (Object value : detail.values()) {
            if (!(value instanceof List<?> list)) continue;
            for (Object item : list) {
                if (item instanceof Map<?, ?> map) {
                    Map<String, Object> typed = new LinkedHashMap<>();
                    map.forEach((k, v) -> typed.put(String.valueOf(k), v));
                    out.add(typed);
                }
            }
        }
        return out;
    }

    private static Object first(Map<String, Object> row, String... keys) {
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return null;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }
}
