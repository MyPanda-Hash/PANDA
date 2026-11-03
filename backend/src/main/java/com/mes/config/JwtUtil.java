package com.mes.config;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.SecretKey;
import java.nio.charset.StandardCharsets;
import java.util.Date;

@Component
public class JwtUtil {

    @Value("${mes.jwt.secret}")
    private String secret;

    @Value("${mes.jwt.expire-hours:24}")
    private long expireHours;

    @PostConstruct
    void validateConfiguration() {
        if (secret == null || secret.isBlank()) {
            throw new IllegalStateException("必须通过 MES_JWT_SECRET 配置 JWT 密钥");
        }
        if (secret.getBytes(StandardCharsets.UTF_8).length < 64) {
            throw new IllegalStateException("MES_JWT_SECRET 至少需要 64 字节，请使用密码学安全随机值");
        }
        if (expireHours <= 0) {
            throw new IllegalStateException("MES_JWT_EXPIRE_HOURS 必须大于 0");
        }
    }

    private SecretKey key() {
        return Keys.hmacShaKeyFor(secret.getBytes(StandardCharsets.UTF_8));
    }

    public String generateToken(String userName) {
        Date now = new Date();
        return Jwts.builder()
                .subject(userName)
                .issuedAt(now)
                .expiration(new Date(now.getTime() + expireHours * 3600_000L))
                .signWith(key())
                .compact();
    }

    public String parseUserName(String token) {
        Claims claims = Jwts.parser().verifyWith(key()).build()
                .parseSignedClaims(token).getPayload();
        return claims.getSubject();
    }

    public boolean validate(String token) {
        try {
            parseUserName(token);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
