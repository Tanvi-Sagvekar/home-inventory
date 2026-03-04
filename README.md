# Home Inventory Expiry & Refill Reminder

Smart home inventory assistant built with Flask and MySQL.

## Features

- Track household items (groceries, medicines, cosmetics, etc.)
- Dashboard with total items, expiring soon, and low stock counts
- Scan barcode with camera (html5-qrcode in browser)
- Auto-fill product name using OpenFoodFacts API
- Expiry alerts page (items expiring within 3 days)
- Refill reminder page (items with low quantity)
- Simple email notifications via daily background scheduler (APScheduler)

## Setup

1. **Create and activate a virtual environment (optional but recommended)**

```bash
cd "d:\HomeStock AI"
python -m venv venv
venv\Scripts\activate
```

2. **Install dependencies**

```bash
pip install -r requirements.txt
```

3. **Create MySQL database and tables**

Log into MySQL and run:

```sql
SOURCE d:/HomeStock AI/database.sql;
```

4. **Set environment variables (PowerShell example)**

```powershell
$env:DB_HOST = "localhost"
$env:DB_USER = "root"
$env:DB_PASSWORD = "your_mysql_password"
$env:DB_NAME = "home_inventory"

# For email alerts (optional)
$env:SMTP_HOST = "smtp.example.com"
$env:SMTP_PORT = "587"
$env:SMTP_USER = "you@example.com"
$env:SMTP_PASSWORD = "smtp_password"
$env:FROM_EMAIL = "you@example.com"
$env:ALERT_EMAIL = "family@example.com"
```

If you skip the SMTP variables, the app will still run; email sending will simply be skipped.

5. **Run the app**

```bash
python app.py
```

Open `http://127.0.0.1:5000` in your browser.

## Usage

- Use **Scan Product** to open the camera, scan a barcode, and auto-fill product name if available.
- Use **Add Manually** for items without a barcode.
- Set **minimum quantity** for each item; if quantity falls below this, it appears in the **Refill** list.
- Items expiring within 3 days show on the **Expiry Alerts** page.

