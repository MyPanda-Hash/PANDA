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
     * 执行核销：写入核销记录并更新往来单已核销额与状态。
     * 同一付款单重复提交时先清理旧记录，保证幂等。
     */
    @Transactional
    public int execute(String paymentNo, String voucherType, List<FinanceVoucher> candidates,
                       BigDecimal payment, String operator) {
        if (paymentNo == null || paymentNo.isBlank()) throw new IllegalArgumentException("缺少收款/付款单编号");
        writeOffMapper.delete(new LambdaQueryWrapper<FinanceWriteOff>()
                .eq(FinanceWriteOff::getPaymentNo, paymentNo));

        List<FinanceVoucher> scoped = new ArrayList<>();
        if (candidates != null) {
            for (FinanceVoucher v : candidates) {
                if (voucherType == null || voucherType.equals(v.getVoucherType())) scoped.add(v);
            }
        }
        List<Allocation> plan = allocate(payment, scoped);
        return applyPlan(paymentNo, plan, operator);
    }

    /**
     * 按单核销：用户勾选要核销的往来单及金额（不按 FIFO 猜），先清旧记录再落新计划。
     * 金额上限由调用方校验（不得超过该单未结额与收款额）。
     */
    @Transactional
    public int executePlan(String paymentNo, List<Allocation> plan, String operator) {
        if (paymentNo == null || paymentNo.isBlank()) throw new IllegalArgumentException("缺少收款/付款单编号");
        writeOffMapper.delete(new LambdaQueryWrapper<FinanceWriteOff>()
                .eq(FinanceWriteOff::getPaymentNo, paymentNo));
        return applyPlan(paymentNo, plan, operator);
    }

    private int applyPlan(String paymentNo, List<Allocation> plan, String operator) {
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
        return plan.size();
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
