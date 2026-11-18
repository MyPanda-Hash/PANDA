package com.mes.service;

import com.mes.entity.StockBalance;
import com.mes.mapper.StockBalanceMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 库存余额聚合：把出入库事务折算成四维余额（存货 + 仓库 + 库位 + 批次）。
 * 单厂自用，不引入组织维度。余额表是库存数量的唯一权威口径。
 */
@Service
public class StockBalanceService {

    /**
     * 一条库存事务：方向 IN/OUT，数量与金额取绝对值，方向决定正负。
     * itemCode 可能为空（历史单据只填名称），此时聚合键回退到 itemName，
     * 否则所有空编码的存货会被合并成同一行。
     */
    public record Movement(String itemCode, String itemName, String spec, String unit,
                           String warehouse, String location, String batchNo,
                           String direction, BigDecimal quantity, BigDecimal amount) {}

    private final StockBalanceMapper mapper;

    public StockBalanceService(StockBalanceMapper mapper) {
        this.mapper = mapper;
    }

    /** 纯函数聚合，便于单测；库位/批次空白统一归一为空串，避免同货两行。 */
    public List<StockBalance> aggregate(List<Movement> movements) {
        Map<String, StockBalance> balances = new LinkedHashMap<>();
        for (Movement movement : movements) {
            String code = nullToEmpty(movement.itemCode());
            String name = nullToEmpty(movement.itemName());
            // 编码缺失时用名称参与唯一键，避免不同存货因空编码被合并
            String identity = code.isEmpty() ? "#" + name : code;
            String key = String.join("|", identity,
                    nullToEmpty(movement.warehouse()),
                    nullToEmpty(movement.location()),
                    nullToEmpty(movement.batchNo()));
            StockBalance balance = balances.computeIfAbsent(key, ignored -> {
                StockBalance created = new StockBalance();
                created.setItemCode(code);
                created.setItemName(name);
                created.setSpec(nullToEmpty(movement.spec()));
                created.setUnit(nullToEmpty(movement.unit()));
                created.setWarehouse(nullToEmpty(movement.warehouse()));
                created.setLocation(nullToEmpty(movement.location()));
                created.setBatchNo(nullToEmpty(movement.batchNo()));
                created.setQuantity(BigDecimal.ZERO);
                created.setLockedQuantity(BigDecimal.ZERO);
                created.setAmount(BigDecimal.ZERO);
                return created;
            });
            BigDecimal sign = "OUT".equals(movement.direction()) ? BigDecimal.valueOf(-1) : BigDecimal.ONE;
            balance.setQuantity(balance.getQuantity().add(nz(movement.quantity()).multiply(sign)));
            balance.setAmount(balance.getAmount().add(nz(movement.amount()).multiply(sign)));
        }
        LocalDateTime now = LocalDateTime.now();
        List<StockBalance> out = new ArrayList<>(balances.values());
        for (StockBalance balance : out) balance.setUpdateTime(now);
        return out;
    }

    /** 全量重建余额表：先清后插，供期初与重算使用。 */
    public int rebuild(List<Movement> movements) {
        List<StockBalance> balances = aggregate(movements);
        mapper.delete(null);
        for (StockBalance balance : balances) mapper.insert(balance);
        return balances.size();
    }

    /** 查询当前余额，供库存状况表与单据现存量共用同一口径。 */
    public List<StockBalance> list() {
        return mapper.selectList(null);
    }

    private static BigDecimal nz(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }
}
