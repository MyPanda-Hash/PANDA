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

    /** 单条转换：容忍空值，金额缺失按 0 处理，日期缺失按空串（逾期天数记 0）。 */
    FinanceStatementService.OpenItem toOpenItem(FinanceVoucher v) {
        return new FinanceStatementService.OpenItem(
                v.getVoucherType() == null ? "" : v.getVoucherType(),
                v.getPartner() == null ? "" : v.getPartner(),
                v.getAmount() == null ? BigDecimal.ZERO : v.getAmount(),
                v.getSettledAmount() == null ? BigDecimal.ZERO : v.getSettledAmount(),
                v.getBizDate() == null ? "" : v.getBizDate().toString(),
                v.getSourcePanel() == null ? "" : v.getSourcePanel(),
                v.getSourceNo() == null ? "" : v.getSourceNo());
    }
}
