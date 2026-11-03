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

    // HS256 要求密钥至少 256 bit；不满足直接拒绝启动，避免用弱密钥签发可伪造的 token
    @PostConstruct
    void requireStrongSecret() {
        int bits = secret == null ? 0 : secret.getBytes(StandardCharsets.UTF_8).length * 8;
        if (bits < 256) {
            throw new IllegalStateException("JWT 密钥未配置或强度不足（当前 " + bits
                    + " bit，HS256 至少需要 256 bit）。请设置环境变量 MES_JWT_SECRET 为至少 32 字节的随机串"
                    + "（例如 openssl rand -hex 32 的输出）。历史密钥已公开泄露，禁止复用。");
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
