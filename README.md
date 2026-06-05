# NHS Trust Board Paper Scraper & Sales Intelligence Platform

Scrapes board papers from all 47 NHS Mental Health Trusts, downloads PDFs, and uses AI to extract sales opportunities, procurement signals, and Trust intelligence profiles.

---

## Quick Start

### 1. Install dependencies

```powershell
# Windows — from the project folder
pip install -r requirements.txt
```

### 2. Set your Gemini API key

Create a `.env` file (copy from the example):

```powershell
# Windows
Copy-Item .env.example .env
```

Then open `.env` and add your key:

```
GEMINI_API_KEY=your-gemini-api-key-here
SECRET_KEY=any-random-string
```

> The key is **required** for the Sales Intelligence features (AI extraction, NL search, pitch generation). Plain scraping and downloading works without it.

### 3. Start the dashboard

```powershell
python app.py
```

Then open **http://localhost:5002** in your browser.

---

## What You Can Do

| Feature | Where |
|---|---|
| Download board papers from all 47 trusts | Dashboard → **Scrape Board Papers** card |
| Download via command line | `python scrape_latest_board_papers.py` |
| Extract AI sales intelligence from PDFs | Dashboard → **Sales Intelligence** tab |
| Ask natural language questions | Intelligence → **Ask a Question** |
| Find trusts that match your product | Intelligence → **Supplier Matching** |
| Generate outreach emails | Intelligence → **Generate Pitch & Email** |

---

## Scraping Board Papers

### Using the Dashboard

1. Open **http://localhost:5002**
2. In the **Scrape Board Papers** card:
   - Tick the report types you want (Board Papers is ticked by default)
   - Select specific trusts or leave all ticked for all 47
   - Optionally tick **Dry Run** to preview without downloading
3. Click **Start Scrape**
4. Watch the live log — progress streams in real time
5. Files land in `downloads/`

### Using the Command Line

```bash
# Download latest board paper from every trust
python scrape_latest_board_papers.py

# Preview what would be downloaded (no files written)
python scrape_latest_board_papers.py --dry-run

# Just one trust
python scrape_latest_board_papers.py --only "Birmingham" --dry-run

# Download more than one paper per trust
python scrape_latest_board_papers.py --limit-per-type 3

# Include strategy documents as well
python scrape_latest_board_papers.py --include-strategy
```

#### All CLI options

| Option | Default | What it does |
|---|---|---|
| `--dry-run` | off | Find papers without downloading |
| `--only "name"` | all 47 | Filter to trusts matching this text |
| `--types all` | `board` | Which types: `board`, `supplementary`, `strategy`, `digital_strategy`, or `all` |
| `--include-supplementary` | off | Also get supplementary packs |
| `--include-strategy` | off | Also get strategy documents |
| `--limit-per-type 3` | `1` | How many files per trust per type |
| `--all-matches` | off | Download everything found, not just the latest |
| `--max-pages 100` | `60` | How deep to crawl each site |
| `--output my_folder` | `downloads` | Where to save files |

### Where files are saved

```
downloads/
  birmingham-and-solihull-mental-health.../
    2026/
      [2026-06-03]_Birmingham_and_Solihull_..._board_Public-Board-of-Directors-3-June-2026.pdf
      [2026-06-03]_...pdf.metadata.json
```

Each file gets a `.metadata.json` sidecar with the source URL, date, report type, and other details.

---

## Sales Intelligence Platform

The Intelligence Platform reads your downloaded PDFs and uses Gemini AI to extract structured insights — opportunities, procurement signals, strategic priorities, and more.

### Step 1 — Download some papers first

Run a scrape (see above). PDFs must be in the `downloads/` folder before the pipeline can process them.

### Step 2 — Open the Intelligence tab

Click **Sales Intelligence** in the top navigation bar (or go to **http://localhost:5002/intelligence**).

### Step 3 — Run the pipeline

Click **Run Pipeline**. The pipeline will:

1. Find all PDFs in `downloads/` not yet processed
2. Extract full text from each PDF
3. Send each chunk to Gemini for structured extraction
4. Save opportunities, procurement signals, and timeline events to a database
5. Generate a written intelligence profile for each Trust

Watch the live log for progress. Large PDFs take 30–60 seconds each.

### Step 4 — Explore the results

**Select any Trust** from the left panel to see:

- **Profile** — written summaries of digital strategy, priorities, challenges, and financials
- **Opportunities** — categorised sales opportunities with confidence scores and evidence quotes
- **Procurement Signals** — classified by intent (high / medium / early stage)
- **Timeline** — upcoming milestones and procurement dates

### Ask a question

Type any natural language question in the **Ask a Question** box:

```
Which trusts mention ambient voice technology?
Which trusts have approved AI budgets this year?
Which trusts are planning an EPR replacement?
```

The platform searches across all indexed board papers and returns a cited answer.

### Find matching trusts for your product

Paste a description of your product/service in the **Supplier Matching** box and click **Find Matching Trusts**:

```
AI-powered ambient voice documentation platform with EPR integration
and clinical workflow automation for secondary mental health care.
```

You'll get a ranked list of trusts with evidence quotes showing why each one is relevant.

### Generate an outreach email

After matching, type a trust name into the **Trust** field and click **Generate Pitch & Email**. You'll get:

- A tailored value proposition
- A ready-to-send email draft
- Discovery call questions
- Recommended themes to lead with

---

## Intelligence Database

All extracted data is stored in `data/intelligence.db` (SQLite). It is automatically created when you first run the pipeline.

What is stored per Trust:

| Table | Contents |
|---|---|
| `opportunities` | Category, description, confidence score, evidence quote |
| `procurement_signals` | Signal type (high/medium/early), description, evidence |
| `timeline_events` | Dates, programmes, milestones |
| `extracted_insights` | Priorities, challenges, digital initiatives, financial items |
| `trust_profiles` | AI-written summaries across 6 dimensions |
| `board_papers` | Full extracted text, page count, file path |

The vector search index lives in `data/chroma/` and powers the Ask a Question feature.

---

## Troubleshooting

### A trust is missing recent papers

Update its `start_urls` in `config/mental_health_trusts.json` to point directly at its board papers page:

```json
{
  "name": "Example NHS Foundation Trust",
  "url": "https://www.example.nhs.uk/",
  "start_urls": [
    "https://www.example.nhs.uk/about-us/corporate-documents/board-papers/"
  ]
}
```

Then rerun with `--dry-run` to confirm:

```bash
python scrape_latest_board_papers.py --only "Example" --dry-run
```

### The scraper visits the right page but finds no PDFs

The site may require JavaScript to load its document list. Add `"js_render": true` to that trust's entry in the JSON config.

### Pipeline says GEMINI_API_KEY is not set

Make sure your `.env` file contains:

```
GEMINI_API_KEY=your-key-here
```

Plain scraping works without the key. The pipeline, NL search, and pitch generation all require it.

### Ask a Question returns "No relevant documents found"

The vector index is empty. Run the pipeline first — it must process at least some PDFs before search works.

### 404 errors in the scrape log

Normal. The scraper tries many common URL patterns and skips the ones that don't exist for each trust.

### Slow scraping

The default 0.5-second delay between requests is intentional (polite to NHS servers). A full 47-trust run takes 30–90 minutes depending on site speed.

---

## Project Structure

```
├── scraper/                  Core scraping engine
│   ├── constants.py          URL patterns, keyword lists, report type definitions
│   ├── discovery.py          Main crawl loop
│   ├── scoring.py            Keyword scoring and report type classification
│   ├── navigation.py         Sitemap parsing
│   ├── downloader.py         File download and naming
│   └── engine.py             ScraperEngine (Flask integration, background jobs)
│
├── intelligence/             Sales Intelligence Platform
│   ├── database.py           SQLite schema (SQLAlchemy)
│   ├── pipeline.py           PDF extraction + Gemini AI analysis
│   ├── runner.py             Background job runner
│   ├── embeddings.py         ChromaDB vector search + RAG
│   └── matching.py           Supplier matching + pitch generation
│
├── config/
│   └── mental_health_trusts.json   All 47 trust entries with URLs
│
├── templates/
│   ├── index.html            Scraper dashboard
│   └── intelligence.html     Sales Intelligence dashboard
│
├── app.py                    Flask app and all routes
├── scrape_latest_board_papers.py   CLI scraper
└── requirements.txt
```

---

## API Reference (quick)

| Method | Route | What it does |
|---|---|---|
| `GET` | `/scrape/trusts` | List all 47 trusts |
| `POST` | `/scrape/start` | Start a scrape job, returns `job_id` |
| `GET` | `/scrape/stream/<job_id>` | Live SSE log stream |
| `DELETE` | `/scrape/cancel/<job_id>` | Cancel a running job |
| `POST` | `/intelligence/run` | Start the intelligence pipeline |
| `GET` | `/intelligence/trusts` | List all trusts with paper/opportunity counts |
| `GET` | `/intelligence/trust/<name>` | Full profile for one trust |
| `GET` | `/intelligence/opportunities` | All opportunities (filter by `?category=&trust=`) |
| `GET` | `/intelligence/search?q=` | Keyword search across insights |
| `GET` | `/intelligence/ask?q=` | Natural language RAG search |
| `POST` | `/match-supplier` | Rank trusts by relevance to supplier capabilities |
| `POST` | `/generate-pitch` | Generate sales pitch + email for a trust |
