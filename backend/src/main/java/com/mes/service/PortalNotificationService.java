package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.FormApproval;
import com.mes.entity.FormData;
import com.mes.entity.PanelConfig;
import com.mes.mapper.FormApprovalMapper;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.PanelConfigMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** 当前账号的审批待办、审批结果消息与实时库存预警。 */
@Service
public class PortalNotificationService {

    static final BigDecimal LOW_STOCK_THRESHOLD = new BigDecimal("500");
    private static final DateTimeFormatter TIME_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final int LIST_LIMIT = 100;
    private static final int APPROVAL_SCAN_LIMIT = 2000;

    private final FormDataMapper formMapper;
    private final FormApprovalMapper approvalMapper;
    private final PanelConfigMapper panelMapper;
    private final ReportQueryService reportQueryService;
    private final RoleService roleService;

    public PortalNotificationService(FormDataMapper formMapper, FormApprovalMapper approvalMapper,
                                     PanelConfigMapper panelMapper, ReportQueryService reportQueryService,
                                     RoleService roleService) {
        this.formMapper = formMapper;
        this.approvalMapper = approvalMapper;
        this.panelMapper = panelMapper;
        this.reportQueryService = reportQueryService;
        this.roleService = roleService;
    }

    public Map<String, Integer> badge(String userName) {
        Map<String, Integer> result = new LinkedHashMap<>();
        result.put("todo", todos(userName).size());
        result.put("message", messages(userName).size());
        result.put("alarm", alarms().size());
        return result;
    }

    public List<Map<String, Object>> list(String userName, String type) {
        return switch (type) {
            case "todo" -> todos(userName);
            case "message" -> messages(userName);
            case "alarm" -> alarms();
            default -> List.of();
        };
    }

    private List<Map<String, Object>> todos(String userName) {
        List<String> approvePanels = stringList(roleService.getPerms(userName).get("approvePanels"));
        if (approvePanels.isEmpty()) return List.of();

        List<FormData> forms = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getStatus, "审批中")
                .in(FormData::getPanelCode, approvePanels)
                .orderByDesc(FormData::getUpdateTime)
                .orderByDesc(FormData::getId)
                .last("LIMIT " + LIST_LIMIT));
        if (forms.isEmpty()) return List.of();

        Set<String> keys = new HashSet<>();
        for (FormData form : forms) keys.add(key(form.getPanelCode(), form.getFormNo()));
        Map<String, FormApproval> submissions = new HashMap<>();
        for (FormApproval record : approvalMapper.selectList(new LambdaQueryWrapper<FormApproval>()
                .eq(FormApproval::getAction, "SUBMIT")
                .eq(FormApproval::getResult, "PENDING")
                .in(FormApproval::getPanelCode, approvePanels)
                .orderByDesc(FormApproval::getId)
                .last("LIMIT " + APPROVAL_SCAN_LIMIT))) {
            String key = key(record.getPanelCode(), record.getFormNo());
            if (keys.contains(key)) submissions.putIfAbsent(key, record);
        }

        Map<String, String> panelNames = panelNames(new HashSet<>(approvePanels));
        List<Map<String, Object>> result = new ArrayList<>();
        for (FormData form : forms) {
            FormApproval submission = submissions.get(key(form.getPanelCode(), form.getFormNo()));
            LocalDateTime time = submission == null ? firstTime(form.getUpdateTime(), form.getCreateTime())
                    : submission.getCreateTime();
            String submitter = submission == null ? text(form.getCreateBy(), "未知账号")
                    : text(submission.getOperator(), "未知账号");
            String panelName = panelNames.getOrDefault(form.getPanelCode(), form.getPanelCode());
            Map<String, Object> item = baseNotice("todo:" + form.getId(), "todo",
                    panelName + " " + form.getFormNo() + " 待审批", time,
                    "提交人「" + submitter + "」已提交" + panelName + " " + form.getFormNo()
                            + "，等待当前账号审批。", form.getPanelCode(), form.getFormNo());
            item.put("submitter", submitter);
            item.put("actionLabel", "去审批");
            result.add(item);
            if (result.size() >= LIST_LIMIT) break;
        }
        return result;
    }

    private List<Map<String, Object>> messages(String userName) {
        List<FormApproval> records = new ArrayList<>(approvalMapper.selectList(
                new LambdaQueryWrapper<FormApproval>()
                        .in(FormApproval::getAction, List.of("SUBMIT", "APPROVE", "REJECT"))
                        .orderByDesc(FormApproval::getId)
                        .last("LIMIT " + APPROVAL_SCAN_LIMIT)));
        if (records.isEmpty()) return List.of();
        records.sort(Comparator.comparing(FormApproval::getId,
                Comparator.nullsLast(Comparator.naturalOrder())));

        Map<String, String> submitters = new HashMap<>();
        List<FormApproval> outcomes = new ArrayList<>();
        for (FormApproval record : records) {
            String key = key(record.getPanelCode(), record.getFormNo());
            if ("SUBMIT".equals(record.getAction())) {
                submitters.put(key, record.getOperator());
            } else if (userName.equals(submitters.get(key))) {
                outcomes.add(record);
            }
        }
        outcomes.sort(Comparator.comparing(FormApproval::getId,
                Comparator.nullsLast(Comparator.naturalOrder())).reversed());

        Set<String> panelCodes = new HashSet<>();
        for (FormApproval outcome : outcomes) panelCodes.add(outcome.getPanelCode());
        Map<String, String> panelNames = panelNames(panelCodes);
        List<Map<String, Object>> result = new ArrayList<>();
        for (FormApproval outcome : outcomes) {
            boolean approved = "APPROVE".equals(outcome.getAction());
            String action = approved ? "审批通过" : "审批驳回";
            String panelName = panelNames.getOrDefault(outcome.getPanelCode(), outcome.getPanelCode());
            StringBuilder content = new StringBuilder("您提交的")
                    .append(panelName).append(' ').append(outcome.getFormNo())
                    .append("已由「").append(text(outcome.getOperator(), "未知账号")).append("」")
                    .append(action).append('。');
            if (outcome.getOpinion() != null && !outcome.getOpinion().isBlank()) {
                content.append("审批意见：").append(outcome.getOpinion());
            }
            Map<String, Object> item = baseNotice("message:" + outcome.getId(), "message",
                    panelName + " " + outcome.getFormNo() + " " + action,
                    outcome.getCreateTime(), content.toString(), outcome.getPanelCode(), outcome.getFormNo());
            item.put("result", approved ? "APPROVED" : "REJECTED");
            item.put("actionLabel", "查看单据");
            result.add(item);
            if (result.size() >= LIST_LIMIT) break;
        }
        return result;
    }

    private List<Map<String, Object>> alarms() {
        List<Map<String, Object>> stockRows = reportQueryService.currentStockRows();
        List<Map<String, Object>> lowStock = new ArrayList<>();
        for (Map<String, Object> stock : stockRows) {
            BigDecimal quantity = number(stock.get("现存量(主)"));
            if (quantity.compareTo(LOW_STOCK_THRESHOLD) < 0) {
                Map<String, Object> copy = new LinkedHashMap<>(stock);
                copy.put("_quantity", quantity);
                lowStock.add(copy);
            }
        }
        lowStock.sort(Comparator.comparing(row -> (BigDecimal) row.get("_quantity")));

        LocalDateTime now = LocalDateTime.now();
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> stock : lowStock) {
            String warehouse = text(stock.get("仓库"), "未指定仓库");
            String inventory = text(stock.get("存货"), text(stock.get("存货编码"), "未命名存货"));
            String inventoryCode = text(stock.get("存货编码"), "-");
            String unit = text(stock.get("主计量"), "");
            BigDecimal quantity = (BigDecimal) stock.get("_quantity");
            String quantityText = decimal(quantity);
            String unitText = unit.isBlank() ? "" : " " + unit;
            Map<String, Object> item = baseNotice(
                    "alarm:" + warehouse + ':' + inventoryCode, "alarm",
                    "低库存：" + inventory + "（" + warehouse + "）", now,
                    "库存状况表显示「" + inventory + "」在「" + warehouse + "」的现存量为 "
                            + quantityText + unitText + "，低于预警阈值 500" + unitText + "。",
                    "STOCK_STATUS", null);
            item.put("warehouse", warehouse);
            item.put("inventoryCode", inventoryCode);
            item.put("quantity", quantity);
            item.put("threshold", LOW_STOCK_THRESHOLD);
            item.put("actionLabel", "查看库存");
            result.add(item);
            if (result.size() >= LIST_LIMIT) break;
        }
        return result;
    }

    private Map<String, Object> baseNotice(String id, String type, String title, LocalDateTime time,
                                            String content, String panelCode, String formNo) {
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", id);
        item.put("type", type);
        item.put("title", title);
        item.put("time", format(time));
        // 当前没有用户级已读表，实时业务项不伪装成持久化未读消息。
        item.put("read", true);
        item.put("content", content);
        item.put("panelCode", panelCode);
        item.put("formNo", formNo);
        item.put("targetPath", "/panelx/list/" + panelCode);
        return item;
    }

    private Map<String, String> panelNames(Set<String> panelCodes) {
        if (panelCodes.isEmpty()) return Map.of();
        Map<String, String> result = new HashMap<>();
        for (PanelConfig panel : panelMapper.selectList(new LambdaQueryWrapper<PanelConfig>()
                .in(PanelConfig::getPanelCode, panelCodes))) {
            result.put(panel.getPanelCode(), text(panel.getPanelName(), panel.getPanelCode()));
        }
        return result;
    }

    private List<String> stringList(Object value) {
        if (!(value instanceof List<?> list)) return List.of();
        List<String> result = new ArrayList<>();
        for (Object item : list) {
            if (item != null && !String.valueOf(item).isBlank()) result.add(String.valueOf(item));
        }
        return result;
    }

    private String key(String panelCode, String formNo) {
        return text(panelCode, "") + '\u0000' + text(formNo, "");
    }

    private LocalDateTime firstTime(LocalDateTime first, LocalDateTime fallback) {
        return first == null ? fallback : first;
    }

    private String format(LocalDateTime value) {
        return value == null ? "" : value.format(TIME_FORMAT);
    }

    private String text(Object value, String fallback) {
        return value == null || String.valueOf(value).isBlank() ? fallback : String.valueOf(value);
    }

    private BigDecimal number(Object value) {
        if (value instanceof BigDecimal decimal) return decimal;
        if (value instanceof Number number) return new BigDecimal(number.toString());
        try {
            return value == null ? BigDecimal.ZERO : new BigDecimal(String.valueOf(value));
        } catch (NumberFormatException ignored) {
            return BigDecimal.ZERO;
        }
    }

    private String decimal(BigDecimal value) {
        return value.stripTrailingZeros().toPlainString();
    }
}
