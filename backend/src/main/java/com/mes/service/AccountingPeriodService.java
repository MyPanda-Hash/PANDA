package com.mes.service;

import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 会计期间：取期、封账、开账。
 *
 * <p>补的是 2026-11-16 评估出的两处地基缺口之一：凭证原来只有业务日期、
 * 没有期间概念，导致 ① 无法按月取数（三大报表的前置）；② 历史账可被随时改，
 * 月末结不了账。
 *
 * <p>规则保持简单且可核对：
 * <ul>
 *   <li>期间由业务日期派生（yyyy-MM），不接受调用方随意指定，避免「往已封期间塞单」；</li>
 *   <li>封账后禁止新增、重记、冲销该期间的凭证 —— 三条都在写入口拦；</li>
 *   <li>反封账保留给管理员纠错，但会记录操作人，不做静默放开。</li>
 * </ul>
 */
@Service
public class AccountingPeriodService {

    private static final DateTimeFormatter PERIOD_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM");
    private static final String DEFAULT_FACTORY = "F01";

    private final JdbcTemplate jdbc;

    public AccountingPeriodService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 业务日期 → 会计期间。日期为空时取当天：宁可记进当月也不让凭证因为缺日期丢失。
     */
    public String resolvePeriod(LocalDate bizDate) {
        LocalDate date = bizDate == null ? LocalDate.now() : bizDate;
        return date.format(PERIOD_FORMAT);
    }

    /** 该期间是否已封账。 */
    public boolean isClosed(String period) {
        if (period == null || period.isBlank()) return false;
        List<String> rows = jdbc.queryForList(
                "SELECT status FROM accounting_period WHERE period = ? AND factory_code = ?",
                String.class, period, DEFAULT_FACTORY);
        return !rows.isEmpty() && "closed".equalsIgnoreCase(rows.get(0));
    }

    /**
     * 写凭证前的硬校验：期间已封账就拒绝。
     *
     * <p>消息里带上期间与动作，避免只报「不允许」而让人不知道是哪个月的账。
     */
    public void requireOpen(String period, String action) {
        if (isClosed(period)) {
            throw new IllegalStateException(
                    "会计期间 " + period + " 已封账，不能" + (action == null ? "记账" : action)
                            + "；如需调整请先反封账");
        }
    }

    /** 封账：幂等，重复封账不报错但要记录最新操作人。 */
    public void close(String period, String operator) {
        String p = normalize(period);
        jdbc.update("INSERT INTO accounting_period (period, status, closed_at, closed_by, factory_code) "
                        + "VALUES (?, 'closed', ?, ?, ?) "
                        + "ON CONFLICT (period, factory_code) DO UPDATE "
                        + "SET status = 'closed', closed_at = EXCLUDED.closed_at, closed_by = EXCLUDED.closed_by",
                p, LocalDateTime.now(), operator, DEFAULT_FACTORY);
    }

    /**
     * 反封账：管理员纠错入口。
     *
     * <p>同时清空封账时间与封账人 —— 只清时间会让面板继续显示上一位封账人，
     * 看起来像「已封账但没人封」，对账时容易误判。
     */
    public void reopen(String period, String operator) {
        String p = normalize(period);
        jdbc.update("UPDATE accounting_period SET status = 'open', closed_at = NULL, closed_by = NULL "
                + "WHERE period = ? AND factory_code = ?", p, DEFAULT_FACTORY);
    }

    /**
     * 期间列表：带凭证张数/分录数/借贷发生额，供面板直接展示。
     *
     * <p>列名必须是中文：面板按列名取值渲染，返回英文键会让表格整片空白
     * （本系统数据键一律中文，写库值仍保留 open/closed 便于程序判断）。
     */
    public List<Map<String, Object>> list() {
        return jdbc.queryForList(
                "SELECT p.period AS \"期间\", "
                        + "       CASE WHEN COALESCE(p.status, 'open') = 'closed' THEN '已封账' ELSE '可记账' END AS \"状态\", "
                        + "       COALESCE(e.vouchers, 0) AS \"凭证张数\", "
                        + "       COALESCE(e.entries, 0) AS \"分录数\", "
                        + "       COALESCE(e.debit, 0) AS \"借方合计\", "
                        + "       COALESCE(e.credit, 0) AS \"贷方合计\", "
                        + "       p.closed_at AS \"封账时间\", p.closed_by AS \"封账人\" "
                        + "FROM accounting_period p "
                        + "LEFT JOIN (SELECT period, COUNT(DISTINCT voucher_no) vouchers, "
                        + "                  COUNT(*) entries, "
                        + "                  SUM(CASE WHEN direction = '借' THEN amount ELSE 0 END) debit, "
                        + "                  SUM(CASE WHEN direction = '贷' THEN amount ELSE 0 END) credit "
                        + "             FROM accounting_entry GROUP BY period) e ON e.period = p.period "
                        + "WHERE p.factory_code = ? "
                        + "ORDER BY p.period DESC", DEFAULT_FACTORY);
    }

    /** 汇总行：让面板底部能看到总体是否平衡。 */
    public Map<String, Object> summary() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("期间数", jdbc.queryForObject(
                "SELECT COUNT(*) FROM accounting_period WHERE factory_code = ?", Integer.class, DEFAULT_FACTORY));
        out.put("已封账", jdbc.queryForObject(
                "SELECT COUNT(*) FROM accounting_period WHERE factory_code = ? AND status = 'closed'",
                Integer.class, DEFAULT_FACTORY));
        return out;
    }

    private String normalize(String period) {
        if (period == null || period.isBlank()) {
            throw new IllegalArgumentException("缺少会计期间");
        }
        String p = period.trim();
        if (!p.matches("\\d{4}-\\d{2}")) {
            throw new IllegalArgumentException("会计期间格式应为 yyyy-MM，收到：" + p);
        }
        return p;
    }
}
