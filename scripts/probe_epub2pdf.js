module.exports = async ({ evaluate, sleep, screenshot }) => {
  // 1) 等 JSZip 就绪
  const ready = await evaluate(`(async () => {
    for (let i = 0; i < 80 && typeof JSZip === 'undefined'; i++)
      await new Promise(r => setTimeout(r, 100));
    return typeof JSZip;
  })()`);

  const r = await evaluate(`(async () => {
    const out = [];
    const ok = (n, c, d) => out.push((c ? 'PASS ' : 'FAIL ') + n + (d !== undefined ? ' [' + d + ']' : ''));
    let printed = 0;
    window.print = () => { printed++; };

    const blob = await (await fetch('assets/sample.epub')).blob();
    const dt = new DataTransfer();
    dt.items.add(new File([blob], 'sample.epub', { type: 'application/epub+zip' }));
    const inp = document.getElementById('file');
    inp.files = dt.files;
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 300));

    ok('选中后按钮可用', !document.getElementById('go').disabled);
    ok('文件名已显示', (document.getElementById('fname').textContent || '').includes('sample.epub'));

    document.getElementById('go').click();
    for (let i = 0; i < 120; i++) {
      const done = document.getElementById('result').classList.contains('show');
      const err = /出错/.test(document.getElementById('status').textContent || '');
      if (done || err) break;
      await new Promise(r => setTimeout(r, 250));
    }
    const st = document.getElementById('status').textContent || '';
    ok('重排完成', document.getElementById('result').classList.contains('show'), st.slice(0, 60));

    const ch = document.getElementById('mCh').textContent;
    const chars = document.getElementById('mChars').textContent;
    const imgs = document.getElementById('mImg').textContent;
    const pages = document.getElementById('mPages').textContent;
    ok('章数 > 0', parseInt(ch) > 0, ch);
    ok('字数 > 0', chars !== '0' && chars !== '—', chars);
    ok('页数 > 0', parseInt(pages) > 0, pages);

    const book = document.getElementById('book');
    const docCount = book.querySelectorAll('.doc').length;
    const coverCount = book.querySelectorAll('.coverpage').length;
    ok('节数 = 正文章数 + 目录页 + 封面页', docCount === parseInt(ch) + 1 + coverCount,
       docCount + ' vs ' + (parseInt(ch) + 1 + coverCount) + '（封面 ' + coverCount + '）');
    ok('生成目录页', !!book.querySelector('.tocpage'));
    ok('目录条目数 = 章数（不含封面）', book.querySelectorAll('.tocpage li').length === parseInt(ch),
       book.querySelectorAll('.tocpage li').length);
    ok('目录里没有「第 N 节」这种空标题',
       !/第 \\d+ 节/.test(book.querySelector('.tocpage').textContent || ''));
    const docList = [...book.querySelectorAll('.doc')];
    ok('第一页是封面', docList[0].classList.contains('coverpage'), docList[0].className);
    const afterCovers = docList.find(e => !e.classList.contains('coverpage'));
    ok('封面之后是目录（印刷顺序）', afterCovers.classList.contains('tocpage'), afterCovers.className);
    ok('正文有文字', (book.textContent || '').length > 500, (book.textContent || '').length + ' 字');

    const pageCss = [...document.head.querySelectorAll('style')].map(s => s.textContent).join('');
    ok('注入 @page 尺寸', /@page\\{size:148mm 210mm;margin:16mm\\}/.test(pageCss));

    const sheetW = Math.round(document.getElementById('sheet').getBoundingClientRect().width);
    ok('纸面宽度约 148mm(559px)', Math.abs(sheetW - 559) < 8, sheetW + 'px');

    const blobImgs = [...book.querySelectorAll('img')].filter(i => (i.getAttribute('src') || '').startsWith('blob:'));
    ok('插图已转 blob URL', blobImgs.length > 0, blobImgs.length + ' 张 / 共 ' + book.querySelectorAll('img').length);

    const leftScripts = book.querySelectorAll('script,style,link').length;
    ok('正文里没有脚本与外部样式', leftScripts === 0, leftScripts);

    document.getElementById('dlPdf').click();
    ok('导出 PDF 调用了打印', printed === 1, 'printed=' + printed);

    // 改纸张后应重排且不清空正文。
    // 注意要挑「明显更小」的纸才测得出重算：A4 比 A5 大，这本小书每章都只占一页，
    // 页数不变是正常结果，拿它当断言会误报。
    const sel = document.getElementById('page');
    sel.value = '107,142';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 500));
    const docCount2 = document.getElementById('book').querySelectorAll('.doc').length;
    ok('换纸张后正文不丢', docCount2 === docCount, docCount2 + ' vs ' + docCount);
    ok('换小纸后页数变多', parseInt(document.getElementById('mPages').textContent) > parseInt(pages),
       pages + ' → ' + document.getElementById('mPages').textContent);
    let pageCss2 = [...document.head.querySelectorAll('style')].map(s => s.textContent).join('');
    ok('6 英寸的 @page 已生效', /size:107mm 142mm/.test(pageCss2));
    sel.value = '210,297';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    pageCss2 = [...document.head.querySelectorAll('style')].map(s => s.textContent).join('');
    ok('A4 的 @page 已生效', /size:210mm 297mm/.test(pageCss2));

    // 复原成 A5 便于截图
    sel.value = '148,210';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 400));
    document.getElementById('stage').scrollIntoView({ block: 'start' });
    return out.join(' || ');
  })()`);

  await sleep(600);
  await screenshot("E:/WorkBuddy/.shots/epub2pdf-preview.png", { full: false });
  return { jszip: ready, result: r };
};
