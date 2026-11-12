package com.mes.panel;

import java.util.List;
import java.util.Map;

/** Stable application contract consumed by the configuration-driven panel controller. */
public interface PanelRuntimeService {

    Map<String, Object> getPanelConfig(String panelCode);

    /** 按登录用户叠加表格列定制（px_column_pref：顺序/显隐/别名）的面板配置；userName 为空时不叠加 */
    default Map<String, Object> getPanelConfig(String panelCode, String userName) {
        return getPanelConfig(panelCode);
    }

    /** 用户级表格列定制保存（columns 空数组=恢复默认）；默认不支持 */
    default void saveColumnPrefs(String panelCode, String userName, List<Map<String, Object>> columns) {
        throw new UnsupportedOperationException("saveColumnPrefs is not supported by this runtime");
    }

    /**
     * 面板自助加字段（登记元数据，读取出口合并；不动表结构）。默认不支持。
     * body: { scope: header|detail, tabKey, dataName, dataType, options, fieldWidth, isRequired, inGrid }
     */
    /** 字段布局：表头 + 明细字段的顺序/列宽/显隐。默认返回空表。 */
    default List<Map<String, Object>> fieldLayout(String panelCode) {
        return List.of();
    }

    /** 保存字段布局（全公司共用，需「调整」权限）。 */
    default void saveFieldLayout(String panelCode, String userName, List<Map<String, Object>> rows) {
        throw new UnsupportedOperationException("字段布局保存未实现");
    }

    default Map<String, Object> addCustomField(String panelCode, Map<String, Object> body) {
        throw new UnsupportedOperationException("addCustomField is not supported by this runtime");
    }

    /** 停用面板自定义字段（只删元数据，业务数据保留）；默认不支持 */
    default void retireCustomField(String panelCode, Map<String, Object> body) {
        throw new UnsupportedOperationException("retireCustomField is not supported by this runtime");
    }

    /**
     * 生单预览：销售订单 → 生产加工单。
     * 逐行返回 需求数量 / 现存量 / 已下单生产数量 / 建议生产数量 / 是否不足。
     * 默认不支持，由 SQL 后端实现。
     */
    default List<Map<String, Object>> producePreview(String soNo) {
        throw new UnsupportedOperationException("producePreview is not supported by this runtime");
    }

    /**
     * 出库预览：销售订单 → 销售出库单。
     * 逐行返回 需求 / 已出库 / 剩余待出 / 现存量 / 建议出库数量 / 是否不足。
     */
    default List<Map<String, Object>> saleOutPreview(String soNo) {
        throw new UnsupportedOperationException("saleOutPreview is not supported by this runtime");
    }

    /** 出库预览（指定发货仓库）；warehouse 为空时按默认成品仓。默认不支持。 */
    default List<Map<String, Object>> saleOutPreview(String soNo, String warehouse) {
        return saleOutPreview(soNo);
    }

    /** 按工序报工：生成只含该工序一行的工序汇报单。 */
    default Map<String, Object> reportProcess(String manuOrderNo, String processCode, double quantity) {
        throw new UnsupportedOperationException("按工序报工未实现");
    }

    /** 某排产批次的报工记录。默认空表。 */
    default List<Map<String, Object>> batchReports(String dispatchNo) {
        return List.of();
    }

    /** 核销弹窗数据：可核销的往来单与未结金额。默认空表。 */
    default List<Map<String, Object>> openWriteOffLines(String panelCode, String formNo) {
        return List.of();
    }

    /** 按排产批次领料：一个批次的多种材料生成一张材料出库单（多行）。 */
    default Map<String, Object> issueBatch(String dispatchNo, List<Map<String, Object>> lines,
                                           String operator, String warehouse) {
        throw new UnsupportedOperationException("按批次整批领料未实现");
    }

    /** 按排产批次领料：生成只含该批次一行的材料出库单。 */
    default Map<String, Object> issueByBatch(String dispatchNo, String materialCode, double quantity,
                                             String operator, String warehouse) {
        throw new UnsupportedOperationException("按批次领料未实现");
    }

    /** 完工汇总：全部工序是否完成、末道是否完成（触发首检）。 */
    default Map<String, Object> completionSummary(String manuOrderNo) {
        return Map.of();
    }

    /** 按排产批次的领料需求看板。默认空表。 */
    default List<Map<String, Object>> materialIssueBoard(String manuOrderNo) {
        return List.of();
    }

    /** 按排产批次报工看板：只列已审核批次。默认空表。 */
    default List<Map<String, Object>> reportBatchBoard() {
        return List.of();
    }

    /** 排产批次看板的「未开工」列：还没排产的工序（可排产量 &gt; 0）。默认空表。 */
    default List<Map<String, Object>> pendingPlanBoard() {
        return List.of();
    }

    /** 按排产批次报工：生成只含该批次一行的汇报单。 */
    default Map<String, Object> reportByBatch(String dispatchNo, double qualified, double rejected) {
        throw new UnsupportedOperationException("按批次报工未实现");
    }

    /** 首件在前、报工在后：给批次所属工序补建首件报检单（一工序一单）。 */
    default Map<String, Object> createFirstInspectByBatch(String dispatchNo) {
        throw new UnsupportedOperationException("批次补建首检单未实现");
    }

    /** 建排产批次：一个产品的某道工序分一批（多产品加工单必须带产品编码）。 */
    default Map<String, Object> planBatch(String manuOrderNo, String productCode, String processCode,
                                          double quantity, String team, String equipment,
                                          String planStart, String planEnd) {
        throw new UnsupportedOperationException("排产未实现");
    }

    /** 一键排产：把当前可排的工序全部排成批次并审批通过。 */
    default Map<String, Object> planAll(String manuOrderNo, String productCode) {
        throw new UnsupportedOperationException("一键排产未实现");
    }

    /** 重算加工单完工进度（维护入口）。 */
    default Map<String, Object> syncCompletion(String manuOrderNo) {
        throw new UnsupportedOperationException("完工进度重算未实现");
    }

    /** 成品报检候选：报完全部工序、且还没生成过成品报检单的加工单。默认返回空表。 */
    default List<Map<String, Object>> finishInspectCandidates() {
        return List.of();
    }

    /** 按加工单生成成品报检单（单号 = 加工单号-JY）。 */
    default Map<String, Object> createFinishInspectFromMo(String manuOrderNo) {
        throw new UnsupportedOperationException("成品报检单生成未实现");
    }

    /** 工序汇报选单看板：加工单 + 每道工序报工进度。默认返回空表。 */
    default List<Map<String, Object>> processReportPickBoard() {
        return List.of();
    }

    /** 销售订单排产看板：一行一个订单行，带订单数量/已排/可排。默认返回空表。 */
    default List<Map<String, Object>> saleOrderPlanBoard(String saleOrderNo) {
        return List.of();
    }

    /** 销售订单排产：按订单行生成生产加工单，生成即已审核。 */
    default Map<String, Object> planFromSalesOrder(String saleOrderNo, List<Map<String, Object>> lines) {
        throw new UnsupportedOperationException("销售订单排产未实现");
    }

    /** 工单排产：按工序给可排产量与已有批次。默认返回空表。 */
    default List<Map<String, Object>> planBoard(String manuOrderNo) {
        return List.of();
    }

    /** 工序报工工作台：一行一道工序，带剩余量与可否报工。默认返回空表。 */
    default List<Map<String, Object>> processReportBoard(String manuOrderNo) {
        return List.of();
    }

    /** 开票工作台：以销货单为行，带发票号与状态。默认返回空表。 */
    default List<Map<String, Object>> invoiceWorkbench() {
        return List.of();
    }

    /**
     * 计划分析可选的销售订单：已审核且还有未排产数量。
     * 默认返回空表。
     */
    default List<Map<String, Object>> planAnalyzeCandidates() {
        return List.of();
    }

    /**
     * 计划分析：一张销售订单的成品/物料/工艺/风险全链路结论。默认不支持。
     * 契约见 PlanAnalyzeQueryService#analyze。
     */
    default Map<String, Object> planAnalyze(String salesOrderNo) {
        throw new UnsupportedOperationException("planAnalyze is not supported by this runtime");
    }

    /**
     * 计划分析 → 生成请购单（草稿）：把外购件缺料行落成 PU_REQ。
     * codes 为空表示全部可请购行。默认不支持。
     */
    default Map<String, Object> planAnalyzePurchaseReq(String salesOrderNo, List<String> codes) {
        throw new UnsupportedOperationException("planAnalyzePurchaseReq is not supported by this runtime");
    }

    /** 开票方（销售方）信息：销售发票打印用。默认返回空表。 */
    default Map<String, Object> companyInfo() {
        return Map.of();
    }

    /**
     * 入库预览：生产加工单 → 产成品入库单。
     * 逐行返回 计划数量 / 已入库数量 / 剩余入库数量 / 建议入库数量。默认不支持。
     */
    default List<Map<String, Object>> finishInPreview(String moNo) {
        throw new UnsupportedOperationException("finishInPreview is not supported by this runtime");
    }

    Map<String, Object> getPermMatrix(String panelCode);

    /** 按登录用户权限过滤的权限矩阵（userName 为空或 admin 时不过滤，保持全真） */
    default Map<String, Object> getPermMatrix(String panelCode, String userName) {
        return getPermMatrix(panelCode);
    }

    Map<String, Object> getNewFormPermMatrix(String panelCode, String operationName);

    Map<String, Object> getFormDescriptor(String panelCode, String code);

    Map<String, Object> queryFormDataList(String panelCode, String keyword,
                                          Map<String, Object> condition, int pageNo, int pageSize);

    List<Map<String, Object>> getApprovalHistory(String panelCode, String formNo);

    Map<String, Object> callButton(String panelCode, String buttonName,
                                   Map<String, Object> formData, Map<String, Object> buttonParam);

    void deleteForms(String panelCode, List<String> rowCodes);

    /**
     * 业务流程追踪：按单号正反向找全相关单据并判定卡点。
     * 默认不支持，由 PxService 实现。
     */
    default Map<String, Object> traceProcess(String docNo) {
        throw new UnsupportedOperationException("traceProcess is not supported by this runtime");
    }

    /**
     * 采购发票行级选单：返回来源入库单/退货单中未开票的明细行。
     * mode=purchase|return；默认不支持，由 PxService 实现。
     */
    default Map<String, Object> invoiceSourceLines(String mode, String supplier, String keyword) {
        throw new UnsupportedOperationException("invoiceSourceLines is not supported by this runtime");
    }
}
