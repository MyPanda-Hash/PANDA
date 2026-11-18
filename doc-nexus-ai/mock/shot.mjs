// 三档截图 + 无横向滚动 + 运行期错误检查（ego-browser 脚本）
// 用法：cd /Users/panda/Projects/_light-m-ai && ~/.local/bin/ego-browser nodejs < doc-nexus-ai/mock/shot.mjs
// 产物：/tmp/dsh-aidesk/shots/<route>-<width>.png
// 前置：python3 doc-nexus-ai/mock/serve_mock.py &  +  node serve-ai-desk.mjs --mock &

const OUT = "/tmp/dsh-aidesk/shots";
const ROUTES = ["todos", "chat", "voice", "groups", "staff"];
const WIDTHS = [1440, 768, 375];
const THEMES = ["light", "dark"];

const task = await taskSpace("ai-desk-redesign");
const page = task.page("p1");
const results = [];

function collectErrors(events) {
  const errors = [];
  for (const event of events) {
    const method = event && event.method;
    const params = (event && event.params) || {};
    if (method === "Runtime.exceptionThrown") {
      const details = params.exceptionDetails || {};
      const text = (details.exception && (details.exception.description || details.exception.value)) || details.text || "unknown";
      errors.push(`异常: ${String(text).split("\n")[0].slice(0, 200)}`);
    }
    if (method === "Log.entryAdded" && params.entry && params.entry.level === "error") {
      errors.push(`日志: ${String(params.entry.text).slice(0, 200)}`);
    }
    if (method === "Runtime.consoleAPICalled" && params.type === "error") {
      const args = (params.args || []).map((a) => a.value || a.description || "").join(" ");
      errors.push(`console.error: ${args.slice(0, 200)}`);
    }
    if (method === "Network.responseReceived") {
      const res = params.response || {};
      if (res.status >= 400) errors.push(`HTTP ${res.status}: ${String(res.url).replace("http://127.0.0.1:18092", "")}`);
    }
    if (method === "Network.loadingFailed" && !params.canceled) {
      errors.push(`请求失败: ${String(params.errorText || "").slice(0, 120)}`);
    }
  }
  return [...new Set(errors)];
}

for (const route of ROUTES) {
  for (const width of WIDTHS) {
    for (const theme of THEMES) {
      const url = `http://127.0.0.1:18092/_shot.html?w=${width}&r=${route}&theme=${theme}`;
      await page.events().catch(() => null);           // 清掉上一轮缓存的事件
      await page.goto(url);
      await page.waitForSelector("#app", { timeout: 8000 }).catch(() => null);
      await new Promise((r) => setTimeout(r, 1800));   // 等 iframe 里的模块挂载与接口返回
      const metrics = await page.evaluate(() => {
        const frame = document.getElementById("app");
        const doc = frame && frame.contentDocument;
        if (!doc) return { error: "no iframe doc" };
        const de = doc.documentElement;
        const smallHits = [...doc.querySelectorAll("button,a")]
          .filter((n) => {
            const r = n.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && (r.height < 40 || r.width < 24);
          })
          .map((n) => (n.textContent || n.getAttribute("aria-label") || n.tagName).trim().slice(0, 24));
        return {
          scrollWidth: de.scrollWidth,
          clientWidth: de.clientWidth,
          overflowX: de.scrollWidth - de.clientWidth,
          smallHits,
          viewText: ((doc.getElementById("view") || {}).innerText || "").trim().slice(0, 80),
        };
      });
      const errors = collectErrors(await page.events().catch(() => []));
      const file = `${OUT}/${route}-${width}${theme === "dark" ? "-dark" : ""}.png`;
      await page.screenshot({ path: file, clip: { x: 0, y: 0, width, height: 900 } });
      results.push({ route, width, theme, ...metrics, errors, file });
    }
  }
}

for (const row of results) {
  const flag = row.overflowX > 0 ? "FAIL(横向滚动)" : row.errors.length ? "WARN(有错误)" : "ok";
  console.log(
    `${row.route.padEnd(6)} ${String(row.width).padStart(4)}px ${row.theme.padEnd(5)} overflowX=${String(row.overflowX).padStart(3)} 小命中=${String((row.smallHits || []).length).padStart(2)} ${flag}`
  );
  if (!row.viewText) console.log("        ⚠ 内容区为空");
  for (const error of row.errors.slice(0, 4)) console.log(`        - ${error}`);
}

const bad = results.filter((r) => r.overflowX > 0);
console.log(bad.length ? `有 ${bad.length} 个宽度出现横向滚动` : "横向滚动：全部通过");
console.log(OUT);
