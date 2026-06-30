"""
SK스퀘어 NAV 할인율 대시보드 백엔드
- 주가: KRX(한국거래소) 정보데이터시스템 API로 실시간(지연시세) 조회
- 지분율 / 보유주식수 / 발행주식수: DART(전자공시) Open API로 수집
- 위 데이터를 결합하여 NAV(순자산가치) 및 NAV 할인율을 실시간 계산
"""

import ast
import io
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import urllib3
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)
CORS(app)

# ===== 설정 =====
DART_API_KEY = os.getenv('DART_API_KEY', '')

# 사내망 등 TLS 가로채기(자체서명 인증서) 환경 대응.
# 외부망/일반 PC에서는 .env 에 VERIFY_SSL=true 로 두는 것을 권장한다.
VERIFY_SSL = os.getenv('VERIFY_SSL', 'false').lower() in ('1', 'true', 'yes')
if not VERIFY_SSL:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 종목코드 (KRX) — DART 고유번호(corp_code)는 corpCode.xml에서 자동 매핑한다.
SK_SQUARE_STOCK = '402340'   # SK스퀘어 (KOSPI) — 011760은 현대코퍼레이션이므로 주의
SK_HYNIX_STOCK = '000660'    # SK하이닉스 (KOSPI)
HYNIX_NAME_KEYWORD = '하이닉스'   # 타법인 출자현황에서 SK하이닉스 행을 찾기 위한 키워드

# DART 키 없이도 대시보드가 동작하도록 한 "현재 기준" 하드코딩 펀더멘털.
# 지분율/주식수는 분기 공시 때만 바뀌므로 값이 갱신되면 아래만 수정하면 된다.
# (DART API Key가 설정되면 이 값 대신 실제 공시 데이터로 자동 대체된다.)
DEFAULT_FUNDAMENTALS = {
    'sq_shares': {              # SK스퀘어 주식총수
        'issued': 131958386,    # 발행주식 총수
        'treasury': 0,          # 자기주식
        'distributed': 131958386,  # 유통주식수
    },
    'holding': {                # SK스퀘어가 보유한 SK하이닉스 지분
        'company': 'SK하이닉스(주)',
        'shares': 146100000,    # 보유주식수
        'ratio': 20.07,         # 지분율(%)
        'book_value': 0,        # 장부가액(원) — 미사용 시 0
        'purpose': '경영참여',
    },
    'other_value': 0,           # 기타 출자/순현금 등 추가 NAV(원). 없으면 0
    'as_of': '현재 기준(하드코딩)',
}

# SK스퀘어 IR NAV 페이지 — '하이닉스 외' 자산가치를 여기서 파싱한다.
SKSQUARE_NAV_URL = 'https://www.sksquare.com/kor/ir/nav.do'
# 페이지 파싱 실패 시 폴백(조원 단위, 2026-06-25 IR 기준)
DEFAULT_OTHER_BREAKDOWN = [
    {'company': '티맵모빌리티', 'value_jo': 1.46},
    {'company': 'SK쉴더스', 'value_jo': 1.02},
    {'company': 'SK플래닛', 'value_jo': 0.62},
    {'company': '원스토어', 'value_jo': 0.42},
    {'company': '콘텐츠웨이브', 'value_jo': 0.26},
    {'company': '기타', 'value_jo': 0.56},
    {'company': '순현금(순차입금)', 'value_jo': 0.79},
]

# 사업연도/보고서 코드 탐색 순서 (최신 → 과거)
# 11011=사업보고서, 11014=3분기, 11012=반기, 11013=1분기
REPORT_CANDIDATES = [
    (str(datetime.now().year), '11011'),
    (str(datetime.now().year - 1), '11011'),
    (str(datetime.now().year), '11014'),
    (str(datetime.now().year), '11012'),
    (str(datetime.now().year), '11013'),
    (str(datetime.now().year - 1), '11014'),
]

# ===== 간단한 메모리 캐시 =====
_cache = {}


def cache_get(key, ttl):
    item = _cache.get(key)
    if item and (time.time() - item['t']) < ttl:
        return item['v']
    return None


def cache_set(key, value):
    _cache[key] = {'v': value, 't': time.time()}


def _to_int(value):
    """'1,234,567' / '-' / '' → int (실패 시 0)"""
    if value is None:
        return 0
    s = str(value).replace(',', '').replace(' ', '').strip()
    if s in ('', '-', '--'):
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _to_float(value):
    if value is None:
        return 0.0
    s = str(value).replace(',', '').replace('%', '').strip()
    if s in ('', '-', '--'):
        return 0.0
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


# ===== 주가 API (네이버 금융 실시간 — KRX/KOSPI 시세) =====
# KRX 정보데이터시스템의 무인증 엔드포인트는 사내망/봇 차단으로 'LOGOUT' 응답을 주는 경우가 많아,
# 동일한 한국거래소(KOSPI/KOSDAQ) 시세를 안정적으로 제공하는 네이버 금융 폴링 API를 사용한다.
NAVER_PRICE_URL = 'https://polling.finance.naver.com/api/realtime/domestic/stock/{code}'
NAVER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
}

_MARKET_STATUS_KO = {'OPEN': '장중', 'CLOSE': '장마감', 'PRE': '장전', 'AFTER': '장후'}


def get_stock_price(stock_code):
    """네이버 금융 실시간 시세 조회 (한국거래소 상장 종목)."""
    cache_key = f'price_{stock_code}'
    cached = cache_get(cache_key, ttl=15)  # 15초 캐시
    if cached is not None:
        return cached
    try:
        resp = requests.get(
            NAVER_PRICE_URL.format(code=stock_code),
            headers=NAVER_HEADERS, timeout=10, verify=VERIFY_SSL,
        )
        resp.raise_for_status()
        datas = resp.json().get('datas') or []
        if not datas:
            return {'code': stock_code, 'price': 0, 'change': 0.0, 'error': '시세 없음'}
        d = datas[0]
        ex = d.get('stockExchangeType') or {}

        # 정규장이 열려 있으면 정규장 주가, 정규장 마감 후 NXT가 열려 있으면 NXT 주가
        om = d.get('overMarketPriceInfo') or {}
        regular_open = d.get('marketStatus') == 'OPEN'
        over_open = om.get('overMarketStatus') == 'OPEN' and _to_int(om.get('overPrice')) > 0
        use_over = (not regular_open) and over_open
        if use_over:
            session = 'NXT 프리마켓' if om.get('tradingSessionType') == 'PRE_MARKET' else 'NXT 애프터마켓'
        else:
            session = _MARKET_STATUS_KO.get(d.get('marketStatus'), d.get('marketStatus'))

        result = {
            'code': stock_code,
            'name': d.get('stockName'),
            'price': _to_int(om.get('overPrice')) if use_over else _to_int(d.get('closePrice')),
            'change': _to_float(om.get('fluctuationsRatio')) if use_over else _to_float(d.get('fluctuationsRatio')),
            'change_price': _to_int(om.get('compareToPreviousClosePrice')) if use_over else _to_int(d.get('compareToPreviousClosePrice')),
            'regular_price': _to_int(d.get('closePrice')),
            'volume': _to_int(om.get('accumulatedTradingVolume')) if use_over else _to_int(d.get('accumulatedTradingVolume')),
            'market': ex.get('nameKor') or '코스피',
            'market_status': session,
            'is_nxt': bool(use_over),
            'traded_at': om.get('localTradedAt') if use_over else d.get('localTradedAt'),
            'date': datetime.now().strftime('%Y-%m-%d'),
            'source': '네이버금융(NXT 시간외)' if use_over else '네이버금융(KRX 정규장)',
        }
        cache_set(cache_key, result)
        return result
    except Exception as e:
        return {'code': stock_code, 'price': 0, 'change': 0.0, 'error': str(e)}


def get_price_history(stock_code, count=120):
    """네이버 차트 API로 일별 종가 딕셔너리 반환 {'YYYYMMDD': 종가}."""
    cache_key = f'hist_{stock_code}_{count}'
    cached = cache_get(cache_key, ttl=3600)  # 1시간 캐시
    if cached is not None:
        return cached
    url = (f'https://fchart.stock.naver.com/siseJson.nhn?symbol={stock_code}'
           f'&timeframe=day&count={count}&requestType=0')
    resp = requests.get(url, headers=NAVER_HEADERS, timeout=15, verify=VERIFY_SSL)
    resp.raise_for_status()
    rows = ast.literal_eval(resp.text.strip())  # [[헤더], [날짜,시,고,저,종,거래량,..], ...]
    out = {}
    for row in rows[1:]:
        try:
            out[str(row[0])] = int(row[4])  # 종가
        except (IndexError, ValueError, TypeError):
            continue
    cache_set(cache_key, out)
    return out


# ===== SK스퀘어 IR NAV 페이지 파싱 ('하이닉스 외' 자산가치) =====
def get_sksquare_other_value():
    """SK스퀘어 IR NAV 페이지에서 SK하이닉스를 제외한 자산가치를 파싱.

    반환: {'other_value': 원, 'breakdown': [{company, value_jo}], 'hynix_value_jo', 'source'}
    """
    cached = cache_get('sksq_other', ttl=21600)  # 6시간
    if cached is not None:
        return cached
    try:
        resp = requests.get(SKSQUARE_NAV_URL, headers=NAVER_HEADERS, timeout=15, verify=VERIFY_SSL)
        resp.raise_for_status()
        html = resp.text

        # 자산 목록 구간으로 한정(하이닉스 ~ 순현금)
        start = html.find('하이닉스')
        end = html.find('순현금')
        section = html[start:end + 300] if (start >= 0 and end > start) else html

        pairs = re.findall(r'<div>\s*([^<>]+?)\s*</div>\s*<div>\s*([\d,]+\.?\d*)\s*</div>', section)
        # 집계/소계 행은 개별 보유자산이 아니므로 제외
        SKIP_KW = ('합계', '총', 'NAV', '소계', 'total', 'Total', '조원')
        breakdown = []
        hynix_jo = 0.0
        for name, num in pairs:
            name = name.strip()
            try:
                val = float(num.replace(',', ''))
            except ValueError:
                continue
            if '하이닉스' in name:
                hynix_jo = val
            elif any(kw in name for kw in SKIP_KW):
                continue
            elif val >= 50:  # 비하이닉스 개별 자산은 50조 미만 → 집계행 방어
                continue
            else:
                breakdown.append({'company': name, 'value_jo': val})

        if not breakdown:
            raise ValueError('NAV 페이지에서 항목을 파싱하지 못함')

        other_jo = sum(x['value_jo'] for x in breakdown)
        result = {
            'other_value': int(round(other_jo * 1e12)),
            'breakdown': breakdown,
            'hynix_value_jo': hynix_jo,
            'source': 'SK스퀘어 IR (sksquare.com/kor/ir/nav.do)',
        }
        cache_set('sksq_other', result)
        return result
    except Exception as e:
        return {
            'other_value': int(round(sum(x['value_jo'] for x in DEFAULT_OTHER_BREAKDOWN) * 1e12)),
            'breakdown': [dict(x) for x in DEFAULT_OTHER_BREAKDOWN],
            'hynix_value_jo': 0.0,
            'source': f'폴백 기준값 (IR 페이지 조회 실패: {e})',
        }


# ===== DART (전자공시) Open API =====
def get_corp_code(stock_code):
    """corpCode.xml(zip)을 받아 종목코드 → DART 고유번호(corp_code) 매핑."""
    if not DART_API_KEY:
        return None
    table = cache_get('dart_corpcode_map', ttl=86400)  # 1일 캐시
    if table is None:
        url = 'https://opendart.fss.or.kr/api/corpCode.xml'
        resp = requests.get(url, params={'crtfc_key': DART_API_KEY}, timeout=30, verify=VERIFY_SSL)
        resp.raise_for_status()
        table = {}
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            root = ET.fromstring(zf.read(xml_name).decode('utf-8'))
            for item in root.iter('list'):
                sc = (item.findtext('stock_code') or '').strip()
                cc = (item.findtext('corp_code') or '').strip()
                if sc and sc != ' ':
                    table[sc] = cc
        cache_set('dart_corpcode_map', table)
    return table.get(stock_code)


def get_shares_outstanding(corp_code):
    """주식총수현황(stockTotqySttus)으로 발행주식수 / 유통주식수 조회."""
    url = 'https://opendart.fss.or.kr/api/stockTotqySttus.json'
    for bsns_year, reprt_code in REPORT_CANDIDATES:
        params = {
            'crtfc_key': DART_API_KEY,
            'corp_code': corp_code,
            'bsns_year': bsns_year,
            'reprt_code': reprt_code,
        }
        try:
            data = requests.get(url, params=params, timeout=10, verify=VERIFY_SSL).json()
        except Exception:
            continue
        if data.get('status') != '000' or 'list' not in data:
            continue
        common = None
        for row in data['list']:
            se = (row.get('se') or '')
            if '보통주' in se:
                common = row
                break
        target = common or data['list'][0]
        issued = _to_int(target.get('istc_totqy'))      # 발행주식 총수
        treasury = _to_int(target.get('tesstk_co'))      # 자기주식
        distributed = _to_int(target.get('distb_stock_co'))  # 유통주식수
        if issued or distributed:
            return {
                'issued': issued,
                'treasury': treasury,
                'distributed': distributed or (issued - treasury),
                'bsns_year': bsns_year,
                'reprt_code': reprt_code,
            }
    return None


def get_other_corp_investments(corp_code):
    """타법인 출자현황(otrCprInvstmntSttus)으로 지분율 / 보유주식수 / 장부가액 조회."""
    url = 'https://opendart.fss.or.kr/api/otrCprInvstmntSttus.json'
    for bsns_year, reprt_code in REPORT_CANDIDATES:
        params = {
            'crtfc_key': DART_API_KEY,
            'corp_code': corp_code,
            'bsns_year': bsns_year,
            'reprt_code': reprt_code,
        }
        try:
            data = requests.get(url, params=params, timeout=10, verify=VERIFY_SSL).json()
        except Exception:
            continue
        if data.get('status') != '000' or 'list' not in data:
            continue
        investments = []
        for row in data['list']:
            name = (row.get('inv_prm') or '').strip()
            if not name:
                continue
            investments.append({
                'company': name,
                'shares': _to_int(row.get('trmend_blce_qy')),          # 기말 보유주식수
                'ratio': _to_float(row.get('trmend_blce_qota_rt')),    # 기말 지분율(%)
                'book_value': _to_int(row.get('trmend_blce_acntbk_amount')),  # 기말 장부가액(원)
                'purpose': (row.get('invstmnt_purps') or '').strip(),
            })
        if investments:
            return {'list': investments, 'bsns_year': bsns_year, 'reprt_code': reprt_code}
    return None


# ===== NAV 계산 =====
def _load_fundamentals():
    """지분율/주식수 등 펀더멘털을 반환. DART 키가 있으면 실제 공시값, 없으면 하드코딩 기준값.

    반환: (fundamentals dict, source 라벨, warning 메시지 or None)
    """
    if not DART_API_KEY:
        return DEFAULT_FUNDAMENTALS, '기준값(하드코딩)', None

    dart = cache_get('dart_fundamentals', ttl=3600)
    if dart is None:
        sq_corp = get_corp_code(SK_SQUARE_STOCK)
        dart = {
            'sq_shares': get_shares_outstanding(sq_corp) if sq_corp else None,
            'investments': get_other_corp_investments(sq_corp) if sq_corp else None,
        }
        cache_set('dart_fundamentals', dart)

    sq_shares = dart['sq_shares']
    invest = dart['investments']
    holding = None
    if invest:
        holding = next((x for x in invest['list'] if HYNIX_NAME_KEYWORD in x['company']), None)

    if not (sq_shares and holding):
        return (DEFAULT_FUNDAMENTALS, '기준값(하드코딩)',
                'DART 조회 실패 → 하드코딩 기준값으로 표시합니다. (보고서 미공시 또는 키 오류)')

    others = [x for x in invest['list'] if x is not holding]
    return (
        {
            'sq_shares': sq_shares,
            'holding': holding,
            'others': others,
            'other_value': sum(max(x['book_value'], 0) for x in others),
            'as_of': f"DART {invest['bsns_year']} / 보고서 {invest['reprt_code']}",
        },
        'DART 공시',
        None,
    )


def build_nav_data():
    """실시간 주가 + (DART 또는 하드코딩) 지분/주식수를 결합하여 NAV·할인율 계산."""
    sq_price = get_stock_price(SK_SQUARE_STOCK)
    hynix_price = get_stock_price(SK_HYNIX_STOCK)

    result = {
        'timestamp': datetime.now().isoformat(),
        'sk_square': {'code': SK_SQUARE_STOCK, 'price': sq_price},
        'sk_hynix': {'code': SK_HYNIX_STOCK, 'price': hynix_price},
        'holding': None,
        'other_investments': [],
        'nav': None,
        'warnings': [],
    }

    fundamentals, source, warning = _load_fundamentals()
    result['data_source'] = source
    if warning:
        result['warnings'].append(warning)

    sq_shares = fundamentals['sq_shares']
    holding = dict(fundamentals['holding'])

    # '하이닉스 외' 자산가치는 SK스퀘어 IR 홈페이지에서 가져온다.
    ov = get_sksquare_other_value()
    other_value = ov['other_value']
    result['sk_square']['shares'] = sq_shares
    result['other_investments'] = ov['breakdown']
    result['other_value'] = other_value
    result['other_value_source'] = ov['source']

    # NAV(순자산가치) = SK하이닉스 지분 시장가치 + 기타 출자/자산
    hynix_unit = hynix_price.get('price', 0)
    hynix_stake_value = holding['shares'] * hynix_unit
    holding['market_value'] = hynix_stake_value
    holding['hynix_unit_price'] = hynix_unit
    result['holding'] = holding

    total_nav = hynix_stake_value + other_value
    shares_for_nav = sq_shares.get('distributed') or sq_shares.get('issued') or 0
    sq_unit = sq_price.get('price', 0)
    per_share_nav = (total_nav / shares_for_nav) if shares_for_nav else 0
    discount_rate = ((sq_unit - per_share_nav) / per_share_nav * 100) if per_share_nav else 0

    result['nav'] = {
        'total': total_nav,
        'per_share': round(per_share_nav),
        'current_price': sq_unit,
        'discount_rate': round(discount_rate, 2),
        'hynix_stake_value': hynix_stake_value,
        'other_value': other_value,
        'shares_for_nav': shares_for_nav,
        'dart_period': fundamentals.get('as_of', ''),
    }
    return result


# ===== API 엔드포인트 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.route('/', methods=['GET'])
def index():
    """대시보드 HTML 서빙 (모바일 등 다른 기기에서 http://<PC-IP>:5000/ 로 접속)."""
    return send_from_directory(BASE_DIR, 'sk_square_dashboard.html')


@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'ok',
        'dart_key_set': bool(DART_API_KEY),
        'timestamp': datetime.now().isoformat(),
    })


@app.route('/api/config', methods=['POST'])
def set_config():
    """DART API KEY 설정."""
    global DART_API_KEY
    data = request.json or {}
    if data.get('api_key'):
        DART_API_KEY = data['api_key'].strip()
        _cache.pop('dart_corpcode_map', None)   # 키 변경 시 캐시 무효화
        _cache.pop('dart_fundamentals', None)
        return jsonify({'status': 'API key updated'})
    return jsonify({'error': 'No API key provided'}), 400


@app.route('/api/prices', methods=['GET'])
def get_prices():
    """경량 폴링용: KRX 주가 + 재계산된 NAV 할인율만 반환."""
    data = build_nav_data()
    return jsonify({
        'timestamp': data['timestamp'],
        'sk_square_price': data['sk_square']['price'],
        'sk_hynix_price': data['sk_hynix']['price'],
        'nav': data['nav'],
    })


@app.route('/api/nav-history', methods=['GET'])
def get_nav_history():
    """과거 일별 주가로 계산한 NAV 할인율 시계열."""
    try:
        count = request.args.get('count', 120, type=int)
        fundamentals, source, _ = _load_fundamentals()
        holding_shares = fundamentals['holding']['shares']
        other_value = get_sksquare_other_value()['other_value']
        sq_shares = (fundamentals['sq_shares'].get('distributed')
                     or fundamentals['sq_shares'].get('issued') or 0)

        sq_hist = get_price_history(SK_SQUARE_STOCK, count)
        hy_hist = get_price_history(SK_HYNIX_STOCK, count)

        series = []
        for date in sorted(sq_hist.keys()):
            hy_close = hy_hist.get(date)
            if not hy_close or not sq_shares:
                continue
            per_share_nav = (hy_close * holding_shares + other_value) / sq_shares
            if per_share_nav <= 0:
                continue
            disc = (sq_hist[date] - per_share_nav) / per_share_nav * 100
            series.append({
                'date': f'{date[:4]}-{date[4:6]}-{date[6:]}',
                'price': sq_hist[date],
                'per_share_nav': round(per_share_nav),
                'discount_rate': round(disc, 2),
            })
        return jsonify({'data_source': source, 'count': len(series), 'series': series})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/nav-dashboard', methods=['GET'])
def get_nav_dashboard():
    """대시보드 전체 데이터."""
    try:
        return jsonify(build_nav_data())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # 0.0.0.0 바인딩: 같은 Wi-Fi의 휴대폰 등에서 http://<PC-IP>:5000 으로 접속 가능
    app.run(host='0.0.0.0', port=5000, debug=False)
