package com.mes.service;

import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.List;

/**
 * 质量控制：三态判定 + SPC 控制图。
 *
 * 三态判定对齐金蝶：合格 / 让步接收 / 报废。让步接收的货可以放行使用但不判合格，
 * 与二元"合格/不合格"相比更贴近实际返工场景。
 */
@Service
public class QualityControlService {

    private static final BigDecimal HUNDRED = new BigDecimal("100");
    private static final MathContext MC = new MathContext(10, RoundingMode.HALF_UP);
    private static final BigDecimal EPS = new BigDecimal("0.000001");

    /** 三态判定结果。 */
    public record Disposition(String verdict, BigDecimal qualifiedQty,
                              BigDecimal concessionQty, BigDecimal scrapQty,
                              BigDecimal usableQty) {}

    /** SPC 控制图：均值、上下控制限、越界点索引。 */
    public record ControlChart(BigDecimal mean, BigDecimal sigma,
                               BigDecimal ucl, BigDecimal lcl, List<Integer> outOfControl) {}

    /** 过程能力：Cp/Cpk 与是否达标。 */
    public record Capability(BigDecimal cp, BigDecimal cpk, boolean capable) {}

    public QualityControlService() {
    }

    /**
     * 三态判定：检验数量必须等于 合格 + 让步接收 + 报废。
     * 可用数量 = 合格 + 让步接收（报废不可用）。
     */
    public Disposition disposition(BigDecimal inspected, BigDecimal qualified,
                                   BigDecimal concession, BigDecimal scrap) {
        BigDecimal ins = nz(inspected);
        BigDecimal q = nz(qualified);
        BigDecimal c = nz(concession);
        BigDecimal s = nz(scrap);
        if (ins.signum() <= 0) throw new IllegalArgumentException("检验数量必须大于 0");
        if (q.signum() < 0 || c.signum() < 0 || s.signum() < 0) {
            throw new IllegalArgumentException("合格/让步接收/报废数量不能为负");
        }
        BigDecimal sum = q.add(c).add(s);
        if (ins.subtract(sum).abs().compareTo(EPS) > 0) {
            throw new IllegalStateException("数量不守恒：检验数量 " + ins
                    + " 必须等于 合格 " + q + " + 让步接收 " + c + " + 报废 " + s);
        }
        String verdict;
        if (s.signum() == 0 && c.signum() == 0) verdict = "合格";
        else if (s.signum() == 0) verdict = "让步接收";
        else if (q.signum() == 0 && c.signum() == 0) verdict = "全批报废";
        else verdict = "部分报废";
        return new Disposition(verdict, q, c, s, q.add(c));
    }

    /** 控制图：按样本均值与样本标准差计算上下控制限；越界点单独列出。 */
    public ControlChart controlChart(List<BigDecimal> samples, int sigmaTimes) {
        List<BigDecimal> values = cleanSamples(samples);
        if (values.isEmpty()) {
            return new ControlChart(BigDecimal.ZERO, BigDecimal.ZERO,
                    BigDecimal.ZERO, BigDecimal.ZERO, List.of());
        }
        BigDecimal mean = mean(values);
        BigDecimal sigma = sigma(values, mean);
        BigDecimal k = BigDecimal.valueOf(Math.max(1, sigmaTimes));
        BigDecimal ucl = mean.add(sigma.multiply(k)).setScale(4, RoundingMode.HALF_UP);
        BigDecimal lcl = mean.subtract(sigma.multiply(k)).setScale(4, RoundingMode.HALF_UP);

        List<Integer> out = new ArrayList<>();
        for (int i = 0; i < values.size(); i++) {
            BigDecimal v = values.get(i);
            if (v.compareTo(ucl) > 0 || v.compareTo(lcl) < 0) out.add(i);
        }
        return new ControlChart(mean.setScale(4, RoundingMode.HALF_UP),
                sigma.setScale(4, RoundingMode.HALF_UP), ucl, lcl, out);
    }

    /**
     * 过程能力：Cp = (USL-LSL)/6σ，Cpk = min(USL-μ, μ-LSL)/3σ。
     * σ 为 0 时视为无限能力（无变异），达标。
     */
    public Capability capability(List<BigDecimal> samples, BigDecimal lsl, BigDecimal usl) {
        List<BigDecimal> values = cleanSamples(samples);
        BigDecimal low = nz(lsl);
        BigDecimal high = nz(usl);
        if (values.size() < 2 || high.compareTo(low) <= 0) {
            return new Capability(BigDecimal.ZERO, BigDecimal.ZERO, false);
        }
        BigDecimal mean = mean(values);
        BigDecimal sigma = sigma(values, mean);
        if (sigma.signum() == 0) {
            // 无变异：只要均值在规格内即视为无限能力
            boolean within = mean.compareTo(low) >= 0 && mean.compareTo(high) <= 0;
            BigDecimal cp = within ? HUNDRED : BigDecimal.ZERO;
            return new Capability(cp, cp, within);
        }
        BigDecimal cp = high.subtract(low).divide(sigma.multiply(new BigDecimal("6")), 4, RoundingMode.HALF_UP);
        BigDecimal upper = high.subtract(mean);
        BigDecimal lower = mean.subtract(low);
        BigDecimal min = upper.compareTo(lower) <= 0 ? upper : lower;
        BigDecimal cpk = min.divide(sigma.multiply(new BigDecimal("3")), 4, RoundingMode.HALF_UP);
        // 行业惯例 Cpk >= 1.33 视为过程能力充足
        boolean capable = cpk.compareTo(new BigDecimal("1.33")) >= 0;
        return new Capability(cp, cpk, capable);
    }

    private static List<BigDecimal> cleanSamples(List<BigDecimal> samples) {
        List<BigDecimal> out = new ArrayList<>();
        if (samples == null) return out;
        for (BigDecimal v : samples) if (v != null) out.add(v);
        return out;
    }

    private static BigDecimal mean(List<BigDecimal> values) {
        BigDecimal sum = BigDecimal.ZERO;
        for (BigDecimal v : values) sum = sum.add(v);
        return sum.divide(BigDecimal.valueOf(values.size()), MC);
    }

    /** 样本标准差（n-1 分母）；单样本返回 0。 */
    private static BigDecimal sigma(List<BigDecimal> values, BigDecimal mean) {
        if (values.size() < 2) return BigDecimal.ZERO;
        BigDecimal acc = BigDecimal.ZERO;
        for (BigDecimal v : values) {
            BigDecimal d = v.subtract(mean);
            acc = acc.add(d.multiply(d));
        }
        BigDecimal variance = acc.divide(BigDecimal.valueOf(values.size() - 1), MC);
        return variance.sqrt(MC);
    }

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
