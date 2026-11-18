package com.mes.service;

import com.mes.ocr.OcrRateLimitException;
import org.junit.jupiter.api.Test;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class OcrRequestLimiterTest {

    @Test
    void canBeCreatedByTheSpringContainer() {
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            context.register(OcrRequestLimiter.class);
            context.refresh();
            assertNotNull(context.getBean(OcrRequestLimiter.class));
        }
    }

    @Test
    void limitsEachUserWithinAMinuteAndResetsTheNextWindow() {
        AtomicLong clock = new AtomicLong(0);
        OcrRequestLimiter limiter = new OcrRequestLimiter(2, 2, clock::get);

        limiter.acquire("worker").close();
        limiter.acquire("worker").close();
        assertThrows(OcrRateLimitException.class, () -> limiter.acquire("worker"));

        clock.set(60_000L);
        assertDoesNotThrow(() -> limiter.acquire("worker").close());
    }

    @Test
    void enforcesTheLimitAcrossFixedMinuteBoundaries() {
        AtomicLong clock = new AtomicLong(1);
        OcrRequestLimiter limiter = new OcrRequestLimiter(2, 2, clock::get);

        limiter.acquire("worker").close();
        limiter.acquire("worker").close();
        clock.set(60_000L);
        assertThrows(OcrRateLimitException.class, () -> limiter.acquire("worker"));

        clock.set(60_001L);
        assertDoesNotThrow(() -> limiter.acquire("worker").close());
    }

    @Test
    void capsConcurrentCloudWorkAndReleasesPermitAfterCompletion() {
        OcrRequestLimiter limiter = new OcrRequestLimiter(10, 1, System::currentTimeMillis);

        OcrRequestLimiter.Permit first = limiter.acquire("worker-a");
        assertThrows(OcrRateLimitException.class, () -> limiter.acquire("worker-b"));
        first.close();

        assertDoesNotThrow(() -> limiter.acquire("worker-b").close());
    }

    @Test
    void rejectsInvalidLimitsInsteadOfSilentlyChangingThem() {
        assertThrows(IllegalArgumentException.class,
                () -> new OcrRequestLimiter(0, 1, System::currentTimeMillis));
        assertThrows(IllegalArgumentException.class,
                () -> new OcrRequestLimiter(1, 0, System::currentTimeMillis));
    }
}
