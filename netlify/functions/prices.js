const { buildNav } = require('../lib/nav');

const HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 'public, max-age=15',
};

exports.handler = async () => {
  try {
    const d = await buildNav();
    const body = {
      timestamp: d.timestamp,
      sk_square_price: d.sk_square.price,
      sk_hynix_price: d.sk_hynix.price,
      nav: d.nav,
    };
    return { statusCode: 200, headers: HEADERS, body: JSON.stringify(body) };
  } catch (e) {
    return { statusCode: 500, headers: HEADERS, body: JSON.stringify({ error: String(e.message || e) }) };
  }
};
