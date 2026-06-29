const { buildNavHistory } = require('../lib/nav');

const HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 'public, max-age=3600',
};

exports.handler = async (event) => {
  try {
    const count = parseInt((event.queryStringParameters || {}).count, 10) || 120;
    const data = await buildNavHistory(count);
    return { statusCode: 200, headers: HEADERS, body: JSON.stringify(data) };
  } catch (e) {
    return { statusCode: 500, headers: HEADERS, body: JSON.stringify({ error: String(e.message || e) }) };
  }
};
