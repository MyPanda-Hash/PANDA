package com.mes.service;

/**
 * PanelX 平台调用异常：携带 HTTP 状态码与平台错误码，
 * 由 GlobalExceptionHandler 还原为平台风格的错误体
 * （{errorDescription, errorCode, error}），前端引擎可识别。
 */
public class PanelxApiException extends RuntimeException {

    private final int httpStatus;
    private final String errorCode;

    public PanelxApiException(int httpStatus, String message, String errorCode) {
        super(message);
        this.httpStatus = httpStatus;
        this.errorCode = errorCode == null ? "unknown" : errorCode;
    }

    public int getHttpStatus() { return httpStatus; }
    public String getErrorCode() { return errorCode; }

    /** HTTP 401 → 视为平台 token 失效，网关会重新登录并重试一次 */
    public boolean isAuthFailure() { return httpStatus == 401; }
}