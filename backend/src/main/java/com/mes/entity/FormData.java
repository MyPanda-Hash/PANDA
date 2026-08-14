package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

@TableName("form_data")
public class FormData {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String panelCode;
    private String formNo;
    private String data;
    private String detailData;
    private String status;
    private String createBy;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
    private String auditBy;
    private LocalDateTime auditTime;
    private LocalDateTime closeTime;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getPanelCode() { return panelCode; }
    public void setPanelCode(String panelCode) { this.panelCode = panelCode; }
    public String getFormNo() { return formNo; }
    public void setFormNo(String formNo) { this.formNo = formNo; }
    public String getData() { return data; }
    public void setData(String data) { this.data = data; }
    public String getDetailData() { return detailData; }
    public void setDetailData(String detailData) { this.detailData = detailData; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getCreateBy() { return createBy; }
    public void setCreateBy(String createBy) { this.createBy = createBy; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
    public LocalDateTime getUpdateTime() { return updateTime; }
    public void setUpdateTime(LocalDateTime updateTime) { this.updateTime = updateTime; }
    public String getAuditBy() { return auditBy; }
    public void setAuditBy(String auditBy) { this.auditBy = auditBy; }
    public LocalDateTime getAuditTime() { return auditTime; }
    public void setAuditTime(LocalDateTime auditTime) { this.auditTime = auditTime; }
    public LocalDateTime getCloseTime() { return closeTime; }
    public void setCloseTime(LocalDateTime closeTime) { this.closeTime = closeTime; }
}
