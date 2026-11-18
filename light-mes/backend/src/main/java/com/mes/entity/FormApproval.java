package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/** 审批记录（审批流：提交/通过/驳回/弃审 全留痕） */
@TableName("form_approval")
public class FormApproval {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 面板编码 */
    private String panelCode;

    /** 单据编号 */
    private String formNo;

    /** 动作: SUBMIT / APPROVE / REJECT / UNAUDIT */
    private String action;

    /** 结果: PENDING / APPROVED / REJECTED */
    private String result;

    /** 审批节点（预留多级） */
    private Integer nodeNo;

    /** 操作人 */
    private String operator;

    /** 意见 */
    private String opinion;

    /** 操作时间 */
    private LocalDateTime createTime;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }

    public String getPanelCode() { return panelCode; }
    public void setPanelCode(String panelCode) { this.panelCode = panelCode; }

    public String getFormNo() { return formNo; }
    public void setFormNo(String formNo) { this.formNo = formNo; }

    public String getAction() { return action; }
    public void setAction(String action) { this.action = action; }

    public String getResult() { return result; }
    public void setResult(String result) { this.result = result; }

    public Integer getNodeNo() { return nodeNo; }
    public void setNodeNo(Integer nodeNo) { this.nodeNo = nodeNo; }

    public String getOperator() { return operator; }
    public void setOperator(String operator) { this.operator = operator; }

    public String getOpinion() { return opinion; }
    public void setOpinion(String opinion) { this.opinion = opinion; }

    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
}
