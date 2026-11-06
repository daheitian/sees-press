module.exports = async ({ evaluate, log }) => {
  const r = await evaluate(`(() => {
    const out = {};
    const sheet = document.getElementById("sheet");
    const chips = document.querySelectorAll("#chips .chip");
    out.chipCount = chips.length;
    out.names = Array.from(chips).map(c => c.textContent);
    out.meta0 = document.getElementById("meta").textContent;

    const read = () => {
      const cs = getComputedStyle(sheet);
      return {
        lead: cs.getPropertyValue("--slead").trim(),
        size: cs.getPropertyValue("--ssize").trim(),
        accent: cs.getPropertyValue("--sa").trim(),
        head: cs.getPropertyValue("--shead").trim()
      };
    };
    out.before = read();

    // 逐个点开，确认样式真的换了
    out.perChip = [];
    chips.forEach((c, i) => {
      c.click();
      const st = read();
      out.perChip.push({ i, on: c.classList.contains("on"), ...st });
    });
    out.onlyOneActive = document.querySelectorAll("#chips .chip.on").length;

    // 改文字 → 渲染与字数统计应跟着变
    const src = document.getElementById("src");
    src.value = "# 测试标题\\n\\n第一段正文，用来验证输入即渲染。";
    src.dispatchEvent(new Event("input", { bubbles: true }));
    out.afterType = document.getElementById("meta").textContent;
    out.h1 = (sheet.querySelector("h1") || {}).textContent || "";
    out.pCount = sheet.querySelectorAll("p").length;

    // 重置按钮
    document.getElementById("reset").click();
    out.afterReset = document.getElementById("meta").textContent;
    out.resetH1 = (sheet.querySelector("h1") || {}).textContent || "";

    // 窄屏：单列
    out.sheetWidth = Math.round(sheet.getBoundingClientRect().width);
    out.docW = document.documentElement.scrollWidth;
    out.vw = innerWidth;
    return out;
  })()`);
  return r;
};
