package com.mes.ocr;

public class OcrRateLimitException extends RuntimeException {

    public OcrRateLimitException(String message) {
        super(message);
    }
}
