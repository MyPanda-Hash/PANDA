package com.mes.service;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 三大财务报表：资产负债表、利润表、现金流量表。
 *
 * <p>补的是 2026-11-16 评估出的最大缺口 —— 系统此前有凭证、有科目余额、有明细账，
 * 却出不了任何一张对外报表，等于「有账无表」。
 *
 * <p>口径说明（都在报表里写清楚，避免看表的人误读）：
 * <ul>
 *   <li>资产负债表按科目 <b>category</b> 归类；<b>本年利润</b> 一栏包含「已结转的 4103 余额」
 *       与「尚未结转的本期损益」，否则结转前表右永远少一块，看起来像数据错了；</li>
 *   <li>利润表按损益类科目发生额计算，净利润 = 收入 − 成本 − 期间费用；</li>
 *   <li>现金流量表<b>由凭证推断</b>：凡分录里出现库存现金/银行存款，就看同一张凭证的对方科目
 *       属于经营、投资还是筹资。这是「直接法取数」的简化版，没有单独的现金流量项目字段，
 *       所以报表里明确标注为推断口径。</li>
 * </ul>
 */
@Service
public class FinancialStatementService {

    /** 现金及现金等价物科目。 */
    private static final Set<String> CASH_ACCOUNTS = Set.of("1001", "1002");

    /** 对方科目 → 现金流类别。未命中的按经营处理（多数往来与损益都属经营）。 */
    private static final Map<String, String> INVESTING_PREFIX = Map.of(
            "1601", "投资", "1602", "投资", "1604", "投资", "1606", "投资",
            "1701", "投资", "1801", "投资");
    private static final Map<String, String> FINANCING_PREFIX = Map.of(
            "4001", "筹资", "4002", "筹资", "4103", "筹资", "4104", "筹资",
            "2001", "筹资", "2501", "筹资");

    private final JdbcTemplate jdbc;

    public FinancialStatementService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    // ---------------- 资产负债表 ----------------

    /**
     * 资产负债表：<b>截至期末</b>的累计余额（不是单期发生额）。
     *
     * <p>这是资产负债表与利润表的根本区别：后者看「这一期赚了多少」，
     * 前者看「到这个月底为止家底有多少」。所以这里用 period &lt;= 选定期间。
     *
     * @param period 会计期间 yyyy-MM；为空表示全部期间
     */
    public List<Map<String, Object>> balanceSheet(String period) {
        String where = blank(period) ? "" : " AND e.period <= ? ";
        Object[] args = blank(period) ? new Object[]{} : new Object[]{period};

        List<Map<String, Object>> balances = jdbc.queryForList(
                "SELECT a.category, a.account_code, a.account_name, a.direction, "
                        + "       COALESCE(SUM(CASE WHEN e.direction = '借' THEN e.amount ELSE -e.amount END), 0) AS net "
                        + "FROM accounting_account a "
                        + "LEFT JOIN accounting_entry e ON e.account_code = a.account_code " + where
                        + "GROUP BY a.category, a.account_code, a.account_name, a.direction "
                        + "ORDER BY a.account_code", args);

        // 未结转损益：收入 − 费用。结转后 4103 已有余额，这里只在结转前起作用。
        BigDecimal uncarried = uncarriedProfit(period);

        BigDecimal assets = BigDecimal.ZERO;
        BigDecimal liabilities = BigDecimal.ZERO;
        BigDecimal equity = BigDecimal.ZERO;
        List<Map<String, Object>> out = new ArrayList<>();

        for (Map<String, Object> row : balances) {
            String category = str(row.get("category"));
            if ("收入".equals(category) || "费用".equals(category)) continue; // 损益类不进资产负债表
            BigDecimal net = decimal(row.get("net"));
            // 正负号按「科目类别」定，不按 direction：
            // 累计折旧(1602) 是资产类但余额在贷方，属备抵科目，必须冲减资产（显示 -950）。
            // 按 direction 判断会把它算成 +950，资产虚增一倍折旧额（实测踩到）。
            // 正负号按「科目类别」定，不按 direction：
            // 累计折旧(1602) 是资产类但余额在贷方，属备抵科目，必须冲减资产（显示 -950）。
            // 按 direction 判断会把它算成 +950，资产虚增一倍折旧额（实测踩到）。
            BigDecimal amount = creditNature(category) ? net.negate() : net;
            out.add(line(category, str(row.get("account_code")), str(row.get("account_name")), amount));
            if ("资产".equals(category)) assets = assets.add(amount);
            else if ("负债".equals(category)) liabilities = liabilities.add(amount);
            else if ("权益".equals(category)) equity = equity.add(amount);
        }

        if (uncarried.signum() != 0) {
            out.add(line("权益", "4103", "本年利润（未结转损益）", uncarried));
            equity = equity.add(uncarried);
        }

        // 凭证里出现科目表没有的科目时，报表绝不能静默丢金额 ——
        // 那会让「资产负债表不平」变成一个查不出原因的谜。
        List<Map<String, Object>> orphans = unmatchedAccounts(period);
        for (Map<String, Object> orphan : orphans) {
            out.add(line("异常", str(orphan.get("account_code")),
                    "科目表未登记的科目：" + str(orphan.get("account_name")),
                    decimal(orphan.get("net"))));
        }

        out.add(summary("资产合计", assets));
        out.add(summary("负债合计", liabilities));
        out.add(summary("所有者权益合计", equity));
        BigDecimal diff = assets.subtract(liabilities).subtract(equity);
        out.add(summary("负债和所有者权益合计", liabilities.add(equity)));
        out.add(summary("差额", diff));
        out.add(summary("是否平衡", diff.abs().compareTo(new BigDecimal("0.005")) < 0 ? "平衡" : "不平衡"));
        return out;
    }

    /** 凭证用到、但科目表里没有的科目（会导致报表丢金额，必须显式暴露）。 */
    private List<Map<String, Object>> unmatchedAccounts(String period) {
        String where = blank(period) ? "" : " AND e.period <= ? ";
        Object[] args = blank(period) ? new Object[]{} : new Object[]{period};
        return jdbc.queryForList(
                "SELECT e.account_code, e.account_name, "
                        + "       SUM(CASE WHEN e.direction = '借' THEN e.amount ELSE -e.amount END) AS net "
                        + "FROM accounting_entry e "
                        + "LEFT JOIN accounting_account a ON a.account_code = e.account_code "
                        + "WHERE a.account_code IS NULL " + where
                        + "GROUP BY e.account_code, e.account_name", args);
    }

    /** 未结转损益 = 截至期末的收入类贷方净额 − 费用类借方净额（与资产负债表同口径）。 */
    private BigDecimal uncarriedProfit(String period) {
        String where = blank(period) ? "" : " AND e.period <= ? ";
        Object[] args = blank(period) ? new Object[]{} : new Object[]{period};
        Map<String, Object> row = jdbc.queryForMap(
                "SELECT "
                        + " COALESCE(SUM(CASE WHEN a.category = '收入' "
                        + "        THEN CASE WHEN e.direction = '贷' THEN e.amount ELSE -e.amount END ELSE 0 END), 0) AS income, "
                        + " COALESCE(SUM(CASE WHEN a.category = '费用' "
                        + "        THEN CASE WHEN e.direction = '借' THEN e.amount ELSE -e.amount END ELSE 0 END), 0) AS expense "
                        + "FROM accounting_entry e JOIN accounting_account a ON a.account_code = e.account_code "
                        + "WHERE 1 = 1 " + where, args);
        return decimal(row.get("income")).subtract(decimal(row.get("expense")));
    }

    // ---------------- 利润表 ----------------

    /** 利润表：收入 − 成本 − 期间费用 = 净利润。 */
    public List<Map<String, Object>> incomeStatement(String period) {
        String where = blank(period) ? "" : " AND e.period = ? ";
        Object[] args = blank(period) ? new Object[]{} : new Object[]{period};
        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT a.category, a.account_code, a.account_name, a.direction, "
                        + "       COALESCE(SUM(CASE WHEN e.direction = '借' THEN e.amount ELSE -e.amount END), 0) AS net "
                        + "FROM accounting_entry e JOIN accounting_account a ON a.account_code = e.account_code "
                        + "WHERE a.category IN ('收入', '费用') "
                        // 排除期末结转凭证：它把损益科目冲平，留着会让利润表在结账后变成 0
                        + "  AND e.source_panel <> 'PERIOD_CLOSE' " + where
                        + "GROUP BY a.category, a.account_code, a.account_name, a.direction "
                        + "ORDER BY a.account_code", args);

        List<Map<String, Object>> out = new ArrayList<>();
        BigDecimal income = BigDecimal.ZERO;
        BigDecimal expense = BigDecimal.ZERO;
        for (Map<String, Object> row : rows) {
            BigDecimal net = decimal(row.get("net"));
            // 收入类：贷方为正；费用类：借方为正
            BigDecimal amount = "收入".equals(str(row.get("category"))) ? net.negate() : net;
            if (amount.signum() == 0) continue;
            out.add(line(str(row.get("category")), str(row.get("account_code")),
                    str(row.get("account_name")), amount));
            if ("收入".equals(str(row.get("category")))) income = income.add(amount);
            else expense = expense.add(amount);
        }
        out.add(summary("营业收入合计", income));
        out.add(summary("成本费用合计", expense));
        out.add(summary("净利润", income.subtract(expense)));
        out.add(summary("期间", blank(period) ? "全部" : period));
        return out;
    }

    // ---------------- 现金流量表 ----------------

    /**
     * 现金流量表（推断口径）：按凭证看现金科目对应的对方科目分类。
     *
     * <p>之所以是推断：系统没有「现金流量项目」字段，只能靠对方科目归类。
     * 报表里会带一行「取数口径 = 按对方科目推断」，避免被当成审计口径使用。
     */
    public List<Map<String, Object>> cashFlow(String period) {
        String where = blank(period) ? "" : " AND e.period = ? ";
        Object[] args = blank(period) ? new Object[]{} : new Object[]{period};
        List<Map<String, Object>> entries = jdbc.queryForList(
                "SELECT e.voucher_no, e.account_code, e.direction, e.amount, e.period "
                        + "FROM accounting_entry e WHERE 1 = 1 " + where
                        + "ORDER BY e.voucher_no, e.id", args);

        // 先按凭证聚合：现金净额 + 该凭证涉及的对方科目
        Map<String, BigDecimal> cashByVoucher = new LinkedHashMap<>();
        Map<String, List<String>> counterpartByVoucher = new LinkedHashMap<>();
        for (Map<String, Object> row : entries) {
            String voucher = str(row.get("voucher_no"));
            String code = str(row.get("account_code"));
            BigDecimal amount = decimal(row.get("amount"));
            boolean isCash = CASH_ACCOUNTS.contains(code);
            if (isCash) {
                BigDecimal signed = "借".equals(str(row.get("direction"))) ? amount : amount.negate();
                cashByVoucher.merge(voucher, signed, BigDecimal::add);
            } else {
                counterpartByVoucher.computeIfAbsent(voucher, k -> new ArrayList<>()).add(code);
            }
        }

        BigDecimal operating = BigDecimal.ZERO;
        BigDecimal investing = BigDecimal.ZERO;
        BigDecimal financing = BigDecimal.ZERO;
        for (Map.Entry<String, BigDecimal> e : cashByVoucher.entrySet()) {
            BigDecimal amount = e.getValue();
            if (amount.signum() == 0) continue;
            String bucket = classify(counterpartByVoucher.getOrDefault(e.getKey(), List.of()));
            switch (bucket) {
                case "投资" -> investing = investing.add(amount);
                case "筹资" -> financing = financing.add(amount);
                default -> operating = operating.add(amount);
            }
        }

        List<Map<String, Object>> out = new ArrayList<>();
        out.add(line("经营活动", "CF-OP", "经营活动产生的现金流量净额", operating));
        out.add(line("投资活动", "CF-INV", "投资活动产生的现金流量净额", investing));
        out.add(line("筹资活动", "CF-FIN", "筹资活动产生的现金流量净额", financing));
        out.add(summary("现金及现金等价物净增加额", operating.add(investing).add(financing)));
        out.add(summary("期间", blank(period) ? "全部" : period));
        out.add(summary("取数口径", "按凭证对方科目推断（无现金流量项目字段）"));
        return out;
    }

    private String classify(List<String> counterpartCodes) {
        for (String code : counterpartCodes) {
            if (INVESTING_PREFIX.containsKey(code)) return "投资";
            if (FINANCING_PREFIX.containsKey(code)) return "筹资";
        }
        return "经营";
    }

    /** 最新会计期间：报表不传期间时用它，避免默认出「全部」把月度表拉成累计表。 */
    public String latestPeriod() {
        List<String> rows = jdbc.queryForList(
                "SELECT period FROM accounting_period WHERE factory_code = 'F01' ORDER BY period DESC LIMIT 1",
                String.class);
        return rows.isEmpty() ? null : rows.get(0);
    }

    // ---------------- 结转损益 ----------------

    /** 结转服务：写分录要用会计服务的统一落库与期间校验。 */
    private AccountingService accountingService;

    /** 期间服务：封账校验必须在这里先做一遍（见 carryForward 注释）。 */
    private AccountingPeriodService periodService;

    @org.springframework.beans.factory.annotation.Autowired(required = false)
    public void setAccountingService(AccountingService accountingService) {
        this.accountingService = accountingService;
    }

    @org.springframework.beans.factory.annotation.Autowired(required = false)
    public void setPeriodService(AccountingPeriodService periodService) {
        this.periodService = periodService;
    }

    /**
     * 期末结转损益：收入类余额转入本年利润，费用类余额冲平。
     *
     * <p>标准做法是两条方向相反的分录组：
     * <pre>
     *   借 各收入科目（冲平）        贷 本年利润
     *   借 本年利润                  贷 各费用科目（冲平）
     * </pre>
     * 结转后 4103 的贷方余额就是本期净利润，资产负债表与利润表才对得上。
     *
     * @return 生成的分录条数；无可结转金额时返回 0
     */
    public int carryForward(String period) {
        String p = blank(period) ? latestPeriod() : period.trim();
        if (blank(p)) throw new IllegalStateException("没有可结转的会计期间");
        // 封账校验必须放在最前面：下面「无发生额就返回 0」会提前短路，
        // 若把校验留在写分录那一步，已封账且已结转过的期间会静默返回成功（实测踩到）。
        if (periodService != null) periodService.requireOpen(p, "结转损益");
        if (accountingService == null) throw new IllegalStateException("结转服务未就绪");

        List<Map<String, Object>> rows = jdbc.queryForList(
                "SELECT a.category, a.account_code, a.account_name, "
                        + "       COALESCE(SUM(CASE WHEN e.direction = '借' THEN e.amount ELSE -e.amount END), 0) AS net "
                        + "FROM accounting_entry e JOIN accounting_account a ON a.account_code = e.account_code "
                        + "WHERE a.category IN ('收入', '费用') AND e.period = ? "
                        + "GROUP BY a.category, a.account_code, a.account_name ORDER BY a.account_code", p);

        List<AccountingService.Entry> entries = new ArrayList<>();
        BigDecimal totalIncome = BigDecimal.ZERO;
        BigDecimal totalExpense = BigDecimal.ZERO;
        for (Map<String, Object> row : rows) {
            BigDecimal net = decimal(row.get("net"));
            if (net.signum() == 0) continue;
            String code = str(row.get("account_code"));
            String name = str(row.get("account_name"));
            if ("收入".equals(str(row.get("category")))) {
                // 收入类平时在贷方，结转时借记冲平
                BigDecimal amount = net.negate();
                if (amount.signum() <= 0) continue;
                entries.add(new AccountingService.Entry(code, name, "借", amount, ""));
                totalIncome = totalIncome.add(amount);
            } else {
                BigDecimal amount = net;
                if (amount.signum() <= 0) continue;
                entries.add(new AccountingService.Entry(code, name, "贷", amount, ""));
                totalExpense = totalExpense.add(amount);
            }
        }
        if (entries.isEmpty()) return 0;

        if (totalIncome.signum() > 0) {
            entries.add(new AccountingService.Entry("4103", "本年利润", "贷", totalIncome, ""));
        }
        if (totalExpense.signum() > 0) {
            entries.add(new AccountingService.Entry("4103", "本年利润", "借", totalExpense, ""));
        }

        java.time.LocalDate bizDate = java.time.LocalDate.parse(p + "-01")
                .withDayOfMonth(java.time.LocalDate.parse(p + "-01").lengthOfMonth());
        return accountingService.postCarryForward(p, bizDate, entries,
                "期末结转损益 " + p);
    }

    // ---------------- 工具 ----------------

    private Map<String, Object> line(String category, String code, String name, BigDecimal amount) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("项目类别", category);
        row.put("项目编码", code);
        row.put("项目名称", name);
        row.put("金额", amount);
        return row;
    }

    private Map<String, Object> summary(String name, BigDecimal amount) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("项目类别", "合计");
        row.put("项目编码", "");
        row.put("项目名称", name);
        row.put("金额", amount);
        return row;
    }

    private Map<String, Object> summary(String name, String text) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("项目类别", "说明");
        row.put("项目编码", "");
        row.put("项目名称", name);
        row.put("金额", text);
        return row;
    }

    /** 负债/权益/收入类是「贷方性质」：余额在贷方为正。资产/费用类相反。 */
    private static boolean creditNature(String category) {
        return "负债".equals(category) || "权益".equals(category) || "收入".equals(category);
    }

    private static boolean blank(String value) {
        return value == null || value.isBlank();
    }

    private static String str(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal decimal(Object value) {
        if (value instanceof BigDecimal b) return b;
        if (value instanceof Number n) return BigDecimal.valueOf(n.doubleValue());
        if (value == null) return BigDecimal.ZERO;
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }
}
