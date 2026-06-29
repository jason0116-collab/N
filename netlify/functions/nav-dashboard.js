const { buildNav } = require('../lib/nav');

const HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 'public, max-age=15',
};

exports.handler = async () => {
  try {
    const data = await buildNav();
    return { statusCode: 200, headers: HEADERS, body: JSON.stringify(data) };
  } catch (e) {
    return { statusCode: 500, headers: HEADERS, body: JSON.stringify({ error: String(e.message || e) }) };
  }
};
