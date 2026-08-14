package com.mes.controller;

import com.mes.dto.ApiResult;
import com.mes.entity.Factory;
import com.mes.mapper.FactoryMapper;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/base/factory")
public class FactoryController {

    private final FactoryMapper factoryMapper;

    public FactoryController(FactoryMapper factoryMapper) {
        this.factoryMapper = factoryMapper;
    }

    @GetMapping("/list")
    public ApiResult<List<Factory>> list() {
        return ApiResult.ok(factoryMapper.selectList(null));
    }
}
