package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.AccountingEntry;
import com.mes.mapper.AccountingEntryMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 会计凭证生成：按业务单据类型套用凭证模板，产出借贷平衡的分录。
 *
 * 术语区分（避免与往来单据混淆）：
 * - FinanceVoucher = 往来单据（应收/应付）
 * - AccountingEntry = 会计凭证分录（借贷记账）
 *
 * 科目按《小企业会计准则》常用编码：1002 银行存款、1122 应收账款、
 * 1405 库存商品、2202 应付账款、6001 主营业务收入。
 */
@Service
public class AccountingService {

    /** 一条会计分录：科目 + 方向 + 金额 + 辅助信息。 */
    public record Entry(String accountCode, String accountName, String direction,
                        BigDecimal amount, String partner) {}

    /** 凭证模板：把一张单据折成固定的借贷结构。 */
    private record Template(String bizType, String sourcePanel, String debitCode, String debitName,
                            String creditCode, String creditName) {}

    private static final Map<String, Template> TEMPLATES = buildTemplates();

    private static Map<String, Template> buildTemplates() {
        Map<String, Template> m = new LinkedHashMap<>();
        // 采购入库：借 库存商品 / 贷 应付账款
        m.put("PURCHASE_IN", new Template("采购入库", "PURCHASE_IN", "1405", "库存商品", "2202", "应付账款"));
        // 采购发票：借 库存商品 / 贷 应付账款
        m.put("PU_INVOICE", new Template("采购发票", "PU_INVOICE", "1405", "库存商品", "2202", "应付账款"));
        // 销货单：借 应收账款 / 贷 主营业务收入 —— 销售收入的唯一入账口径。
        // 销售出库单只过库存、销售发票只登记开票，都不出凭证，避免同一笔业务重复确认收入。
        m.put("SALE_INV", new Template("销货单", "SALE_INV", "1122", "应收账款", "6001", "主营业务收入"));
        // 收款：借 银行存款 / 贷 应收账款
        m.put("RECEIPT", new Template("收款", "RECEIPT", "1002", "银行存款", "1122", "应收账款"));
        // 付款：借 应付账款 / 贷 银行存款
        m.put("RECEIPT_PAYABLE", new Template("付款", "RECEIPT_PAYABLE", "2202", "应付账款", "1002", "银行存款"));
        // 采购退货：借 应付账款 / 贷 库存商品（冲销）
        m.put("PURCHASE_RETURN", new Template("采购退货", "PURCHASE_RETURN", "2202", "应付账款", "1405", "库存商品"));
        // 销售退货：借 主营业务收入 / 贷 应收账款（冲销）
        m.put("SALE_RETURN", new Template("销售退货", "SALE_RETURN", "6001", "主营业务收入", "1122", "应收账款"));
        return java.util.Collections.unmodifiableMap(m);
    }

    private final AccountingEntryMapper entryMapper;

    public AccountingService(AccountingEntryMapper entryMapper) {
        this.entryMapper = entryMapper;
    }

    /** 面板对应的凭证模板；非会计面板返回 null。 */
    public Template templateOf(String panelCode) {
        return panelCode == null ? null : TEMPLATES.get(panelCode);
    }

    /** 按模板产出借贷两条分录；金额为零或非会计面板返回空列表。 */
    public List<Entry> buildEntries(String panelCode, String sourceNo, BigDecimal amount,
                                    String partner, String summary) {
        Template t = templateOf(panelCode);
        if (t == null) return List.of();
        BigDecimal money = amount == null ? BigDecimal.ZERO : amount;
        if (money.signum() == 0) return List.of();
        return List.of(
                new Entry(t.debitCode(), t.debitName(), "借", money, partner == null ? "" : partner),
                new Entry(t.creditCode(), t.creditName(), "贷", money, partner == null ? "" : partner));
    }

    /** 借贷是否平衡。 */
    public boolean isBalanced(List<Entry> entries) {
        BigDecimal debit = BigDecimal.ZERO;
        BigDecimal credit = BigDecimal.ZERO;
        for (Entry e : entries) {
            if ("借".equals(e.direction())) debit = debit.add(e.amount());
            else credit = credit.add(e.amount());
        }
        return debit.compareTo(credit) == 0;
    }

    /** 不平衡直接抛错，防止脏凭证落库。 */
    public void assertBalanced(List<Entry> entries) {
        if (!isBalanced(entries)) {
            throw new IllegalStateException("凭证借贷不平衡：" + entries);
        }
    }

    /**
     * 记账：按来源单据生成分录并落库。同一来源重复记账先清旧分录，保证幂等。
     */
    @Transactional
    public int post(String panelCode, String sourceNo, BigDecimal amount, String partner,
                    String summary, LocalDate bizDate) {
        if (templateOf(panelCode) == null) return 0;
        entryMapper.delete(new LambdaQueryWrapper<AccountingEntry>()
                .eq(AccountingEntry::getSourcePanel, panelCode)
                .eq(AccountingEntry::getSourceNo, sourceNo));
        List<Entry> entries = buildEntries(panelCode, sourceNo, amount, partner, summary);
        if (entries.isEmpty()) return 0;
        assertBalanced(entries);
        String voucherNo = buildVoucherNo(panelCode, sourceNo);
        for (Entry e : entries) {
            AccountingEntry entity = new AccountingEntry();
            entity.setVoucherNo(voucherNo);
            entity.setSourcePanel(panelCode);
            entity.setSourceNo(sourceNo);
            entity.setAccountCode(e.accountCode());
            entity.setAccountName(e.accountName());
            entity.setDirection(e.direction());
            entity.setAmount(e.amount());
            entity.setPartner(e.partner());
            entity.setSummary(summary);
            entity.setBizDate(bizDate);
            entity.setCreateTime(LocalDateTime.now());
            entryMapper.insert(entity);
        }
        return entries.size();
    }

    /** 冲销该来源单据的全部分录。 */
    @Transactional
    public int reverse(String panelCode, String sourceNo) {
        if (templateOf(panelCode) == null) return 0;
        return entryMapper.delete(new LambdaQueryWrapper<AccountingEntry>()
                .eq(AccountingEntry::getSourcePanel, panelCode)
                .eq(AccountingEntry::getSourceNo, sourceNo));
    }

    /** 科目余额：按科目汇总借贷发生额与余额方向。 */
    public List<Map<String, Object>> accountBalances() {
        List<AccountingEntry> all = entryMapper.selectList(null);
        Map<String, Map<String, Object>> agg = new LinkedHashMap<>();
        for (AccountingEntry e : all) {
            Map<String, Object> row = agg.computeIfAbsent(e.getAccountCode(), k -> {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("科目编码", e.getAccountCode());
                m.put("科目名称", e.getAccountName());
                m.put("借方发生额", BigDecimal.ZERO);
                m.put("贷方发生额", BigDecimal.ZERO);
                return m;
            });
            BigDecimal amt = e.getAmount() == null ? BigDecimal.ZERO : e.getAmount();
            if ("借".equals(e.getDirection())) {
                row.put("借方发生额", ((BigDecimal) row.get("借方发生额")).add(amt));
            } else {
                row.put("贷方发生额", ((BigDecimal) row.get("贷方发生额")).add(amt));
            }
        }
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> row : agg.values()) {
            BigDecimal debit = (BigDecimal) row.get("借方发生额");
            BigDecimal credit = (BigDecimal) row.get("贷方发生额");
            BigDecimal net = debit.subtract(credit);
            row.put("余额", net.abs());
            row.put("余额方向", net.signum() >= 0 ? "借" : "贷");
            out.add(row);
        }
        return out;
    }

    /** 明细账：按科目列出分录流水。 */
    public List<Map<String, Object>> accountDetail(String accountCode) {
        List<AccountingEntry> rows = accountCode == null || accountCode.isBlank()
                ? entryMapper.selectList(null)
                : entryMapper.selectList(new LambdaQueryWrapper<AccountingEntry>()
                        .eq(AccountingEntry::getAccountCode, accountCode));
        List<Map<String, Object>> out = new ArrayList<>();
        for (AccountingEntry e : rows) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("业务日期", e.getBizDate());
            m.put("凭证号", e.getVoucherNo());
            m.put("科目编码", e.getAccountCode());
            m.put("科目名称", e.getAccountName());
            m.put("方向", e.getDirection());
            m.put("金额", e.getAmount());
            m.put("往来单位", e.getPartner());
            m.put("摘要", e.getSummary());
            m.put("来源面板", e.getSourcePanel());
            m.put("来源单号", e.getSourceNo());
            out.add(m);
        }
        return out;
    }

    /** 会计科目列表：读 accounting_account 基础表。 */
    public List<Map<String, Object>> listAccounts() {
        List<Map<String, Object>> rows = entryMapper.selectAccounts();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> r : rows) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("科目编码", r.get("account_code"));
            m.put("科目名称", r.get("account_name"));
            m.put("科目类别", r.get("category"));
            m.put("余额方向", r.get("direction"));
            // enabled 由 JDBC 返回 Integer/Long/Short，不能与 BigDecimal 比较
            Object enabledFlag = r.get("enabled");
            boolean enabled = enabledFlag instanceof Number n && n.intValue() == 1;
            m.put("启用状态", enabled ? "启用" : "停用");
            out.add(m);
        }
        return out;
    }

    /** 凭证号：记-yyyyMMdd-来源单号，保证可与业务单据互查。 */
    private String buildVoucherNo(String panelCode, String sourceNo) {
        return "记-" + LocalDate.now().toString().replace("-", "") + "-" + sourceNo;
    }
}
