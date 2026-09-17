from flask import Flask, Response, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin, quote
from functools import wraps
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone
from math import hypot

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': os.environ.get('ADMIN_PASSWORD', '123')
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)
TEMPERATURE_URL = (
    'https://api.open-meteo.com/v1/forecast'
    '?latitude=40.8244&longitude=140.74&current=temperature_2m'
    '&timezone=Asia%2FTokyo'
)

DISASTER_REPORT_URL = os.environ.get('DISASTER_REPORT_URL', '').strip()
LOCATION_SETTINGS_URL = os.environ.get('LOCATION_SETTINGS_URL', '').strip()
WALK_EVENT_URL = os.environ.get('WALK_EVENT_URL', '').strip()

REGIONAL_DISASTER_STATUS = [
    {
        'name': '青森駅・中心部', 'symbol': '◎', 'level': '低',
        'risk': '地震、火災、帰宅困難', 'situation': '現在、大きな情報なし',
        'response': '周囲の建物や落下物に注意'
    },
    {
        'name': '東部（浅虫・小柳）', 'symbol': '△', 'level': '注意',
        'risk': '津波、高潮、河川の増水', 'situation': '沿岸部では海面変化に注意',
        'response': '海岸や川から離れ、高い場所へ避難'
    },
    {
        'name': '西部（三内・新城）', 'symbol': '◎', 'level': '低',
        'risk': '地震、土砂災害、火災', 'situation': '現在、大きな情報なし',
        'response': '避難経路と近隣施設を確認'
    },
    {
        'name': '浪岡地区', 'symbol': '△', 'level': '注意',
        'risk': '大雨、洪水、土砂災害', 'situation': '大雨時は河川の水位上昇に注意',
        'response': '気象情報を確認し、早めに避難'
    },
]

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_shelters():
    """避難所データをファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


INSTRUCTION_PRIORITIES = ('低', '中', '高')
INSTRUCTION_REGIONS = ('全地域', '北部', '南部')
INSTRUCTION_AGE_GROUPS = ('20代未満を含む', '20〜50代', '60代以上')


def instruction_priority(instruction):
    """旧データを含む指示から表示用の重要度を取得する"""
    priority = instruction.get('priority')
    if priority in INSTRUCTION_PRIORITIES:
        return priority
    content = instruction.get('content', '')
    return '高' if '土砂災害' in content or '危険' in content else '低'


def instruction_for_display(instruction):
    """指示一覧・詳細で使う不足項目を補完する"""
    result = dict(instruction)
    result['priority'] = instruction_priority(instruction)
    result['title'] = result.get('title') or '指示・発信'
    result['region'] = result.get('region') or '全地域'
    result['age_groups'] = [
        age_group for age_group in result.get('age_groups', [])
        if age_group in INSTRUCTION_AGE_GROUPS
    ]
    return result


def sort_instructions_newest_first(instruction_list):
    """通知日時を基準に新しい通知から並べる"""
    def sort_key(instruction):
        created_at = instruction.get('created_at', '')
        try:
            return datetime.strptime(created_at, '%Y年%m月%d日 %H:%M')
        except (TypeError, ValueError):
            return datetime.min

    return sorted(instruction_list, key=sort_key, reverse=True)


def instruction_form_values(source):
    """指示登録フォームの値を読み込み、許可値を検証する"""
    title = source.get('title', '').strip()
    content = source.get('content', '').strip()
    priority = source.get('priority', '')
    region = source.get('region', '')
    submitted_age_groups = source.getlist('age_groups')
    if '全て' in submitted_age_groups:
        age_groups = list(INSTRUCTION_AGE_GROUPS)
    else:
        age_groups = [age for age in submitted_age_groups if age in INSTRUCTION_AGE_GROUPS]
    valid = (
        bool(title) and bool(content) and priority in INSTRUCTION_PRIORITIES
        and region in INSTRUCTION_REGIONS and bool(age_groups)
    )
    return {
        'title': title,
        'content': content,
        'priority': priority,
        'region': region,
        'age_groups': age_groups,
        'valid': valid,
    }


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


SEARCH_CONDITIONS = ('pregnant', 'wheelchair', 'pet', 'disability')


def get_shelter_coordinates(shelter):
    """新形式と既存形式の座標を読み、無効な値は座標なしとして扱う"""
    latitude = shelter.get('latitude', shelter.get('lat'))
    longitude = shelter.get('longitude', shelter.get('lng'))
    try:
        return float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None, None


def shelter_form_values(source):
    """フォームまたはJSONから登録項目を読み取り、共通形式にする"""
    if request.is_json:
        data = request.get_json(silent=True) or {}
        name = str(data.get('name', '')).strip()
        address = str(data.get('address', '')).strip()
        capacity_value = data.get('capacity', '')
        support_options = data.get('support_options', [])
    else:
        name = source.get('name', '').strip()
        address = source.get('address', '').strip()
        capacity_value = source.get('capacity', '').strip()
        support_options = source.getlist('support_options')

    errors = []
    if not name:
        errors.append('避難所名は必須項目です。')
    if not address:
        errors.append('住所は必須項目です。')
    try:
        capacity = int(capacity_value)
        if capacity < 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append('最大収容人数は0以上の整数で入力してください。')
        capacity = None

    valid_options = {'車いす対応可', 'ペット同伴可', '福祉避難所'}
    if not isinstance(support_options, list):
        support_options = []
    support_options = [option for option in support_options if option in valid_options]
    return {
        'name': name,
        'address': address,
        'capacity': capacity if not errors else capacity_value,
        'support_options': support_options,
        'errors': errors,
    }


def geocode_address(address):
    """住所を地理座標へ変換する。失敗時はNoneを返す"""
    if not address:
        return None
    request_url = (
        'https://nominatim.openstreetmap.org/search'
        f'?q={quote(address)}&format=jsonv2&limit=1'
    )
    try:
        geocode_request = urllib.request.Request(
            request_url,
            headers={'User-Agent': 'bousai-app-shelter-geocoder/1.0'}
        )
        with urllib.request.urlopen(geocode_request, timeout=5) as response:
            candidates = json.loads(response.read().decode('utf-8'))
        if not candidates:
            return None
        return float(candidates[0]['lat']), float(candidates[0]['lon'])
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None


def apply_shelter_values(shelter, values):
    """新旧の保存形式を保ったまま登録項目を上書きする"""
    shelter.update({
        'name': values['name'],
        'address': values['address'],
        'capacity': values['capacity'],
        'support_options': values['support_options'],
        'wheelchair': '車いす対応可' in values['support_options'],
        'pet': 'ペット同伴可' in values['support_options'],
        'disability': '福祉避難所' in values['support_options'],
    })


CURRENT_LOCATION = {
    'address': '青森県青森市古川3丁目',
    'lat': 40.8223,
    'lng': 140.7224
}


def sort_shelters_by_distance(shelter_list):
    """現在地から近い順に避難所を並べる"""
    def distance(shelter):
        latitude, longitude = get_shelter_coordinates(shelter)
        if latitude is None or longitude is None:
            return float('inf')
        return hypot(
            latitude - CURRENT_LOCATION['lat'],
            longitude - CURRENT_LOCATION['lng']
        )

    return sorted(
        shelter_list,
        key=distance
    )


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """青森市の警報・注意報と現在気温を取得する"""
    result = {
        "area_name": AREA_NAME,
        "warnings": [],
        "temperature": None,
        "report_time": "不明",
        "last_fetch_time": get_japan_time(),
        "error": False,
    }
    try:
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)
        result['warnings'] = warnings
        result['report_time'] = format_report_time(report_datetime)
    except Exception:
        result['error'] = True
        result['error_message'] = '気象警報・注意報を取得できませんでした。'

    try:
        with urllib.request.urlopen(TEMPERATURE_URL, timeout=10) as res:
            temperature_data = json.loads(res.read())
        result['temperature'] = temperature_data.get('current', {}).get('temperature_2m')
    except Exception:
        result['error'] = True
        result['error_message'] = '現在気温を取得できませんでした。'

    return result


# トップページ
@app.route('/')
def index():
    resident_notices = [i for i in instructions if i.get('target') == '住民']
    return render_template(
        'index.html', resident_notices=resident_notices,
        disaster_regions=REGIONAL_DISASTER_STATUS,
        disaster_report_url=DISASTER_REPORT_URL,
        location_settings_url=LOCATION_SETTINGS_URL,
        walk_event_url=WALK_EVENT_URL,
        current_location=CURRENT_LOCATION,
        map_shelters=shelters,
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        values = shelter_form_values(request.form)
        if values['errors']:
            return render_template(
                'shelter_register.html',
                error=True,
                message=' '.join(values['errors']),
                form_values=values,
                checked_options=values['support_options'],
                current_location=CURRENT_LOCATION
            )

        shelter = {
            'id': max((shelter.get('id', 0) for shelter in shelters), default=0) + 1,
            'details': request.form.get('details', '').strip(),
        }
        apply_shelter_values(shelter, values)
        coordinates = geocode_address(values['address'])
        if coordinates:
            shelter['latitude'], shelter['longitude'] = coordinates
        shelters.append(shelter)
        save_shelters()
        return render_template(
            'shelter_register.html',
            success=True,
            message='避難所を登録しました。',
            current_location=CURRENT_LOCATION
        )

    return render_template('shelter_register.html', current_location=CURRENT_LOCATION)


@app.route('/api/geocode')
@login_required
def api_geocode():
    address = request.args.get('address', '').strip()
    if not address:
        return jsonify({'error': '住所を入力してください。'}), 400
    coordinates = geocode_address(address)
    if not coordinates:
        return jsonify({'error': '住所が見つからないか、住所検索サービスに接続できませんでした。'}), 404
    latitude, longitude = coordinates
    return jsonify({'latitude': latitude, 'longitude': longitude, 'display_name': address})


@app.route('/api/map-tiles/<int:zoom>/<int:x>/<int:y>.png')
def api_map_tiles(zoom, x, y):
    if not 0 <= zoom <= 19 or not 0 <= x < 2 ** zoom or not 0 <= y < 2 ** zoom:
        return jsonify({'error': '無効な地図タイルです。'}), 400
    tile_url = f'https://cyberjapanda.gsi.go.jp/xyz/std/{zoom}/{x}/{y}.png'
    try:
        tile_request = urllib.request.Request(
            tile_url,
            headers={'User-Agent': 'bousai-app-gsi-map-proxy/1.0'}
        )
        with urllib.request.urlopen(tile_request, timeout=5) as response:
            return Response(response.read(), mimetype='image/png')
    except OSError:
        return jsonify({'error': '地図タイルを取得できませんでした。'}), 502


@app.route('/api/shelters', methods=['GET'])
@login_required
def api_shelter_list():
    return jsonify([{'id': shelter.get('id'), 'name': shelter.get('name', '')} for shelter in shelters])


@app.route('/api/shelters/<int:shelter_id>', methods=['GET', 'PUT', 'PATCH', 'DELETE'])
@login_required
def api_shelter_detail(shelter_id):
    shelter = next((item for item in shelters if item.get('id') == shelter_id), None)
    if shelter is None:
        return jsonify({'error': '避難所が見つかりません。'}), 404
    if request.method == 'DELETE':
        shelters.remove(shelter)
        save_shelters()
        return jsonify({'message': '避難所を削除しました。'})
    if request.method == 'GET':
        support_options = shelter.get('support_options')
        if support_options is None:
            support_options = []
            if shelter.get('wheelchair'):
                support_options.append('車いす対応可')
            if shelter.get('pet'):
                support_options.append('ペット同伴可')
            if shelter.get('disability') or shelter.get('help'):
                support_options.append('福祉避難所')
        return jsonify({
            'id': shelter.get('id'),
            'name': shelter.get('name', ''),
            'address': shelter.get('address', ''),
            'capacity': shelter.get('capacity', ''),
            'support_options': support_options,
            'latitude': shelter.get('latitude', shelter.get('lat')),
            'longitude': shelter.get('longitude', shelter.get('lng')),
        })

    values = shelter_form_values(request.get_json(silent=True) or {})
    if values['errors']:
        return jsonify({'error': ' '.join(values['errors'])}), 400
    apply_shelter_values(shelter, values)
    coordinates = geocode_address(values['address'])
    if coordinates:
        shelter['latitude'], shelter['longitude'] = coordinates
    save_shelters()
    return jsonify({'message': '避難所を更新しました。'})

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    districts = sorted({shelter.get('district') for shelter in shelters if shelter.get('district')})
    return render_template(
        'shelter_search.html', districts=districts,
        shelters=shelters, current_location=CURRENT_LOCATION
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template(
        'search_results.html',
        results=sort_shelters_by_distance(
            filter_shelters(request.args.get('district'))
        ), current_location=CURRENT_LOCATION
    )


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = sort_instructions_newest_first([
        instruction_for_display(i)
        for i in instructions if i.get('target') == '住民'
    ])
    return render_template('board.html', instructions=resident_instructions)


@app.route('/instruction/new', methods=['GET', 'POST'])
@login_required
def instruction_new():
    form_values = {
        'title': '', 'content': '', 'priority': '', 'region': '', 'age_groups': []
    }
    if request.method == 'POST':
        form_values = instruction_form_values(request.form)
        if not form_values['valid']:
            return render_template(
                'instruction_new.html', error=True, form_values=form_values,
                priorities=INSTRUCTION_PRIORITIES, regions=INSTRUCTION_REGIONS,
                age_groups=INSTRUCTION_AGE_GROUPS
            )

        now = get_japan_time()
        instructions.append({
            'id': max((item.get('id', 0) for item in instructions), default=0) + 1,
            'target': '住民',
            'title': form_values['title'],
            'content': form_values['content'],
            'priority': form_values['priority'],
            'region': form_values['region'],
            'age_groups': form_values['age_groups'],
            'shelter': '',
            'status': '発信中',
            'created_at': now,
            'updated_at': now,
        })
        save_instructions()
        return redirect(url_for('board'))

    return render_template(
        'instruction_new.html', form_values=form_values,
        priorities=INSTRUCTION_PRIORITIES, regions=INSTRUCTION_REGIONS,
        age_groups=INSTRUCTION_AGE_GROUPS
    )


@app.route('/instruction/<int:instruction_id>')
@login_required
def instruction_detail(instruction_id):
    instruction = next(
        (item for item in instructions
         if item.get('id') == instruction_id and item.get('target') == '住民'),
        None
    )
    if instruction is None:
        return redirect(url_for('board'))
    return render_template(
        'instruction_detail.html', instruction=instruction_for_display(instruction)
    )


@app.route('/instruction/<int:instruction_id>/dismiss', methods=['POST'])
@login_required
def instruction_dismiss(instruction_id):
    instruction = next(
        (item for item in instructions
         if item.get('id') == instruction_id and item.get('target') == '住民'),
        None
    )
    if instruction is not None:
        instruction['status'] = '解除'
        instruction['updated_at'] = get_japan_time()
        save_instructions()
    return redirect(url_for('board'))

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results', methods=['GET', 'POST'])
def search_results():
    selected_conditions = []
    if request.method == 'POST':
        selected_conditions = [
            condition for condition in SEARCH_CONDITIONS
            if request.form.get(condition) == 'on'
        ]

    district = request.args.get('district') or request.form.get('district')
    results = filter_shelters(district)
    results = [
        shelter for shelter in results
        if all(shelter.get(condition, False) is True for condition in selected_conditions)
    ]
    return render_template(
        'search_results.html',
        results=results,
        current_location=CURRENT_LOCATION,
        selected_conditions=selected_conditions
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        return jsonify({'error': '該当する避難所が見つかりませんでした。'}), 404

    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
