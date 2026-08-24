-- MySQL dump 10.13  Distrib 8.0.12, for Win64 (x86_64)
--
-- Host: localhost    Database: light_mes
-- ------------------------------------------------------
-- Server version	8.0.12

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
 SET NAMES utf8mb4 ;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

--
-- Dumping data for table `panel_config`
--
-- WHERE:  panel_code='DEPT'

LOCK TABLES `panel_config` WRITE;
/*!40000 ALTER TABLE `panel_config` DISABLE KEYS */;
INSERT INTO `panel_config` VALUES (6,'DEPT','部门','基础档案','{\"metadata\":{\"panelCode\":\"DEPT\",\"panelName\":\"部门\",\"panelCategory\":\"基础档案\",\"panelState\":{\"dataName\":\"状态\",\"dataType\":\"STRING\",\"defaultOptions\":[\"启用\",\"停用\"]},\"panelPageDto\":{\"tablePages\":[{\"tableName\":\"部门列表\",\"queryFields\":[{\"dataName\":\"部门编码\",\"dataType\":\"文本\"},{\"dataName\":\"部门名称\",\"dataType\":\"文本\"}],\"gridTabs\":[{\"label\":\"列表\",\"rowSource\":\"rows\",\"columns\":[\"部门编码\",\"部门名称\",\"负责人\",\"停用\"]}],\"topBarBtn\":[{\"buttonName\":\"新增流程\"},{\"buttonName\":\"删除\"},{\"buttonName\":\"刷新\"}],\"rowOperationBarBtn\":[],\"events\":[]}],\"formPages\":[{\"formName\":\"部门\",\"fieldNames\":\"部门编码,部门名称,负责人,停用\",\"bottomOperationBarBtn\":[{\"buttonName\":\"保存\"},{\"buttonName\":\"删除\"},{\"buttonName\":\"放弃\"}],\"events\":[]}]},\"panelButtons\":[{\"buttonName\":\"新增流程\"},{\"buttonName\":\"删除\"},{\"buttonName\":\"刷新\"},{\"buttonName\":\"保存\"},{\"buttonName\":\"放弃\"}],\"buttonGroups\":[{\"name\":\"新增\",\"actions\":[\"新增\"]},{\"name\":\"修改\",\"actions\":[\"修改\"]},{\"name\":\"删除\",\"actions\":[\"删除\",\"删除单据\"]},{\"name\":\"查找\",\"actions\":[\"查找\",\"刷新\"]},{\"name\":\"打印\",\"actions\":[\"打印\",\"预览\"]},{\"name\":\"导入\",\"actions\":[\"下载模板\",\"导入\"]},{\"name\":\"更多\",\"actions\":[\"复制\",\"导出\",\"退出\"]}],\"version\":\"1.0\"},\"dataSchema\":{\"type\":\"object\",\"fields\":[{\"dataName\":\"部门编码\",\"dataType\":\"文本\",\"isRequired\":true},{\"dataName\":\"部门名称\",\"dataType\":\"文本\",\"isRequired\":true},{\"dataName\":\"负责人\",\"dataType\":\"文本\"},{\"dataName\":\"停用\",\"dataType\":\"是否\",\"defaultValue\":false}]},\"detail\":{\"tabs\":[]}}','1.0','2026-08-21 14:24:46','2026-08-21 14:24:46');
/*!40000 ALTER TABLE `panel_config` ENABLE KEYS */;
UNLOCK TABLES;

--
-- Dumping data for table `form_data`
--
-- WHERE:  panel_code='DEPT'

LOCK TABLES `form_data` WRITE;
/*!40000 ALTER TABLE `form_data` DISABLE KEYS */;
INSERT INTO `form_data` VALUES (4000,'DEPT','DEPT-001','{\"编号\":\"DEPT-001\",\"部门编码\":\"D01\",\"部门名称\":\"总经办\",\"负责人\":\"系统管理员\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4001,'DEPT','DEPT-002','{\"编号\":\"DEPT-002\",\"部门编码\":\"D02\",\"部门名称\":\"销售一部\",\"负责人\":\"刘经理\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4002,'DEPT','DEPT-003','{\"编号\":\"DEPT-003\",\"部门编码\":\"D03\",\"部门名称\":\"销售二部\",\"负责人\":\"赵经理\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4003,'DEPT','DEPT-004','{\"编号\":\"DEPT-004\",\"部门编码\":\"D04\",\"部门名称\":\"国际部\",\"负责人\":\"周经理\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4004,'DEPT','DEPT-005','{\"编号\":\"DEPT-005\",\"部门编码\":\"D05\",\"部门名称\":\"熔铸车间\",\"负责人\":\"王强\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4005,'DEPT','DEPT-006','{\"编号\":\"DEPT-006\",\"部门编码\":\"D06\",\"部门名称\":\"轧制车间\",\"负责人\":\"李丽\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4006,'DEPT','DEPT-007','{\"编号\":\"DEPT-007\",\"部门编码\":\"D07\",\"部门名称\":\"精整车间\",\"负责人\":\"孙涛\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4007,'DEPT','DEPT-008','{\"编号\":\"DEPT-008\",\"部门编码\":\"D08\",\"部门名称\":\"测试车间\",\"负责人\":\"赵刚\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4008,'DEPT','DEPT-009','{\"编号\":\"DEPT-009\",\"部门编码\":\"D09\",\"部门名称\":\"质检部\",\"负责人\":\"赵刚\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL),(4009,'DEPT','DEPT-010','{\"编号\":\"DEPT-010\",\"部门编码\":\"D10\",\"部门名称\":\"仓储部\",\"负责人\":\"陈仓管\",\"停用\":false}','{}','启用','admin','2026-08-21 14:25:43','admin','2026-08-21 14:25:43',NULL,NULL,NULL);
/*!40000 ALTER TABLE `form_data` ENABLE KEYS */;
UNLOCK TABLES;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

-- Dump completed on 2026-08-24 14:25:15
