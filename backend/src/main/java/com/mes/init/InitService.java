package com.mes.init;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

/**
 * 系统初始化向导：状态、一次性锁、上传留痕、管理员保护、模板与解析入口。
 *
 * <p><b>一次性锁</b>（决策 D8）：一个账套只允许初始化一次。完成调用 {@link #finish} 后
 * {@code init_state.locked = true}，此后任何导入请求一律 409，界面也不提供重开入口。
 *
 * <p><b>管理员保护</b>（决策 D3）：初始化永远不创建、不修改管理员账号与管理员角色；
 * 模板里出现 admin 账号或「管理员」岗位时整批拒绝（见 {@link #guardAdmin(List)}）。
 *
 * <p><b>上传留痕</b>：每次上传把原件落盘并记 {@code init_upload}（文件名/大小/哈希/行数），
 * 期初库存另有 {@code init_balance_evidence} 记录「原始值 → 确认值」。
 *
 * <p>本类目前只做「模板 → 上传 → 解析 → 校验 → 留痕 → 锁定」；
 * 各模块落库（P1/P2）在后续实现，接口先留出 {@code apply} 的位置并以明确错误拒绝，避免半成品写脏库。
 */
@Service
public class InitService {

    private static final Logger log = LoggerFactory.getLogger(InitService.class);
    private static final DateTimeFormatter BATCH_FMT = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss");
    /** 墙钟格式：timestamp 直接交给 Jackson 会按 UTC 序列化，界面显示会早 8 小时 */
    private static final DateTimeFormatter WALL = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final String STATUS_RUNNING = "进行中";
    private static final String STATUS_DONE = "已完成";

    private final JdbcTemplate jdbc;
    private final InitTemplateService templates;
    private final InitApplyService applier;

    /** 上传原件落盘目录；与数据库备份同源思路，默认放 /opt/light-mes 下 */
    @Value("${mes.init.upload-dir:/opt/light-mes/init-uploads}")
    private String uploadDir;

    public InitService(JdbcTemplate jdbc, InitTemplateService templates, InitApplyService applier) {
        this.jdbc = jdbc;
        this.templates = templates;
        this.applier = applier;
    }

    // ───────────────────────── 状态与锁 ─────────────────────────

    /** 当前初始化状态：是否已锁定、批次号、已完成模块、各模块能否上传。 */
    public Map<String, Object> state() {
        Map<String, Object> row = stateRow();
        boolean locked = row != null && Boolean.TRUE.equals(row.get("locked"));
        String batchNo = row == null ? "" : text(row.get("batch_no"));
        List<String> done = row == null ? List.of() : splitModules(row.get("modules_done"));

        List<Map<String, Object>> modules = new ArrayList<>();
        for (InitModule m : InitModule.values()) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("code", m.code());
            item.put("label", m.label());
            item.put("fileName", m.fileName());
            item.put("headers", m.headers());
            item.put("done", done.contains(m.code()));
            item.put("needsConfirmation", m.needsConfirmation());
            item.put("blockedBy", m.requires().stream()
                    .filter(req -> !done.contains(req.code()))
                    .map(InitModule::label).toList());
            modules.add(item);
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("locked", locked);
        out.put("batchNo", batchNo);
        out.put("lockedAt", row == null ? null : wallClock(row.get("locked_at")));
        out.put("lockedBy", row == null ? null : row.get("locked_by"));
        out.put("modulesDone", done);
        out.put("modules", modules);
        return out;
    }

    /** 未锁定时抛出 409 语义的异常：所有导入入口的第一道闸。 */
    public void ensureUnlocked() {
        Map<String, Object> row = stateRow();
        if (row != null && Boolean.TRUE.equals(row.get("locked"))) {
            throw new IllegalStateException("本账套已完成初始化（" + wallClock(row.get("locked_at"))
                    + "），不允许再次导入；如需调整请走各面板的导入或库存盘点/调整单");
        }
    }

    /** 完成初始化：锁定账套，返回完成报告。 */
    public Map<String, Object> finish(String operator) {
        Map<String, Object> row = stateRow();
        if (row == null || text(row.get("batch_no")).isEmpty()) {
            throw new IllegalStateException("没有进行中的初始化批次，请先上传资料");
        }
        if (Boolean.TRUE.equals(row.get("locked"))) {
            throw new IllegalStateException("本账套已锁定，不能重复完成");
        }
        String batchNo = text(row.get("batch_no"));
        String status = jdbc.queryForObject(
                "select status from init_batch where batch_no = ?", String.class, batchNo);
        if (!STATUS_RUNNING.equals(status)) {
            throw new IllegalStateException("初始化批次 " + batchNo + " 状态为「" + status + "」，不能完成");
        }
        jdbc.update("""
                update init_state set locked = true, locked_at = now(), locked_by = ?, update_time = now()
                 where id = ?
                """, operator, row.get("id"));
        jdbc.update("update init_batch set status = ? where batch_no = ?", STATUS_DONE, batchNo);
        log.info("初始化完成并锁定账套：batch={} by {}", batchNo, operator);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("batchNo", batchNo);
        out.put("locked", true);
        out.put("modulesDone", splitModules(row.get("modules_done")));
        out.put("message", "初始化已完成，本账套不再允许再次导入");
        return out;
    }

    private Map<String, Object> stateRow() {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "select id, batch_no, modules_done, locked, locked_at, locked_by from init_state order by id limit 1");
        return rows.isEmpty() ? null : rows.get(0);
    }

    /** 取得（必要时创建）当前批次：一次初始化 = 一个批次号。 */
    Map<String, Object> currentBatch(String operator) {
        Map<String, Object> row = stateRow();
        if (row != null && text(row.get("batch_no")).length() > 0) return row;
        String batchNo = "INIT-" + LocalDateTime.now().format(BATCH_FMT);
        jdbc.update("insert into init_batch (batch_no, operator, status) values (?,?,?)",
                batchNo, operator, STATUS_RUNNING);
        jdbc.update("""
                insert into init_state (batch_no, modules_done, locked, create_time, update_time)
                values (?, '', false, now(), now())
                """, batchNo);
        row = stateRow();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", row.get("id"));
        out.put("batch_no", batchNo);
        out.put("modules_done", "");
        out.put("locked", false);
        return out;
    }

    // ───────────────────────── 模板 / 上传 / 解析 ─────────────────────────

    public byte[] template(String moduleCode) throws IOException {
        return templates.template(InitModule.of(moduleCode));
    }

    /**
     * 上传并解析一个模块：留痕原件 + 解析行 + 逐行校验。
     *
     * @return {module, fileName, rowCount, rows, errors, warnings, uploadId}
     */
    public Map<String, Object> upload(String moduleCode, MultipartFile file, String operator) throws IOException {
        ensureUnlocked();
        InitModule module = InitModule.of(moduleCode);
        Map<String, Object> batch = currentBatch(operator);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("module", module.code());
        out.put("moduleLabel", module.label());
        out.put("fileName", file == null ? "" : file.getOriginalFilename());

        List<Map<String, String>> rows;
        try {
            rows = templates.parse(file, module);
        } catch (IllegalArgumentException e) {
            out.put("rowCount", 0);
            out.put("rows", List.of());
            out.put("errors", List.of(e.getMessage()));
            out.put("warnings", List.of());
            return out;
        }

        // 留痕：原件落盘 + 哈希 + 行数（期初库存的证据表另行记录原始值）
        String storedPath = storeFile(batch.get("batch_no").toString(), module, file);
        long uploadId = recordUpload(batch.get("batch_no").toString(), module, file, storedPath, rows.size(), operator);

        List<String> errors = new ArrayList<>();
        List<String> warnings = new ArrayList<>();
        guardAdmin(module, rows, errors);
        validate(module, rows, errors, warnings);

        out.put("rowCount", rows.size());
        out.put("rows", rows);
        out.put("errors", errors);
        out.put("warnings", warnings);
        out.put("uploadId", uploadId);
        out.put("batchNo", batch.get("batch_no"));
        return out;
    }

    /**
     * 管理员保护（决策 D3）：初始化永远不创建/修改管理员。
     *
     * <p>模板里出现 `admin` 账号（忽略大小写）或岗位写成「管理员 / 系统管理员 / 超级管理员」，
     * 整批拒绝并说明原因——不做静默降级，避免客户以为「已经给某人配了管理员」。
     */
    static void guardAdmin(InitModule module, List<Map<String, String>> rows, List<String> errors) {
        if (module != InitModule.EMPLOYEE) return;
        for (Map<String, String> row : rows) {
            String account = value(row, "登录账号").toLowerCase(Locale.ROOT);
            String title = value(row, "职务");
            String duties = value(row, "岗位职责");
            String who = value(row, "姓名").isEmpty() ? value(row, "员工编码") : value(row, "姓名");
            if ("admin".equals(account)) {
                errors.add("第 " + row.get("__row") + " 行：" + who
                        + " 的登录账号不能是 admin（管理员账号由系统保留，初始化不改动）");
            }
            if (title.contains("管理员") || duties.contains("管理员")) {
                errors.add("第 " + row.get("__row") + " 行：" + who
                        + " 的职务/职责写了「管理员」，初始化不创建管理员；请填具体业务岗位（如生产主管、仓管员）");
            }
        }
    }

    /**
     * 逐模块校验（不落库）：必填、编码重复、前置引用、数字列格式。
     *
     * <p>只做「能当场判断」的检查；跨模块引用（BOM 引用存货）在落库阶段按前置模块完成情况再校验，
     * 避免这里查一堆 JSON 造成上传变慢。
     */
    void validate(InitModule module, List<Map<String, String>> rows, List<String> errors, List<String> warnings) {
        List<String> required = module.headers().stream()
                .filter(h -> InitTemplateService.requiredFor(module, h)).toList();
        Map<String, Integer> seen = new LinkedHashMap<>();
        String keyColumn = keyColumn(module);
        for (Map<String, String> row : rows) {
            int line = lineOf(row);
            for (String column : required) {
                String v = value(row, column);
                if (v.isEmpty()) errors.add("第 " + line + " 行：" + column + " 不能为空");
            }
            String key = dedupeKey(module, row);
            if (!key.isEmpty()) {
                Integer prev = seen.put(key, line);
                if (prev != null) errors.add("第 " + line + " 行：" + keyColumn + "「" + value(row, keyColumn)
                        + "」与第 " + prev + " 行重复");
            }
        }
        for (String column : numericColumns(module)) {
            for (Map<String, String> row : rows) {
                String v = value(row, column);
                if (v.isEmpty()) continue;
                try {
                    Double.parseDouble(v);
                } catch (NumberFormatException e) {
                    errors.add("第 " + lineOf(row) + " 行：" + column + "「" + v + "」不是数字");
                }
            }
        }
        if (module == InitModule.OPENING_STOCK) {
            warnings.add("期初库存不会直接落库：上传后需在弹窗里核对/修改并确认，确认时才写期初单与证据表");
        }
    }

    /**
     * 判重键：一项一行的是单列，一对多的必须用组合键——
     * 工艺路线一个编码下多道工序、BOM 一个产品下多个子件，都是正常的多行，不能算重复。
     */
    static String dedupeKey(InitModule module, Map<String, String> row) {
        String primary = value(row, keyColumn(module));
        return switch (module) {
            case ROUTING -> primary + "|" + value(row, "工序顺序") + "|" + value(row, "工序编码");
            case BOM -> primary + "|" + value(row, "子件编码");
            default -> primary;
        };
    }

    static String keyColumn(InitModule module) {
        return switch (module) {
            case DEPT -> "部门编码";
            case EMPLOYEE -> "员工编码";
            case ITEM -> "存货编码";
            case PROCESS -> "工序编码";
            case ROUTING -> "工艺路线编码";
            case BOM -> "产品编码";
            case OPENING_STOCK -> "存货编码";
        };
    }

    static List<String> numericColumns(InitModule module) {
        return switch (module) {
            case DEPT -> List.of("排序");
            case EMPLOYEE -> List.of();
            case ITEM -> List.of("参考成本");
            case PROCESS -> List.of("标准合格率%");
            case ROUTING -> List.of("工序顺序");
            case BOM -> List.of("定额数量", "生产数量", "损耗率%");
            case OPENING_STOCK -> List.of("数量", "主单价");
        };
    }

    // ───────────────────────── 落库入口（P1 实现） ─────────────────────────

    /**
     * 按模块落库。当前实现明确拒绝，避免半成品写脏库——落库逻辑（部门/员工/存货/工序/
     * 路线/BOM/账号）在 P1 补齐；期初库存走 {@code openingConfirm}（P2）。
     */
    public Map<String, Object> apply(String moduleCode, List<Map<String, String>> rows, String operator) {
        ensureUnlocked();
        InitModule module = InitModule.of(moduleCode);
        if (module.needsConfirmation()) {
            throw new IllegalStateException("期初库存要核对后确认才能落库，请走「核对并写入期初」");
        }
        if (rows == null || rows.isEmpty()) {
            throw new IllegalArgumentException("没有可落库的行，请先上传「" + module.fileName() + "」");
        }
        // 前置模块必须先完成：BOM 离不开存货与路线，路线离不开工序
        Map<String, Object> state = state();
        @SuppressWarnings("unchecked")
        List<String> done = (List<String>) state.get("modulesDone");
        for (InitModule required : module.requires()) {
            if (!done.contains(required.code())) {
                throw new IllegalStateException("请先完成「" + required.label() + "」再导入「" + module.label() + "」");
            }
        }
        Map<String, Object> batch = currentBatch(operator);
        String batchNo = batch.get("batch_no").toString();

        Map<String, Object> result = applier.apply(module, rows, operator);
        // 逐条留痕 + 标记模块完成
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> items = (List<Map<String, Object>>) result.get("items");
        for (Map<String, Object> item : items) {
            jdbc.update("""
                    insert into init_batch_item (batch_no, module, natural_key, target, action, message)
                    values (?,?,?,?,?,?)
                    """, batchNo, module.code(), String.valueOf(item.get("key")),
                    String.valueOf(item.get("target")), String.valueOf(item.get("action")),
                    String.valueOf(item.get("message")));
        }
        markModuleDone(batchNo, module);
        result.put("batchNo", batchNo);
        return result;
    }

    /** 标记模块完成（幂等：已存在不重复追加）。 */
    private void markModuleDone(String batchNo, InitModule module) {
        Map<String, Object> row = stateRow();
        List<String> done = row == null ? List.of() : splitModules(row.get("modules_done"));
        if (done.contains(module.code())) return;
        List<String> next = new ArrayList<>(done);
        next.add(module.code());
        jdbc.update("update init_state set modules_done = ?, update_time = now() where batch_no = ?",
                String.join(",", next), batchNo);
    }

    // ───────────────────────── 落盘与留痕 ─────────────────────────

    private String storeFile(String batchNo, InitModule module, MultipartFile file) {
        if (file == null || file.isEmpty()) return "";
        try {
            Path dir = Paths.get(uploadDir, batchNo).toAbsolutePath().normalize();
            Files.createDirectories(dir);
            String name = module.code() + "-" + LocalDateTime.now().format(BATCH_FMT) + ".xlsx";
            Path target = dir.resolve(name).normalize();
            if (!target.startsWith(dir.getParent())) return "";
            file.transferTo(target.toFile());
            return target.toString();
        } catch (Exception e) {
            log.warn("初始化上传原件落盘失败（不影响解析）：{}", e.getMessage());
            return "";
        }
    }

    private long recordUpload(String batchNo, InitModule module, MultipartFile file,
                              String storedPath, int rowCount, String operator) {
        Long id = jdbc.queryForObject("""
                insert into init_upload (batch_no, module, file_name, file_size, stored_path, sha256, row_count, operator)
                values (?,?,?,?,?,?,?,?) returning id
                """, Long.class, batchNo, module.code(),
                file == null ? "" : String.valueOf(file.getOriginalFilename()),
                file == null ? 0L : file.getSize(), storedPath, sha256(file), rowCount, operator);
        return id == null ? 0L : id;
    }

    private static String sha256(MultipartFile file) {
        if (file == null || file.isEmpty()) return "";
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(md.digest(file.getBytes()));
        } catch (Exception e) {
            return "";
        }
    }

    private static String value(Map<String, String> row, String key) {
        String v = row == null ? null : row.get(key);
        return v == null ? "" : v.trim();
    }

    private static int lineOf(Map<String, String> row) {
        try {
            return Integer.parseInt(value(row, "__row"));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    /** 时间统一成「本地墙钟字符串」，与其它模块一致，避免前端显示成 UTC。 */
    private static String wallClock(Object value) {
        if (value == null) return "";
        if (value instanceof Timestamp ts) return ts.toLocalDateTime().format(WALL);
        return text(value);
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static List<String> splitModules(Object raw) {
        String s = text(raw);
        if (s.isEmpty()) return List.of();
        List<String> out = new ArrayList<>();
        for (String part : s.split(",")) {
            String code = part.trim();
            if (!code.isEmpty()) out.add(code);
        }
        return out;
    }
}