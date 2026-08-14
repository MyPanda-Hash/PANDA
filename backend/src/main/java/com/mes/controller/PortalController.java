package com.mes.controller;

import com.mes.dto.ApiResult;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/portal")
public class PortalController {

    @GetMapping("/badge")
    public ApiResult<Map<String, Integer>> badge() {
        Map<String, Integer> b = new HashMap<>();
        b.put("todo", 0);
        b.put("message", 0);
        b.put("alarm", 0);
        return ApiResult.ok(b);
    }

    private static final Map<String, List<Map<String, Object>>> NOTICES = buildNotices();

    private static Map<String, List<Map<String, Object>>> buildNotices() {
        Map<String, List<Map<String, Object>>> m = new LinkedHashMap<>();
        m.put("notice", List.of(
            notice(1, "notice", "轻MES v0.2 更新公告", "2026-08-13 09:00", false, "本次更新：新增单据查询/新增单据快捷入口、界面设置与工作台设置、消息通知中心。"),
            notice(2, "notice", "生产加工单模块即将上线", "2026-08-12 15:30", true, "生产加工单（建表 → 后端 CRUD → 前端列表+表单页）将作为第一个真实业务模块开发。")
        ));
        m.put("todo", List.of(
            notice(11, "todo", "工单 MO20260813-003 待审核", "2026-08-13 10:30", false, "工单 MO20260813-003（减速箱体 A ×200）已提交，等待您审核。请及时处理以免影响排产。"),
            notice(12, "todo", "领料单 LL20260813-007 待审批", "2026-08-13 09:45", false, "车间提交领料单 LL20260813-007（轴套 C 原材料），请审批后发放物料。"),
            notice(13, "todo", "设备 EQ-03 点检到期提醒", "2026-08-12 18:00", false, "设备 EQ-03（数控车床）日点检即将到期，请安排点检并录入点检结果。"),
            notice(14, "todo", "工序汇报单待确认", "2026-08-12 16:20", true, "3 张工序汇报单等待确认，涉及 5 名工人计件工资核算。"),
            notice(15, "todo", "期初库存余额待录入", "2026-08-11 11:00", true, "初始化：库存期初余额尚未完成录入，请尽快完成以保证月末核算准确。")
        ));
        m.put("message", List.of(
            notice(21, "message", "领料单 LL20260813-007 审批通过", "2026-08-13 09:50", false, "您提交的领料单 LL20260813-007 已由管理员审批通过，可前往仓库领料。"),
            notice(22, "message", "系统将于周六 22:00 维护", "2026-08-13 08:00", false, "系统将于本周六 22:00 - 24:00 进行例行维护，期间服务暂停，请提前保存数据。"),
            notice(23, "message", "新的角色权限已生效", "2026-08-12 14:00", true, "管理员已为您开通「生产管理」模块权限，重新登录后生效。")
        ));
        m.put("alarm", List.of(
            notice(31, "alarm", "库存预警：法兰盘 B 原材料低于安全库存", "2026-08-13 08:30", false, "存货「45# 圆钢 Φ60」当前库存 120 件，低于安全库存 200 件，请及时采购补充。"),
            notice(32, "alarm", "设备稼动率异常：EQ-05 低于 60%", "2026-08-12 17:00", false, "设备 EQ-05 今日稼动率 54%，低于阈值 60%，请排查停机原因。")
        ));
        return m;
    }

    private static Map<String, Object> notice(long id, String type, String title, String time, boolean read, String content) {
        Map<String, Object> n = new LinkedHashMap<>();
        n.put("id", id);
        n.put("type", type);
        n.put("title", title);
        n.put("time", time);
        n.put("read", read);
        n.put("content", content);
        return n;
    }

    @GetMapping("/notice/list")
    public ApiResult<List<Map<String, Object>>> noticeList(@RequestParam(defaultValue = "todo") String type) {
        return ApiResult.ok(NOTICES.getOrDefault(type, new ArrayList<>()));
    }
}
