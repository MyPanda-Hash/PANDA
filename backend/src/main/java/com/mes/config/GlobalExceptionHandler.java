package com.mes.config;

import com.mes.dto.ApiResult;
import com.mes.service.PanelxApiException;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import java.util.LinkedHashMap;
import java.util.Map;

@RestControllerAdvice
public class GlobalExceptionHandler {

    /** PanelX 平台调用失败：还原平台风格错误体（errorDescription/errorCode/error）+ HTTP 状态码 */
    @ExceptionHandler(PanelxApiException.class)
    public ResponseEntity<Map<String, Object>> handlePanelx(PanelxApiException e) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("errorDescription", e.getMessage());
        body.put("level", "ERROR");
        body.put("errorCode", e.getErrorCode());
        body.put("error", "invalid_request");
        return ResponseEntity.status(e.getHttpStatus()).body(body);
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ApiResult<Void> handleIllegalArgument(IllegalArgumentException e) {
        return ApiResult.fail(400, e.getMessage());
    }

    @ExceptionHandler(IllegalStateException.class)
    public ApiResult<Void> handleIllegalState(IllegalStateException e) {
        return ApiResult.fail(409, e.getMessage());
    }

    @ExceptionHandler(Exception.class)
    public ApiResult<Void> handleOther(Exception e) {
        return ApiResult.fail(500, e.getMessage() == null ? "服务器内部错误" : e.getMessage());
    }
}
