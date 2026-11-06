/**
 * 转换完成后截一张结果面板的图，用于人工核对排版。
 */
module.exports = async ({ evaluate, sleep, screenshot }) => {
  await evaluate(`(async () => {
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    for (let i = 0; i < 80 && !(window.pdfjsLib && window.pdfjsLib.version); i++) await wait(100);
    const blob = await (await fetch("assets/sample.pdf")).blob();
    const dt = new DataTransfer();
    dt.items.add(new File([blob], "sample.pdf", { type: "application/pdf" }));
    const inp = document.getElementById("file");
    inp.files = dt.files;
    inp.dispatchEvent(new Event("change", { bubbles: true }));
    await wait(400);
    document.getElementById("go").click();
    for (let i = 0; i < 200 && document.getElementById("metrics").children.length === 0; i++) await wait(250);
    return true;
  })()`);
  await sleep(600);
  await screenshot("C:/Users/ga/WorkBuddy/2026-11-05-14-11-49/.shots/pdf-result.png");
  return await evaluate(`({
    status: document.getElementById("status").textContent.trim(),
    note: document.getElementById("resultNote").textContent.trim(),
    metrics: document.getElementById("metrics").textContent.replace(/\\s+/g," ").trim()
  })`);
};
