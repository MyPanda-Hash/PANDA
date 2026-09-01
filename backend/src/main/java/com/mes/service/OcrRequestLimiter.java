package com.mes.service;

import com.mes.ocr.OcrRateLimitException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.LongSupplier;

@Component
public class OcrRequestLimiter {

    private static final long WINDOW_MILLIS = 60_000L;

    private final int requestsPerMinute;
    private final Semaphore concurrentRequests;
    private final LongSupplier clock;
    private final ConcurrentHashMap<String, SlidingWindow> userWindows = new ConcurrentHashMap<>();
    private final AtomicInteger cleanupTicker = new AtomicInteger();

    @Autowired
    public OcrRequestLimiter(
            @Value("${mes.ocr.requests-per-minute:10}") int requestsPerMinute,
            @Value("${mes.ocr.max-concurrent:4}") int maxConcurrent) {
        this(requestsPerMinute, maxConcurrent, System::currentTimeMillis);
    }

    OcrRequestLimiter(int requestsPerMinute, int maxConcurrent, LongSupplier clock) {
        if (requestsPerMinute <= 0) throw new IllegalArgumentException("OCR 每分钟请求数必须大于 0");
        if (maxConcurrent <= 0) throw new IllegalArgumentException("OCR 最大并发数必须大于 0");
        this.requestsPerMinute = requestsPerMinute;
        this.concurrentRequests = new Semaphore(maxConcurrent, true);
        this.clock = clock;
    }

    public Permit acquire(String userName) {
        if (!concurrentRequests.tryAcquire()) {
            throw new OcrRateLimitException("OCR 服务正忙，请稍后再试");
        }

        boolean allowed = false;
        try {
            long now = clock.getAsLong();
            evictIdleWindows(now);
            while (true) {
                SlidingWindow window = userWindows.computeIfAbsent(userName, ignored -> new SlidingWindow(now));
                synchronized (window) {
                    if (userWindows.get(userName) != window) continue;
                    discardExpired(window, now);
                    if (window.requests.size() >= requestsPerMinute) {
                        throw new OcrRateLimitException("扫描过于频繁，请稍后再试");
                    }
                    window.requests.addLast(now);
                    window.lastSeen = now;
                }
                break;
            }
            allowed = true;
            return new Permit(concurrentRequests);
        } finally {
            if (!allowed) concurrentRequests.release();
        }
    }

    private void discardExpired(SlidingWindow window, long now) {
        long cutoff = now - WINDOW_MILLIS;
        while (!window.requests.isEmpty() && window.requests.peekFirst() <= cutoff) {
            window.requests.removeFirst();
        }
    }

    private void evictIdleWindows(long now) {
        if ((cleanupTicker.incrementAndGet() & 127) != 0) return;
        userWindows.entrySet().removeIf(entry -> {
            SlidingWindow window = entry.getValue();
            synchronized (window) {
                discardExpired(window, now);
                return window.requests.isEmpty() && now - window.lastSeen >= WINDOW_MILLIS;
            }
        });
    }

    private static final class SlidingWindow {
        private final Deque<Long> requests = new ArrayDeque<>();
        private long lastSeen;

        private SlidingWindow(long now) {
            this.lastSeen = now;
        }
    }

    public static final class Permit implements AutoCloseable {

        private final Semaphore semaphore;
        private final AtomicBoolean closed = new AtomicBoolean(false);

        private Permit(Semaphore semaphore) {
            this.semaphore = semaphore;
        }

        @Override
        public void close() {
            if (closed.compareAndSet(false, true)) semaphore.release();
        }
    }
}
