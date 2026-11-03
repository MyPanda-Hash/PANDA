package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/** 应收应付核销记录：一次收款/付款可核销多张往来单，逐条留痕。 */
@TableName("finance_write_off")
public class FinanceWriteOff {

    @TableId(type = IdType.AUTO)
    private Long id;
    private Long voucherId;
    private String paymentNo;
    private BigDecimal amount;
    private LocalDateTime writeOffTime;
    private String operator;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public Long getVoucherId() { return voucherId; }
    public void setVoucherId(Long voucherId) { this.voucherId = voucherId; }
    public String getPaymentNo() { return paymentNo; }
    public void setPaymentNo(String paymentNo) { this.paymentNo = paymentNo; }
    public BigDecimal getAmount() { return amount; }
    public void setAmount(BigDecimal amount) { this.amount = amount; }
    public LocalDateTime getWriteOffTime() { return writeOffTime; }
    public void setWriteOffTime(LocalDateTime writeOffTime) { this.writeOffTime = writeOffTime; }
    public String getOperator() { return operator; }
    public void setOperator(String operator) { this.operator = operator; }
}
