import csv
import sqlite3

DATABASE = 'vine_orders.db'  # Your database name

def update_order_price_from_csv(csv_filename):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    with open(csv_filename, mode='r') as file:
        csv_reader = csv.DictReader(file)
        for row in csv_reader:
            url = row['url']
            price = row['price'].strip()

            # Store the price as a real number (or NULL if the field is blank)
            # so the database keeps a consistent type for the price column
            price = float(price) if price else None

            # Update the price for the order matching the URL
            cursor.execute("""
                UPDATE orders
                SET price = ?
                WHERE url = ?
            """, (price, url))

    conn.commit()
    conn.close()
    print(f"Prices from {csv_filename} have been imported successfully.")

# Example usage:
csv_filename = 'prices.csv'  # Replace with the path to your CSV file
update_order_price_from_csv(csv_filename)
