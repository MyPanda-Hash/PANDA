package com.mes.i18n;

import com.aliyun.alimt20181012.Client;
import com.aliyun.alimt20181012.models.TranslateGeneralRequest;
import com.aliyun.alimt20181012.models.TranslateGeneralResponse;
import com.aliyun.tea.TeaException;
import com.aliyun.teaopenapi.models.Config;
import com.aliyun.teautil.models.RuntimeOptions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * 界面词条翻译（2026-09-01 阶段 A）：zh 源 → 目标语言。
 * 词典三级来源：内存缓存（TTL）→ sys_translation 库表 → 阿里云机器翻译（alimt 通用版），
 * 机翻成功写回库表并失效缓存；机翻失败/未配置凭证吞掉异常，前端 tt() 回退中文原文。
 */
@Service
public class TranslationService {

    private static final Logger log = LoggerFactory.getLogger(TranslationService.class);
    private static final ExecutorService MT_POOL = Executors.newFixedThreadPool(8, r -> {
        Thread t = new Thread(r, "alimt-translate");
        t.setDaemon(true);
        return t;
    });

    private final JdbcTemplate jdbcTemplate;
    private final String accessKeyId;
    private final String accessKeySecret;
    private final String endpoint;
    private final RuntimeOptions runtime;
    private final long readTimeoutMs;
    private final long cacheTtlMs;
    private final int maxKeysPerRequest;

    private volatile Client client;
    private final Map<String, CacheEntry> cache = new ConcurrentHashMap<>();

    private record CacheEntry(Map<String, String> dict, long expiresAt) {
    }

    public TranslationService(JdbcTemplate jdbcTemplate,
                              @Value("${mes.mt.access-key-id:}") String accessKeyId,
                              @Value("${mes.mt.access-key-secret:}") String accessKeySecret,
                              @Value("${mes.mt.endpoint:mt.cn-hangzhou.aliyuncs.com}") String endpoint,
                              @Value("${mes.mt.connect-timeout-ms:5000}") int connectTimeoutMs,
                              @Value("${mes.mt.read-timeout-ms:10000}") int readTimeoutMs,
                              @Value("${mes.mt.cache-ttl-seconds:30}") int cacheTtlSeconds,
                              @Value("${mes.mt.max-keys-per-request:50}") int maxKeysPerRequest) {
        this.jdbcTemplate = jdbcTemplate;
        this.accessKeyId = accessKeyId;
        this.accessKeySecret = accessKeySecret;
        this.endpoint = endpoint;
        this.runtime = new RuntimeOptions()
                .setConnectTimeout(connectTimeoutMs)
                .setReadTimeout(readTimeoutMs);
        this.readTimeoutMs = readTimeoutMs;
        this.cacheTtlMs = cacheTtlSeconds * 1000L;
        this.maxKeysPerRequest = maxKeysPerRequest;
    }

    /** 语言键归一：zh-TW/HK/MO → zh-TW，其余取主语言小写（en-US→en）；zh/zh-CN → 空（源语言，无需翻译） */
    public static String localeKey(String locale) {
        if (locale == null) return "";
        String v = locale.trim();
        if (v.isEmpty()) return "";
        String lower = v.toLowerCase(Locale.ROOT).replace('_', '-');
        if (!lower.startsWith("zh")) {
            int dash = lower.indexOf('-');
            return dash > 0 ? lower.substring(0, dash) : lower;
        }
        String region = lower.contains("-") ? lower.substring(lower.indexOf('-') + 1) : "";
        if (region.equals("tw") || region.equals("hk") || region.equals("mo")) return "zh-TW";
        return ""; // zh / zh-CN / zh-SG：源语言
    }

    /** 可选语言清单：zh-CN 恒首位；其余来自 sys_locale(enabled=1)，表未建时仅中文 */
    public List<Map<String, String>> listLocales() {
        List<Map<String, String>> out = new ArrayList<>();
        out.add(localeRow("zh-CN", "简体中文", "简体中文"));
        try {
            for (Map<String, Object> row : jdbcTemplate.queryForList(
                    "SELECT locale, name_zh, name_native FROM sys_locale WHERE enabled = 1 ORDER BY sort, locale")) {
                String code = String.valueOf(row.get("locale"));
                if ("zh-CN".equals(code)) continue; // 已恒首位
                out.add(localeRow(code,
                        String.valueOf(row.get("name_zh")),
                        String.valueOf(row.get("name_native"))));
            }
        } catch (Exception e) {
            log.warn("sys_locale unavailable, fallback to zh-CN only: {}", redact(e.getMessage()));
        }
        return out;
    }

    private static Map<String, String> localeRow(String locale, String nameZh, String nameNative) {
        Map<String, String> m = new LinkedHashMap<>();
        m.put("locale", locale);
        m.put("nameZh", nameZh);
        m.put("nameNative", nameNative);
        return m;
    }

    /**
     * 词条字典：zh→locale 翻译。只返回已有翻译的键（库表缓存或机翻成功）；
     * 失败键不返回，前端 tt() 回退原文。单请求键数上限 maxKeysPerRequest（超出截断并告警）。
     */
    public Map<String, String> dict(String locale, List<String> keys) {
        Map<String, String> out = new LinkedHashMap<>();
        String loc = localeKey(locale);
        if (loc.isEmpty()) return out; // zh/未指定：源语言无需翻译
        if (keys == null || keys.isEmpty()) return out;

        Set<String> wanted = new LinkedHashSet<>();
        for (String k : keys) {
            if (k == null) continue;
            String t = k.trim();
            if (!t.isEmpty()) wanted.add(t);
        }
        if (wanted.size() > maxKeysPerRequest) {
            log.warn("locale dict truncated: locale={}, keys={}, limit={}", loc, wanted.size(), maxKeysPerRequest);
            wanted = new LinkedHashSet<>(new ArrayList<>(wanted).subList(0, maxKeysPerRequest));
        }

        long now = System.currentTimeMillis();
        CacheEntry entry = cache.get(loc);
        Map<String, String> cached = entry != null && entry.expiresAt() > now ? entry.dict() : null;

        Set<String> pending = new LinkedHashSet<>();
        for (String key : wanted) {
            String hit = cached != null ? cached.get(key) : null;
            if (hit != null) out.put(key, hit);
            else pending.add(key);
        }
        if (pending.isEmpty()) return out;

        // 1) 库表（sys_translation 持久词典，机翻结果落库）
        Map<String, String> fromDb = loadFromDb(loc, pending);
        for (Map.Entry<String, String> e : fromDb.entrySet()) {
            out.put(e.getKey(), e.getValue());
        }
        Set<String> remaining = new LinkedHashSet<>(pending);
        remaining.removeAll(fromDb.keySet());
        if (remaining.isEmpty()) {
            refreshCache(loc, wanted, out, cached, now);
            return out;
        }

        // 2) 机翻（并行）+ 写回库表
        Map<String, String> fromMt = machineTranslate(loc, remaining);
        for (Map.Entry<String, String> e : fromMt.entrySet()) {
            out.put(e.getKey(), e.getValue());
        }
        saveToDb(loc, fromMt);

        refreshCache(loc, wanted, out, cached, now);
        return out;
    }

    private void refreshCache(String loc, Set<String> wanted, Map<String, String> fresh,
                              Map<String, String> cached, long now) {
        Map<String, String> merged = new LinkedHashMap<>();
        if (cached != null) merged.putAll(cached);
        for (String key : wanted) {
            String value = fresh.get(key);
            if (value != null) merged.put(key, value);
        }
        cache.put(loc, new CacheEntry(Map.copyOf(merged), now + cacheTtlMs));
    }

    private Map<String, String> loadFromDb(String loc, Set<String> keys) {
        if (keys.isEmpty()) return Map.of();
        try {
            String placeholders = String.join(",", keys.stream().map(k -> "?").toList());
            List<Object> args = new ArrayList<>(keys);
            args.add(0, loc);
            Map<String, String> out = new LinkedHashMap<>();
            for (Map<String, Object> row : jdbcTemplate.queryForList(
                    "SELECT ref_key, text FROM sys_translation WHERE scope = 'biz' AND locale = ? "
                            + "AND ref_key IN (" + placeholders + ")", args.toArray())) {
                Object text = row.get("text");
                if (text != null && !String.valueOf(text).isBlank()) {
                    out.put(String.valueOf(row.get("ref_key")), String.valueOf(text));
                }
            }
            return out;
        } catch (Exception e) {
            log.warn("sys_translation unavailable, skip db dictionary: {}", redact(e.getMessage()));
            return Map.of();
        }
    }

    private void saveToDb(String loc, Map<String, String> translated) {
        if (translated.isEmpty()) return;
        try {
            for (Map.Entry<String, String> e : translated.entrySet()) {
                jdbcTemplate.update(
                        "INSERT INTO sys_translation (scope, ref_key, locale, text, source) VALUES ('biz', ?, ?, ?, 'mt') "
                                + "ON DUPLICATE KEY UPDATE text = VALUES(text), source = VALUES(source)",
                        e.getKey(), loc, e.getValue());
            }
        } catch (Exception e) {
            log.warn("save translations failed (non-fatal): {}", redact(e.getMessage()));
        }
    }

    /** 并行机翻；凭证未配置/调用失败返回空（调用方回退中文），绝不抛出 */
    private Map<String, String> machineTranslate(String loc, Set<String> keys) {
        if (accessKeyId.isBlank() || accessKeySecret.isBlank()) return Map.of();
        Map<String, String> out = new ConcurrentHashMap<>();
        List<CompletableFuture<Void>> futures = new ArrayList<>();
        for (String key : keys) {
            futures.add(CompletableFuture.runAsync(() -> {
                String value = translateOne(key, loc);
                if (value != null && !value.isBlank()) out.put(key, value);
            }, MT_POOL));
        }
        try {
            CompletableFuture.allOf(futures.toArray(new CompletableFuture[0]))
                    .get(readTimeoutMs + 5000L, TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            log.warn("alimt translate batch incomplete (non-fatal): {}", redact(e.getMessage()));
        }
        return out;
    }

    private String translateOne(String text, String target) {
        try {
            TranslateGeneralResponse resp = client().translateGeneralWithOptions(
                    new TranslateGeneralRequest()
                            .setSourceLanguage("zh")
                            .setTargetLanguage(target)
                            .setScene("general")
                            .setFormatType("text")
                            .setSourceText(text),
                    runtime);
            Integer code = resp.getBody() == null ? null : resp.getBody().getCode();
            if (code == null || code != 200) {
                log.warn("alimt rejected: code={}, keyLength={}", code, text.length());
                return null;
            }
            return resp.getBody().getData() == null ? null : resp.getBody().getData().getTranslated();
        } catch (TeaException e) {
            log.warn("alimt call failed: status={}, code={}, message={}",
                    e.getStatusCode(), redact(e.getCode()), redact(e.getMessage()));
            return null;
        } catch (Exception e) {
            log.warn("alimt call failed: type={}, message={}", e.getClass().getSimpleName(), redact(e.getMessage()));
            return null;
        }
    }

    private Client client() throws Exception {
        Client c = client;
        if (c == null) {
            synchronized (this) {
                c = client;
                if (c == null) {
                    c = new Client(new Config()
                            .setAccessKeyId(accessKeyId)
                            .setAccessKeySecret(accessKeySecret)
                            .setEndpoint(endpoint));
                    client = c;
                }
            }
        }
        return c;
    }

    private String redact(Object value) {
        String text = value == null ? "" : String.valueOf(value);
        if (!accessKeyId.isBlank()) text = text.replace(accessKeyId, "***");
        if (!accessKeySecret.isBlank()) text = text.replace(accessKeySecret, "***");
        return text.length() <= 500 ? text : text.substring(0, 500) + "...";
    }
}
