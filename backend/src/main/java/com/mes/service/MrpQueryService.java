package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.mes.entity.FormData;
import com.mes.entity.StockBalance;
import com.mes.mapper.FormDataMapper;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * MRP 运算入口：从 MPS 计划行取需求 → 读 BOM → 递归展开 → 扣库存 → 采购建议。
 *
 * 与现有 PU_REQ_ANALYSIS 的区别：本服务按 BOM 实时展开，
 * 不依赖生产加工单里已展开的材料明细。
 */
@Service
public class MrpQueryService {

    private static final int MAX_DEPTH = 8;

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final MrpExplosionService explosionService;
    private final MpsQueryService mpsQueryService;
    private final StockBalanceService stockBalanceService;

    public MrpQueryService(FormDataMapper formMapper, ObjectMapper json,
                           MrpExplosionService explosionService,
                           MpsQueryService mpsQueryService,
                           StockBalanceService stockBalanceService) {
        this.formMapper = formMapper;
        this.json = json;
        this.explosionService = explosionService;
        this.mpsQueryService = mpsQueryService;
        this.stockBalanceService = stockBalanceService;
    }

    /** MRP 运算结果：按物料汇总的采购/自制建议。 */
    public List<Map<String, Object>> run() {
        Map<String, BigDecimal> demand = new LinkedHashMap<>();
        for (Map<String, Object> row : mpsQueryService.planRows()) {
            String code = text(row.get("存货编码"));
            if (code.isEmpty()) continue;
            demand.merge(code, number(row.get("待排产数量")), BigDecimal::add);
        }
        if (demand.isEmpty()) return List.of();

        MrpExplosionService.ExplosionResult result = explosionService.explodeDetailed(
                demand, loadBom(), MAX_DEPTH, loadStock());

        List<Map<String, Object>> out = new ArrayList<>();
        for (MrpExplosionService.Requirement req : result.requirements()) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("物料编码", req.itemCode());
            row.put("物料名称", req.itemName());
            row.put("供应方式", req.supplyType());
            row.put("毛需求", req.grossQty());
            row.put("现存量", req.stockQty());
            row.put("净需求", req.netQty());
            row.put("建议数量", req.netQty());
            row.put("BOM层级", req.level());
            out.add(row);
        }
        return out;
    }

    /** 从 BOM 面板读已审核 BOM，构造 父件 → 子件行 的索引。 */
    @SuppressWarnings("unchecked")
    private Map<String, List<MrpExplosionService.BomLine>> loadBom() {
        Map<String, List<MrpExplosionService.BomLine>> bom = new LinkedHashMap<>();
        List<FormData> rows = formMapper.selectList(new LambdaQueryWrapper<FormData>()
                .eq(FormData::getPanelCode, "BOM")
                .eq(FormData::getStatus, "已审核"));
        for (FormData fd : rows) {
            Map<String, Object> detail = parse(fd.getDetailData());
            Object children = detail.get("children");
            if (!(children instanceof List<?> list)) continue;
            for (Object item : list) {
                if (!(item instanceof Map<?, ?> raw)) continue;
                Map<String, Object> row = (Map<String, Object>) raw;
                String parent = text(row.get("父件编码"));
                String child = text(row.get("子件编码"));
                if (parent.isEmpty() || child.isEmpty()) continue;
                BigDecimal unitQty = number(row.get("定额数量"));
                if (unitQty.signum() <= 0) continue;
                BigDecimal scrap = number(row.get("损耗率%"));
                bom.computeIfAbsent(parent, k -> new ArrayList<>())
                        .add(new MrpExplosionService.BomLine(parent, child,
                                text(row.get("子件名称")), unitQty, scrap));
            }
        }
        return bom;
    }

    /** 现存量：按存货编码汇总所有仓库。 */
    private Map<String, BigDecimal> loadStock() {
        Map<String, BigDecimal> stock = new LinkedHashMap<>();
        if (stockBalanceService == null) return stock;
        for (StockBalance balance : stockBalanceService.list()) {
            String code = text(balance.getItemCode());
            String name = text(balance.getItemName());
            String key = code.isEmpty() ? name : code;
            if (key.isEmpty()) continue;
            BigDecimal qty = balance.getQuantity() == null ? BigDecimal.ZERO : balance.getQuantity();
            stock.merge(key, qty, BigDecimal::add);
        }
        return stock;
    }

    private Map<String, Object> parse(String value) {
        if (value == null || value.isBlank()) return new LinkedHashMap<>();
        try {
            return json.readValue(value, new TypeReference<Map<String, Object>>() {});
        } catch (Exception ignored) {
            return new LinkedHashMap<>();
        }
    }

    private static String text(Object value) {
        return value == null ? "" : String.valueOf(value).trim();
    }

    private static BigDecimal number(Object value) {
        if (value == null) return BigDecimal.ZERO;
        if (value instanceof BigDecimal d) return d;
        if (value instanceof Number n) return new BigDecimal(n.toString());
        try {
            return new BigDecimal(String.valueOf(value).trim());
        } catch (NumberFormatException ignored) {
            return BigDecimal.ZERO;
        }
    }
}
