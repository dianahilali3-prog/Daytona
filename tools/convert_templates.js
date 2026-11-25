const { chromium } = require('playwright');
const fs = require('fs'); const path=require('path');
(async () => {
  const [src, outdir] = [process.argv[2], process.argv[3]];
  const files = process.argv.slice(4);
  const b = await chromium.launch(); const p = await b.newPage();
  p.on('pageerror', e => {});
  await p.goto('http://127.0.0.1:8188/', {waitUntil:'networkidle'});
  await p.waitForFunction(() => window.app && window.app.graph, null, {timeout:120000});
  for (const f of files) {
    const wf = JSON.parse(fs.readFileSync(path.join(src,f),'utf8'));
    try {
      const res = await p.evaluate(async (wf) => {
        await window.app.loadGraphData(wf, true, true);
        await new Promise(r=>setTimeout(r,1500));
        const r = await window.app.graphToPrompt();
        return r.output;
      }, wf);
      fs.writeFileSync(path.join(outdir, f.replace('.json','.api.json')), JSON.stringify(res,null,1));
      console.log('OK', f, Object.keys(res).length);
    } catch(e) { console.log('FAIL', f, String(e).slice(0,300)); }
  }
  await b.close();
})();
