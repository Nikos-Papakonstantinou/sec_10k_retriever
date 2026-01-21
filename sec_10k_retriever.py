import requests
import logging
import json
import time
import random
from playwright.sync_api import sync_playwright
import os


logging.basicConfig(level=logging.DEBUG) #TODO: revert to info
logger = logging.getLogger(__name__)


COMPANY_TO_TICKER = {
  "Apple": "AAPL",
  "Meta": "META",
  "Alphabet": "GOOGL",
  "Amazon": "AMZN",
  "Netflix": "NFLX",
  "Goldman Sachs": "GS",
}

companies_of_interest = ["Apple","Meta","Alphabet","Amazon", "Netflix", "Goldman Sachs"]
SEC_UA = "QuartrRetriever/1.0 (Quartr take-home; nikos.papakonstantinou@hotmail.com)"

# ------------------------
# SEC Fetching / Parsing
# ------------------------


def _create_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": SEC_UA
    })
    return session

def _get_json_with_retries(session: requests.Session, url: str, retries: int = 5, timeout: int = 20) -> dict:
    """
    Minimal retry helper: exponential backoff + jitter.
    """
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            last_err = e
            sleep_s = (2 ** attempt) + random.uniform(0.2, 1.2)
            logger.warning("GET failed (%s). Retry %d/%d in %.2fs: %s", url, attempt + 1, retries, sleep_s, e)
            time.sleep(sleep_s)
    raise RuntimeError(f"Failed to fetch JSON after {retries} retries: {url}") from last_err



def _to_cik_10_digits(cik_str: int) -> str:
    "Filling digits to use in the api calls"
    return str(cik_str).zfill(10)

def _construct_cik_mapping(tickers_json: dict):
    "This function returns a mapping of ticker -> SEC company record (includes cik_str, title)."

    cik_by_ticker = {}
    for row in tickers_json.values():
        t = row.get("ticker")
        if t:
            cik_by_ticker[t.upper()] = row
    
    return cik_by_ticker

def _refine_cik_by_company(cik_by_ticker: dict, ticker: str):
    row = cik_by_ticker.get(ticker)
    if not row:
        logger.warning(f"Ticker {ticker} not found in SEC mapping")
        return None
    cik_str = row.get("cik_str")
    if not cik_str:
        logger.warning(f"No cik_str found for ticker {ticker}")
        return None

    cik10 = _to_cik_10_digits(row.get("cik_str"))
    logger.debug(f"{ticker} -> CIK10: {cik10}")
    return cik10


def _get_company_ticker_maps(session: requests.Session) -> dict:
    url = "https://www.sec.gov/files/company_tickers.json"
    tickers_json = _get_json_with_retries(session, url)
    first_key = next(iter(tickers_json))
    logger.info(json.dumps(tickers_json[first_key], indent=2))
    cik_by_ticker = _construct_cik_mapping(tickers_json)
    cik10_by_ticker = {}
    for company in companies_of_interest:
        ticker = COMPANY_TO_TICKER[company]
        logger.debug(f"Refining CIKs for {company}...")
        cik10_by_ticker[ticker] = _refine_cik_by_company(cik_by_ticker, ticker)

    return cik_by_ticker, cik10_by_ticker

def safe_get(arr, i):
    # Defensive indexing (arrays should align, but don't assume)
    return arr[i] if isinstance(arr, list) and i < len(arr) else None

def _get_latest_10k_metadata(session: requests.Session, cik10: str) -> dict | None:
    ticker = "metadata entity"
    logger.debug(f"Retrieving metadata for {ticker}...")
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    data = _get_json_with_retries(session, submissions_url)

    company_name = data.get("name")
    recent = data.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    accession_numbers = recent.get("accessionNumber", [])
    primary_documents = recent.get("primaryDocument", [])

    # Selection rule:Prefer the most recent "10-K". If none, "10-K/A" as fallback.
    idx_10k = next((i for i, f in enumerate(forms) if f == "10-K"), None)
    idx_10ka = next((i for i, f in enumerate(forms) if f == "10-K/A"), None)
    idx = idx_10k if idx_10k is not None else idx_10ka

    if idx is None:
        logger.warning("No 10-K (or 10-K/A) found for CIK%s", cik10)
        return None

    meta = {
        "cik10": cik10,
        "company_name": company_name,
        "form": safe_get(forms, idx),
        "filing_date": safe_get(filing_dates, idx),
        "accession_number": safe_get(accession_numbers, idx),
        "primary_document": safe_get(primary_documents, idx),
    }

    logger.info("Latest %s for CIK%s: %s", meta.get("form"), cik10, meta)
    return meta


def _build_primary_doc_url(meta: dict) -> str:
    cik_int = str(int(meta["cik10"]))
    accession_nodash = meta["accession_number"].replace("-", "")
    primary_doc = meta["primary_document"]

    return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{primary_doc}"

def _make_output_path(ticker: str, meta: dict, ext: str = "html") -> str:
    form = meta.get("form", "UNKNOWN").replace("/", "_")
    filing_date = meta.get("filing_date", "unknown-date")
    accession = meta.get("accession_number", "unknown-accession").replace("-", "")
    return f"./{ticker}_{form}_{filing_date}_{accession}.{ext}"


def _download_html(session: requests.Session, url: str, out_path: str) -> None:
    resp = session.get(url, timeout=30)
    resp.raise_for_status()

    if "Your Request Originates from an Undeclared Automated Tool" in resp.text:
        raise RuntimeError("SEC returned the automated tool blocking page for HTML download.")

    with open(out_path, "wb") as f:
        f.write(resp.content)
    logger.debug("Saved HTML: %s", out_path)
    logger.debug("Status: %s", resp.status_code)
    logger.debug("Content-Type: %s", resp.headers.get("Content-Type"))
    logger.debug("Final URL: %s", resp.url)

# ------------------------
# PDF Rendering
# ------------------------

def _render_pdf_from_local_html(html_path: str, pdf_path: str) -> None:
    html_abspath = os.path.abspath(html_path)
    file_url = f"file://{html_abspath}"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()

        # Block ALL network requests (keeps rendering offline)
        def block_external(route):
            req_url = route.request.url
            if req_url.startswith("http://") or req_url.startswith("https://"):
                return route.abort()
            return route.continue_()

        context.route("**/*", block_external)

        page = context.new_page()
        page.goto(file_url, wait_until="load", timeout=60_000)

        page.pdf(
            path=pdf_path,
            format="Letter",
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template="""
              <div style="font-size:10px; width:100%; padding:0 12mm; color:#666;">
                <span style="float:right;">
                  Page <span class="pageNumber"></span> of <span class="totalPages"></span>
                </span>
              </div>
            """,
            margin={"top": "20mm", "bottom": "20mm", "left": "12mm", "right": "12mm"},
        )

        context.close()
        browser.close()

    logger.info("Saved PDF: %s", pdf_path)

def main():
    """
    Entry point:
    - Resolves company → ticker → CIK
    - Fetches latest 10-K metadata per company
    - Downloads primary 10-K HTML
    - Renders downloaded HTML to paginated PDF (offline render)
    """
    session = _create_session()
    logger.info("This is the main function")
    cik_by_ticker, cik10_by_ticker = _get_company_ticker_maps(session)
    logger.info(f"Retrieved {len(cik10_by_ticker)} tickers for {companies_of_interest}")
    logger.info(f"{cik10_by_ticker}")
    jobs = []

    for ticker, cik10 in cik10_by_ticker.items():
        try:
            meta = _get_latest_10k_metadata(session, cik10)
            if not meta:
                logger.warning("No meta found for %s", ticker)
                continue
            target_url = _build_primary_doc_url(meta)
            out_path_html = _make_output_path(ticker, meta, "html")
            _download_html(session, target_url, out_path_html)
            jobs.append((ticker, meta, out_path_html))
            time.sleep(0.3)
        except Exception as e:
            logger.exception("Failed downloading HTML for %s: %s", ticker, e)
            continue

    for ticker, meta, out_path_html in jobs:
        try:
            out_path_pdf = _make_output_path(ticker, meta, "pdf")
            _render_pdf_from_local_html(out_path_html, out_path_pdf)

        except Exception as e:
            logger.exception("Failed rendering PDF for %s: %s", ticker, e)
            continue


if __name__ == "__main__":
    logger.info("Initializing.")
    logger.debug("debug is visible")
    main()
