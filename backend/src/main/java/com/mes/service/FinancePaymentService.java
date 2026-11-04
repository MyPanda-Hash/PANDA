package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.FinanceVoucher;
import com.mes.entity.FinanceWriteOff;
import com.mes.mapper.FinanceVoucherMapper;
import com.mes.mapper.FinanceWriteOffMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/**
 * 收付款核销：把一张收款单/付款单的金额按业务日期顺序分配到未结往来单。
 * 单厂单账套，人民币；核销记录逐条留痕。
 */
@Service
public class FinancePaymentService {

    /** 一条核销计划：核销到哪张往来单、多少金额。 */
    public record Allocation(FinanceVoucher voucher, BigDecimal amount) {}

    /** 手工核销的一行：核销到哪张来源单（单号）、多少金额。 */
    public record PlanItem(String sourceNo, BigDecimal amount) {}

    private final FinanceVoucherMapper voucherMapper;
    private final FinanceWriteOffMapper writeOffMapper;

    public FinancePaymentService(FinanceVoucherMapper voucherMapper, FinanceWriteOffMapper writeOffMapper) {
        this.voucherMapper = voucherMapper;
        this.writeOffMapper = writeOffMapper;
    }

    /**
     * 把金额按序分配到未结往来单；余额耗尽即停。
     * 允许部分核销，不校验总额是否覆盖（用于预览场景）。
     */
    public List<Allocation> allocate(BigDecimal payment, List<FinanceVoucher> vouchers) {
        List<Allocation> plan = new ArrayList<>();
        BigDecimal remain = nz(payment);
        if (remain.signum() <= 0 || vouchers == null || vouchers.isEmpty()) return plan;

        List<FinanceVoucher> ordered = new ArrayList<>(vouchers);
        ordered.sort(Comparator.comparing(
                v -> v.getBizDate() == null ? java.time.LocalDate.MAX : v.getBizDate()));

        for (FinanceVoucher v : ordered) {
            if (remain.signum() <= 0) break;
            BigDecimal open = remaining(nz(v.getAmount()), nz(v.getSettledAmount()));
            if (open.signum() <= 0) continue;
            BigDecimal take = remain.compareTo(open) >= 0 ? open : remain;
            plan.add(new Allocation(v, take));
            remain = remain.subtract(take);
        }
        return plan;
    }

    /**
     * 限额核销：收款额超过未结合计时拒绝，避免把预收当成核销吞掉。
     * 需要预收场景应单独录预收款单，而非超额核销。
     * 注意与 allocate 的区别：allocate 只按可用余额分配（余额耗尽即停），
     * 本方法额外校验总额，超额直接抛错。
     */
    public List<Allocation> allocateWithinLimit(BigDecimal payment, List<FinanceVoucher> vouchers) {
        BigDecimal openTotal = BigDecimal.ZERO;
        if (vouchers != null) {
            for (FinanceVoucher v : vouchers) {
                openTotal = openTotal.add(remaining(nz(v.getAmount()), nz(v.getSettledAmount())));
            }
        }
        if (nz(payment).compareTo(openTotal) > 0) {
            throw new IllegalStateException("核销金额超出未结合计：本次 " + nz(payment) + "，未结 " + openTotal);
        }
        return allocate(payment, vouchers);
    }

    /** 未结金额 = 原始金额 - 已核销，不为负。 */
    public BigDecimal remaining(BigDecimal amount, BigDecimal settled) {
        BigDecimal left = nz(amount).subtract(nz(settled));
        return left.signum() < 0 ? BigDecimal.ZERO : left;
    }

    /** 是否已结清。 */
    public boolean isFullySettled(BigDecimal amount, BigDecimal settled) {
        return remaining(amount, settled).signum() == 0;
    }

    /**
     * 按往来单位筛选候选往来单：核销不能跨客户/供应商串账。
     * 此前候选池只按 voucherType 取，A 客户的收款单能把 B 客户的应收核掉。
     * partner 为空一律不匹配（宁可报错，不猜）。
     */
    public static List<FinanceVoucher> filterByPartner(List<FinanceVoucher> candidates, String partner) {
        List<FinanceVoucher> scoped = new ArrayList<>();
        String want = partner == null ? "" : partner.trim();
        if (want.isEmpty() || candidates == null) return scoped;
        for (FinanceVoucher v : candidates) {
            String have = v.getPartner() == null ? "" : v.getPartner().trim();
            if (want.equals(have)) scoped.add(v);
        }
        return scoped;
    }

    /** 该收/付款单已核销合计：回写单据表头的「已核销金额」。 */
    public BigDecimal settledAmount(String paymentNo) {
        if (paymentNo == null || paymentNo.isBlank() || writeOffMapper == null) return BigDecimal.ZERO;
        BigDecimal total = BigDecimal.ZERO;
        for (FinanceWriteOff w : writeOffMapper.selectList(new LambdaQueryWrapper<FinanceWriteOff>()
                .eq(FinanceWriteOff::getPaymentNo, paymentNo))) {
            total = total.add(nz(w.getAmount()));
        }
        return total;
    }

    /**
     * 执行核销：写入核销记录并更新往来单已核销额与状态。
     *
     * <p>幂等：先 {@link #reverse} 掉这张收款单的历史核销，再按当前往来单余额重新分配。
     * 此前只删核销记录、不回滚往来单的已核销额，同一张收款单点两次核销会把应收核掉两遍。
     *
     * <p>限额：核销金额超过未结合计时直接拒绝 —— 多收的钱是预收款，应另立单据，
     * 不能靠超额核销把它吞掉（见 {@link #allocateWithinLimit}）。
     */
    @Transactional
    public List<Allocation> execute(String paymentNo, String voucherType, List<FinanceVoucher> candidates,
                                    BigDecimal payment, String operator) {
        if (paymentNo == null || paymentNo.isBlank()) throw new IllegalArgumentException("缺少收款/付款单编号");
        List<FinanceVoucher> scoped = new ArrayList<>();
        if (candidates != null) {
            for (FinanceVoucher v : candidates) {
                if (voucherType == null || voucherType.equals(v.getVoucherType())) scoped.add(v);
            }
        }
        // 先按「上一次核销已撤销」的口径预检（不写库），再真正回滚+落库：
        // 校验失败时库里不会留下「回滚了但没重新核销」的半截状态。
        previewReverse(paymentNo, scoped);
        List<Allocation> plan = allocateWithinLimit(payment, scoped);
        reverse(paymentNo);
        return applyAllocations(paymentNo, plan, operator);
    }

    /**
     * 手工核销：按用户指定的「哪张单、核多少」执行，支持只核一部分、指定到某张单。
     *
     * <p>校验三条：行必须对得上未结往来单；单行不超该单未结额；合计不超本次核销金额。
     * 合计小于收款额时就是部分核销，剩下的钱留在单据上（预收/待核）。
     */
    @Transactional
    public List<Allocation> executePlan(String paymentNo, String voucherType, List<FinanceVoucher> candidates,
                                        List<PlanItem> items, BigDecimal payment, String operator) {
        if (paymentNo == null || paymentNo.isBlank()) throw new IllegalArgumentException("缺少收款/付款单编号");
        List<FinanceVoucher> scoped = new ArrayList<>();
        if (candidates != null) {
            for (FinanceVoucher v : candidates) {
                if (voucherType == null || voucherType.equals(v.getVoucherType())) scoped.add(v);
            }
        }
        List<Allocation> plan;
        // 预检用「回滚后」的余额（见 execute 的注释），通过后才真正写库
        previewReverse(paymentNo, scoped);
        plan = validatePlan(items, scoped, payment);
        reverse(paymentNo);
        return applyAllocations(paymentNo, plan, operator);
    }

    /**
     * 把「这张单据上一次核销掉的金额」从内存候选里先减掉，等价于先回滚再校验，但不写库。
     * 这样校验失败时库里不会留下半截状态（PxController 把业务异常转成 409 返回，事务不会回滚）。
     */
    private void previewReverse(String paymentNo, List<FinanceVoucher> candidates) {
        if (paymentNo == null || paymentNo.isBlank() || writeOffMapper == null || candidates == null) return;
        java.util.Map<Long, BigDecimal> applied = new java.util.HashMap<>();
        for (FinanceWriteOff w : writeOffMapper.selectList(new LambdaQueryWrapper<FinanceWriteOff>()
                .eq(FinanceWriteOff::getPaymentNo, paymentNo))) {
            applied.merge(w.getVoucherId(), nzStatic(w.getAmount()), BigDecimal::add);
        }
        if (applied.isEmpty()) return;
        for (FinanceVoucher v : candidates) {
            if (v.getId() == null) continue;
            BigDecimal was = applied.get(v.getId());
            if (was == null) continue;
            BigDecimal settled = nzStatic(v.getSettledAmount()).subtract(was);
            v.setSettledAmount(settled.signum() < 0 ? BigDecimal.ZERO : settled);
        }
    }

    /**
     * 校验并规范化手工核销计划（纯函数，便于单测）。
     * 不依赖 mapper；同一来源单出现多行按顺序累加校验。
     */
    public static List<Allocation> validatePlan(List<PlanItem> items, List<FinanceVoucher> candidates,
                                                BigDecimal payment) {
        if (items == null || items.isEmpty()) return List.of();
        java.util.Map<String, FinanceVoucher> byNo = new java.util.HashMap<>();
        if (candidates != null) {
            for (FinanceVoucher v : candidates) {
                if (v.getSourceNo() != null) byNo.putIfAbsent(v.getSourceNo().trim(), v);
            }
        }
        BigDecimal remain = nzStatic(payment);
        // 同一张往来单出现多行时按行累加校验；用对象身份而不是 id，测试里没落库的单据 id 可能是 null
        java.util.Map<FinanceVoucher, BigDecimal> used = new java.util.IdentityHashMap<>();
        List<Allocation> plan = new ArrayList<>();
        for (PlanItem item : items) {
            String no = item.sourceNo() == null ? "" : item.sourceNo().trim();
            if (no.isEmpty()) throw new IllegalStateException("核销明细存在空的来源单号");
            FinanceVoucher v = byNo.get(no);
            if (v == null) {
                throw new IllegalStateException("核销明细的来源单号不在此次可核销范围：" + no);
            }
            BigDecimal want = nzStatic(item.amount());
            if (want.signum() <= 0) throw new IllegalStateException("核销金额必须大于 0：" + no);
            if (want.compareTo(remain) > 0) {
                throw new IllegalStateException("核销合计超出本次金额：本次 " + remain + "，" + no + " 还要 " + want);
            }
            BigDecimal already = used.getOrDefault(v, BigDecimal.ZERO);
            BigDecimal open = nzStatic(v.getAmount()).subtract(nzStatic(v.getSettledAmount())).subtract(already);
            if (open.signum() < 0) open = BigDecimal.ZERO;
            if (want.compareTo(open) > 0) {
                throw new IllegalStateException(no + " 核销金额超出未结额：本次 " + want + "，未结 " + open);
            }
            used.put(v, already.add(want));
            plan.add(new Allocation(v, want));
            remain = remain.subtract(want);
        }
        return plan;
    }

    /** 落核销记录并回写往来单已核销额；调用方负责先 {@link #reverse} 保证幂等。 */
    private List<Allocation> applyAllocations(String paymentNo, List<Allocation> plan, String operator) {
        for (Allocation a : plan) {
            FinanceVoucher v = a.voucher();
            BigDecimal settled = nz(v.getSettledAmount()).add(a.amount());
            v.setSettledAmount(settled);
            v.setStatus(isFullySettled(nz(v.getAmount()), settled) ? "已结算" : "部分结算");
            voucherMapper.updateById(v);

            FinanceWriteOff w = new FinanceWriteOff();
            w.setVoucherId(v.getId());
            w.setPaymentNo(paymentNo);
            w.setAmount(a.amount());
            w.setWriteOffTime(LocalDateTime.now());
            w.setOperator(operator);
            writeOffMapper.insert(w);
        }
        return plan;
    }

    /** 信用余额：退货单金额 - 已冲抵。 */
    public BigDecimal creditRemaining(FinanceVoucher credit) {
        return remaining(nz(credit.getAmount()), nz(credit.getSettledAmount()));
    }

    /**
     * 退货冲抵：把退货单的信用（应收侧的负数）冲减到同一往来单位的未结应收上。
     * 幂等：先回滚这张退货单的旧冲抵记录，再按当前余额重算，重复点不会冲两遍。
     */
    @Transactional
    public List<Allocation> setOff(String docNo, FinanceVoucher credit, List<FinanceVoucher> candidates,
                                   String operator) {
        if (docNo == null || docNo.isBlank()) throw new IllegalArgumentException("缺少退货单编号");
        if (credit == null) throw new IllegalStateException("该退货单没有产生往来记录");
        previewReverse(docNo, candidates);
        FinanceVoucher fresh = voucherMapper.selectById(credit.getId());
        if (fresh == null) throw new IllegalStateException("退货单往来记录不存在：" + docNo);
        BigDecimal left = creditRemaining(fresh);
        // 退货单自身的已冲抵额也要按「回滚后」算：先把它减回去再判断还剩多少信用
        BigDecimal alreadySetOff = BigDecimal.ZERO;
        if (writeOffMapper != null) {
            for (FinanceWriteOff w : writeOffMapper.selectList(new LambdaQueryWrapper<FinanceWriteOff>()
                    .eq(FinanceWriteOff::getPaymentNo, docNo))) {
                BigDecimal applied = nzStatic(w.getAmount());
                alreadySetOff = alreadySetOff.add(applied);
            }
        }
        left = nzStatic(left).add(alreadySetOff);
        if (left.signum() <= 0) return List.of();

        List<Allocation> plan = allocate(left, candidates);
        // 没有可冲的单据就直接返回（不 reverse、不写库），由调用方给出提示
        if (plan.isEmpty()) return List.of();
        reverse(docNo);
        BigDecimal applied = BigDecimal.ZERO;
        for (Allocation a : plan) {
            FinanceVoucher v = a.voucher();
            BigDecimal settled = nz(v.getSettledAmount()).add(a.amount());
            v.setSettledAmount(settled);
            v.setStatus(isFullySettled(nz(v.getAmount()), settled) ? "已结算" : "部分结算");
            voucherMapper.updateById(v);

            FinanceWriteOff w = new FinanceWriteOff();
            w.setVoucherId(v.getId());
            w.setPaymentNo(docNo);
            w.setAmount(a.amount());
            w.setWriteOffTime(LocalDateTime.now());
            w.setOperator(operator);
            writeOffMapper.insert(w);
            applied = applied.add(a.amount());
        }
        // 冲抵是「按当前余额重算」而不是累加：信用单的已冲抵额直接等于本次分配合计
        fresh.setSettledAmount(applied);
        fresh.setStatus(isFullySettled(nz(fresh.getAmount()), applied) ? "已结算" : "部分结算");
        voucherMapper.updateById(fresh);
        return plan;
    }

    /** 反冲抵：撤销该退货单的全部冲抵记录，并清空信用单的已冲抵额。 */
    @Transactional
    public int reverseSetOff(String docNo, FinanceVoucher credit) {
        int removed = reverse(docNo);
        if (credit != null) {
            FinanceVoucher fresh = voucherMapper.selectById(credit.getId());
            if (fresh != null) {
                fresh.setSettledAmount(BigDecimal.ZERO);
                fresh.setStatus("未结算");
                voucherMapper.updateById(fresh);
            }
        }
        return removed;
    }

    private static BigDecimal nzStatic(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    /** 反核销：删除该付款单的核销记录并回滚往来单已核销额。 */
    @Transactional
    public int reverse(String paymentNo) {
        List<FinanceWriteOff> rows = writeOffMapper.selectList(new LambdaQueryWrapper<FinanceWriteOff>()
                .eq(FinanceWriteOff::getPaymentNo, paymentNo));
        for (FinanceWriteOff w : rows) {
            FinanceVoucher v = voucherMapper.selectById(w.getVoucherId());
            if (v != null) {
                BigDecimal settled = nz(v.getSettledAmount()).subtract(nz(w.getAmount()));
                if (settled.signum() < 0) settled = BigDecimal.ZERO;
                v.setSettledAmount(settled);
                v.setStatus(settled.signum() == 0 ? "未结算" : "部分结算");
                voucherMapper.updateById(v);
            }
            writeOffMapper.deleteById(w.getId());
        }
        return rows.size();
    }

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
