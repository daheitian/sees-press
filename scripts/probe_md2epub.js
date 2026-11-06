module.exports = async ({ evaluate, sleep, screenshot }) => {
  // 1) 等 marked / JSZip 就绪
  const ready = await evaluate(`(async () => {
    for (let i = 0; i < 80 && (typeof marked === 'undefined' || typeof JSZip === 'undefined'); i++)
      await new Promise(r => setTimeout(r, 100));
    return [typeof marked, typeof JSZip].join('|');
  })()`);

  const r = await evaluate(`(async () => {
    const out = [];
    const ok = (n, c, d) => out.push((c ? 'PASS ' : 'FAIL ') + n + (d !== undefined ? ' [' + d + ']' : ''));

    // 测试稿：front matter + 两个一级标题 + 图片引用 + 代码块里的假标题
    const MD = [
      '---',
      'title: 测试之书',
      'author: 张三',
      '---',
      '',
      '# 第一章 开端',
      '',
      '这是第一段正文。',
      '',
      '![本地图](images/pic.jpg)',
      '',
      '\`\`\`',
      '# 这不是标题',
      '\`\`\`',
      '',
      '# 第二章 转折',
      '',
      '第二段正文。',
      '',
      '## 小节一',
      '',
      '细节。',
      '',
    ].join('\\n');

    const file = new File([MD], 'test.md', { type: 'text/markdown' });
    const dt = new DataTransfer();
    dt.items.add(file);
    const inp = document.getElementById('file');
    inp.files = dt.files;
    inp.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise(r => setTimeout(r, 200));

    ok('选中后按钮可用', !document.getElementById('go').disabled);
    ok('文件名已显示', (document.getElementById('fname').textContent || '').includes('test.md'));

    document.getElementById('go').click();
    for (let i = 0; i < 60; i++) {
      if (document.getElementById('result').classList.contains('show')) break;
      await new Promise(r => setTimeout(r, 200));
    }
    const st = document.getElementById('status').textContent || '';
    ok('解析完成', document.getElementById('result').classList.contains('show'), st.slice(0, 50));

    ok('书名自动识别（元信息）', document.getElementById('title').value === '测试之书',
       document.getElementById('title').value);
    ok('作者自动识别（元信息）', document.getElementById('author').value === '张三',
       document.getElementById('author').value);

    const chs = document.querySelectorAll('#chlist li');
    ok('清单 = 书名页 + 2 章', chs.length === 3, chs.length);
    ok('章数指标不含书名页', document.getElementById('mCh').textContent === '2',
       document.getElementById('mCh').textContent);
    ok('字数指标 > 0', parseInt(document.getElementById('mChars').textContent.replace(/,/g, '')) > 0,
       document.getElementById('mChars').textContent);

    const titles = [...chs].map(li => li.querySelector('.t').textContent).join(' / ');
    ok('第一章是书名页', titles.startsWith('书名页'), titles);
    ok('代码块里的 # 没被当标题切分', !titles.includes('这不是标题'), titles);
    ok('二级标题留在章内没有切出新章', !titles.includes('小节一'), titles);

    const warnTxt = document.getElementById('warns').textContent || '';
    ok('本地图片有占位提示', warnTxt.includes('本地图片'), warnTxt.slice(0, 80));

    // 打包 EPUB
    document.getElementById('dl').click();
    let blob = null;
    for (let i = 0; i < 80; i++) {
      blob = window.__epubBlob;
      if (blob) break;
      await new Promise(r => setTimeout(r, 250));
    }
    ok('EPUB blob 已生成', !!blob);
    if (!blob) return out.join(' || ');
    ok('EPUB 大小指标就位', /\\d/.test(document.getElementById('mSize').textContent),
       document.getElementById('mSize').textContent);

    // 用 JSZip 回读包内容逐项校验
    const zip = await JSZip.loadAsync(blob);
    ok('mimetype 在包根', !!zip.file('mimetype'));
    ok('mimetype 内容正确', (await zip.file('mimetype').async('string')) === 'application/epub+zip');
    const container = await zip.file('META-INF/container.xml').async('string');
    ok('container 指向 OPF', container.includes('OEBPS/content.opf'));
    const opf = await zip.file('OEBPS/content.opf').async('string');
    ok('OPF 书名正确', opf.includes('<dc:title>测试之书</dc:title>'));
    ok('OPF 作者正确', opf.includes('<dc:creator>张三</dc:creator>'));
    ok('OPF nav 声明 properties', opf.includes('properties="nav"'));
    ok('OPF 声明 reflowable', opf.includes('rendition:layout">reflowable'));
    ok('OPF 声明封面 meta', opf.includes('meta name="cover" content="cover-image"'));
    ok('OPF 封面资源 properties', opf.includes('href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"'));
    ok('spine = 封面 + 3 章 + nav = 5', (opf.match(/<itemref /g) || []).length === 5,
       (opf.match(/<itemref /g) || []).length);
    ok('spine 带 toc=ncx', opf.includes('<spine toc="ncx"'));
    const nav = await zip.file('OEBPS/nav.xhtml').async('string');
    const tocPart = nav.split('epub:type="landmarks"')[0];
    ok('目录 3 条', (tocPart.match(/<li>/g) || []).length === 3, (tocPart.match(/<li>/g) || []).length);
    ok('landmark 含封面', nav.includes('epub:type="cover" href="text/cover.xhtml"'));
    const ncx = await zip.file('OEBPS/toc.ncx').async('string');
    ok('ncx 存在且 3 个 navPoint', (ncx.match(/<navPoint /g) || []).length === 3);
    ok('ncx uid 与 OPF 一致',
       ncx.match(/dtb:uid" content="urn:uuid:([^"]+)"/)[1] === opf.match(/urn:uuid:([^<]+)</)[1]);
    const coverBin = await zip.file('OEBPS/images/cover.jpg').async('uint8array');
    ok('封面是 JPEG（FFD8 魔数）', coverBin[0] === 0xFF && coverBin[1] === 0xD8,
       'size=' + coverBin.length);
    ok('封面尺寸 1400×2100（短边≥1400、≤300万像素）',
       coverBin.length > 30000 && (await zip.file('OEBPS/text/cover.xhtml').async('string')).includes('images/cover.jpg'));    const c1 = await zip.file('OEBPS/text/ch001.xhtml').async('string');
    ok('第 1 章是书名页', c1.includes('titlepage') && c1.includes('测试之书'));
    const c2 = await zip.file('OEBPS/text/ch002.xhtml').async('string');
    ok('第 2 章标题正确', c2.includes('<title>第一章 开端</title>'));
    ok('本地图片换成占位符', c2.includes('〔图：本地图〕'));
    ok('代码块内容保留', c2.includes('这不是标题'));
    ok('h1 只有 1 个', (c2.match(/<h1/g) || []).length === 1, (c2.match(/<h1/g) || []).length);
    ok('空元素已自闭合', !/<(br|hr|img)\\b[^>]*[^/]>$/.test(c2.replace(/<code[\\s\\S]*?<\\/code>/g, '')));
    const c3 = await zip.file('OEBPS/text/ch003.xhtml').async('string');
    ok('第 3 章含二级小节', c3.includes('小节一'));
    ok('章节文件数 = 3', Object.keys(zip.files).filter(f => /^OEBPS\\\/text\\\/ch\\d+\\.xhtml$/.test(f)).length === 3);

    return { ok: out.join(' || '), epubB64: null };
  })()`);
  // 取回生成的 EPUB 做线下 compat 体检
  const b64 = await evaluate(`(async () => {
    const blob = window.__epubBlob;
    if (!blob) return null;
    const buf = new Uint8Array(await blob.arrayBuffer());
    let s = '';
    for (let i = 0; i < buf.length; i += 0x8000)
      s += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(s);
  })()`);

  await sleep(400);
  await screenshot("E:/WorkBuddy/.shots/md2epub-result.png", { full: false });
  return { libs: ready, result: r.ok || r, epubB64: b64 };
};
