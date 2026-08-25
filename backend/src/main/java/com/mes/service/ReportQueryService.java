package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Builds read-only T+ style reports from the current form_data business rows. */
@Service
public class ReportQueryService {

    private record Doc(FormData entity, Map<String, Object> head, Map<String, Object> detail) {}

    private static final Set<String> EFFECTIVE_STATUSES = Set.of("已审核", "生产中", "已完工", "已关闭");
    private static final DateTimeFormatter DATE_TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    private final FormDataMapper formMapper;
    private final ObjectMapper json;

    public ReportQueryService(FormDataMapper formMapper, ObjectMapper json) {
        this.formMapper = formMapper;
        this.json = json;
    }

    public Map<String, Object> query(String panelCode, String keyword, Map<String, Object> condition,
                                     int pageNo, int pageSize) {
        List<Map<String, Object>> rows = build(panelCode, condition == null ? Map.of() : condition);
        rows = filter(rows, keyword, condition == null ? Map.of() : condition);
        int safePage = Math.max(1, pageNo);
        int safeSize = Math.max(1, Math.min(pageSize, 500));
        int from = Math.min((safePage - 1) * safeSize, rows.size());
        int to = Math.min(from + safeSize, rows.size());
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("totalSize", rows.size());
        result.put("list", new ArrayList<>(rows.subList(from, to)));
        return result;
    }

    private List<Map<String, Object>> build(String code, Map<String, Object> condition) {
        return switch (code) {
            case "PURCHASE_IN_DETAIL" -> inventoryDetail("PURCHASE_IN", "PURCHASE");
            case "FINISH_IN_DETAIL" -> inventoryDetail("FINISH_IN", "FINISH");
            case "OTHER_IN_DETAIL" -> inventoryDetail("OTHER_IN", "OTHER_IN");
            case "SALE_OUT_DETAIL" -> inventoryDetail("SALE_OUT", "SALE");
            case "MATERIAL_OUT_DETAIL" -> inventoryDetail("MATERIAL_OUT", "MATERIAL");
            case "OTHER_OUT_DETAIL" -> inventoryDetail("OTHER_OUT", "OTHER_OUT");
            case "PURCHASE_IN_STATS" -> inventoryStats("PURCHASE");
            case "FINISH_IN_STATS" -> inventoryStats("FINISH");
            case "OTHER_IN_STATS" -> inventoryStats("OTHER_IN");
            case "SALE_OUT_STATS" -> inventoryStats("SALE");
            case "MATERIAL_OUT_STATS" -> inventoryStats("MATERIAL");
            case "OTHER_OUT_STATS" -> inventoryStats("OTHER_OUT");
            case "COST_MAINTAIN" -> costRows();
            case "STOCK_STATUS" -> stockStatus();
            case "STOCK_SUMMARY" -> stockSummary(condition);
            case "STOCK_LEDGER" -> stockLedger(condition);
            case "SALES_ORDER_DETAIL" -> salesOrderDetail();
            case "SALES_ORDER_STATS" -> salesOrderStats();
            case "SALES_ORDER_EXEC" -> salesOrderExecution();
            case "SALES_ORDER_PROGRESS" -> salesOrderProgress();
            case "MANU_ORDER_DETAIL" -> manufactureDetail();
            case "MANU_ORDER_STATS" -> manufactureStats();
            case "MANU_PROC_STATS" -> manufactureProcessStats();
            case "PROC_DETAIL" -> processDetail();
            case "PROC_STATS" -> processStats();
            case "SALARY_DETAIL" -> salaryDetail();
            case "SALARY_STATS" -> salaryStats();
            case "REWORK_REPORT" -> reworkRows();
            case "OUTSOURCE_ORDER_PRODUCT_DETAIL" -> outsourceOrderProductDetail();
            case "OUTSOURCE_ORDER_MATERIAL_DETAIL" -> outsourceOrderMaterialDetail();
            case "OUTSOURCE_FEE_DETAIL" -> outsourceFeeDetail();
            case "OUTSOURCE_ORDER_EXEC" -> outsourceOrderExecution();
            case "OUTSOURCE_ISSUE_BALANCE" -> outsourceIssueBalance();
            case "OUTSOURCE_ORDER_PRODUCT_STATS" -> outsourceOrderProductStats();
            case "OUTSOURCE_ORDER_MATERIAL_STATS" -> outsourceOrderMaterialStats();
            case "OUTSOURCE_FEE_STATS" -> outsourceFeeStats();
            case "MANU_ORDER_EXEC" -> manufactureOrderExecution();
            case "MANU_ORDER_TRACKER" -> manufactureOrderTracker();
            case "MANU_ORDER_PRODUCT_DETAIL" -> manufactureDetail();
            case "MANU_ORDER_MATERIAL_DETAIL" -> manufactureMaterialDetail();
            case "MANU_ORDER_PRODUCT_STATS" -> manufactureProductStats();
            case "MANU_ORDER_MATERIAL_STATS" -> manufactureMaterialStats();
            case "PICK_ORDER_DETAIL" -> pickOrderDetail();
            case "PICK_ORDER_STATS" -> pickOrderStats();
            case "PICK_ORDER_SUMMARY" -> pickOrderSummary();
            default -> List.of();
        };
    }

    // ---------- inventory document reports ----------

    private List<Map<String, Object>> inventoryDetail(String source, String kind) {
        Map<String, Map<String, Object>> inventory = inventoryIndex();
        Map<String, String> warehouses = warehouseCodes();
        List<Map<String, Object>> rows = new ArrayList<>();
        for (Doc doc : docs(source)) {
            for (Map<String, Object> item : detailRows(doc, source.equals("FINISH_IN") ? "items" : "items")) {
                String name = text(first(item, doc.head(),
                        kind.equals("MATERIAL") ? new String[]{"材料名称", "存货名称"}
                                : kind.equals("FINISH") ? new String[]{"产品名称", "存货名称"}
                                : new String[]{"存货名称", "存货", "产品名称"}));
                Map<String, Object> inv = inventory.getOrDefault(name, Map.of());
                String code = text(first(item, inv, kind.equals("MATERIAL")
                        ? new String[]{"材料编码", "存货编码"} : new String[]{"存货编码", "产品编码"}));
                String warehouse = text(first(item, doc.head(), "仓库"));
                BigDecimal quantity = number(first(item, Map.of(), kind.equals("PURCHASE") || kind.equals("FINISH")
                        ? new String[]{"实收数量", "数量"} : new String[]{"数量", "实收数量"}));
                BigDecimal price = number(first(item, Map.of(), kind.equals("SALE")
                        ? new String[]{"成本价", "单价"} : new String[]{"单价", "成本价"}));
                BigDecimal amount = number(first(item, Map.of(), kind.equals("SALE")
                        ? new String[]{"成本金额", "金额"} : new String[]{"金额", "成本金额"}));
                if (amount.signum() == 0 && quantity.signum() != 0 && price.signum() != 0) amount = quantity.multiply(price);

                Map<String, Object> row = row(
                        "单据日期", value(doc, "单据日期"), "创建时间", created(doc),
                        "单据编号", documentNo(doc), "业务类型", value(doc, "业务类型"),
                        "仓库编码", warehouses.getOrDefault(warehouse, ""), "仓库", warehouse,
                        "入库类别", value(doc, "入库类别"), "出库类别", value(doc, "出库类别"),
                        "供应商编码", value(doc, "供应商编码"), "供应商", firstText(doc.head(), "供应商", "供应商名称"),
                        "客户编码", value(doc, "客户编码"), "客户", value(doc, "客户"),
                        "部门编码", value(doc, "部门编码"), "部门", value(doc, "部门"),
                        "生产车间编码", value(doc, "生产车间编码"), "生产车间", value(doc, "生产车间"),
                        "经手人编码", value(doc, "经手人编码"), "经手人", value(doc, "经手人"),
                        "领用人编码", value(doc, "领用人编码"), "领用人", value(doc, "领用人"),
                        "备注", first(item, doc.head(), "备注"), "制单人", firstText(doc.head(), "制单人", "发起人编号"),
                        "审核人", firstText(doc.head(), "审核人"), "存货编码", code, "存货", name,
                        "材料编码", code, "材料名称", name, "规格型号", first(item, inv, "规格型号"),
                        "材料规格", first(item, inv, "材料规格", "规格型号"),
                        "计量单位", first(item, inv, "计量单位", "生产单位"),
                        "主单位", first(item, inv, "计量单位", "生产单位"), "辅单位", first(item, Map.of(), "计量单位2"),
                        "实收数量", quantity, "数量", quantity, "应发数量", quantity,
                        "实收数量(主单位)", quantity, "数量(主单位)", quantity,
                        "单价", price, "单价(主单位)", price, "成本价", price,
                        "金额", amount, "成本金额", amount, "总成本", amount,
                        "计量单位2", first(item, Map.of(), "计量单位2"),
                        "实收数量2", number(first(item, Map.of(), "实收数量2", "数量2")),
                        "数量2", number(first(item, Map.of(), "数量2", "实收数量2")),
                        "入库调整", number(first(item, Map.of(), "入库调整")),
                        "出库调整", number(first(item, Map.of(), "出库调整")),
                        "费用调整", number(first(item, Map.of(), "费用调整")),
                        "费用金额", number(first(item, Map.of(), "费用金额")),
                        "销售订单号", firstText(doc.head(), "销售订单号"), "入库单号", first(item, doc.head(), "入库单号"),
                        "项目", first(item, doc.head(), "项目"), "单据状态", doc.entity().getStatus());
                row.put("明细.生产车间", first(item, doc.head(), "生产车间"));
                row.put("工作中心", first(item, doc.head(), "工作中心"));
                row.put("班组", first(item, doc.head(), "班组"));
                row.put("工人", first(item, doc.head(), "工人"));
                row.put("设备", first(item, doc.head(), "设备"));
                rows.add(row);
            }
        }
        rows.sort(dateDesc());
        return rows;
    }

    private List<Map<String, Object>> inventoryStats(String kind) {
        List<Map<String, Object>> detail = switch (kind) {
            case "PURCHASE" -> inventoryDetail("PURCHASE_IN", kind);
            case "FINISH" -> inventoryDetail("FINISH_IN", kind);
            case "OTHER_IN" -> inventoryDetail("OTHER_IN", kind);
            case "SALE" -> inventoryDetail("SALE_OUT", kind);
            case "MATERIAL" -> inventoryDetail("MATERIAL_OUT", kind);
            default -> inventoryDetail("OTHER_OUT", kind);
        };
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : detail) {
            String itemCode = text(source.get(kind.equals("MATERIAL") ? "材料编码" : "存货编码"));
            String key = join(source.get("仓库"), source.get("供应商"), itemCode, source.get("单据日期"));
            Map<String, Object> target = groups.get(key);
            if (target == null) {
                target = new LinkedHashMap<>(source);
                groups.put(key, target);
            } else {
                add(target, "实收数量(主单位)", source.get("实收数量"));
                add(target, "数量(主单位)", source.get("数量"));
                add(target, "金额", source.get("金额"));
                add(target, "成本金额", source.get("成本金额"));
                add(target, "入库调整", source.get("入库调整"));
                add(target, "出库调整", source.get("出库调整"));
                add(target, "费用调整", source.get("费用调整"));
                add(target, "费用金额", source.get("费用金额"));
            }
        }
        for (Map<String, Object> target : groups.values()) {
            BigDecimal quantity = number(kind.equals("PURCHASE") || kind.equals("FINISH")
                    ? target.get("实收数量(主单位)") : target.get("数量(主单位)"));
            BigDecimal amount = number(kind.equals("SALE") ? target.get("成本金额") : target.get("金额"));
            BigDecimal average = divide(amount, quantity);
            target.put("单价", average);
            target.put("单价(主单位)", average);
            target.put("成本价(主单位)", average);
            target.put("总成本", amount.add(number(target.get("入库调整"))).add(number(target.get("费用调整"))));
            target.put("单据日期（周）", target.get("单据日期"));
            target.put("计量单位(辅单位)", target.get("辅单位"));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> costRows() {
        List<Map<String, Object>> out = new ArrayList<>();
        Map<String, String> sources = new LinkedHashMap<>();
        sources.put("PURCHASE_IN", "采购入库单");
        sources.put("FINISH_IN", "产成品入库单");
        sources.put("OTHER_IN", "其他入库单");
        sources.put("SALE_OUT", "销售出库单");
        sources.put("MATERIAL_OUT", "材料出库单");
        sources.put("OTHER_OUT", "其他出库单");
        for (Map.Entry<String, String> entry : sources.entrySet()) {
            String kind = switch (entry.getKey()) {
                case "PURCHASE_IN" -> "PURCHASE";
                case "FINISH_IN" -> "FINISH";
                case "OTHER_IN" -> "OTHER_IN";
                case "SALE_OUT" -> "SALE";
                case "MATERIAL_OUT" -> "MATERIAL";
                default -> "OTHER_OUT";
            };
            for (Map<String, Object> detail : inventoryDetail(entry.getKey(), kind)) {
                out.add(row("单据类型", entry.getValue(), "单据编号", detail.get("单据编号"),
                        "单据日期", detail.get("单据日期"), "仓库", detail.get("仓库"),
                        "存货编码", first(detail, detail, "存货编码", "材料编码"),
                        "存货类别", kind.contains("OUT") || "MATERIAL".equals(kind) ? "出库存货" : "入库存货",
                        "存货", first(detail, detail, "存货", "材料名称"), "规格型号", first(detail, detail, "规格型号", "材料规格"),
                        "计量单位", detail.get("计量单位"), "数量", first(detail, detail, "数量", "实收数量"),
                        "原成本", first(detail, detail, "成本价", "单价"), "调整后成本", first(detail, detail, "成本价", "单价"),
                        "成本金额", first(detail, detail, "成本金额", "金额"), "制单人", detail.get("制单人"),
                        "单据状态", detail.get("单据状态")));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    // ---------- stock status, summary and ledger ----------

    private List<Map<String, Object>> movements() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("INIT_BALANCE")) addMovements(out, doc, "INITIAL", "期初结存");
        for (String source : List.of("PURCHASE_IN", "FINISH_IN", "OTHER_IN")) {
            for (Doc doc : docs(source)) if (effective(doc)) addMovements(out, doc, "IN", documentType(source));
        }
        for (String source : List.of("SALE_OUT", "MATERIAL_OUT", "OTHER_OUT")) {
            for (Doc doc : docs(source)) if (effective(doc)) addMovements(out, doc, "OUT", documentType(source));
        }
        out.sort(Comparator.comparing(r -> text(r.get("单据日期"))));
        return out;
    }

    private void addMovements(List<Map<String, Object>> out, Doc doc, String direction, String type) {
        Map<String, Map<String, Object>> inventory = inventoryIndex();
        Map<String, String> warehouseCodes = warehouseCodes();
        for (Map<String, Object> item : detailRows(doc, "items")) {
            String name = firstText(item, "存货名称", "产品名称", "材料名称", "存货");
            Map<String, Object> inv = inventory.getOrDefault(name, Map.of());
            String code = text(first(item, inv, "存货编码", "产品编码", "材料编码"));
            String warehouse = text(first(item, doc.head(), "仓库"));
            BigDecimal qty = number(first(item, Map.of(), "实收数量", "数量"));
            BigDecimal price = number(first(item, Map.of(), direction.equals("OUT") ? new String[]{"成本价", "单价", "主单价"}
                    : new String[]{"单价", "主单价", "成本价"}));
            BigDecimal amount = number(first(item, Map.of(), direction.equals("OUT") ? new String[]{"成本金额", "金额"}
                    : new String[]{"金额", "成本金额"}));
            if (amount.signum() == 0 && qty.signum() != 0 && price.signum() != 0) amount = qty.multiply(price);
            out.add(row("方向", direction, "单据日期", value(doc, "单据日期"), "单据类型", type,
                    "单据编号", documentNo(doc), "业务类型", value(doc, "业务类型"),
                    "往来单位", firstText(doc.head(), "供应商", "客户", "来料客户"),
                    "项目", first(item, doc.head(), "项目"), "仓库编码", warehouseCodes.getOrDefault(warehouse, ""),
                    "仓库", warehouse, "存货编码", code, "存货", name,
                    "规格型号", first(item, inv, "规格型号", "材料规格"),
                    "主单位", first(item, inv, "计量单位", "生产单位"),
                    "数量", qty, "单价", price, "金额", amount));
        }
    }

    private List<Map<String, Object>> stockStatus() {
        Map<String, Map<String, Object>> balances = new LinkedHashMap<>();
        for (Map<String, Object> movement : movements()) {
            String key = join(movement.get("仓库"), movement.get("存货编码"), movement.get("存货"));
            Map<String, Object> balance = balances.computeIfAbsent(key, ignored -> row(
                    "仓库编码", movement.get("仓库编码"), "仓库", movement.get("仓库"),
                    "存货编码", movement.get("存货编码"), "存货", movement.get("存货"),
                    "规格型号", movement.get("规格型号"), "主计量", movement.get("主单位"),
                    "现存量(主)", BigDecimal.ZERO, "结存金额", BigDecimal.ZERO));
            BigDecimal sign = "OUT".equals(movement.get("方向")) ? BigDecimal.valueOf(-1) : BigDecimal.ONE;
            add(balance, "现存量(主)", number(movement.get("数量")).multiply(sign));
            add(balance, "结存金额", number(movement.get("金额")).multiply(sign));
        }
        for (Map<String, Object> balance : balances.values()) {
            balance.put("结存单价(主)", divide(number(balance.get("结存金额")), number(balance.get("现存量(主)"))));
        }
        return new ArrayList<>(balances.values());
    }

    private List<Map<String, Object>> stockSummary(Map<String, Object> condition) {
        LocalDate start = date(condition.get("开始日期"));
        LocalDate end = date(condition.get("结束日期"));
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> movement : movements()) {
            LocalDate movementDate = date(movement.get("单据日期"));
            boolean beforeStart = start != null && movementDate != null && movementDate.isBefore(start);
            boolean inPeriod = (start == null || movementDate == null || !movementDate.isBefore(start))
                    && (end == null || movementDate == null || !movementDate.isAfter(end));
            String key = join(movement.get("仓库"), movement.get("存货编码"), movement.get("存货"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "仓库编码", movement.get("仓库编码"), "仓库", movement.get("仓库"),
                    "存货编码", movement.get("存货编码"), "存货", movement.get("存货"),
                    "规格型号", movement.get("规格型号"), "主单位", movement.get("主单位"), "辅单位", "",
                    "期初数量", BigDecimal.ZERO, "期初金额", BigDecimal.ZERO,
                    "本期入库数量", BigDecimal.ZERO, "本期入库金额", BigDecimal.ZERO,
                    "本期出库数量", BigDecimal.ZERO, "本期出库金额", BigDecimal.ZERO));
            String direction = text(movement.get("方向"));
            BigDecimal qty = number(movement.get("数量"));
            BigDecimal amount = number(movement.get("金额"));
            if ("INITIAL".equals(direction) || beforeStart) {
                BigDecimal sign = "OUT".equals(direction) ? BigDecimal.valueOf(-1) : BigDecimal.ONE;
                add(target, "期初数量", qty.multiply(sign));
                add(target, "期初金额", amount.multiply(sign));
            } else if (inPeriod && "IN".equals(direction)) {
                add(target, "本期入库数量", qty);
                add(target, "本期入库金额", amount);
            } else if (inPeriod && "OUT".equals(direction)) {
                add(target, "本期出库数量", qty);
                add(target, "本期出库金额", amount);
            }
        }
        for (Map<String, Object> target : groups.values()) {
            BigDecimal openingQty = number(target.get("期初数量"));
            BigDecimal openingAmount = number(target.get("期初金额"));
            BigDecimal inQty = number(target.get("本期入库数量"));
            BigDecimal inAmount = number(target.get("本期入库金额"));
            BigDecimal outQty = number(target.get("本期出库数量"));
            BigDecimal outAmount = number(target.get("本期出库金额"));
            target.put("期初平均单价", divide(openingAmount, openingQty));
            target.put("入库平均单价", divide(inAmount, inQty));
            target.put("出库平均单价", divide(outAmount, outQty));
            target.put("期末结存数量", openingQty.add(inQty).subtract(outQty));
            target.put("期末结存金额", openingAmount.add(inAmount).subtract(outAmount));
            target.put("期末平均单价", divide(number(target.get("期末结存金额")), number(target.get("期末结存数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> stockLedger(Map<String, Object> condition) {
        List<Map<String, Object>> selected = filter(movements(), null, condition);
        selected.sort(Comparator.comparing(r -> text(r.get("单据日期"))));
        BigDecimal balanceQty = BigDecimal.ZERO;
        BigDecimal balanceAmount = BigDecimal.ZERO;
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> movement : selected) {
            String direction = text(movement.get("方向"));
            BigDecimal qty = number(movement.get("数量"));
            BigDecimal amount = number(movement.get("金额"));
            boolean outgoing = "OUT".equals(direction);
            balanceQty = outgoing ? balanceQty.subtract(qty) : balanceQty.add(qty);
            balanceAmount = outgoing ? balanceAmount.subtract(amount) : balanceAmount.add(amount);
            Map<String, Object> row = row(
                    "单据日期", movement.get("单据日期"), "单据类型", movement.get("单据类型"),
                    "单据编号", movement.get("单据编号"), "业务类型", movement.get("业务类型"),
                    "往来单位", movement.get("往来单位"), "项目", movement.get("项目"),
                    "收入数量", outgoing ? BigDecimal.ZERO : qty, "收入单价", outgoing ? BigDecimal.ZERO : movement.get("单价"),
                    "收入金额", outgoing ? BigDecimal.ZERO : amount, "发出数量", outgoing ? qty : BigDecimal.ZERO,
                    "发出单价", outgoing ? movement.get("单价") : BigDecimal.ZERO, "发出金额", outgoing ? amount : BigDecimal.ZERO,
                    "结存数量", balanceQty, "结存平均单价", divide(balanceAmount, balanceQty), "结存金额", balanceAmount,
                    "仓库", movement.get("仓库"), "存货", movement.get("存货"));
            out.add(row);
        }
        return out;
    }

    // ---------- sales and production reports ----------

    private List<Map<String, Object>> salesOrderDetail() {
        Map<String, Map<String, Object>> inventory = inventoryIndex();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("SO_ORDER")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                String name = firstText(item, "存货名称", "存货", "产品名称");
                Map<String, Object> inv = inventory.getOrDefault(name, Map.of());
                BigDecimal quantity = number(first(item, Map.of(), "数量"));
                BigDecimal price = number(first(item, Map.of(), "单价"));
                BigDecimal amount = number(first(item, Map.of(), "金额"));
                if (amount.signum() == 0) amount = quantity.multiply(price);
                out.add(row("单据日期", value(doc, "单据日期"), "单据编号", documentNo(doc),
                        "单据状态", doc.entity().getStatus(), "客户编码", value(doc, "客户编码"),
                        "客户", value(doc, "客户"), "结算客户", value(doc, "结算客户"),
                        "部门", value(doc, "部门"), "业务员", firstText(doc.head(), "业务员", "经手人"),
                        "项目", first(item, doc.head(), "项目"), "存货编码", first(item, inv, "存货编码"),
                        "存货", name, "规格型号", first(item, inv, "规格型号"),
                        "计量单位", first(item, inv, "销售单位", "计量单位"), "数量", quantity,
                        "单价", price, "税率%", number(first(item, Map.of(), "税率%")),
                        "含税单价", number(first(item, Map.of(), "含税单价")), "金额", amount,
                        "含税金额", number(first(item, Map.of(), "含税金额")),
                        "折扣金额", number(first(item, Map.of(), "折扣金额")),
                        "预计交货日期", first(item, doc.head(), "预计交货日期"), "现存量", number(first(item, Map.of(), "现存量")),
                        "制单人", firstText(doc.head(), "制单人", "发起人编号"), "审核人", firstText(doc.head(), "审核人")));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> salesOrderStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : salesOrderDetail()) {
            String key = join(source.get("客户编码"), source.get("存货编码"), source.get("存货"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "客户编码", source.get("客户编码"), "客户", source.get("客户"), "部门", source.get("部门"),
                    "业务员", source.get("业务员"), "存货编码", source.get("存货编码"), "存货", source.get("存货"),
                    "规格型号", source.get("规格型号"), "主单位", source.get("计量单位"),
                    "单据数", BigDecimal.ZERO, "数量(主单位)", BigDecimal.ZERO, "金额", BigDecimal.ZERO,
                    "含税金额", BigDecimal.ZERO, "折扣金额", BigDecimal.ZERO,
                    "预计交货日期", source.get("预计交货日期")));
            add(target, "单据数", BigDecimal.ONE);
            add(target, "数量(主单位)", source.get("数量"));
            add(target, "金额", source.get("金额"));
            add(target, "含税金额", source.get("含税金额"));
            add(target, "折扣金额", source.get("折扣金额"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("单价", divide(number(target.get("金额")), number(target.get("数量(主单位)"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> salesOrderExecution() {
        Map<String, BigDecimal> shipped = shippedByOrderAndItem();
        Map<String, Map<String, Object>> production = productionByOrderAndItem();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> detail : salesOrderDetail()) {
            String key = join(detail.get("单据编号"), detail.get("存货编码"), detail.get("存货"));
            BigDecimal ordered = number(detail.get("数量"));
            BigDecimal shippedQty = shipped.getOrDefault(key, BigDecimal.ZERO);
            Map<String, Object> prod = production.getOrDefault(key, Map.of());
            BigDecimal produced = number(prod.get("已汇报数量"));
            out.add(row("单据编号", detail.get("单据编号"), "单据日期", detail.get("单据日期"),
                    "客户编码", detail.get("客户编码"), "客户", detail.get("客户"), "部门", detail.get("部门"),
                    "业务员", detail.get("业务员"), "存货编码", detail.get("存货编码"), "存货", detail.get("存货"),
                    "规格型号", detail.get("规格型号"), "订单数量", ordered, "已出库数量", shippedQty,
                    "出库执行率%", percent(shippedQty, ordered), "已生产数量", produced,
                    "生产进度%", percent(produced, ordered), "未执行数量", ordered.subtract(shippedQty),
                    "预计交货日期", detail.get("预计交货日期"), "单据状态", detail.get("单据状态")));
        }
        return out;
    }

    private List<Map<String, Object>> salesOrderProgress() {
        Map<String, Map<String, Object>> production = productionByOrderAndItem();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> detail : salesOrderDetail()) {
            String key = join(detail.get("单据编号"), detail.get("存货编码"), detail.get("存货"));
            Map<String, Object> prod = production.getOrDefault(key, Map.of());
            BigDecimal ordered = number(detail.get("数量"));
            BigDecimal reported = number(prod.get("已汇报数量"));
            out.add(row("单据编号", detail.get("单据编号"), "单据日期", detail.get("单据日期"),
                    "客户", detail.get("客户"), "存货编码", detail.get("存货编码"), "存货", detail.get("存货"),
                    "规格型号", detail.get("规格型号"), "订单数量", ordered,
                    "加工单号", prod.getOrDefault("加工单号", ""), "加工单数量", number(prod.get("加工单数量")),
                    "已汇报数量", reported, "完工数量", number(prod.get("完工数量")),
                    "生产进度%", percent(reported, ordered), "预完工日", prod.getOrDefault("预完工日", ""),
                    "单据状态", prod.getOrDefault("单据状态", detail.get("单据状态"))));
        }
        return out;
    }

    private List<Map<String, Object>> manufactureDetail() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("MANU_ORDER")) {
            for (Map<String, Object> item : detailRows(doc, "products")) {
                out.add(row("单据编号", documentNo(doc), "单据状态", doc.entity().getStatus(),
                        "生产车间", value(doc, "生产车间"), "客户编码", value(doc, "客户编码"), "客户", value(doc, "客户"),
                        "产品编码", item.get("产品编码"), "产品名称", item.get("产品名称"), "规格型号", item.get("规格型号"),
                        "生产单位", item.get("生产单位"), "数量", number(item.get("数量")),
                        "齐套数量(主)", number(item.get("齐套数量(主)")),
                        "累计汇报套数(工序单位)", number(item.get("累计汇报套数(工序单位)")),
                        "可用量", number(item.get("可用量")), "现存量", number(item.get("现存量")), "图号", item.get("图号"),
                        "单重", number(item.get("单重")), "总重", number(item.get("总重")), "需求令号", item.get("需求令号"),
                        "预开工日", value(doc, "预开工日"), "预完工日", value(doc, "预完工日"),
                        "销售订单号", value(doc, "销售订单号")));
            }
        }
        return out;
    }

    private List<Map<String, Object>> manufactureStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : manufactureDetail()) {
            String key = join(source.get("产品编码"), source.get("产品名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "产品编码", source.get("产品编码"), "产品名称", source.get("产品名称"),
                    "规格型号", source.get("规格型号"), "生产单位", source.get("生产单位"),
                    "加工单数", BigDecimal.ZERO, "计划数量", BigDecimal.ZERO,
                    "累计汇报数量", BigDecimal.ZERO, "完工数量", BigDecimal.ZERO));
            add(target, "加工单数", BigDecimal.ONE);
            add(target, "计划数量", source.get("数量"));
            add(target, "累计汇报数量", source.get("累计汇报套数(工序单位)"));
            if ("已完工".equals(source.get("单据状态"))) add(target, "完工数量", source.get("数量"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("生产进度%", percent(number(target.get("累计汇报数量")), number(target.get("计划数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> manufactureProcessStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Doc doc : docs("MANU_ORDER")) {
            for (Map<String, Object> process : detailRows(doc, "processes")) {
                String key = join(process.get("工序编码"), process.get("工序名称"), process.get("生产车间"));
                Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                        "工序编码", process.get("工序编码"), "工序名称", process.get("工序名称"),
                        "生产车间", first(process, doc.head(), "生产车间"), "工作中心", process.get("工作中心"),
                        "班组", first(process, Map.of(), "班组", "班组名称"), "设备", process.get("设备"),
                        "加工单数", BigDecimal.ZERO, "计划数量", BigDecimal.ZERO, "金额", BigDecimal.ZERO,
                        "已完工", BigDecimal.ZERO, "进行中", BigDecimal.ZERO, "未开工", BigDecimal.ZERO,
                        "单位标准工时", number(process.get("单位标准工时"))));
                add(target, "加工单数", BigDecimal.ONE);
                add(target, "计划数量", process.get("计划数量"));
                add(target, "金额", first(process, Map.of(), "金额", "计时/计件金额"));
                String status = firstText(process, "工序完工状态", "派工加工状态");
                if (status.contains("完工")) add(target, "已完工", BigDecimal.ONE);
                else if (status.contains("进行") || status.contains("派工")) add(target, "进行中", BigDecimal.ONE);
                else add(target, "未开工", BigDecimal.ONE);
            }
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> processDetail() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("PROCESS_REPORT")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                BigDecimal reported = number(item.get("报工数量"));
                BigDecimal qualified = number(item.get("合格数量"));
                BigDecimal rejected = number(first(item, Map.of(), "不合格数量", "工废数量", "料废数量"));
                BigDecimal amount = number(first(item, Map.of(), "金额", "计时/计件金额", "计件金额"));
                out.add(row("单据编号", documentNo(doc), "单据状态", doc.entity().getStatus(),
                        "单据日期", value(doc, "单据日期"), "加工单号", first(item, doc.head(), "加工单号"),
                        "生产车间", first(item, doc.head(), "生产车间"), "产品编码", first(item, doc.head(), "产品编码"),
                        "产品名称", first(item, doc.head(), "产品名称"), "规格型号", first(item, doc.head(), "规格型号"),
                        "工序编码", item.get("工序编码"), "工序名称", item.get("工序名称"), "工作中心", item.get("工作中心"),
                        "设备", item.get("设备"), "班组名称", first(item, Map.of(), "班组名称", "班组"),
                        "工人名称", first(item, Map.of(), "工人名称", "工人"), "报工数量", reported,
                        "合格数量", qualified, "不合格数量", rejected, "合格率%", percent(qualified, reported),
                        "工资类型", item.get("工资类型"), "工价", number(item.get("工价")), "金额", amount,
                        "开工日期", item.get("开工日期"), "完工日期", item.get("完工日期"), "备注", item.get("备注"),
                        "待返修数量-本序发现", number(item.get("待返修数量-本序发现")),
                        "待返修数量-他序发现", number(item.get("待返修数量-他序发现")),
                        "返修责任工序", item.get("返修责任工序"), "返修状态", first(item, Map.of(), "返修状态", "工序完工状态"),
                        "调整工资", number(item.get("调整工资")), "客户", value(doc, "客户")));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> processStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : processDetail()) {
            String key = join(source.get("工序编码"), source.get("工人名称"), source.get("班组名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "工序编码", source.get("工序编码"), "工序名称", source.get("工序名称"),
                    "生产车间", source.get("生产车间"), "班组名称", source.get("班组名称"),
                    "工人名称", source.get("工人名称"), "报工单数", BigDecimal.ZERO,
                    "报工数量", BigDecimal.ZERO, "合格数量", BigDecimal.ZERO,
                    "不合格数量", BigDecimal.ZERO, "金额", BigDecimal.ZERO));
            add(target, "报工单数", BigDecimal.ONE);
            add(target, "报工数量", source.get("报工数量"));
            add(target, "合格数量", source.get("合格数量"));
            add(target, "不合格数量", source.get("不合格数量"));
            add(target, "金额", source.get("金额"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("合格率%", percent(number(target.get("合格数量")), number(target.get("报工数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> salaryDetail() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> process : processDetail()) {
            BigDecimal quantity = number(process.get("报工数量"));
            BigDecimal price = number(process.get("工价"));
            BigDecimal pieceAmount = number(process.get("金额"));
            if (pieceAmount.signum() == 0) pieceAmount = quantity.multiply(price);
            out.add(row("单据编号", process.get("单据编号"), "单据日期", process.get("单据日期"),
                    "单据状态", process.get("单据状态"), "加工单号", process.get("加工单号"),
                    "工序编码", process.get("工序编码"), "工序名称", process.get("工序名称"),
                    "班组名称", process.get("班组名称"), "工人名称", process.get("工人名称"),
                    "工资类型", process.get("工资类型"), "计件数量", quantity, "工价", price,
                    "计件金额", pieceAmount, "金额", pieceAmount,
                    "调整工资", process.get("调整工资")));
        }
        return out;
    }

    private List<Map<String, Object>> salaryStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : salaryDetail()) {
            String key = join(source.get("工人名称"), source.get("班组名称"), source.get("工资类型"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "工人名称", source.get("工人名称"), "班组名称", source.get("班组名称"),
                    "工资类型", source.get("工资类型"), "报工单数", BigDecimal.ZERO,
                    "计件数量", BigDecimal.ZERO, "计件金额", BigDecimal.ZERO,
                    "调整工资", BigDecimal.ZERO, "工资合计", BigDecimal.ZERO));
            add(target, "报工单数", BigDecimal.ONE);
            add(target, "计件数量", source.get("计件数量"));
            add(target, "计件金额", source.get("计件金额"));
            add(target, "调整工资", source.get("调整工资"));
            target.put("工资合计", number(target.get("计件金额")).add(number(target.get("调整工资"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> reworkRows() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> source : processDetail()) {
            BigDecimal own = number(source.get("待返修数量-本序发现"));
            BigDecimal other = number(source.get("待返修数量-他序发现"));
            if (own.add(other).signum() == 0 && !text(source.get("返修状态")).contains("返修")) continue;
            out.add(row("单据编号", source.get("单据编号"), "单据日期", source.get("单据日期"),
                    "单据状态", source.get("单据状态"), "加工单号", source.get("加工单号"),
                    "产品编码", source.get("产品编码"), "产品名称", source.get("产品名称"),
                    "规格型号", source.get("规格型号"), "客户", source.get("客户"),
                    "工序编码", source.get("工序编码"), "工序名称", source.get("工序名称"),
                    "工作中心", source.get("工作中心"), "设备", source.get("设备"),
                    "班组", source.get("班组名称"), "工人", source.get("工人名称"),
                    "待返修数量-本序发现", own, "待返修数量-他序发现", other,
                    "待返修合计", own.add(other), "返修责任工序", source.get("返修责任工序"),
                    "返修状态", source.get("返修状态")));
        }
        return out;
    }

    // ---------- outsource reports (T+ 委外管理 OM 模块, 2026-08-25) ----------

    private List<Map<String, Object>> outsourceOrderProductDetail() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("OUTSOURCE_ORDER")) {
            for (Map<String, Object> item : detailRows(doc, "products")) {
                BigDecimal qty = number(item.get("数量"));
                BigDecimal price = number(first(item, Map.of(), "委外单价", "单价"));
                BigDecimal amount = number(item.get("金额"));
                if (amount.signum() == 0) amount = qty.multiply(price);
                out.add(row("单据编号", documentNo(doc), "单据状态", doc.entity().getStatus(),
                        "单据日期", value(doc, "单据日期"), "委外供应商", value(doc, "委外供应商"),
                        "生产车间", value(doc, "生产车间"), "经手人", value(doc, "经手人"),
                        "交货日期", first(item, doc.head(), "交货日期", "预完工日"),
                        "产品编码", item.get("产品编码"), "产品名称", item.get("产品名称"),
                        "规格型号", item.get("规格型号"), "计量单位", first(item, Map.of(), "计量单位", "生产单位"),
                        "数量", qty, "委外单价", price, "金额", amount,
                        "预完工日", value(doc, "预完工日"), "制单人", firstText(doc.head(), "制单人", "发起人编号"),
                        "审核人", firstText(doc.head(), "审核人")));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> outsourceOrderMaterialDetail() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("OUTSOURCE_ORDER")) {
            for (Map<String, Object> item : detailRows(doc, "materials")) {
                out.add(row("单据编号", documentNo(doc), "单据状态", doc.entity().getStatus(),
                        "单据日期", value(doc, "单据日期"), "委外供应商", value(doc, "委外供应商"),
                        "材料编码", item.get("材料编码"), "材料名称", item.get("材料名称"),
                        "规格型号", item.get("规格型号"), "计量单位", first(item, Map.of(), "计量单位", "生产单位"),
                        "计划数量", number(first(item, Map.of(), "计划数量", "需用数量")),
                        "预出仓库", first(item, Map.of(), "预出仓库", "仓库"),
                        "现存量", number(item.get("现存量")), "可用量", number(item.get("可用量"))));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> outsourceFeeDetail() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("OUTSOURCE_FEE")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                out.add(row("单据编号", documentNo(doc), "单据日期", value(doc, "单据日期"),
                        "单据状态", doc.entity().getStatus(), "委外供应商", value(doc, "委外供应商"),
                        "委外加工单号", value(doc, "委外加工单号"), "费用项目", item.get("费用项目"),
                        "产品名称", item.get("产品名称"), "计量单位", item.get("计量单位"),
                        "数量", number(item.get("数量")), "委外单价", number(item.get("委外单价")),
                        "费用金额", number(first(item, Map.of(), "费用金额", "金额")),
                        "费用合计", number(doc.head().get("费用合计")),
                        "经手人", value(doc, "经手人"), "备注", item.get("备注")));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> outsourceOrderExecution() {
        Map<String, BigDecimal> received = new HashMap<>();
        Map<String, BigDecimal> issued = new HashMap<>();
        for (Doc doc : docs("OUTSOURCE_IN")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                String orderNo = firstText(doc.head(), "委外加工单号");
                String key = join(orderNo, item.get("产品编码"), item.get("产品名称"));
                received.merge(key, number(item.get("实收数量")), BigDecimal::add);
            }
        }
        for (Doc doc : docs("OUTSOURCE_ISSUE")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                String orderNo = firstText(doc.head(), "委外加工单号");
                String key = join(orderNo, item.get("材料编码"), item.get("材料名称"));
                issued.merge(key, number(item.get("数量")), BigDecimal::add);
            }
        }
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("OUTSOURCE_ORDER")) {
            for (Map<String, Object> item : detailRows(doc, "products")) {
                String key = join(documentNo(doc), item.get("产品编码"), item.get("产品名称"));
                BigDecimal ordered = number(item.get("数量"));
                BigDecimal receivedQty = received.getOrDefault(key, BigDecimal.ZERO);
                BigDecimal issueQty = issued.getOrDefault(join(documentNo(doc), item.get("产品编码"), item.get("产品名称")), BigDecimal.ZERO);
                out.add(row("单据编号", documentNo(doc), "单据日期", value(doc, "单据日期"),
                        "委外供应商", value(doc, "委外供应商"), "产品编码", item.get("产品编码"),
                        "产品名称", item.get("产品名称"), "规格型号", item.get("规格型号"),
                        "订单数量", ordered, "已入库数量", receivedQty,
                        "入库执行率%", percent(receivedQty, ordered),
                        "已发料数量", issueQty, "未入库数量", ordered.subtract(receivedQty),
                        "交货日期", first(item, doc.head(), "交货日期", "预完工日"),
                        "单据状态", doc.entity().getStatus()));
            }
        }
        return out;
    }

    private List<Map<String, Object>> outsourceIssueBalance() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("OUTSOURCE_ISSUE")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                BigDecimal issued = number(item.get("数量"));
                out.add(row("单据编号", documentNo(doc), "单据日期", value(doc, "单据日期"),
                        "委外供应商", value(doc, "委外供应商"), "委外加工单号", value(doc, "委外加工单号"),
                        "材料编码", item.get("材料编码"), "材料名称", item.get("材料名称"),
                        "规格型号", item.get("规格型号"), "计量单位", item.get("计量单位"),
                        "发料数量", issued, "耗用数量", BigDecimal.ZERO, "结存数量", issued));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> outsourceOrderProductStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : outsourceOrderProductDetail()) {
            String key = join(source.get("委外供应商"), source.get("产品编码"), source.get("产品名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "委外供应商", source.get("委外供应商"), "产品编码", source.get("产品编码"),
                    "产品名称", source.get("产品名称"), "规格型号", source.get("规格型号"),
                    "计量单位", source.get("计量单位"), "加工单数", BigDecimal.ZERO,
                    "订单数量", BigDecimal.ZERO, "已入库数量", BigDecimal.ZERO,
                    "未入库数量", BigDecimal.ZERO, "委外金额", BigDecimal.ZERO));
            add(target, "加工单数", BigDecimal.ONE);
            add(target, "订单数量", source.get("数量"));
            add(target, "金额", source.get("金额"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("委外金额", target.get("金额"));
            target.put("未入库数量", number(target.get("订单数量")).subtract(number(target.get("已入库数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> outsourceOrderMaterialStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : outsourceOrderMaterialDetail()) {
            String key = join(source.get("委外供应商"), source.get("材料编码"), source.get("材料名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "委外供应商", source.get("委外供应商"), "材料编码", source.get("材料编码"),
                    "材料名称", source.get("材料名称"), "规格型号", source.get("规格型号"),
                    "计量单位", source.get("计量单位"), "加工单数", BigDecimal.ZERO,
                    "计划数量", BigDecimal.ZERO, "已发料数量", BigDecimal.ZERO,
                    "未发料数量", BigDecimal.ZERO));
            add(target, "加工单数", BigDecimal.ONE);
            add(target, "计划数量", source.get("计划数量"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("未发料数量", number(target.get("计划数量")).subtract(number(target.get("已发料数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> outsourceFeeStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : outsourceFeeDetail()) {
            String key = join(source.get("委外供应商"), source.get("费用项目"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "委外供应商", source.get("委外供应商"), "费用项目", source.get("费用项目"),
                    "单据数", BigDecimal.ZERO, "数量", BigDecimal.ZERO,
                    "委外单价", BigDecimal.ZERO, "费用金额", BigDecimal.ZERO,
                    "费用合计", BigDecimal.ZERO));
            add(target, "单据数", BigDecimal.ONE);
            add(target, "数量", source.get("数量"));
            add(target, "费用金额", source.get("费用金额"));
            add(target, "费用合计", source.get("费用合计"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("委外单价", divide(number(target.get("费用金额")), number(target.get("数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    // ---------- 生产管理执行/跟踪/产成品材料报表（对齐真实 T+ 生产管理 MP 模块, 2026-08-25） ----------

    private List<Map<String, Object>> manufactureOrderExecution() {
        Map<String, BigDecimal> received = new HashMap<>();
        for (Doc doc : docs("FINISH_IN")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                String orderNo = firstText(doc.head(), "加工单号", "来源单号", "销售订单号");
                if (orderNo.isBlank()) continue;
                String key = join(orderNo, item.get("产品编码"), item.get("产品名称"));
                received.merge(key, number(first(item, Map.of(), "实收数量", "数量")), BigDecimal::add);
            }
        }
        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> source : manufactureDetail()) {
            String orderNo = text(source.get("单据编号"));
            String key = join(orderNo, source.get("产品编码"), source.get("产品名称"));
            BigDecimal planned = number(source.get("数量"));
            BigDecimal reported = number(source.get("累计汇报套数(工序单位)"));
            BigDecimal completed = "已完工".equals(source.get("单据状态")) ? planned : BigDecimal.ZERO;
            BigDecimal inStock = received.getOrDefault(key, BigDecimal.ZERO);
            out.add(row("单据编号", orderNo, "单据状态", source.get("单据状态"),
                    "生产车间", source.get("生产车间"), "客户", source.get("客户"),
                    "产品编码", source.get("产品编码"), "产品名称", source.get("产品名称"),
                    "规格型号", source.get("规格型号"), "计划数量", planned,
                    "累计汇报数量", reported, "完工数量", completed,
                    "累计入库数量", inStock, "未完工数量", planned.subtract(reported).max(BigDecimal.ZERO),
                    "生产进度%", percent(reported, planned), "预完工日", source.get("预完工日")));
        }
        return out;
    }

    private List<Map<String, Object>> manufactureOrderTracker() {
        List<Map<String, Object>> out = new ArrayList<>();
        Map<String, BigDecimal> reportedByProcess = new HashMap<>();
        for (Doc doc : docs("PROCESS_REPORT")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                String orderNo = text(first(item, doc.head(), "加工单号"));
                String key = join(orderNo, item.get("工序编码"));
                reportedByProcess.merge(key, number(first(item, Map.of(), "报工数量", "合格数量")), BigDecimal::add);
            }
        }
        for (Doc doc : docs("MANU_ORDER")) {
            for (Map<String, Object> process : detailRows(doc, "processes")) {
                String orderNo = documentNo(doc);
                String key = join(orderNo, process.get("工序编码"));
                BigDecimal planQty = number(process.get("计划数量"));
                BigDecimal reported = reportedByProcess.getOrDefault(key, BigDecimal.ZERO);
                out.add(row("单据编号", orderNo, "单据状态", doc.entity().getStatus(),
                        "产品编码", first(process, Map.of(), "产品编码"), "产品名称", first(process, Map.of(), "产品名称"),
                        "规格型号", first(process, Map.of(), "规格型号"), "数量", planQty,
                        "工序编码", process.get("工序编码"), "工序名称", process.get("工序名称"),
                        "生产车间", first(process, doc.head(), "生产车间"), "工作中心", process.get("工作中心"),
                        "设备", process.get("设备"), "班组", first(process, Map.of(), "班组", "班组名称"),
                        "工人", first(process, Map.of(), "工人", "工人名称"),
                        "计划数量", planQty, "报工数量", reported,
                        "合格数量", reported,
                        "工序完工状态", firstText(process, "工序完工状态", "派工加工状态"),
                        "工序进度%", percent(reported, planQty)));
            }
        }
        return out;
    }

    private List<Map<String, Object>> manufactureMaterialDetail() {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("MANU_ORDER")) {
            for (Map<String, Object> item : detailRows(doc, "materials")) {
                Map<String, Object> product = detailRows(doc, "products").isEmpty()
                        ? Map.of() : detailRows(doc, "products").get(0);
                out.add(row("单据编号", documentNo(doc), "单据状态", doc.entity().getStatus(),
                        "生产车间", value(doc, "生产车间"),
                        "产品编码", product.get("产品编码"), "产品名称", product.get("产品名称"),
                        "材料编码", item.get("材料编码"), "材料名称", item.get("材料名称"),
                        "规格型号", first(item, Map.of(), "规格型号", "材料规格"),
                        "计量单位", first(item, Map.of(), "计量单位", "生产单位"),
                        "计划数量", number(first(item, Map.of(), "计划数量", "需用数量", "定额需用数量")),
                        "需用数量", number(first(item, Map.of(), "需用数量", "定额需用数量", "计划数量")),
                        "预出仓库", first(item, Map.of(), "预出仓库", "仓库"),
                        "现存量", number(item.get("现存量")), "可用量", number(item.get("可用量"))));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> manufactureProductStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : manufactureDetail()) {
            String key = join(source.get("产品编码"), source.get("产品名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "产品编码", source.get("产品编码"), "产品名称", source.get("产品名称"),
                    "规格型号", source.get("规格型号"), "生产单位", source.get("生产单位"),
                    "加工单数", BigDecimal.ZERO, "计划数量", BigDecimal.ZERO,
                    "已完工数量", BigDecimal.ZERO, "生产进度%", BigDecimal.ZERO));
            add(target, "加工单数", BigDecimal.ONE);
            add(target, "计划数量", source.get("数量"));
            if ("已完工".equals(source.get("单据状态"))) add(target, "已完工数量", source.get("数量"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("生产进度%", percent(number(target.get("已完工数量")), number(target.get("计划数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> manufactureMaterialStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : manufactureMaterialDetail()) {
            String key = join(source.get("材料编码"), source.get("材料名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "材料编码", source.get("材料编码"), "材料名称", source.get("材料名称"),
                    "规格型号", source.get("规格型号"), "计量单位", source.get("计量单位"),
                    "加工单数", BigDecimal.ZERO, "计划数量", BigDecimal.ZERO,
                    "已领料数量", BigDecimal.ZERO, "未领料数量", BigDecimal.ZERO));
            add(target, "加工单数", BigDecimal.ONE);
            add(target, "计划数量", source.get("计划数量"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("未领料数量", number(target.get("计划数量")).subtract(number(target.get("已领料数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    // ---------- 配货管理报表（对齐真实 T+ 配货管理 DIM 模块, 2026-08-25） ----------

    private List<Map<String, Object>> pickOrderDetail() {
        Map<String, Map<String, Object>> inventory = inventoryIndex();
        List<Map<String, Object>> out = new ArrayList<>();
        for (Doc doc : docs("PICK_ORDER")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                String name = firstText(item, "存货名称", "产品名称", "存货");
                Map<String, Object> inv = inventory.getOrDefault(name, Map.of());
                BigDecimal qty = number(first(item, Map.of(), "数量", "配货数量"));
                BigDecimal price = number(first(item, Map.of(), "单价", "售价"));
                BigDecimal amount = number(first(item, Map.of(), "金额", "销售金额"));
                if (amount.signum() == 0 && qty.signum() != 0 && price.signum() != 0) amount = qty.multiply(price);
                out.add(row("单据编号", documentNo(doc), "单据日期", value(doc, "单据日期"),
                        "单据状态", doc.entity().getStatus(), "客户", value(doc, "客户"),
                        "仓库", first(item, doc.head(), "仓库"), "存货编码", first(item, inv, "存货编码"),
                        "存货名称", name, "规格型号", first(item, inv, "规格型号"),
                        "计量单位", first(item, inv, "销售单位", "计量单位"), "数量", qty,
                        "单价", price, "金额", amount, "销售订单号", firstText(doc.head(), "销售订单号", "来源单号"),
                        "制单人", firstText(doc.head(), "制单人", "发起人编号"), "审核人", firstText(doc.head(), "审核人")));
            }
        }
        out.sort(dateDesc());
        return out;
    }

    private List<Map<String, Object>> pickOrderStats() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : pickOrderDetail()) {
            String key = join(source.get("客户"), source.get("存货编码"), source.get("存货名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "客户", source.get("客户"), "存货编码", source.get("存货编码"),
                    "存货名称", source.get("存货名称"), "规格型号", source.get("规格型号"),
                    "主单位", source.get("计量单位"), "配货单数", BigDecimal.ZERO,
                    "数量(主单位)", BigDecimal.ZERO, "金额", BigDecimal.ZERO));
            add(target, "配货单数", BigDecimal.ONE);
            add(target, "数量(主单位)", source.get("数量"));
            add(target, "金额", source.get("金额"));
        }
        return new ArrayList<>(groups.values());
    }

    private List<Map<String, Object>> pickOrderSummary() {
        Map<String, Map<String, Object>> groups = new LinkedHashMap<>();
        for (Map<String, Object> source : pickOrderDetail()) {
            String key = join(source.get("仓库"), source.get("存货编码"), source.get("存货名称"));
            Map<String, Object> target = groups.computeIfAbsent(key, ignored -> row(
                    "仓库", source.get("仓库"), "存货编码", source.get("存货编码"),
                    "存货名称", source.get("存货名称"), "规格型号", source.get("规格型号"),
                    "配货单数", BigDecimal.ZERO, "配货数量", BigDecimal.ZERO,
                    "销售出库数量", BigDecimal.ZERO, "未出库数量", BigDecimal.ZERO));
            add(target, "配货单数", BigDecimal.ONE);
            add(target, "配货数量", source.get("数量"));
        }
        // 销售出库数量：SALE_OUT 中 销售订单号 关联
        for (Map<String, Object> row : inventoryDetail("SALE_OUT", "SALE")) {
            String key = join(row.get("仓库"), row.get("存货编码"), row.get("存货"));
            Map<String, Object> target = groups.get(key);
            if (target != null) add(target, "销售出库数量", row.get("数量"));
        }
        for (Map<String, Object> target : groups.values()) {
            target.put("未出库数量", number(target.get("配货数量")).subtract(number(target.get("销售出库数量"))));
        }
        return new ArrayList<>(groups.values());
    }

    // ---------- cross-report indexes ----------

    private Map<String, BigDecimal> shippedByOrderAndItem() {
        Map<String, BigDecimal> out = new HashMap<>();
        for (Map<String, Object> row : inventoryDetail("SALE_OUT", "SALE")) {
            String orderNo = text(row.get("销售订单号"));
            if (orderNo.isBlank()) continue;
            String key = join(orderNo, row.get("存货编码"), row.get("存货"));
            out.merge(key, number(row.get("数量")), BigDecimal::add);
        }
        return out;
    }

    private Map<String, Map<String, Object>> productionByOrderAndItem() {
        Map<String, Map<String, Object>> out = new LinkedHashMap<>();
        for (Map<String, Object> row : manufactureDetail()) {
            String orderNo = text(row.get("销售订单号"));
            if (orderNo.isBlank()) continue;
            String key = join(orderNo, row.get("产品编码"), row.get("产品名称"));
            Map<String, Object> target = out.computeIfAbsent(key, ignored -> row(
                    "加工单号", row.get("单据编号"), "加工单数量", BigDecimal.ZERO,
                    "已汇报数量", BigDecimal.ZERO, "完工数量", BigDecimal.ZERO,
                    "预完工日", row.get("预完工日"), "单据状态", row.get("单据状态")));
            add(target, "加工单数量", row.get("数量"));
            add(target, "已汇报数量", row.get("累计汇报套数(工序单位)"));
            if ("已完工".equals(row.get("单据状态"))) add(target, "完工数量", row.get("数量"));
        }
        return out;
    }

    private Map<String, Map<String, Object>> inventoryIndex() {
        Map<String, Map<String, Object>> out = new HashMap<>();
        for (Doc doc : docs("INV")) {
            for (Map<String, Object> item : detailRows(doc, "items")) {
                String name = firstText(item, "存货名称", "产品名称", "材料名称");
                String code = firstText(item, "存货编码", "产品编码", "材料编码");
                if (!name.isBlank()) out.put(name, item);
                if (!code.isBlank()) out.put(code, item);
            }
        }
        return out;
    }

    private Map<String, String> warehouseCodes() {
        Map<String, String> out = new HashMap<>();
        for (Doc doc : docs("WH")) {
            for (Map<String, Object> item : allDetailRows(doc)) {
                String name = firstText(item, "仓库名称", "仓库");
                String code = firstText(item, "仓库编码");
                if (!name.isBlank()) out.put(name, code);
            }
        }
        return out;
    }

    // ---------- filtering and data access ----------

    private List<Map<String, Object>> filter(List<Map<String, Object>> source, String keyword,
                                              Map<String, Object> condition) {
        List<Map<String, Object>> out = new ArrayList<>();
        LocalDate start = date(condition.get("开始日期"));
        LocalDate end = date(condition.get("结束日期"));
        for (Map<String, Object> row : source) {
            LocalDate rowDate = date(first(row, row, "单据日期", "预开工日", "创建时间"));
            if (start != null && rowDate != null && rowDate.isBefore(start)) continue;
            if (end != null && rowDate != null && rowDate.isAfter(end)) continue;
            boolean hit = true;
            for (Map.Entry<String, Object> entry : condition.entrySet()) {
                String key = entry.getKey();
                String expected = text(entry.getValue());
                if (expected.isBlank() || key.equals("开始日期") || key.equals("结束日期")) continue;
                if (!aliasValues(row, key).stream().anyMatch(value -> text(value).contains(expected))) {
                    hit = false;
                    break;
                }
            }
            if (!hit) continue;
            if (keyword != null && !keyword.isBlank()) {
                boolean keywordHit = row.values().stream().anyMatch(value -> text(value).contains(keyword));
                if (!keywordHit) continue;
            }
            out.add(row);
        }
        return out;
    }

    private List<Object> aliasValues(Map<String, Object> row, String key) {
        Set<String> aliases = switch (key) {
            case "存货" -> Set.of("存货", "存货名称", "产品名称", "材料名称");
            case "材料名称" -> Set.of("材料名称", "存货", "存货名称");
            case "产品名称" -> Set.of("产品名称", "存货", "存货名称");
            case "工人" -> Set.of("工人", "工人名称");
            case "工人名称" -> Set.of("工人名称", "工人");
            case "班组" -> Set.of("班组", "班组名称");
            case "班组名称" -> Set.of("班组名称", "班组");
            default -> Set.of(key);
        };
        List<Object> values = new ArrayList<>();
        for (String alias : aliases) if (row.containsKey(alias)) values.add(row.get(alias));
        return values;
    }

    private List<Doc> docs(String panelCode) {
        List<FormData> rows = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, panelCode)
                .orderByAsc(FormData::getCreateTime)
                .orderByAsc(FormData::getId));
        List<Doc> out = new ArrayList<>();
        for (FormData row : rows) out.add(new Doc(row, parse(row.getData()), parse(row.getDetailData())));
        return out;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> detailRows(Doc doc, String key) {
        Object value = doc.detail().get(key);
        if (value instanceof List<?>) return (List<Map<String, Object>>) value;
        return List.of();
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> allDetailRows(Doc doc) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Object value : doc.detail().values()) {
            if (value instanceof List<?>) out.addAll((List<Map<String, Object>>) value);
        }
        return out;
    }

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(value, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }

    private boolean effective(Doc doc) {
        return EFFECTIVE_STATUSES.contains(doc.entity().getStatus());
    }

    private String documentNo(Doc doc) {
        String configured = firstText(doc.head(), "单据编号", "锭号", "期初余额单号", "编号");
        return configured.isBlank() ? text(doc.entity().getFormNo()) : configured;
    }

    private String documentType(String source) {
        return switch (source) {
            case "PURCHASE_IN" -> "采购入库单";
            case "FINISH_IN" -> "产成品入库单";
            case "OTHER_IN" -> "其他入库单";
            case "SALE_OUT" -> "销售出库单";
            case "MATERIAL_OUT" -> "材料出库单";
            case "OTHER_OUT" -> "其他出库单";
            default -> source;
        };
    }

    private Object value(Doc doc, String key) {
        return doc.head().getOrDefault(key, "");
    }

    private String created(Doc doc) {
        Object configured = doc.head().get("创建时间");
        if (configured != null && !text(configured).isBlank()) return text(configured);
        LocalDateTime time = doc.entity().getCreateTime();
        return time == null ? "" : time.format(DATE_TIME);
    }

    private Comparator<Map<String, Object>> dateDesc() {
        return Comparator.comparing((Map<String, Object> row) -> text(first(row, row, "单据日期", "创建时间"))).reversed();
    }

    private Object first(Map<String, Object> primary, Map<String, Object> fallback, String... keys) {
        for (String key : keys) {
            Object value = primary.get(key);
            if (value != null && !text(value).isBlank()) return value;
        }
        for (String key : keys) {
            Object value = fallback.get(key);
            if (value != null && !text(value).isBlank()) return value;
        }
        return "";
    }

    private String firstText(Map<String, Object> source, String... keys) {
        for (String key : keys) {
            Object value = source.get(key);
            if (value != null && !text(value).isBlank()) return text(value);
        }
        return "";
    }

    private String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private BigDecimal number(Object value) {
        if (value instanceof BigDecimal decimal) return decimal;
        if (value instanceof Number number) return BigDecimal.valueOf(number.doubleValue());
        try {
            String raw = text(value).replace(",", "").replace("%", "");
            return raw.isBlank() ? BigDecimal.ZERO : new BigDecimal(raw);
        } catch (Exception ignored) {
            return BigDecimal.ZERO;
        }
    }

    private BigDecimal divide(BigDecimal amount, BigDecimal quantity) {
        if (quantity == null || quantity.signum() == 0) return BigDecimal.ZERO;
        return amount.divide(quantity, 4, RoundingMode.HALF_UP).stripTrailingZeros();
    }

    private BigDecimal percent(BigDecimal numerator, BigDecimal denominator) {
        if (denominator == null || denominator.signum() == 0) return BigDecimal.ZERO;
        return numerator.multiply(BigDecimal.valueOf(100)).divide(denominator, 2, RoundingMode.HALF_UP).stripTrailingZeros();
    }

    private LocalDate date(Object value) {
        String raw = text(value);
        if (raw.length() >= 10) raw = raw.substring(0, 10);
        try {
            return raw.isBlank() ? null : LocalDate.parse(raw);
        } catch (Exception ignored) {
            return null;
        }
    }

    private void add(Map<String, Object> row, String key, Object value) {
        row.put(key, number(row.get(key)).add(number(value)));
    }

    private String join(Object... values) {
        List<String> parts = new ArrayList<>();
        for (Object value : values) parts.add(text(value));
        return String.join("|", parts);
    }

    private Map<String, Object> row(Object... values) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i + 1 < values.length; i += 2) out.put(String.valueOf(values[i]), values[i + 1]);
        return out;
    }
}