package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 序列号管理：序列号登记与核销都跟着单据走，不再单独维护台账。
 *
 * <p>数据来源（都是 form_data 明细，状况表/跟踪表实时聚合，弃审自动回滚）：
 * <ul>
 *   <li>入库：采购入库单 / 产成品入库单 / 委外入库单 / 其他入库单 的「序列号」页签；</li>
 *   <li>出库：销售出库单 / 材料出库单 / 其他出库单 的同名页签；</li>
 *   <li>补录：序列号登记单（历史数据或导入，不走审批）。</li>
 * </ul>
 *
 * <p>审核硬校验：存货在档案里启用序列号 → 序列号数量必须等于单据数量、单内不重复、同一存货全局唯一；
 * 出库序列号必须「在库」且属于该存货。
 */
@Service
public class SerialNumberService {

    /** 入库类面板：审核通过即登记序列号。 */
    static final Set<String> INBOUND_PANELS = Set.of(
            "PURCHASE_IN", "FINISH_IN", "OUTSOURCE_IN", "OTHER_IN", "INIT_BALANCE");

    /** 出库类面板：审核通过即核销序列号（状态转「已出库」）。 */
    static final Set<String> OUTBOUND_PANELS = Set.of("SALE_OUT", "MATERIAL_OUT", "OTHER_OUT");

    /** 手工补录/导入的登记单。 */
    static final String MANUAL_PANEL = "SERIAL_NO";

    /** 序列号明细页签的 key（入库/出库单据都用它）。 */
    static final String TAB = "serials";

    static final String IN_STOCK = "在库";
    static final String SHIPPED = "已出库";

    /** 一条序列号记录：入库来源 + 出库去向，构成跟踪表的一行。 */
    public record Serial(String itemCode, String itemName, String spec, String unit,
                         String serialNo, String warehouse, String inDoc, String inDate,
                         String outDoc, String outDate, String status) {
    }

    private final FormDataMapper formMapper;
    private final ObjectMapper json = new ObjectMapper();

    public SerialNumberService(FormDataMapper formMapper) {
        this.formMapper = formMapper;
    }

    // ---------- 对外入口（读库） ----------

    /** 全部序列号（在库 + 已出库），状况表取在库、跟踪表取全部。 */
    public List<Serial> all() {
        return aggregate(load(trackedPanels()));
    }

    /** 某存货当前在库的序列号（出库校验与选号用）。 */
    public Set<String> inStock(String itemCode) {
        Set<String> out = new LinkedHashSet<>();
        for (Serial s : all()) {
            if (s.itemCode().equals(text(itemCode)) && IN_STOCK.equals(s.status())) out.add(s.serialNo());
        }
        return out;
    }

    /** 审核前的序列号硬校验。 */
    public void validate(String panelCode, String formNo, Map<String, Object> head, Map<String, Object> detail) {
        validate(load(trackedPanels()), panelCode, formNo, head, detail);
    }

    // ---------- 核心逻辑（可单测：直接喂单据列表） ----------

    /** 存货档案里「序列号管理=是」的存货编码集合。 */
    Set<String> managedItems(List<FormData> docs) {
        Set<String> out = new LinkedHashSet<>();
        for (FormData inv : docs) {
            if (!"INV".equals(inv.getPanelCode())) continue;
            for (Map<String, Object> row : rowsOf(parseJson(inv.getDetailData()), "items")) {
                String code = text(row.get("存货编码"));
                if (code.isEmpty()) continue;
                Object flag = row.get("序列号管理");
                if ("是".equals(text(flag)) || Boolean.TRUE.equals(flag)) out.add(code);
            }
        }
        return out;
    }

    /** 聚合：入库（含补录）→ 在库；被已审核出库单消耗的转「已出库」。 */
    List<Serial> aggregate(List<FormData> docs) {
        Map<String, Serial> byKey = new LinkedHashMap<>();
        Set<String> inboundPanels = new LinkedHashSet<>(INBOUND_PANELS);
        inboundPanels.add(MANUAL_PANEL);
        for (FormData fd : docs) {
            if (!inboundPanels.contains(fd.getPanelCode())) continue;
            // 只有已审核的入库单才算「已登记」；登记单（补录）不走审批，按未作废计
            if (isVoid(fd.getStatus())) continue;
            if (!MANUAL_PANEL.equals(fd.getPanelCode()) && !"已审核".equals(fd.getStatus())) continue;
            Map<String, Object> head = parseJson(fd.getData());
            for (Map<String, Object> row : serialsOf(fd.getPanelCode(), parseJson(fd.getDetailData()))) {
                String code = text(first(row.get("存货编码"), head.get("存货编码"), head.get("存货")));
                String sn = text(row.get("序列号"));
                if (sn.isEmpty()) continue;
                byKey.putIfAbsent(key(code, sn), new Serial(code,
                        text(first(row.get("存货名称"), head.get("存货"))),
                        text(first(row.get("规格型号"), head.get("规格型号"))),
                        text(first(row.get("计量单位"), head.get("计量单位"))),
                        sn,
                        text(first(row.get("仓库"), head.get("仓库"))),
                        fd.getFormNo(), text(head.get("单据日期")), "", "", IN_STOCK));
            }
        }
        for (FormData fd : docs) {
            // 只有已审核的出库单才核销序列号
            if (!OUTBOUND_PANELS.contains(fd.getPanelCode()) || !"已审核".equals(fd.getStatus())) continue;
            Map<String, Object> head = parseJson(fd.getData());
            String date = text(head.get("单据日期"));
            for (Map<String, Object> row : serialRows(parseJson(fd.getDetailData()))) {
                String code = text(row.get("存货编码"));
                String sn = text(row.get("序列号"));
                if (sn.isEmpty()) continue;
                Serial existing = byKey.get(key(code, sn));
                if (existing == null) {
                    // 没有入库记录的序列号（历史脏数据/外部导入）：仍然列出，标已出库
                    byKey.put(key(code, sn), new Serial(code, text(row.get("存货名称")), "", "",
                            sn, text(row.get("仓库")), "", "", fd.getFormNo(), date, SHIPPED));
                    continue;
                }
                if (SHIPPED.equals(existing.status())) continue;
                byKey.put(key(code, sn), new Serial(existing.itemCode(), existing.itemName(),
                        existing.spec(), existing.unit(), sn, existing.warehouse(),
                        existing.inDoc(), existing.inDate(), fd.getFormNo(), date, SHIPPED));
            }
        }
        return new ArrayList<>(byKey.values());
    }

    /** 审核校验（docs 为全部相关单据，测试可直接构造）。 */
    void validate(List<FormData> docs, String panelCode, String formNo,
                  Map<String, Object> head, Map<String, Object> detail) {
        if (panelCode == null) return;
        Set<String> managed = managedItems(docs);
        List<Map<String, Object>> items = rowsOf(detail, "items");
        List<Map<String, Object>> serials = serialsOf(panelCode, detail);
        if (INBOUND_PANELS.contains(panelCode)) {
            validateInbound(docs, formNo, items, serials, managed);
        } else if (OUTBOUND_PANELS.contains(panelCode)) {
            validateOutbound(docs, formNo, items, serials, managed);
        } else if (MANUAL_PANEL.equals(panelCode)) {
            validateManual(docs, formNo, serials, managed, head);
        }
    }

    private void validateInbound(List<FormData> docs, String formNo, List<Map<String, Object>> items,
                                 List<Map<String, Object>> serials, Set<String> managed) {
        Map<String, Double> qtyByItem = quantities(items);
        Map<String, List<String>> serialsByItem = groupSerials(serials);
        Set<String> seen = new HashSet<>();
        for (String sn : flatten(serialsByItem)) {
            if (!seen.add(sn)) throw new IllegalStateException("单据 " + formNo + " 里序列号重复：" + sn);
        }
        for (Map.Entry<String, List<String>> e : serialsByItem.entrySet()) {
            if (!managed.contains(e.getKey())) {
                throw new IllegalStateException("存货 " + label(e.getKey(), items)
                        + " 没有启用序列号，不能登记序列号");
            }
        }
        for (String code : managed) {
            double qty = qtyByItem.getOrDefault(code, 0.0);
            if (qty <= 0) continue;
            int got = serialsByItem.getOrDefault(code, List.of()).size();
            if (got != (int) Math.round(qty)) {
                throw new IllegalStateException("存货 " + label(code, items) + " 启用序列号：本次入库 "
                        + fmt(qty) + "，但登记了 " + got + " 个序列号，请一一对应");
            }
        }
        Set<String> registered = registeredKeys(docs);
        for (Map.Entry<String, List<String>> e : serialsByItem.entrySet()) {
            for (String sn : e.getValue()) {
                if (registered.contains(key(e.getKey(), sn))) {
                    throw new IllegalStateException("序列号 " + sn + " 已存在（存货 " + e.getKey()
                            + "），不能重复登记");
                }
            }
        }
    }

    private void validateOutbound(List<FormData> docs, String formNo, List<Map<String, Object>> items,
                                  List<Map<String, Object>> serials, Set<String> managed) {
        Map<String, Double> qtyByItem = quantities(items);
        Map<String, List<String>> serialsByItem = groupSerials(serials);
        Set<String> seen = new HashSet<>();
        for (String sn : flatten(serialsByItem)) {
            if (!seen.add(sn)) throw new IllegalStateException("单据 " + formNo + " 里序列号重复：" + sn);
        }
        Map<String, Serial> pool = new HashMap<>();
        for (Serial s : aggregate(docs)) pool.put(key(s.itemCode(), s.serialNo()), s);
        for (String code : managed) {
            double qty = qtyByItem.getOrDefault(code, 0.0);
            if (qty <= 0) continue;
            List<String> list = serialsByItem.getOrDefault(code, List.of());
            if (list.size() != (int) Math.round(qty)) {
                throw new IllegalStateException("存货 " + label(code, items) + " 启用序列号：本次出库 "
                        + fmt(qty) + "，但登记了 " + list.size() + " 个序列号，请一一对应");
            }
            for (String sn : list) {
                Serial s = pool.get(key(code, sn));
                if (s == null) {
                    throw new IllegalStateException("序列号 " + sn + " 不是存货 " + label(code, items)
                            + " 的入库序列号，不能出库");
                }
                if (!IN_STOCK.equals(s.status())) {
                    throw new IllegalStateException("序列号 " + sn + " 已于 " + s.outDate() + " 由 "
                            + s.outDoc() + " 出库，不能重复出库");
                }
            }
        }
    }

    private void validateManual(List<FormData> docs, String formNo, List<Map<String, Object>> serials,
                                Set<String> managed, Map<String, Object> head) {
        if (serials.isEmpty()) return;
        Set<String> registered = registeredKeys(docs);
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < serials.size(); i++) {
            Map<String, Object> row = serials.get(i);
            String code = text(first(row.get("存货编码"), head.get("存货编码")));
            String sn = text(row.get("序列号"));
            if (code.isEmpty() || sn.isEmpty()) {
                throw new IllegalStateException("序列号登记单 " + formNo + " 第 " + (i + 1)
                        + " 行缺少存货编码或序列号");
            }
            if (!managed.contains(code)) {
                throw new IllegalStateException("存货 " + code + " 没有启用序列号，不能登记");
            }
            if (!seen.add(key(code, sn))) {
                throw new IllegalStateException("序列号 " + sn + " 在本单重复");
            }
            if (registered.contains(key(code, sn))) {
                throw new IllegalStateException("序列号 " + sn + " 已登记过（存货 " + code + "）");
            }
        }
    }

    // ---------- 工具 ----------

    private Set<String> trackedPanels() {
        Set<String> all = new LinkedHashSet<>(INBOUND_PANELS);
        all.addAll(OUTBOUND_PANELS);
        all.add(MANUAL_PANEL);
        all.add("INV");
        return all;
    }

    private List<FormData> load(Set<String> panels) {
        return formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .in(FormData::getPanelCode, panels)
                .orderByAsc(FormData::getCreateTime));
    }

    /** 已登记过的序列号（全局唯一判定）。 */
    private Set<String> registeredKeys(List<FormData> docs) {
        Set<String> out = new HashSet<>();
        for (Serial s : aggregate(docs)) out.add(key(s.itemCode(), s.serialNo()));
        return out;
    }

    /** 序列号行来源：出入库单据取 serials 页签；手工补录单的 items 本身就是序列号行。 */
    private List<Map<String, Object>> serialsOf(String panelCode, Map<String, Object> detail) {
        return MANUAL_PANEL.equals(panelCode) ? rowsOf(detail, "items") : serialRows(detail);
    }

    /** 单据明细里的序列号页签；没有该页签时为空。 */
    private List<Map<String, Object>> serialRows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> row : rowsOf(detail, TAB)) {
            if (!text(row.get("序列号")).isEmpty()) out.add(row);
        }
        return out;
    }

    private Map<String, Double> quantities(List<Map<String, Object>> items) {
        Map<String, Double> out = new LinkedHashMap<>();
        for (Map<String, Object> row : items) {
            String code = text(first(row.get("存货编码"), row.get("产品编码"), row.get("材料编码")));
            if (code.isEmpty()) continue;
            double qty = num(first(row.get("实收数量"), row.get("数量"),
                    row.get("入库数量"), row.get("出库数量")));
            out.merge(code, qty, Double::sum);
        }
        return out;
    }

    private Map<String, List<String>> groupSerials(List<Map<String, Object>> serials) {
        Map<String, List<String>> out = new LinkedHashMap<>();
        for (Map<String, Object> row : serials) {
            String code = text(row.get("存货编码"));
            String sn = text(row.get("序列号"));
            if (code.isEmpty() || sn.isEmpty()) continue;
            out.computeIfAbsent(code, k -> new ArrayList<>()).add(sn);
        }
        return out;
    }

    private String label(String code, List<Map<String, Object>> items) {
        for (Map<String, Object> row : items) {
            String rowCode = text(first(row.get("存货编码"), row.get("产品编码"), row.get("材料编码")));
            if (!code.equals(rowCode)) continue;
            String name = text(first(row.get("存货名称"), row.get("产品名称"), row.get("材料名称")));
            return name.isEmpty() ? code : name + "(" + code + ")";
        }
        return code;
    }

    private static String key(String itemCode, String serialNo) {
        return itemCode + "|" + serialNo;
    }

    private static List<String> flatten(Map<String, List<String>> grouped) {
        List<String> out = new ArrayList<>();
        grouped.values().forEach(out::addAll);
        return out;
    }

    private static boolean isVoid(String status) {
        String s = status == null ? "" : status.trim();
        return "作废".equals(s) || "已中止".equals(s) || "中止".equals(s);
    }

    private static String fmt(double v) {
        return v == Math.rint(v) ? String.valueOf((long) v) : String.valueOf(v);
    }

    private List<Map<String, Object>> rowsOf(Map<String, Object> detail, String key) {
        if (detail == null) return new ArrayList<>();
        Object value = detail.get(key);
        if (!(value instanceof List<?> list)) return new ArrayList<>();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object item : list) {
            if (item instanceof Map<?, ?> raw) {
                Map<String, Object> row = new LinkedHashMap<>();
                raw.forEach((k, v) -> row.put(String.valueOf(k), v));
                out.add(row);
            }
        }
        return out;
    }

    private Map<String, Object> parseJson(String jsonText) {
        if (jsonText == null || jsonText.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(jsonText, new TypeReference<Map<String, Object>>() {});
        } catch (Exception e) {
            return new LinkedHashMap<>();
        }
    }

    private static Object first(Object... values) {
        for (Object v : values) if (v != null && !String.valueOf(v).isBlank()) return v;
        return "";
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static double num(Object value) {
        if (value instanceof Number n) return n.doubleValue();
        try {
            return value == null || String.valueOf(value).isBlank() ? 0 : Double.parseDouble(String.valueOf(value));
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
