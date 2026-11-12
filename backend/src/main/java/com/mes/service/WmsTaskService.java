package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.WmsTask;
import com.mes.mapper.FormDataMapper;
import com.mes.mapper.WmsTaskMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 仓内作业（WMS 作业层）：把已审核的出入库单拆成现场任务，记录实作业进度。
 *
 * <p>此前仓内只有单据和一张只读的「拣货工作台」报表——任务是每次查询现算的，
 * 现场拣了多少、上架到哪个货位、有没有复核过，都没有地方存。这里补三件事：
 * 拣货任务与上架任务落库（幂等同步）、按仓库编波次、确认作业量与货位、复核结论留痕。
 * 库存口径不变：库存仍由单据审核落 {@code stock_txn}，作业层只解决「怎么做的」。
 */
@Service
public class WmsTaskService {

    /** 需要拣货的出库面板。 */
    public static final List<String> OUTBOUND_PANELS = List.of("SALE_OUT", "MATERIAL_OUT", "OTHER_OUT");
    /** 需要上架的入库面板。 */
    public static final List<String> INBOUND_PANELS = List.of("PURCHASE_IN", "FINISH_IN", "OTHER_IN");

    public static final String PICK = "PICK";
    public static final String PUTAWAY = "PUTAWAY";

    public static final String PENDING = "待作业";
    public static final String PARTIAL = "部分完成";
    public static final String DONE = "已完成";

    private static final DateTimeFormatter WAVE_DATE = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final WmsTaskMapper taskMapper;
    private final FormDataMapper formMapper;
    private final ObjectMapper json;

    public WmsTaskService(WmsTaskMapper taskMapper, FormDataMapper formMapper, ObjectMapper json) {
        this.taskMapper = taskMapper;
        this.formMapper = formMapper;
        this.json = json;
    }

    /** 一条待作业行：纯计算结果，落库前用它判断增量。 */
    public record TaskRow(String taskType, String sourcePanel, String sourceNo, int rowNo,
                          String itemCode, String itemName, String spec, String unit,
                          String warehouse, String fromLocation, BigDecimal planQty) {}

    // ---------- 纯计算：单据 → 作业行 ----------

    /**
     * 由单据表头与明细折算作业行。
     * 拣货行取「库位」作为来源货位；上架行的目标货位留空，等现场确认时填。
     */
    public List<TaskRow> buildRows(String taskType, String panel, String formNo,
                                   Map<String, Object> head, Map<String, Object> detail) {
        boolean pick = PICK.equals(taskType);
        if (!(pick ? OUTBOUND_PANELS : INBOUND_PANELS).contains(panel)) return List.of();
        List<TaskRow> out = new ArrayList<>();
        Map<String, Object> safeHead = head == null ? Map.of() : head;
        String headWarehouse = text(first(safeHead, "仓库"));
        int rowNo = 0;
        for (Map<String, Object> row : detailRows(detail)) {
            rowNo += 1;
            String itemCode = text(first(row, "存货编码", "产品编码", "材料编码"));
            String itemName = text(first(row, "存货名称", "产品名称", "材料名称", "存货"));
            if (itemCode.isBlank() && itemName.isBlank()) continue;
            BigDecimal qty = pick
                    ? number(first(row, "数量"))
                    : number(first(row, "数量", "实收数量", "入库数量"));
            if (qty.signum() <= 0) continue;
            String warehouse = text(first(row, "仓库"));
            if (warehouse.isBlank()) warehouse = headWarehouse;
            out.add(new TaskRow(taskType, panel, formNo, rowNo,
                    itemCode, itemName,
                    text(first(row, "规格型号", "材料规格")),
                    text(first(row, "计量单位", "主单位", "生产单位")),
                    warehouse,
                    text(first(row, "库位", "货位")),
                    qty.abs()));
        }
        return out;
    }

    /** 状态推进：没做 = 待作业，做了一部分 = 部分完成，做满 = 已完成。 */
    public static String statusOf(BigDecimal plan, BigDecimal done) {
        BigDecimal p = plan == null ? BigDecimal.ZERO : plan;
        BigDecimal d = done == null ? BigDecimal.ZERO : done;
        if (d.signum() <= 0) return PENDING;
        return d.compareTo(p) < 0 ? PARTIAL : DONE;
    }

    /** 波次号：W-20261112-001（按天编号，同一仓库一个波次）。 */
    public static String waveNo(LocalDate date, int seq) {
        return "W-" + (date == null ? LocalDate.now() : date).format(WAVE_DATE) + "-"
                + String.format("%03d", Math.max(seq, 1));
    }

    // ---------- 落库：同步 / 确认 / 波次 / 复核 ----------

    /**
     * 幂等同步任务：扫描已审核单据，缺任务的行补上，已有任务的保留实作业进度与货位。
     * 单据被弃审后不再产生新任务，但已产生的任务不删（现场做过的活要留痕），由状态体现。
     */
    @Transactional
    public Map<String, Object> sync(String taskType) {
        List<String> panels = PICK.equals(taskType) ? OUTBOUND_PANELS : INBOUND_PANELS;
        int created = 0, kept = 0;
        LocalDateTime now = LocalDateTime.now();
        for (String panel : panels) {
            for (FormData fd : formMapper.selectList(new LambdaQueryWrapper<FormData>()
                    .eq(FormData::getPanelCode, panel).eq(FormData::getStatus, "已审核"))) {
                Map<String, Object> head = parse(fd.getData());
                Map<String, Object> detail = parse(fd.getDetailData());
                for (TaskRow row : buildRows(taskType, panel, fd.getFormNo(), head, detail)) {
                    boolean exists = taskMapper.selectCount(new LambdaQueryWrapper<WmsTask>()
                            .eq(WmsTask::getTaskType, taskType)
                            .eq(WmsTask::getSourcePanel, row.sourcePanel())
                            .eq(WmsTask::getSourceNo, row.sourceNo())
                            .eq(WmsTask::getRowNo, row.rowNo())) > 0;
                    if (exists) {
                        kept += 1;
                        continue;
                    }
                    WmsTask task = new WmsTask();
                    task.setTaskType(taskType);
                    task.setWaveNo("");
                    task.setSourcePanel(row.sourcePanel());
                    task.setSourceNo(row.sourceNo());
                    task.setRowNo(row.rowNo());
                    task.setItemCode(row.itemCode());
                    task.setItemName(row.itemName());
                    task.setSpec(row.spec());
                    task.setUnit(row.unit());
                    task.setWarehouse(row.warehouse());
                    task.setFromLocation(row.fromLocation());
                    task.setToLocation("");
                    task.setPlanQty(row.planQty());
                    task.setDoneQty(BigDecimal.ZERO);
                    task.setStatus(PENDING);
                    task.setOperator("");
                    task.setReviewStatus("");
                    task.setReviewRemark("");
                    task.setBizDate(dateOf(head.get("单据日期")));
                    task.setCreateTime(now);
                    taskMapper.insert(task);
                    created += 1;
                }
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("类型", taskType);
        out.put("新增任务", created);
        out.put("已有任务", kept);
        return out;
    }

    /** 确认作业：写实作业量（覆盖式，支持现场改数）、目标货位与操作人，推进状态。 */
    @Transactional
    public Map<String, Object> confirm(Long id, BigDecimal qty, String location, String operator) {
        WmsTask task = taskMapper.selectById(id);
        if (task == null) throw new IllegalArgumentException("作业任务不存在：" + id);
        if (qty == null || qty.signum() < 0) throw new IllegalArgumentException("实作业数量必须 ≥ 0");
        if (qty.compareTo(task.getPlanQty()) > 0) {
            throw new IllegalStateException("实作业数量 " + plain(qty) + " 超过计划量 "
                    + plain(task.getPlanQty()) + "，请核对");
        }
        if (PUTAWAY.equals(task.getTaskType())
                && qty.compareTo(task.getPlanQty()) >= 0
                && (location == null || location.isBlank())) {
            throw new IllegalStateException("上架完成必须填目标货位");
        }
        task.setDoneQty(qty);
        if (location != null && !location.isBlank()) task.setToLocation(location.trim());
        if (operator != null && !operator.isBlank()) task.setOperator(operator.trim());
        task.setStatus(statusOf(task.getPlanQty(), qty));
        task.setDoneTime(DONE.equals(task.getStatus()) ? LocalDateTime.now() : null);
        taskMapper.updateById(task);
        return toMap(task);
    }

    /**
     * 编波次：把某个仓库里「还没开始拣」的任务归到一个波次号（已编波的不重编）。
     * 波次是仓库现场的合并拣货单位，一张波次里可以有多张出库单。
     */
    @Transactional
    public Map<String, Object> assignWave(String warehouse) {
        LambdaQueryWrapper<WmsTask> query = new LambdaQueryWrapper<WmsTask>()
                .eq(WmsTask::getTaskType, PICK)
                .eq(WmsTask::getStatus, PENDING)
                .eq(WmsTask::getWaveNo, "");
        if (warehouse != null && !warehouse.isBlank()) query.eq(WmsTask::getWarehouse, warehouse.trim());
        List<WmsTask> tasks = taskMapper.selectList(query);
        LocalDate today = LocalDate.now();
        long used = taskMapper.selectCount(new LambdaQueryWrapper<WmsTask>()
                .likeRight(WmsTask::getWaveNo, "W-" + today.format(WAVE_DATE)));
        int seq = (int) used + 1;
        String wave = waveNo(today, seq);
        for (WmsTask task : tasks) task.setWaveNo(wave);
        for (WmsTask task : tasks) taskMapper.updateById(task);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("波次号", tasks.isEmpty() ? "" : wave);
        out.put("任务数", tasks.size());
        out.put("仓库", warehouse == null ? "" : warehouse);
        return out;
    }

    /** 复核：按来源单号给整单任务写复核结论（出库前的数量/单据核对）。 */
    @Transactional
    public Map<String, Object> review(String sourceNo, String result, String remark) {
        if (sourceNo == null || sourceNo.isBlank()) throw new IllegalArgumentException("缺少来源单号");
        String verdict = result == null || result.isBlank() ? "已复核" : result.trim();
        List<WmsTask> tasks = taskMapper.selectList(new LambdaQueryWrapper<WmsTask>()
                .eq(WmsTask::getSourceNo, sourceNo.trim()));
        if (tasks.isEmpty()) throw new IllegalStateException("来源单号 " + sourceNo + " 没有作业任务");
        for (WmsTask task : tasks) {
            task.setReviewStatus(verdict);
            task.setReviewRemark(remark == null ? "" : remark);
            taskMapper.updateById(task);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("来源单号", sourceNo);
        out.put("复核结果", verdict);
        out.put("任务数", tasks.size());
        return out;
    }

    /** 任务列表（页面用）：可按类型、状态过滤。 */
    public List<Map<String, Object>> list(String taskType, String status) {
        LambdaQueryWrapper<WmsTask> query = new LambdaQueryWrapper<WmsTask>()
                .orderByAsc(WmsTask::getWarehouse, WmsTask::getFromLocation, WmsTask::getSourceNo);
        if (taskType != null && !taskType.isBlank()) query.eq(WmsTask::getTaskType, taskType);
        if (status != null && !status.isBlank()) query.eq(WmsTask::getStatus, status);
        List<Map<String, Object>> out = new ArrayList<>();
        for (WmsTask task : taskMapper.selectList(query)) out.add(toMap(task));
        return out;
    }

    /** 看板汇总：待作业/部分/已完成 + 未复核单数。 */
    public Map<String, Object> summary() {
        List<WmsTask> all = taskMapper.selectList(null);
        int pending = 0, partial = 0, done = 0, unreviewed = 0;
        java.util.Set<String> reviewedSources = new java.util.LinkedHashSet<>();
        for (WmsTask task : all) {
            if (PENDING.equals(task.getStatus())) pending += 1;
            else if (PARTIAL.equals(task.getStatus())) partial += 1;
            else if (DONE.equals(task.getStatus())) done += 1;
            if ("".equals(nullToEmpty(task.getReviewStatus()))) unreviewed += 1;
            else reviewedSources.add(task.getSourceNo());
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("任务数", all.size());
        out.put("待作业", pending);
        out.put("部分完成", partial);
        out.put("已完成", done);
        out.put("未复核任务", unreviewed);
        return out;
    }

    /** 删除某张单据的任务（单据被删除时由调用方触发；弃审不删任务）。 */
    @Transactional
    public int dropBySource(String sourceNo) {
        return taskMapper.delete(new LambdaQueryWrapper<WmsTask>().eq(WmsTask::getSourceNo, sourceNo));
    }

    // ---------- 内部工具 ----------

    private Map<String, Object> toMap(WmsTask task) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", task.getId());
        out.put("作业类型", PICK.equals(task.getTaskType()) ? "拣货" : "上架");
        out.put("波次号", task.getWaveNo());
        out.put("来源面板", task.getSourcePanel());
        out.put("来源单号", task.getSourceNo());
        out.put("行号", task.getRowNo());
        out.put("存货编码", task.getItemCode());
        out.put("存货名称", task.getItemName());
        out.put("规格型号", task.getSpec());
        out.put("计量单位", task.getUnit());
        out.put("仓库", task.getWarehouse());
        out.put("来源货位", task.getFromLocation());
        out.put("目标货位", task.getToLocation());
        out.put("计划数量", task.getPlanQty());
        out.put("完成数量", task.getDoneQty());
        out.put("差异数量", task.getPlanQty().subtract(task.getDoneQty()).max(BigDecimal.ZERO));
        out.put("状态", task.getStatus());
        out.put("作业人", task.getOperator());
        out.put("复核", task.getReviewStatus());
        out.put("复核说明", task.getReviewRemark());
        out.put("单据日期", task.getBizDate() == null ? "" : task.getBizDate().toString());
        out.put("完成时间", task.getDoneTime() == null ? "" : task.getDoneTime().toString());
        return out;
    }

    private Map<String, Object> parse(String raw) {
        if (raw == null || raw.isBlank()) return Map.of();
        try {
            return json.readValue(raw, new TypeReference<>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    /** 明细可能有多个页签，按页签顺序合并成一组行（与库存事务同一口径）。 */
    private List<Map<String, Object>> detailRows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (detail == null) return out;
        for (Object value : detail.values()) {
            if (value instanceof List<?> rows) {
                for (Object row : rows) {
                    if (row instanceof Map<?, ?> map) {
                        Map<String, Object> typed = new LinkedHashMap<>();
                        map.forEach((k, v) -> typed.put(String.valueOf(k), v));
                        out.add(typed);
                    }
                }
            }
        }
        return out;
    }

    private static Object first(Map<String, Object> row, String... keys) {
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return null;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }

    private static String plain(BigDecimal value) {
        return value == null ? "0" : value.stripTrailingZeros().toPlainString();
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value.trim();
    }

    private static LocalDate dateOf(Object value) {
        String text = text(value);
        if (text.length() < 10) return null;
        try {
            return LocalDate.parse(text.substring(0, 10));
        } catch (Exception e) {
            return null;
        }
    }
}
