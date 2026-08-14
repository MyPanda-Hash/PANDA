package com.mes.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * PanelX 平台网关配置（application.yml 的 mes.panelx 段）。
 * 后端代理模式下，网关用这里的演示凭据登录平台并缓存 JWT。
 */
@ConfigurationProperties(prefix = "mes.panelx")
public class PanelxProperties {

    /** 平台基础地址（以 / 结尾） */
    private String baseUrl = "https://demo.kwaidoo.com/VF_DEV/";

    /** 业务域 */
    private String busDomainCode = "SdkTest";

    /** 应用码 */
    private String appCode = "SdkTest";

    /** 平台演示账号（网关服务端登录用，生产应换成环境变量/密钥管理） */
    private String username = "admin";
    private String password = "123456";

    /** 本地面板码 → 平台面板码（未配置的码原样透传） */
    private Map<String, String> panelMap = new LinkedHashMap<>();

    public String getBaseUrl() { return baseUrl; }
    public void setBaseUrl(String baseUrl) { this.baseUrl = baseUrl; }

    public String getBusDomainCode() { return busDomainCode; }
    public void setBusDomainCode(String busDomainCode) { this.busDomainCode = busDomainCode; }

    public String getAppCode() { return appCode; }
    public void setAppCode(String appCode) { this.appCode = appCode; }

    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }

    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }

    public Map<String, String> getPanelMap() { return panelMap; }
    public void setPanelMap(Map<String, String> panelMap) { this.panelMap = panelMap; }
}