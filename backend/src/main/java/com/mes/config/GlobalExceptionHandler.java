package com.mes.config;

import com.mes.dto.ApiResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    @ExceptionHandler(IllegalArgumentException.class)
    public ApiResult<Void> handleIllegalArgument(IllegalArgumentException e) {
        log.warn("业务校验失败: {}", e.getMessage());
        return ApiResult.fail(400, e.getMessage());
    }

    @ExceptionHandler(IllegalStateException.class)
    public ApiResult<Void> handleIllegalState(IllegalStateException e) {
        log.warn("业务状态拒绝: {}", e.getMessage());
        return ApiResult.fail(409, e.getMessage());
    }

    @ExceptionHandler(Exception.class)
    public ApiResult<Void> handleOther(Exception e) {
        // 内部异常细节（SQL 片段、表名等）不下发给客户端，仅记录服务端日志
        log.error("未处理异常", e);
        return ApiResult.fail(500, "服务器内部错误，请稍后重试或联系管理员");
    }
}
