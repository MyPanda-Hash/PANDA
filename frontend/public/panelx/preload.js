/**
 * PanelX SDK 加载器（preload.js）—— 反混淆版
 *
 * 作用：在页面脚本执行前，自动定位后端 baseURL，并同步拉取、执行真正的
 * panelx-sdk.js，从而把全局 PanelXSdk 暴露出来。
 *
 * 与原混淆文件行为完全一致，仅去除了混淆、重命名变量并补充注释。
 *
 * 使用方式：
 *   <script src="./preload.js" devSdkUrl="http://your-api-domain.com/wp-core/api/getPanelXSdk"></script>
 *
 * 环境说明：
 *   - 开发环境（localhost / 127.0.0.1）：从 <script> 标签的 devSdkUrl 属性读取 SDK 地址（必填，缺失则抛错）。
 *   - 生产环境：通过 ping 探测找到 baseURL，再拼上 wp-core/api/getPanelXSdk 拉取 SDK。
 */
(function () {
  // ---------- 常量 ----------
  var PING_PATH = 'wp-core/api/ping';           // 探活接口，响应体必须等于 "pong"
  var SDK_PATH = 'wp-core/api/getPanelXSdk';    // 拉取 SDK 源码的接口
  var CACHE_KEY_PREFIX = 'SDK_BASE_URL_';       // localStorage 缓存键前缀
  var MAX_ANCESTOR_DEPTH = 10;                  // srcdoc 环境向上查找祖先窗口的最大深度
  var SRCDOC_HREF = 'about:srcdoc';             // iframe.srcdoc 的 location.href

  // ---------- 工具：判断一个窗口上下文是否属于 srcdoc / 无效环境 ----------
  function isSrcdocContext(origin, href) {
    return href === SRCDOC_HREF ||
           origin === 'null' ||
           !origin ||
           origin === 'null://null';
  }

  // ---------- 探查 baseURL ----------
  function findBaseUrl() {
    var origin = window.location.origin;
    var pathname = window.location.pathname;

    // 1) srcdoc iframe 场景：向上遍历祖先窗口，找第一个"正常"的 origin / pathname
    if (isSrcdocContext(origin, window.location.href)) {
      console.log('[PanelX SDK] 检测到 iframe.srcdoc 环境');
      try {
        var win = window.parent;
        var depth = 0;

        while (win && win !== window && depth < MAX_ANCESTOR_DEPTH) {
          try {
            var ancOrigin = win.location.origin;
            var ancHref = win.location.href;

            if (!isSrcdocContext(ancOrigin, ancHref)) {
              origin = ancOrigin;
              pathname = win.location.pathname;
              console.log('[PanelX SDK] 找到有效的祖先窗口 (深度=' + depth + '):',
                          { origin: origin, pathname: pathname });
              break;
            }

            if (win.parent && win.parent !== win) {
              win = win.parent;
              depth++;
            } else {
              console.warn('[PanelX SDK] 所有祖先窗口都是 srcdoc 环境');
              return null;
            }
          } catch (err) {
            console.warn('[PanelX SDK] 无法访问祖先窗口 (深度=' + depth + ',可能跨域):', err.message);
            return null;
          }
        }

        if (depth >= MAX_ANCESTOR_DEPTH) {
          console.warn('[PanelX SDK] 嵌套层级过深,超过最大深度限制');
          return null;
        }
        if (!origin || origin === 'null') {
          console.warn('[PanelX SDK] 未能找到有效的祖先窗口 URL');
          return null;
        }
      } catch (err) {
        console.warn('[PanelX SDK] srcdoc 环境且无法访问父窗口(可能跨域):', err.message);
        return null;
      }
    }

    // 2) 优先读 localStorage 缓存，命中后用 ping 验证有效性
    var cacheKey = CACHE_KEY_PREFIX + origin + pathname;
    var cached = localStorage.getItem(cacheKey);
    if (cached) {
      console.log('[PanelX Preload] 发现缓存baseURL:', cached);
      try {
        var cacheXhr = new XMLHttpRequest();
        cacheXhr.open('GET', cached + PING_PATH, false); // 同步验证
        cacheXhr.send(null);
        if (cacheXhr.status === 200 && cacheXhr.responseText === 'pong') {
          console.log('[PanelX Preload] 缓存验证成功,直接使用');
          return cached;
        }
        console.warn('[PanelX Preload] 缓存验证失败，重新探查...');
        localStorage.removeItem(cacheKey);
      } catch (err) {
        console.warn('[PanelX Preload] 缓存验证异常:', err);
        localStorage.removeItem(cacheKey);
      }
    }

    // 3) 从 pathname 逐级向上 ping 探测，找到最深的有效 baseURL
    console.log('[PanelX Preload] 开始探查baseURL...');
    var segments = pathname.split('/').filter(function (seg) {
      return seg.length > 0;
    });

    for (var i = segments.length; i >= 0; i--) {
      var segs = segments.slice(0, i);
      var baseUrl = origin;
      if (segs.length > 0) {
        baseUrl += '/' + segs.join('/');
      }
      if (!baseUrl.endsWith('/')) {
        baseUrl += '/';
      }

      var probeUrl = baseUrl + PING_PATH;
      try {
        console.log('[PanelX Preload] 尝试探查:', probeUrl);
        var probeXhr = new XMLHttpRequest();
        probeXhr.open('GET', probeUrl, false); // 同步探测
        probeXhr.send(null);
        if (probeXhr.status === 200 && probeXhr.responseText === 'pong') {
          console.log('[PanelX Preload] ✓ 找到有效的baseURL:', baseUrl);
          localStorage.setItem(cacheKey, baseUrl);
          return baseUrl;
        }
      } catch (err) {
        console.log('[PanelX Preload] × 探查失败，继续尝试...');
      }
    }

    console.warn('[PanelX Preload] 未能通过探查找到有效的baseURL');
    return null;
  }

  // ---------- 主流程 ----------
  var scriptTag = document.currentScript;
  var isDev = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  var sdkUrl;

  if (isDev) {
    // 开发环境：从 <script> 标签读 devSdkUrl 属性（必填，缺失则抛错）
    var devSdkUrl = scriptTag ? scriptTag.getAttribute('devSdkUrl') : null;
    if (!devSdkUrl) {
      throw new Error('[PanelX Preload] Development mode requires "devSdkUrl" attribute on preload.js script tag');
    }
    sdkUrl = devSdkUrl;
    console.log('[PanelX Preload] Detected development environment, using devSdkUrl:', sdkUrl);
  } else {
    // 生产环境：ping 探查 baseURL，再拼上拉取 SDK 的路径
    var baseUrl = findBaseUrl();
    sdkUrl = baseUrl ? baseUrl + SDK_PATH : null;
    if (sdkUrl) {
      console.log('[PanelX Preload] Production environment, SDK URL:', sdkUrl);
    }
    // 生产环境清理 devSdkUrl 属性，避免泄漏开发地址
    if (scriptTag && scriptTag.hasAttribute('devSdkUrl')) {
      scriptTag.removeAttribute('devSdkUrl');
    }
  }

  // ---------- 拉取并执行 SDK ----------
  if (sdkUrl) {
    console.log('[PanelX Preload] Loading SDK synchronously from:', sdkUrl);
    try {
      var loadXhr = new XMLHttpRequest();
      loadXhr.open('GET', sdkUrl, false); // 同步请求，阻塞页面直到 SDK 就绪
      loadXhr.send(null);
      if (loadXhr.status === 200) {
        console.log('[PanelX Preload] SDK code fetched, executing...');
        (0, eval)(loadXhr.responseText); // 间接 eval：在全局作用域执行 SDK 代码
        console.log('[PanelX Preload] SDK loaded and executed successfully');
      } else {
        console.error('[PanelX Preload] Failed to fetch SDK, status:', loadXhr.status);
      }
    } catch (err) {
      console.error('[PanelX Preload] Error loading SDK:', err);
    }
  } else {
    console.error('[PanelX Preload] Failed to determine SDK URL, cannot load SDK.');
  }
})();
