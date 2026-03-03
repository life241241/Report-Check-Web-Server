from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import requests
import re
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from bs4 import BeautifulSoup
import time

from scan_logger_supabase import log_scan, get_logs, get_log_by_id, get_stats, save_subscriber, update_scan_subscriber, update_scan_vehicle

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
    # {"name": "רשות שדות התעופה", "rashut": "920070", "report_type": "1"},
    {"name": "מ.מ אורנית", "rashut": "920043", "report_type": "1"},
]


MUNI_COLORS = [
    "#6366f1", "#8b5cf6", "#a855f7", "#d946ef", "#ec4899",
    "#f43f5e", "#ef4444", "#f97316", "#f59e0b", "#eab308",
    "#84cc16", "#22c55e", "#10b981", "#14b8a6", "#06b6d4",
    "#0ea5e9", "#3b82f6", "#2563eb", "#4f46e5", "#7c3aed",
    "#9333ea", "#c026d3",
]

stream_executor = ThreadPoolExecutor(max_workers=5)

# Toggle: show total open fines count per municipality
SHOW_TOTAL_OPEN_FINES = True


class CheckRequest(BaseModel):
    id_number: str
    car_number: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class SubscribeRequest(BaseModel):
    email: str
    first_name: Optional[str] = ""
    last_name: Optional[str] = ""
    scan_id: Optional[int] = None


def _get_fine_images(session, base, car_number, report_type, language, sw_show, rashut, report_c, sw_hide_pic_parking, sw_hide_pic_general):
    """Call step2_show.aspx for a single fine to get image URLs."""
    try:
        # Check if images should be hidden for this report type
        if report_type in ("1",) and str(sw_hide_pic_parking) == "1":
            return []
        if report_type not in ("1",) and str(sw_hide_pic_general) == "1":
            return []

        import base64
        str_find_encoded = "1" + base64.b64encode(car_number.encode()).decode() + "2"

        r = session.post(f"{base}/step2_show.aspx", data={
            "status": "view",
            "ReportC": report_c,
            "StrFind": str_find_encoded,
            "ReportType": report_type,
            "language": language,
            "SwShow": sw_show or "",
        }, headers={
            **HEADERS,
            "Referer": f"{base}/step2.aspx",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        }, timeout=15)

        if r.status_code != 200:
            return []

        res = r.json()
        pic_found = int(res.get("PicFound", 0))
        report_kod = res.get("ReportKod", "")
        d_date = res.get("DDate", "")

        if pic_found <= 0 or not report_kod:
            return []

        image_urls = []
        for i in range(1, pic_found + 1):
            url = (
                f"https://ws.comax.co.il/Hanita/Parking/Image.aspx?"
                f"SwHanita=1&CustomerCode={rashut}"
                f"&ReportNo={report_kod}&ReportC={report_c}"
                f"&ReportD={d_date}&ImageNumber={i}"
            )
            image_urls.append(url)
        return image_urls
    except Exception:
        return []


def _get_fines_from_step2(session, base, car_number, id_number, report_type, doch_c, rashut, sw_qr, language, param_resp=None):
    try:
        step2_url = (
            f"{base}/step2.aspx?StrFind={car_number}&ReportNo={id_number}"
            f"&status=GetDetails&ReportType={report_type}&DochC={doch_c}"
            f"&SwQR=0&language={language}&Rashut={rashut}&SwOrder=2"
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
                # Extract ReportC from checkbox name attribute
                if checkbox.get("name"):
                    fine["_report_c"] = checkbox["name"]
            price_el = row.find(class_="price")
            if price_el:
                fine["price_display"] = price_el.get_text(strip=True)

            # Extract ReportC from the view link (data-class attribute)
            view_link = row.find("a", attrs={"data-class": True})
            if view_link:
                fine["_report_c"] = view_link["data-class"]

            # Parse all cell divs in order matching column layout:
            # [checkbox, number, date, time, location, amount, comments, view]
            cell_divs = row.find_all("div", class_="cell")
            for div in cell_divs:
                text = div.get_text(strip=True)
                classes = div.get("class", [])
                if re.match(r"\d{2}/\d{2}/\d{4}", text):
                    fine["date"] = text
                elif re.match(r"\d{2}:\d{2}$", text):
                    fine["time"] = text
                elif div.get("id") == "Street" or ("w4" in classes and "nomobile" in classes and "location" not in fine and "price" not in classes):
                    # Location column (w4 nomobile, first occurrence)
                    if text and "location" not in fine and not div.find(class_="price"):
                        fine["location"] = text
                elif "w4" in classes and "nomobile" in classes and "location" in fine and "comments" not in fine:
                    # Comments column (w4 nomobile, second occurrence after location)
                    if text:
                        fine["comments"] = text
            if fine:
                fines.append(fine)

        # Fetch images for each fine that has a ReportC
        if fines and param_resp:
            sw_hide_pic_parking = param_resp.get("SwHidePicParking", "0")
            sw_hide_pic_general = param_resp.get("SwHidePicGeneral", "0")
            sw_show = param_resp.get("SwShow", "")

            for fine in fines:
                report_c = fine.pop("_report_c", None)
                if report_c:
                    image_urls = _get_fine_images(
                        session, base, car_number, report_type, language,
                        sw_show, rashut, report_c, sw_hide_pic_parking, sw_hide_pic_general
                    )
                    if image_urls:
                        fine["image_urls"] = image_urls
        else:
            # Remove internal _report_c keys even if we couldn't fetch images
            for fine in fines:
                fine.pop("_report_c", None)

        if fines:
            return {"status": "fine", "count": len(fines), "amount": f"{total:.2f}" if total > 0 else "ראה פרטים", "fines": fines}
        # step2 returned no data rows — the C value was system-wide, not personal
        return {"status": "clean"}
    except Exception as e:
        return {"status": "fine", "count": doch_c, "amount": f"לא ידוע (step2 שגיאה: {e})"}


def _build_payment_url(rashut, report_type, qcode=None):
    base = "https://www.doh.co.il"
    if qcode:
        return f"{base}/Default.aspx?a={qcode}"
    return f"{base}/Default.aspx?ReportType={report_type}&Rashut={rashut}"


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

        param_resp = None
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
            "ReportType": report_type, "tokenCaptcha": "", "SwShow": "", "SwOrder": "2"
        }, headers={
            **HEADERS, "Referer": f"{base}/step1.aspx", "Origin": base,
            "Content-Type": "application/x-www-form-urlencoded", "X-Requested-With": "XMLHttpRequest",
        }, timeout=45)

        if r.status_code != 200:
            return {"name": name, "status": "failed", "error": f"HTTP {r.status_code}"}

        data = r.json()
        count = data.get("C", 0)
        itra_sum = data.get("ItraSum", "")

        # total_open_fines = system-wide open fines count for this municipality
        # Only available for qcode municipalities (e.g. Beit Shemesh) where
        # C is the system-wide total. For non-qcode municipalities, C is the
        # personal count, so we don't expose it as total_open_fines.
        total_open = count if (SHOW_TOTAL_OPEN_FINES and qcode) else None

        payment_url = _build_payment_url(rashut, report_type, qcode)

        if count == 0:
            result = {"name": name, "status": "clean"}
            if total_open is not None:
                result["total_open_fines"] = total_open
            return result

        if itra_sum:
            # Even when ItraSum is available, fetch step2 for detailed per-fine breakdown
            # (location, comments, individual amounts)
            step2_result = _get_fines_from_step2(session, base, car_number, id_number, report_type, count, actual_rashut, sw_qr, language, param_resp)
            if step2_result.get("status") == "fine" and step2_result.get("fines"):
                result = {"name": name, "status": "fine", "count": step2_result["count"],
                          "amount": itra_sum, "person_name": data.get("Nm", ""),
                          "fines": step2_result["fines"], "payment_url": payment_url}
            else:
                result = {"name": name, "status": "fine", "count": count, "amount": itra_sum,
                          "person_name": data.get("Nm", ""), "payment_url": payment_url}
            if total_open is not None:
                result["total_open_fines"] = total_open
            return result

        # Some municipalities (e.g. Beit Shemesh) return a system-wide C
        # with empty personal fields. We must always check step2 to know
        # if there are real fines — step2 returns actual rows only for
        # fines that belong to this person.
        result = _get_fines_from_step2(session, base, car_number, id_number, report_type, count, actual_rashut, sw_qr, language, param_resp)
        result["name"] = name
        if result.get("status") == "fine":
            result["payment_url"] = payment_url
        if total_open is not None:
            result["total_open_fines"] = total_open
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
    result = []
    for i, m in enumerate(MUNICIPALITIES):
        name = m["name"]
        short = (name
                 .replace("עיריית ", "")
                 .replace("מועצה מקומית ", "")
                 .replace("מועצה אזורית ", "")
                 .replace("מ.א ", "").replace("מ.א. ", "")
                 .replace("מ.מ ", "").replace("מ.מ. ", "")
                 .replace("רשות ", ""))[:2]
        result.append({
            "name": name,
            "id": m["rashut"],
            "initials": short,
            "color": MUNI_COLORS[i % len(MUNI_COLORS)],
        })
    return {"municipalities": result, "total": len(result)}


from fastapi.responses import Response

@app.get("/fine-image")
def proxy_fine_image(url: str = Query(..., description="Full image URL from ws.comax.co.il")):
    """Proxy fine images to avoid CORS issues in the browser."""
    if not url.startswith("https://ws.comax.co.il/"):
        raise HTTPException(status_code=400, detail="Invalid image URL")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail="Image not found")
        content_type = r.headers.get("Content-Type", "image/jpeg")
        return Response(content=r.content, media_type=content_type, headers={
            "Cache-Control": "public, max-age=86400",
        })
    except requests.exceptions.RequestException:
        raise HTTPException(status_code=502, detail="Failed to fetch image")


@app.post("/check-stream")
async def check_stream(req: CheckRequest, request: Request):
    if not req.id_number.strip() or not req.car_number.strip():
        raise HTTPException(status_code=400, detail="id_number and car_number are required")

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")

    async def event_generator():
        yield f"data: {json.dumps({'type': 'start', 'total': len(MUNICIPALITIES)}, ensure_ascii=False)}\n\n"

        loop = asyncio.get_event_loop()
        results = []

        async def check_one(m):
            try:
                return await loop.run_in_executor(
                    stream_executor,
                    check_municipality,
                    m["name"], m["rashut"], m["report_type"],
                    req.id_number.strip(), req.car_number.strip(),
                    m.get("qcode")
                )
            except Exception as e:
                return {"name": m["name"], "status": "failed", "error": str(e)}

        tasks = [asyncio.create_task(check_one(m)) for m in MUNICIPALITIES]

        for coro in asyncio.as_completed(tasks):
            result = await coro
            results.append(result)
            yield f"data: {json.dumps({'type': 'result', 'result': result}, ensure_ascii=False)}\n\n"

        summary = {
            "clean": sum(1 for r in results if r["status"] == "clean"),
            "fine": sum(1 for r in results if r["status"] == "fine"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
        }

        # Log the completed scan and get the scan ID
        scan_id = None
        try:
            scan_id = log_scan(
                client_ip, req.id_number.strip(), req.car_number.strip(),
                results, summary,
                user_agent=user_agent,
                latitude=req.latitude,
                longitude=req.longitude,
            )
        except Exception:
            pass  # never break the response over logging

        yield f"data: {json.dumps({'type': 'done', 'summary': summary, 'scan_id': scan_id}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/check")
def check_all(req: CheckRequest, request: Request):
    if not req.id_number.strip() or not req.car_number.strip():
        raise HTTPException(status_code=400, detail="id_number and car_number are required")

    client_ip = request.client.host if request.client else ""
    user_agent = request.headers.get("user-agent", "")

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

    # Log the completed scan
    try:
        log_scan(
            client_ip, req.id_number.strip(), req.car_number.strip(),
            results, summary,
            user_agent=user_agent,
            latitude=req.latitude,
            longitude=req.longitude,
        )
    except Exception:
        pass  # never break the response over logging

    return {"results": results, "summary": summary}


# ─── Scan Logs Endpoints ───────────────────────────────────

@app.get("/scan-logs")
def scan_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Return recent scan log entries (newest first)."""
    logs = get_logs(limit=limit, offset=offset)
    # Strip raw_results from list view for brevity
    for log in logs:
        meta = log.get("check_metadata") or {}
        meta.pop("raw_results", None)
    return {"logs": logs, "count": len(logs)}


@app.get("/scan-logs/{log_id}")
def scan_log_detail(log_id: int):
    """Return a single scan log with full structured data."""
    entry = get_log_by_id(log_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Log not found")
    # Add a clear subscribed flag
    user_info = entry.get("user_info") or {}
    entry["subscribed"] = bool(user_info.get("email"))
    return entry


@app.get("/scan-stats")
def scan_stats():
    """Return aggregate scan statistics."""
    return get_stats()


class VehicleUpdateRequest(BaseModel):
    manufacturer: Optional[str] = ""
    model: Optional[str] = ""


@app.patch("/scan-logs/{scan_id}/vehicle")
def update_vehicle(scan_id: int, req: VehicleUpdateRequest):
    """Attach vehicle manufacturer & model to a scan log row."""
    try:
        update_scan_vehicle(scan_id, req.manufacturer or "", req.model or "")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=500, detail="שגיאה בעדכון פרטי רכב")


@app.post("/subscribe")
def subscribe(req: SubscribeRequest):
    """Subscribe a user to email updates and link to scan log."""
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="כתובת מייל לא תקינה")
    try:
        # 1. Save to subscribers table (unique emails)
        try:
            save_subscriber(email, req.first_name or "", req.last_name or "")
        except Exception as e:
            error_msg = str(e)
            if not ("duplicate" in error_msg.lower() or "unique" in error_msg.lower() or "23505" in error_msg):
                raise

        # 2. Update the scan_log row with subscriber info
        if req.scan_id:
            update_scan_subscriber(
                req.scan_id, email,
                req.first_name or "",
                req.last_name or "",
            )

        return {"status": "ok", "message": "נרשמת בהצלחה!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="שגיאה בשמירת הנתונים")
