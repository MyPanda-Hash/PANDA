package com.mes.openapi;

/** 开放接口门禁失败，携带 HTTP 状态码由控制器翻译。 */
public class OpenApiException extends RuntimeException {

    private final int status;

    public OpenApiException(int status, String message) {
        super(message);
        this.status = status;
    }

    public int getStatus() {
        return status;
    }
}
