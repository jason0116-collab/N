// GitHub Actions에서 실행 — 네이버 시세 + SK스퀘어 IR로 NAV 데이터를 생성해
// data/nav.json, data/history.json 으로 저장한다. (GitHub Pages가 이 파일을 읽음)
const fs = require('fs');
const path = require('path');
const { buildNav, buildNavHistory } = require('../netlify/lib/nav');

(async () => {
  const outDir = path.join(__dirname, '..', 'data');
  fs.mkdirSync(outDir, { recursive: true });

  const nav = await buildNav();
  fs.writeFileSync(path.join(outDir, 'nav.json'), JSON.stringify(nav, null, 2));
  console.log('nav.json 생성: 할인율', nav.nav.discount_rate + '%',
    '| 주가', nav.sk_square.price.price, '| 하이닉스외', (nav.other_value / 1e12).toFixed(2) + '조');

  const hist = await buildNavHistory(120);
  fs.writeFileSync(path.join(outDir, 'history.json'), JSON.stringify(hist, null, 2));
  console.log('history.json 생성:', hist.count, '포인트');
})().catch((e) => {
  console.error('데이터 생성 실패:', e);
  process.exit(1);
});
