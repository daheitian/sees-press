module.exports = async ({ evaluate, sleep }) => {
  const r = await evaluate(`(async () => {
    const out = [];
    const ok = (n, c, d) => out.push((c ? 'PASS ' : 'FAIL ') + n + (d !== undefined ? ' [' + d + ']' : ''));

    const a = document.querySelector('.toolbar a.back');
    ok('工具栏有返回链接', !!a);
    if (!a) return out.join(' || ');
    ok('指向 index.html', a.getAttribute('href') === 'index.html', a.getAttribute('href'));
    const box = a.getBoundingClientRect();
    ok('可见且可点', box.width > 40 && box.height > 20, Math.round(box.width) + '×' + Math.round(box.height) + 'px');
    ok('在标题左侧', box.left < document.querySelector('.toolbar b').getBoundingClientRect().left,
       Math.round(box.left) + ' < ' + Math.round(document.querySelector('.toolbar b').getBoundingClientRect().left));
    ok('文字带返回箭头', /←/.test(a.textContent), a.textContent.trim());
    ok('目录仍有 3 条', document.querySelectorAll('nav.toc a[data-target]').length === 3);

    // 返回目标必须真实存在（不点，点了会销毁本测试上下文）
    try {
      const resp = await fetch('index.html');
      const txt = await resp.text();
      ok('返回目标可访问', resp.status === 200 && txt.length > 1000, resp.status + ' / ' + txt.length + ' 字节');
      ok('目标是首页', /见室印制所/.test(txt));
    } catch (e) {
      ok('返回目标可访问', false, e.message);
    }
    return out.join(' || ');
  })()`);
  await sleep(300);
  return { result: r };
};
