# NHS Board Paper Scraper

A lightweight scraper for finding the latest NHS Trust and ICB board papers and downloading them into a structured folder system. The Flask dashboard and Gemini analysis tools are still available, but the default workflow is now the simple no-AI downloader.

The app has two main jobs:

- Crawl NHS organisation websites and find PDF board papers.
- Download the most recent board paper found for each trust into its own folder.

AI analysis can be expanded later on top of those downloaded files.

## Quick Start: Download Latest Board Papers

This is the recommended default workflow. It does not need Gemini, Flask, Selenium, Chrome, or Crawl4AI.

On Windows PowerShell:

```powershell
.\setup_basic.ps1
.\run_latest_board_papers.ps1
```

On Windows Command Prompt:

```bat
run_latest_board_papers.bat
```

On macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-basic.txt
python scrape_latest_board_papers.py
```

By default, the scraper uses [config/mental_health_trusts.json](config/mental_health_trusts.json), which contains the mental health trusts listed for this project.

Downloaded files are written to:

```text
board_papers/<trust-name>/<report-type>/<year>/
```

Each downloaded file gets a sidecar metadata file. Each report-type folder also gets a `latest.json` pointer.

Files are renamed in this format:

```text
YYYY-MM-DD - Trust Name - report_type - Document Title.pdf
```

Example:

```text
board_papers/
  berkshire-healthcare-nhs-foundation-trust/
    board-papers/
      2026/
        2026-05-12 - Berkshire Healthcare NHS Foundation Trust - board - Download Trust Board Public Meeting Papers 12 May 2026.pdf
        2026-05-12 - Berkshire Healthcare NHS Foundation Trust - board - Download Trust Board Public Meeting Papers 12 May 2026.pdf.metadata.json
      latest.json
```

### Useful Scraper Options

```bash
python scrape_latest_board_papers.py --help
```

Common options:

```bash
python scrape_latest_board_papers.py --dry-run
python scrape_latest_board_papers.py --only "Cambridgeshire"
python scrape_latest_board_papers.py --include-supplementary
python scrape_latest_board_papers.py --include-strategy
python scrape_latest_board_papers.py --types all
python scrape_latest_board_papers.py --all-matches
python scrape_latest_board_papers.py --output downloaded_board_papers
python scrape_latest_board_papers.py --trusts config/mental_health_trusts.json
python scrape_latest_board_papers.py --max-pages 50
```

What the options do:

- `--dry-run`: find the latest candidate for each trust without downloading.
- `--only`: run only trusts whose name or URL contains the given text.
- `--types`: choose report types. Supported values are `board`, `supplementary`, `strategy`, `digital_strategy`, or `all`.
- `--include-supplementary`: add supplementary board material to the default board-paper run.
- `--include-strategy`: add strategic reporting and digital strategy material to the default board-paper run.
- `--all-matches`: download every matching file instead of only the latest per report type.
- `--limit-per-type`: when not using `--all-matches`, choose how many latest files to download per report type.
- `--output`: choose a different output folder.
- `--trusts`: use a different trust list JSON file.
- `--max-pages`: scan more likely board-paper pages per trust.
- `--timeout`: change the HTTP timeout in seconds.
- `--verify-ssl`: verify SSL certificates. By default this is off because some NHS sites have incomplete certificate chains.

### Trust List

The default trust list is [config/mental_health_trusts.json](config/mental_health_trusts.json).

Add or edit entries in this format:

```json
[
  {
    "name": "Example NHS Trust",
    "url": "https://www.example.nhs.uk/",
    "start_urls": [
      "https://www.example.nhs.uk/about-us/board-papers/"
    ],
    "allowed_domains": [
      "example.nhs.uk"
    ]
  }
]
```

The scraper creates one folder per `name`.

`start_urls` is optional. Use it when you know the exact board papers, trust board, meetings, or publications page for a trust. This makes the scraper faster and more reliable than starting from the homepage.

`allowed_domains` is optional. Use it when a trust redirects to a new domain or stores papers on another official NHS domain.

### How The No-AI Scraper Chooses The Latest Paper

The simple scraper:

1. Starts at each trust website URL.
2. Adds known board/governance/publication path guesses.
3. Reads sitemap URLs when available and prioritises likely report pages.
4. Follows links that look like board, governance, publication, strategy, or meeting pages.
5. Classifies document links into report types.
6. Extracts dates from link text and file URLs where possible.
7. Falls back to HTTP `Last-Modified` when no date is visible.
8. Picks the newest dated candidate per selected report type, unless `--all-matches` is used.

This is deliberately simple and easy to run. Some trusts have unusual website structures, so check `metadata.json` when a result looks unexpected.

### Report Types

The scraper can classify and download:

- `board`: board papers, board packs, public board agendas, trust board meeting papers.
- `supplementary`: supplementary packs, appendices, supporting papers, additional papers.
- `strategy`: strategies, annual plans, operational plans, corporate plans, annual reports, quality accounts.
- `digital_strategy`: digital strategy, data strategy, technology strategy, digital plans, digital roadmaps.

Default:

```bash
python scrape_latest_board_papers.py
```

Board papers plus supplementary material:

```bash
python scrape_latest_board_papers.py --include-supplementary
```

Board papers plus strategic and digital strategy material:

```bash
python scrape_latest_board_papers.py --include-strategy
```

Everything:

```bash
python scrape_latest_board_papers.py --types all
```

## Features

- Simple no-AI downloader for the latest board paper per trust.
- Creates a clean folder structure automatically.
- Uses editable JSON config for trust names and website URLs.
- Browser-based dashboard for running crawls and viewing results.
- Crawls a built-in list of NHS websites from `app.py`.
- Tests one or more specific websites from the UI.
- Supports a `scrape_only` mode to find papers without running full Gemini analysis.
- Filters out papers dated before 2024.
- Stores results in a local JSON file.
- Marks newly discovered papers.
- Shows relevant terms, summaries, quotes, and organisation metadata when analysis is available.
- Exposes JSON API endpoints for automation or export.

## Requirements

For the default no-AI downloader:

- Python 3.11 or newer.
- Internet access for crawling websites and downloading PDFs.
- Dependencies from `requirements-basic.txt`.

For the optional Flask/Gemini dashboard:

- Dependencies from `requirements.txt`.
- Chrome or Chromium available to Selenium.
- A Google Gemini API key for PDF date extraction and analysis.

On Linux/Docker, the code expects:

- Chromium at `/usr/bin/chromium`
- Chromedriver at `/usr/bin/chromedriver`

On Windows/macOS, Selenium Manager is used when no `CHROMEDRIVER_PATH` is configured.

## Optional Setup: Flask Dashboard And AI Analysis

Use this setup only when you want the dashboard and Gemini analysis features.

1. Create and activate a virtual environment, if you have not already.

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   On macOS/Linux:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```

2. Install dependencies.

   ```bash
   pip install -r requirements.txt
   ```

3. Create your environment file.

   ```bash
   cp .env.example .env
   ```

   On Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Edit `.env` and set at least:

   ```env
   GEMINI_API_KEY=your-gemini-api-key-here
   SECRET_KEY=replace-this-with-a-random-secret
   ```

5. Run the app.

   ```bash
   python app.py
   ```

6. Open the dashboard.

   ```text
   http://localhost:5002
   ```

## Configuration

Configuration is loaded from environment variables in [utils/config.py](utils/config.py).

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | Required for analysis | None | Google Gemini key used for PDF date extraction, metadata extraction, and term analysis. |
| `SECRET_KEY` | Recommended | `default-secret-key` | Flask secret key. Set this in real deployments. |
| `FLASK_ENV` | No | `development` | Flask environment label. |
| `DEBUG` | No | `True` | App debug flag in config. The direct `python app.py` runner currently starts Flask with debug enabled. |
| `DATA_DIR` | No | `data` | Directory where `board_papers.json` is stored. |
| `CHROMEDRIVER_PATH` | No | Auto-detected | Optional explicit path to chromedriver. |
| `OPENAI_API_KEY` | No | None | Present for Crawl4AI-related settings, but not used directly by the current app code. |
| `SIMULATE_CRAWLER` | No | `False` | Present in config but not currently used by the crawler. |
| `MAX_PAGES_PER_SITE` | No | `30` | Present in config but not currently enforced by the crawler. |
| `CRAWL_DELAY` | No | `1.0` | Present in config but not currently enforced by the crawler. |

If `GEMINI_API_KEY` is missing, the dashboard can still load, but analysis and crawl routes that need PDF date extraction return a `503` error.

## Using The Dashboard

Start the Flask app and open `http://localhost:5002`.

### Run Crawler Now

Use this to crawl the built-in NHS website list in `run_crawler()` inside [app.py](app.py).

This mode:

- Crawls each configured organisation website.
- Detects pagination where possible.
- Searches for PDFs.
- Extracts dates with Gemini.
- Skips papers before 2024.
- Runs full Gemini analysis for papers from 2024 onwards.
- Saves results to `DATA_DIR/board_papers.json`.

This can take a long time because it uses a browser, downloads PDFs, and calls Gemini.

### Test Specific Websites

Use the text area to crawl only selected websites.

Supported input formats:

```text
https://www.example.nhs.uk/
https://www.another-example.nhs.uk/
```

or:

```text
@https://www.example.nhs.uk/ @https://www.another-example.nhs.uk/
```

### Test URLs

Runs the selected websites through the normal crawl and analysis process.

Use this when you want:

- PDF discovery.
- Date filtering.
- Full Gemini analysis.
- Results added to the displayed table.

### Scrape Only

Runs the selected websites with `scrape_only=true`.

Use this when you want to find 2024 onwards board papers without full term analysis. Gemini is still needed for date extraction, but the expensive full analysis step is skipped.

### Results Table

The table shows saved papers, including:

- Title or filename.
- Organisation.
- Date.
- Terms found.
- Organisation priorities, when available in stored results.
- Summary/term details, when analysis exists.
- Link to view the source PDF.

Use **Show New Papers Only** to filter the table to records marked as newly discovered during a run.

## API Endpoints

The app can also be used via HTTP.

### `GET /`

Loads the dashboard.

### `GET /results`

Returns the stored board paper list as JSON.

Example:

```bash
curl http://localhost:5002/results
```

### `POST /run-crawler`

Runs the built-in website crawl.

Example:

```bash
curl -X POST http://localhost:5002/run-crawler
```

### `POST /test-specific-urls`

Crawls specific website URLs.

Example with full analysis:

```bash
curl -X POST http://localhost:5002/test-specific-urls \
  -H "Content-Type: application/json" \
  -d "{\"urls\":[\"https://www.hct.nhs.uk/\"],\"scrape_only\":false}"
```

Example scrape-only request:

```bash
curl -X POST http://localhost:5002/test-specific-urls \
  -H "Content-Type: application/json" \
  -d "{\"urls\":[\"https://www.hct.nhs.uk/\"],\"scrape_only\":true}"
```

### `POST /analyze-papers`

Analyses specific PDF URLs.

Example:

```bash
curl -X POST http://localhost:5002/analyze-papers \
  -H "Content-Type: application/json" \
  -d "{\"urls\":[\"https://example.nhs.uk/board-paper.pdf\"]}"
```

The response includes:

- `url`
- `title`
- `date`
- `organization`
- `has_relevant_terms`
- `terms_found`
- `terms_count`
- `terms_data`
- `analysis_source`

`analysis_source` is usually:

- `cached` when existing analysis is reused.
- `new` when Gemini analysis was run.
- `error` when analysis failed for that paper.

## Data Storage

Results are stored in:

```text
data/board_papers.json
```

Change the directory with:

```env
DATA_DIR=some/other/path
```

The stored JSON contains:

- `last_run`
- `board_papers`

Each paper can include:

- `url`
- `filename`
- `title`
- `date`
- `trust`
- `organization`
- `has_relevant_terms`
- `terms_found`
- `terms_count`
- `terms_data`
- `is_new`
- `found_date`
- `sort_date`

To export results, either copy `data/board_papers.json` or call `GET /results`.

## Healthcare Terms And Analysis

The current Gemini prompts live in [utils/prompts](utils/prompts), with prompt-loading helpers in [utils/prompt_helper.py](utils/prompt_helper.py).

The analyzer looks for healthcare themes such as:

- Virtual wards.
- Remote patient monitoring.
- Hospital at home.
- Proactive care.
- Related operational and commercial context requested by the prompts.

For each term, the app attempts to store:

- Quotes from the paper.
- Structured summaries.
- Metadata such as date, title, and organisation.

## Running The Analyzer Script

[test_analyzer.py](test_analyzer.py) is a manual smoke script for a single PDF URL.

Make sure `GEMINI_API_KEY` is set, then run:

```bash
python test_analyzer.py
```

This script downloads and analyses the configured test PDF, then prints the result JSON.

## Docker And Deployment

The repo includes:

- [Dockerfile](Dockerfile)
- [Procfile](Procfile)
- [cloudbuild.yaml](cloudbuild.yaml)

For Docker-style deployments, make sure the runtime image includes Chromium and chromedriver at the paths expected by [crawler/crawler.py](crawler/crawler.py), or set `CHROMEDRIVER_PATH`.

Typical container environment variables:

```env
GEMINI_API_KEY=your-gemini-api-key
SECRET_KEY=your-production-secret
DATA_DIR=/app/data
```

The `Procfile` runs:

```text
web: gunicorn app:app
```

## Troubleshooting

### The dashboard loads, but analysis returns `503`

Set `GEMINI_API_KEY` in `.env` or in your deployment environment.

### Selenium cannot find Chrome or chromedriver

Install Chrome/Chromium locally, or set:

```env
CHROMEDRIVER_PATH=C:\path\to\chromedriver.exe
```

On Linux, ensure `/usr/bin/chromium` and `/usr/bin/chromedriver` exist, or update [crawler/crawler.py](crawler/crawler.py) for your image paths.

### Crawl4AI or Playwright reports missing browsers

Install the browser dependencies used by Crawl4AI/Playwright:

```bash
python -m playwright install chromium
```

If you use Crawl4AI's CLI setup command, ensure your Python scripts directory is on `PATH`.

### Crawling takes a long time

That is expected for full analysis. The app may:

- Open browser sessions.
- Visit multiple pages.
- Download large PDFs.
- Extract text from PDFs.
- Call Gemini multiple times.

Use **Scrape Only** when you only need discovery and date filtering.

### Papers are skipped

Papers are skipped when their extracted date is unknown or before 2024. Date extraction depends on PDF text quality and Gemini responses.

### PDF text extraction fails

Some PDFs are scanned images or have little extractable text. PyPDF2 may not extract useful content from those files. OCR is not currently built into this project.

## Project Structure

```text
app.py                  Flask app, routes, crawl orchestration
scrape_latest_board_papers.py
                        No-AI latest-board-paper downloader
config/trusts.json      Default trust list for the no-AI downloader
crawler/                Crawl4AI and Selenium website crawler
utils/config.py         Environment configuration
utils/pdf_analyzer.py   PDF download, text extraction, Gemini analysis
utils/prompt_helper.py  Prompt loading helpers
utils/prompts/          Prompt text files
utils/results_store.py  JSON persistence wrapper
templates/              Flask HTML templates
static/                 CSS and JavaScript
requirements-basic.txt  Minimal dependencies for the no-AI downloader
requirements.txt        Python runtime dependencies
```

## Maintenance Notes

- Keep `requirements.txt` limited to direct runtime dependencies imported by this app.
- Do not commit real API keys. Use `.env` locally and deployment environment variables in production.
- The full crawler depends on external website structure, browser availability, PDF quality, and Gemini availability, so use `/test-specific-urls` for focused debugging before running the full built-in crawl.
