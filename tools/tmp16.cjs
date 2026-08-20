const fs = require('fs');
const { execSync } = require('child_process');
// 用前端方式获取：先简单用 mysql -N 导出（避免转义问题用临时文件? 直接 -e 单引号问题）——改用 PowerShell 管道读取
const out = execSync('cmd /c "D:\\phpstudy_pro\\Extensions\\MySQL8.0.12\\bin\\mysql.exe --default-character-set=utf8mb4 -uroot -p000518 -P3308 light_mes -N -e \"SELECT panel_code, config FROM panel_config WHERE panel_code IN (\\'PU_IN\\',\\'PU_ORDER\\',\\'SALE_INV\\',\\'PICK_ORDER\\',\\'MATERIAL_REQ\\',\\'ARRIVAL_IN\\',\\'FINISH_INSPECT\\',\\'INSPECTION\\',\\'DISPATCH\\');\""', { encoding: 'utf8', maxBuffer: 50 * 1024 * 1024 });