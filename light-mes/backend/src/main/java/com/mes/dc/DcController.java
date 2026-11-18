package com.mes.dc;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RestController;

import com.mes.dto.ApiResult;
import com.mes.service.RoleService;

import jakarta.servlet.http.HttpServletRequest;

/**
 * 数采平台接口（dc_* 七张表）。
 *
 * <p>三块能力：
 * <ol>
 *   <li><b>IOT 设备配置</b>：{@code GET /config} + 网关/设备/采集点/规则的读写；</li>
 *   <li><b>数据监控</b>：{@code GET /monitor} + 报警处理；</li>
 *   <li><b>状态统计</b>：{@code GET /stats}。</li>
 * </ol>
 *
 * <p><b>权限</b>：读接口登录即可；写接口仅管理员（与源系统一致：「改系统参数」不是现场动作）。
 * 前端另外按 {@code user.isAdmin} 隐藏写按钮——那只是减少误点，真正的闸门在这里。
 *
 * <p><b>硬件侧</b>：{@code POST /ingest}、{@code POST|GET /ingest/text}、{@code POST /heartbeat}
 * 在 SecurityConfig 里 permitAll（电子秤/网关没有登录账号），鉴权靠请求头
 * {@code X-DC-Key} = 设备所绑网关的 api_key。
 *
 * <p><b>数据真实性</b>：没有硬件上报就是「离线 / 无数据 / 待配置」，接口不编造演示数字。
 */
@RestController
@RequestMapping("/api/px/dc")
public class DcController {

    private static final Logger log = LoggerFactory.getLogger(DcController.class);

    /** 文本口节流：秤连续发送时 5~10Hz，不节流会把流水刷爆（现场口径「1 秒抓一次」） */
    private static final Map<String, Long> LAST_TEXT_WRITE = new ConcurrentHashMap<>();
    private static final long TEXT_MIN_INTERVAL_MS = 1000L;
    /** 空秤阈值（kg）：≤ 这个值不记流水，但刷新一次心跳，避免「空秤」被判离线 */
    private static final double TEXT_MIN_WEIGHT_KG = 0.005;

    private final DcService dc;
    private final RoleService roleService;

    public DcController(DcService dc, RoleService roleService) {
        this.dc = dc;
        this.roleService = roleService;
    }

    // ───────────────────────── ① IOT 设备配置 ─────────────────────────

    @GetMapping("/config")
    public ApiResult<Map<String, Object>> config() {
        return ApiResult.ok(dc.config());
    }

    @PostMapping("/gateway/save")
    public ApiResult<Map<String, Object>> saveGateway(@RequestBody(required = false) Map<String, Object> body,
                                                      Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.saveGateway(body == null ? Map.of() : body, auth.getName()));
    }

    @PostMapping("/gateway/delete")
    public ApiResult<Map<String, Object>> deleteGateway(@RequestBody Map<String, Object> body, Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.deleteGateway(requireId(body), auth.getName()));
    }

    @PostMapping("/device/save")
    public ApiResult<Map<String, Object>> saveDevice(@RequestBody(required = false) Map<String, Object> body,
                                                     Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.saveDevice(body == null ? Map.of() : body, auth.getName()));
    }

    @PostMapping("/device/delete")
    public ApiResult<Map<String, Object>> deleteDevice(@RequestBody Map<String, Object> body, Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.deleteDevice(requireId(body), auth.getName()));
    }

    @PostMapping("/device/debug")
    public ApiResult<Map<String, Object>> debugDevice(@RequestBody Map<String, Object> body, Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.markDebug(requireId(body), auth.getName()));
    }

    @GetMapping("/points")
    public ApiResult<List<Map<String, Object>>> points(@RequestParam(required = false) Long deviceId) {
        return ApiResult.ok(dc.points(deviceId));
    }

    @PostMapping("/point/save")
    public ApiResult<Map<String, Object>> savePoint(@RequestBody(required = false) Map<String, Object> body,
                                                    Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.savePoint(body == null ? Map.of() : body, auth.getName()));
    }

    @PostMapping("/point/delete")
    public ApiResult<Integer> deletePoint(@RequestBody Map<String, Object> body, Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.deletePoint(requireId(body), auth.getName()));
    }

    @GetMapping("/rules")
    public ApiResult<List<Map<String, Object>>> rules(@RequestParam(required = false) Long deviceId) {
        return ApiResult.ok(dc.rules(deviceId));
    }

    @PostMapping("/rule/save")
    public ApiResult<Map<String, Object>> saveRule(@RequestBody(required = false) Map<String, Object> body,
                                                   Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.saveRule(body == null ? Map.of() : body, auth.getName()));
    }

    @PostMapping("/rule/delete")
    public ApiResult<Integer> deleteRule(@RequestBody Map<String, Object> body, Authentication auth) {
        requireAdmin(auth);
        return ApiResult.ok(dc.deleteRule(requireId(body), auth.getName()));
    }

    // ───────────────────────── ② 数据监控 ─────────────────────────

    @GetMapping("/monitor")
    public ApiResult<Map<String, Object>> monitor(@RequestParam(required = false) Long deviceId) {
        return ApiResult.ok(dc.monitor(deviceId));
    }

    @GetMapping("/alarms")
    public ApiResult<Map<String, Object>> alarms(@RequestParam(required = false) String state,
                                                 @RequestParam(required = false) String level,
                                                 @RequestParam(required = false) Long deviceId,
                                                 @RequestParam(defaultValue = "1") int pageNo,
                                                 @RequestParam(defaultValue = "50") int pageSize) {
        return ApiResult.ok(dc.alarms(state, level, deviceId, pageNo, pageSize));
    }

    /** 处理报警：车间任何人都能认领，只要求登录。 */
    @PostMapping("/alarm/handle")
    public ApiResult<Map<String, Object>> handleAlarm(@RequestBody Map<String, Object> body, Authentication auth) {
        Long id = requireId(body);
        Object note = body.get("note");
        dc.handleAlarm(id, auth == null ? "system" : auth.getName(), note == null ? "" : String.valueOf(note));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", id);
        out.put("state", "已处理");
        return ApiResult.ok(out);
    }

    // ───────────────────────── ③ 状态统计 ─────────────────────────

    @GetMapping("/stats")
    public ApiResult<Map<String, Object>> stats(@RequestParam(required = false) String from,
                                                @RequestParam(required = false) String to) {
        return ApiResult.ok(dc.stats(from, to));
    }

    // ───────────────────────── 硬件侧接入（免登录，凭 X-DC-Key） ─────────────────────────

    /** JSON 批量上报：{deviceCode, values:[{pointCode|pointId, value|text}]}。 */
    @PostMapping("/ingest")
    public ApiResult<Map<String, Object>> ingest(@RequestBody(required = false) Map<String, Object> body,
                                                 @RequestHeader(value = "X-DC-Key", required = false) String key) {
        return ApiResult.ok(dc.ingestBatch(body == null ? Map.of() : body, key));
    }

    /**
     * 文本上报口：给只能把串口原始帧发出来的透传模块用（W111 等），
     * 取数位置依次为 query 的 data/v、POST 正文、整个查询串。
     */
    @RequestMapping(value = "/ingest/text", method = {RequestMethod.GET, RequestMethod.POST})
    public ApiResult<Map<String, Object>> ingestText(@RequestParam(required = false) String device,
                                                     @RequestParam(required = false) String deviceCode,
                                                     @RequestParam(required = false) String data,
                                                     @RequestParam(required = false) String v,
                                                     @RequestParam(required = false) String value,
                                                     @RequestParam(required = false) String point,
                                                     @RequestParam(required = false) String pointCode,
                                                     HttpServletRequest request) {
        String dev = firstNonBlank(device, deviceCode, request.getHeader("X-DC-Device"));
        if (dev.isBlank()) dev = "DZ-001";
        String raw = firstNonBlank(data, v, value);
        if (raw.isBlank()) {
            try {
                raw = new String(request.getInputStream().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
            } catch (Exception ignored) {
                // 读不到正文就退回请求行
            }
        }
        if (raw.isBlank()) raw = request.getQueryString() == null ? "" : request.getQueryString();
        if (raw.isBlank()) raw = request.getRequestURI();
        Double kg = parseWeightText(raw);
        if (kg == null) throw new IllegalArgumentException("报文里没解析出重量：" + raw);
        String key = request.getHeader("X-DC-Key");
        String pt = firstNonBlank(pointCode, point);
        if (pt.isBlank()) pt = "W01";
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("deviceCode", dev);
        out.put("weight", kg);
        out.put("raw", raw.length() > 120 ? raw.substring(0, 120) : raw);
        if (Math.abs(kg) <= TEXT_MIN_WEIGHT_KG) {
            try {
                dc.heartbeat(Map.of("deviceCode", dev), key);
            } catch (RuntimeException ignored) {
                // 空秤但密钥错时不吞真实错误：下面 skipped 里没有 result，调用方看得出没落库
            }
            out.put("skipped", "空重量(≤" + TEXT_MIN_WEIGHT_KG + "kg)不记流水");
            return ApiResult.ok(out);
        }
        long nowMs = System.currentTimeMillis();
        Long prev = LAST_TEXT_WRITE.get(dev);
        if (prev != null && nowMs - prev < TEXT_MIN_INTERVAL_MS) {
            out.put("skipped", "距上次写入不足 " + TEXT_MIN_INTERVAL_MS + "ms，丢弃该帧");
            return ApiResult.ok(out);
        }
        LAST_TEXT_WRITE.put(dev, nowMs);
        Map<String, Object> one = new LinkedHashMap<>();
        one.put("deviceCode", dev);
        one.put("pointCode", pt);
        one.put("value", kg);
        out.put("result", dc.ingest(one, key));
        return ApiResult.ok(out);
    }

    /** 心跳：{deviceCode}；在线/离线切换写状态日志。 */
    @PostMapping("/heartbeat")
    public ApiResult<Map<String, Object>> heartbeat(@RequestBody(required = false) Map<String, Object> body,
                                                    @RequestHeader(value = "X-DC-Key", required = false) String key) {
        return ApiResult.ok(dc.heartbeat(body == null ? Map.of() : body, key));
    }

    // ───────────────────────── 工具 ─────────────────────────

    private void requireAdmin(Authentication auth) {
        if (auth == null || !roleService.isAdminUser(auth.getName())) {
            throw new AccessDeniedException("仅管理员可维护数采配置");
        }
    }

    private static Long requireId(Map<String, Object> body) {
        Long id = DcService.longOrNull(body == null ? null : body.get("id"));
        if (id == null) throw new IllegalArgumentException("缺少 id");
        return id;
    }

    private static String firstNonBlank(String... values) {
        for (String v : values) if (v != null && !v.isBlank()) return v.trim();
        return "";
    }

    /**
     * 串口帧里的重量：先按现场格式「净重[皮重]kg」（如 {@code +000.39000.00kg}），
     * 再退回第一个带小数的数，最后退到末尾整数。
     */
    static Double parseWeightText(String raw) {
        String s = raw == null ? "" : raw.replace('\r', ' ').replace('\n', ' ').trim();
        Matcher exact = Pattern.compile("([+-]?\\d{1,4}\\.\\d{1,2})(?:[+-]?\\d{1,4}\\.\\d{1,2})?\\s*(?:kg|KG|g|G)?").matcher(s);
        if (exact.find()) {
            try {
                return Double.parseDouble(exact.group(1));
            } catch (NumberFormatException ignored) {
                // 继续兜底
            }
        }
        Matcher any = Pattern.compile("(-?\\d{1,4}\\.\\d{1,3})").matcher(s);
        if (any.find()) {
            try {
                return Double.parseDouble(any.group(1));
            } catch (NumberFormatException ignored) {
                // 继续兜底
            }
        }
        Matcher whole = Pattern.compile("(-?\\d{1,4})\\s*(?:kg|KG|g|G)?\\s*$").matcher(s);
        if (whole.find()) {
            try {
                return Double.parseDouble(whole.group(1));
            } catch (NumberFormatException ignored) {
                // 无解
            }
        }
        log.debug("dc.ingest.text 未解析出重量：{}", s.length() > 80 ? s.substring(0, 80) : s);
        return null;
    }
}
