/**
 * 实测 pdf2epub.html：选中样例 PDF → 点击转换 → 截获 EPUB 字节做结构检查。
 * 用 CDP 真实时钟等待，不依赖 --virtual-time-budget。
 */
module.exports = async ({ evaluate, sleep }) => {
  const waitLib = await evaluate(`new Promise((res) => {
    let n = 0;
    const t = setInterval(() => {
      n++;
      if (window.pdfjsLib && window.pdfjsLib.version) { clearInterval(t); res(window.pdfjsLib.version); }
      else if (n > 100) { clearInterval(t); res(null); }
    }, 100);
  })`);
  if (!waitLib) return { error: "pdf.js 未加载" };

  const report = await evaluate(`(async () => {
    const R = { lib: window.pdfjsLib.version, steps: {} };
    const okk = (k, v) => { R.steps[k] = v; };
    okk("workerSrc", window.pdfjsLib.GlobalWorkerOptions.workerSrc);
    okk("onchange", typeof document.getElementById("file").onchange);

    // 截获下载 blob
    let last = null;
    const orig = URL.createObjectURL.bind(URL);
    URL.createObjectURL = (b) => { last = b; return orig(b); };

    const resp = await fetch("assets/sample.pdf");
    const blob = await resp.blob();
    okk("pdfBytes", blob.size);
    const dt = new DataTransfer();
    dt.items.add(new File([blob], "sample.pdf", { type: "application/pdf" }));
    const inp = document.getElementById("file");
    inp.files = dt.files;
    inp.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    okk("goEnabled", !document.getElementById("go").disabled);
    okk("fname", document.getElementById("fname").textContent);

    document.getElementById("go").click();

    const t0 = Date.now();
    while (Date.now() - t0 < 60000) {
      if (document.getElementById("metrics").children.length > 0) break;
      await new Promise(r => setTimeout(r, 250));
    }
    okk("status", document.getElementById("status").textContent.trim());
    okk("metricsCount", document.getElementById("metrics").children.length);
    okk("metrics", document.getElementById("metrics").textContent.replace(/\\s+/g, " ").trim().slice(0, 200));
    okk("warns", document.getElementById("warns").children.length);
    okk("warnText", document.getElementById("warns").textContent.replace(/\\s+/g, " ").trim().slice(0, 160));
    const md = document.getElementById("md").value || "";
    okk("mdChars", md.length);
    okk("mdHeadings", (md.match(/^#{1,3}\\s.+$/gm) || []).slice(0, 6));
    okk("mdSample", md.slice(0, 120).replace(/\\n/g, " / "));

    document.getElementById("dlEpub").click();
    await new Promise(r => setTimeout(r, 900));
    if (!last) { okk("epub", "none"); return R; }
    okk("epubBytes", last.size);
    const buf = new Uint8Array(await last.arrayBuffer());
    let head = ""; for (let i = 0; i < 4; i++) head += String.fromCharCode(buf[i]);
    okk("zipHead", head);
    let all = "";
    for (let i = 0; i < buf.length; i += 4000) all += String.fromCharCode.apply(null, buf.subarray(i, i + 4000));
    okk("hasMimetypeFirst", all.indexOf("mimetypeapplication/epub+zip") >= 0);
    okk("hasContentOpf", all.indexOf("content.opf") >= 0);
    okk("hasNav", all.indexOf("nav.xhtml") >= 0);
    okk("hasNcx", all.indexOf("toc.ncx") >= 0);
    okk("hasXhtmlChapter", /section\\d*\\.xhtml/.test(all) || all.indexOf("chap") >= 0);
    return R;
  })()`);

  return report;
};
