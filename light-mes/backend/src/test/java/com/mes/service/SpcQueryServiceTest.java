package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * SPC 过程能力接线：控制图之外，实测值齐全时必须真的输出 Cp/Cpk。
 *
 * <p>这条测试盯的是「接线」而不是算法 —— QualityControlService.capability 早有单测，
 * 但一直没人调用，SPC 面板上从来没有过程能力列（与 accountingAmount 同一类缺陷）。
 */
class SpcQueryServiceTest {

    private FormDataMapper formMapper;
    private SpcQueryService service;

    @BeforeEach
    void setUp() {
        formMapper = mock(FormDataMapper.class);
        service = new SpcQueryService(formMapper, new ObjectMapper(), new QualityControlService());
    }

    private FormData inspection(List<Map<String, Object>> items) {
        FormData fd = new FormData();
        fd.setPanelCode("INSPECTION");
        fd.setFormNo("JY-1");
        fd.setStatus("已审核");
        fd.setData("{}");
        try {
            Map<String, Object> detail = new LinkedHashMap<>();
            detail.put("items", items);
            fd.setDetailData(new ObjectMapper().writeValueAsString(detail));
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
        return fd;
    }

    private Map<String, Object> measured(double value, double low, double high) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("检验项目", "尺寸公差");
        row.put("实测值", value);
        row.put("规格下限", low);
        row.put("规格上限", high);
        return row;
    }

    @Test
    void measuredValuesWithSpecProduceCpAndCpk() {
        List<Map<String, Object>> items = new ArrayList<>();
        for (double v : new double[]{10.1, 9.9, 10.2, 9.8, 10.0}) items.add(measured(v, 9.5, 10.5));
        // 两类检验面板各查一次：第一次给数据，第二次给空，避免同一批样本被算两遍
        when(formMapper.selectList(any())).thenReturn(List.of(inspection(items)), List.of());

        List<Map<String, Object>> rows = service.controlCharts();

        assertEquals(1, rows.size());
        Map<String, Object> row = rows.get(0);
        assertNotNull(row.get("Cp"), "有实测值与规格就必须出 Cp");
        assertNotNull(row.get("Cpk"), "有实测值与规格就必须出 Cpk");
        assertEquals(5, ((Number) row.get("样本数")).intValue());
        BigDecimal cpk = new BigDecimal(String.valueOf(row.get("Cpk")));
        assertTrue(cpk.compareTo(BigDecimal.ONE) > 0, "这组样本 Cpk 应略高于 1");
        assertEquals("尚可", row.get("过程能力"), "Cpk 在 1.0~1.33 之间判为尚可");
    }

    @Test
    void passRateOnlyHasNoCapabilityColumns() {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("检验项目", "外观与包装");
        row.put("合格率", 95);
        when(formMapper.selectList(any())).thenReturn(List.of(inspection(List.of(row))), List.of());

        List<Map<String, Object>> rows = service.controlCharts();

        assertEquals(1, rows.size());
        // 合格率是比例数据、没有公差，硬算 Cpk 会得到无意义的数字
        assertNull(rows.get(0).get("Cpk"), "只有合格率时不应输出 Cpk");
        assertNull(rows.get(0).get("过程能力"));
        assertFalse(rows.get(0).containsKey("规格上限"));
    }

    @Test
    void missingSpecLeavesCapabilityEmpty() {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("检验项目", "化学成分");
        row.put("实测值", 10.0);
        when(formMapper.selectList(any())).thenReturn(List.of(inspection(List.of(row))), List.of());

        List<Map<String, Object>> rows = service.controlCharts();

        assertNull(rows.get(0).get("Cpk"), "缺规格上下限就不给能力指数");
    }

    /**
     * 同一检验项目同时存在实测值与旧数据的合格率时，必须分成两行。
     *
     * <p>回归保护：早先只按检验项目分组，实测值（10 上下）与合格率（100）被塞进同一序列，
     * 算出的均值 25、标准差 36.7、Cpk 负数 —— 看起来算出来了，其实整条控制图都是错的。
     */
    @Test
    void measuredAndPassRateAreNeverMixedInOneBucket() {
        List<Map<String, Object>> items = new ArrayList<>();
        items.add(measured(10.0, 9.5, 10.5));
        items.add(measured(10.2, 9.5, 10.5));
        items.add(measured(9.8, 9.5, 10.5));
        Map<String, Object> legacy = new LinkedHashMap<>();
        legacy.put("检验项目", "尺寸公差");
        legacy.put("合格率", 100);
        items.add(legacy);

        when(formMapper.selectList(any())).thenReturn(List.of(inspection(items)), List.of());

        List<Map<String, Object>> rows = service.controlCharts();

        assertEquals(2, rows.size(), "实测值与合格率各成一行");
        Map<String, Object> measuredRow = rows.stream()
                .filter(r -> "实测值".equals(r.get("特性值"))).findFirst().orElseThrow();
        Map<String, Object> rateRow = rows.stream()
                .filter(r -> "合格率%".equals(r.get("特性值"))).findFirst().orElseThrow();

        assertEquals(3, ((Number) measuredRow.get("样本数")).intValue(), "实测值只算自己的 3 个样本");
        assertEquals(0, new BigDecimal("10.0").compareTo(
                new BigDecimal(String.valueOf(measuredRow.get("均值")))), "均值应为 10.0，不能被 100 拉高");
        assertNotNull(measuredRow.get("Cpk"), "实测值组有规格，应当给过程能力");

        assertEquals(1, ((Number) rateRow.get("样本数")).intValue());
        assertNull(rateRow.get("Cpk"), "合格率组没有公差，不予能力指数");
    }

    @Test
    void capabilityLabelBucketsFollowIndustryThresholds() {
        assertEquals("充足", SpcQueryService.capabilityLabel(
                new QualityControlService.Capability(bd("1.5"), bd("1.5"), true)));
        // 1.33 恰好是「过程能力充足」的行业门槛，取等号算充足
        assertEquals("充足", SpcQueryService.capabilityLabel(
                new QualityControlService.Capability(bd("1.33"), bd("1.33"), true)));
        assertEquals("尚可", SpcQueryService.capabilityLabel(
                new QualityControlService.Capability(bd("1.32"), bd("1.32"), true)));
        assertEquals("尚可", SpcQueryService.capabilityLabel(
                new QualityControlService.Capability(bd("1.0"), bd("1.0"), true)));
        assertEquals("不足", SpcQueryService.capabilityLabel(
                new QualityControlService.Capability(bd("0.8"), bd("0.8"), false)));
        assertEquals("无规格", SpcQueryService.capabilityLabel(
                new QualityControlService.Capability(BigDecimal.ZERO, BigDecimal.ZERO, false)));
    }

    private static BigDecimal bd(String value) {
        return new BigDecimal(value);
    }
}
