from flask import Flask, render_template, request, redirect, url_for, jsonify
import random
import re
import logging
import requests
import sqlite3
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import markdown
import csv

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)

DATABASE = 'vine_orders.db'

# Vine items become legally yours six months after the review date
OWNERSHIP_DAYS = 180

# A useful page for the current user agents is https://www.whatismybrowser.com/guides/the-latest-user-agent/
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0.6723.78 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPad; CPU OS 17_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0.6723.78 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPod; CPU iPhone OS 17_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0.6723.78 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.73 Mobile Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14.7; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (X11; Linux i686; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (X11; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (X11; Ubuntu; Linux i686; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (X11; Fedora; Linux x86_64; rv:131.0) Gecko/20100101 Firefox/131.0',
    'Mozilla/5.0 (Android 15; Mobile; rv:131.0) Gecko/131.0 Firefox/131.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
]


def scrape_price_and_description(url):
    headers = {'User-Agent': random.choice(USER_AGENTS)}
    logging.info(f"Using User-Agent: {headers['User-Agent']} for URL: {url}")

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        logging.info("Request successful, parsing HTML...")

        soup = BeautifulSoup(response.text, 'html.parser')

        # Scrape price
        price_tag = soup.find('span', {'class': 'a-offscreen'})
        price_cleaned = 0.0
        if price_tag:
            price_text = price_tag.get_text().strip()
            logging.info(f"Price text found: {price_text}")

            price_cleaned = re.sub(r'[^\d.]', '', price_text)
            price_cleaned = float(price_cleaned) if price_cleaned else 0.0
            logging.info(f"Cleaned price: {price_cleaned}")

        # Scrape product description
        title_tag = soup.find('span', {'id': 'productTitle'})
        description = title_tag.get_text().strip() if title_tag else ""
        logging.info(f"Product description found: {description}")

        return {"price": price_cleaned, "description": description}
    except Exception as e:
        logging.error(f"Error scraping URL {url}: {e}")
        return {"price": 0.0, "description": ""}


@app.route('/fetch-product-info', methods=['POST'])
def fetch_product_info():
    data = request.get_json()
    url = data.get('url', '')

    if not url:
        logging.warning("No URL provided in request.")
        return jsonify({"error": "Invalid URL"}), 400

    logging.info(f"Fetching product info for URL: {url}")
    product_info = scrape_price_and_description(url)

    logging.info(f"Scraped product info: {product_info}")
    return jsonify(product_info)


# Function to get a database connection
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # Ensure rows behave like dictionaries
    return conn


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

    # Create the review_periods table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS review_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL
        )
    ''')

    # --- Migrations for existing databases ---
    # Add the sales tracking columns if they are not already present
    cursor.execute('PRAGMA table_info(orders)')
    existing_columns = {row['name'] for row in cursor.fetchall()}

    sales_columns = {
        'date_sold': 'TEXT',
        'sale_price': 'REAL',
        'sale_platform': 'TEXT',
        'sale_fees': 'REAL',
    }
    for column_name, column_type in sales_columns.items():
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


# Initialise the database
init_db()


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
    review_percentage_color = "green" if review_percentage >= 90 else "red"

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

    # Return the values to the template
    return render_template('index.html',
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
    url = request.form['url']
    description = request.form['description']
    price = float(request.form['price'])
    date_ordered = request.form['date_ordered']

    # Review due date is 30 days after the order date
    review_due_date = (datetime.strptime(date_ordered, '%Y-%m-%d') + timedelta(days=30)).strftime('%Y-%m-%d')
    review_date = None  # Initially, no review has been completed

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO orders (url, description, price, date_ordered, review_due_date, review_date)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (url, description, price, date_ordered, review_due_date, review_date))
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
                   date_sold, sale_price, sale_platform
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

        formatted_order = {
            'id': order['id'],
            'description': order['description'],
            'price': formatted_price,
            'date_ordered': datetime.strptime(order['date_ordered'], '%Y-%m-%d').strftime('%d/%m/%Y'),
            'review_date': order['review_date'],  # Include review_date for highlighting
            'date_sold': datetime.strptime(order['date_sold'], '%Y-%m-%d').strftime('%d/%m/%Y') if order['date_sold'] else None,
            'sale_price': formatted_sale_price,
            'sale_platform': order['sale_platform'] or '—',
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
    if order['review']:  # If there's a review, set due date to 'Complete'
        review_due_date = 'Complete'
        review_date = datetime.strptime(order['review_date'], '%Y-%m-%d').strftime('%d/%m/%Y') if order['review_date'] else 'Not reviewed yet'
    else:
        # Calculate 30 days from the order date for review due date
        review_due_date = (date_ordered + timedelta(days=30)).strftime('%d/%m/%Y')
        review_date = 'Not reviewed yet'

    return render_template('view_order.html',
                           order=order,
                           review_html=review_html,
                           is_editing=is_editing,
                           datetime=datetime,
                           is_owned=is_owned,
                           ownership_date=ownership_date,
                           review_due_date=review_due_date,
                           review_date=review_date,
                           platforms=platforms,
                           today=datetime.now().strftime('%Y-%m-%d'))


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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
