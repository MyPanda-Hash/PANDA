package com.mes.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * AI 数字员工单点登录票据。
 *
 * <p>协议与 doc-nexus-ai 的 {@code app/services/mes_sso.py} 一一对应：
 * <pre>
 * ticket = base64url(payload_json) + "." + base64url(hmac_sha256(secret, base64url(payload_json)))
 * payload = {"sub","name","role","admin","iat","exp","jti"}
 * </pre>
 * 两端共用同一密钥（MES_AI_SSO_SECRET / DOC_NEXUS_MES_SSO_SECRET），留空即关闭入口。
 */
@Service
public class AiSsoService {

    private static final Base64.Encoder B64 = Base64.getUrlEncoder().withoutPadding();

    private final ObjectMapper objectMapper;
    private final String secret;
    private final String aiBaseUrl;
    private final long ttlSeconds;

    public AiSsoService(ObjectMapper objectMapper,
                        @Value("${mes.ai.sso.secret:}") String secret,
                        @Value("${mes.ai.sso.base-url:http://localhost:18091}") String aiBaseUrl,
                        @Value("${mes.ai.sso.ticket-ttl-seconds:60}") long ttlSeconds) {
        this.objectMapper = objectMapper;
        this.secret = secret == null ? "" : secret.trim();
        this.aiBaseUrl = aiBaseUrl == null ? "" : aiBaseUrl.trim();
        this.ttlSeconds = ttlSeconds <= 0 ? 60 : ttlSeconds;
    }

    /** 未配置密钥时入口关闭，前端回落到「只打开对话页」。 */
    public boolean enabled() {
        return !secret.isEmpty() && !aiBaseUrl.isEmpty();
    }

    /** 签发 60 秒票据：账号 + 姓名 + 角色 + 管理员标记。 */
    public String issueTicket(String userName, String realName, boolean admin, Instant now) {
        if (!enabled()) {
            throw new IllegalStateException("未配置 MES_AI_SSO_SECRET，AI 单点登录不可用");
        }
        if (userName == null || userName.isBlank()) {
            throw new IllegalArgumentException("缺少账号");
        }
        long issuedAt = now.getEpochSecond();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("sub", userName);
        payload.put("name", realName == null || realName.isBlank() ? userName : realName);
        payload.put("role", admin ? "admin" : "user");
        payload.put("admin", admin);
        payload.put("iat", issuedAt);
        payload.put("exp", issuedAt + ttlSeconds);
        payload.put("jti", UUID.randomUUID().toString().replace("-", ""));

        final byte[] json;
        try {
            json = objectMapper.writeValueAsBytes(payload);
        } catch (Exception e) {
            throw new IllegalStateException("票据序列化失败", e);
        }
        String body = B64.encodeToString(json);
        return body + "." + B64.encodeToString(hmacSha256(body));
    }

    /** AI 服务的免密入口地址：带上票据跳转，由对方建会话后 302 到对话页。 */
    public String ssoUrl(String ticket) {
        String base = aiBaseUrl.endsWith("/") ? aiBaseUrl.substring(0, aiBaseUrl.length() - 1) : aiBaseUrl;
        return base + "/api/auth/mes-sso?ticket=" + URLEncoder.encode(ticket, StandardCharsets.UTF_8);
    }

    private byte[] hmacSha256(String body) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
            return mac.doFinal(body.getBytes(StandardCharsets.US_ASCII));
        } catch (Exception e) {
            throw new IllegalStateException("票据签名失败", e);
        }
    }
}
