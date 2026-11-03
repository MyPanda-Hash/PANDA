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
 * 齐套分析的查询入口：按 MPS 计划行逐产品判断能否开工。
 * 复用 MrpQueryService 的 BOM 与库存装载逻辑，避免两套口径。
 */
@Service
public class KitQueryService {

    private final FormDataMapper formMapper;
    private final ObjectMapper json;
    private final KitAnalyzeService kitService;
    private final MpsQueryService mpsQueryService;
    private final StockBalanceService stockBalanceService;

    public KitQueryService(FormDataMapper formMapper, ObjectMapper json,
                           KitAnalyzeService kitService,
                           MpsQueryService mpsQueryService,
                           StockBalanceService stockBalanceService) {
        this.formMapper = formMapper;
        this.json = json;
        this.kitService = kitService;
        this.mpsQueryService = mpsQueryService;
        this.stockBalanceService = stockBalanceService;
    }

    /**
     * 齐套明细：每个产品的每个直接子件一行，带缺料标记。
     * 前端按「齐套状态」列即可筛出不能开工的项。
     */
    public List<Map<String, Object>> analyze() {
        Map<String, List<MrpExplosionService.BomLine>> bom = loadBom();
        Map<String, BigDecimal> stock = loadStock();

        List<Map<String, Object>> out = new ArrayList<>();
        for (Map<String, Object> plan : mpsQueryService.planRows()) {
            String code = text(plan.get("存货编码"));
            if (code.isEmpty()) continue;
            BigDecimal demand = number(plan.get("待排产数量"));
            KitAnalyzeService.KitResult result = kitService.analyze(code, demand, bom, stock);
            for (KitAnalyzeService.KitLine line : result.lines()) {
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("成品编码", code);
                row.put("成品名称", text(plan.get("存货名称")));
                row.put("需求数量", demand);
                row.put("子件编码", line.itemCode());
                row.put("子件名称", line.itemName());
                row.put("需要数量", line.requiredQty());
                row.put("现存量", line.stockQty());
                row.put("缺料数量", line.shortageQty());
                row.put("满足率%", line.fulfillRate());
                row.put("齐套状态", result.ready() ? "齐套" : "缺料");
                row.put("客户", text(plan.get("客户")));
                row.put("需求交期", text(plan.get("需求交期")));
                out.add(row);
            }
        }
        return out;
    }

    /** 从 BOM 面板读已审核 BOM。 */
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
                bom.computeIfAbsent(parent, k -> new ArrayList<>())
                        .add(new MrpExplosionService.BomLine(parent, child,
                                text(row.get("子件名称")), unitQty, number(row.get("损耗率%"))));
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
