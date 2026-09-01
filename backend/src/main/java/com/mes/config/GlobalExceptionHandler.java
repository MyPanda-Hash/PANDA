package com.mes.config;

import com.mes.dto.ApiResult;
import com.mes.ocr.OcrRateLimitException;
import com.mes.ocr.OcrServiceException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.multipart.MultipartException;
import org.springframework.web.multipart.MaxUploadSizeExceededException;
import org.springframework.web.multipart.support.MissingServletRequestPartException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(AccessDeniedException.class)
    public ApiResult<Void> handleAccessDenied(AccessDeniedException e) {
        return ApiResult.fail(403, e.getMessage());
    }

    @ExceptionHandler(MaxUploadSizeExceededException.class)
    public ApiResult<Void> handleMaxUploadSize(MaxUploadSizeExceededException e) {
        return ApiResult.fail(400, "图片不能超过10MB");
    }

    @ExceptionHandler(MissingServletRequestPartException.class)
    public ApiResult<Void> handleMissingPart(MissingServletRequestPartException e) {
        return ApiResult.fail(400, "image".equals(e.getRequestPartName())
                ? "请选择需要扫描的图片" : "上传请求缺少必要参数");
    }

    @ExceptionHandler(MissingServletRequestParameterException.class)
    public ApiResult<Void> handleMissingParameter(MissingServletRequestParameterException e) {
        return ApiResult.fail(400, "panelCode".equals(e.getParameterName())
                ? "面板编码不能为空" : "请求缺少必要参数");
    }

    @ExceptionHandler(MultipartException.class)
    public ApiResult<Void> handleMultipart(MultipartException e) {
        return ApiResult.fail(400, "图片上传请求格式不正确");
    }

    @ExceptionHandler(OcrServiceException.class)
    public ApiResult<Void> handleOcrService(OcrServiceException e) {
        return ApiResult.fail(503, e.getMessage());
    }

    @ExceptionHandler(OcrRateLimitException.class)
    public ApiResult<Void> handleOcrRateLimit(OcrRateLimitException e) {
        return ApiResult.fail(429, e.getMessage());
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
