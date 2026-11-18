package com.mes.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import java.time.LocalDateTime;

/**
 * 发票官方票面文件（OFD / PDF）。
 *
 * <p>系统本身不签发发票，正式票面由电子税务局或开票服务商开具；这里只做归档，
 * 供打印、下载与查验时取出真实文件（含二维码与发票监制章）。
 */
@TableName("invoice_file")
public class InvoiceFile {

    @TableId(type = IdType.AUTO)
    private Long id;
    /** 发票单据号（SALE_INVOICE 的编号） */
    private String invoiceNo;
    private String fileName;
    /** 相对存储根目录的路径 */
    private String filePath;
    private Long fileSize;
    /** 文件类型：OFD / PDF */
    private String fileType;
    /** SHA-256，同一票面重复上传可识别 */
    private String sha256;
    private String uploadBy;
    private LocalDateTime uploadTime;

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getInvoiceNo() { return invoiceNo; }
    public void setInvoiceNo(String invoiceNo) { this.invoiceNo = invoiceNo; }
    public String getFileName() { return fileName; }
    public void setFileName(String fileName) { this.fileName = fileName; }
    public String getFilePath() { return filePath; }
    public void setFilePath(String filePath) { this.filePath = filePath; }
    public Long getFileSize() { return fileSize; }
    public void setFileSize(Long fileSize) { this.fileSize = fileSize; }
    public String getFileType() { return fileType; }
    public void setFileType(String fileType) { this.fileType = fileType; }
    public String getSha256() { return sha256; }
    public void setSha256(String sha256) { this.sha256 = sha256; }
    public String getUploadBy() { return uploadBy; }
    public void setUploadBy(String uploadBy) { this.uploadBy = uploadBy; }
    public LocalDateTime getUploadTime() { return uploadTime; }
    public void setUploadTime(LocalDateTime uploadTime) { this.uploadTime = uploadTime; }
}
