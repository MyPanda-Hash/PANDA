package com.mes.service;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * 数字员工的通知客户端：把 AI 派发的待办与群聊消息取回 MES 顶栏。
 *
 * <p>三个原则：
 * <ul>
 *   <li><b>失败不影响 MES</b>——AI 没起或没绑定账号时返回 null，通知中心只用 MES 自己的数据；</li>
 *   <li><b>失败要缓存</b>——AI 挂掉时不能让每次点开通知都等超时，失败结果缓存 30 秒；</li>
 *   <li><b>只读</b>——不替人完成待办、不发消息。</li>
 * </ul>
 */
@Service
public class AiNoticeClient {

    private static final Logger log = LoggerFactory.getLogger(AiNoticeClient.class);
    private static final long FAILURE_TTL_MS = 30_000;
    private static final java.time.format.DateTimeFormatter SINCE_FORMAT =
            java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final RestTemplate rest;
    private final ObjectMapper json;
    private final String baseUrl;
    private final String token;

    /** 群聊页地址：消息通知跳过去时用 */
    private final String webUrl;

    private volatile long failedUntil = 0;

    public AiNoticeClient(RestTemplateBuilder builder, ObjectMapper json,
                          // 用环境变量名而不是 mes.ai.* 前缀：MES_AI_BASE_URL 已被 SSO 入口占用
                          // （值是 AI 门户 18091），再用 mes.ai.base-url 会串味
                          @Value("${MES_AI_NOTICE_URL:http://127.0.0.1:8000}") String baseUrl,
                          @Value("${mes.ai.internal-token:${MES_INIT_AI_TOKEN:}}") String token,
                          @Value("${MES_AI_GROUP_URL:http://localhost:18091}") String webUrl) {
        this.rest = builder.setConnectTimeout(Duration.ofSeconds(2))
                .setReadTimeout(Duration.ofSeconds(4)).build();
        this.json = json;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.token = token == null ? "" : token.trim();
        this.webUrl = webUrl.replaceAll("/+$", "");
        if (token == null || token.isBlank()) {
            log.info("数字员工通知未启用（未配置 MES_INIT_AI_TOKEN）：顶栏只显示 MES 自己的待办与消息");
        } else {
            log.info("数字员工通知已启用：{}（群聊跳转 {}）", this.baseUrl, this.webUrl);
        }
    }

    /** 群聊深链：点消息直接进对应群。 */
    public String groupUrl(String groupId) {
        if (groupId == null || groupId.isBlank()) return webUrl + "/group";
        return webUrl + "/group?group=" + groupId;
    }

    public boolean enabled() {
        return !token.isEmpty();
    }

    /**
     * 取某个 MES 账号在数字员工侧的待办与消息。
     *
     * @return 为 null 表示不可用（未配置令牌 / AI 未启动 / 该账号还没绑定）
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> fetch(String mesUserName) {
        return fetch(mesUserName, null);
    }

    /**
     * 取通知；`since` 是 MES 侧的已读游标（本地墙钟时间），只回它之后的消息。
     */
    public Map<String, Object> fetch(String mesUserName, java.time.LocalDateTime since) {
        if (!enabled() || mesUserName == null || mesUserName.isBlank()) return null;
        if (System.currentTimeMillis() < failedUntil) return null;
        try {
            HttpHeaders headers = new HttpHeaders();
            headers.set("X-Nexus-Internal-Token", token);
            String url = baseUrl + "/internal/v1/mes/notifications?mes_user={u}";
            Object[] args = new Object[] { mesUserName };
            if (since != null) {
                url += "&since={s}";
                args = new Object[] { mesUserName, since.format(SINCE_FORMAT) };
            }
            ResponseEntity<String> response = rest.exchange(
                    url, HttpMethod.GET, new HttpEntity<>(headers), String.class, args);
            String body = response.getBody();
            if (body == null || body.isBlank()) return null;
            Map<String, Object> parsed = json.readValue(body, Map.class);
            if (!Boolean.TRUE.equals(parsed.get("bound"))) return null;
            return parsed;
        } catch (Exception e) {
            failedUntil = System.currentTimeMillis() + FAILURE_TTL_MS;
            log.debug("数字员工通知不可用（{} 秒内不再重试）：{}", FAILURE_TTL_MS / 1000, e.getMessage());
            return null;
        }
    }

    public List<Map<String, Object>> todos(Map<String, Object> payload) {
        return rows(payload, "todos");
    }

    public List<Map<String, Object>> messages(Map<String, Object> payload) {
        return rows(payload, "messages");
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> rows(Map<String, Object> payload, String key) {
        Object value = payload == null ? null : payload.get(key);
        if (!(value instanceof List<?> list)) return List.of();
        List<Map<String, Object>> out = new java.util.ArrayList<>();
        for (Object row : list) {
            if (row instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
        }
        return out;
    }

    /** 把待办里的单据引用拼成 MES 的跳转路径；没有引用就返回 null。 */
    public String targetPath(Map<String, Object> todo) {
        Object refs = todo.get("refs");
        if (!(refs instanceof List<?> list) || list.isEmpty()) return null;
        Object first = list.get(0);
        if (!(first instanceof Map<?, ?> ref)) return null;
        String docNo = String.valueOf(ref.get("docNo") == null ? "" : ref.get("docNo")).trim();
        String panel = String.valueOf(ref.get("panel") == null ? "" : ref.get("panel")).trim();
        if (docNo.isEmpty()) return null;
        if (panel.isEmpty()) panel = DocNoRules.panelOfFormNo(docNo);
        // 仍然认不出面板就不要乱跳（宁可让用户去群聊看上下文），避免把采购单送进销售列表
        if (panel.isEmpty()) return null;
        return "/panelx/form/" + panel + "?focus=" + docNo;
    }

    /** 供前端直接使用的元信息（不外泄令牌）。 */
    public Map<String, Object> describe() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("enabled", enabled());
        out.put("baseUrl", baseUrl);
        out.put("webUrl", webUrl);
        return out;
    }
}