package com.mes.config;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.cors.CorsConfiguration;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;

class SecurityConfigTest {

    @Test
    void corsUsesExplicitOriginsWithoutCredentials() {
        SecurityConfig config = config("http://localhost:5173,https://mes.example.com");

        CorsConfiguration cors = config.corsConfigurationSource()
                .getCorsConfiguration(new MockHttpServletRequest("GET", "/api/auth/login"));

        assertEquals(java.util.List.of("http://localhost:5173", "https://mes.example.com"),
                cors.getAllowedOrigins());
        assertFalse(Boolean.TRUE.equals(cors.getAllowCredentials()));
    }

    @Test
    void corsRejectsWildcardOrigin() {
        SecurityConfig wildcard = config("*");

        assertThrows(IllegalStateException.class, wildcard::corsConfigurationSource);
    }

    private SecurityConfig config(String origins) {
        SecurityConfig config = new SecurityConfig(mock(JwtAuthFilter.class));
        ReflectionTestUtils.setField(config, "allowedOrigins", origins);
        return config;
    }
}
