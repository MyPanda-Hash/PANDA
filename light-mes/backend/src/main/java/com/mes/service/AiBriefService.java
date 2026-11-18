package com.mes.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 我的桌面 → AI 助理卡片取数。
 *
 * <p>为什么由后端代理而不是前端直连：AI 后端与 MES 不同源，登录态在对方站点的
 * Cookie/令牌里，浏览器带跨源凭据会被 CORS 与 SameSite 挡下。这里走
 * 「MES 签票据 → AI 换成 access_token → 服务端取数」，浏览器只跟 MES 说话。
 *
 * <p>失败一律降级成 available=false：桌面是首屏，AI 侧挂了不能把整页拖白。
 */
@Service
public class AiBriefService {

    private static final Duration TOKEN_TTL = Duration.ofMinutes(4);

    private final ObjectMapper objectMapper;
    private final AiSsoService ssoService;
    private final String apiBaseUrl;
    private final HttpClient http;
    /** 账号 → (access_token, 过期时刻)，避免每次看桌面都换一次票。 */
    private final Map<String, CachedToken> tokens = new ConcurrentHashMap<>();

    private record CachedToken(String token, Instant expiresAt) {
    }

    public AiBriefService(ObjectMapper objectMapper,
                          AiSsoService ssoService,
                          @Value("${mes.ai.api-base-url:http://localhost:8000}") String apiBaseUrl) {
        this.objectMapper = objectMapper;
        this.ssoService = ssoService;
        this.apiBaseUrl = (apiBaseUrl == null ? "" : apiBaseUrl.trim()).replaceAll("/+$", "");
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    }

    public boolean enabled() {
        return ssoService.enabled() && !apiBaseUrl.isEmpty();
    }

    /** 取当前账号的今日简报摘要；任何失败都返回 available=false 而不是抛错。 */
    public Map<String, Object> brief(String userName, String realName, boolean admin) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("available", false);
        if (!enabled()) {
            out.put("reason", "未配置 AI 助理地址或 SSO 密钥");
            return out;
        }
        try {
            String token = tokenFor(userName, realName, admin);
            String role = admin ? "manager" : "supervisor";
            JsonNode payload = getJson("/assistant/briefing/board?role=" + role, token);
            JsonNode data = payload.path("data");
            if (data.isMissingNode() || data.isNull()) {
                out.put("reason", "AI 助理没有返回简报数据");
                return out;
            }
            out.put("available", true);
            out.put("headline", data.path("headline").asText(""));
            out.put("hidden", data.path("hidden").asInt(0));
            List<Map<String, Object>> stats = new ArrayList<>();
            for (JsonNode s : data.path("stats")) {
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("label", s.path("label").asText(""));
                item.put("value", s.path("value").asText(""));
                item.put("tone", s.path("tone").asText(""));
                item.put("hint", s.path("hint").asText(""));
                stats.add(item);
            }
            out.put("stats", stats);
            List<Map<String, Object>> decisions = new ArrayList<>();
            for (JsonNode d : data.path("decisions")) {
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("title", d.path("title").asText(""));
                item.put("reason", d.path("reason").asText(""));
                item.put("advice", d.path("advice").asText(""));
                decisions.add(item);
            }
            out.put("decisions", decisions);
            return out;
        } catch (Exception exc) {
            // 桌面主动写日志，方便排查「卡片为什么空了」
            System.out.println("[ai-brief] " + userName + " 取简报失败：" + exc.getMessage());
            out.put("reason", exc.getMessage() == null ? exc.getClass().getSimpleName() : exc.getMessage());
            return out;
        }
    }

    /**
     * 看板 AI 分析：把界面上的指标快照交给 AI 助理，换回「增长点 + 建议」。
     *
     * <p>分析对象就是用户屏幕上那份数，所以指标由前端传入而不是后端重算。
     * 任何失败都降级成 available=false，看板只显示提示，不阻塞。
     */
    public Map<String, Object> analysis(String userName, String realName, boolean admin,
                                        Map<String, Object> metrics) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("available", false);
        if (!enabled()) {
            out.put("reason", "未配置 AI 助理地址或 SSO 密钥");
            return out;
        }
        try {
            String token = tokenFor(userName, realName, admin);
            String body = objectMapper.writeValueAsString(Map.of("metrics", metrics == null ? Map.of() : metrics));
            HttpRequest request = HttpRequest.newBuilder(URI.create(apiBaseUrl + "/assistant/briefing/analysis"))
                    .timeout(Duration.ofSeconds(75))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + token)
                    .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                    .build();
            HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if (response.statusCode() >= 400) {
                throw new IllegalStateException("AI 助理分析返回 " + response.statusCode());
            }
            JsonNode data = objectMapper.readTree(response.body()).path("data");
            out.put("available", data.path("available").asBoolean(false));
            out.put("reason", data.path("reason").asText(""));
            out.put("summary", data.path("summary").asText(""));
            List<String> growth = new ArrayList<>();
            for (JsonNode n : data.path("growth")) {
                growth.add(n.asText());
            }
            out.put("growth", growth);
            List<String> advice = new ArrayList<>();
            for (JsonNode n : data.path("advice")) {
                advice.add(n.asText());
            }
            out.put("advice", advice);
            return out;
        } catch (Exception exc) {
            System.out.println("[ai-analysis] " + userName + " 分析失败：" + exc.getMessage());
            out.put("reason", exc.getMessage() == null ? exc.getClass().getSimpleName() : exc.getMessage());
            return out;
        }
    }

    private String tokenFor(String userName, String realName, boolean admin) throws Exception {
        CachedToken cached = tokens.get(userName);
        if (cached != null && cached.expiresAt().isAfter(Instant.now())) {
            return cached.token();
        }
        String ticket = ssoService.issueTicket(userName, realName, admin, Instant.now());
        String body = objectMapper.writeValueAsString(Map.of("ticket", ticket));
        HttpRequest request = HttpRequest.newBuilder(URI.create(apiBaseUrl + "/auth/mes-sso-token"))
                .timeout(Duration.ofSeconds(5))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body, StandardCharsets.UTF_8))
                .build();
        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (response.statusCode() >= 400) {
            throw new IllegalStateException("AI 助理换票失败：" + response.statusCode());
        }
        JsonNode json = objectMapper.readTree(response.body());
        String token = json.path("access_token").asText("");
        if (token.isEmpty()) {
            throw new IllegalStateException("AI 助理未返回 access_token");
        }
        tokens.put(userName, new CachedToken(token, Instant.now().plus(TOKEN_TTL)));
        return token;
    }

    private JsonNode getJson(String path, String token) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create(apiBaseUrl + path))
                .timeout(Duration.ofSeconds(6))
                .header("Authorization", "Bearer " + token)
                .GET()
                .build();
        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
        if (response.statusCode() >= 400) {
            throw new IllegalStateException("AI 助理接口返回 " + response.statusCode());
        }
        return objectMapper.readTree(response.body());
    }
}
