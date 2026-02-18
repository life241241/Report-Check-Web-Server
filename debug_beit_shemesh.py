import requests
import json
import re
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "he,en;q=0.9",
}
base = "https://www.doh.co.il"
ID_NUMBER = "207089616"
CAR_NUMBER = "6185313"
QCODE = "1621.7973811.1486367.1"
TIMEOUT = 45  # Beit Shemesh is VERY slow

s = requests.Session()

# Step 1: Load page for cookies
print("Step 1: Loading Default.aspx...")
r = s.get(f"{base}/Default.aspx?a={QCODE}", headers=HEADERS, timeout=TIMEOUT)
print(f"  -> {r.status_code}")

# Step 2: setParam with qcode
print("Step 2: setParam (qcode)...")
r = s.post(f"{base}/Menu/setParam.aspx", data={
    "action": "getData", "a": QCODE
}, headers={**HEADERS, "Referer": f"{base}/Default.aspx?a={QCODE}",
    "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"
}, timeout=TIMEOUT)
params = r.json()
rashut = str(params.get("Rashut", ""))
report_type = str(params.get("ReportType", "1"))
sw_qr = str(params.get("SwQR", "0"))
language = str(params.get("language", "he"))
print(f"  -> Rashut={rashut}, ReportType={report_type}, SwQR={sw_qr}")

# Step 3: Load step1.aspx
print("Step 3: step1.aspx...")
r = s.get(f"{base}/step1.aspx", headers={**HEADERS, "Referer": f"{base}/Default.aspx"}, timeout=TIMEOUT)
print(f"  -> {r.status_code}")

# Step 4: Check_Report
print("Step 4: Check_Report (this may be slow)...")
r = s.post(f"{base}/Check_Report.aspx", data={
    "status": "Check_Report", "StrFind": CAR_NUMBER, "ReportNo": ID_NUMBER,
    "ReportType": report_type, "tokenCaptcha": "", "SwShow": "", "SwOrder": "1"
}, headers={**HEADERS, "Referer": f"{base}/step1.aspx", "Origin": base,
    "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest"},
    timeout=TIMEOUT)
print(f"  -> Status: {r.status_code}")
data = r.json()
doch_c = data.get("C", 0)
print(f"  -> Full response: {json.dumps(data, ensure_ascii=False)}")

if doch_c == 0:
    print("No fines found!")
    exit()

# Step 5: First fetch step2.js to understand the API
print("\nStep 5: Fetching step2.js to analyze API calls...")
r_js = s.get(f"{base}/js/step2.js", headers=HEADERS, timeout=TIMEOUT)
print(f"  -> step2.js: {r_js.status_code}, {len(r_js.text)} chars")

# Print the full step2.js (it should reveal the GetDetails AJAX call)
print("\n=== step2.js FULL CONTENT ===")
print(r_js.text[:5000])
if len(r_js.text) > 5000:
    print(f"\n... ({len(r_js.text)} total chars) ...")
    print(r_js.text[5000:10000])

# Step 6: Navigate to step2.aspx  
step2_url = f"{base}/step2.aspx?StrFind={CAR_NUMBER}&ReportNo={ID_NUMBER}&status=GetDetails&ReportType={report_type}&DochC={doch_c}&SwQR={sw_qr}&language={language}&Rashut={rashut}&SwOrder=1"
print(f"\nStep 6: Loading step2.aspx...")
print(f"  URL: {step2_url}")
r = s.get(step2_url, headers={**HEADERS, "Referer": f"{base}/step1.aspx"}, timeout=TIMEOUT)
print(f"  -> {r.status_code}, {len(r.text)} chars")

# Show step2.aspx HTML briefly
soup = BeautifulSoup(r.text, "html.parser")
print(f"\n  Inline scripts:")
for sc in soup.find_all("script"):
    if not sc.get("src") and sc.string:
        print(f"    {sc.string.strip()[:300]}")

# Step 7: Parse the step2.aspx HTML for fine data
print("\nStep 7: Parsing step2.aspx HTML for fine data...")
print(f"\n=== step2.aspx HTML (full) ===")
print(r.text)

# Look for tables, prices, fine data
print("\n=== Tables found ===")
tables = soup.find_all("table")
print(f"  {len(tables)} tables")
for i, tbl in enumerate(tables):
    rows = tbl.find_all("tr")
    print(f"\n  Table {i}: {len(rows)} rows")
    for j, row in enumerate(rows):
        cells = row.find_all(["td", "th"])
        cell_texts = [c.get_text(strip=True) for c in cells]
        print(f"    Row {j}: {cell_texts}")

# Look for price/amount divs
print("\n=== Price/amount elements ===")
for el in soup.find_all(class_=re.compile(r"price|amount|sum|total", re.I)):
    print(f"  {el.name}.{el.get('class')}: {el.get_text(strip=True)[:200]}")

# Look for checkboxes (fine selection)
print("\n=== Checkboxes (fine items) ===")
for cb in soup.find_all("input", {"type": "checkbox"}):
    print(f"  name={cb.get('name')} data-price={cb.get('data-price')} data-swprice={cb.get('data-swprice')}")

# Look for divs with 'tableDiv' class (fine rows)
print("\n=== tableDiv elements ===")
for div in soup.find_all(class_=re.compile(r"tableDiv", re.I)):
    print(f"\n  div.{div.get('class')}:")
    print(f"    {div.get_text(strip=True)[:300]}")

# Look for ₪ symbol
print("\n=== Elements containing ₪ ===")
for el in soup.find_all(string=re.compile(r"₪")):
    print(f"  {el.parent.name}: {el.strip()[:200]}")

print("\nDone.")


