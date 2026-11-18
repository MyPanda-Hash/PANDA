package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.service.EngChangeService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** 工程变更执行接口：只有「执行」一个写动作，单据本身走通用面板运行时。 */
@RestController
@RequestMapping("/api/px/eng")
public class EngChangeController {

    private final EngChangeService service;

    public EngChangeController(EngChangeService service) {
        this.service = service;
    }

    /** 执行已审核的工程变更单：目标物料清单/工艺路线版本 +1 并盖变更单号。 */
    @PostMapping("/apply")
    public ApiResult<Map<String, Object>> apply(@RequestBody Map<String, Object> body) {
        Object no = body.get("编号");
        if (no == null) throw new IllegalArgumentException("缺少变更单号");
        return ApiResult.ok(service.apply(String.valueOf(no)));
    }
}
