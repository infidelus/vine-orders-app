from flask import Flask, render_template, request, redirect, url_for, jsonify, Response
import random
import re
import io
import logging
import requests
import sqlite3
import os
import threading
import time
from urllib.parse import urlparse, quote
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import markdown
import csv

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)

DATABASE = 'vine_orders.db'

# Backups are optional and switched on from the Settings page.  These are the
# starting values until you change them there.
DEFAULT_BACKUP_DIR = 'backups'   # A 'backups' folder inside the app's folder
DEFAULT_BACKUP_KEEP = 14

# Vine items become legally yours six months after the review date
OWNERSHIP_DAYS = 180

# Vine asks for a review within 30 days of ordering, and the dashboard warns
# about reviews falling due within the next DUE_SOON_DAYS days
REVIEW_DUE_DAYS = 30
DUE_SOON_DAYS = 7

# Desktop browsers only.  Mobile user agents make Amazon serve a completely
# different page layout, which the price and title look-ups below don't expect.
# A useful page for the current user agents is https://www.whatismybrowser.com/guides/the-latest-user-agent/
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0',
    'Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15',
]

# Sending only a User-Agent looks very unlike a real browser, so send the
# other headers a browser normally includes as well
BASE_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-GB,en;q=0.9',
    'Upgrade-Insecure-Requests': '1',
    'DNT': '1',
}

# Places Amazon puts the main "price to pay", most reliable first.  Amazon
# changes its layout from time to time, so if prices stop being found this
# is the list to update.
PRICE_SELECTORS = [
    '#corePriceDisplay_desktop_feature_div .priceToPay',
    '#corePriceDisplay_desktop_feature_div .a-price',
    '#corePrice_feature_div .a-price',
    '#corePrice_desktop .a-price',
    '#apex_desktop .priceToPay',
    '#apex_desktop .a-price',
    '#booksHeaderSection .a-price',
    '#tmmSwatches .a-button-selected .a-color-price',
    '#kindle-price',
    '#price_inside_buybox',
    '#newBuyBoxPrice',
    '#priceblock_ourprice',
    '#priceblock_dealprice',
    '#price',
]

# Short links (e.g. from the Amazon app's Share button) that need following
SHORT_LINK_HOSTS = ('amzn.eu', 'amzn.to', 'a.co', 'amzn.asia')

# Product codes (ASINs) are 10 letters/digits and appear after /dp/, /gp/product/ etc.
ASIN_PATTERN = re.compile(r'/(?:dp|gp/product|gp/aw/d|product|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?#]|$)', re.I)


def extract_asin(url):
    """Return the product code (ASIN) from an Amazon URL, or None."""
    match = ASIN_PATTERN.search(url or '')
    return match.group(1).upper() if match else None


def normalise_amazon_url(url):
    """
    Turn a long Amazon URL (full of tracking codes) into the short, tidy
    https://www.amazon.co.uk/dp/ASIN form.  This is both nicer to store and
    makes it easy to spot if you've already added the same item.
    Returns (tidy_url, asin).  If no ASIN can be found the URL is returned unchanged.
    """
    url = (url or '').strip()
    if url and not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    asin = extract_asin(url)
    if not asin:
        return url, None

    host = urlparse(url).netloc.lower()
    # Keep the same Amazon country site (co.uk, com, de...) the link was for
    if not host.startswith('www.') and host.startswith('amazon.'):
        host = 'www.' + host
    if 'amazon.' not in host:
        host = 'www.amazon.co.uk'
    return f'https://{host}/dp/{asin}', asin


def parse_price(text):
    """
    Turn price text such as '£1,299.99', '12,99 €' or '£15.00 - £20.00'
    into a number.  Returns None if no sensible price can be found.
    """
    if not text:
        return None
    # For a price range, use the first (lowest) price
    match = re.search(r'\d[\d.,\s]*', text)
    if not match:
        return None
    number = re.sub(r'\s', '', match.group(0)).rstrip('.,')

    if ',' in number and '.' in number:
        # Whichever comes last is the decimal point
        if number.rfind(',') > number.rfind('.'):
            number = number.replace('.', '').replace(',', '.')
        else:
            number = number.replace(',', '')
    elif ',' in number:
        # '12,99' is a decimal comma; '1,299' is a thousands separator
        if re.search(r',\d{2}$', number):
            number = number.replace(',', '.')
        else:
            number = number.replace(',', '')

    try:
        price = float(number)
    except ValueError:
        return None
    return price if price > 0 else None


def price_from_element(element):
    """Read a price from one of Amazon's price elements."""
    # The hidden 'a-offscreen' span holds the full price as text (e.g. '£12.99')
    offscreen = element.select_one('.a-offscreen')
    if offscreen and offscreen.get_text(strip=True):
        return parse_price(offscreen.get_text(strip=True))

    # Newer layouts sometimes leave that empty and split the price into parts
    whole = element.select_one('.a-price-whole')
    if whole:
        fraction = element.select_one('.a-price-fraction')
        whole_text = re.sub(r'[^\d]', '', whole.get_text())
        fraction_text = re.sub(r'[^\d]', '', fraction.get_text()) if fraction else '00'
        return parse_price(f'{whole_text}.{fraction_text or "00"}')

    return parse_price(element.get_text(' ', strip=True))


def find_price(soup):
    """Try each known price location in turn."""
    for selector in PRICE_SELECTORS:
        for element in soup.select(selector):
            # Skip per-unit prices such as '£2.50 / 100 g' and crossed-out RRPs
            classes = ' '.join(element.get('class', []))
            if 'a-text-price' in classes or element.find_parent(class_='pricePerUnit'):
                continue
            price = price_from_element(element)
            if price:
                logging.info(f"Price {price} found using '{selector}'")
                return price

    # Some pages keep the price in a hidden form field
    for field_id in ('twister-plus-price-data-price', 'attach-base-product-price'):
        field = soup.find('input', id=field_id)
        if field and parse_price(field.get('value')):
            logging.info(f"Price found in hidden field '{field_id}'")
            return parse_price(field.get('value'))

    return None


# Pictures are stored as the address of a small version of Amazon's own image
# (nothing is downloaded or saved on the server).  Amazon resizes its images on
# request using a code in the address, e.g. ._AC_SL160_ = at most 160 pixels.
THUMBNAIL_SIZE_CODE = '._AC_SL160_'
AMAZON_IMAGE_PATTERN = re.compile(
    r'^https://(?:m\.media-amazon\.com|images-[a-z]+\.ssl-images-amazon\.com|[a-z0-9-]+\.media-amazon\.com)'
    r'(/images/I/[^./?#]+)(?:\.[^/?#]*?)?\.(jpg|jpeg|png|webp|gif)(?:[?#].*)?$', re.I)


def thumbnail_url(image_url):
    """
    Turn any Amazon product image address into the address of a small
    (160 pixel) version.  Returns None if it isn't an Amazon product image,
    so only Amazon picture addresses are ever stored.
    """
    match = AMAZON_IMAGE_PATTERN.match((image_url or '').strip())
    if not match:
        return None
    return f"https://m.media-amazon.com{match.group(1)}{THUMBNAIL_SIZE_CODE}.{match.group(2).lower()}"


def find_image(soup):
    """Find the main product picture and return the small version's address."""
    for element_id in ('landingImage', 'imgBlkFront', 'ebooksImgBlkFront', 'main-image'):
        tag = soup.find(id=element_id)
        if tag:
            for attribute in ('data-old-hires', 'src'):
                small = thumbnail_url(tag.get(attribute))
                if small:
                    return small
            # Some pages list the image sizes in a data attribute instead
            for candidate in re.findall(r'"(https://[^"]+)"', tag.get('data-a-dynamic-image', '')):
                small = thumbnail_url(candidate)
                if small:
                    return small

    meta = soup.find('meta', attrs={'property': 'og:image'})
    if meta:
        return thumbnail_url(meta.get('content'))
    return None


def find_title(soup):
    """Find the product title, falling back to the page's meta data."""
    for element_id in ('productTitle', 'ebooksProductTitle', 'title'):
        tag = soup.find(id=element_id)
        if tag and tag.get_text(strip=True):
            return ' '.join(tag.get_text().split())

    for attrs in ({'name': 'title'}, {'property': 'og:title'}):
        meta = soup.find('meta', attrs=attrs)
        if meta and meta.get('content'):
            return tidy_page_title(meta['content'])

    if soup.title and soup.title.string:
        return tidy_page_title(soup.title.string)
    return ''


def tidy_page_title(title):
    """Strip the 'Amazon.co.uk: ' prefix and ': Amazon.co.uk: Category' suffix."""
    title = ' '.join(title.split())
    title = re.sub(r'^Amazon\.[a-z.]+\s*:\s*', '', title, flags=re.I)
    title = re.split(r'\s*:\s*Amazon\.[a-z.]+', title, flags=re.I)[0]
    # A bare 'Amazon.co.uk' title means we didn't get a product page at all
    return '' if re.fullmatch(r'Amazon\.[a-z.]+', title, flags=re.I) else title


def looks_blocked(response, soup):
    """
    Amazon often answers automated requests with a 'Continue shopping' or
    'Enter the characters you see' page.  It arrives with a normal 200 status,
    so it has to be recognised by its content.
    """
    if response.status_code in (429, 503):
        return True
    if soup.find(id='productTitle'):
        return False
    text = response.text
    return ('validateCaptcha' in text
            or 'api-services-support@amazon.com' in text
            or 'Sorry, we just need to make sure you' in text)


def scrape_price_and_description(url):
    """
    Fetch an Amazon product page and pull out the title and price.
    Always returns a dictionary with 'description', 'price' (a number or None),
    'url', 'asin' and, if something went wrong, a human-readable 'message'.
    """
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    session.headers['User-Agent'] = random.choice(USER_AGENTS)

    result = {'description': '', 'price': None, 'image_url': None, 'url': url, 'asin': None, 'message': ''}

    try:
        # Follow short links first so we can find the product code
        if urlparse(url if '://' in url else 'https://' + url).netloc.lower() in SHORT_LINK_HOSTS:
            response = session.get(url if '://' in url else 'https://' + url, timeout=10, allow_redirects=True)
            url = response.url

        tidy_url, asin = normalise_amazon_url(url)
        result['url'] = tidy_url
        result['asin'] = asin
        logging.info(f"Fetching {tidy_url} using User-Agent: {session.headers['User-Agent']}")

        response = session.get(tidy_url, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')

        if looks_blocked(response, soup):
            logging.warning("Amazon returned its bot-check page rather than the product")
            result['message'] = ("Amazon didn't show the product page (it served its 'are you a robot?' "
                                 "page instead). Try again in a minute, or use the 'Add to Vine Orders' "
                                 "browser button while you're on the Amazon page.")
            return result

        if response.status_code == 404:
            result['message'] = "Amazon says that page doesn't exist - please check the URL."
            return result
        response.raise_for_status()

        result['description'] = find_title(soup)
        result['price'] = find_price(soup)
        result['image_url'] = find_image(soup)
        logging.info(f"Description: {result['description']!r}  Price: {result['price']}")

        if not result['description']:
            result['message'] = "Couldn't find the product details on that page."
        elif result['price'] is None:
            result['message'] = ("Found the product but not a price - Vine items often have no price "
                                 "until they go on general sale, so you may need to enter it yourself.")
        return result

    except requests.RequestException as e:
        logging.error(f"Error fetching URL {url}: {e}")
        result['message'] = f"Couldn't reach Amazon ({e.__class__.__name__}). Please try again."
        return result


def find_existing_orders(asin, url):
    """Return any orders already in the database for the same product."""
    conn = get_db_connection()
    if asin:
        rows = conn.execute('SELECT id, date_ordered FROM orders WHERE url LIKE ? ORDER BY date_ordered DESC',
                            (f'%{asin}%',)).fetchall()
    else:
        rows = conn.execute('SELECT id, date_ordered FROM orders WHERE url = ? ORDER BY date_ordered DESC',
                            (url,)).fetchall()
    conn.close()
    return [{'id': row['id'], 'date_ordered': datetimeformat(row['date_ordered'])} for row in rows]


@app.route('/fetch-product-info', methods=['POST'])
def fetch_product_info():
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()

    if not url:
        logging.warning("No URL provided in request.")
        return jsonify({"message": "Please paste a product URL first."}), 400

    product_info = scrape_price_and_description(url)
    product_info['existing'] = find_existing_orders(product_info['asin'], product_info['url'])
    return jsonify(product_info)


# Check whether a product is already in the database (used after the browser button)
@app.route('/check-existing')
def check_existing():
    url, asin = normalise_amazon_url(request.args.get('url', ''))
    return jsonify({'existing': find_existing_orders(asin, url) if url else []})


# The browser button ("bookmarklet").  When clicked on an Amazon product page it
# reads the title, price and picture straight from the page you are looking at - so
# Amazon's bot checks never come into it - then opens this app with the
# 'Add a New Order' form already filled in.  {app_url} is filled in when the
# dashboard is shown.
BOOKMARKLET_JS = """(function(){
var d=document,q=function(s){return d.querySelector(s);};
var t=q('#productTitle')||q('#ebooksProductTitle');
var title=t?t.textContent.trim().replace(/\\s+/g,' '):d.title;
var sels=['#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen','#corePriceDisplay_desktop_feature_div .a-price .a-offscreen','#corePrice_feature_div .a-price .a-offscreen','#corePrice_desktop .a-price .a-offscreen','#apex_desktop .a-price .a-offscreen','#kindle-price','#price_inside_buybox','#priceblock_ourprice','#price'];
var price='';
for(var i=0;i<sels.length&&!price;i++){var e=q(sels[i]);if(e&&e.textContent.trim()){price=e.textContent.trim();}}
var a=q('#ASIN'),m=location.href.match(/\\/(?:dp|gp\\/product)\\/([A-Z0-9]{10})/i);
var asin=a?a.value:(m?m[1]:'');
var url=asin?location.origin+'/dp/'+asin:location.href;
var image='',ok=function(s){return s&&s.indexOf('http')===0&&s.indexOf('/images/I/')>0;};
var ims=d.querySelectorAll('#landingImage,#imgBlkFront,#ebooksImgBlkFront,#imgTagWrapperId img,#main-image-container img,#imageBlock img,#main-image');
for(var j=0;j<ims.length&&!image;j++){var im=ims[j],c=[im.getAttribute('data-old-hires'),im.currentSrc,im.getAttribute('src')];
try{c=c.concat(Object.keys(JSON.parse(im.getAttribute('data-a-dynamic-image')||'{}')));}catch(x){}
for(var k=0;k<c.length&&!image;k++){if(ok(c[k])){image=c[k];}}}
if(!image){var og=q('meta[property="og:image"]');if(og&&ok(og.content)){image=og.content;}}
window.open('{app_url}?v={version}&url='+encodeURIComponent(url)+'&description='+encodeURIComponent(title)+'&price='+encodeURIComponent(price)+'&image='+encodeURIComponent(image),'_blank');
})();"""


# Bumped whenever the browser button's code changes, so the app can tell
# when someone is still using an old copy in their bookmarks bar
BOOKMARKLET_VERSION = '3'


def build_bookmarklet(app_url):
    """Return the javascript: link for the browser button, pointing at this app."""
    js = BOOKMARKLET_JS.replace('{app_url}', app_url).replace('{version}', BOOKMARKLET_VERSION)
    js = ''.join(line.strip() for line in js.splitlines())
    return 'javascript:' + quote(js, safe="(){}[]'=+:;,.!?/*&|<>-_~")


# Function to get a database connection
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # Ensure rows behave like dictionaries
    return conn


def review_due_info(date_ordered, review_date):
    """
    Work out when an unreviewed item's review is due and whether it's
    'overdue', 'due_soon' or neither.  Returns (due_date, status), or
    (None, None) once the item has been reviewed.
    """
    if review_date or not date_ordered:
        return None, None
    try:
        due = datetime.strptime(date_ordered, '%Y-%m-%d').date() + timedelta(days=REVIEW_DUE_DAYS)
    except ValueError:
        return None, None
    today = datetime.now().date()
    if due < today:
        return due, 'overdue'
    if due <= today + timedelta(days=DUE_SOON_DAYS):
        return due, 'due_soon'
    return due, None


def review_due_cutoffs():
    """
    The order dates that matter for reminders, as yyyy-mm-dd text:
    anything ordered before 'overdue_before' is overdue, and anything ordered
    from then up to 'due_soon_before' is due within DUE_SOON_DAYS.
    """
    today = datetime.now().date()
    overdue_before = today - timedelta(days=REVIEW_DUE_DAYS)
    due_soon_before = overdue_before + timedelta(days=DUE_SOON_DAYS)
    return overdue_before.strftime('%Y-%m-%d'), due_soon_before.strftime('%Y-%m-%d')


# Custom filter for formatting datetime in dd/mm/yyyy format
@app.template_filter('datetimeformat')
def datetimeformat(value, days=0, format='%d/%m/%Y'):
    if value:
        try:
            date_obj = datetime.strptime(value, '%Y-%m-%d')
        except (TypeError, ValueError):
            # Not a yyyy-mm-dd date (e.g. 'Complete'), so return it unchanged
            return value
        date_obj += timedelta(days=days)  # Add the specified number of days
        return date_obj.strftime(format)
    return value  # In case value is None, return it unchanged


# --- Settings (stored in the database's 'settings' table) ---

def get_setting(key, default=None):
    """Read one setting, or return the default if it hasn't been set."""
    conn = get_db_connection()
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()


def backup_settings():
    """Return (enabled, folder, number_to_keep) from the saved settings."""
    enabled = get_setting('backup_enabled', '0') == '1'
    folder = get_setting('backup_dir', DEFAULT_BACKUP_DIR)
    try:
        keep = max(1, int(get_setting('backup_keep', DEFAULT_BACKUP_KEEP)))
    except ValueError:
        keep = DEFAULT_BACKUP_KEEP
    return enabled, os.path.abspath(folder), keep


def check_backup_folder(folder):
    """
    Make sure a backup folder exists and can be written to.
    Returns an error message, or None if all is well.
    """
    try:
        os.makedirs(folder, exist_ok=True)
        test_file = os.path.join(folder, '.vine_write_test')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
    except OSError as e:
        return f"Can't use that folder: {e.strerror or e}"
    return None


# --- Backups ---

def list_backups(folder):
    """Return the backup files in a folder, newest first."""
    if not os.path.isdir(folder):
        return []
    names = [name for name in os.listdir(folder)
             if name.startswith('vine_orders-') and name.endswith('.db')]
    return sorted(names, reverse=True)


def copy_database(target_path):
    """
    Copy the database to target_path using SQLite's own backup method, which
    is safe even if the app is in the middle of saving something.  It writes
    to a temporary name first, so a half-finished copy is never mistaken for
    a good one.
    """
    temp_path = target_path + '.tmp'
    source = sqlite3.connect(DATABASE)
    target = sqlite3.connect(temp_path)
    with target:
        source.backup(target)
    target.close()
    source.close()
    os.replace(temp_path, target_path)


def backup_database(force=False):
    """
    Make today's backup (if backups are switched on and there isn't one
    already), then delete the oldest backups beyond the number to keep.
    force=True makes a backup now even if switched off or one exists today.
    Returns the backup's file name, or None if nothing was done.
    """
    enabled, folder, keep = backup_settings()
    if not (enabled or force) or not os.path.exists(DATABASE):
        return None

    os.makedirs(folder, exist_ok=True)
    backup_name = f"vine_orders-{datetime.now().strftime('%Y-%m-%d')}.db"
    backup_path = os.path.join(folder, backup_name)

    if force or not os.path.exists(backup_path):
        copy_database(backup_path)
        logging.info(f"Backup saved: {backup_path}")

    # Remove the oldest backups beyond the number we're keeping
    for old_name in list_backups(folder)[keep:]:
        os.remove(os.path.join(folder, old_name))
        logging.info(f"Old backup removed: {old_name}")

    return backup_name


def backup_loop():
    """Runs in the background: checks once an hour whether today's backup is due."""
    while True:
        try:
            backup_database()
        except Exception as e:
            logging.error(f"Automatic backup failed: {e}")
        time.sleep(60 * 60)


# Function to initialise the database and apply any schema migrations
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Create the orders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            description TEXT,
            price REAL,
            date_ordered TEXT,
            review_due_date TEXT,
            review_date TEXT,
            review TEXT
        )
    ''')

    # Create the settings table (used for the backup options)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # Create the review_periods table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS review_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL
        )
    ''')

    # --- Migrations for existing databases ---
    # Add any columns from newer versions that are not already present
    cursor.execute('PRAGMA table_info(orders)')
    existing_columns = {row['name'] for row in cursor.fetchall()}

    new_columns = {
        # Sales tracking (added in v2.0)
        'date_sold': 'TEXT',
        'sale_price': 'REAL',
        'sale_platform': 'TEXT',
        'sale_fees': 'REAL',
        # Address of a small product picture on Amazon (added in v2.2)
        'image_url': 'TEXT',
    }
    for column_name, column_type in new_columns.items():
        if column_name not in existing_columns:
            cursor.execute(f'ALTER TABLE orders ADD COLUMN {column_name} {column_type}')
            logging.info(f"Migration: added column '{column_name}' to orders table")

    # Data tidy-up: prices stored as empty strings should be NULL so they
    # display consistently as 'N/A' rather than confusing the SUM() stats
    cursor.execute("""
        UPDATE orders SET price = NULL
        WHERE typeof(price) = 'text' AND trim(price) = ''
    """)
    # Any remaining text prices are converted to real numbers
    cursor.execute("""
        UPDATE orders SET price = CAST(price AS REAL)
        WHERE typeof(price) = 'text'
    """)

    # Check if review_periods is empty and insert a default period if necessary
    cursor.execute('SELECT COUNT(*) FROM review_periods')
    count = cursor.fetchone()[0]
    if count == 0:
        # Insert a default review period
        cursor.execute('''
            INSERT INTO review_periods (start_date, end_date)
            VALUES (DATE('now', 'start of month'), DATE('now', 'start of month', '+1 month', '-1 day'))
        ''')

    conn.commit()
    conn.close()


# Initialise the database, then start the daily backup checks (which do
# nothing unless backups are switched on in Settings)
init_db()
threading.Thread(target=backup_loop, daemon=True, name='daily-backup').start()


# Helper function to get the current review period from the database
def get_current_review_period():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT start_date, end_date FROM review_periods ORDER BY id DESC LIMIT 1')
    row = cursor.fetchone()
    conn.close()

    if row:
        start_date = datetime.strptime(row['start_date'], '%Y-%m-%d').strftime('%d/%m/%Y')
        end_date = datetime.strptime(row['end_date'], '%Y-%m-%d').strftime('%d/%m/%Y')
        return start_date, end_date
    else:
        return None, None


@app.route('/get_review_period_stats', methods=['GET'])
def get_review_period_stats():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    conn = get_db_connection()
    cursor = conn.cursor()

    # Query to get items ordered during the review period
    items_ordered_review_period = cursor.execute('''
        SELECT COUNT(*) FROM orders WHERE date_ordered BETWEEN ? AND ?
    ''', (start_date, end_date)).fetchone()[0]

    # Query to get items reviewed during the review period
    items_reviewed_review_period = cursor.execute('''
        SELECT COUNT(*) FROM orders WHERE review_date BETWEEN ? AND ?
    ''', (start_date, end_date)).fetchone()[0]

    conn.close()

    # Calculate the review percentage
    review_percentage = (items_reviewed_review_period / items_ordered_review_period * 100) if items_ordered_review_period > 0 else 0
    # Same rule as the dashboard: at least 80 items ordered and 90% reviewed
    meets_requirements = items_ordered_review_period >= 80 and review_percentage >= 90
    review_percentage_color = "green" if meets_requirements else "red"

    return jsonify({
        'items_ordered_review_period': items_ordered_review_period,
        'items_reviewed_review_period': items_reviewed_review_period,
        'review_percentage': f'{review_percentage:.2f}',
        'review_percentage_color': review_percentage_color
    })


# Home page with form for adding a new order and displaying statistics
@app.route('/')
def index():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Query to get the total number of orders
    cursor.execute('SELECT COUNT(*) FROM orders')
    total_orders = cursor.fetchone()[0]

    # Query to get the number of items ordered in the current year
    cursor.execute('''
        SELECT COUNT(*) FROM orders
        WHERE strftime('%Y', date_ordered) = strftime('%Y', 'now')
    ''')
    items_ordered_current_year = cursor.fetchone()[0]

    # Total value of items ordered
    cursor.execute('SELECT SUM(price) FROM orders')
    total_value = cursor.fetchone()[0] or 0
    total_value = f"{total_value:.2f}"  # Format the number to 2 decimal places as a string

    # Value of items ordered this year
    cursor.execute('''
        SELECT SUM(price) FROM orders
        WHERE strftime('%Y', date_ordered) = strftime('%Y', 'now')
    ''')
    current_year_value = cursor.fetchone()[0] or 0
    current_year_value = f"{current_year_value:.2f}"  # Format the number to 2 decimal places as a string

    # --- Sales statistics ---
    cursor.execute('''
        SELECT COUNT(*), SUM(sale_price), SUM(COALESCE(sale_fees, 0))
        FROM orders WHERE date_sold IS NOT NULL
    ''')
    row = cursor.fetchone()
    items_sold = row[0] or 0
    total_sales = row[1] or 0
    total_fees = row[2] or 0
    net_sales = total_sales - total_fees

    # Sales in the current year
    cursor.execute('''
        SELECT SUM(sale_price) FROM orders
        WHERE date_sold IS NOT NULL
        AND strftime('%Y', date_sold) = strftime('%Y', 'now')
    ''')
    sales_current_year = cursor.fetchone()[0] or 0

    # --- Review reminders ---
    overdue_before, due_soon_before = review_due_cutoffs()
    cursor.execute('''
        SELECT COUNT(*) FROM orders
        WHERE review_date IS NULL AND date_ordered < ?
    ''', (overdue_before,))
    reviews_overdue = cursor.fetchone()[0]
    cursor.execute('''
        SELECT COUNT(*) FROM orders
        WHERE review_date IS NULL AND date_ordered >= ? AND date_ordered <= ?
    ''', (overdue_before, due_soon_before))
    reviews_due_soon = cursor.fetchone()[0]

    # Fetch the statistics for the current review period
    review_period = get_current_review_period()
    if review_period:
        review_start, review_end = review_period
        try:
            # Convert the dates from DD/MM/YYYY to YYYY-MM-DD for SQL query
            review_start_formatted = datetime.strptime(review_start, '%d/%m/%Y').strftime('%Y-%m-%d')
            review_end_formatted = datetime.strptime(review_end, '%d/%m/%Y').strftime('%Y-%m-%d')
        except (TypeError, ValueError):
            review_start_formatted = None
            review_end_formatted = None
    else:
        review_start_formatted = None
        review_end_formatted = None
        review_start = None
        review_end = None

    # Query to get the number of items ordered during the current review period
    if review_start_formatted and review_end_formatted:
        cursor.execute('''
            SELECT COUNT(*) FROM orders
            WHERE date_ordered BETWEEN ? AND ?
        ''', (review_start_formatted, review_end_formatted))
        items_ordered_review_period = cursor.fetchone()[0] or 0

        # Query to get the number of reviewed items during the current review period
        cursor.execute('''
            SELECT COUNT(*) FROM orders
            WHERE review_date IS NOT NULL
            AND review_date BETWEEN ? AND ?
        ''', (review_start_formatted, review_end_formatted))
        items_reviewed_review_period = cursor.fetchone()[0] or 0
    else:
        items_ordered_review_period = 0
        items_reviewed_review_period = 0

    conn.close()

    # Calculate review percentage
    if items_ordered_review_period > 0:
        review_percentage = (items_reviewed_review_period / items_ordered_review_period) * 100
    else:
        review_percentage = 0

    # Apply the 80-item and 90% review requirement logic
    meets_requirements = items_ordered_review_period >= 80 and review_percentage >= 90

    # Determine the colour for review percentage (green if meets requirements, red if not)
    review_percentage_color = "green" if meets_requirements else "red"

    # Values passed in the address bar by the browser button, used to
    # pre-fill the 'Add a New Order' form
    prefill_url, _ = normalise_amazon_url(request.args.get('url', ''))
    prefill_price = parse_price(request.args.get('price', ''))
    prefill = {
        'url': prefill_url if request.args.get('url') else '',
        'description': ' '.join(request.args.get('description', '').split()),
        'price': f'{prefill_price:.2f}' if prefill_price else '',
        'image_url': thumbnail_url(request.args.get('image', '')) or '',
        # True if the details came from an old copy of the browser button
        'old_button': bool(request.args.get('url')) and request.args.get('v') != BOOKMARKLET_VERSION,
    }

    # Return the values to the template
    return render_template('index.html',
                           prefill=prefill,
                           reviews_overdue=reviews_overdue,
                           reviews_due_soon=reviews_due_soon,
                           due_soon_days=DUE_SOON_DAYS,
                           bookmarklet=build_bookmarklet(request.host_url),
                           total_orders=total_orders,
                           items_ordered_current_year=items_ordered_current_year,
                           total_value=total_value,
                           current_year_value=current_year_value,
                           items_sold=items_sold,
                           total_sales=f"{total_sales:.2f}",
                           net_sales=f"{net_sales:.2f}",
                           sales_current_year=f"{sales_current_year:.2f}",
                           review_start=review_start or "Not set",
                           review_end=review_end or "Not set",
                           items_ordered_review_period=items_ordered_review_period,
                           items_reviewed_review_period=items_reviewed_review_period,
                           review_percentage=f"{review_percentage:.2f}%",
                           review_percentage_color=review_percentage_color)


# Add a new order
@app.route('/add', methods=['POST'])
def add_order():
    url, _ = normalise_amazon_url(request.form['url'])
    description = request.form['description'].strip()
    date_ordered = request.form['date_ordered']
    image_url = thumbnail_url(request.form.get('image_url', ''))

    # The price is optional - Vine items often have no price on Amazon yet.
    # A blank price is stored as NULL and shows as 'N/A' until you add it.
    price_text = request.form.get('price', '').strip()
    try:
        price = float(price_text) if price_text else None
    except ValueError:
        return "Error: The price must be a number (or left blank).", 400

    # Review due date is REVIEW_DUE_DAYS after the order date
    try:
        review_due_date = (datetime.strptime(date_ordered, '%Y-%m-%d')
                           + timedelta(days=REVIEW_DUE_DAYS)).strftime('%Y-%m-%d')
    except ValueError:
        return "Error: A valid order date is required.", 400
    review_date = None  # Initially, no review has been completed

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (url, description, price, date_ordered, review_due_date, review_date, image_url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (url, description, price, date_ordered, review_due_date, review_date, image_url))
    conn.commit()
    conn.close()

    return redirect(url_for('index'))


# View all orders
@app.route('/orders')
def orders():
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get the filter type, default to 'awaiting' if no filter is provided
    filter_type = request.args.get('filter', 'awaiting')

    # Pagination (applies to ALL filters)
    try:
        page = int(request.args.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    if page < 1:
        page = 1
    per_page = 50

    # Server-side search (applies to ALL filters, so it works with pagination)
    q = request.args.get('q', '').strip()

    # Items become legally yours six months after the review date
    six_months_ago = (datetime.now() - timedelta(days=OWNERSHIP_DAYS)).strftime('%Y-%m-%d')

    search_sql = ''
    search_params = []
    if q:
        # Search across description, url and review text (review is helpful for reviewed items)
        search_sql = ' AND (description LIKE ? OR url LIKE ? OR review LIKE ?)'
        like = f'%{q}%'
        search_params = [like, like, like]

    def count_and_fetch(where_sql: str, params: list, order_by: str = 'date_ordered DESC'):
        nonlocal page

        # Total count for pagination controls
        cursor.execute(f'SELECT COUNT(*) FROM orders {where_sql}', params)
        row = cursor.fetchone()
        total = row[0] if row else 0

        # Clamp the requested page so a manual entry beyond the end
        # simply lands on the last page rather than showing nothing
        max_page = (total + per_page - 1) // per_page if total else 1
        if page > max_page:
            page = max_page
        offset = (page - 1) * per_page

        # Page of results
        cursor.execute(
            f'''
            SELECT id, description, price, date_ordered, review_date,
                   date_sold, sale_price, sale_platform, image_url
            FROM orders
            {where_sql}
            ORDER BY {order_by}
            LIMIT ? OFFSET ?
            ''',
            params + [per_page, offset]
        )
        return total, cursor.fetchall()

    if filter_type == 'reviewed':
        where_sql = 'WHERE review_date IS NOT NULL' + search_sql
        total_items, orders = count_and_fetch(where_sql, search_params)

    elif filter_type == 'awaiting':
        where_sql = 'WHERE review_date IS NULL' + search_sql
        total_items, orders = count_and_fetch(where_sql, search_params)

    elif filter_type == 'all':
        where_sql = 'WHERE 1=1' + search_sql
        total_items, orders = count_and_fetch(where_sql, search_params)

    elif filter_type == 'legally_mine':
        # Legally yours once the review is at least six months old
        where_sql = 'WHERE review_date <= ?' + search_sql
        total_items, orders = count_and_fetch(where_sql, [six_months_ago] + search_params)

    elif filter_type == 'overdue':
        # Not reviewed and past the due date - oldest (most overdue) first
        overdue_before, _ = review_due_cutoffs()
        where_sql = 'WHERE review_date IS NULL AND date_ordered < ?' + search_sql
        total_items, orders = count_and_fetch(where_sql, [overdue_before] + search_params,
                                              order_by='date_ordered ASC')

    elif filter_type == 'due_soon':
        # Not reviewed and due within the next few days - soonest first
        overdue_before, due_soon_before = review_due_cutoffs()
        where_sql = 'WHERE review_date IS NULL AND date_ordered >= ? AND date_ordered <= ?' + search_sql
        total_items, orders = count_and_fetch(where_sql, [overdue_before, due_soon_before] + search_params,
                                              order_by='date_ordered ASC')

    elif filter_type == 'sold':
        # Sold items, most recent sale first
        where_sql = 'WHERE date_sold IS NOT NULL' + search_sql
        total_items, orders = count_and_fetch(where_sql, search_params, order_by='date_sold DESC')

    else:
        where_sql = 'WHERE review_date IS NULL' + search_sql
        total_items, orders = count_and_fetch(where_sql, search_params)

    total_pages = (total_items + per_page - 1) // per_page if total_items else 1

    conn.close()

    # Format the dates to dd/mm/yyyy and prices to £x.xx before sending to the template
    formatted_orders = []
    for order in orders:
        if order['price'] is not None and order['price'] != '':
            formatted_price = f"£{float(order['price']):.2f}"  # Format the price to 2 decimal places with £ sign
        else:
            formatted_price = "N/A"  # Display N/A if price is missing or invalid

        if order['sale_price'] is not None:
            formatted_sale_price = f"£{float(order['sale_price']):.2f}"
        else:
            formatted_sale_price = "N/A"

        due_date, review_status = review_due_info(order['date_ordered'], order['review_date'])

        formatted_order = {
            'id': order['id'],
            'review_due': due_date.strftime('%d/%m/%Y') if due_date else None,
            'review_status': review_status,
            'description': order['description'],
            'price': formatted_price,
            'date_ordered': datetime.strptime(order['date_ordered'], '%Y-%m-%d').strftime('%d/%m/%Y'),
            'review_date': order['review_date'],  # Include review_date for highlighting
            'date_sold': datetime.strptime(order['date_sold'], '%Y-%m-%d').strftime('%d/%m/%Y') if order['date_sold'] else None,
            'sale_price': formatted_sale_price,
            'sale_platform': order['sale_platform'] or '—',
            'image_url': order['image_url'],
        }
        formatted_orders.append(formatted_order)

    # Pass total_items and six_months_ago to the template
    return render_template(
        'orders.html',
        orders=formatted_orders,
        total_items=total_items,
        six_months_ago=six_months_ago,
        filter=filter_type,
        page=page,
        total_pages=total_pages,
        q=q
    )


@app.route('/orders/<int:order_id>', methods=['GET'])
def view_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch the order
    order = cursor.execute('SELECT * FROM orders WHERE id = ?', (order_id,)).fetchone()

    # Guard against an invalid order ID (e.g. typed into the address bar)
    if order is None:
        conn.close()
        return render_template('not_found.html'), 404

    # Fetch the distinct platforms already used, to feed the autocomplete datalist
    cursor.execute('''
        SELECT DISTINCT sale_platform FROM orders
        WHERE sale_platform IS NOT NULL AND trim(sale_platform) != ''
        ORDER BY sale_platform COLLATE NOCASE
    ''')
    platforms = [row['sale_platform'] for row in cursor.fetchall()]
    conn.close()

    # Convert the review from markdown to HTML, if it exists
    review_html = markdown.markdown(order['review']) if order['review'] else "No review yet"

    # Check if the user is editing the review (using a query parameter)
    is_editing = request.args.get('edit') == '1'

    # Ownership: items become legally yours six months after the review date
    if order['review_date']:
        review_date_obj = datetime.strptime(order['review_date'], '%Y-%m-%d')
        ownership_date_obj = review_date_obj + timedelta(days=OWNERSHIP_DAYS)
        ownership_date = ownership_date_obj.strftime('%d/%m/%Y')
        is_owned = ownership_date_obj <= datetime.now()
    else:
        ownership_date = None
        is_owned = False

    date_ordered = datetime.strptime(order['date_ordered'], '%Y-%m-%d')

    # Logic for review due date
    review_due_status = None
    if order['review']:  # If there's a review, set due date to 'Complete'
        review_due_date = 'Complete'
        review_date = datetime.strptime(order['review_date'], '%Y-%m-%d').strftime('%d/%m/%Y') if order['review_date'] else 'Not reviewed yet'
    else:
        # The review is due REVIEW_DUE_DAYS after the order date
        due_date, review_status = review_due_info(order['date_ordered'], order['review_date'])
        review_due_date = due_date.strftime('%d/%m/%Y') if due_date else 'Unknown'
        days_left = (due_date - datetime.now().date()).days if due_date else None
        if review_status == 'overdue':
            review_due_date += f" (overdue by {-days_left} day{'s' if days_left != -1 else ''})"
        elif review_status == 'due_soon':
            review_due_date += " (due today)" if days_left == 0 else f" (in {days_left} day{'s' if days_left != 1 else ''})"
        review_due_status = review_status
        review_date = 'Not reviewed yet'

    return render_template('view_order.html',
                           order=order,
                           review_html=review_html,
                           is_editing=is_editing,
                           datetime=datetime,
                           is_owned=is_owned,
                           ownership_date=ownership_date,
                           review_due_date=review_due_date,
                           review_due_status=review_due_status,
                           review_date=review_date,
                           platforms=platforms,
                           picture_message=request.args.get('picture_message'),
                           picture_error=request.args.get('picture_error'),
                           today=datetime.now().strftime('%Y-%m-%d'))


# Edit an order's description, URL and order date
@app.route('/orders/<int:order_id>/update_details', methods=['POST'])
def update_details(order_id):
    description = ' '.join(request.form.get('description', '').split())
    url, _ = normalise_amazon_url(request.form.get('url', ''))
    date_ordered = request.form.get('date_ordered', '').strip()

    if not description:
        return "Error: The description can't be empty.", 400
    try:
        order_date = datetime.strptime(date_ordered, '%Y-%m-%d')
    except ValueError:
        return "Error: A valid order date is required.", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    order = cursor.execute('SELECT review_date FROM orders WHERE id = ?', (order_id,)).fetchone()
    if order is None:
        conn.close()
        return render_template('not_found.html'), 404

    # The order date can't be after the review was written
    if order['review_date'] and order_date > datetime.strptime(order['review_date'], '%Y-%m-%d'):
        conn.close()
        return "Error: The order date can't be later than the review date.", 400

    # Keep the stored due date in step with the order date (unless already reviewed)
    if order['review_date']:
        review_due_date = 'Complete'
    else:
        review_due_date = (order_date + timedelta(days=REVIEW_DUE_DAYS)).strftime('%Y-%m-%d')

    cursor.execute('''
        UPDATE orders
        SET description = ?, url = ?, date_ordered = ?, review_due_date = ?
        WHERE id = ?
    ''', (description, url, date_ordered, review_due_date, order_id))
    conn.commit()
    conn.close()

    return redirect(url_for('view_order', order_id=order_id))


def save_order_image(order_id, image_url):
    """Store a picture address against an order.  Returns False if there's no such order."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET image_url = ? WHERE id = ?', (image_url, order_id))
    conn.commit()
    updated = cursor.rowcount
    conn.close()
    return bool(updated)


# Add (or replace) an order's picture from a pasted picture link (order page)
@app.route('/orders/<int:order_id>/set_image', methods=['POST'])
def set_image(order_id):
    image_url = thumbnail_url(request.form.get('image_url', ''))
    if not image_url:
        return redirect(url_for('view_order', order_id=order_id, picture_error=(
            "That doesn't look like an Amazon product picture link. On the Amazon page, right-click "
            "the main product picture and choose 'Copy Image Link' (or 'Copy image address'), then paste it.")))
    if not save_order_image(order_id, image_url):
        return render_template('not_found.html'), 404
    return redirect(url_for('view_order', order_id=order_id, picture_message="Picture saved."))


# Try to get an order's picture from Amazon automatically (order page)
@app.route('/orders/<int:order_id>/fetch_image', methods=['POST'])
def fetch_image(order_id):
    conn = get_db_connection()
    order = conn.execute('SELECT url FROM orders WHERE id = ?', (order_id,)).fetchone()
    conn.close()
    if order is None:
        return render_template('not_found.html'), 404
    if not order['url']:
        return redirect(url_for('view_order', order_id=order_id,
                                picture_error="This order has no product URL to fetch the picture from."))

    result = scrape_price_and_description(order['url'])
    if result['image_url']:
        save_order_image(order_id, result['image_url'])
        return redirect(url_for('view_order', order_id=order_id, picture_message="Picture fetched from Amazon."))

    if result['description']:
        reason = "The Amazon page didn't have a picture the app could recognise."
    else:
        reason = "Amazon blocked the request (its 'are you a robot?' check), or the page couldn't be reached."
    return redirect(url_for('view_order', order_id=order_id, picture_error=(
        f"Couldn't get the picture automatically. {reason} Please paste the picture link instead.")))


# Remove the picture from an order (from the order page)
@app.route('/orders/<int:order_id>/remove_image', methods=['POST'])
def remove_image(order_id):
    conn = get_db_connection()
    conn.execute('UPDATE orders SET image_url = NULL WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('view_order', order_id=order_id))


# Mark an order as sold (or update the sale details)
@app.route('/orders/<int:order_id>/mark_sold', methods=['POST'])
def mark_sold(order_id):
    date_sold = request.form.get('date_sold', '').strip()
    sale_price = request.form.get('sale_price', '').strip()
    sale_platform = request.form.get('sale_platform', '').strip()
    sale_fees = request.form.get('sale_fees', '').strip()

    # Validate the date
    try:
        datetime.strptime(date_sold, '%Y-%m-%d')
    except (TypeError, ValueError):
        return "Error: A valid sale date is required.", 400

    # Validate the sale price
    try:
        sale_price = float(sale_price)
    except (TypeError, ValueError):
        return "Error: A valid sale price is required.", 400

    # Fees are optional
    if sale_fees:
        try:
            sale_fees = float(sale_fees)
        except (TypeError, ValueError):
            return "Error: Fees must be a number (or left blank).", 400
    else:
        sale_fees = None

    # Platform is optional free text; store NULL rather than an empty string
    sale_platform = sale_platform or None

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE orders
        SET date_sold = ?, sale_price = ?, sale_platform = ?, sale_fees = ?
        WHERE id = ?
    ''', (date_sold, sale_price, sale_platform, sale_fees, order_id))
    conn.commit()
    conn.close()

    return redirect(url_for('view_order', order_id=order_id))


# Remove the sale details from an order (undo 'Mark as Sold')
@app.route('/orders/<int:order_id>/clear_sale', methods=['POST'])
def clear_sale(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE orders
        SET date_sold = NULL, sale_price = NULL, sale_platform = NULL, sale_fees = NULL
        WHERE id = ?
    ''', (order_id,))
    conn.commit()
    conn.close()

    return redirect(url_for('view_order', order_id=order_id))


# Update the review date for an order
@app.route('/orders/<int:order_id>/review_date', methods=['POST'])
def update_review_date(order_id):
    review_date = request.form['review_date']

    conn = get_db_connection()
    cursor = conn.cursor()

    # Fetch the order's date_ordered to validate against it
    cursor.execute('SELECT date_ordered FROM orders WHERE id = ?', (order_id,))
    order = cursor.fetchone()

    if order is None:
        conn.close()
        return "Error: Order not found.", 404

    order_date = datetime.strptime(order['date_ordered'], '%Y-%m-%d')

    if review_date:
        review_date_obj = datetime.strptime(review_date, '%Y-%m-%d')

        # Check that the review date is not earlier than the order date
        if review_date_obj < order_date:
            conn.close()
            return "Error: Review date cannot be earlier than the order date.", 400

        # Update review date and set review_due_date to 'Complete'
        cursor.execute('''
            UPDATE orders
            SET review_date = ?, review_due_date = 'Complete'
            WHERE id = ?
        ''', (review_date, order_id))
        conn.commit()
    conn.close()

    return redirect(url_for('view_order', order_id=order_id))


# Update the review for an order (inline update)
@app.route('/orders/<int:order_id>/update_review', methods=['POST'])
def update_review(order_id):
    try:
        new_review = request.form['review']

        conn = get_db_connection()
        cursor = conn.cursor()

        # Fetch existing review_date
        cursor.execute('SELECT review_date FROM orders WHERE id = ?', (order_id,))
        row = cursor.fetchone()

        if row and row['review_date']:
            review_date = row['review_date']   # Preserve existing date
        else:
            review_date = datetime.now().strftime('%Y-%m-%d')  # First review

        cursor.execute(
            '''
            UPDATE orders
            SET review = ?, review_date = ?, review_due_date = "Complete"
            WHERE id = ?
            ''',
            (new_review, review_date, order_id)
        )

        conn.commit()
        conn.close()

        updated_review_html = markdown.markdown(new_review)

        return jsonify({
            "success": True,
            "updated_review_html": updated_review_html,
            "review_date": review_date,
            "review_due_date": "Complete"
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# Update the price for an order
@app.route('/update_price/<int:order_id>', methods=['POST'])
def update_price(order_id):
    try:
        # Get the price from the form
        new_price = request.form.get('price')
        if new_price is None or not new_price.strip():
            raise ValueError("Price cannot be empty.")

        # Convert to float
        price = float(new_price)

        # Update the price in the database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE orders SET price = ? WHERE id = ?', (price, order_id))
        conn.commit()
        conn.close()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# Delete an order
@app.route('/orders/<int:order_id>/delete', methods=['POST'])
def delete_order(order_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM orders WHERE id = ?', (order_id,))
    conn.commit()
    conn.close()

    return redirect(url_for('orders'))


# Route to import data from CSV (POST because it modifies the database)
@app.route('/import_csv', methods=['POST'])
def import_csv():
    try:
        with open('VineOrders.csv', newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            conn = get_db_connection()
            cursor = conn.cursor()

            new_items_count = 0  # Counter for new items added

            for row in reader:
                url = row['url']
                date_ordered = row['date_ordered']
                description = row['description']
                cursor.execute('SELECT COUNT(1) FROM orders WHERE url = ?', (url,))
                exists = cursor.fetchone()[0]

                if not exists:
                    cursor.execute('INSERT INTO orders (url, description, date_ordered) VALUES (?, ?, ?)',
                                   (url, description, date_ordered))
                    new_items_count += 1

            conn.commit()
            conn.close()

        return jsonify({"message": f"CSV data imported successfully! {new_items_count} new items added."}), 200

    except FileNotFoundError:
        return jsonify({"message": "Error: VineOrders.csv file not found."}), 404

    except Exception as e:
        return jsonify({"message": f"An error occurred: {str(e)}"}), 500


# Route to handle saving the review period
@app.route('/save_review_period', methods=['POST'])
def save_review_period():
    start_date = request.form['start_date']
    end_date = request.form['end_date']

    # Save to the database
    conn = get_db_connection()
    cursor = conn.cursor()

    # Insert the new review period into the table
    cursor.execute('''
        INSERT INTO review_periods (start_date, end_date)
        VALUES (?, ?)
    ''', (start_date, end_date))

    conn.commit()
    conn.close()

    return '', 204  # Return success without reloading the page


# Statistics page with charts and export links
@app.route('/stats')
def stats():
    conn = get_db_connection()
    cursor = conn.cursor()

    def month_series(rows, months=24):
        """Fill in any missing months with zero so the charts have no gaps."""
        data = {row[0]: row[1] for row in rows}
        labels = []
        values = []
        # Walk backwards from the current month
        current = datetime.now().replace(day=1)
        month_list = []
        for _ in range(months):
            month_list.append(current.strftime('%Y-%m'))
            # Step back one month
            current = (current - timedelta(days=1)).replace(day=1)
        for month in reversed(month_list):
            labels.append(datetime.strptime(month, '%Y-%m').strftime('%b %y'))
            values.append(data.get(month, 0))
        return labels, values

    # Orders per month (last 24 months)
    cursor.execute('''
        SELECT strftime('%Y-%m', date_ordered) AS month, COUNT(*)
        FROM orders
        WHERE date_ordered >= date('now', 'start of month', '-23 months')
        GROUP BY month
    ''')
    orders_month_labels, orders_month_values = month_series(cursor.fetchall())

    # Order value per year
    cursor.execute('''
        SELECT strftime('%Y', date_ordered) AS year,
               COUNT(*), ROUND(SUM(COALESCE(price, 0)), 2)
        FROM orders
        GROUP BY year
        ORDER BY year
    ''')
    year_rows = cursor.fetchall()
    year_labels = [row[0] for row in year_rows]
    year_counts = [row[1] for row in year_rows]
    year_values = [row[2] for row in year_rows]

    # Sales per month (last 24 months, gross)
    cursor.execute('''
        SELECT strftime('%Y-%m', date_sold) AS month,
               ROUND(SUM(sale_price), 2)
        FROM orders
        WHERE date_sold IS NOT NULL
        AND date_sold >= date('now', 'start of month', '-23 months')
        GROUP BY month
    ''')
    sales_month_labels, sales_month_values = month_series(cursor.fetchall())

    # Sales by platform (for the doughnut chart and the breakdown table)
    cursor.execute('''
        SELECT COALESCE(NULLIF(trim(sale_platform), ''), 'Not specified') AS platform,
               COUNT(*),
               ROUND(SUM(sale_price), 2),
               ROUND(SUM(COALESCE(sale_fees, 0)), 2)
        FROM orders
        WHERE date_sold IS NOT NULL
        GROUP BY platform
        ORDER BY SUM(sale_price) DESC
    ''')
    platform_rows = [
        {
            'platform': row[0],
            'items': row[1],
            'gross': row[2],
            'fees': row[3],
            'net': round(row[2] - row[3], 2),
        }
        for row in cursor.fetchall()
    ]

    # Headline totals for the top of the page
    cursor.execute('SELECT COUNT(*), ROUND(SUM(COALESCE(price, 0)), 2) FROM orders')
    total_orders, total_value = cursor.fetchone()
    cursor.execute('''
        SELECT COUNT(*), ROUND(COALESCE(SUM(sale_price), 0), 2),
               ROUND(COALESCE(SUM(COALESCE(sale_fees, 0)), 0), 2)
        FROM orders WHERE date_sold IS NOT NULL
    ''')
    items_sold, sales_gross, sales_fees = cursor.fetchone()

    conn.close()

    return render_template('stats.html',
                           total_orders=total_orders,
                           total_value=f"{(total_value or 0):.2f}",
                           items_sold=items_sold,
                           sales_gross=f"{sales_gross:.2f}",
                           sales_net=f"{(sales_gross - sales_fees):.2f}",
                           orders_month_labels=orders_month_labels,
                           orders_month_values=orders_month_values,
                           year_labels=year_labels,
                           year_counts=year_counts,
                           year_values=year_values,
                           sales_month_labels=sales_month_labels,
                           sales_month_values=sales_month_values,
                           platform_rows=platform_rows)


def csv_response(header, rows, filename):
    """Build a CSV download response from a header row and data rows."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# Export every order as CSV
@app.route('/export/orders.csv')
def export_orders_csv():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT description, url, price, date_ordered, review_date,
               date_sold, sale_platform, sale_price, sale_fees, image_url
        FROM orders
        ORDER BY date_ordered
    ''')
    rows = cursor.fetchall()
    conn.close()

    return csv_response(
        ['description', 'url', 'price', 'date_ordered', 'review_date',
         'date_sold', 'sale_platform', 'sale_price', 'sale_fees', 'image_url'],
        rows,
        'vine_orders.csv'
    )


# Export sold items as CSV (with net amounts, handy for tax records)
@app.route('/export/sales.csv')
def export_sales_csv():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT description, date_ordered, price, date_sold,
               COALESCE(sale_platform, '') AS platform, sale_price,
               COALESCE(sale_fees, 0) AS fees,
               ROUND(sale_price - COALESCE(sale_fees, 0), 2) AS net
        FROM orders
        WHERE date_sold IS NOT NULL
        ORDER BY date_sold
    ''')
    rows = cursor.fetchall()
    conn.close()

    return csv_response(
        ['description', 'date_ordered', 'rrp', 'date_sold',
         'platform', 'sale_price', 'fees', 'net'],
        rows,
        'vine_sales.csv'
    )


def folder_storage_warning(folder):
    """
    When running in Docker, check whether a folder is inside a folder mapped
    from the server (a Docker 'Path').  If it isn't, anything saved there only
    exists inside the container and is lost when the container is rebuilt.
    Returns a warning message, or None if all is well (or not in Docker).
    """
    if not os.path.exists('/.dockerenv'):
        return None
    try:
        with open('/proc/self/mountinfo') as f:
            # The 5th item on each line is where something is mounted.  Spaces
            # in folder names are written as \040, so turn them back.
            mount_points = [line.split()[4].replace('\\040', ' ') for line in f if line.strip()]
    except OSError:
        return None

    folder = os.path.abspath(folder)
    # Find the most specific mounted folder that contains this folder
    best = '/'
    for mount_point in mount_points:
        if (folder == mount_point or folder.startswith(mount_point.rstrip('/') + '/')) \
                and len(mount_point) > len(best):
            best = mount_point
    if best == '/':
        return (f"Warning: {folder} isn't in a folder mapped from your server, so backups saved there "
                f"would be lost when the container is rebuilt. Check the Docker Path's container path "
                f"matches the start of the backup folder.")
    return None


# Settings page (currently the backup options)
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    message = None
    error = None

    if request.method == 'POST':
        # Both 'Save Settings' and 'Back Up Now' save what's in the form first,
        # so a backup always goes to the folder you can see on the page
        backup_now = request.form.get('action') == 'backup_now'
        enabled = request.form.get('backup_enabled') == 'on'
        folder = request.form.get('backup_dir', '').strip() or DEFAULT_BACKUP_DIR
        keep_text = request.form.get('backup_keep', '').strip()

        try:
            keep = int(keep_text)
            if keep < 1:
                raise ValueError
        except ValueError:
            error = "The number of backups to keep must be a whole number of at least 1."

        # Check the folder can be used if a backup is going to be made
        if not error and (enabled or backup_now):
            error = check_backup_folder(os.path.abspath(folder))

        if not error:
            set_setting('backup_enabled', '1' if enabled else '0')
            set_setting('backup_dir', folder)
            set_setting('backup_keep', keep)
            message = "Settings saved."
            if enabled or backup_now:
                try:
                    name = backup_database(force=backup_now)
                    full_path = os.path.join(os.path.abspath(folder), name)
                    if backup_now:
                        message = f"Settings saved, and a backup was made: {full_path}"
                    else:
                        message = f"Settings saved, and today's backup is in place: {full_path}"
                except Exception as e:
                    error = f"Settings saved, but the backup failed: {e}"

    enabled, folder, keep = backup_settings()
    backups = []
    for name in list_backups(folder):
        path = os.path.join(folder, name)
        backups.append({
            'name': name,
            'date': datetime.strptime(name[len('vine_orders-'):-len('.db')], '%Y-%m-%d').strftime('%d/%m/%Y'),
            'size': f"{os.path.getsize(path) / 1024:.0f} KB",
        })

    return render_template('settings.html',
                           message=message,
                           error=error,
                           warning=folder_storage_warning(folder),
                           backup_enabled=enabled,
                           backup_dir=get_setting('backup_dir', DEFAULT_BACKUP_DIR),
                           backup_dir_full=folder,
                           backup_keep=keep,
                           backups=backups)


# Download a copy of the whole database (a manual backup)
@app.route('/export/database')
def export_database():
    # Make a consistent copy first, rather than sending the live file
    temp_path = os.path.abspath('vine_orders-download.db')
    copy_database(temp_path)
    with open(temp_path, 'rb') as f:
        data = f.read()
    os.remove(temp_path)

    filename = f"vine_orders-{datetime.now().strftime('%Y-%m-%d')}.db"
    return Response(data, mimetype='application/octet-stream',
                    headers={'Content-Disposition': f'attachment; filename={filename}'})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
