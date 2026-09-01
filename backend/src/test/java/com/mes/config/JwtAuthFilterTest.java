package com.mes.config;

import com.mes.entity.SysUser;
import com.mes.mapper.SysUserMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.security.core.context.SecurityContextHolder;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class JwtAuthFilterTest {

    @Mock private JwtUtil jwtUtil;
    @Mock private SysUserMapper userMapper;
    @Mock private HttpServletRequest request;
    @Mock private HttpServletResponse response;
    @Mock private FilterChain chain;

    @AfterEach
    void clearSecurityContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void rejectsAnOtherwiseValidTokenAfterTheUserIsDisabled() throws Exception {
        configureToken();
        when(userMapper.selectOne(any())).thenReturn(user(0));

        new JwtAuthFilter(jwtUtil, userMapper).doFilterInternal(request, response, chain);

        assertNull(SecurityContextHolder.getContext().getAuthentication());
        verify(chain).doFilter(request, response);
    }

    @Test
    void authenticatesAnEnabledUser() throws Exception {
        configureToken();
        when(userMapper.selectOne(any())).thenReturn(user(1));

        new JwtAuthFilter(jwtUtil, userMapper).doFilterInternal(request, response, chain);

        assertEquals("worker", SecurityContextHolder.getContext().getAuthentication().getName());
        verify(chain).doFilter(request, response);
    }

    private void configureToken() {
        when(request.getHeader("Authorization")).thenReturn("Bearer token");
        when(jwtUtil.validate("token")).thenReturn(true);
        when(jwtUtil.parseUserName("token")).thenReturn("worker");
    }

    private SysUser user(int enabled) {
        SysUser user = new SysUser();
        user.setUserName("worker");
        user.setEnabled(enabled);
        return user;
    }
}
