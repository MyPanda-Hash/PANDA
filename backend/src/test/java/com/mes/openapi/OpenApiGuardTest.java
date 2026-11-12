package com.mes.openapi;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * 门禁必须是「失败即拒绝」：没开、没令牌、令牌错、来源不对、刷太快，一律拒绝。
 */
class OpenApiGuardTest {

    private OpenApiGuard guard(String token, String ips, int limit) {
        return new OpenApiGuard(true, token, ips, limit);
    }

    @Test
    void disabled_api_is_not_reachable() {
        OpenApiGuard disabled = new OpenApiGuard(false, "secret", "127.0.0.1", 60);
        OpenApiException error = assertThrows(OpenApiException.class,
                () -> disabled.check("secret", "127.0.0.1"));
        assertEquals(404, error.getStatus());
    }

    @Test
    void empty_configured_token_fails_closed() {
        OpenApiGuard noToken = guard("  ", "127.0.0.1", 60);
        OpenApiException error = assertThrows(OpenApiException.class,
                () -> noToken.check("", "127.0.0.1"));
        assertEquals(503, error.getStatus());
    }

    @Test
    void missing_or_wrong_token_is_401() {
        OpenApiGuard g = guard("s3cret-token", "127.0.0.1", 60);
        assertEquals(401, assertThrows(OpenApiException.class, () -> g.check(null, "127.0.0.1")).getStatus());
        assertEquals(401, assertThrows(OpenApiException.class, () -> g.check(" ", "127.0.0.1")).getStatus());
        assertEquals(401, assertThrows(OpenApiException.class, () -> g.check("s3cret-tokeX", "127.0.0.1")).getStatus());
    }

    @Test
    void foreign_ip_is_rejected_even_with_valid_token() {
        OpenApiGuard g = guard("s3cret-token", "127.0.0.1", 60);
        assertEquals(403, assertThrows(OpenApiException.class,
                () -> g.check("s3cret-token", "10.1.2.3")).getStatus());
    }

    @Test
    void ipv4_with_port_and_ipv6_loopback_are_accepted() {
        OpenApiGuard g = guard("s3cret-token", "127.0.0.1,::1", 60);
        g.check("s3cret-token", "127.0.0.1:54321");
        g.check("s3cret-token", "::1");
        g.check("s3cret-token", "[::1]:8080");
    }

    @Test
    void rate_limit_blocks_after_quota() {
        OpenApiGuard g = guard("s3cret-token", "127.0.0.1", 2);
        g.check("s3cret-token", "127.0.0.1");
        g.check("s3cret-token", "127.0.0.1");
        assertEquals(429, assertThrows(OpenApiException.class,
                () -> g.check("s3cret-token", "127.0.0.1")).getStatus());
    }

    @Test
    void token_is_never_echoed_in_error_message() {
        OpenApiGuard g = guard("s3cret-token", "127.0.0.1", 60);
        OpenApiException error = assertThrows(OpenApiException.class,
                () -> g.check("wrong-guess", "127.0.0.1"));
        assertTrue(!error.getMessage().contains("s3cret"));
        assertTrue(!error.getMessage().contains("wrong-guess"));
    }
}
