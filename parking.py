import requests
import json
import time
import re
from bs4 import BeautifulSoup

# --- הגדרות ---
ID_NUMBER = "207089616"
CAR_NUMBER = "6185313"

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


def check_municipality(session, name, rashut, report_type, id_number, car_number, qcode=None):
    """בודק דוחות בעירייה אחת - בלי Selenium, רק requests"""
    base = "https://www.doh.co.il"
    
    try:
        # שלב 1: טען דף עירייה (cookies)
        if qcode:
            page_url = f"{base}/Default.aspx?a={qcode}"
        else:
            page_url = f"{base}/Default.aspx?ReportType={report_type}&Rashut={rashut}"
        session.get(page_url, headers=HEADERS, timeout=15)
        
        # שלב 2: אתחל session
        if qcode:
            param_data = {"action": "getData", "a": qcode}
        else:
            param_data = {
                "action": "getData",
                "ReportType": report_type,
                "Rashut": rashut,
                "language": "",
                "SwShow": "",
                "TK": ""
            }
        r_param = session.post(f"{base}/Menu/setParam.aspx", data=param_data, headers={
            **HEADERS,
            "Referer": page_url,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded"
        }, timeout=15)
        
        # שמור את ה-Rashut האמיתי מהתגובה (לפעמים שונה מהקלט)
        try:
            param_resp = r_param.json()
            actual_rashut = str(param_resp.get("Rashut", rashut))
            sw_qr = str(param_resp.get("SwQR", "0"))
            language = str(param_resp.get("language", "he"))
        except:
            actual_rashut = rashut
            sw_qr = "1" if qcode else "0"
            language = "he"
        
        # שלב 3: טען step1.aspx
        session.get(f"{base}/step1.aspx", headers={
            **HEADERS,
            "Referer": page_url
        }, timeout=15)
        
        # שלב 4: שלח בקשת חיפוש
        r = session.post(f"{base}/Check_Report.aspx", data={
            "status": "Check_Report",
            "StrFind": car_number,
            "ReportNo": id_number,
            "ReportType": report_type,
            "tokenCaptcha": "",
            "SwShow": "",
            "SwOrder": "1"
        }, headers={
            **HEADERS,
            "Referer": f"{base}/step1.aspx",
            "Origin": base,
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }, timeout=45)
        
        if r.status_code != 200:
            return {"status": "failed", "error": f"HTTP {r.status_code}"}
        
        data = r.json()
        count = data.get("C", 0)
        itra_sum = data.get("ItraSum", "")
        
        # אם אין דוחות כלל
        if count == 0:
            return {"status": "clean"}
        
        # דוח קיים עם סכום ב-API
        if itra_sum:
            return {
                "status": "fine",
                "count": count,
                "amount": itra_sum,
                "name": data.get("Nm", ""),
                "raw": data
            }
        
        # C > 0 אבל ItraSum ריק - צריך לשלוף מ-step2.aspx (קורה בבית שמש ודומות)
        return _get_fines_from_step2(
            session, base, car_number, id_number, report_type,
            count, actual_rashut, sw_qr, language
        )
    
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        return {"status": "failed", "error": "timeout/connection error"}
    except requests.exceptions.JSONDecodeError:
        return {"status": "failed", "error": "not JSON response"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def _get_fines_from_step2(session, base, car_number, id_number, report_type, doch_c, rashut, sw_qr, language):
    """שלב נוסף: שולף פרטי דוחות מדף step2.aspx (HTML) כאשר Check_Report מחזיר C>0 אבל בלי ItraSum"""
    try:
        step2_url = (
            f"{base}/step2.aspx?StrFind={car_number}&ReportNo={id_number}"
            f"&status=GetDetails&ReportType={report_type}&DochC={doch_c}"
            f"&SwQR={sw_qr}&language={language}&Rashut={rashut}&SwOrder=1"
        )
        r = session.get(step2_url, headers={
            **HEADERS,
            "Referer": f"{base}/step1.aspx"
        }, timeout=45)
        
        if r.status_code != 200:
            return {"status": "failed", "error": f"step2 HTTP {r.status_code}"}
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        # חלץ שורות דוחות מהטבלה
        fines = []
        total = 0.0
        for row in soup.select("tr.tableDiv.data, tr[class*='tableDiv'][class*='data']"):
            fine = {}
            cells = row.find_all("td")
            
            # מספר דוח
            label = row.find("label")
            if label:
                fine["number"] = label.get_text(strip=True)
            
            # סכום מ-checkbox data-price
            checkbox = row.find("input", {"type": "checkbox"})
            if checkbox and checkbox.get("data-price"):
                try:
                    price = float(checkbox["data-price"])
                    fine["amount"] = price
                    total += price
                except ValueError:
                    pass
            
            # סכום מטקסט ₪
            price_el = row.find(class_="price")
            if price_el:
                price_text = price_el.get_text(strip=True)
                fine["price_display"] = price_text
            
            # תאריך ושעה מ-cells
            cell_divs = row.find_all("div", class_="cell")
            for div in cell_divs:
                text = div.get_text(strip=True)
                # זיהוי תאריך (DD/MM/YYYY)
                if re.match(r"\d{2}/\d{2}/\d{4}", text):
                    fine["date"] = text
                # זיהוי שעה (HH:MM)
                elif re.match(r"\d{2}:\d{2}$", text):
                    fine["time"] = text
            
            if fine:
                fines.append(fine)
        
        if fines:
            return {
                "status": "fine",
                "count": len(fines),
                "amount": f"{total:.2f}" if total > 0 else "ראה פרטים",
                "fines": fines,
            }
        
        # אם לא מצאנו שורות דוחות ב-HTML - גם ככה C>0 מעיד על דוח
        return {"status": "fine", "count": doch_c, "amount": "לא ידוע (C>0, step2 ריק)"}
    
    except Exception as e:
        # C > 0 מעיד על דוח גם אם step2 נכשל
        return {"status": "fine", "count": doch_c, "amount": f"לא ידוע (step2 שגיאה: {e})"}


def main():
    print(f"🔍 סורק דוחות חנייה")
    print(f"   ת.ז: {ID_NUMBER}")
    print(f"   רכב: {CAR_NUMBER}")
    print(f"{'='*60}")
    
    results = {"fine": [], "clean": [], "failed": []}
    
    for i, m in enumerate(MUNICIPALITIES):
        session = requests.Session()  # session חדש לכל עירייה
        
        result = check_municipality(
            session, m["name"], m["rashut"], m["report_type"],
            ID_NUMBER, CAR_NUMBER, qcode=m.get("qcode")
        )
        
        status = result["status"]
        icon = {"fine": "💰", "clean": "✅", "failed": "❌"}[status]
        
        extra = ""
        if status == "fine":
            extra = f" | {result['count']} דוחות | סכום: {result['amount']}"
        elif status == "failed":
            extra = f" | {result.get('error', '')}"
        
        print(f"  [{i+1:2d}/{len(MUNICIPALITIES)}] {icon} {m['name']}{extra}")
        results[status].append({"name": m["name"], **result})
        
        time.sleep(0.3)  # המתנה קצרה בין בקשות
    
    # סיכום
    print(f"\n{'='*60}")
    print(f"📊 סיכום:")
    print(f"   ✅ נקי: {len(results['clean'])}")
    print(f"   💰 דוחות: {len(results['fine'])}")
    print(f"   ❌ נכשל: {len(results['failed'])}")
    
    if results["fine"]:
        print(f"\n⚠️  דוחות שנמצאו:")
        for f in results["fine"]:
            print(f"   • {f['name']}: {f['count']} דוחות, סכום: {f['amount']}")
            # הצג פרטי דוחות בודדים אם יש
            if "fines" in f:
                for fine in f["fines"]:
                    num = fine.get("number", "?")
                    date = fine.get("date", "?")
                    amt = fine.get("amount", "?")
                    price = fine.get("price_display", "")
                    print(f"     - דוח {num} | {date} | {price or f'{amt}₪'}")
    
    if results["failed"]:
        print(f"\n⚠️  עיריות שנכשלו:")
        for f in results["failed"]:
            print(f"   • {f['name']}: {f.get('error', '')}")


if __name__ == "__main__":
    main()
