import streamlit as st
import pandas as pd
import asyncio
import re
import sys
import time
import subprocess
import io
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

# --- 1. SYSTEM SETUP ---
def install_playwright_browsers():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
    except Exception: pass

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# --- 2. ENHANCED DATE PARSER ---
def parse_linkedin_date(rel_text):
    if not rel_text: return "N/A"
    try:
        clean_text = rel_text.lower()
        match = re.search(r'(\d+)\s*([dwhmy])', clean_text)
        if not match: return "Recently"
        
        count = int(match.group(1))
        unit = match.group(2)
        today = datetime.now()
        
        if unit == 'd' or unit == 'h': delta = timedelta(days=count)
        elif unit == 'w': delta = timedelta(weeks=count)
        elif unit == 'm': delta = timedelta(days=count*30)
        elif unit == 'y': delta = timedelta(days=count*365)
        else: return "Recently"
        
        return (today - delta).strftime('%d-%b-%Y')
    except: return "Manual Check"

# --- 3. AUDIT LOGIC WITH URL & FAILURE ANALYSIS ---
async def audit_row(browser, semaphore, row, c_col, l_col, name_col, index):
    async with semaphore:
        res = row.to_dict()
        student_name = str(row.get(name_col, '')).strip()
        c_url = str(row.get(c_col, '')).strip()
        l_url = str(row.get(l_col, '')).strip()

        res.update({
            "Coursera_Status": "Invalid", 
            "LinkedIn_Status": "Invalid", 
            "Row_Final_Status": "UNVERIFIED",
            "Failure_Reason": "",
            "Cert_Date": "N/A",
            "LinkedIn_Relative_Date": "N/A" 
        })

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800}
        )
        page = await context.new_page()
        
        # --- COURSERA SCRAPER & URL CHECK ---
        if "/learn/" in c_url:
            res["Failure_Reason"] += "[Coursera: Submitted Course link, not Certificate link] "
            res["Coursera_Status"] = "Invalid Link Type"
        else:
            try:
                await page.goto(c_url, wait_until="load", timeout=45000)
                c_passed = False
                for _ in range(20): 
                    content = await page.content()
                    content_lower = content.lower()
                    first_name = student_name.split()[0].lower()
                    
                    if student_name.lower() in content_lower or first_name in content_lower:
                        res["Coursera_Status"] = "VALID"
                        c_passed = True
                        date_match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}', content)
                        res["Cert_Date"] = date_match.group() if date_match else "Found"
                        break
                    await asyncio.sleep(0.5)
                
                if not c_passed:
                    res["Failure_Reason"] += "[Coursera: Student Name not found on certificate page] "
            except:
                res["Coursera_Status"] = "Timeout"
                res["Failure_Reason"] += "[Coursera: Connection Timeout] "

        # --- LINKEDIN SCRAPER (IMPROVED DATE DETECTION) ---
        if "linkedin.com/posts" in l_url or "linkedin.com/feed/update" in l_url:
            try:
                await page.goto(l_url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5) 
                
                found_date = False
                selectors = ["span.update-components-actor__sub-description", "span.visually-hidden", ".update-components-text-view"]
                
                for sel in selectors:
                    elements = await page.query_selector_all(sel)
                    for el in elements:
                        txt = await el.inner_text()
                        if re.search(r'\b\d+[dwmyh]\b', txt) or " ago" in txt or " • " in txt:
                            res["LinkedIn_Relative_Date"] = parse_linkedin_date(txt)
                            found_date = True
                            break
                    if found_date: break
                
                res["LinkedIn_Status"] = "VERIFIED_POST"
                if not found_date:
                    res["Failure_Reason"] += "[LinkedIn: Date could not be scraped] "
            except:
                res["LinkedIn_Status"] = "Timeout"
                res["Failure_Reason"] += "[LinkedIn: Post Private or Timeout] "
        else:
            res["LinkedIn_Status"] = "Invalid Link"
            res["Failure_Reason"] += "[LinkedIn: Link is a Profile, not a Post] "

        # FINAL VERDICT
        if res["Coursera_Status"] == "VALID" and res["LinkedIn_Status"] == "VERIFIED_POST":
            res["Row_Final_Status"] = "VERIFIED"
            res["Failure_Reason"] = "N/A"
        
        await page.close()
        await context.close()
        return (index, res)

# --- 4. DASHBOARD UI ---
st.set_page_config(page_title="Mission Control Auditor", layout="wide")

st.title("🛡️ Predictive Analytics: Mission Control")
st.markdown("### Verification System with Deep URL Diagnostics")

file = st.file_uploader("📂 Upload Excel", type=["xlsx"])

if file:
    df = pd.read_excel(file).copy()
    total = len(df)
    
    colA, colB, colC = st.columns(3)
    colA.metric("Rows Found", total)
    colB.metric("Avg Speed", "2 s/row")
    colC.metric("Status", "Engine Ready")

    name_col = next((c for c in df.columns if "name" in c.lower()), None)
    roll_col = next((c for c in df.columns if "roll" in c.lower()), None)
    c_col = next((c for c in df.columns if "coursera" in c.lower()), None)
    l_col = next((c for c in df.columns if "linkedin" in c.lower()), None)

    if st.button("🏁 EXECUTE FULL SYSTEM SCAN"):
        install_playwright_browsers()
        
        p_label = st.empty()
        p_bar = st.progress(0)
        status_box = st.empty()
        timer_box = st.empty()
        start_time = time.time()

        async def run_process():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                semaphore = asyncio.Semaphore(10)
                tasks = [audit_row(browser, semaphore, row, c_col, l_col, name_col, i) for i, row in df.iterrows()]
                
                results = []
                for i, t in enumerate(asyncio.as_completed(tasks)):
                    results.append(await t)
                    
                    done = i + 1
                    pct = done / total
                    p_bar.progress(pct)
                    p_label.markdown(f"## ⚙️ Scanning: {done} / {total} ({int(pct*100)}%)")
                    
                    elapsed = time.time() - start_time
                    rem = (elapsed / done) * (total - done)
                    
                    status_box.info("🔍 **Audit Engine running...** Analyzing URL types and searching for student credentials. Sit tight!")
                    timer_box.markdown(f"⏱️ **Elapsed:** {int(elapsed)}s | ⌛ **Expected Completion in:** {int(rem)}s")
                
                await browser.close()
                results.sort(key=lambda x: x[0])
                return [r[1] for r in results]

        final_rows = asyncio.run(run_process())
        final_df = pd.DataFrame(final_rows)
        
        # Mark Logic
        if roll_col:
            v_counts = final_df[final_df['Row_Final_Status'] == 'VERIFIED'].groupby(roll_col).size()
            final_df['Verified_Projects'] = final_df[roll_col].map(lambda x: v_counts.get(x, 0))
            final_df['Marks'] = final_df['Verified_Projects'].apply(lambda x: min(x // 3, 8))

        st.success("✅ **Scan Complete.** Detailed failure logs and LinkedIn dates generated.")
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            final_df.to_excel(writer, index=False)
        
        st.download_button("📥 DOWNLOAD PRODUCTION REPORT", output.getvalue(), "Production_Audit_Detailed.xlsx")
        
        
# git add app.py
# git commit -m "Improved name matching logic"
# git push origin main