package com.mes.service;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;

import jakarta.annotation.PostConstruct;

/**
 * 内置计量单位库：系统自带一套常用单位，开箱即用，部门可自行增删。
 *
 * <p>为什么用内置库而不是让客户先导一份计量单位表：单位是全系统共用的基础字典，
 * 存货、BOM、期初、报工都要引用它；让客户先填一份表只会多一道门槛，而单位本身
 * 没有客户差异。初始化向导里因此**不再有「计量单位」模块**。
 *
 * <p>合并规则：按「名称」判重，只补缺、不改已有——管理员改过的主单位/换算率不会被启动时覆盖。
 */
@Component
public class UomSeedRegistry {

    private static final Logger log = LoggerFactory.getLogger(UomSeedRegistry.class);
    private static final String PANEL = "UOM";
    private static final String DOC_NO = "计量单位";

    private final FormDataMapper formMapper;
    private final ObjectMapper json;

    public UomSeedRegistry(FormDataMapper formMapper, ObjectMapper json) {
        this.formMapper = formMapper;
        this.json = json;
    }

    @PostConstruct
    public void seed() {
        try {
            FormData doc = formMapper.selectOne(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, PANEL)
                    .eq(FormData::getFormNo, DOC_NO));
            if (doc == null) return;
            Map<String, Object> detail = parse(doc.getDetailData());
            List<Map<String, Object>> existing = rowsOf(detail, "uoms");
            List<Map<String, Object>> added = merge(existing);
            if (added.isEmpty()) return;
            detail.put("uoms", existing);
            doc.setDetailData(json.writeValueAsString(detail));
            doc.setUpdateTime(LocalDateTime.now());
            formMapper.updateById(doc);
            log.info("内置计量单位补齐 {} 条（现有 {} 条）", added.size(), existing.size());
        } catch (Exception e) {
            // 基础字典补不齐不该拦住启动，但必须留痕
            log.warn("内置计量单位补齐失败：{}", e.getMessage());
        }
    }

    /**
     * 把内置库并入现有列表：按名称判重，命中就跳过（不覆盖已有配置），
     * 未命中的追加到列表末尾，编号接着现有最大序号往上排。
     *
     * @return 本次新增的行（会被直接加进传入的 list 里）
     */
    static List<Map<String, Object>> merge(List<Map<String, Object>> existing) {
        Set<String> names = new LinkedHashSet<>();
        for (Map<String, Object> row : existing) names.add(String.valueOf(row.get("计量单位名称")).trim());
        int seq = nextSeq(existing);
        List<Map<String, Object>> added = new ArrayList<>();
        for (String name : UNIT_LIBRARY) {
            if (!names.add(name)) continue;
            seq++;
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("编号", "UOM-" + String.format("%03d", seq));
            row.put("计量单位编码", "U" + String.format("%02d", seq));
            row.put("计量单位名称", name);
            row.put("单位类型", "单计量");
            row.put("主单位", name);
            row.put("换算率", 1);
            existing.add(row);
            added.add(row);
        }
        return added;
    }

    /** 现有编号里最大的 UOM-nnn 序号；没有就从 0 开始。 */
    static int nextSeq(List<Map<String, Object>> rows) {
        int max = 0;
        for (Map<String, Object> row : rows) {
            String no = String.valueOf(row.get("编号"));
            if (!no.startsWith("UOM-")) continue;
            try {
                max = Math.max(max, Integer.parseInt(no.substring(4)));
            } catch (NumberFormatException ignore) {
                // 手工改过编号的跳过
            }
        }
        return max;
    }

    /**
     * 内置单位库：长度 / 面积 / 体积 / 容积 / 重量 / 数量 / 包装 / 时间 / 工艺参数。
     *
     * <p>计价单位（元、万元）不算实物单位，不放进来；换算关系（箱→件）属于业务口径，
     * 由客户按自己的装箱量配，系统不预设。
     */
    static final List<String> UNIT_LIBRARY = List.of(
            // 长度
            "毫米(mm)", "厘米(cm)", "分米(dm)", "米(m)", "千米(km)", "英寸(in)", "英尺(ft)", "码(yd)",
            // 面积 / 体积
            "平方米(m²)", "平方厘米(cm²)", "立方毫米(mm³)", "立方厘米(cm³)", "立方米(m³)",
            // 容积
            "毫升(ml)", "升(L)", "加仑(gal)",
            // 重量
            "毫克(mg)", "克(g)", "千克(kg)", "吨(t)", "市斤", "盎司(oz)", "磅(lb)",
            // 数量（计数类，无换算）
            "件", "个", "只", "支", "根", "条", "张", "片", "块", "枚", "粒", "颗",
            "套", "台", "座", "组", "副", "对", "打", "本", "卷", "捆", "扎", "把", "双",
            // 包装
            "箱", "包", "袋", "桶", "瓶", "罐", "盒", "托盘", "板", "托", "捆扎",
            // 时间（工时/节拍）
            "秒(s)", "分钟(min)", "小时(h)", "天(d)", "月",
            // 工艺参数
            "摄氏度(℃)", "帕(Pa)", "千帕(kPa)", "兆帕(MPa)", "千瓦时(kWh)", "安培(A)", "伏特(V)");

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(raw, new TypeReference<Map<String, Object>>() { });
        } catch (Exception e) {
            return new LinkedHashMap<>();
        }
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> rowsOf(Map<String, Object> detail, String key) {
        Object value = detail.get(key);
        if (value instanceof List<?> list) {
            List<Map<String, Object>> out = new ArrayList<>();
            for (Object row : list) {
                if (row instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
            }
            if (!out.isEmpty() || !list.isEmpty()) return out;
        }
        return new ArrayList<>();
    }
}