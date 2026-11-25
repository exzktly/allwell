const { chromium } = require('playwright'); const fs = require('fs');
const D = require('path').join(__dirname, 'project');
const deck = JSON.parse(fs.readFileSync(D + '/deck.json', 'utf8'));
const css = `* { margin: 0 } body { font: 32px/1.2 sans-serif } body > section { width: 1920px !important; height: 1080px !important; overflow: hidden !important; box-sizing: border-box !important; position: relative; isolation: isolate } aside { display: none !important } *{animation:none!important}`;
const links = Object.values(deck.faces).map(f => `<link rel="stylesheet" href="${f.href}">`).join('');
const body = deck.order.map(id => fs.readFileSync(D + '/slides/' + id + '.html', 'utf8')).join('\n');
const html = `<!doctype html><html><head>${links}<style>${css}${fs.readFileSync(D + '/deck.css', 'utf8')}</style></head><body>${body}</body></html>`;
(async () => {
  const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  await p.setContent(html, { waitUntil: 'networkidle' }); await p.waitForTimeout(500);
  const r = await p.evaluate(() => {
    const out = [];
    document.querySelectorAll('body > section').forEach(sec => {
      const divs = [...sec.querySelectorAll('div')];
      divs.forEach((host, hi) => {
        const kids = [...host.children].filter(c => getComputedStyle(c).position === 'absolute');
        if (!kids.length) return;
        const H = host.getBoundingClientRect();
        let l = 0, t = 0, rr = 0, bb = 0; const bad = [];
        kids.forEach(k => { const R = k.getBoundingClientRect();
          const dl = H.left - R.left, dt = H.top - R.top, dr = R.right - H.right, db = R.bottom - H.bottom;
          if (dl > 0.5 || dt > 0.5 || dr > 0.5 || db > 0.5) bad.push(`${k.tagName}"${k.textContent.trim().slice(0,30)}" l${dl.toFixed(0)} t${dt.toFixed(0)} r${dr.toFixed(0)} b${db.toFixed(0)}`);
          l = Math.max(l, dl); t = Math.max(t, dt); rr = Math.max(rr, dr); bb = Math.max(bb, db); });
        if (bad.length) out.push({ id: sec.id, hi, cls: host.className, l: Math.ceil(l), t: Math.ceil(t), r: Math.ceil(rr), b: Math.ceil(bb), bad });
      });
    });
    return out;
  });
  fs.writeFileSync('over.json', JSON.stringify(r, null, 1));
  for (const x of r) console.log(x.id, x.hi, x.cls, 'l', x.l, 't', x.t, 'r', x.r, 'b', x.b, '\n   ' + x.bad.join('\n   '));
  await b.close();
})();
