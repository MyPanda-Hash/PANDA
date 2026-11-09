package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.mes.entity.StockBalance;
import com.mes.entity.StockTxn;
import com.mes.mapper.StockTxnMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 单据 → 库存事务：审核出入库单时把明细折算成 stock_txn 行，再触发余额重算。
 * 字段名与 ReportQueryService 的既有聚合保持一致，避免出现第二套口径。
 */
@Service
public class StockTxnService {

    /** 入库方向的面板：审核后增加库存。销售退货是客户退货回来，同样增加库存。 */
    private static final Set<String> INBOUND_PANELS = Set.of(
            "PURCHASE_IN", "FINISH_IN", "OTHER_IN", "OUTSOURCE_IN", "INIT_BALANCE", "SALE_RETURN");

    /** 出库方向的面板：审核后减少库存。采购退货是把货退回供应商，同样减少库存。 */
    private static final Set<String> OUTBOUND_PANELS = Set.of(
            "SALE_OUT", "MATERIAL_OUT", "OTHER_OUT", "PURCHASE_RETURN");

    /** 一条库存事务（方向为正数，由方向决定加减）。 */
    public record Txn(String panelCode, String formNo, int rowNo, String direction,
                      String itemCode, String itemName, String spec, String unit,
                      String warehouse, String location, String batchNo,
                      BigDecimal quantity, BigDecimal price, BigDecimal amount, LocalDate bizDate) {}

    private final StockTxnMapper txnMapper;
    private final StockBalanceService balanceService;

    public StockTxnService(StockTxnMapper txnMapper, StockBalanceService balanceService) {
        this.txnMapper = txnMapper;
        this.balanceService = balanceService;
    }

    /**
     * 该存货实际存放的仓库（数量最多的那个）；没有库存记录返回 null。
     *
     * <p>用于加工单材料明细自动带出「预出仓库」：此前固定写「原料仓」，
     * 辅料仓的料被从原料仓出，直接出成负库存。
     */
    public String warehouseOfItem(String itemCode, String itemName) {
        String code = itemCode == null ? "" : itemCode.trim();
        String name = itemName == null ? "" : itemName.trim();
        String best = null;
        BigDecimal bestQty = BigDecimal.ZERO;
        for (StockBalance balance : balanceService.list()) {
            boolean codeMatch = !code.isEmpty()
                    ? code.equals(balance.getItemCode() == null ? "" : balance.getItemCode().trim())
                    : !name.isEmpty() && name.equals(balance.getItemName() == null ? "" : balance.getItemName().trim());
            if (!codeMatch) continue;
            String warehouse = balance.getWarehouse() == null ? "" : balance.getWarehouse().trim();
            if (warehouse.isEmpty()) continue;
            BigDecimal qty = balance.getQuantity() == null ? BigDecimal.ZERO : balance.getQuantity();
            if (qty.compareTo(bestQty) > 0) {
                bestQty = qty;
                best = warehouse;
            }
        }
        return best;
    }

    /**
     * 某存货在某仓库的现存量；无记录返回 0。
     *
     * <p>供生单前做可用量校验用。编码为空的历史单据回退按名称匹配，
     * 与 {@link StockBalanceService#aggregate} 的唯一键口径保持一致。
     */
    public BigDecimal quantityOf(String itemCode, String itemName, String warehouse) {
        String code = itemCode == null ? "" : itemCode.trim();
        String name = itemName == null ? "" : itemName.trim();
        String wh = warehouse == null ? "" : warehouse.trim();
        BigDecimal sum = BigDecimal.ZERO;
        for (StockBalance balance : balanceService.list()) {
            boolean codeMatch = !code.isEmpty()
                    ? code.equals(balance.getItemCode() == null ? "" : balance.getItemCode().trim())
                    : !name.isEmpty() && name.equals(balance.getItemName() == null ? "" : balance.getItemName().trim());
            if (!codeMatch) continue;
            if (!wh.isEmpty() && !wh.equals(balance.getWarehouse() == null ? "" : balance.getWarehouse().trim())) continue;
            sum = sum.add(balance.getQuantity() == null ? BigDecimal.ZERO : balance.getQuantity());
        }
        return sum;
    }

    /**
     * 结存平均成本（金额 / 数量），可限定仓库；没有结存返回 0。
     *
     * <p>销售出库按出库时刻的结存均价取成本价，产成品入库按材料结存均价折材料成本，
     * 两边共用同一个口径，避免成本台账出现两套数。
     */
    public BigDecimal averageCostOf(String itemCode, String itemName, String warehouse) {
        if (balanceService == null) return BigDecimal.ZERO;
        String code = itemCode == null ? "" : itemCode.trim();
        String name = itemName == null ? "" : itemName.trim();
        String wh = warehouse == null ? "" : warehouse.trim();
        BigDecimal qty = BigDecimal.ZERO;
        BigDecimal amount = BigDecimal.ZERO;
        for (StockBalance balance : balanceService.list()) {
            boolean codeMatch = !code.isEmpty()
                    ? code.equals(balance.getItemCode() == null ? "" : balance.getItemCode().trim())
                    : !name.isEmpty() && name.equals(balance.getItemName() == null ? "" : balance.getItemName().trim());
            if (!codeMatch) continue;
            if (!wh.isEmpty() && !wh.equals(balance.getWarehouse() == null ? "" : balance.getWarehouse().trim())) continue;
            qty = qty.add(balance.getQuantity() == null ? BigDecimal.ZERO : balance.getQuantity());
            amount = amount.add(balance.getAmount() == null ? BigDecimal.ZERO : balance.getAmount());
        }
        if (qty.signum() == 0) return BigDecimal.ZERO;
        return amount.divide(qty, 4, java.math.RoundingMode.HALF_UP);
    }

    /** 全部仓库的结存平均成本。 */
    public BigDecimal averageCostOf(String itemCode, String itemName) {
        return averageCostOf(itemCode, itemName, null);
    }

    /** 面板对应的出入库方向；非库存面板返回 null。 */
    public String directionOf(String panelCode) {
        if (panelCode == null) return null;
        if (INBOUND_PANELS.contains(panelCode)) return "IN";
        if (OUTBOUND_PANELS.contains(panelCode)) return "OUT";
        return null;
    }

    /** 把一张单据的表头与明细折算成事务行；非库存面板返回空列表。 */
    public List<Txn> buildTxns(String panelCode, String formNo, Map<String, Object> head,
                               Map<String, Object> detail) {
        String direction = directionOf(panelCode);
        if (direction == null) return List.of();
        List<Txn> out = new ArrayList<>();
        String headWarehouse = text(first(head, "仓库"));
        LocalDate bizDate = date(first(head, "单据日期"));
        for (Map<String, Object> row : allDetailRows(detail)) {
            String itemCode = text(first(row, "存货编码", "产品编码", "材料编码"));
            String itemName = text(first(row, "存货名称", "产品名称", "材料名称", "存货"));
            BigDecimal quantity = number(first(row, "实收数量", "数量", "入库数量", "出库数量"));
            if (quantity.signum() == 0) continue;
            BigDecimal price = number(first(row, "成本价", "单价", "主单价"));
            BigDecimal amount = number(first(row, "成本金额", "金额"));
            if (amount.signum() == 0 && price.signum() != 0) amount = quantity.multiply(price);
            String warehouse = text(first(row, "仓库"));
            if (warehouse.isBlank()) warehouse = headWarehouse;
            out.add(new Txn(panelCode, formNo, out.size() + 1, direction,
                    itemCode, itemName,
                    text(first(row, "规格型号", "材料规格")),
                    text(first(row, "计量单位", "主单位", "生产单位")),
                    warehouse,
                    text(first(row, "库位", "货位")),
                    text(first(row, "批次号", "批号")),
                    quantity.abs(), price, amount.abs(), bizDate));
        }
        return out;
    }

    /**
     * 审核后落账：先删除该单据的旧事务（支持弃审重审），再写入新事务，最后重算余额。
     * 余额表由全部事务聚合，因此每次重算都能自愈。
     */
    @Transactional
    public int post(String panelCode, String formNo, Map<String, Object> head, Map<String, Object> detail) {
        if (directionOf(panelCode) == null) return 0;
        txnMapper.delete(new LambdaQueryWrapper<StockTxn>()
                .eq(StockTxn::getPanelCode, panelCode)
                .eq(StockTxn::getFormNo, formNo));
        List<Txn> txns = buildTxns(panelCode, formNo, head, detail);
        for (Txn txn : txns) txnMapper.insert(toEntity(txn));
        rebuildBalance();
        return txns.size();
    }

    /** 弃审或删除时冲销该单据的事务。 */
    @Transactional
    public int reverse(String panelCode, String formNo) {
        if (directionOf(panelCode) == null) return 0;
        int removed = txnMapper.delete(new LambdaQueryWrapper<StockTxn>()
                .eq(StockTxn::getPanelCode, panelCode)
                .eq(StockTxn::getFormNo, formNo));
        if (removed > 0) rebuildBalance();
        return removed;
    }

    /** 由全部事务重建余额表。 */
    public int rebuildBalance() {
        List<StockTxn> all = txnMapper.selectList(null);
        List<StockBalanceService.Movement> movements = new ArrayList<>();
        for (StockTxn txn : all) {
            movements.add(new StockBalanceService.Movement(
                    txn.getItemCode(), txn.getItemName(), txn.getSpec(), txn.getUnit(),
                    txn.getWarehouse(), txn.getLocation(), txn.getBatchNo(),
                    txn.getDirection(), txn.getQuantity(), txn.getAmount()));
        }
        return balanceService.rebuild(movements);
    }

    private StockTxn toEntity(Txn txn) {
        StockTxn entity = new StockTxn();
        entity.setPanelCode(txn.panelCode());
        entity.setFormNo(txn.formNo());
        entity.setRowNo(txn.rowNo());
        entity.setDirection(txn.direction());
        entity.setItemCode(txn.itemCode());
        entity.setItemName(txn.itemName());
        entity.setSpec(txn.spec());
        entity.setUnit(txn.unit());
        entity.setWarehouse(txn.warehouse());
        entity.setLocation(txn.location());
        entity.setBatchNo(txn.batchNo());
        entity.setQuantity(txn.quantity());
        entity.setPrice(txn.price());
        entity.setAmount(txn.amount());
        entity.setBizDate(txn.bizDate());
        entity.setCreateTime(LocalDateTime.now());
        return entity;
    }

    // ---------- 取值助手（与 ReportQueryService 同名同义，避免两套口径） ----------

    private static Object first(Map<String, Object> row, String... keys) {
        for (String key : keys) {
            Object value = row.get(key);
            if (value != null && !String.valueOf(value).isBlank()) return value;
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> allDetailRows(Map<String, Object> detail) {
        List<Map<String, Object>> out = new ArrayList<>();
        if (detail == null) return out;
        for (Object value : detail.values()) {
            if (value instanceof List<?> list) {
                for (Object item : list) {
                    if (item instanceof Map<?, ?> map) out.add((Map<String, Object>) map);
                }
            }
        }
        return out;
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        if (value instanceof BigDecimal decimal) return decimal;
        if (value instanceof Number num) return new BigDecimal(num.toString());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return BigDecimal.ZERO;
        }
    }

    private static LocalDate date(Object value) {
        String text = text(value);
        if (text.isBlank()) return null;
        try {
            return LocalDate.parse(text.length() > 10 ? text.substring(0, 10) : text);
        } catch (Exception ignored) {
            return null;
        }
    }
}
