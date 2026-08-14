package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.math.BigDecimal;
import java.time.LocalDateTime;

@TableName("manu_order_item")
public class ManuOrderItem {

    @TableId(type = IdType.AUTO)
    private Long id;
    private Long orderId;
    private Integer seq;
    private String processCode;
    private String processName;
    private String equipment;
    private String workerGroup;
    private BigDecimal planQty;
    private BigDecimal finishedQty;
    private BigDecimal qualifiedQty;
    private BigDecimal defectQty;
    private LocalDateTime startTime;
    private LocalDateTime endTime;
    private String remark;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public Long getOrderId() { return orderId; }
    public void setOrderId(Long orderId) { this.orderId = orderId; }
    public Integer getSeq() { return seq; }
    public void setSeq(Integer seq) { this.seq = seq; }
    public String getProcessCode() { return processCode; }
    public void setProcessCode(String processCode) { this.processCode = processCode; }
    public String getProcessName() { return processName; }
    public void setProcessName(String processName) { this.processName = processName; }
    public String getEquipment() { return equipment; }
    public void setEquipment(String equipment) { this.equipment = equipment; }
    public String getWorkerGroup() { return workerGroup; }
    public void setWorkerGroup(String workerGroup) { this.workerGroup = workerGroup; }
    public BigDecimal getPlanQty() { return planQty; }
    public void setPlanQty(BigDecimal planQty) { this.planQty = planQty; }
    public BigDecimal getFinishedQty() { return finishedQty; }
    public void setFinishedQty(BigDecimal finishedQty) { this.finishedQty = finishedQty; }
    public BigDecimal getQualifiedQty() { return qualifiedQty; }
    public void setQualifiedQty(BigDecimal qualifiedQty) { this.qualifiedQty = qualifiedQty; }
    public BigDecimal getDefectQty() { return defectQty; }
    public void setDefectQty(BigDecimal defectQty) { this.defectQty = defectQty; }
    public LocalDateTime getStartTime() { return startTime; }
    public void setStartTime(LocalDateTime startTime) { this.startTime = startTime; }
    public LocalDateTime getEndTime() { return endTime; }
    public void setEndTime(LocalDateTime endTime) { this.endTime = endTime; }
    public String getRemark() { return remark; }
    public void setRemark(String remark) { this.remark = remark; }
}
