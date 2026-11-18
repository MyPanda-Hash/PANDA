package com.mes.config;

import com.mes.mapper.SysUserMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Collections;

@Component
public class JwtAuthFilter extends OncePerRequestFilter {

    private final JwtUtil jwtUtil;
    private final SysUserMapper userMapper;

    public JwtAuthFilter(JwtUtil jwtUtil, SysUserMapper userMapper) {
        this.jwtUtil = jwtUtil;
        this.userMapper = userMapper;
    }

    /** 前端切账套时带的请求头；缺省时用 FactoryContext 的默认账套。 */
    private static final String FACTORY_HEADER = "X-Factory-Code";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        // 账套上下文必须在最外层设置、在 finally 清理：
        // 查询链路（Controller → Service → Mapper）任何一层都可能读它，
        // 而线程池复用意味着漏清一次就会把账套串到下一个无关请求。
        String factoryCode = request.getHeader(FACTORY_HEADER);
        FactoryContext.set(factoryCode);
        try {
            applyAuthentication(request);
            chain.doFilter(request, response);
        } finally {
            FactoryContext.clear();
        }
    }

    private void applyAuthentication(HttpServletRequest request) {
        String auth = request.getHeader("Authorization");
        if (auth != null && auth.startsWith("Bearer ")) {
            String token = auth.substring(7);
            if (jwtUtil.validate(token)) {
                String userName = jwtUtil.parseUserName(token);
                var user = userMapper.selectOne(
                        new com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper<com.mes.entity.SysUser>()
                                .eq(com.mes.entity.SysUser::getUserName, userName));
                if (user != null && (user.getEnabled() == null || user.getEnabled() != 0)) {
                    var authToken = new UsernamePasswordAuthenticationToken(userName, null, Collections.emptyList());
                    SecurityContextHolder.getContext().setAuthentication(authToken);
                }
            }
        }
    }
}
