package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.service.WmsTaskService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/**
 * 仓内作业接口：拣货 / 上架 / 复核 / 波次。
 *
 * <p>单据审核落库存（stock_txn），作业层只回答「现场怎么做的」：谁拣了多少、
 * 上架到哪个货位、复核结论是什么。任务由已审核单据同步而来，重复同步幂等。
 */
@RestController
@RequestMapping("/api/px/wms")
public class WmsController {

    private final WmsTaskService service;

    public WmsController(WmsTaskService service) {
        this.service = service;
    }

    /** 作业任务列表：type=PICK|PUTAWAY，status=待作业|部分完成|已完成。 */
    @GetMapping("/tasks")
    public ApiResult<List<Map<String, Object>>> tasks(@RequestParam(required = false) String type,
                                                     @RequestParam(required = false) String status) {
        return ApiResult.ok(service.list(type, status));
    }

    /** 作业看板汇总。 */
    @GetMapping("/summary")
    public ApiResult<Map<String, Object>> summary() {
        return ApiResult.ok(service.summary());
    }

    /** 从已审核单据同步任务（幂等，不覆盖现场已做的进度）。 */
    @PostMapping("/sync")
    public ApiResult<Map<String, Object>> sync(@RequestBody(required = false) Map<String, Object> body) {
        Object type = body == null ? null : body.get("类型");
        return ApiResult.ok(service.sync(type == null || String.valueOf(type).isBlank()
                ? WmsTaskService.PICK : String.valueOf(type)));
    }

    /** 确认作业：数量 + 目标货位（上架必填）+ 操作人。 */
    @PostMapping("/confirm")
    public ApiResult<Map<String, Object>> confirm(@RequestBody Map<String, Object> body) {
        Object id = body.get("id");
        if (id == null) throw new IllegalArgumentException("缺少任务 id");
        Object qty = body.get("数量");
        return ApiResult.ok(service.confirm(Long.valueOf(String.valueOf(id)),
                qty == null ? BigDecimal.ZERO : new BigDecimal(String.valueOf(qty)),
                body.get("货位") == null ? "" : String.valueOf(body.get("货位")),
                body.get("操作人") == null ? "" : String.valueOf(body.get("操作人"))));
    }

    /** 编波次：把某仓库的待拣任务归到一个波次号。 */
    @PostMapping("/wave")
    public ApiResult<Map<String, Object>> wave(@RequestBody(required = false) Map<String, Object> body) {
        Object warehouse = body == null ? null : body.get("仓库");
        return ApiResult.ok(service.assignWave(warehouse == null ? "" : String.valueOf(warehouse)));
    }

    /** 复核：按来源单号写复核结论（已复核 / 有差异）。 */
    @PostMapping("/review")
    public ApiResult<Map<String, Object>> review(@RequestBody Map<String, Object> body) {
        Object sourceNo = body.get("来源单号");
        if (sourceNo == null) throw new IllegalArgumentException("缺少来源单号");
        return ApiResult.ok(service.review(String.valueOf(sourceNo),
                body.get("结果") == null ? "" : String.valueOf(body.get("结果")),
                body.get("备注") == null ? "" : String.valueOf(body.get("备注"))));
    }
}
