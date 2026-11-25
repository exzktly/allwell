const { chromium } = require('playwright'); const fs = require('fs');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  const css = fs.readFileSync('project/deck.css', 'utf8');
  for (const id of ['st_vary']) {
    let s = fs.readFileSync('project/slides/' + id + '.html', 'utf8').replace(/<aside>[\s\S]*?<\/aside>/, '');
    await p.setContent(`<!doctype html><html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fredoka:wght@400..700&family=Nunito+Sans:wght@400;700&display=swap"><style>*{margin:0;box-sizing:content-box} body{margin:0} section{width:1920px;height:1080px;box-sizing:border-box;overflow:hidden} ${css} *{animation:none!important}</style></head><body>${s}</body></html>`, { waitUntil: 'networkidle' });
    await p.waitForTimeout(300);
    await p.screenshot({ path: 'prev_' + id + '.png' });
  }
  await b.close();
})();
