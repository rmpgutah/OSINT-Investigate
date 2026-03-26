# OSINT Investigation Suite

A professional OSINT (Open-Source Intelligence) investigation platform with both CLI and web interfaces, backed by PostgreSQL.

Built for private investigators, skip-tracers, and OSINT analysts to manage cases, gather intelligence from public sources, and generate investigation reports.

## Features

- **Case Management** — Create, track, and close investigations with unique case numbers
- **Multi-type Targets** — Track persons, domains, emails, phones, usernames, IPs, organizations
- **6 OSINT Modules** — Person search, web scraping, email intel, phone lookup, domain recon, social media enumeration
- **Cross-referencing** — Automatic correlation of findings across targets using exact and fuzzy matching
- **Report Generation** — Export to CSV, HTML, JSON, or PDF
- **Dual Interface** — Full CLI (Typer) and web dashboard (FastAPI)
- **PostgreSQL** — JSONB storage for flexible finding data, full-text search

## Quick Start

### 1. Start PostgreSQL

```bash
docker compose up -d
```

### 2. Install

```bash
pip install -e .
```

### 3. Initialize Database

```bash
# Copy env file
cp .env.example .env

# Create initial migration
osint db migrate "initial schema"

# Run migrations
osint db init
```

### 4. Use the CLI

```bash
# Create a case
osint case new "Missing Person - Jane Doe"

# Add a target
osint target add CASE-0001 person \
    --name "Jane Doe" \
    --email "jane@example.com" \
    --phone "555-555-0100" \
    --city "Portland" \
    --state "OR"

# Run all OSINT modules against the target
osint run CASE-0001 --target <TARGET_UUID>

# Generate an HTML report
osint report generate CASE-0001 --format html

# List available modules
osint modules
```

### 5. Use the Web Dashboard

```bash
osint-web
# Opens at http://127.0.0.1:8000
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `osint case new <title>` | Create new investigation |
| `osint case list` | List all cases |
| `osint case show <case#>` | Show case details |
| `osint case close <case#>` | Close a case |
| `osint target add <case#> <type>` | Add target with PII fields |
| `osint target list <case#>` | List targets in a case |
| `osint target search <query>` | Search across all targets |
| `osint target findings <id>` | Show findings for a target |
| `osint run <case#> -t <id>` | Run modules on a target |
| `osint run <case#> -t <id> -m <module>` | Run specific module |
| `osint modules` | List available modules |
| `osint report generate <case#> -f <fmt>` | Generate report (csv/html/json/pdf) |
| `osint db init` | Initialize database |
| `osint db export <case#>` | Export case as JSON |

## OSINT Modules

| Module | Target Types | Description |
|--------|-------------|-------------|
| `person_search` | person | Google, Whitepages, FamilySearch lookup |
| `web_scraper` | domain, person, org | Page content, links, and email extraction |
| `email_intel` | email, person | Format validation, MX records, HIBP breaches |
| `phone_lookup` | phone, person | Validation, carrier, geolocation via phonenumbers |
| `domain_recon` | domain | WHOIS, DNS records (A/AAAA/MX/TXT/NS/SOA) |
| `social_media` | username, person | Profile detection across 22+ platforms |

## Web API

The web interface exposes a REST API at `/api/`:

- `POST /api/investigations/` — Create investigation
- `GET /api/investigations/` — List investigations
- `POST /api/targets/` — Add target
- `POST /api/targets/{id}/run` — Run modules
- `GET /api/findings/?target_id=X` — List findings
- `POST /api/reports/` — Generate report

## Configuration

All settings via environment variables (prefix `OSINT_`):

```bash
OSINT_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/osint_db
OSINT_HIBP_API_KEY=your-api-key    # Optional: Have I Been Pwned
OSINT_SHODAN_API_KEY=your-key      # Optional: Shodan
OSINT_HTTP_RATE_LIMIT_PER_SECOND=2 # Rate limiting for requests
```

## Project Structure

```
src/osintsuite/
    config.py          # Settings (pydantic-settings)
    db/                # SQLAlchemy models, session, repository
    modules/           # OSINT modules (person, email, phone, domain, social, web)
    engine/            # Investigation orchestrator + correlator
    cli/               # Typer CLI application
    web/               # FastAPI web app + templates
    reporting/         # Report generation (CSV, HTML, JSON, PDF)
```

## Security

This tool was rebuilt from scratch to fix security vulnerabilities in the original scripts:
- All database queries use parameterized bindings (no SQL injection)
- No hardcoded credentials (environment variables via pydantic-settings)
- CSV export sanitizes against formula injection
- HTTP requests are rate-limited with proper User-Agent headers
- All user input is validated through Pydantic models

## License

MIT
