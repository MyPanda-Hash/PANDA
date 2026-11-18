package com.mes.openapi;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * 开放接口的门禁：令牌 + IP 白名单 + 限流。
 *
 * <p>设计取舍：
 * <ul>
 *   <li><b>失败即拒绝</b>：没配令牌就整体关闭，绝不"空令牌放行"。</li>
 *   <li><b>常量时间比较</b>：令牌比对用 {@link MessageDigest#isEqual}，避免逐字符提前返回泄露长度信息。</li>
 *   <li><b>IP 白名单</b>：默认只允许回环，AI 与 MES 同机部署时不需要放开任何网段。</li>
 *   <li><b>限流</b>：按令牌维度每分钟计数，防止被当成批量爬取工具。</li>
 * </ul>
 *
 * <p>本类只做"能不能进"，不碰业务数据；业务查询在 {@link OpenApprovalService}。
 */
@Component
public class OpenApiGuard {

    private static final Logger log = LoggerFactory.getLogger(OpenApiGuard.class);
    public static final String HEADER = "X-Open-Api-Token";

    public static String headerName() {
        return HEADER;
    }

    private final boolean enabled;
    private final byte[] token;
    private final Set<String> allowedIps;
    private final int rateLimitPerMinute;
    private final Map<String, long[]> windows = new ConcurrentHashMap<>();

    public OpenApiGuard(@Value("${mes.open-api.enabled:false}") boolean enabled,
                        @Value("${mes.open-api.token:}") String token,
                        @Value("${mes.open-api.allowed-ips:127.0.0.1,::1,0:0:0:0:0:0:0:1}") String allowedIps,
                        @Value("${mes.open-api.rate-limit-per-minute:120}") int rateLimitPerMinute) {
        this.enabled = enabled;
        this.token = token == null ? new byte[0] : token.trim().getBytes(StandardCharsets.UTF_8);
        Set<String> ips = new LinkedHashSet<>();
        if (allowedIps != null) {
            Arrays.stream(allowedIps.split(","))
                    .map(String::trim)
                    .filter(item -> !item.isEmpty())
                    .forEach(ips::add);
        }
        this.allowedIps = Set.copyOf(ips);
        this.rateLimitPerMinute = Math.max(1, rateLimitPerMinute);
    }

    /** 校验一次请求；不通过抛 {@link OpenApiException}。 */
    public void check(String providedToken, String remoteIp) {
        if (!enabled) throw new OpenApiException(404, "开放接口未启用");
        if (token.length == 0) throw new OpenApiException(503, "开放接口未配置令牌");

        if (!ipAllowed(remoteIp)) {
            log.warn("open-api rejected by ip: {}", remoteIp);
            throw new OpenApiException(403, "来源地址不允许访问");
        }
        if (providedToken == null || providedToken.isBlank()) {
            throw new OpenApiException(401, "缺少访问令牌");
        }
        byte[] provided = providedToken.trim().getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(provided, token)) {
            log.warn("open-api rejected by token from {}", remoteIp);
            throw new OpenApiException(401, "访问令牌无效");
        }
        if (!acquire(providedToken.trim())) {
            throw new OpenApiException(429, "请求过于频繁");
        }
    }

    boolean ipAllowed(String remoteIp) {
        if (remoteIp == null || remoteIp.isBlank()) return false;
        String ip = remoteIp.trim();
        int colon = ip.lastIndexOf(':');
        // IPv4:port 形式去掉端口；IPv6 里裸 ':' 很多，只有出现 [..]:port 才处理
        if (ip.startsWith("[") && ip.contains("]")) {
            ip = ip.substring(1, ip.indexOf(']'));
        } else if (colon > 0 && ip.chars().filter(ch -> ch == ':').count() == 1) {
            ip = ip.substring(0, colon);
        }
        return allowedIps.contains(ip);
    }

    private boolean acquire(String key) {
        long now = System.currentTimeMillis();
        long[] window = windows.computeIfAbsent(key, ignored -> new long[]{now, 0});
        synchronized (window) {
            if (now - window[0] >= 60_000L) {
                window[0] = now;
                window[1] = 0;
            }
            if (window[1] >= rateLimitPerMinute) return false;
            window[1]++;
            return true;
        }
    }

    public List<String> describeAllowedIps() {
        return new ArrayList<>(allowedIps);
    }
}
