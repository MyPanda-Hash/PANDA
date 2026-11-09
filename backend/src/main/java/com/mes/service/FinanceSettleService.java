package com.mes.service;

import com.mes.entity.FinanceVoucher;
import com.mes.mapper.FinanceVoucherMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 应收应付结算：把已审核的业务单据折算成应收单(AR)/应付单(AP)，并提供核销运算。
 * 单厂单账套、人民币，不引入多币种与组织维度。
 */
@Service
public class FinanceSettleService {

    /**
     * 产生应付(AP)的面板：票到确认（2026-11-06）——采购发票审核才产生应付，
     * 采购入库只入库存、不再生成 AP，避免入库+发票双记。
     */
    private static final Set<String> PAYABLE_PANELS = Set.of("PU_INVOICE", "OUTSOURCE_IN", "OUTSOURCE_FEE");

    /**
     * 产生应收(AR)的面板：只有销货单。
     *
     * <p>出库单只做出库与成本、发票只是票据；三张单都挂应收会让同一笔发货
     * 在账龄里翻两倍（实测出库 16,272 + 销货 16,272）。
     */
    private static final Set<String> RECEIVABLE_PANELS = Set.of("SALE_INV");

    /** 冲减应付(AP_RETURN)：采购退货，应付减少。 */
    private static final Set<String> PAYABLE_RETURN_PANELS = Set.of("PURCHASE_RETURN");

    /** 冲减应收(AR_RETURN)：销售退货，应收减少。 */
    private static final Set<String> RECEIVABLE_RETURN_PANELS = Set.of("SALE_RETURN");

    /** 一条往来结算：AR 应收 / AP 应付 / AR_RETURN 应收冲减 / AP_RETURN 应付冲减。 */
    public record Settlement(String type, String partner, String sourcePanel, String sourceNo,
                             BigDecimal amount, LocalDate bizDate, String remark) {}

    /** 核销结果：本次核销额与剩余未结额。 */
    public record SettleResult(BigDecimal settled, BigDecimal outstanding) {
        public boolean settledCoversAll() { return outstanding.signum() == 0; }
    }

    private final FinanceVoucherMapper voucherMapper;
    private final StockTxnService stockTxnService; // 与库存落账共享事务边界，保留注入位

    public FinanceSettleService(FinanceVoucherMapper voucherMapper, StockTxnService stockTxnService) {
        this.voucherMapper = voucherMapper;
        this.stockTxnService = stockTxnService;
    }

    /** 面板对应的往来类型；非财务面板返回 null。 */
    public String typeOf(String panelCode) {
        if (panelCode == null) return null;
        if (PAYABLE_PANELS.contains(panelCode)) return "AP";
        if (RECEIVABLE_PANELS.contains(panelCode)) return "AR";
        if (PAYABLE_RETURN_PANELS.contains(panelCode)) return "AP_RETURN";
        if (RECEIVABLE_RETURN_PANELS.contains(panelCode)) return "AR_RETURN";
        return null;
    }

    /** 把一张单据的表头与明细折算成结算记录；非财务面板或金额为零返回 null。 */
    public Settlement buildSettlement(String panelCode, String formNo, Map<String, Object> head,
                                      Map<String, Object> detail) {
        String type = typeOf(panelCode);
        if (type == null) return null;
        // AP_RETURN 也属应付侧，需按前缀判断而不是精确等于 AP
        String partner = type.startsWith("AP")
                ? text(first(head, "供应商", "往来单位", "供应商名称"))
                : text(first(head, "客户", "往来单位", "客户名称"));
        BigDecimal amount = BigDecimal.ZERO;
        for (Map<String, Object> row : allDetailRows(detail)) {
            amount = amount.add(rowAmount(row));
        }
        if (amount.signum() == 0) return null;
        return new Settlement(type, partner, panelCode, formNo, amount,
                date(first(head, "单据日期")), text(first(head, "备注")));
    }

    /** 核销运算：本次核销额不超过未结额，剩余额不出现负数。 */
    public SettleResult computeSettle(BigDecimal writeOff, BigDecimal outstanding) {
        BigDecimal remain = nz(outstanding);
        BigDecimal want = nz(writeOff);
        if (remain.signum() <= 0) return new SettleResult(BigDecimal.ZERO, BigDecimal.ZERO);
        if (want.signum() <= 0) return new SettleResult(BigDecimal.ZERO, remain);
        BigDecimal settled = want.compareTo(remain) >= 0 ? remain : want;
        return new SettleResult(settled, remain.subtract(settled));
    }

    /**
     * 审核后生成往来单据：同一来源单据重复审核时先删除旧记录，保证幂等。
     * 金额为累计明细金额，来源单号与面板写入以便追溯。
     */
    @Transactional
    public int post(String panelCode, String formNo, Map<String, Object> head, Map<String, Object> detail) {
        if (typeOf(panelCode) == null) return 0;
        voucherMapper.delete(new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<FinanceVoucher>()
                .eq(FinanceVoucher::getSourcePanel, panelCode)
                .eq(FinanceVoucher::getSourceNo, formNo));
        Settlement s = buildSettlement(panelCode, formNo, head, detail);
        if (s == null) return 0;
        FinanceVoucher entity = new FinanceVoucher();
        entity.setVoucherType(s.type());
        entity.setPartner(s.partner());
        entity.setSourcePanel(s.sourcePanel());
        entity.setSourceNo(s.sourceNo());
        entity.setAmount(s.amount());
        entity.setSettledAmount(BigDecimal.ZERO);
        entity.setStatus("未结算");
        entity.setBizDate(s.bizDate());
        entity.setRemark(s.remark());
        entity.setCreateTime(LocalDateTime.now());
        voucherMapper.insert(entity);
        return 1;
    }

    /** 弃审时冲销该来源单据的往来记录。 */
    @Transactional
    public int reverse(String panelCode, String formNo) {
        if (typeOf(panelCode) == null) return 0;
        return voucherMapper.delete(new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<FinanceVoucher>()
                .eq(FinanceVoucher::getSourcePanel, panelCode)
                .eq(FinanceVoucher::getSourceNo, formNo));
    }

    /** 单行金额：优先取金额/含税金额/价税合计/销售金额，其次数量×单价。 */
    private static BigDecimal rowAmount(Map<String, Object> row) {
        BigDecimal amount = number(first(row, "含税金额", "价税合计", "销售金额", "金额", "成本金额", "实收金额"));
        if (amount.signum() != 0) return amount;
        BigDecimal qty = number(first(row, "实收数量", "数量"));
        BigDecimal price = number(first(row, "含税单价", "售价", "单价", "成本价"));
        return qty.multiply(price);
    }

    // ---------- 取值助手（与 StockTxnService 同名同义，统一口径） ----------

    private static Object first(Map<String, Object> row, String... keys) {
        if (row == null) return null;
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> allDetailRows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (detail == null) return out;
        for (Object value : detail.values()) {
            if (value instanceof List<?> list) {
                for (Object item : list) {
                    if (item instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
                }
            }
        }
        return out;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        if (value instanceof BigDecimal decimal) return decimal;
        if (value instanceof Number num) return new BigDecimal(num.toString());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return BigDecimal.ZERO;
        }
    }

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    private static LocalDate date(Object value) {
        String t = text(value);
        if (t.isBlank()) return null;
        try {
            return LocalDate.parse(t.length() > 10 ? t.substring(0, 10) : t);
        } catch (Exception ignored) {
            return null;
        }
    }
}
