package com.mes.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Base64;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 票据格式必须与 doc-nexus-ai 的 {@code app/services/mes_sso.py} 一致：
 * 这里用 Python 侧的校验算法反向验一遍，跨语言不一致会直接红。
 */
class AiSsoServiceTest {

    private static final String SECRET = "unit-test-secret";
    private static final Instant NOW = Instant.parse("2026-11-03T02:30:00Z");

    private AiSsoService service(String secret) {
        return new AiSsoService(new ObjectMapper(), secret, "http://localhost:18091", 60);
    }

    /** 照 mes_sso.py 的 verify_ticket 重写：base64url 解载荷 + 重算 HMAC + 校验到期。 */
    private JsonNode verifyLikePython(String ticket, String secret) throws Exception {
        String[] parts = ticket.split("\\.");
        assertEquals(2, parts.length, "票据必须是 body.signature 两段");
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        byte[] expected = mac.doFinal(parts[0].getBytes(StandardCharsets.US_ASCII));
        byte[] provided = Base64.getUrlDecoder().decode(parts[1]);
        assertArrayEqualsHex(expected, provided);
        byte[] payload = Base64.getUrlDecoder().decode(parts[0]);
        JsonNode node = new ObjectMapper().readTree(payload);
        assertTrue(node.get("exp").asLong() > 0, "票据必须带到期时间");
        return node;
    }

    private void assertArrayEqualsHex(byte[] expected, byte[] provided) {
        assertEquals(expected.length, provided.length, "签名长度不一致");
        for (int i = 0; i < expected.length; i++) {
            assertEquals(expected[i], provided[i], "签名第 " + i + " 字节不一致");
        }
    }

    @Test
    void 签发的票据能被Python侧算法验回身份() throws Exception {
        String ticket = service(SECRET).issueTicket("admin", "管理员", true, NOW);
        JsonNode payload = verifyLikePython(ticket, SECRET);

        assertEquals("admin", payload.get("sub").asText());
        assertEquals("管理员", payload.get("name").asText());
        assertEquals("admin", payload.get("role").asText());
        assertTrue(payload.get("admin").asBoolean());
        assertEquals(NOW.getEpochSecond(), payload.get("iat").asLong());
        assertEquals(NOW.getEpochSecond() + 60, payload.get("exp").asLong());
        assertFalse(payload.get("jti").asText().isBlank());
    }

    @Test
    void 非管理员角色写成user() throws Exception {
        String ticket = service(SECRET).issueTicket("zhangsan", "张三", false, NOW);
        JsonNode payload = verifyLikePython(ticket, "unit-test-secret");
        assertEquals("zhangsan", payload.get("sub").asText());
        assertEquals("user", payload.get("role").asText());
        assertFalse(payload.get("admin").asBoolean());
    }

    @Test
    void 每次签发的票据不相同() {
        AiSsoService s = service(SECRET);
        assertNotEquals(s.issueTicket("admin", "管理员", true, NOW), s.issueTicket("admin", "管理员", true, NOW));
    }

    @Test
    void 换密钥后签名对不上() {
        String ticket = service("other-secret").issueTicket("admin", "管理员", true, NOW);
        assertThrows(AssertionError.class, () -> verifyLikePython(ticket, SECRET));
    }

    @Test
    void 未配置密钥时不签发并给出明确错误() {
        AiSsoService s = service("");
        assertFalse(s.enabled());
        IllegalStateException ex = assertThrows(IllegalStateException.class,
                () -> s.issueTicket("admin", "管理员", true, NOW));
        assertTrue(ex.getMessage().contains("MES_AI_SSO_SECRET"));
    }

    @Test
    void 登录地址带上票据且票据被URL编码() {
        AiSsoService s = service(SECRET);
        String ticket = s.issueTicket("admin", "管理员", true, NOW);
        String url = s.ssoUrl(ticket);
        assertEquals("http://localhost:18091/api/auth/mes-sso?ticket=" + ticket, url);
        // base-url 结尾多写一个斜杠也不会拼出 //
        assertEquals("http://localhost:18091/api/auth/mes-sso?ticket=" + ticket,
                new AiSsoService(new ObjectMapper(), SECRET, "http://localhost:18091/", 60).ssoUrl(ticket));
    }

    @Test
    void 账号为空直接拒绝() {
        assertThrows(IllegalArgumentException.class,
                () -> service(SECRET).issueTicket(" ", "x", false, NOW));
    }
}
