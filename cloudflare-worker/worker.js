// SK스퀘어 NAV 실시간 프록시 (Cloudflare Worker)
// - 네이버 금융(NXT 포함) 시세 + SK스퀘어 IR을 서버측에서 받아 CORS 붙여 전달
// - 엔드포인트:  GET /nav  (대시보드 전체),  GET /history  (NAV 할인율 시계열)
// 배포: Cloudflare 대시보드 → Workers → Create → 이 코드 붙여넣기 → Deploy
//       또는  wrangler deploy

const SK_SQUARE = '402340';
const SK_HYNIX = '000660';
const HOLDING = { company: 'SK하이닉스(주)', shares: 146100000, ratio: 20.07 };
const SQ_SHARES = { issued: 131958386, treasury: 0, distributed: 131958386 };

const SKSQUARE_NAV_URL = 'https://www.sksquare.com/kor/ir/nav.do';
const DEFAULT_OTHER_BREAKDOWN = [
  { company: '티맵모빌리티', value_jo: 1.46 },
  { company: 'SK쉴더스', value_jo: 1.02 },
  { company: 'SK플래닛', value_jo: 0.62 },
  { company: '원스토어', value_jo: 0.42 },
  { company: '콘텐츠웨이브', value_jo: 0.26 },
  { company: '기타', value_jo: 0.56 },
  { company: '순현금(순차입금)', value_jo: 0.79 },
];

const UA = {
  'User-Agent':
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
};
const MARKET_STATUS_KO = { OPEN: '장중', CLOSE: '장마감', PRE: '장전', AFTER: '장후' };

const toInt = (v) => {
  if (v === null || v === undefined) return 0;
  const n = parseInt(String(v).replace(/[, ]/g, ''), 10);
  return Number.isNaN(n) ? 0 : n;
};
const toFloat = (v) => {
  if (v === null || v === undefined) return 0;
  const n = parseFloat(String(v).replace(/[,%]/g, ''));
  return Number.isNaN(n) ? 0 : n;
};

async function fetchPrice(code) {
  try {
    const r = await fetch(
      `https://polling.finance.naver.com/api/realtime/domestic/stock/${code}`,
      { headers: UA }
    );
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const j = await r.json();
    const d = (j.datas || [])[0];
    if (!d) return { code, price: 0, change: 0, error: '시세 없음' };
    const ex = d.stockExchangeType || {};
    const om = d.overMarketPriceInfo;
    const regularOpen = d.marketStatus === 'OPEN';
    const overOpen = om && om.overMarketStatus === 'OPEN' && toInt(om.overPrice) > 0;
    const useOver = !regularOpen && overOpen;
    const sessionKo = useOver
      ? (om.tradingSessionType === 'PRE_MARKET' ? 'NXT 프리마켓' : 'NXT 애프터마켓')
      : (MARKET_STATUS_KO[d.marketStatus] || d.marketStatus);
    return {
      code,
      name: d.stockName,
      price: useOver ? toInt(om.overPrice) : toInt(d.closePrice),
      change: useOver ? toFloat(om.fluctuationsRatio) : toFloat(d.fluctuationsRatio),
      change_price: useOver ? toInt(om.compareToPreviousClosePrice) : toInt(d.compareToPreviousClosePrice),
      regular_price: toInt(d.closePrice),
      volume: useOver ? toInt(om.accumulatedTradingVolume) : toInt(d.accumulatedTradingVolume),
      market: ex.nameKor || '코스피',
      market_status: sessionKo,
      is_nxt: !!useOver,
      traded_at: useOver ? om.localTradedAt : d.localTradedAt,
      source: useOver ? '네이버금융(NXT 시간외)' : '네이버금융(KRX 정규장)',
    };
  } catch (e) {
    return { code, price: 0, change: 0, error: String(e.message || e) };
  }
}

async function fetchHistory(code, count = 120) {
  const r = await fetch(
    `https://fchart.stock.naver.com/siseJson.nhn?symbol=${code}&timeframe=day&count=${count}&requestType=0`,
    { headers: UA }
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const text = await r.text();
  const out = {};
  const re = /\["(\d{8})",\s*[\d.]+,\s*[\d.]+,\s*[\d.]+,\s*([\d.]+)/g;
  let m;
  while ((m = re.exec(text)) !== null) out[m[1]] = parseInt(m[2], 10);
  return out;
}

async function fetchOtherValue() {
  try {
    const r = await fetch(SKSQUARE_NAV_URL, { headers: UA });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const html = await r.text();
    const start = html.indexOf('하이닉스');
    const end = html.indexOf('순현금');
    const section = start >= 0 && end > start ? html.slice(start, end + 300) : html;
    const re = /<div>\s*([^<>]+?)\s*<\/div>\s*<div>\s*([\d,]+\.?\d*)\s*<\/div>/g;
    const SKIP = ['합계', '총', 'NAV', '소계', 'total', 'Total', '조원'];
    const breakdown = [];
    let m;
    while ((m = re.exec(section)) !== null) {
      const name = m[1].trim();
      const val = toFloat(m[2]);
      if (name.includes('하이닉스')) continue;
      if (SKIP.some((k) => name.includes(k))) continue;
      if (val >= 50) continue;
      breakdown.push({ company: name, value_jo: val });
    }
    if (!breakdown.length) throw new Error('파싱 항목 없음');
    const otherJo = breakdown.reduce((s, x) => s + x.value_jo, 0);
    return { other_value: Math.round(otherJo * 1e12), breakdown, source: 'SK스퀘어 IR (sksquare.com/kor/ir/nav.do)' };
  } catch (e) {
    const bd = DEFAULT_OTHER_BREAKDOWN.map((x) => ({ ...x }));
    return {
      other_value: Math.round(bd.reduce((s, x) => s + x.value_jo, 0) * 1e12),
      breakdown: bd,
      source: `폴백 기준값 (IR 조회 실패: ${e.message || e})`,
    };
  }
}

async function buildNav() {
  const [sqPrice, hyPrice, ov] = await Promise.all([fetchPrice(SK_SQUARE), fetchPrice(SK_HYNIX), fetchOtherValue()]);
  const hynixUnit = hyPrice.price || 0;
  const hynixStake = HOLDING.shares * hynixUnit;
  const totalNav = hynixStake + ov.other_value;
  const sharesForNav = SQ_SHARES.distributed || SQ_SHARES.issued || 0;
  const sqUnit = sqPrice.price || 0;
  const perShareNav = sharesForNav ? totalNav / sharesForNav : 0;
  const discount = perShareNav ? ((sqUnit - perShareNav) / perShareNav) * 100 : 0;
  return {
    timestamp: new Date().toISOString(),
    sk_square: { code: SK_SQUARE, price: sqPrice, shares: SQ_SHARES },
    sk_hynix: { code: SK_HYNIX, price: hyPrice },
    holding: { ...HOLDING, market_value: hynixStake, hynix_unit_price: hynixUnit },
    other_investments: ov.breakdown,
    other_value: ov.other_value,
    other_value_source: ov.source,
    nav: {
      total: totalNav,
      per_share: Math.round(perShareNav),
      current_price: sqUnit,
      discount_rate: Math.round(discount * 100) / 100,
      hynix_stake_value: hynixStake,
      other_value: ov.other_value,
      shares_for_nav: sharesForNav,
      dart_period: '',
    },
    data_source: '실시간(Cloudflare Worker)',
    warnings: [],
  };
}

async function buildNavHistory(count = 120) {
  const [sqHist, hyHist, ov] = await Promise.all([fetchHistory(SK_SQUARE, count), fetchHistory(SK_HYNIX, count), fetchOtherValue()]);
  const sharesForNav = SQ_SHARES.distributed || SQ_SHARES.issued || 0;
  const series = [];
  for (const date of Object.keys(sqHist).sort()) {
    const hy = hyHist[date];
    if (!hy || !sharesForNav) continue;
    const perShareNav = (hy * HOLDING.shares + ov.other_value) / sharesForNav;
    if (perShareNav <= 0) continue;
    const disc = ((sqHist[date] - perShareNav) / perShareNav) * 100;
    series.push({
      date: `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6)}`,
      price: sqHist[date],
      per_share_nav: Math.round(perShareNav),
      discount_rate: Math.round(disc * 100) / 100,
    });
  }
  return { data_source: '실시간(Cloudflare Worker)', count: series.length, series };
}

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const headers = {
      'Access-Control-Allow-Origin': '*',
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    };
    if (request.method === 'OPTIONS') return new Response(null, { headers });
    try {
      const data = url.pathname.endsWith('/history') ? await buildNavHistory(120) : await buildNav();
      return new Response(JSON.stringify(data), { headers });
    } catch (e) {
      return new Response(JSON.stringify({ error: String(e.message || e) }), { status: 500, headers });
    }
  },
};
