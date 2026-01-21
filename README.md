# sec_10k_retriever
Repository created for the purposes of Quartr home assignement. Details in readme.
=======
# SEC 10-K Retriever → PDF (Quartr Take-Home)

This script fetches the latest **10-K** filing for a list of companies from the **SEC EDGAR** API and converts the primary filing HTML into a **PDF**.

## What it does
For each company:
1. Resolves ticker → CIK using the SEC `company_tickers.json`
2. Fetches the latest 10-K metadata from `data.sec.gov/submissions/CIK##########.json`
3. Builds the primary filing document URL from the SEC Archives
4. Downloads the primary 10-K HTML
5. Renders the HTML into a paginated PDF using Playwright (Chromium)

## Requirements
- Python 3.10+ (recommended)
- `pip`

## Install
```bash
pip install requests playwright
python -m playwright install chromium
```

> Note: On Linux you may also need:
```bash
python -m playwright install-deps chromium
```

## Run
```bash
python sec_10k_retriever.py
```

## Output
The script saves one HTML and one PDF per company in the current directory, using the naming pattern:

```
<TICKER>_<FORM>_<FILING_DATE>_<ACCESSION>.html
<TICKER>_<FORM>_<FILING_DATE>_<ACCESSION>.pdf
```

Example:
```
AAPL_10-K_2025-10-31_000032019325000079.pdf
```

## Notes / Tradeoffs
- The script prefers the most recent `10-K` and falls back to `10-K/A` if needed.
- PDF rendering is performed from the downloaded local HTML file, and external network requests are blocked during rendering to avoid SEC automated-tool interstitial pages.  
  This makes conversion deterministic, but some external assets (e.g., images) may not appear in the PDF.
- Requests include a descriptive SEC-compliant `User-Agent`.
