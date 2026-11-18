package com.mes.config;

import com.baomidou.mybatisplus.extension.plugins.MybatisPlusInterceptor;
import com.baomidou.mybatisplus.extension.plugins.handler.TenantLineHandler;
import com.baomidou.mybatisplus.extension.plugins.inner.PaginationInnerInterceptor;
import com.baomidou.mybatisplus.extension.plugins.inner.TenantLineInnerInterceptor;
import net.sf.jsqlparser.expression.Expression;
import net.sf.jsqlparser.expression.StringValue;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.util.Set;

@Configuration
public class MybatisPlusConfig {

    /**
     * 参与账套隔离的业务表。
     *
     * 只列业务数据表：系统配置类（sys_menu/sys_role/sys_locale/panel_config 等）
     * 是全局共享的，隔离它们会导致「切了账套看不到菜单」这类荒谬结果。
     * 与 V8 迁移里加过 factory_code 的表保持一致。
     */
    private static final Set<String> TENANT_TABLES = Set.of(
            "form_data", "form_approval",
            "stock_balance", "stock_txn",
            "manu_order", "manu_order_item",
            "finance_voucher", "finance_write_off",
            "accounting_account", "accounting_entry",
            "invoice_file", "wms_task",
            "dc_alarm", "dc_device", "dc_gateway", "dc_point",
            "dc_rule", "dc_sample", "dc_status_log"
    );

    /**
     * 不写死方言：PaginationInnerInterceptor 按 JDBC URL 自动识别（当前为 PostgreSQL 16）。
     * TenantLineInnerInterceptor 必须排在分页之前：
     * 它改写的是 WHERE 条件，先拼条件再算分页总数，否则 count 会多算别的账套。
     */
    @Bean
    public MybatisPlusInterceptor mybatisPlusInterceptor() {
        MybatisPlusInterceptor interceptor = new MybatisPlusInterceptor();

        interceptor.addInnerInterceptor(new TenantLineInnerInterceptor(new TenantLineHandler() {
            @Override
            public Expression getTenantId() {
                // 每条 SQL 的账套条件都从这里取，所以切换账套只需换请求头即可全局生效
                return new StringValue(FactoryContext.current());
            }

            @Override
            public String getTenantIdColumn() {
                return "factory_code";
            }

            @Override
            public boolean ignoreTable(String tableName) {
                return !TENANT_TABLES.contains(tableName.toLowerCase());
            }
        }));

        interceptor.addInnerInterceptor(new PaginationInnerInterceptor());
        return interceptor;
    }
}