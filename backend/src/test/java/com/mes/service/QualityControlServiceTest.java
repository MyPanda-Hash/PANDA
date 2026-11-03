package com.mes.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 质量三态判定与 SPC：金蝶用「合格/让步接收/报废」三态，不是二元合格/不合格。
 * 让步接收 = 有瑕疵但可放行，会影响可用数量但不判合格。
 */
class QualityControlServiceTest {

    private final QualityControlService service = new QualityControlService();

    // ---------- 三态判定 ----------

    @Test
    void allQualifiedIsPass() {
        QualityControlService.Disposition d = service.disposition(
                bd("100"), bd("100"), bd("0"), bd("0"));

        assertEquals("合格", d.verdict());
        assertEquals(0, bd("100").compareTo(d.usableQty()));
    }

    @Test
    void concessionCountsAsUsableButNotQualified() {
        QualityControlService.Disposition d = service.disposition(
                bd("100"), bd("90"), bd("10"), bd("0"));

        assertEquals("让步接收", d.verdict());
        assertEquals(0, bd("100").compareTo(d.usableQty()), "让步接收仍可用");
        assertEquals(0, bd("10").compareTo(d.concessionQty()));
    }

    @Test
    void scrapIsNotUsable() {
        QualityControlService.Disposition d = service.disposition(
                bd("100"), bd("90"), bd("0"), bd("10"));

        assertEquals("部分报废", d.verdict());
        assertEquals(0, bd("90").compareTo(d.usableQty()));
    }

    @Test
    void allScrapIsRejected() {
        QualityControlService.Disposition d = service.disposition(
                bd("100"), bd("0"), bd("0"), bd("100"));

        assertEquals("全批报废", d.verdict());
        assertEquals(0, BigDecimal.ZERO.compareTo(d.usableQty()));
    }

    @Test
    void mixedConcessionAndScrap() {
        QualityControlService.Disposition d = service.disposition(
                bd("100"), bd("80"), bd("15"), bd("5"));

        assertEquals("部分报废", d.verdict());
        assertEquals(0, bd("95").compareTo(d.usableQty()), "合格 80 + 让步 15");
    }

    @Test
    void quantitiesMustBeConserved() {
        IllegalStateException e = assertThrows(IllegalStateException.class,
                () -> service.disposition(bd("100"), bd("90"), bd("5"), bd("0")));
        assertTrue(e.getMessage().contains("守恒") || e.getMessage().contains("等于"),
                "数量不守恒必须拒绝");
    }

    @Test
    void negativeQuantityIsRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> service.disposition(bd("100"), bd("-1"), bd("0"), bd("0")));
    }

    @Test
    void zeroInspectedIsRejected() {
        assertThrows(IllegalArgumentException.class,
                () -> service.disposition(BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO, BigDecimal.ZERO));
    }

    // ---------- SPC 控制图 ----------

    @Test
    void controlLimitsFromSamples() {
        // 样本 10,12,11,13,12,11,10,12,11,13 → 均值 11.5
        List<BigDecimal> samples = List.of(bd("10"), bd("12"), bd("11"), bd("13"), bd("12"),
                bd("11"), bd("10"), bd("12"), bd("11"), bd("13"));

        QualityControlService.ControlChart chart = service.controlChart(samples, 3);

        assertEquals(0, new BigDecimal("11.5").compareTo(chart.mean()));
        assertTrue(chart.ucl().compareTo(chart.mean()) > 0);
        assertTrue(chart.lcl().compareTo(chart.mean()) < 0);
        assertEquals(0, chart.outOfControl().size());
    }

    @Test
    void outOfControlPointsAreFlagged() {
        // 20 个点里第 20 个明显偏高。样本量大时单个离群值不会被自身拉平控制限
        List<BigDecimal> samples = new ArrayList<>();
        for (int i = 0; i < 19; i++) samples.add(new BigDecimal(i % 2 == 0 ? "10" : "11"));
        samples.add(new BigDecimal("40"));

        QualityControlService.ControlChart chart = service.controlChart(samples, 3);

        assertFalse(chart.outOfControl().isEmpty(), "离群值必须被标记");
        assertTrue(chart.outOfControl().contains(19), "第 20 个点应越界");
    }

    @Test
    void singleSampleHasZeroSigma() {
        QualityControlService.ControlChart chart = service.controlChart(List.of(bd("10")), 3);

        assertEquals(0, bd("10").compareTo(chart.mean()));
        assertEquals(0, BigDecimal.ZERO.compareTo(chart.sigma()));
    }

    @Test
    void emptySamplesProduceNullChart() {
        QualityControlService.ControlChart chart = service.controlChart(List.of(), 3);

        assertEquals(0, BigDecimal.ZERO.compareTo(chart.mean()));
        assertTrue(chart.outOfControl().isEmpty());
    }

    @Test
    void processCapabilityIsReported() {
        List<BigDecimal> samples = List.of(bd("10"), bd("11"), bd("10"), bd("11"), bd("10"), bd("11"));

        QualityControlService.Capability cap = service.capability(samples, bd("8"), bd("13"));

        assertTrue(cap.cpk().compareTo(BigDecimal.ZERO) > 0, "CPK 应为正");
        assertTrue(cap.capable(), "样本集中在中心应判有能力");
    }

    @Test
    void capabilityFailsWhenSigmaZero() {
        List<BigDecimal> samples = List.of(bd("10"), bd("10"), bd("10"));

        QualityControlService.Capability cap = service.capability(samples, bd("8"), bd("13"));

        // 无变异时 Cpk 视为无限能力，但仍要返回可判定结果
        assertEquals(true, cap.capable());
    }

    private static BigDecimal bd(String v) {
        return new BigDecimal(v);
    }
}
