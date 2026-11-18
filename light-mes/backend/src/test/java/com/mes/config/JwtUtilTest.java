package com.mes.config;

import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JwtUtilTest {

    @Test
    void rejectsMissingOrWeakSecret() {
        JwtUtil missing = util("", 24);
        IllegalStateException missingError = assertThrows(IllegalStateException.class, missing::validateConfiguration);
        assertTrue(missingError.getMessage().contains("MES_JWT_SECRET"));

        JwtUtil weak = util("too-short", 24);
        assertThrows(IllegalStateException.class, weak::validateConfiguration);
    }

    @Test
    void acceptsAStrongSecretAndValidExpiry() {
        JwtUtil valid = util("a".repeat(64), 24);

        assertDoesNotThrow(valid::validateConfiguration);
    }

    @Test
    void rejectsNonPositiveExpiry() {
        JwtUtil invalid = util("a".repeat(64), 0);

        assertThrows(IllegalStateException.class, invalid::validateConfiguration);
    }

    private JwtUtil util(String secret, long expireHours) {
        JwtUtil util = new JwtUtil();
        ReflectionTestUtils.setField(util, "secret", secret);
        ReflectionTestUtils.setField(util, "expireHours", expireHours);
        return util;
    }
}
