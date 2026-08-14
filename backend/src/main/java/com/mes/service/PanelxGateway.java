package com.mes.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.config.PanelxProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientResponseException;
import org.springframework.web.util.UriComponentsBuilder;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

/**
 * PanelX 平台 HTTP 网关。
 * 后端持有平台演示凭据，自动登录换取 JWT 并缓存（平台 token 有效期 30 天）；
 * HTTP 401 时自动重新登录并重试一次。
 * 平台业务失败（HTTP 200 但 state!=200）原样透传，由前端引擎按封装判断。
 */
@Service
public class PanelxGateway {

    private static final Logger log = LoggerFactory.getLogger(PanelxGateway.class);
    private static final ParameterizedTypeReference<Map<String, Object>> MAP_REF =
            new ParameterizedTypeReference<Map<String, Object>>() {};

    private final RestClient client;
    private final PanelxProperties props;
    private final ObjectMapper mapper;
    private final AtomicReference<String> token = new AtomicReference<>();

    public PanelxGateway(RestClient panelxRestClient, PanelxProperties props, ObjectMapper mapper) {
        this.client = panelxRestClient;
        this.props = props;
        this.mapper = mapper;
    }

    /** 本地面板码 → 平台面板码（mes.panelx.panel-map 未配置则透传） */
    public String resolveCode(String panelCode) {
        return props.getPanelMap().getOrDefault(panelCode, panelCode);
    }

    /** 登录平台换取 JWT 并缓存 */
    public synchronized String login() {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("userName", props.getUsername());
        body.put("password", props.getPassword());
        Map<String, Object> res = execute(() -> client.post()
                .uri("wp-core/api/user/login")
                .contentType(MediaType.APPLICATION_JSON)
                .body(body)
                .retrieve()
                .body(MAP_REF));
        String state = str(res.get("state"));
        if (!"200".equals(state)) {
            throw new PanelxApiException(502, "PanelX 登录失败：" + str(res.get("msg")), null);
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> data = (Map<String, Object>) res.getOrDefault("data", Map.of());
        String t = str(data.get("token"));
        if (t == null || t.isBlank()) {
            throw new PanelxApiException(502, "PanelX 登录响应缺少 token", null);
        }
        token.set(t);
        log.info("PanelX 登录成功：域={} 用户={}", props.getBusDomainCode(), props.getUsername());
        return t;
    }

    private String currentToken() {
        String t = token.get();
        if (t == null || t.isBlank()) {
            return login();
        }
        return t;
    }

    /** GET（自动附加 Bearer token；401 时重新登录并重试一次） */
    public Map<String, Object> get(String path, Map<String, Object> query) {
        return call(() -> {
            UriComponentsBuilder ub = UriComponentsBuilder.fromPath(path);
            if (query != null) {
                query.forEach(ub::queryParam);
            }
            String uri = ub.encode().build().toUriString();
            return client.get()
                    .uri(uri)
                    .header("Authorization", "Bearer " + currentToken())
                    .retrieve()
                    .body(MAP_REF);
        }, true);
    }

    /** POST JSON（同上） */
    public Map<String, Object> post(String path, Object body) {
        return call(() -> client.post()
                .uri(path)
                .contentType(MediaType.APPLICATION_JSON)
                .header("Authorization", "Bearer " + currentToken())
                .body(body)
                .retrieve()
                .body(MAP_REF), true);
    }

    private Map<String, Object> call(Supplier<Map<String, Object>> supplier, boolean retryOnAuth) {
        try {
            return execute(supplier);
        } catch (PanelxApiException e) {
            if (retryOnAuth && e.isAuthFailure()) {
                log.info("PanelX token 失效，重新登录并重试");
                login();
                return execute(supplier);
            }
            throw e;
        }
    }

    /** 执行请求：空响应视为网关错误；HTTP 4xx/5xx 还原为平台风格异常 */
    private Map<String, Object> execute(Supplier<Map<String, Object>> supplier) {
        try {
            Map<String, Object> res = supplier.get();
            if (res == null) {
                throw new PanelxApiException(502, "PanelX 平台返回空响应", null);
            }
            return res;
        } catch (RestClientResponseException e) {
            throw toException(e);
        }
    }

    /** 平台错误体：{"errorDescription":"...","level":"ERROR","errorCode":"...","error":"invalid_request"} */
    private PanelxApiException toException(RestClientResponseException e) {
        String raw = e.getResponseBodyAsString();
        String desc = e.getStatusText();
        String code = "unknown";
        if (raw != null && !raw.isBlank()) {
            try {
                Map<String, Object> m = mapper.readValue(raw, new TypeReference<Map<String, Object>>() {});
                if (str(m.get("errorDescription")) != null) {
                    desc = str(m.get("errorDescription"));
                }
                if (str(m.get("errorCode")) != null) {
                    code = str(m.get("errorCode"));
                }
            } catch (Exception ignore) {
                desc = raw.length() > 300 ? raw.substring(0, 300) : raw;
            }
        }
        return new PanelxApiException(e.getStatusCode().value(), desc, code);
    }

    private static String str(Object o) {
        return o == null ? null : String.valueOf(o);
    }
}