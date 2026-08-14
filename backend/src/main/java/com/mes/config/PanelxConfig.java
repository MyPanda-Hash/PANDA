package com.mes.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.boot.http.client.ClientHttpRequestFactoryBuilder;
import org.springframework.boot.http.client.ClientHttpRequestFactorySettings;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.client.RestClient;

import java.time.Duration;

@Configuration
@EnableConfigurationProperties(PanelxProperties.class)
public class PanelxConfig {

    /**
     * PanelX 平台专用 RestClient：
     * 每个请求自动携带 BusDomainCode / AppCode 头（与浏览器 SDK 行为一致），
     * token 由 PanelxGateway 按请求动态附加。
     */
    @Bean
    public RestClient panelxRestClient(PanelxProperties props) {
        String base = props.getBaseUrl().endsWith("/") ? props.getBaseUrl() : props.getBaseUrl() + "/";
        var settings = ClientHttpRequestFactorySettings.defaults()
                .withConnectTimeout(Duration.ofSeconds(10))
                .withReadTimeout(Duration.ofSeconds(30));
        return RestClient.builder()
                .baseUrl(base)
                .requestFactory(ClientHttpRequestFactoryBuilder.detect().build(settings))
                .defaultHeader("BusDomainCode", props.getBusDomainCode())
                .defaultHeader("AppCode", props.getAppCode())
                .build();
    }
}