const { chromium } = require('playwright'); const fs = require('fs');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  const css = fs.readFileSync('project/deck.css', 'utf8');
  for (const id of JSON.parse(fs.readFileSync('project/deck.json','utf8')).order) {
    let s = fs.readFileSync('project/slides/' + id + '.html', 'utf8').replace(/<aside>[\s\S]*?<\/aside>/, '');
    await p.setContent(`<!doctype html><html><head><link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fredoka:wght@400..700&family=Nunito+Sans:wght@400;700&display=swap"><style>*{margin:0;box-sizing:content-box} body{margin:0} section{width:1920px;height:1080px;box-sizing:border-box;overflow:hidden} ${css} *{animation:none!important}</style></head><body>${s}</body></html>`, { waitUntil: 'networkidle' });
    const r = await p.evaluate(() => { const h = document.querySelector('h2'); if (!h) return null; const rg = document.createRange(); rg.selectNodeContents(h); return [h.offsetHeight, Math.round(rg.getBoundingClientRect().width), h.clientWidth]; });
    if (r && (r[0] > 75 || r[1] > r[2] - 10)) console.log('WRAP', id, r);
  }
  await b.close(); console.log('done');
})();
