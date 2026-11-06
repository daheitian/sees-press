#!/usr/bin/env node
/**
 * 用 Chrome DevTools Protocol 实测演示站页面。
 *
 * 为什么不用 `chrome --headless --dump-dom`：
 *   1) 无头 Chrome 有 500px 最小视口，量不到真手机宽度；
 *   2) `--virtual-time-budget` 会把定时器快进，pdf.js 这种真活还没干完就被判超时。
 * CDP 走真实时钟，可以「等到某个条件成立」再取结果。
 *
 *   node scripts/browser_probe.js <url> <probe.js> [超时秒数]
 *
 * probe.js 里写 `module.exports = async (api) => { ... }`，
 * api 提供 evaluate / sleep / log，返回值会被打印成 JSON。
 */
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const CHROME =
  process.env.CHROME_PATH ||
  "C:/Program Files/Google/Chrome/Application/chrome.exe";
const PORT = 9333 + Math.floor(Math.random() * 200);

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function waitForDevtools(timeoutMs = 20000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/version`);
      if (r.ok) return await r.json();
    } catch (_) {}
    await sleep(200);
  }
  throw new Error("Chrome DevTools 未就绪");
}

async function openPage() {
  // 不带 URL 建空 target，导航交给 CDP —— `?url` 的编码在不同 Chrome 版本行为不一致
  const r = await fetch(`http://127.0.0.1:${PORT}/json/new`, { method: "PUT" });
  if (!r.ok) throw new Error("新建页面失败 " + r.status);
  return await r.json();
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    ws.addEventListener("open", () => {
      resolve({
        send(method, params = {}) {
          const mid = ++id;
          ws.send(JSON.stringify({ id: mid, method, params }));
          return new Promise((res, rej) => pending.set(mid, { res, rej }));
        },
        close() {
          ws.close();
        },
      });
    });
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
      }
    });
    ws.addEventListener("error", (e) => reject(e));
  });
}

async function main() {
  const url = process.argv[2];
  const probeFile = process.argv[3];
  const timeoutSec = Number(process.argv[4] || 90);
  if (!url || !probeFile) {
    console.error("用法: node scripts/browser_probe.js <url> <probe.js> [超时秒数]");
    process.exit(2);
  }

  const userDir = fs.mkdtempSync(path.join(os.tmpdir(), "cdp-"));
  const chrome = spawn(
    CHROME,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-sandbox",
      "--no-first-run",
      "--disable-extensions",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${userDir}`,
      "--window-size=1440,1000",
      "about:blank",
    ],
    { stdio: "ignore" }
  );

  let client;
  try {
    await waitForDevtools();
    const page = await openPage();
    client = await connect(page.webSocketDebuggerUrl);
    await client.send("Runtime.enable");
    await client.send("Page.enable");
    await client.send("Page.navigate", { url });
    // 等 readyState=complete，并确认地址栏确实是目标页（否则后面所有断言都在错的文档上跑）
    let atTarget = false;
    let last = "";
    for (let i = 0; i < 150; i++) {
      const st = await client.send("Runtime.evaluate", {
        expression: "[document.readyState, location.href].join('|')",
        returnByValue: true,
      });
      last = (st.result && st.result.value) || "";
      if (last.startsWith("complete|") && last.includes(url.replace(/\/$/, ""))) {
        atTarget = true;
        break;
      }
      await sleep(100);
    }
    if (!atTarget) throw new Error("页面未导航到目标地址：" + url + " 实际=" + last);

    const errors = [];
    // 收集未捕获异常
    const origSend = client.send.bind(client);
    client.send = origSend;
    const poll = setInterval(() => {}, 1000);

    const evaluate = async (expr, awaitPromise = true) => {
      const r = await client.send("Runtime.evaluate", {
        expression: expr,
        awaitPromise,
        returnByValue: true,
        userGesture: true,
      });
      if (r.exceptionDetails) {
        throw new Error(
          r.exceptionDetails.exception?.description ||
            r.exceptionDetails.text ||
            "evaluate 异常"
        );
      }
      return r.result.value;
    };

    const api = {
      evaluate,
      sleep,
      log: (...a) => console.log(...a),
      errors,
      // 整页截图（captureBeyondViewport 能拍到滚动区之外）
      screenshot: async (file, opts = {}) => {
        const r = await client.send("Page.captureScreenshot", {
          format: "png",
          captureBeyondViewport: opts.full !== false,
        });
        fs.mkdirSync(path.dirname(path.resolve(file)), { recursive: true });
        fs.writeFileSync(file, Buffer.from(r.data, "base64"));
        return file;
      },
    };

    const mod = require(path.resolve(probeFile));
    const out = await Promise.race([
      mod(api),
      sleep(timeoutSec * 1000).then(() => ({ timedOut: true })),
    ]);
    console.log(JSON.stringify(out, null, 2));
    clearInterval(poll);
    process.exitCode = out && out.timedOut ? 1 : 0;
  } catch (e) {
    console.error("PROBE ERROR:", e.message);
    process.exitCode = 1;
  } finally {
    try {
      client && client.close();
    } catch (_) {}
    chrome.kill();
    await sleep(300);
    try {
      fs.rmSync(userDir, { recursive: true, force: true });
    } catch (_) {}
  }
}

main();
