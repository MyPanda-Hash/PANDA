package com.mes.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.mes.entity.AccountingEntry;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Select;

import java.util.List;
import java.util.Map;

@Mapper
public interface AccountingEntryMapper extends BaseMapper<AccountingEntry> {

    /** 会计科目基础表（accounting_account）不在实体层建模，直接读 Map。 */
    @Select("SELECT account_code, account_name, category, direction, enabled "
            + "FROM accounting_account ORDER BY account_code")
    List<Map<String, Object>> selectAccounts();
}
