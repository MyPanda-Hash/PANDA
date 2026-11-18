package com.mes.panel;

import com.mes.service.AccountingPeriodService;
import com.mes.service.FinancialStatementService;
import com.mes.service.FixedAssetService;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 会计期间动作：封账 / 反封账。
 *
 * <p>为什么做成独立的 PanelActionHandler：封账是账务域的领域动作，
 * 塞进 PxService 那个上万行 switch 里既难找也容易被后续改动漏掉。
 * 放在扩展点上，supports 条件一眼看清它只认「会计期间」面板的两个按钮。
 */
@Component
public class AccountingPeriodActionHandler implements PanelActionHandler {

    private static final String PANEL = "ACCOUNT_PERIOD";
    private static final List<String> ACTIONS = List.of("封账", "反封账", "结转损益", "计提折旧");

    private final AccountingPeriodService periodService;
    private final FinancialStatementService statementService;
    private final FixedAssetService fixedAssetService;

    public AccountingPeriodActionHandler(AccountingPeriodService periodService,
                                         FinancialStatementService statementService,
                                         FixedAssetService fixedAssetService) {
        this.periodService = periodService;
        this.statementService = statementService;
        this.fixedAssetService = fixedAssetService;
    }

    @Override
    public boolean supports(String panelCode, String action) {
        return PANEL.equals(panelCode) && ACTIONS.contains(action);
    }

    @Override
    public Map<String, Object> handle(PanelActionContext context) {
        String period = periodOf(context);
        if (period == null || period.isBlank()) {
            throw new IllegalArgumentException("请先选中一行会计期间再操作");
        }
        String operator = context.userName() == null || context.userName().isBlank()
                ? "system" : context.userName();

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("期间", period);
        out.put("操作人", operator);

        if ("计提折旧".equals(context.action())) {
            Map<String, Object> result = fixedAssetService.depreciate(period);
            out.putAll(result);
            return out;
        }

        if ("结转损益".equals(context.action())) {
            int count = statementService.carryForward(period);
            out.put("生成分录", count);
            out.put("说明", count == 0
                    ? "该期间没有可结转的损益发生额"
                    : "已结转损益：收入与费用转入本年利润 4103，共 " + count + " 条分录");
            return out;
        }

        if ("封账".equals(context.action())) {
            periodService.close(period, operator);
            out.put("状态", "closed");
            out.put("说明", "该期间凭证已锁定：不可新增、不可重记、不可冲销");
        } else {
            periodService.reopen(period, operator);
            out.put("状态", "open");
            out.put("说明", "该期间已恢复记账");
        }
        return out;
    }

    /** 期间可来自选中行的「期间」列，也可由调用方通过参数显式指定。 */
    private String periodOf(PanelActionContext context) {
        Map<String, Object> params = context.parameters();
        if (params != null) {
            Object fromParams = params.get("期间");
            if (fromParams != null && !String.valueOf(fromParams).isBlank()) {
                return String.valueOf(fromParams).trim();
            }
        }
        Map<String, Object> form = context.formData();
        if (form != null) {
            Object fromForm = form.get("期间");
            if (fromForm == null) fromForm = form.get("编号");
            if (fromForm != null && !String.valueOf(fromForm).isBlank()) {
                return String.valueOf(fromForm).trim();
            }
        }
        return null;
    }
}
