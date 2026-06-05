# Property Hunter

Pi-local Domain.com.au structured-data fetcher and API.

## CLI

```bash
source venv/bin/activate

# Fetch HTML through Playwright, preserving browser cookies/profile locally.
python domain_cli.py fetch --url "https://www.domain.com.au/sale/zetland-nsw-2017/" --out page.html

# Extract IDs only.
python domain_cli.py ids --html page.html --json

# Extract normalized listing cards from search-page embedded JSON.
python domain_cli.py search --html page.html --limit 10

# Fetch + parse in one command.
python domain_cli.py search --url "https://www.domain.com.au/sale/zetland-nsw-2017/" --limit 10
```

## API

```bash
source venv/bin/activate
uvicorn domain_api:app --host 127.0.0.1 --port 8787
```

Endpoints:

- `GET /health`
- `POST /domain/search`
- `POST /domain/listing`
- `POST /reports/daily`

Example:

```bash
curl -s http://127.0.0.1:8787/domain/search \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.domain.com.au/sale/zetland-nsw-2017/","limit":10}'
```

## Reality Check

The parser works against Domain pages that contain `digitalData` or
`__NEXT_DATA__`, including the saved blocked capture in `tmp/domain-trace`.
If Domain returns a hard Akamai access-denied page with no embedded JSON, the API
returns a blocked error instead of pretending it found data.
