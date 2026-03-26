# Cloud PostgreSQL Setup Guide

All platforms (Mac, Windows, Android) connect to the same cloud database for seamless data sync.

## Option 1: Neon (Free Tier)

1. Sign up at https://neon.tech
2. Create a new project
3. Copy your connection string
4. Set in your `.env`:

```bash
OSINT_DATABASE_URL=postgresql+asyncpg://user:password@ep-xxx.us-east-2.aws.neon.tech/osint_db?sslmode=require
OSINT_DATABASE_URL_SYNC=postgresql+psycopg://user:password@ep-xxx.us-east-2.aws.neon.tech/osint_db?sslmode=require
```

## Option 2: Supabase (Free Tier)

1. Sign up at https://supabase.com
2. Create a new project
3. Go to Settings > Database > Connection string
4. Set in your `.env`:

```bash
OSINT_DATABASE_URL=postgresql+asyncpg://postgres:password@db.xxxxx.supabase.co:5432/postgres
OSINT_DATABASE_URL_SYNC=postgresql+psycopg://postgres:password@db.xxxxx.supabase.co:5432/postgres
```

## Option 3: Railway

1. Sign up at https://railway.app
2. Create a PostgreSQL service
3. Copy the connection URL from Variables tab
4. Set in your `.env`

## Option 4: Self-Hosted VPS

```bash
# On your VPS (Ubuntu/Debian)
sudo apt install postgresql
sudo -u postgres createuser osint --pwprompt
sudo -u postgres createdb osint_db --owner=osint

# Edit pg_hba.conf to allow remote connections
# Edit postgresql.conf: listen_addresses = '*'
# Restart: sudo systemctl restart postgresql
```

## After Setup

1. Update `.env` on every platform with the cloud database URL
2. Run migrations once from any machine:
   ```bash
   osint db init
   ```
3. All platforms now share the same data

## Android Configuration

The Android app connects to your web server (not the database directly).
Deploy the web dashboard to a cloud server and point the Android app to that URL.

Example deployment:
```bash
# On your cloud server
pip install osintsuite
osint db init
OSINT_WEB_HOST=0.0.0.0 osint-web
```

Then enter `https://your-server.example.com` in the Android app's setup screen.
