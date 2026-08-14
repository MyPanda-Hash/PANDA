package com.mes.service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.mes.entity.ManuOrder;
import com.mes.entity.ManuOrderItem;
import com.mes.mapper.ManuOrderItemMapper;
import com.mes.mapper.ManuOrderMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.Map;

@Service
public class ManuOrderService {

    private static final DateTimeFormatter NO_DAY = DateTimeFormatter.ofPattern("yyyyMMdd");

    private final ManuOrderMapper orderMapper;
    private final ManuOrderItemMapper itemMapper;

    public ManuOrderService(ManuOrderMapper orderMapper, ManuOrderItemMapper itemMapper) {
        this.orderMapper = orderMapper;
        this.itemMapper = itemMapper;
    }

    public Map<String, Object> pageList(int pageNo, int pageSize, String orderNo, String ingotNo, Integer status, String factoryCode) {
        LambdaQueryWrapper<ManuOrder> qw = new LambdaQueryWrapper<ManuOrder>()
                .like(orderNo != null && !orderNo.isBlank(), ManuOrder::getOrderNo, orderNo)
                .like(ingotNo != null && !ingotNo.isBlank(), ManuOrder::getIngotNo, ingotNo)
                .eq(status != null, ManuOrder::getStatus, status)
                .eq(factoryCode != null && !factoryCode.isBlank(), ManuOrder::getFactoryCode, factoryCode)
                .orderByDesc(ManuOrder::getCreateTime)
                .orderByDesc(ManuOrder::getId);
        Page<ManuOrder> page = orderMapper.selectPage(new Page<>(pageNo, pageSize), qw);
        return Map.of("total", page.getTotal(), "records", page.getRecords());
    }

    public ManuOrder getDetail(Long id) {
        ManuOrder order = orderMapper.selectById(id);
        if (order == null) return null;
        order.setItems(itemMapper.selectList(new LambdaQueryWrapper<ManuOrderItem>()
                .eq(ManuOrderItem::getOrderId, id)
                .orderByAsc(ManuOrderItem::getSeq)));
        return order;
    }

    @Transactional
    public ManuOrder create(ManuOrder order, String userName) {
        order.setId(null);
        validate(order);
        order.setOrderNo(generateOrderNo());
        if (order.getOrderDate() == null) order.setOrderDate(LocalDate.now());
        if (order.getStatus() == null) order.setStatus(0);
        order.setCreateBy(userName);
        order.setCreateTime(LocalDateTime.now());
        orderMapper.insert(order);
        saveItems(order.getId(), order.getItems());
        return order;
    }

    @Transactional
    public ManuOrder update(Long id, ManuOrder order, String userName) {
        ManuOrder db = requireDraft(id);
        validate(order);
        order.setId(id);
        order.setOrderNo(db.getOrderNo());
        order.setStatus(db.getStatus());
        order.setCreateBy(db.getCreateBy());
        order.setCreateTime(db.getCreateTime());
        orderMapper.updateById(order);
        itemMapper.delete(new LambdaQueryWrapper<ManuOrderItem>().eq(ManuOrderItem::getOrderId, id));
        saveItems(id, order.getItems());
        return getDetail(id);
    }

    @Transactional
    public void delete(Long id) {
        requireDraft(id);
        orderMapper.deleteById(id);
        itemMapper.delete(new LambdaQueryWrapper<ManuOrderItem>().eq(ManuOrderItem::getOrderId, id));
    }

    @Transactional
    public ManuOrder audit(Long id, String userName) {
        ManuOrder db = orderMapper.selectById(id);
        if (db == null) throw new IllegalArgumentException("单据不存在");
        if (db.getStatus() != 0) throw new IllegalStateException("仅草稿状态可审核");
        db.setStatus(1);
        db.setAuditBy(userName);
        db.setAuditTime(LocalDateTime.now());
        orderMapper.updateById(db);
        return db;
    }

    @Transactional
    public ManuOrder unaudit(Long id) {
        ManuOrder db = orderMapper.selectById(id);
        if (db == null) throw new IllegalArgumentException("单据不存在");
        if (db.getStatus() != 1) throw new IllegalStateException("仅已审核状态可弃审");
        db.setStatus(0);
        db.setAuditBy(null);
        db.setAuditTime(null);
        orderMapper.updateById(db);
        return db;
    }

    @Transactional
    public ManuOrder close(Long id) {
        ManuOrder db = orderMapper.selectById(id);
        if (db == null) throw new IllegalArgumentException("单据不存在");
        if (db.getStatus() != 1 && db.getStatus() != 2) throw new IllegalStateException("仅已审核/生产中状态可关闭");
        db.setStatus(4);
        db.setCloseTime(LocalDateTime.now());
        orderMapper.updateById(db);
        return db;
    }

    private ManuOrder requireDraft(Long id) {
        ManuOrder db = orderMapper.selectById(id);
        if (db == null) throw new IllegalArgumentException("单据不存在");
        if (db.getStatus() != 0) throw new IllegalStateException("仅草稿状态可编辑/删除");
        return db;
    }

    private void validate(ManuOrder order) {
        if (order.getContractNo() == null || order.getContractNo().isBlank()) throw new IllegalArgumentException("合同号不能为空");
        if (order.getIngotNo() == null || order.getIngotNo().isBlank()) throw new IllegalArgumentException("锭号不能为空");
        if (order.getBatchNo() == null || order.getBatchNo().isBlank()) throw new IllegalArgumentException("批号不能为空");
        if (order.getWorkshop() == null || order.getWorkshop().isBlank()) throw new IllegalArgumentException("生产车间不能为空");
        if (order.getTestProgram() == null || order.getTestProgram().isBlank()) throw new IllegalArgumentException("测试程序不能为空");
        if (order.getItems() == null || order.getItems().isEmpty()) throw new IllegalArgumentException("请至少添加一行工序明细");
    }

    private void saveItems(Long orderId, List<ManuOrderItem> items) {
        if (items == null || items.isEmpty()) return;
        int seq = 1;
        for (ManuOrderItem it : items) {
            it.setId(null);
            it.setOrderId(orderId);
            if (it.getSeq() == null) it.setSeq(seq);
            itemMapper.insert(it);
            seq++;
        }
    }

    private String generateOrderNo() {
        String prefix = "MO" + LocalDate.now().format(NO_DAY) + "-";
        long count = orderMapper.selectCount(new LambdaQueryWrapper<ManuOrder>()
                .likeRight(ManuOrder::getOrderNo, prefix));
        return prefix + String.format("%03d", count + 1);
    }
}
