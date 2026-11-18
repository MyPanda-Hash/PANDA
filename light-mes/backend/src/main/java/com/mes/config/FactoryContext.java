package com.mes.config;

/**
 * 当前请求的账套上下文。
 *
 * 多账套隔离的核心：所有业务查询/写入都要带 factory_code。
 * 前端切账套时在请求头带 X-Factory-Code，过滤器把它塞进这里，
 * 业务层从 FactoryContext 取，不依赖每个方法都传参——
 * 否则 13000 行的 PxService 里每个查询都要改签名。
 *
 * 用 ThreadLocal 是安全的：Spring MVC 一个请求一个线程，
 * 且下面在 finally 里一定会清，不会串到下一个请求。
 */
public final class FactoryContext {

    /** 老数据默认归属的账套，与 V8 迁移里的回填口径一致。 */
    public static final String DEFAULT_CODE = "F01";

    private static final ThreadLocal<String> CURRENT = new ThreadLocal<>();

    private FactoryContext() {}

    /** 当前账套；没设置时返回默认账套，保证历史逻辑永远拿到一个有效值。 */
    public static String current() {
        String v = CURRENT.get();
        return (v == null || v.isBlank()) ? DEFAULT_CODE : v;
    }

    /** 是否显式设置过（未设置时走默认值，用于日志区分）。 */
    public static boolean isSet() {
        String v = CURRENT.get();
        return v != null && !v.isBlank();
    }

    public static void set(String code) {
        if (code == null || code.isBlank()) {
            CURRENT.remove();
        } else {
            CURRENT.set(code.trim().toUpperCase());
        }
    }

    /** 必须每个请求结束时调用，防止线程复用把账套带出去。 */
    public static void clear() {
        CURRENT.remove();
    }
}