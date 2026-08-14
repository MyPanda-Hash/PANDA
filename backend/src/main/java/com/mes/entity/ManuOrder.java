package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

@TableName("manu_order")
public class ManuOrder {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String orderNo;
    private LocalDate orderDate;
    private String factoryCode;
    private String contractNo;
    private String ingotNo;
    private String batchNo;
    private String workshop;
    private LocalDate planStart;
    private LocalDate planEnd;
    private String saleOrderNo;
    private String customerCode;
    private String customerName;
    private String testProgram;
    private String prodOrderCustomer;
    private Integer status;
    private String remark;
    private String createBy;
    private LocalDateTime createTime;
    private String auditBy;
    private LocalDateTime auditTime;
    private LocalDateTime closeTime;

    @TableField(exist = false)
    private List<ManuOrderItem> items;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getOrderNo() { return orderNo; }
    public void setOrderNo(String orderNo) { this.orderNo = orderNo; }
    public LocalDate getOrderDate() { return orderDate; }
    public void setOrderDate(LocalDate orderDate) { this.orderDate = orderDate; }
    public String getFactoryCode() { return factoryCode; }
    public void setFactoryCode(String factoryCode) { this.factoryCode = factoryCode; }
    public String getContractNo() { return contractNo; }
    public void setContractNo(String contractNo) { this.contractNo = contractNo; }
    public String getIngotNo() { return ingotNo; }
    public void setIngotNo(String ingotNo) { this.ingotNo = ingotNo; }
    public String getBatchNo() { return batchNo; }
    public void setBatchNo(String batchNo) { this.batchNo = batchNo; }
    public String getWorkshop() { return workshop; }
    public void setWorkshop(String workshop) { this.workshop = workshop; }
    public LocalDate getPlanStart() { return planStart; }
    public void setPlanStart(LocalDate planStart) { this.planStart = planStart; }
    public LocalDate getPlanEnd() { return planEnd; }
    public void setPlanEnd(LocalDate planEnd) { this.planEnd = planEnd; }
    public String getSaleOrderNo() { return saleOrderNo; }
    public void setSaleOrderNo(String saleOrderNo) { this.saleOrderNo = saleOrderNo; }
    public String getCustomerCode() { return customerCode; }
    public void setCustomerCode(String customerCode) { this.customerCode = customerCode; }
    public String getCustomerName() { return customerName; }
    public void setCustomerName(String customerName) { this.customerName = customerName; }
    public String getTestProgram() { return testProgram; }
    public void setTestProgram(String testProgram) { this.testProgram = testProgram; }
    public String getProdOrderCustomer() { return prodOrderCustomer; }
    public void setProdOrderCustomer(String prodOrderCustomer) { this.prodOrderCustomer = prodOrderCustomer; }
    public Integer getStatus() { return status; }
    public void setStatus(Integer status) { this.status = status; }
    public String getRemark() { return remark; }
    public void setRemark(String remark) { this.remark = remark; }
    public String getCreateBy() { return createBy; }
    public void setCreateBy(String createBy) { this.createBy = createBy; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
    public String getAuditBy() { return auditBy; }
    public void setAuditBy(String auditBy) { this.auditBy = auditBy; }
    public LocalDateTime getAuditTime() { return auditTime; }
    public void setAuditTime(LocalDateTime auditTime) { this.auditTime = auditTime; }
    public LocalDateTime getCloseTime() { return closeTime; }
    public void setCloseTime(LocalDateTime closeTime) { this.closeTime = closeTime; }
    public List<ManuOrderItem> getItems() { return items; }
    public void setItems(List<ManuOrderItem> items) { this.items = items; }
}
