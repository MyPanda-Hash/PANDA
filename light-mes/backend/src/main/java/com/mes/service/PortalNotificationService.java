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
    private final AiNoticeClient aiNotices;
    private final org.springframework.jdbc.core.JdbcTemplate jdbc;

    public PortalNotificationService(FormDataMapper formMapper, FormApprovalMapper approvalMapper,
                                     PanelConfigMapper panelMapper, ReportQueryService reportQueryService,
                                     RoleService roleService, AiNoticeClient aiNotices,
                                     org.springframework.jdbc.core.JdbcTemplate jdbc) {
        this.formMapper = formMapper;
        this.approvalMapper = approvalMapper;
        this.panelMapper = panelMapper;
        this.reportQueryService = reportQueryService;
        this.roleService = roleService;
        this.aiNotices = aiNotices;
        this.jdbc = jdbc;
    }

    public Map<String, Integer> badge(String userName) {
        Map<String, Integer> result = new LinkedHashMap<>();
        result.put("todo", todos(userName).size());
        result.put("message", unreadMessageCount(userName));
        result.put("alarm", alarms().size());
        return result;
    }

    /** 数字员工的待办与消息（AI 不可用时返回空表，不影响 MES 自己的通知）。 */
    private Map<String, Object> aiPayload(String userName) {
        return aiNotices.fetch(userName);
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
        result.addAll(aiTodos(userName));
        return result;
    }

    /**
     * 数字员工派下来的待办：带来源标记与跳转路径，按时间倒序插在最前面。
     *
     * <p>这些待办在 AI 侧完成后会自动消失，不需要 MES 记已读状态。
     */
    private List<Map<String, Object>> aiTodos(String userName) {
        Map<String, Object> payload = aiPayload(userName);
        if (payload == null) return List.of();
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> todo : aiNotices.todos(payload)) {
            String id = String.valueOf(todo.getOrDefault("id", ""));
            if (id.isEmpty()) continue;
            String title = String.valueOf(todo.getOrDefault("title", ""));
            String detail = String.valueOf(todo.getOrDefault("detail", ""));
            String groupName = String.valueOf(todo.getOrDefault("groupName", ""));
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", "ai-todo:" + id);
            item.put("type", "todo");
            item.put("title", title);
            item.put("time", String.valueOf(todo.getOrDefault("createdAt", "")));
            item.put("read", true);
            item.put("content", detail.isEmpty() ? "来自数字员工" + (groupName.isEmpty() ? "" : "（" + groupName + "）") : detail);
            item.put("source", "ai");
            item.put("sourceLabel", "数字员工");
            item.put("priority", String.valueOf(todo.getOrDefault("priority", "")));
            String target = aiNotices.targetPath(todo);
            if (target != null) {
                item.put("targetPath", target);
                item.put("actionLabel", "去处理");
            } else {
                item.put("targetPath", aiNotices.groupUrl(String.valueOf(todo.getOrDefault("groupId", ""))));
                item.put("external", true);
                item.put("actionLabel", "去群聊看");
            }
            result.add(item);
        }
        return result;
    }

    /**
     * 消息：按群聊分类，每个群一条；只统计该群「已读游标」之后的未读消息。
     *
     * <p>口径（2026-11-13 用户确认）：不混入 MES 审批结果；点开某个群只清这个群的未读，
     * 清空后该群从列表消失。MES 不存消息副本，AI 侧是权威数据源。
     */
    private List<Map<String, Object>> messages(String userName) {
        Map<String, List<Map<String, Object>>> byGroup = new LinkedHashMap<>();
        for (Map<String, Object> message : aiMessages(userName)) {
            String groupId = String.valueOf(message.getOrDefault("groupId", ""));
            byGroup.computeIfAbsent(groupId, k -> new ArrayList<>()).add(message);
        }
        Map<String, LocalDateTime> cursors = groupCursors(userName);
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map.Entry<String, List<Map<String, Object>>> entry : byGroup.entrySet()) {
            String groupId = entry.getKey();
            LocalDateTime cursor = cursors.get(groupId);
            List<Map<String, Object>> unread = new ArrayList<>();
            for (Map<String, Object> message : entry.getValue()) {
                if (cursor == null || after(message, cursor)) unread.add(message);
            }
            if (unread.isEmpty()) continue;
            String groupName = String.valueOf(unread.get(0).getOrDefault("groupName", "项目群聊"));
            Map<String, Object> latest = unread.get(0);
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", "ai-group:" + groupId);
            item.put("type", "message");
            item.put("title", groupName);
            item.put("time", String.valueOf(latest.getOrDefault("createdAt", "")));
            item.put("read", false);
            item.put("content", "未读 " + unread.size() + " 条 · " + String.valueOf(latest.getOrDefault("text", "")));
            item.put("source", "ai");
            item.put("sourceLabel", "群聊");
            item.put("groupId", groupId);
            item.put("unreadCount", unread.size());
            item.put("targetPath", aiNotices.groupUrl(groupId));
            item.put("external", true);
            item.put("actionLabel", "打开群聊");
            // 弹窗里要展示的消息（按时间正序，读起来顺）
            List<Map<String, Object>> detail = new ArrayList<>(unread);
            detail.sort(Comparator.comparing(m -> String.valueOf(m.getOrDefault("createdAt", ""))));
            item.put("messages", detail);
            result.add(item);
        }
        return result;
    }

    /** 未读总数：按群统计后求和。 */
    private int unreadMessageCount(String userName) {
        int total = 0;
        for (Map<String, Object> group : messages(userName)) {
            total += (int) group.getOrDefault("unreadCount", 0);
        }
        return total;
    }

    /** 某个群的消息是否在游标之后。 */
    private boolean after(Map<String, Object> message, LocalDateTime cursor) {
        Object createdAt = message.get("createdAt");
        if (createdAt == null) return true;
        try {
            return LocalDateTime.parse(String.valueOf(createdAt), TIME_FORMAT).isAfter(cursor);
        } catch (Exception e) {
            return true;
        }
    }

    /** 该账号在各群上的已读游标。 */
    private Map<String, LocalDateTime> groupCursors(String userName) {
        Map<String, LocalDateTime> cursors = new LinkedHashMap<>();
        jdbc.query("select group_id, read_at from portal_notice_read_group where user_name = ?",
                rs -> {
                    java.sql.Timestamp readAt = rs.getTimestamp(2);
                    cursors.put(rs.getString(1), readAt == null ? null : readAt.toLocalDateTime());
                }, userName);
        return cursors;
    }

    /**
     * 清空某个群的未读（只推这个群的游标，其它群不受影响）。返回清掉的条数。
     *
     * @param groupId 为空时清空所有群
     */
    public int markMessageRead(String userName, String groupId) {
        int cleared = 0;
        LocalDateTime now = LocalDateTime.now();
        List<String> groups = new ArrayList<>();
        if (groupId == null || groupId.isBlank()) {
            for (Map<String, Object> group : messages(userName)) {
                groups.add(String.valueOf(group.get("groupId")));
                cleared += (int) group.getOrDefault("unreadCount", 0);
            }
        } else {
            groups.add(groupId);
            for (Map<String, Object> group : messages(userName)) {
                if (groupId.equals(String.valueOf(group.get("groupId")))) {
                    cleared = (int) group.getOrDefault("unreadCount", 0);
                    break;
                }
            }
        }
        for (String group : groups) {
            if (group == null || group.isBlank() || "null".equals(group)) continue;
            jdbc.update("""
                    insert into portal_notice_read_group (user_name, group_id, read_at, update_time)
                    values (?, ?, ?, now())
                    on conflict (user_name, group_id) do update set read_at = excluded.read_at,
                                                                  update_time = now()
                    """, userName, group, now);
        }
        return cleared;
    }

    /** 数字员工相关群聊的最近消息（AI 侧已按账号过滤）。 */
    private List<Map<String, Object>> aiMessages(String userName) {
        Map<String, Object> payload = aiNotices.fetch(userName, null);
        if (payload == null) return List.of();
        List<Map<String, Object>> result = new ArrayList<>();
        for (Map<String, Object> message : aiNotices.messages(payload)) {
            String id = String.valueOf(message.getOrDefault("id", ""));
            if (id.isEmpty()) continue;
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", "ai-msg:" + id);
            item.put("groupId", String.valueOf(message.getOrDefault("groupId", "")));
            item.put("groupName", String.valueOf(message.getOrDefault("groupName", "")));
            item.put("senderKind", String.valueOf(message.getOrDefault("senderKind", "")));
            item.put("text", String.valueOf(message.getOrDefault("text", "")));
            item.put("createdAt", String.valueOf(message.getOrDefault("createdAt", "")));
            result.add(item);
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
