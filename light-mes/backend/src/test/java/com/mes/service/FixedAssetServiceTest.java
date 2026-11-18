package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

/** 固定资产直线法折旧：公式与边界。 */
class FixedAssetServiceTest {

    /** 只测纯计算，构造器依赖不进这里。 */
    private final FixedAssetService service = new FixedAssetService(null, null, null, null);

    private Map<String, Object> card(double original, double residualRate, int months) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("原值", original);
        m.put("残值率%", residualRate);
        m.put("使用月数", months);
        return m;
    }

    @Test
    void straightLineMonthlyDepreciation() {
        // 120000 × (1 − 5%) ÷ 120 = 950
        assertEquals(0, new BigDecimal("950").compareTo(
                service.monthlyDepreciation(card(120000, 5, 120))));
    }

    @Test
    void zeroResidualRateDepreciatesFullValue() {
        assertEquals(0, new BigDecimal("1000").compareTo(
                service.monthlyDepreciation(card(12000, 0, 12))));
    }

    @Test
    void invalidInputsYieldZeroInsteadOfNegative() {
        assertEquals(0, BigDecimal.ZERO.compareTo(service.monthlyDepreciation(card(0, 5, 120))));
        assertEquals(0, BigDecimal.ZERO.compareTo(service.monthlyDepreciation(card(1000, 5, 0))));
        assertEquals(0, BigDecimal.ZERO.compareTo(service.monthlyDepreciation(card(1000, 100, 12))),
                "残值率 100% 时没有可折旧金额");
    }
}
