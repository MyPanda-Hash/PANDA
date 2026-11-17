package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * 使用日志：登录成功/失败、面板业务动作留痕。
 *
 * 两类事件用 eventType 区分：
 *   LOGIN / LOGIN_FAIL / OPEN_AI —— 会话类，只有账号与 IP
 *   ACTION                       —— 业务动作类，带面板/动作/单据号
 * 「使用权限查看」页面把两者放在一张表里按账号分组展示。
 */
@TableName("sys_usage_log")
public class SysUsageLog {

    @TableId(type = IdType.AUTO)
    private Long id;
    private String userName;
    private String realName;
    private String eventType;
    private String detail;
    private String ip;
    /** 业务动作所在面板（名称与编码分开存：名称可改，编码是稳定标识） */
    private String panelName;
    private String panelCode;
    private String actionName;
    private String docNo;
    private LocalDateTime createTime;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getUserName() { return userName; }
    public void setUserName(String userName) { this.userName = userName; }
    public String getRealName() { return realName; }
    public void setRealName(String realName) { this.realName = realName; }
    public String getEventType() { return eventType; }
    public void setEventType(String eventType) { this.eventType = eventType; }
    public String getDetail() { return detail; }
    public void setDetail(String detail) { this.detail = detail; }
    public String getIp() { return ip; }
    public void setIp(String ip) { this.ip = ip; }
    public String getPanelName() { return panelName; }
    public void setPanelName(String panelName) { this.panelName = panelName; }
    public String getPanelCode() { return panelCode; }
    public void setPanelCode(String panelCode) { this.panelCode = panelCode; }
    public String getActionName() { return actionName; }
    public void setActionName(String actionName) { this.actionName = actionName; }
    public String getDocNo() { return docNo; }
    public void setDocNo(String docNo) { this.docNo = docNo; }
    public LocalDateTime getCreateTime() { return createTime; }
    public void setCreateTime(LocalDateTime createTime) { this.createTime = createTime; }
}