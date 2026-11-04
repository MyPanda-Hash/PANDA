package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.FinanceVoucher;
import com.mes.mapper.FinanceVoucherMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 对账/账龄查询：把 finance_voucher 读出来交给 FinanceStatementService 出报表。
 *
 * 数据源是往来单据表（FinanceVoucher），不是会计凭证表（AccountingEntry），
 * 两者口径不同：前者记业务往来，后者记借贷分录。
 */
@Service
public class FinanceStatementQueryService {

    private final FinanceVoucherMapper voucherMapper;
    private final FinanceStatementService statementService;

    public FinanceStatementQueryService(FinanceVoucherMapper voucherMapper,
                                        FinanceStatementService statementService) {
        this.voucherMapper = voucherMapper;
        this.statementService = statementService;
    }

    /** 客户对账单：应收侧逐张未结单据。 */
    public List<Map<String, Object>> customerStatement() {
        return statementService.statement(openItems(), "AR");
    }

    /** 供应商对账单：应付侧逐张未结单据。 */
    public List<Map<String, Object>> supplierStatement() {
        return statementService.statement(openItems(), "AP");
    }

    /** 账龄分析表：应收应付合并，用「往来类型」列区分，按往来单位汇总。 */
    public List<Map<String, Object>> aging() {
        return aging(null);
    }

    /**
     * 账龄分析表（指定截止日期）：面板「截止日期」查询条件走这里。
     * 截止日期为空时回退到今天。
     */
    public List<Map<String, Object>> aging(String baseDate) {
        String base = (baseDate == null || baseDate.isBlank()) ? LocalDate.now().toString() : baseDate;
        return statementService.aging(openItems(), null, base);
    }

    /** 全部往来记录转 OpenItem；mapper 缺失时返回空列表而不是抛异常。 */
    List<FinanceStatementService.OpenItem> openItems() {
        if (voucherMapper == null) return List.of();
        List<FinanceVoucher> vouchers = voucherMapper.selectList(new LambdaQueryWrapper<>());
        List<FinanceStatementService.OpenItem> out = new ArrayList<>();
        for (FinanceVoucher v : vouchers) out.add(toOpenItem(v));
        return out;
    }

    /**
     * 单条转换：容忍空值，金额缺失按 0 处理，日期缺失按空串（逾期天数记 0）。
     *
     * <p>退货/红字（AR_RETURN / AP_RETURN）在库里和正向单据一样存正数金额，
     * 出报表时按负数冲减 —— 否则一张 1000 的销售退货会让客户应收「多出」1000，
     * 对账单和账龄一起虚增（2026-11-03 实测：应收 4000，实际 3000）。
     * 放在投影层做而不是改落库符号，是因为历史数据里已经存了正数，这样新旧单据口径一致。
     */
    FinanceStatementService.OpenItem toOpenItem(FinanceVoucher v) {
        String type = v.getVoucherType() == null ? "" : v.getVoucherType();
        return new FinanceStatementService.OpenItem(
                type,
                v.getPartner() == null ? "" : v.getPartner(),
                signed(type, v.getAmount()),
                signed(type, v.getSettledAmount()),
                v.getBizDate() == null ? "" : v.getBizDate().toString(),
                v.getSourcePanel() == null ? "" : v.getSourcePanel(),
                v.getSourceNo() == null ? "" : v.getSourceNo());
    }

    /** 退货/红字单据按负数冲减；其它类型原样。 */
    private static BigDecimal signed(String type, BigDecimal amount) {
        BigDecimal value = amount == null ? BigDecimal.ZERO : amount;
        if (value.signum() == 0) return value;
        String t = type == null ? "" : type.trim().toUpperCase();
        return t.endsWith("_RETURN") ? value.negate() : value;
    }
}
