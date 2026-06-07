# NHS Evidence Scraper & Sales Intelligence Platform

Scrapes documents from all 47 NHS Mental Health Trusts, 42 ICBs/Commissioners, and 8 national NHS datasets. Downloads PDFs and spreadsheets, then uses AI to extract sales opportunities, procurement signals, and trust intelligence profiles.

---

## Quick Start

### 1. Install dependencies

```powershell
pip install -r requirements.txt
```

### 2. Set your Gemini API key (optional)

Create a `.env` file:

```powershell
Copy-Item .env.example .env
```

Open `.env` and add:

```
GEMINI_API_KEY=your-gemini-api-key-here
SECRET_KEY=any-random-string
```

> The key is required for Sales Intelligence features (AI extraction, NL search, pitch generation). Scraping and downloading works without it.

### 3. Start the dashboard

```powershell
python app.py
```

Open **http://localhost:5002**.

---

## What You Can Do

| Feature | Where |
|---|---|
| Scrape NHS trust documents (47 trusts, 7 types) | Dashboard → tick **NHS Mental Health Trusts** |
| Scrape ICB / commissioner documents (42 ICBs, 3 types) | Dashboard → tick **ICBs / Commissioners** |
| Download national NHS datasets (8 sources) | Dashboard → tick **National NHS Datasets** |
| Run all three sources simultaneously | Tick all three → **Start Selected Sources** |
| Download summary CSV after each run | Results section → **Download Summary (CSV)** |
| View which orgs consistently fail | Dashboard → **Failure Log** |
| Manually add or fix website URLs | Dashboard → **Organisation Editor** |
| Extract AI sales intelligence from PDFs | **Sales Intelligence** tab |
| Ask natural language questions | Intelligence → **Ask a Question** |
| Find trusts that match your product | Intelligence → **Supplier Matching** |
| Generate outreach emails | Intelligence → **Generate Pitch & Email** |

---

## Scraping Documents

### Using the Dashboard

1. Open **http://localhost:5002**
2. Tick one or more sources: **NHS Mental Health Trusts**, **ICBs / Commissioners**, **National NHS Datasets**
3. Each source expands to show its own configuration:
   - Tick the document types you want
   - Set a lookback period per type (months, 0 = no limit)
   - Select specific organisations or leave all deselected to run all
4. Set shared settings: parallel workers, request delay, max releases per type
5. Click **Start Selected Sources** — all ticked sources run simultaneously
6. Progress streams live per source; when done a results table appears
7. Click **Download Summary (CSV)** to export a spreadsheet of what was found

### Document Types

#### Trust documents

| Key | What it is | Default lookback |
|---|---|---|
| Board Papers | Full board meeting packs and agendas | 3 months |
| Quality Account | Annual quality improvement reports | 24 months |
| Annual Report | Annual report and accounts | 24 months |
| Strategic Reporting | Trust strategy and long-term plans | 36 months |
| Digital Strategy | Digital and technology strategies | 36 months |
| Supplementary | Supporting papers and appendices | 3 months |
| CQC Report | CQC inspection reports | 36 months |

#### ICB / Commissioner documents

| Key | What it is |
|---|---|
| Joint Forward Plan | 5-year NHS system plan (mandated by NHS England) |
| ICB MH Strategy | Mental health and SMI strategies |
| Integrated Care Strategy | ICS health and wellbeing strategies |

#### National datasets

| Key | Source | Format |
|---|---|---|
| MHSDS | NHS Mental Health Services Data Set — monthly statistics | XLSX/CSV |
| OAP | Out of Area Placements (archived April 2024, now in MHSDS) | CSV |
| CQC Survey | CQC Community Mental Health Survey | ODS/XLSX |
| NCAP | National Clinical Audit of Psychosis | PDF |
| Fingertips | OHID Adult Mental Health profile | CSV |
| QOF | Quality and Outcomes Framework SMI registers | XLSX |
| PHSMI | Physical Health Checks for SMI (quarterly) | CSV |
| Oversight | NHS Oversight Framework segmentation tables | CSV/XLSX |

National dataset files are saved to `downloads/national/<source>/` and skipped on re-runs if already present.

### Where files are saved

```
downloads/
  birmingham-and-solihull-mental-health.../
    2026/
      [2026-06-03]_Birmingham_..._board_Public-Board-of-Directors.pdf
      [2026-06-03]_....metadata.json
  national/
    mhsds/
      Mental-Health-Services-Monthly-Statistics-Performance-May-2026.xlsx
    cqc-survey/
      2026_community_mental_health_benchmark.ods
    ...
```

Each scraped file gets a `.metadata.json` sidecar with the source URL, date, report type, and scoring details.

### Rate limiting

If NHS sites return 429 errors, increase the **Request delay** slider (1–2s is usually sufficient). The scraper automatically retries 429 responses up to 3 times, using the `Retry-After` header when present.

---

## Failure Log & Caching

The dashboard includes a **Failure Log** showing every organisation that returned no documents on its last run, sorted by consecutive failure count. Organisations with 3+ consecutive failures are highlighted in red.

On re-runs, previously-failed organisations are fast-checked against their cached known-good pages first — this avoids wasting time re-crawling dead start URLs. If that finds nothing, a full crawl runs as a fallback.

To investigate persistent failures, open the **Organisation Editor** and add the correct document directory URL as a start URL.

---

## Organisation Editor

The **Organisation Editor** (bottom of dashboard) lets you add or edit website entries without touching config files directly.

- **Edit existing** — select an organisation from the dropdown, update its base URL, start URLs, or allowed domains, and save
- **Add new** — click "+ Add New", fill in the form, and save; the entry is immediately available for the next scrape

Start URLs are the specific pages the crawler begins from (e.g. `.../publications/board-papers/`). The crawler follows links from these pages up to the configured max-pages limit.

Allowed domains (optional) restrict which domains the crawler will follow links to — useful for ICBs whose documents live on a partner site.

Changes are written to `config/mental_health_trusts.json` or `config/icb_config.json` and take effect immediately.

---

## Using the Command Line

```bash
# Download latest board paper from every trust
python scrape_latest_board_papers.py

# Preview without downloading
python scrape_latest_board_papers.py --dry-run

# One trust only
python scrape_latest_board_papers.py --only "Birmingham" --dry-run

# Multiple releases per trust
python scrape_latest_board_papers.py --limit-per-type 3

# Include strategy documents
python scrape_latest_board_papers.py --include-strategy
```

#### CLI options

| Option | Default | What it does |
|---|---|---|
| `--dry-run` | off | Find without downloading |
| `--only "name"` | all | Filter to trusts matching this text |
| `--types all` | `board` | Comma-separated types or `all` |
| `--limit-per-type N` | `1` | Files per trust per type |
| `--all-matches` | off | Download everything found |
| `--max-pages N` | `60` | Crawl depth per site |
| `--output folder` | `downloads` | Download location |

---

## Sales Intelligence Platform

The Intelligence Platform reads downloaded PDFs and uses Gemini AI to extract structured insights.

### Run the pipeline

1. Download some papers (see above)
2. Open **http://localhost:5002/intelligence**
3. Click **Run Pipeline**

The pipeline extracts opportunities, procurement signals, timeline events, and generates a written intelligence profile per trust.

### Explore results

Select any trust from the left panel to see:
- **Profile** — digital strategy, priorities, challenges, financials
- **Opportunities** — categorised with confidence scores and evidence quotes
- **Procurement Signals** — classified by intent (high / medium / early stage)
- **Timeline** — upcoming milestones and dates

### Ask a question

```
Which trusts mention ambient voice technology?
Which trusts have approved AI budgets this year?
Which trusts are planning an EPR replacement?
```

### Match your product to trusts

Paste a product description into **Supplier Matching** to get a ranked list of trusts with supporting evidence.

### Generate an outreach email

Select a trust and click **Generate Pitch & Email** for a tailored value proposition, email draft, and discovery call questions.

---

## Project Structure

```
├── scraper/
│   ├── constants.py          URL patterns, keywords, all 10 report type definitions
│   ├── discovery.py          Main crawl loop
│   ├── scoring.py            Keyword scoring and type classification
│   ├── downloader.py         File download and naming
│   ├── engine.py             ScraperEngine — trust + ICB scraping, job management
│   ├── national_datasets.py  8 national dataset fetchers
│   ├── national_engine.py    NationalFetchEngine — job management for national fetches
│   ├── failure_cache.py      Tracks orgs with no results for fast-check optimisation
│   └── session.py            HTTP session, 429 retry with backoff
│
├── intelligence/
│   ├── database.py           SQLite schema (SQLAlchemy)
│   ├── pipeline.py           PDF extraction + Gemini AI analysis
│   ├── runner.py             Background job runner
│   ├── embeddings.py         ChromaDB vector search + RAG
│   └── matching.py           Supplier matching + pitch generation
│
├── config/
│   ├── mental_health_trusts.json   47 trust entries with URLs and start_urls
│   └── icb_config.json             42 ICB entries with URLs and start_urls
│
├── data/
│   ├── failure_cache.json    Per-org failure history (auto-managed)
│   └── discovery_cache.json  Per-org known-good page cache (auto-managed)
│
├── templates/
│   ├── index.html            Evidence scraper dashboard
│   └── intelligence.html     Sales Intelligence dashboard
│
├── app.py                    Flask app and all routes
└── requirements.txt
```

---

## API Reference

### Scraping

| Method | Route | Description |
|---|---|---|
| `GET` | `/scrape/trusts` | List all 47 trusts |
| `GET` | `/scrape/icbs` | List all 42 ICBs |
| `POST` | `/scrape/start` | Start a scrape job (`source`: `trust` or `icb`) |
| `GET` | `/scrape/stream/<job_id>` | Live SSE log stream |
| `GET` | `/scrape/status/<job_id>` | Job status JSON |
| `GET` | `/scrape/results/<job_id>` | Results table JSON |
| `GET` | `/scrape/export/<job_id>` | Download results as CSV |
| `DELETE` | `/scrape/cancel/<job_id>` | Cancel a running job |
| `GET` | `/scrape/failures` | List failure cache entries |
| `DELETE` | `/scrape/failures/<name>` | Clear one failure entry |
| `DELETE` | `/scrape/failures/clear-all` | Clear all failure entries |

### National Datasets

| Method | Route | Description |
|---|---|---|
| `GET` | `/national/sources` | List all 8 dataset sources |
| `POST` | `/national/fetch` | Start a fetch job (`source_keys`: list or null for all) |
| `GET` | `/national/stream/<job_id>` | Live SSE stream (named events) |
| `GET` | `/national/status/<job_id>` | Job status JSON |

### Config / Org Editor

| Method | Route | Description |
|---|---|---|
| `GET` | `/config/org?name=&source=` | Get one org's config |
| `PUT` | `/config/org` | Update an existing org |
| `POST` | `/config/org` | Add a new org |

### Sales Intelligence

| Method | Route | Description |
|---|---|---|
| `POST` | `/intelligence/run` | Start the intelligence pipeline |
| `GET` | `/intelligence/trusts` | List trusts with counts |
| `GET` | `/intelligence/trust/<name>` | Full profile for one trust |
| `GET` | `/intelligence/opportunities` | All opportunities (`?category=&trust=&min_confidence=`) |
| `GET` | `/intelligence/search?q=` | Keyword search across insights |
| `GET` | `/intelligence/ask?q=` | Natural language RAG search |
| `POST` | `/match-supplier` | Rank trusts by relevance |
| `POST` | `/generate-pitch` | Generate pitch and email for a trust |

---

## Troubleshooting

### A trust or ICB is not finding documents

1. Open the **Failure Log** to see its failure history
2. Open the **Organisation Editor**, select the org, and add the specific publications page as a start URL (e.g. `https://www.example.nhs.uk/about-us/publications/`)
3. Re-run with **Dry run** ticked to confirm documents are now found before downloading

### The scraper visits the right page but finds no PDFs

The site may require JavaScript to load its document list. Add `"js_render": true` to that entry in the config file.

### Getting 429 rate-limit errors

Increase the **Request delay** slider to 1–2 seconds. The scraper already retries automatically on 429, but a higher base delay prevents hitting the limit in the first place.

### Pipeline says GEMINI_API_KEY is not set

Add it to your `.env` file. Plain scraping works without the key.

### Slow scraping

Default 0.5s delay is intentional. A full 47-trust run takes 30–90 minutes. Run fewer trusts or increase parallel workers to speed up, or increase the delay if getting rate-limited.
