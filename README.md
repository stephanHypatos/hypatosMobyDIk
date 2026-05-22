# 🐋 MobyDik · Hypatos

A Streamlit app that fetches documents from the Hypatos API and applies the MobyDik sorting and classification logic. Results are displayed in-browser and can be exported as CSV. A built-in scheduler runs the pipeline automatically every X minutes.

---

## Features

- **API authentication** — OAuth 2.0 client credentials flow (EU and US regions supported)
- **Field mapping** — map any Hypatos entity field to a MobyDik column; load available fields directly from sample documents
- **MobyDik processing** — sort articles by REF number or quantity, group by document (Belegnummer), detect float quantities, direct deliveries, and info articles
- **Run on demand** — one-click pipeline execution with live results
- **Scheduler** — background cron that re-runs the pipeline every N minutes without leaving the browser
- **CSV export** — download the sorted output at any time

---

## Project structure

```
MobYDIk/
├── app.py                # Streamlit UI (sidebar, tabs, scheduler)
├── auth.py               # HypatosAPI — OAuth2 authentication + project helpers
├── hypatos_client.py     # HypatosDocumentClient — paginated document fetching
├── moby_processor.py     # MobyDik sorting & classification logic
├── requirements.txt
└── README.md
```

---

## Requirements

- Python 3.11+
- A Hypatos account with API credentials (client ID + secret) that have the `documents.read` scope

---

## Installation

```bash
# 1. Clone or copy the project folder
cd MobYDIk

# 2. (Optional) create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Launch the app
streamlit run app.py
```

The app opens at **http://localhost:8501**.

---

## Usage

### 1. Connect

In the sidebar:
- Select your **region** (EU → `api.cloud.hypatos.ai` / US → `api.cloud.hypatos.com`)
- Enter your **Client ID** and **Client Secret**
- Click **Connect** — the app authenticates and loads your projects

### 2. Field Mapping (Tab 1)

| Step | Action |
|------|--------|
| Select projects | Filter by one or more projects, or leave empty for all |
| Load fields | Click **Load fields from API** — pulls a sample of documents and discovers available entity field names |
| Map fields | For each MobyDik column, choose the corresponding Hypatos entity field from the dropdown (or type it manually) |
| Save | Click **Save mapping** |

**Nested fields** (e.g. line-item arrays) are supported using dot notation: `items.articleNumber`.

### 3. Sorting Rules (Tab 2)

| Setting | Description |
|---------|-------------|
| Quantity-first suppliers | Suppliers sorted by **Menge Gebinde** first, then REF — one name per line |
| Order-sequence suppliers | Documents from these suppliers keep their original order (no re-sort) |
| Info articles | Article number patterns to watch — matching rows are highlighted in results |

Click **Save rules** when done.

### 4. Run / Schedule (Tab 3)

- **Run pipeline** — fetches documents with state `done` / `doneAutomatically` and processes them immediately
- **Scheduler** — set an interval in minutes, then click **Start scheduler**; the pipeline runs in the background and results refresh automatically. Click **Stop scheduler** to cancel.

Results show:
- Summary metrics (total rows, floats, directs, info hits)
- Sortable dataframe of all processed rows
- Expandable sections for flagged rows (float quantities, direct deliveries, info article hits)
- Full processing log
- **Download CSV** button

---

## MobyDik logic overview

| Concept | Description |
|---------|-------------|
| Grouping | Documents are grouped by **Belegnummer** (first 10 chars of the document ID, or the mapped field) |
| Sorting | Default: REF first, then Menge. Configurable per supplier. |
| Float flag | Rows where **Menge Gebinde** contains a comma or decimal point |
| Direct flag | Rows where **Bemerkung** contains `#DL` or **Artikelbezeichnung** contains `#Direkt` |
| Info articles | Rows whose **Artikelnummer** matches any configured pattern |
| CSV output | Semicolon-delimited, with all MobyDik column headers |

---

## API endpoints used

| Endpoint | Purpose |
|----------|---------|
| `POST /auth/token` | OAuth2 token (client credentials) |
| `GET /projects` | Load available projects for the dropdown |
| `GET /documents` | Paginated document list (with project + state filters) |
| `GET /documents/{id}` | Full document detail including entities |

Base URLs:
- EU: `https://api.cloud.hypatos.ai/v2`
- US: `https://api.cloud.hypatos.com/v2`
