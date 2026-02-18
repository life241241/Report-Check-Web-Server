from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import time

app = FastAPI(title="Parking Fines API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "he,en;q=0.9",
}

MUNICIPALITIES = [
    {"name": "עיריית בית שמש", "rashut": "1621", "report_type": "1", "qcode": "1621.7973811.1486367.1"},
    {"name": "עיריית רמת גן", "rashut": "186111", "report_type": "1"},
    {"name": "עיריית מודיעין עילית", "rashut": "920094", "report_type": "1"},
    {"name": "עיריית גבעתיים", "rashut": "920044", "report_type": "1"},
    {"name": "מ.א דרום השרון", "rashut": "920058", "report_type": "1"},
    {"name": "עיריית הרצליה", "rashut": "920039", "report_type": "1"},
    {"name": "מועצה אזורית גוש עציון", "rashut": "920041", "report_type": "1"},
    {"name": "עיריית כפר קאסם", "rashut": "920061", "report_type": "1"},
    {"name": "מועצה מקומית בית דגן", "rashut": "920016", "report_type": "1"},
    {"name": "מ.מ. מזכרת בתיה", "rashut": "920037", "report_type": "1"},
    {"name": "מועצה מקומית שוהם", "rashut": "920038", "report_type": "1"},
    {"name": "עיריית מעלה אדומים", "rashut": "836160", "report_type": "1"},
    {"name": "עיריית גני תקווה", "rashut": "920010", "report_type": "1"},
    {"name": "עיריית מצפה רמון", "rashut": "920053", "report_type": "1"},
    {"name": "עיריית ערד", "rashut": "920021", "report_type": "1"},
    {"name": "עיריית טירת כרמל", "rashut": "920056", "report_type": "1"},
    {"name": "עיריית כוכב יאיר-צור יגאל", "rashut": "920051", "report_type": "1"},
    {"name": "מ.א עמק יזרעאל", "rashut": "920015", "report_type": "1"},
    {"name": "עיריית שדרות", "rashut": "920057", "report_type": "1"},
    {"name": "עיריית יהוד - מונוסון", "rashut": "920011", "report_type": "1"},
    {"name": "רשות שדות התעופה", "rashut": "920070", "report_type": "1"},
    {"name": "מ.מ אורנית", "rashut": "920043", "report_type": "1"},
]


class CheckRequest(BaseModel):
    id_number: str
    car_number: str


def _get_fines_from_step2(session, base, car_number, id_number, report_type, doch_c, rashut, sw_qr, language):
    try:
        step2_url = (
            f"{base}/step2.aspx?StrFind={car_number}&ReportNo={id_number}"
            f"&status=GetDetails&ReportType={report_type}&DochC={doch_c}"
            f"&SwQR={sw_qr}&language={language}&Rashut={rashut}&SwOrder=1"
        )
        r = session.get(step2_url, headers={**HEADERS, "Referer": f"{base}/step1.aspx"}, timeout=45)
        if r.status_code != 200:
            return {"status": "failed", "error": f"step2 HTTP {r.status_code}"}

        soup = BeautifulSoup(r.text, "html.parser")
        fines = []
        total = 0.0
        for row in soup.select("tr.tableDiv.data, tr[class*='tableDiv'][class*='data']"):
            fine = {}
            label = row.find("label")
            if label:
                fine["number"] = label.get_text(strip=True)
            checkbox = row.find("input", {"type": "checkbox"})
            if checkbox and checkbox.get("data-price"):
                try:
                    price = float(checkbox["data-price"])
                    fine["amount"] = price
                    total += price
                except ValueError:
                    pass
            price_el = row.find(class_="price")
            if price_el:
                fine["price_display"] = price_el.get_text(strip=True)
            cell_divs = row.find_all("div", class_="cell")
            for div in cell_divs:
                text = div.get_text(strip=True)
                if re.match(r"\d{2}/\d{2}/\d{4}", text):
                    fine["date"] = text
                elif re.match(r"\d{2}:\d{2}$", text):
                    fine["time"] = text
            if fine:
                fines.append(fine)

        if fines:
            return {"status": "fine", "count": len(fines), "amount": f"{total:.2f}" if total > 0 else "ראה פרטים", "fines": fines}
        return {"status": "fine", "count": doch_c, "amount": "לא ידוע (C>0, step2 ריק)"}
    except Exception as e:
        return {"status": "fine", "count": doch_c, "amount": f"לא ידוע (step2 שגיאה: {e})"}


def check_municipality(name, rashut, report_type, id_number, car_number, qcode=None):
    base = "https://www.doh.co.il"
    session = requests.Session()
    try:
        if qcode:
            page_url = f"{base}/Default.aspx?a={qcode}"
        else:
            page_url = f"{base}/Default.aspx?ReportType={report_type}&Rashut={rashut}"
        session.get(page_url, headers=HEADERS, timeout=15)

        if qcode:
            param_data = {"action": "getData", "a": qcode}
        else:
            param_data = {"action": "getData", "ReportType": report_type, "Rashut": rashut, "language": "", "SwShow": "", "TK": ""}

        r_param = session.post(f"{base}/Menu/setParam.aspx", data=param_data, headers={
            **HEADERS, "Referer": page_url, "X-Requested-With": "XMLHttpRequest", "Content-Type": "application/x-www-form-urlencoded"
        }, timeout=15)

        try:
            param_resp = r_param.json()
            actual_rashut = str(param_resp.get("Rashut", rashut))
            sw_qr = str(param_resp.get("SwQR", "0"))
            language = str(param_resp.get("language", "he"))
        except:
            actual_rashut = rashut
            sw_qr = "1" if qcode else "0"
            language = "he"

        session.get(f"{base}/step1.aspx", headers={**HEADERS, "Referer": page_url}, timeout=15)

        r = session.post(f"{base}/Check_Report.aspx", data={
            "status": "Check_Report", "StrFind": car_number, "ReportNo": id_number,
            "ReportType": report_type, "tokenCaptcha": "", "SwShow": "", "SwOrder": "1"
        }, headers={
            **HEADERS, "Referer": f"{base}/step1.aspx", "Origin": base,
            "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest",
        }, timeout=45)

        if r.status_code != 200:
            return {"name": name, "status": "failed", "error": f"HTTP {r.status_code}"}

        data = r.json()
        count = data.get("C", 0)
        itra_sum = data.get("ItraSum", "")

        if count == 0:
            return {"name": name, "status": "clean"}

        if itra_sum:
            return {"name": name, "status": "fine", "count": count, "amount": itra_sum, "person_name": data.get("Nm", "")}

        result = _get_fines_from_step2(session, base, car_number, id_number, report_type, count, actual_rashut, sw_qr, language)
        result["name"] = name
        return result

    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        return {"name": name, "status": "failed", "error": "timeout/connection error"}
    except requests.exceptions.JSONDecodeError:
        return {"name": name, "status": "failed", "error": "not JSON response"}
    except Exception as e:
        return {"name": name, "status": "failed", "error": str(e)}


@app.get("/")
def root():
    return {"status": "ok", "message": "Parking Fines API is running"}


@app.get("/municipalities")
def get_municipalities():
    return {"municipalities": [m["name"] for m in MUNICIPALITIES], "total": len(MUNICIPALITIES)}


@app.post("/check")
def check_all(req: CheckRequest):
    if not req.id_number.strip() or not req.car_number.strip():
        raise HTTPException(status_code=400, detail="id_number and car_number are required")

    results = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(
                check_municipality,
                m["name"], m["rashut"], m["report_type"],
                req.id_number.strip(), req.car_number.strip(),
                m.get("qcode")
            ): m for m in MUNICIPALITIES
        }
        for future in futures:
            try:
                result = future.result(timeout=60)
                results.append(result)
            except Exception as e:
                m = futures[future]
                results.append({"name": m["name"], "status": "failed", "error": str(e)})

    summary = {
        "clean": sum(1 for r in results if r["status"] == "clean"),
        "fine": sum(1 for r in results if r["status"] == "fine"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
    }

    return {"results": results, "summary": summary}
