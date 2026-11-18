package com.mes.service.generation;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Function;

import com.mes.entity.FormData;

/**
 * 推式生单的源单快照：把「查源单 → 校验存在 → 校验状态 → 解析表头与明细」
 * 这段在十余条生单线里逐字重复的头部收敛成一次调用。
 *
 * <p>边界：
 * <ul>
 *   <li>只负责拿到干净的表头与明细，<b>不做字段映射</b>（那是各线独有的业务）。</li>
 *   <li>不落库、不生成编号、不校验权限——那些归 {@code insertGenerated}。</li>
 *   <li>异常文案由调用方给出，与原有实现逐字一致，调用方行为不变。</li>
 *   <li>JSON 解析器由调用方注入，避免与 PxService 的 ObjectMapper 行为分叉。</li>
 * </ul>
 */
public final class SourceDocument {

    private final FormData raw;
    private final Map<String, Object> head;
    private final Map<String, Object> detail;

    private SourceDocument(FormData raw, Map<String, Object> head, Map<String, Object> detail) {
        this.raw = raw;
        this.head = head;
        this.detail = detail;
    }

    /**
     * 由已查出的源单构造快照。
     *
     * @param raw        按 (panelCode, formNo) 查出的源单；为 null 时抛「不存在」
     * @param sourceName 源单中文名，用于错误文案（如「费用单」）
     * @param sourceNo   源单号，用于错误文案
     * @param parser     JSON 解析器（通常传 {@code this::parseData}）
     */
    public static SourceDocument of(FormData raw, String sourceName, Object sourceNo,
                                    Function<String, Map<String, Object>> parser) {
        if (raw == null) throw new IllegalArgumentException(sourceName + "不存在：" + sourceNo);
        Map<String, Object> head = parser.apply(raw.getData());
        Map<String, Object> detail = parser.apply(raw.getDetailData());
        return new SourceDocument(raw, head == null ? new HashMap<>() : head,
                detail == null ? new HashMap<>() : detail);
    }

    /** 校验源单状态；不匹配时抛出调用方指定的文案。返回自身便于链式调用。 */
    public SourceDocument requireStatus(String expected, String message) {
        if (!expected.equals(raw.getStatus())) throw new IllegalStateException(message);
        return this;
    }

    /** 源单实体（状态等字段仍可读）。 */
    public FormData raw() {
        return raw;
    }

    /** 表头，永不为 null。 */
    public Map<String, Object> head() {
        return head;
    }

    /** 明细容器（含 items/products/processes 等键），永不为 null。 */
    public Map<String, Object> detail() {
        return detail;
    }

    /** 取指定键的明细行；缺失或类型不符时返回空列表（等价于原三元表达式）。 */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> rows(String key) {
        Object value = detail.get(key);
        return value instanceof List ? (List<Map<String, Object>>) value : new ArrayList<>();
    }
}
