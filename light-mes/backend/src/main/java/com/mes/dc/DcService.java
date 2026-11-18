package com.mes.dc;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.sql.Timestamp;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * 数采平台（DC = Data Collection）：设备接入、采集点、规则、实时监控、报警、状态统计、硬件上报。
 *
 * <p>移植自 YINJIA-MES 的 {@code bs_dc_*} 七张表与 DcService/DcPlatformService，
 * 方言换 PostgreSQL，权限与审计列按 light-mes 口径。
 *
 * <p><b>数据真实性</b>：没有硬件上报就是「离线 / 无数据 / 待配置」，本服务不编造任何演示值。
 * 采集数据只能由网关调 {@code POST /api/px/dc/ingest}（或文本上报口 / 心跳）写入。
 *
 * <p><b>在线判定</b>：最近上报时间 + 心跳周期 × 3（下限 180 秒）之内算在线；状态变化写
 * {@code dc_status_log}，状态统计的在线率据此计算。
 */
@Service
public class DcService {

    private static final Logger log = LoggerFactory.getLogger(DcService.class);

    /** 硬件多久没上报算离线：心跳周期 × 该倍数，且有下限，避免 5 秒心跳的设备抖一下就被判离线 */
    static final int OFFLINE_MULTIPLIER = 3;
    static final int OFFLINE_MIN_SECONDS = 180;

    /** 五连检查的键与中文名（顺序即页面展示顺序） */
    public static final String[][] CHECKS = {
            {"gateway", "绑定网关"},
            {"project", "数采工程"},
            {"scada", "绘制组态"},
            {"rule", "数采规则"},
            {"debug", "数据调试"},
    };

    private static final DateTimeFormatter WALL = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final DateTimeFormatter DAY_LABEL = DateTimeFormatter.ofPattern("MM-dd");
    private static final String ALARM_HANDLED = "已处理";
    private static final String ALARM_OPEN = "未处理";

    private final JdbcTemplate jdbc;

    public DcService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    // ───────────────────────── ① IOT 设备配置 ─────────────────────────

    /** 配置页：KPI + 网关表 + 设备五连检查表。 */
    public Map<String, Object> config() {
        List<Map<String, Object>> devices = devices();
        List<Map<String, Object>> gateways = gateways();
        int bound = 0;
        int ready = 0;
        for (Map<String, Object> row : devices) {
            @SuppressWarnings("unchecked")
            Map<String, Boolean> checks = (Map<String, Boolean>) row.get("checks");
            if (Boolean.TRUE.equals(checks.get("gateway"))) bound++;
            if (Boolean.TRUE.equals(row.get("configured"))) ready++;
        }
        Map<String, Object> kpis = new LinkedHashMap<>();
        kpis.put("deviceTotal", devices.size());
        kpis.put("gatewayBound", bound);
        kpis.put("fullyReady", ready);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("kpis", kpis);
        out.put("gateways", gateways);
        out.put("devices", devices);
        out.put("checkDefs", checkDefs());
        return out;
    }

    public List<Map<String, Object>> devices() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> row : deviceRows()) out.add(view(row));
        return out;
    }

    /**
     * 设备行 + 派生字段。五连只看台账字段，不引入额外的「已配」标记，免得出现假绿：
     * 网关非空 / 工程非空 / 组态已画 / 有启用规则 / 有调试时间。
     */
    private List<Map<String, Object>> deviceRows() {
        return jdbc.queryForList("""
                select d.id, d.code, d.name, d.device_type, d.model, d.serial_no, d.gateway_id,
                       d.ip, d.port, d.protocol, d.workshop, d.project_code, d.scada_drawn,
                       d.last_debug_at, d.heartbeat_seconds, d.status, d.last_seen_at, d.enabled, d.remark,
                       coalesce(g.name, '') as gateway_name,
                       (g.api_key is not null and g.api_key <> '') as key_set,
                       (select count(*) from dc_point p where p.device_id = d.id and not p.cancelled) as point_count,
                       (select count(*) from dc_rule r where r.device_id = d.id and not r.cancelled and r.enabled) as rule_count,
                       (select count(*) from dc_sample s where s.device_id = d.id) as sample_count,
                       (select count(*) from dc_alarm a where a.device_id = d.id and not a.cancelled and a.state = '未处理') as alarm_open
                  from dc_device d
                  left join dc_gateway g on g.id = d.gateway_id
                 where not d.cancelled
                 order by d.device_type, d.code
                """);
    }

    private Map<String, Object> view(Map<String, Object> row) {
        Map<String, Boolean> checks = checks(row);
        List<String> missing = new ArrayList<>();
        int done = 0;
        for (String[] c : CHECKS) {
            if (Boolean.TRUE.equals(checks.get(c[0]))) done++;
            else missing.add(c[1]);
        }
        Map<String, Object> v = new LinkedHashMap<>();
        v.put("id", row.get("id"));
        v.put("code", str(row.get("code")));
        v.put("name", str(row.get("name")));
        v.put("type", str(row.get("device_type")));
        v.put("model", str(row.get("model")));
        v.put("serialNo", str(row.get("serial_no")));
        v.put("gatewayId", row.get("gateway_id"));
        v.put("gatewayName", str(row.get("gateway_name")));
        v.put("ip", str(row.get("ip")));
        v.put("port", row.get("port"));
        v.put("workshop", str(row.get("workshop")));
        v.put("projectCode", str(row.get("project_code")));
        v.put("scadaDrawn", Boolean.TRUE.equals(row.get("scada_drawn")));
        v.put("heartbeatSeconds", num(row.get("heartbeat_seconds")));
        v.put("remark", str(row.get("remark")));
        v.put("enabled", Boolean.TRUE.equals(row.get("enabled")));
        v.put("online", isOnline(row));
        v.put("status", effectiveStatus(row));
        v.put("lastSeenAt", wallClock(row.get("last_seen_at")));
        v.put("checks", checks);
        v.put("configured", done == CHECKS.length);
        v.put("progress", Math.round(done * 100f / CHECKS.length));
        v.put("pointCount", num(row.get("point_count")));
        v.put("ruleCount", num(row.get("rule_count")));
        v.put("sampleCount", num(row.get("sample_count")));
        v.put("alarmOpen", num(row.get("alarm_open")));
        v.put("missingTip", missing.isEmpty() ? null : String.join("、", missing));
        return v;
    }

    private Map<String, Boolean> checks(Map<String, Object> row) {
        Map<String, Boolean> c = new LinkedHashMap<>();
        c.put("gateway", row.get("gateway_id") != null);
        c.put("project", !str(row.get("project_code")).isBlank());
        c.put("scada", Boolean.TRUE.equals(row.get("scada_drawn")));
        c.put("rule", num(row.get("rule_count")) > 0);
        c.put("debug", row.get("last_debug_at") != null);
        return c;
    }

    public List<Map<String, Object>> checkDefs() {
        List<Map<String, Object>> defs = new ArrayList<>();
        for (String[] c : CHECKS) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("key", c[0]);
            m.put("label", c[1]);
            defs.add(m);
        }
        return defs;
    }

    public List<Map<String, Object>> gateways() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> row : jdbc.queryForList("""
                select g.id, g.code, g.name, g.protocol, g.host, g.port, g.status, g.last_seen_at,
                       g.enabled, g.remark,
                       (g.api_key is not null and g.api_key <> '') as key_set,
                       (select count(*) from dc_device d where d.gateway_id = g.id and not d.cancelled) as device_count
                  from dc_gateway g
                 where not g.cancelled
                 order by g.code
                """)) {
            Map<String, Object> g = new LinkedHashMap<>();
            g.put("id", row.get("id"));
            g.put("code", str(row.get("code")));
            g.put("name", str(row.get("name")));
            g.put("protocol", str(row.get("protocol")));
            g.put("host", str(row.get("host")));
            g.put("port", row.get("port"));
            g.put("remark", str(row.get("remark")));
            g.put("status", "online".equalsIgnoreCase(str(row.get("status"))));
            g.put("enabled", Boolean.TRUE.equals(row.get("enabled")));
            g.put("lastSeenAt", wallClock(row.get("last_seen_at")));
            g.put("keySet", Boolean.TRUE.equals(row.get("key_set")));
            g.put("deviceCount", num(row.get("device_count")));
            out.add(g);
        }
        return out;
    }

    /** 保存网关：不传 apiKey 时保持原密钥（编辑页看不到旧密钥，也就不该把它清空）。 */
    public Map<String, Object> saveGateway(Map<String, Object> body, String user) {
        Long id = longOrNull(body.get("id"));
        String code = str(body.get("code")).trim();
        String name = str(body.get("name")).trim();
        if (code.isEmpty()) throw new IllegalArgumentException("缺少网关编码");
        if (name.isEmpty()) throw new IllegalArgumentException("缺少网关名称");
        String protocol = str(body.getOrDefault("protocol", "MQTT"));
        String host = emptyToNull(body.get("host"));
        Integer port = intOrNull(body.get("port"));
        String remark = emptyToNull(body.get("remark"));
        boolean enabled = !(body.get("enabled") instanceof Boolean b) || b;
        String apiKey = emptyToNull(body.get("apiKey"));

        if (id == null) {
            Integer dup = jdbc.queryForObject(
                    "select count(*) from dc_gateway where code = ? and not cancelled", Integer.class, code);
            if (dup != null && dup > 0) throw new IllegalArgumentException("网关编码已存在：" + code);
            id = jdbc.queryForObject("""
                    insert into dc_gateway (code, name, protocol, host, port, api_key, enabled, remark, create_by, create_time)
                    values (?,?,?,?,?,?,?,?,?,now()) returning id
                    """, Long.class, code, name, protocol, host, port, apiKey, enabled, remark, user);
        } else {
            Integer dup = jdbc.queryForObject(
                    "select count(*) from dc_gateway where code = ? and id <> ? and not cancelled", Integer.class, code, id);
            if (dup != null && dup > 0) throw new IllegalArgumentException("网关编码已存在：" + code);
            jdbc.update("""
                    update dc_gateway set code = ?, name = ?, protocol = ?, host = ?, port = ?,
                           enabled = ?, remark = ?, update_by = ?, update_time = now()
                     where id = ?
                    """, code, name, protocol, host, port, enabled, remark, user, id);
            if (apiKey != null) {
                jdbc.update("update dc_gateway set api_key = ?, update_by = ?, update_time = now() where id = ?",
                        apiKey, user, id);
            }
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        return out;
    }

    /** 删除网关：先解绑设备（保留设备台账），再软删网关。 */
    public Map<String, Object> deleteGateway(Long id, String user) {
        int detached = jdbc.update(
                "update dc_device set gateway_id = null, update_by = ?, update_time = now() where gateway_id = ? and not cancelled",
                user, id);
        jdbc.update("update dc_gateway set cancelled = true, update_by = ?, update_time = now() where id = ?", user, id);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        out.put("detachedDevices", detached);
        return out;
    }

    public Map<String, Object> saveDevice(Map<String, Object> body, String user) {
        Long id = longOrNull(body.get("id"));
        String code = str(body.get("code")).trim();
        String name = str(body.get("name")).trim();
        String type = str(body.getOrDefault("deviceType", body.get("type"))).trim();
        if (code.isEmpty()) throw new IllegalArgumentException("缺少设备编码");
        if (name.isEmpty()) throw new IllegalArgumentException("缺少设备名称");
        if (type.isEmpty()) throw new IllegalArgumentException("缺少设备类型");
        Long gatewayId = longOrNull(body.get("gatewayId"));
        Integer dup = jdbc.queryForObject(
                "select count(*) from dc_device where code = ? and coalesce(id, 0) <> ? and not cancelled",
                Integer.class, code, id == null ? 0L : id);
        if (dup != null && dup > 0) throw new IllegalArgumentException("设备编码已存在：" + code);
        boolean scada = Boolean.TRUE.equals(body.get("scadaDrawn")) || "Y".equalsIgnoreCase(str(body.get("scadaDrawn")));
        boolean enabled = !(body.get("enabled") instanceof Boolean b) || b;
        Integer heartbeat = intOrNull(body.get("heartbeatSeconds"));
        if (heartbeat == null || heartbeat <= 0) heartbeat = 60;

        if (id == null) {
            id = jdbc.queryForObject("""
                    insert into dc_device (code, name, device_type, model, serial_no, gateway_id, ip, port, protocol,
                                           workshop, project_code, scada_drawn, heartbeat_seconds, enabled, remark,
                                           create_by, create_time)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,now()) returning id
                    """, Long.class, code, name, type, emptyToNull(body.get("model")), emptyToNull(body.get("serialNo")),
                    gatewayId, emptyToNull(body.get("ip")), intOrNull(body.get("port")), emptyToNull(body.get("protocol")),
                    emptyToNull(body.get("workshop")), emptyToNull(body.get("projectCode")), scada, heartbeat, enabled,
                    emptyToNull(body.get("remark")), user);
        } else {
            jdbc.update("""
                    update dc_device set code = ?, name = ?, device_type = ?, model = ?, serial_no = ?, gateway_id = ?,
                           ip = ?, port = ?, protocol = ?, workshop = ?, project_code = ?, scada_drawn = ?,
                           heartbeat_seconds = ?, enabled = ?, remark = ?, update_by = ?, update_time = now()
                     where id = ?
                    """, code, name, type, emptyToNull(body.get("model")), emptyToNull(body.get("serialNo")), gatewayId,
                    emptyToNull(body.get("ip")), intOrNull(body.get("port")), emptyToNull(body.get("protocol")),
                    emptyToNull(body.get("workshop")), emptyToNull(body.get("projectCode")), scada, heartbeat, enabled,
                    emptyToNull(body.get("remark")), user, id);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        return out;
    }

    /** 删除设备：台账软删，采集点/规则一并软删；历史流水与报警保留可查。 */
    public Map<String, Object> deleteDevice(Long id, String user) {
        jdbc.update("update dc_point set cancelled = true, update_by = ?, update_time = now() where device_id = ?", user, id);
        jdbc.update("update dc_rule set cancelled = true, update_by = ?, update_time = now() where device_id = ?", user, id);
        jdbc.update("update dc_device set cancelled = true, update_by = ?, update_time = now() where id = ?", user, id);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        return out;
    }

    /** 标记「数据调试」已完成（对接现场联调时人工点一下，五连的调试项才变绿）。 */
    public Map<String, Object> markDebug(Long id, String user) {
        jdbc.update("update dc_device set last_debug_at = now(), update_by = ?, update_time = now() where id = ?", user, id);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        return out;
    }

    // ───────────────────────── 采集点 / 数采规则 ─────────────────────────

    public List<Map<String, Object>> points(Long deviceId) {
        List<Map<String, Object>> out = new ArrayList<>();
        String sql = """
                select p.* from dc_point p
                 where not p.cancelled
                """ + (deviceId == null ? "" : " and p.device_id = ?") + " order by p.device_id, p.sort_no, p.code";
        List<Map<String, Object>> rows = deviceId == null
                ? jdbc.queryForList(sql)
                : jdbc.queryForList(sql, deviceId);
        for (Map<String, Object> r : rows) {
            Map<String, Object> p = new LinkedHashMap<>();
            p.put("id", r.get("id"));
            p.put("deviceId", r.get("device_id"));
            p.put("code", str(r.get("code")));
            p.put("name", str(r.get("name")));
            p.put("dataType", str(r.get("data_type")));
            p.put("unit", str(r.get("unit")));
            p.put("addr", str(r.get("addr")));
            p.put("freqSeconds", num(r.get("freq_seconds")));
            p.put("lowLimit", r.get("low_limit"));
            p.put("highLimit", r.get("high_limit"));
            p.put("sortNo", num(r.get("sort_no")));
            p.put("enabled", Boolean.TRUE.equals(r.get("enabled")));
            p.put("remark", str(r.get("remark")));
            out.add(p);
        }
        return out;
    }

    public Map<String, Object> savePoint(Map<String, Object> body, String user) {
        Long id = longOrNull(body.get("id"));
        Long deviceId = longOrNull(body.get("deviceId"));
        String code = str(body.get("code")).trim();
        String name = str(body.get("name")).trim();
        if (deviceId == null) throw new IllegalArgumentException("缺少所属设备");
        if (code.isEmpty()) throw new IllegalArgumentException("缺少采集点编码");
        if (name.isEmpty()) throw new IllegalArgumentException("缺少采集点名称");
        Integer dup = jdbc.queryForObject("""
                select count(*) from dc_point
                 where device_id = ? and code = ? and coalesce(id, 0) <> ? and not cancelled
                """, Integer.class, deviceId, code, id == null ? 0L : id);
        if (dup != null && dup > 0) throw new IllegalArgumentException("同一设备下采集点编码重复：" + code);
        boolean enabled = !(body.get("enabled") instanceof Boolean b) || b;
        Integer freq = intOrNull(body.get("freqSeconds"));
        if (freq == null || freq <= 0) freq = 5;
        BigDecimal low = decOrNull(body.get("lowLimit"));
        BigDecimal high = decOrNull(body.get("highLimit"));
        String dataType = str(body.getOrDefault("dataType", "数值"));
        if (dataType.isBlank()) dataType = "数值";
        if (low != null && high != null && low.compareTo(high) > 0) {
            throw new IllegalArgumentException("下限不能大于上限");
        }
        if (id == null) {
            id = jdbc.queryForObject("""
                    insert into dc_point (device_id, code, name, data_type, unit, addr, freq_seconds,
                                          low_limit, high_limit, sort_no, enabled, remark, create_by, create_time)
                    values (?,?,?,?,?,?,?,?,?,?,?,?,?,now()) returning id
                    """, Long.class, deviceId, code, name, dataType, emptyToNull(body.get("unit")),
                    emptyToNull(body.get("addr")), freq, low, high, num(body.get("sortNo")), enabled,
                    emptyToNull(body.get("remark")), user);
        } else {
            jdbc.update("""
                    update dc_point set device_id = ?, code = ?, name = ?, data_type = ?, unit = ?, addr = ?,
                           freq_seconds = ?, low_limit = ?, high_limit = ?, sort_no = ?, enabled = ?, remark = ?,
                           update_by = ?, update_time = now()
                     where id = ?
                    """, deviceId, code, name, dataType, emptyToNull(body.get("unit")), emptyToNull(body.get("addr")),
                    freq, low, high, num(body.get("sortNo")), enabled, emptyToNull(body.get("remark")), user, id);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        return out;
    }

    public int deletePoint(Long id, String user) {
        return jdbc.update("update dc_point set cancelled = true, update_by = ?, update_time = now() where id = ?", user, id);
    }

    public List<Map<String, Object>> rules(Long deviceId) {
        List<Map<String, Object>> out = new ArrayList<>();
        String sql = """
                select r.* from dc_rule r
                 where not r.cancelled
                """ + (deviceId == null ? "" : " and r.device_id = ?") + " order by r.device_id, r.id";
        List<Map<String, Object>> rows = deviceId == null
                ? jdbc.queryForList(sql)
                : jdbc.queryForList(sql, deviceId);
        for (Map<String, Object> r : rows) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("id", r.get("id"));
            m.put("deviceId", r.get("device_id"));
            m.put("name", str(r.get("name")));
            m.put("ruleType", str(r.get("rule_type")));
            m.put("expression", str(r.get("expression")));
            m.put("params", str(r.get("params")));
            m.put("enabled", Boolean.TRUE.equals(r.get("enabled")));
            m.put("remark", str(r.get("remark")));
            out.add(m);
        }
        return out;
    }

    public Map<String, Object> saveRule(Map<String, Object> body, String user) {
        Long id = longOrNull(body.get("id"));
        Long deviceId = longOrNull(body.get("deviceId"));
        String name = str(body.get("name")).trim();
        if (deviceId == null) throw new IllegalArgumentException("缺少所属设备");
        if (name.isEmpty()) throw new IllegalArgumentException("缺少规则名称");
        boolean enabled = !(body.get("enabled") instanceof Boolean b) || b;
        String ruleType = str(body.getOrDefault("ruleType", "定时采集"));
        if (ruleType.isBlank()) ruleType = "定时采集";
        if (id == null) {
            id = jdbc.queryForObject("""
                    insert into dc_rule (device_id, name, rule_type, expression, params, enabled, remark, create_by, create_time)
                    values (?,?,?,?,?,?,?,?,now()) returning id
                    """, Long.class, deviceId, name, ruleType, emptyToNull(body.get("expression")),
                    emptyToNull(body.get("params")), enabled, emptyToNull(body.get("remark")), user);
        } else {
            jdbc.update("""
                    update dc_rule set device_id = ?, name = ?, rule_type = ?, expression = ?, params = ?,
                           enabled = ?, remark = ?, update_by = ?, update_time = now()
                     where id = ?
                    """, deviceId, name, ruleType, emptyToNull(body.get("expression")),
                    emptyToNull(body.get("params")), enabled, emptyToNull(body.get("remark")), user, id);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        return out;
    }

    public int deleteRule(Long id, String user) {
        return jdbc.update("update dc_rule set cancelled = true, update_by = ?, update_time = now() where id = ?", user, id);
    }

    // ───────────────────────── ② 数据监控 ─────────────────────────

    /** 监控页：设备列表 + 指定设备的采集点当前值 + 最新 10 条流水 + 报警。 */
    public Map<String, Object> monitor(Long deviceId) {
        List<Map<String, Object>> devices = new ArrayList<>();
        for (Map<String, Object> row : deviceRows()) {
            Map<String, Object> v = new LinkedHashMap<>();
            v.put("id", row.get("id"));
            v.put("code", str(row.get("code")));
            v.put("name", str(row.get("name")));
            v.put("type", str(row.get("device_type")));
            v.put("enabled", Boolean.TRUE.equals(row.get("enabled")));
            v.put("online", isOnline(row));
            v.put("status", effectiveStatus(row));
            v.put("lastSeenAt", wallClock(row.get("last_seen_at")));
            v.put("alarmOpen", num(row.get("alarm_open")));
            devices.add(v);
        }
        Long target = deviceId;
        if (target == null && !devices.isEmpty()) target = ((Number) devices.get(0).get("id")).longValue();
        Map<String, Object> targetRow = null;
        for (Map<String, Object> row : deviceRows()) {
            if (target != null && target.equals(row.get("id"))) {
                targetRow = row;
                break;
            }
        }

        List<Map<String, Object>> points = new ArrayList<>();
        if (target != null) {
            long window = offlineWindow(targetRow);
            for (Map<String, Object> r : jdbc.queryForList("""
                    select p.id as point_id, p.code as point_code, p.name as point_name, p.unit,
                           p.low_limit, p.high_limit, s.value_num, s.value_text, s.quality, s.collect_at
                      from dc_point p
                      left join lateral (
                            select x.value_num, x.value_text, x.quality, x.collect_at
                              from dc_sample x
                             where x.point_id = p.id
                             order by x.collect_at desc, x.id desc
                             limit 1
                      ) s on true
                     where p.device_id = ? and not p.cancelled and p.enabled
                     order by p.sort_no, p.code
                    """, target)) {
                String text = str(r.get("value_text"));
                Object valueNum = r.get("value_num");
                if (text.isEmpty()) text = valueNum == null ? "" : plain(valueNum);
                LocalDateTime at = toLocal(r.get("collect_at"));
                String status = "normal";
                if (isOver(r.get("low_limit"), r.get("high_limit"), r.get("value_num"))) status = "alarm";
                else if (at == null || Duration.between(at, LocalDateTime.now()).getSeconds() > window) status = "stale";
                Map<String, Object> p = new LinkedHashMap<>();
                p.put("pointId", r.get("point_id"));
                p.put("code", str(r.get("point_code")));
                p.put("name", str(r.get("point_name")));
                p.put("valueText", text);
                p.put("valueNum", valueNum);
                p.put("unit", str(r.get("unit")));
                p.put("at", at == null ? null : at.withNano(0).toString());
                p.put("lowLimit", r.get("low_limit"));
                p.put("highLimit", r.get("high_limit"));
                p.put("quality", str(r.get("quality")));
                p.put("status", status);
                points.add(p);
            }
        }

        List<Map<String, Object>> samples = new ArrayList<>();
        if (target != null) {
            for (Map<String, Object> r : jdbc.queryForList("""
                    select s.id, s.value_num, s.value_text, s.quality, s.collect_at,
                           coalesce(p.code, '') as point_code, coalesce(p.name, '') as point_name,
                           coalesce(p.unit, '') as unit
                      from dc_sample s
                      left join dc_point p on p.id = s.point_id
                     where s.device_id = ?
                     order by s.id desc
                     limit 10
                    """, target)) {
                Map<String, Object> x = new LinkedHashMap<>();
                x.put("id", r.get("id"));
                x.put("pointCode", str(r.get("point_code")));
                x.put("pointName", str(r.get("point_name")));
                x.put("valueText", str(r.get("value_text")).isEmpty() ? plain(r.get("value_num")) : str(r.get("value_text")));
                x.put("unit", str(r.get("unit")));
                x.put("quality", str(r.get("quality")));
                x.put("at", wallClock(r.get("collect_at")));
                samples.add(x);
            }
        }

        Map<String, Object> alarmPage = alarms(null, null, target, 1, 50);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> alarmRows = (List<Map<String, Object>>) alarmPage.get("rows");
        int open = 0;
        for (Map<String, Object> a : alarmRows) if (!ALARM_HANDLED.equals(str(a.get("state")))) open++;

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("deviceId", target);
        out.put("devices", devices);
        out.put("points", points);
        out.put("samples", samples);
        out.put("alarms", alarmRows);
        out.put("alarmOpen", open);
        out.put("serverTime", LocalDateTime.now().withNano(0).toString());
        return out;
    }

    public Map<String, Object> alarms(String state, String level, Long deviceId, int pageNo, int pageSize) {
        StringBuilder where = new StringBuilder(" where not a.cancelled");
        List<Object> args = new ArrayList<>();
        if (state != null && !state.isBlank()) {
            where.append(" and a.state = ?");
            args.add(state.trim());
        }
        if (level != null && !level.isBlank()) {
            where.append(" and a.level = ?");
            args.add(level.trim());
        }
        if (deviceId != null) {
            where.append(" and a.device_id = ?");
            args.add(deviceId);
        }
        Integer total = jdbc.queryForObject("select count(*) from dc_alarm a" + where, Integer.class, args.toArray());
        int size = Math.max(1, Math.min(pageSize, 200));
        int offset = Math.max(0, (Math.max(1, pageNo) - 1) * size);
        List<Object> pageArgs = new ArrayList<>(args);
        pageArgs.add(size);
        pageArgs.add(offset);
        List<Map<String, Object>> rows = jdbc.queryForList("""
                select a.id, a.device_id, d.code as device_code, d.name as device_name, a.point_id,
                       p.code as point_code, p.name as point_name, a.level, a.message, a.value_text,
                       a.occur_at, a.state, a.handled_by, a.handled_at, a.handled_note
                  from dc_alarm a
                  left join dc_device d on d.id = a.device_id
                  left join dc_point p on p.id = a.point_id
                """ + where + """
                 order by case when a.state = '未处理' then 0 else 1 end, a.occur_at desc
                 limit ? offset ?
                """, pageArgs.toArray());
        List<Map<String, Object>> outRows = new ArrayList<>();
        for (Map<String, Object> r : rows) {
            Map<String, Object> a = new LinkedHashMap<>();
            a.put("id", r.get("id"));
            a.put("deviceId", r.get("device_id"));
            a.put("deviceCode", str(r.get("device_code")));
            a.put("deviceName", str(r.get("device_name")));
            a.put("pointCode", str(r.get("point_code")));
            a.put("pointName", str(r.get("point_name")));
            a.put("level", str(r.get("level")));
            a.put("message", str(r.get("message")));
            a.put("valueText", str(r.get("value_text")));
            a.put("occurAt", wallClock(r.get("occur_at")));
            a.put("state", str(r.get("state")));
            a.put("handledBy", str(r.get("handled_by")));
            a.put("handledAt", wallClock(r.get("handled_at")));
            a.put("handledNote", str(r.get("handled_note")));
            outRows.add(a);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("total", total == null ? 0 : total);
        out.put("pageNo", pageNo);
        out.put("pageSize", size);
        out.put("rows", outRows);
        return out;
    }

    /** 处理报警：只把状态置「已处理」并留痕，不删除记录。 */
    public int handleAlarm(Long id, String user, String note) {
        return jdbc.update("""
                update dc_alarm set state = '已处理', handled_by = ?, handled_at = now(), handled_note = ?
                 where id = ? and not cancelled
                """, user, str(note), id);
    }

    // ───────────────────────── ③ 状态统计 ─────────────────────────

    /**
     * 状态统计：当前在线数 + 窗口内每台设备的在线/离线时长与在线率 + 按日趋势 + 报警汇总。
     *
     * <p>口径：从未接入（没有状态日志、也没有任何上报）的设备不参与在线率分母，时长返回 null，
     * 界面显示「—」，避免把「设备还不存在的那段时间」算成离线。
     */
    public Map<String, Object> stats(String from, String to) {
        LocalDate toDate = parseDate(to, LocalDate.now());
        LocalDate fromDate = parseDate(from, toDate.minusDays(29));
        if (fromDate.isAfter(toDate)) {
            LocalDate swap = fromDate;
            fromDate = toDate;
            toDate = swap;
        }
        LocalDateTime now = LocalDateTime.now();
        LocalDateTime winStart = fromDate.atStartOfDay();
        LocalDateTime winEnd = toDate.plusDays(1).atStartOfDay();
        if (winEnd.isAfter(now)) winEnd = now;
        if (winStart.isAfter(winEnd)) winStart = winEnd;
        long winMs = Math.max(1L, Duration.between(winStart, winEnd).toMillis());

        List<Map<String, Object>> devices = jdbc.queryForList("""
                select id, code, name, device_type, status, last_seen_at, enabled, heartbeat_seconds
                  from dc_device where not cancelled order by device_type, code
                """);
        List<Map<String, Object>> logs = jdbc.queryForList("""
                select device_id, status, changed_at from dc_status_log
                 where changed_at < ? order by device_id, changed_at
                """, Timestamp.valueOf(winEnd));
        Map<Long, List<Map<String, Object>>> logsByDevice = new HashMap<>();
        for (Map<String, Object> l : logs) {
            logsByDevice.computeIfAbsent(((Number) l.get("device_id")).longValue(), k -> new ArrayList<>()).add(l);
        }
        Map<Long, LocalDateTime> firstSample = new HashMap<>();
        for (Map<String, Object> r : jdbc.queryForList("""
                select device_id, min(collect_at) as first_at from dc_sample group by device_id
                """)) {
            firstSample.put(((Number) r.get("device_id")).longValue(), toLocal(r.get("first_at")));
        }
        Map<Long, LocalDateTime> knownStart = new HashMap<>(firstSample);
        for (Map.Entry<Long, List<Map<String, Object>>> e : logsByDevice.entrySet()) {
            LocalDateTime first = null;
            for (Map<String, Object> l : e.getValue()) {
                LocalDateTime at = toLocal(l.get("changed_at"));
                if (at != null && (first == null || at.isBefore(first))) first = at;
            }
            if (first == null) continue;
            LocalDateTime prev = knownStart.get(e.getKey());
            if (prev == null || first.isBefore(prev)) knownStart.put(e.getKey(), first);
        }
        Map<Long, Integer> alarmCount = new HashMap<>();
        for (Map<String, Object> r : jdbc.queryForList("""
                select device_id, count(*) as cnt from dc_alarm
                 where not cancelled and occur_at >= ? and occur_at < ?
                 group by device_id
                """, Timestamp.valueOf(winStart), Timestamp.valueOf(winEnd))) {
            alarmCount.put(((Number) r.get("device_id")).longValue(), ((Number) r.get("cnt")).intValue());
        }

        int enabledTotal = 0;
        int connectedTotal = 0;
        int onlineNow = 0;
        int offlineNow = 0;
        int disabled = 0;
        List<Map<String, Object>> byDevice = new ArrayList<>();
        List<Map<String, Object>> activeDevices = new ArrayList<>();
        for (Map<String, Object> row : devices) {
            Long id = ((Number) row.get("id")).longValue();
            boolean enabled = Boolean.TRUE.equals(row.get("enabled"));
            boolean online = enabled && isOnline(row);
            String current = !enabled ? "disabled" : (online ? "online" : "offline");
            boolean connectedNow = logsByDevice.get(id) != null && !logsByDevice.get(id).isEmpty()
                    || toLocal(row.get("last_seen_at")) != null;
            if (enabled) {
                enabledTotal++;
                if (connectedNow) connectedTotal++;
                if (online) onlineNow++; else offlineNow++;
                activeDevices.add(row);
            } else {
                disabled++;
            }
            List<Map<String, Object>> devLogs = logsByDevice.get(id);
            boolean neverConnected = (devLogs == null || devLogs.isEmpty()) && toLocal(row.get("last_seen_at")) == null;
            Map<String, Object> d = new LinkedHashMap<>();
            d.put("deviceId", id);
            d.put("deviceCode", str(row.get("code")));
            d.put("deviceName", str(row.get("name")));
            d.put("deviceType", str(row.get("device_type")));
            d.put("currentStatus", current);
            d.put("connected", !neverConnected);
            if (neverConnected) {
                d.put("onlineSeconds", null);
                d.put("offlineSeconds", null);
                d.put("onlineRate", null);
                d.put("statusChanges", 0);
            } else {
                long[] usage = foldUsage(devLogs, winStart, winEnd);
                if (usage[0] + usage[1] == 0) {
                    LocalDateTime first = firstSample.get(id);
                    if (first != null && first.isBefore(winEnd)) {
                        LocalDateTime start = first.isBefore(winStart) ? winStart : first;
                        usage = new long[]{Duration.between(start, winEnd).toMillis(), 0L, 0L};
                    }
                }
                long knownMs = usage[0] + usage[1];
                d.put("onlineSeconds", Math.round(usage[0] / 1000.0));
                d.put("offlineSeconds", Math.round(usage[1] / 1000.0));
                d.put("onlineRate", knownMs <= 0 ? null : round1(usage[0] * 100.0 / knownMs));
                d.put("statusChanges", usage[2]);
            }
            d.put("alarmCount", alarmCount.getOrDefault(id, 0));
            byDevice.add(d);
        }

        Map<String, Object> summary = new LinkedHashMap<>();
        summary.put("deviceTotal", enabledTotal);
        summary.put("disabledTotal", disabled);
        summary.put("onlineNow", onlineNow);
        summary.put("offlineNow", offlineNow);
        summary.put("connectedTotal", connectedTotal);
        summary.put("onlineRate", connectedTotal == 0 ? null : round1(onlineNow * 100.0 / connectedTotal));

        List<Map<String, Object>> trend = new ArrayList<>();
        for (LocalDate day = fromDate; !day.isAfter(toDate); day = day.plusDays(1)) {
            LocalDateTime snapshot = day.plusDays(1).atStartOfDay();
            if (snapshot.isAfter(now)) snapshot = now;
            int online = 0;
            int offline = 0;
            for (Map<String, Object> row : activeDevices) {
                Long id = ((Number) row.get("id")).longValue();
                LocalDateTime known = knownStart.get(id);
                if (known == null || snapshot.isBefore(known)) continue;
                List<Map<String, Object>> devLogs = logsByDevice.get(id);
                String st;
                if (devLogs == null || devLogs.isEmpty()) {
                    LocalDateTime lastSeen = toLocal(row.get("last_seen_at"));
                    long win = offlineWindow(row);
                    st = (lastSeen == null || snapshot.isAfter(lastSeen.plusSeconds(win))) ? "offline" : "online";
                } else {
                    st = statusAt(devLogs, snapshot);
                }
                if ("online".equalsIgnoreCase(st)) online++; else offline++;
            }
            Map<String, Object> t = new LinkedHashMap<>();
            t.put("label", day.format(DAY_LABEL));
            t.put("date", day.toString());
            t.put("online", online);
            t.put("offline", offline);
            trend.add(t);
        }

        int alarmTotal = 0;
        int unhandled = 0;
        List<Map<String, Object>> byLevel = new ArrayList<>();
        for (Map<String, Object> r : jdbc.queryForList("""
                select coalesce(nullif(trim(level), ''), '提示') as level, count(*) as cnt,
                       sum(case when coalesce(state, '') <> '已处理' then 1 else 0 end) as open_cnt
                  from dc_alarm
                 where not cancelled and occur_at >= ? and occur_at < ?
                 group by coalesce(nullif(trim(level), ''), '提示')
                 order by count(*) desc
                """, Timestamp.valueOf(winStart), Timestamp.valueOf(winEnd))) {
            int cnt = ((Number) r.get("cnt")).intValue();
            alarmTotal += cnt;
            unhandled += ((Number) r.get("open_cnt")).intValue();
            Map<String, Object> lv = new LinkedHashMap<>();
            lv.put("level", str(r.get("level")));
            lv.put("count", cnt);
            byLevel.add(lv);
        }
        Map<String, Object> alarms = new LinkedHashMap<>();
        alarms.put("total", alarmTotal);
        alarms.put("unhandled", unhandled);
        alarms.put("byLevel", byLevel);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("from", fromDate.toString());
        out.put("to", toDate.toString());
        out.put("windowHours", Math.round(winMs / 3600_000.0 * 10) / 10.0);
        out.put("summary", summary);
        out.put("byDevice", byDevice);
        out.put("trend", trend);
        out.put("alarms", alarms);
        return out;
    }

    /** 折叠窗口内的在线/离线时长（毫秒）与状态变化次数；从首次有日志的时刻开始算。 */
    private static long[] foldUsage(List<Map<String, Object>> logs, LocalDateTime from, LocalDateTime to) {
        if (logs == null || logs.isEmpty()) return new long[]{0L, 0L, 0L};
        List<Map<String, Object>> sorted = new ArrayList<>(logs);
        sorted.sort(Comparator.comparing(m -> toLocal(m.get("changed_at"))));
        String prevStatus = null;
        LocalDateTime prevAt = null;
        long online = 0;
        long offline = 0;
        long changes = 0;
        for (Map<String, Object> l : sorted) {
            LocalDateTime at = toLocal(l.get("changed_at"));
            if (at == null || at.isAfter(to)) break;
            String st = str(l.get("status"));
            if (prevStatus != null) {
                long ms = Math.max(0, Duration.between(prevAt, at).toMillis());
                if ("online".equalsIgnoreCase(prevStatus)) online += ms; else offline += ms;
                changes++;
            }
            prevStatus = st;
            prevAt = at;
        }
        if (prevStatus != null && prevAt != null) {
            long ms = Math.max(0, Duration.between(prevAt, to).toMillis());
            if ("online".equalsIgnoreCase(prevStatus)) online += ms; else offline += ms;
        }
        return new long[]{online, offline, changes};
    }

    private static String statusAt(List<Map<String, Object>> logs, LocalDateTime at) {
        String st = "offline";
        LocalDateTime best = null;
        for (Map<String, Object> l : logs) {
            LocalDateTime ts = toLocal(l.get("changed_at"));
            if (ts == null || ts.isAfter(at)) continue;
            if (best == null || ts.isAfter(best)) {
                best = ts;
                st = str(l.get("status"));
            }
        }
        return st;
    }

    // ───────────────────────── 硬件上报 ─────────────────────────

    /**
     * 硬件批量上报：{gatewayCode?, deviceCode, values:[{pointCode|pointId, value|text, quality?, at?}]}。
     * 鉴权 X-DC-Key = 设备所绑网关的 api_key；未绑网关或未配密钥一律拒绝。
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> ingestBatch(Map<String, Object> body, String key) {
        String deviceCode = str(body.get("deviceCode"));
        Map<String, Object> device = requireGatewayKey(deviceCode, key);
        Object valuesRaw = body.get("values");
        if (!(valuesRaw instanceof List<?> values) || values.isEmpty()) {
            throw new IllegalArgumentException("缺少 values（至少一个采集点）");
        }
        List<Map<String, Object>> accepted = new ArrayList<>();
        for (Object item : values) {
            if (!(item instanceof Map<?, ?> m)) continue;
            Map<String, Object> one = new LinkedHashMap<>((Map<String, Object>) m);
            one.put("deviceCode", deviceCode);
            accepted.add(ingestOne(device, one));
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("deviceCode", deviceCode);
        out.put("accepted", accepted.size());
        out.put("items", accepted);
        return out;
    }

    /** 单条上报：写流水 → 刷新在线 → 越限写报警。 */
    public Map<String, Object> ingest(Map<String, Object> body, String key) {
        Map<String, Object> device = requireGatewayKey(str(body.get("deviceCode")), key);
        return ingestOne(device, body);
    }

    private Map<String, Object> ingestOne(Map<String, Object> device, Map<String, Object> body) {
        long deviceId = ((Number) device.get("id")).longValue();
        String pointCode = str(body.get("pointCode"));
        Long pointId = longOrNull(body.get("pointId"));
        Map<String, Object> point;
        if (pointId != null) {
            List<Map<String, Object>> rows = jdbc.queryForList("""
                    select id, code, name, unit, low_limit, high_limit from dc_point
                     where id = ? and device_id = ? and not cancelled
                    """, pointId, deviceId);
            if (rows.isEmpty()) throw new IllegalArgumentException("采集点不属于该设备：" + pointId);
            point = rows.get(0);
        } else if (!pointCode.isBlank()) {
            List<Map<String, Object>> rows = jdbc.queryForList("""
                    select id, code, name, unit, low_limit, high_limit from dc_point
                     where device_id = ? and code = ? and not cancelled
                    """, deviceId, pointCode);
            if (rows.isEmpty()) throw new IllegalArgumentException("采集点不存在：" + pointCode);
            point = rows.get(0);
        } else {
            throw new IllegalArgumentException("缺少采集点（pointCode 或 pointId）");
        }
        BigDecimal valueNum = decOrNull(body.get("value"));
        String valueText = emptyToNull(body.get("text"));
        if (valueNum == null && valueText == null) throw new IllegalArgumentException("缺少采集值（value 或 text）");
        LocalDateTime at = str(body.get("at")).isBlank() ? LocalDateTime.now() : LocalDateTime.parse(str(body.get("at")));
        String quality = str(body.get("quality")).isBlank() ? "G" : str(body.get("quality")).substring(0, 1).toUpperCase();

        jdbc.update("""
                insert into dc_sample (device_id, point_id, value_num, value_text, quality, collect_at)
                values (?,?,?,?,?,?)
                """, deviceId, point.get("id"), valueNum, valueText, quality, Timestamp.valueOf(at));
        touchOnline(deviceId, at, "采集上报");
        boolean over = isOver(point.get("low_limit"), point.get("high_limit"), valueNum);
        if (over) {
            String msg = String.format("%s(%s) 采集值 %s 超出限值 [%s, %s]",
                    point.get("name"), point.get("code"),
                    valueNum == null ? valueText : valueNum.toPlainString(),
                    point.get("low_limit") == null ? "-" : point.get("low_limit"),
                    point.get("high_limit") == null ? "-" : point.get("high_limit"));
            jdbc.update("""
                    insert into dc_alarm (device_id, point_id, level, message, value_text, occur_at, state)
                    values (?,?,?,?,?,?,'未处理')
                    """, deviceId, point.get("id"), "预警", msg,
                    valueNum == null ? valueText : valueNum.toPlainString(), Timestamp.valueOf(at));
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("deviceId", deviceId);
        out.put("pointId", point.get("id"));
        out.put("collectAt", at.toString());
        out.put("overLimit", over);
        return out;
    }

    /** 心跳：刷新在线状态（切换时写状态日志），并顺带刷新网关在线。 */
    public Map<String, Object> heartbeat(Map<String, Object> body, String key) {
        Map<String, Object> device = requireGatewayKey(str(body.get("deviceCode")), key);
        long deviceId = ((Number) device.get("id")).longValue();
        LocalDateTime at = LocalDateTime.now();
        touchOnline(deviceId, at, "心跳");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("deviceId", deviceId);
        out.put("status", "online");
        out.put("at", at.withNano(0).toString());
        return out;
    }

    /** 硬件鉴权：设备必须绑了启用的网关、网关配了密钥，且请求头与之相同。 */
    private Map<String, Object> requireGatewayKey(String deviceCode, String key) {
        if (deviceCode.isBlank()) throw new IllegalArgumentException("缺少 deviceCode");
        List<Map<String, Object>> rows = jdbc.queryForList("""
                select d.id, d.code, coalesce(g.api_key, '') as api_key, g.enabled as gw_enabled,
                       g.cancelled as gw_cancelled
                  from dc_device d
                  left join dc_gateway g on g.id = d.gateway_id
                 where d.code = ? and not d.cancelled
                """, deviceCode);
        if (rows.isEmpty()) throw new IllegalArgumentException("设备不存在：" + deviceCode);
        Map<String, Object> r = rows.get(0);
        String expect = str(r.get("api_key"));
        if (expect.isEmpty()) throw new IllegalArgumentException("设备未绑定网关或网关未配置接入密钥：" + deviceCode);
        if (Boolean.TRUE.equals(r.get("gw_cancelled")) || !Boolean.TRUE.equals(r.get("gw_enabled"))) {
            throw new IllegalArgumentException("设备所绑网关已停用：" + deviceCode);
        }
        if (key == null || key.isBlank() || !expect.equals(key)) throw new IllegalArgumentException("接入密钥不正确");
        return r;
    }

    /** 刷新设备在线状态；状态变化写日志（在线率的历史依据），并同步刷新网关在线。 */
    private void touchOnline(long deviceId, LocalDateTime at, String reason) {
        List<Map<String, Object>> rows = jdbc.queryForList(
                "select coalesce(status, 'offline') as status from dc_device where id = ?", deviceId);
        String prev = rows.isEmpty() ? "offline" : str(rows.get(0).get("status"));
        jdbc.update("""
                update dc_device set status = 'online', last_seen_at = ?, last_debug_at = ?, update_time = now()
                 where id = ?
                """, Timestamp.valueOf(at), Timestamp.valueOf(at), deviceId);
        if (!"online".equalsIgnoreCase(prev)) {
            jdbc.update("""
                    insert into dc_status_log (device_id, status, changed_at, reason) values (?,?,?,?)
                    """, deviceId, "online", Timestamp.valueOf(at), reason);
        }
        jdbc.update("""
                update dc_gateway set status = 'online', last_seen_at = ?, update_time = now()
                 where id = (select gateway_id from dc_device where id = ?) and not cancelled
                """, Timestamp.valueOf(at), deviceId);
    }

    // ───────────────────────── 判定与格式工具 ─────────────────────────

    /** 在线 = 最近上报时间 + 心跳窗口之内；停用或没上报过一律离线。 */
    static boolean isOnline(Map<String, Object> row) {
        if (row == null || !Boolean.TRUE.equals(row.get("enabled"))) return false;
        LocalDateTime last = toLocal(row.get("last_seen_at"));
        if (last == null) return false;
        return !last.plusSeconds(offlineWindow(row)).isBefore(LocalDateTime.now());
    }

    private static String effectiveStatus(Map<String, Object> row) {
        if (row == null || !Boolean.TRUE.equals(row.get("enabled"))) return "disabled";
        return isOnline(row) ? "online" : "offline";
    }

    /** 离线窗口 = 心跳周期 × 3，且不小于 180 秒。 */
    static long offlineWindow(Map<String, Object> row) {
        int beat = row == null ? 60 : num(row.get("heartbeat_seconds"));
        if (beat <= 0) beat = 60;
        return Math.max((long) beat * OFFLINE_MULTIPLIER, OFFLINE_MIN_SECONDS);
    }

    /** 越限：上下限都可空；只有数值型且任一边限存在时才判。 */
    static boolean isOver(Object low, Object high, Object value) {
        if (!(value instanceof Number)) return false;
        double v = ((Number) value).doubleValue();
        if (low instanceof Number l && v < l.doubleValue()) return true;
        return high instanceof Number h && v > h.doubleValue();
    }

    static double round1(double v) {
        return BigDecimal.valueOf(v).setScale(1, RoundingMode.HALF_UP).doubleValue();
    }

    private static LocalDate parseDate(String raw, LocalDate fallback) {
        try {
            return raw == null || raw.isBlank() ? fallback : LocalDate.parse(raw.trim());
        } catch (Exception e) {
            return fallback;
        }
    }

    private static String wallClock(Object value) {
        LocalDateTime at = toLocal(value);
        return at == null ? null : at.format(WALL);
    }

    private static LocalDateTime toLocal(Object value) {
        if (value == null) return null;
        if (value instanceof LocalDateTime ldt) return ldt;
        if (value instanceof Timestamp ts) return ts.toLocalDateTime();
        if (value instanceof java.util.Date d) return new Timestamp(d.getTime()).toLocalDateTime();
        return null;
    }

    private static String plain(Object value) {
        if (value == null) return "";
        if (value instanceof BigDecimal bd) return bd.stripTrailingZeros().toPlainString();
        return String.valueOf(value);
    }

    static int num(Object value) {
        if (value instanceof Number n) return n.intValue();
        try {
            return value == null ? 0 : Integer.parseInt(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    static String str(Object value) {
        return value == null ? "" : String.valueOf(value);
    }

    private static String emptyToNull(Object value) {
        String s = str(value).trim();
        return s.isEmpty() ? null : s;
    }

    static Long longOrNull(Object value) {
        if (value instanceof Number n) return n.longValue();
        try {
            String s = str(value).trim();
            return s.isEmpty() ? null : Long.valueOf(s);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static Integer intOrNull(Object value) {
        Long l = longOrNull(value);
        return l == null ? null : l.intValue();
    }

    private static BigDecimal decOrNull(Object value) {
        if (value instanceof BigDecimal bd) return bd;
        if (value instanceof Number n) return new BigDecimal(n.toString());
        String s = str(value).trim();
        if (s.isEmpty()) return null;
        try {
            return new BigDecimal(s);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** 供测试与日志使用：把设备 id 之外的对象判等（Map 里是 Long/Integer 混用时） */
    static boolean sameDevice(Object a, Object b) {
        return Objects.equals(longOrNull(a), longOrNull(b));
    }
}
