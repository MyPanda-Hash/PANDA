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

    /**
     * 账套上下文：过滤器要从 X-Factory-Code 取当前账套，并在请求结束后清理。
     *
     * <p>这条测试是补的回归保护：加账套隔离后过滤器多了一次 getHeader，
     * 原有测试没 stub 它，Mockito 严格模式直接把两个既有用例打红了。
     */
    @Test
    void setsFactoryContextDuringChainAndClearsItAfter() throws Exception {
        configureToken();
        when(request.getHeader("X-Factory-Code")).thenReturn("F02");
        when(userMapper.selectOne(any())).thenReturn(user(1));
        String[] seen = new String[1];
        org.mockito.Mockito.doAnswer(invocation -> {
            seen[0] = FactoryContext.current();
            return null;
        }).when(chain).doFilter(request, response);

        new JwtAuthFilter(jwtUtil, userMapper).doFilterInternal(request, response, chain);

        assertEquals("F02", seen[0], "链路执行期间应当读到请求头里的账套");
        assertEquals(FactoryContext.DEFAULT_CODE, FactoryContext.current(),
                "请求结束后必须清理，否则线程复用会把账套串到下一个请求");
    }

    private void configureToken() {
        when(request.getHeader("Authorization")).thenReturn("Bearer token");
        // 过滤器会读账套头；不 stub 会触发 Mockito 严格模式报错
        when(request.getHeader("X-Factory-Code")).thenReturn(null);
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
