package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.service.MpsService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 主生产计划接口：从销售订单生成计划期，再按计划期做物料需求汇总。
 *
 * <p>写接口只有两个动作，其余（列表、单据详情）走通用面板运行时。
 */
@RestController
@RequestMapping("/api/px/mps")
public class MpsController {

    private final MpsService service;

    public MpsController(MpsService service) {
        this.service = service;
    }

    /** 生成/追加计划期的主生产计划行（幂等：已计划的数量不重复计划）。 */
    @PostMapping("/generate")
    public ApiResult<Map<String, Object>> generate(@RequestBody(required = false) Map<String, Object> body) {
        Object period = body == null ? null : body.get("计划期");
        return ApiResult.ok(service.generate(period == null ? "" : String.valueOf(period)));
    }

    /** 物料需求汇总：MPS → 各来源订单分析 → 按物料聚合缺口与建议。 */
    @PostMapping("/mrp")
    public ApiResult<Map<String, Object>> mrp(@RequestBody Map<String, Object> body) {
        Object no = body.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少主生产计划编号");
        return ApiResult.ok(service.mrp(String.valueOf(no)));
    }

    /**
     * MRP 建议请购的物料 → 生成请购单草稿。
     *
     * <p>补的是链路断点：此前 MRP 只算不写，看到「建议请购」也没有入口变成单据。
     * 只生成草稿，审核仍由人走审批流（请购涉及预算责任）。
     */
    @PostMapping("/createPurchaseReq")
    public ApiResult<Map<String, Object>> createPurchaseReq(@RequestBody Map<String, Object> body) {
        Object no = body.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少主生产计划编号");
        String factory = body.get("账套") == null ? null : String.valueOf(body.get("账套"));
        String created = service.createPurchaseReq(String.valueOf(no), factory);
        Map<String, Object> out = new java.util.LinkedHashMap<>();
        if (created == null) {
            out.put("created", null);
            out.put("message", "没有「建议请购」且净需求大于 0 的物料，未生成请购单");
        } else {
            out.put("created", created);
            out.put("message", "已生成请购单草稿：" + created);
        }
        return ApiResult.ok(out);
    }
}
