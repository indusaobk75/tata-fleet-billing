import io
import os
import json
import time
import re
import datetime
import calendar
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

# Safe optional library imports
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False

st.set_page_config(
    page_title="M/s INDU SAO | Enterprise Site ERP",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------- 0. MASTER CONFIGURATION -----------------
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "Admin@2026"
DEFAULT_ADMIN_NAME = "Indu Sao (Proprietor)"

COMPANY_HEADER = {
    "NAME": "M/s INDU SAO",
    "ADDRESS": "Office Address: Sondiha, Chainpur, Dist Ramgarh, (Jharkhand)",
    "PIN_CALL": "Pin No. 829134   Call 9234779896, 9661152652",
    "EMAIL": "Email: indusao_bk@yahoo.com",
    "GST": "GST No. 20AUOPK2982K1ZC",
    "VENDOR": "Vendor Code - 47",
    "ISO": "ISO Certified Company 45001:2018",
    "CURRENT_AC": "482025110000105"
}

# ----------------- 1. DATABASE & SECRETS CONFIGURATION -----------------
SHEET_NAME = st.secrets.get("test_spreadsheet_name", st.secrets.get("spreadsheet_name", "Tata_Steel_ERP_TEST_DB"))

USER_COLUMNS = [
    "Username", "Password", "Full_Name", "Role", "Allowed_Tabs", "Status",
    "Contact_No", "Emergency_Contact", "Photo_Base64"
]

WORKER_MASTER_COLS = [
    "Worker_ID", "Full_Name", "Govt_ID_Ref", "Division", "Designation", "Wage_Type", 
    "Base_Rate", "Contact_No", "Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details", 
    "Status", "Created_Date"
]

MUSTER_ROLL_COLS = [
    "Date", "Worker_ID", "Worker_Name", "Division", "Attendance", "OT_Hours", 
    "Duty_Location", "Advance_Amount", "Payment_Mode", "Supervisor", "Remarks"
]

ADVANCE_LOG_COLS = [
    "Txn_ID", "Date", "Worker_ID", "Worker_Name", "Division", "Amount", 
    "Payment_Mode", "Supervisor", "Remarks"
]

TATA_COMPLIANCE_COLS = [
    "Wages_Period", "Worker_ID", "Worker_Name", "Approved_Days", 
    "Net_Amount_Paid", "Uploaded_At"
]

FLEET_ROLES = [
    "Hyva Tipper Driver", "Excavator Operator (Heavy)", "JCB Backhoe Operator", 
    "Wheel Loader Operator", "Dozer Operator", "Hydra Crane Operator", 
    "Heavy Equipment Mechanic", "Auto Electrician", "Tyre Fitter / Service Mechanic", 
    "Fleet Maintenance Helper", "Fleet Supervisor", "Other (Custom Entry)"
]

CIVIL_ROLES = [
    "Mason (Rajmistri)", "Shuttering / Framework Carpenter", "Gypsum & False Ceiling Installer", 
    "Civil Painter (Putty / Coating)", "Steel & Bar Bender", "Welder & Structural Fabricator", 
    "Plumber & Pipe Fitter", "Rigger / Scaffolding Specialist", "Tile & Stone Layer", 
    "Civil Construction Helper", "Civil Site Supervisor", "Other (Custom Entry)"
]

@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if "gcp_json_raw" in st.secrets:
        creds_info = json.loads(st.secrets["gcp_json_raw"])
    elif "gcp_service_account" in st.secrets:
        creds_info = dict(st.secrets["gcp_service_account"])
    else:
        raise KeyError("Service account configuration missing from application secrets.")
    credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(credentials)

@st.cache_data(ttl=5, show_spinner=False)
def load_sheet_df(worksheet_name):
    schema_map = {
        "worker_master": WORKER_MASTER_COLS,
        "muster_roll": MUSTER_ROLL_COLS,
        "worker_advances": ADVANCE_LOG_COLS,
        "tata_form_xvii": TATA_COMPLIANCE_COLS,
        "app_users": USER_COLUMNS
    }
    expected_cols = schema_map.get(worksheet_name, [])

    for attempt in range(3):
        try:
            gc = get_gspread_client()
            sh = gc.open(SHEET_NAME)
            ws = sh.worksheet(worksheet_name)
            data = ws.get_all_records()
            df = pd.DataFrame(data)
            for col in expected_cols:
                if col not in df.columns:
                    df[col] = ""
            return df
        except Exception as e:
            if "429" in str(e):
                time.sleep(1.5 * (attempt + 1))
                continue
            return pd.DataFrame(columns=expected_cols)
    return pd.DataFrame(columns=expected_cols)

def save_sheet_df(worksheet_name, df):
    if df is None:
        return False
    df_to_save = df.copy()
    temp_drop = [c for c in df_to_save.columns if "parsed_dt" in str(c).lower() or str(c).startswith("_")]
    if temp_drop:
        df_to_save = df_to_save.drop(columns=temp_drop)

    prepared_values = [[str(c) for c in df_to_save.columns.tolist()]]
    for row in df_to_save.values.tolist():
        prepared_values.append([str(c).strip() if pd.notna(c) and c is not None else "" for c in row])

    for attempt in range(3):
        try:
            gc = get_gspread_client()
            sh = gc.open(SHEET_NAME)
            try:
                ws = sh.worksheet(worksheet_name)
            except gspread.exceptions.WorksheetNotFound:
                ws = sh.add_worksheet(title=worksheet_name, rows="100", cols="20")
            ws.clear()
            ws.update(prepared_values, value_input_option="RAW")
            st.cache_data.clear()
            return True
        except Exception as e:
            if "429" in str(e):
                time.sleep(1.5 * (attempt + 1))
                continue
            st.error(f"Error persisting to {worksheet_name}: {e}")
            return False
    return False

def generate_next_worker_id(division, existing_df):
    prefix = "FLT" if division == "Fleet Operations" else "CIV"
    if existing_df.empty or "Worker_ID" not in existing_df.columns:
        return f"{prefix}-1001"
    
    existing_ids = existing_df["Worker_ID"].astype(str).tolist()
    matched_nums = []
    for wid in existing_ids:
        if wid.startswith(prefix + "-"):
            try:
                num = int(wid.split("-")[1])
                matched_nums.append(num)
            except (IndexError, ValueError):
                continue
                
    if not matched_nums:
        return f"{prefix}-1001"
    return f"{prefix}-{max(matched_nums) + 1}"

def parse_tata_form_xvii_pdf(uploaded_pdf_file):
    if not PYPDF_AVAILABLE:
        return pd.DataFrame()

    reader = pypdf.PdfReader(uploaded_pdf_file)
    records = []
    header_keywords = [
        "FORM XVII", "REGISTER OF WAGES", "Name and address", "WAGES PERIOD", 
        "Certified that the amount", "Signatory of the Certifying", "Total:-",
        "CIVIL WORK", "West Bokaro", "INDU SAO", "Serial No.", "Designation"
    ]
    
    all_lines = []
    for page in reader.pages:
        txt = page.extract_text()
        if not txt:
            continue
        page_lines = [l.strip() for l in txt.split("\n") if l.strip()]
        for l in page_lines:
            if any(k in l for k in header_keywords) or re.search(r"^\d+(\s+\d+){5,}", l):
                continue
            all_lines.append(l)

    buffer = ""
    for line in all_lines:
        buffer = (buffer + " " + line).strip()
        if re.search(r"\d+\+\d+[\=\-]\d+", buffer):
            parts = buffer.split()
            if len(parts) >= 8 and parts[-1].isdigit():
                formula_idx = -1
                for p_i, p in enumerate(parts):
                    if re.search(r"\d+\+\d+[\=\-]\d+", p):
                        formula_idx = p_i
                        break
                
                if formula_idx > 2:
                    days_worked = float(parts[formula_idx - 1])
                    net_amount = float(parts[-1])
                    
                    sl = parts[0] if parts[0].isdigit() else ""
                    rest = parts[1:] if sl else parts
                    pre_days = rest[: formula_idx - (1 if sl else 0) - 1]
                    
                    serial = ""
                    s_idx = -1
                    for idx, t in enumerate(pre_days):
                        if t.isdigit() and len(t) <= 3:
                            serial = t
                            s_idx = idx
                            break
                            
                    if s_idx != -1:
                        name = " ".join(pre_days[:s_idx])
                    else:
                        name = " ".join(pre_days)
                        
                    clean_name = re.sub(r"^[0-9\.\s]+", "", name).strip()
                    if clean_name:
                        records.append({
                            "Worker_Name": clean_name.upper(),
                            "Serial_No": serial,
                            "Approved_Days": days_worked,
                            "Net_Amount_Paid": net_amount
                        })
                    buffer = ""
                    
    return pd.DataFrame(records)


# ----------------- 2. DIRECT PDF GENERATORS (LETTERHEAD + 8-UP ATM ID CARDS) -----------------
def generate_bank_authority_letter_pdf(bank_batch_type, records_list, cheque_no, cheque_date):
    """Generates a direct print-ready A4 PDF with the exact M/s INDU SAO letterhead."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # Exact Diagonal Header Banner
    pdf.set_fill_color(46, 125, 50)
    pdf.polygon([(0, 46), (210, 36), (210, 39), (0, 49)], style="F")
    pdf.set_fill_color(244, 180, 0)
    pdf.polygon([(0, 43), (210, 33), (210, 36), (0, 46)], style="F")
    pdf.set_fill_color(56, 163, 241)
    pdf.polygon([(0, 0), (210, 0), (210, 33), (0, 43)], style="F")

    # Left White Oval Emblem
    pdf.set_fill_color(255, 255, 255)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.8)
    pdf.ellipse(x=8, y=4, w=48, h=30, style="DF")

    pdf.set_fill_color(13, 27, 110)
    pdf.polygon([(21, 25), (21, 17), (25, 14), (25, 25)], style="F")
    pdf.polygon([(26, 25), (26, 13), (31, 10), (31, 25)], style="F")
    pdf.polygon([(32, 25), (32, 9), (37, 6), (37, 25)], style="F")
    pdf.set_line_width(0.4)
    pdf.line(39, 7, 39, 25)
    pdf.line(41, 9, 41, 25)
    pdf.line(43, 11, 43, 25)
    pdf.set_fill_color(13, 27, 110)
    pdf.ellipse(x=20, y=26.5, w=16, h=4, style="F")
    pdf.set_fill_color(0, 0, 0)
    pdf.ellipse(x=29, y=28.5, w=16, h=3.5, style="F")

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(7, 35.5)
    pdf.cell(52, 4, "ISO Certified Company 45001:2018", align="L")

    pdf.set_xy(64, 4.5)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(138, 8, "M/s INDU SAO", ln=True)

    pdf.set_font("Helvetica", "B", 8.5)
    info_lines = [
        "Office Address: Sondiha, Chainpur, Dist Ramgarh, (Jharkhand)",
        "Pin No. 829134   Call 9234779896, 9661152652",
        "Email: indusao_bk@yahoo.com",
        "GST No. 20AUOPK2982K1ZC",
        "Vendor Code - 47"
    ]
    for line in info_lines:
        pdf.set_x(64)
        pdf.cell(138, 4.2, line, ln=True)

    # Comfortable 20mm Left & Right Margins
    pdf.set_y(55)
    pdf.set_left_margin(20)
    pdf.set_right_margin(20)

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(110, 5, "To,", ln=False)
    pdf.cell(60, 5, f"Date: {cheque_date}", align="R", ln=True)
    pdf.cell(170, 5, "The Branch Manager,", ln=True)
    pdf.cell(170, 5, "Bank of India", ln=True)

    pdf.ln(3)
    pdf.set_font("Helvetica", "BU", 10)
    pdf.cell(170, 5.5, "BANK DETAILS", align="C", ln=True)
    pdf.cell(170, 5.5, "SUB: Transfer of payment from our account to below A/C", align="C", ln=True)

    pdf.ln(4)
    total_amount = sum([float(r.get("AMOUNT", 0.0) or 0.0) for r in records_list])
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(170, 5, "Dear Sir,", ln=True)
    pdf.set_font("Helvetica", "", 9.5)
    body_txt = (
        f"We request you to transfer Rs. {total_amount:.0f}/- from our Current A/C No. "
        f"{COMPANY_HEADER['CURRENT_AC']} to following account as per the payment details given "
        f"ch.no. {cheque_no} Dated: {cheque_date}"
    )
    pdf.multi_cell(170, 5, body_txt)
    pdf.ln(4)

    col_widths = [12, 46, 22, 36, 24, 30]
    headers = ["SL.NO", "NAME", "AMOUNT", "ACCOUNT NO", "IFSC CODE", "BANK & BRANCH"]

    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.3)
    pdf.set_font("Helvetica", "B", 8)

    for w, h in zip(col_widths, headers):
        pdf.cell(w, 7, h, border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    for idx, r in enumerate(records_list, start=1):
        amt_fmt = f"{float(r.get('AMOUNT', 0.0) or 0.0):.0f}"
        pdf.cell(col_widths[0], 6.5, str(idx), border=1, align="C")
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(col_widths[1], 6.5, str(r.get("NAME", ""))[:26], border=1, align="L")
        pdf.cell(col_widths[2], 6.5, amt_fmt, border=1, align="R")
        pdf.set_font("Courier", "B", 8)
        pdf.cell(col_widths[3], 6.5, str(r.get("BENEFICIARY ACCOUNT NO", "")), border=1, align="C")
        pdf.cell(col_widths[4], 6.5, str(r.get("IFSC CODE", "")), border=1, align="C")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.cell(col_widths[5], 6.5, str(r.get("BANK & BRANCH DETAILS", ""))[:18], border=1, align="L")
        pdf.ln()

    pdf.set_fill_color(248, 250, 252)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(col_widths[0] + col_widths[1], 7, "TOTAL:", border=1, align="R", fill=True)
    pdf.cell(col_widths[2], 7, f"{total_amount:.0f}", border=1, align="R", fill=True)
    pdf.cell(col_widths[3] + col_widths[4] + col_widths[5], 7, "", border=1, fill=True)
    pdf.ln(8)

    # Left: M/s INDU SAO Proprietor | Right: Bank Sign & Stamp
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(170, 5, "THANKING YOU", ln=True)
    pdf.ln(12)

    y_sign = pdf.get_y()
    pdf.set_xy(20, y_sign)
    pdf.cell(65, 5, "M/s INDU SAO", align="C", ln=True)
    pdf.set_x(20)
    pdf.cell(65, 5, "Proprietor", align="C")

    pdf.set_xy(125, y_sign + 5)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(65, 5, "Bank Sign & Stamp", align="C")

    # Bottom Yellow + Sky Blue Strip
    pdf.set_fill_color(244, 180, 0)
    pdf.rect(0, 288, 210, 2.5, style="F")
    pdf.set_fill_color(56, 163, 241)
    pdf.rect(0, 290.5, 210, 6.5, style="F")

    return bytes(pdf.output())

def generate_atm_id_cards_pdf(workers_df):
    """Generates 8 ATM-sized (85.6mm x 54mm) Employee ID Cards per A4 sheet (2 cols x 4 rows)."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    card_w = 85.6
    card_h = 54.0
    margin_x = 14.0
    margin_y = 14.0
    gap_x = 10.8
    gap_y = 16.0

    records = workers_df.to_dict(orient="records")
    for idx, emp in enumerate(records):
        slot = idx % 8
        if slot == 0:
            pdf.add_page()

        col = slot % 2
        row = slot // 2
        x0 = margin_x + col * (card_w + gap_x)
        y0 = margin_y + row * (card_h + gap_y)

        # Outer ATM Card Border
        pdf.set_draw_color(100, 116, 139)
        pdf.set_line_width(0.35)
        pdf.rect(x0, y0, card_w, card_h, style="D")

        # Card Header Banner (Sky Blue + Yellow Accent)
        pdf.set_fill_color(56, 163, 241)
        pdf.rect(x0, y0, card_w, 12.5, style="F")
        pdf.set_fill_color(244, 180, 0)
        pdf.rect(x0, y0 + 12.5, card_w, 1.3, style="F")

        # Mini Oval Logo
        pdf.set_fill_color(255, 255, 255)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_line_width(0.3)
        pdf.ellipse(x0 + 2.5, y0 + 2.0, 12.0, 8.5, style="DF")
        pdf.set_fill_color(13, 27, 110)
        pdf.rect(x0 + 6.5, y0 + 4.2, 1.5, 4.2, style="F")
        pdf.rect(x0 + 8.5, y0 + 3.2, 1.5, 5.2, style="F")

        # Company Header Text
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_xy(x0 + 16.5, y0 + 1.8)
        pdf.cell(66, 4.5, "M/s INDU SAO", ln=True)
        pdf.set_font("Helvetica", "B", 6.2)
        pdf.set_x(x0 + 16.5)
        pdf.cell(66, 3.5, "TATA STEEL WEST BOKARO | VENDOR CODE: ID47")

        # Worker ID Badge Pill
        wid = str(emp.get("Worker_ID", ""))
        pdf.set_fill_color(15, 23, 42)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Courier", "B", 8.5)
        pdf.set_xy(x0 + 57.5, y0 + 16.0)
        pdf.cell(25.0, 5.5, wid, align="C", fill=True)

        # Photo Placeholder Box on Left
        pdf.set_draw_color(148, 163, 184)
        pdf.set_fill_color(248, 250, 252)
        pdf.rect(x0 + 3.5, y0 + 16.0, 19.0, 23.0, style="DF")
        pdf.set_text_color(100, 116, 139)
        pdf.set_font("Helvetica", "", 6)
        pdf.set_xy(x0 + 3.5, y0 + 25.5)
        pdf.cell(19.0, 3.5, "PHOTO", align="C")

        # Employee Details
        wname = str(emp.get("Full_Name", "")).upper()[:24]
        wrole = str(emp.get("Designation", ""))[:26]
        wdiv = str(emp.get("Division", ""))
        wphone = str(emp.get("Contact_No", "")) or "N/A"
        wref = str(emp.get("Govt_ID_Ref", "")) or wid

        pdf.set_text_color(15, 23, 42)
        pdf.set_font("Helvetica", "B", 9.5)
        pdf.set_xy(x0 + 24.5, y0 + 22.2)
        pdf.cell(59, 4.5, wname)

        pdf.set_font("Helvetica", "B", 7.2)
        pdf.set_text_color(55, 48, 163)
        pdf.set_xy(x0 + 24.5, y0 + 27.0)
        pdf.cell(59, 3.8, wrole)

        pdf.set_text_color(51, 65, 85)
        pdf.set_font("Helvetica", "", 6.8)
        pdf.set_xy(x0 + 24.5, y0 + 31.2)
        pdf.cell(59, 3.5, f"Div: {wdiv}")
        pdf.set_xy(x0 + 24.5, y0 + 34.8)
        pdf.cell(59, 3.5, f"Ref: {wref}  |  Mob: {wphone}")

        # Card Bottom Strip + Proprietor Sign
        pdf.set_fill_color(241, 245, 249)
        pdf.rect(x0, y0 + 44.5, card_w, 9.5, style="F")
        pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "B", 6.2)
        pdf.set_xy(x0 + 3.5, y0 + 46.2)
        pdf.cell(48, 3.5, "ISO 45001:2018 Certified")
        pdf.set_xy(x0 + 3.5, y0 + 49.5)
        pdf.set_font("Helvetica", "", 5.8)
        pdf.cell(48, 3.2, "Sondiha, Chainpur, Ramgarh (JH)")

        pdf.set_font("Helvetica", "B", 6.5)
        pdf.set_xy(x0 + 53.0, y0 + 49.0)
        pdf.cell(30, 3.5, "Proprietor Sign", align="C")

    return bytes(pdf.output())


# ----------------- 3. HIERARCHICAL WORKSPACES & RBAC -----------------
TAB_REGISTRY = {
    "➕ Employee Enrolment": "Attendance",
    "📋 Employee Master Register": "Attendance",
    "Daily Muster Roll (Field Attendance)": "Attendance",
    "Advance & Expense Register": "Attendance",
    "Monthly Wages & Payout Summary": "Attendance",

    "Daily Logsheet Entry": "Billing",
    "Monthly Statement & Abstract": "Billing",
    "Locked RA Bills Repository": "Billing",
    "Delivery Order (DO) Contract Management": "Billing",
    "Vehicle Fleet Master Mapping": "Billing",
    "Commercial Rate Master": "Billing",

    "Daily Diesel Issue Slip": "Fuel",
    "Fleet Fuel Benchmark Master": "Fuel",
    "Theft & Pilferage Audit": "Fuel"
}

TAB_ALIASES = {
    "Worker Daily Muster Roll": "Daily Muster Roll (Field Attendance)",
    "Driver & Operator Pool": "📋 Employee Master Register",
    "Worker & Crew Directory": "📋 Employee Master Register",
    "Monthly Attendance Summary": "Monthly Wages & Payout Summary",
    "Advance & Khuraki Register": "Advance & Expense Register"
}

WORKSPACES_TREE = [
    {
        "id": "Attendance",
        "icon": "👷",
        "title": "Worker Attendance",
        "submenus": [
            ("Employee Enrolment", "➕ Employee Enrolment"),
            ("Master Register & ID Cards", "📋 Employee Master Register"),
            ("Daily Field Muster Roll", "Daily Muster Roll (Field Attendance)"),
            ("Advance & Expenses", "Advance & Expense Register"),
            ("Wages & Bank Letters", "Monthly Wages & Payout Summary")
        ]
    },
    {
        "id": "Billing",
        "icon": "📑",
        "title": "Tata Steel Billing",
        "submenus": [
            ("Daily Logsheet Entry", "Daily Logsheet Entry"),
            ("Monthly Abstract", "Monthly Statement & Abstract"),
            ("Locked RA Bills", "Locked RA Bills Repository"),
            ("DO Contracts", "Delivery Order (DO) Contract Management"),
            ("Fleet Vehicle Mapping", "Vehicle Fleet Master Mapping"),
            ("Commercial Rate Master", "Commercial Rate Master")
        ]
    },
    {
        "id": "Fuel",
        "icon": "⛽",
        "title": "Site Fuel Record",
        "submenus": [
            ("Daily Diesel Slip", "Daily Diesel Issue Slip"),
            ("Fuel Benchmark Master", "Fleet Fuel Benchmark Master"),
            ("Anti-Pilferage Audit", "Theft & Pilferage Audit")
        ]
    }
]

INDU_SAO_SVG_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 120" style="width: 36px; height: 26px;"><ellipse cx="80" cy="60" rx="76" ry="56" fill="#ffffff" stroke="#1e293b" stroke-width="5"/><polygon points="54,90 54,42 70,30 70,90" fill="#1e293b"/><polygon points="76,90 76,22 92,12 92,90" fill="#3730a3"/><rect x="98" y="24" width="4" height="66" fill="#64748b"/><rect x="105" y="32" width="4" height="58" fill="#64748b"/><path d="M 32,76 C 50,98 112,98 128,78 C 114,90 58,90 32,76 Z" fill="#1e293b"/><circle cx="80" cy="74" r="5" fill="#3730a3"/></svg>"""

# ----------------- SLEEK MODERN PILL SIDEBAR & HEADER STYLING -----------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
    }

    .stApp {
        background-color: #ffffff;
    }
    
    .block-container { 
        padding-top: 3.6rem !important; 
        padding-bottom: 2.5rem !important;
        padding-left: 2.2rem !important;
        padding-right: 2.2rem !important;
        max-width: 100% !important;
    }

    /* Sleek Modern Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #f8fafc !important;
        border-right: 1px solid #e2e8f0 !important;
        width: 275px !important;
        min-width: 275px !important;
    }
    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 1.1rem !important;
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0.15rem !important;
    }

    /* PARENT MENU BUTTONS (Bold, Prominent Category Headers) */
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"] {
        width: 100% !important;
        min-height: 38px !important;
        height: 38px !important;
        text-align: left !important;
        justify-content: flex-start !important;
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        border-radius: 8px !important;
        padding: 6px 10px !important;
        margin-top: 4px !important;
        font-size: 0.88rem !important;
        font-weight: 700 !important;
        color: #0f172a !important;
        letter-spacing: -0.01em !important;
    }
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"]:hover {
        background-color: #e2e8f0 !important;
    }

    /* SUB-MENU INACTIVE BUTTONS (Indented, Lighter, Tree-Line) */
    section[data-testid="stSidebar"] div.stButton > button[kind="tertiary"] {
        min-height: 33px !important;
        height: 33px !important;
        text-align: left !important;
        justify-content: flex-start !important;
        background-color: transparent !important;
        border: none !important;
        border-left: 2px solid #cbd5e1 !important;
        box-shadow: none !important;
        border-radius: 0px 8px 8px 0px !important;
        padding: 4px 12px 4px 16px !important;
        margin-left: 14px !important;
        width: calc(100% - 14px) !important;
        font-size: 0.82rem !important;
        font-weight: 400 !important;
        color: #475569 !important;
    }
    section[data-testid="stSidebar"] div.stButton > button[kind="tertiary"]:hover {
        background-color: #f1f5f9 !important;
        color: #0f172a !important;
        border-left: 2px solid #64748b !important;
    }

    /* SUB-MENU ACTIVE PILL (Bold Indigo Highlight) */
    section[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
        width: calc(100% - 14px) !important;
        min-height: 34px !important;
        height: 34px !important;
        text-align: left !important;
        justify-content: flex-start !important;
        background-color: #e0e7ff !important;
        color: #1e3a8a !important;
        border: none !important;
        border-left: 3px solid #4f46e5 !important;
        border-radius: 0px 16px 16px 0px !important;
        padding: 4px 12px 4px 15px !important;
        margin-left: 14px !important;
        font-size: 0.83rem !important;
        font-weight: 600 !important;
        box-shadow: none !important;
    }

    /* Main Content Primary Buttons */
    div.stMain div.stButton > button[kind="primary"],
    div.stFormSubmitButton > button[kind="primary"],
    div.stDownloadButton > button[kind="primary"] {
        background-color: #1e293b !important;
        color: #ffffff !important;
        border: 1px solid #1e293b !important;
        border-radius: 8px !important;
        font-weight: 500 !important;
    }
    div.stMain div.stButton > button[kind="primary"]:hover {
        background-color: #334155 !important;
    }

    /* Main Content Secondary Buttons */
    div.stMain div.stButton > button[kind="secondary"],
    div.stDownloadButton > button[kind="secondary"] {
        background-color: #ffffff !important;
        color: #334155 !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
        font-size: 0.8rem !important;
        font-weight: 500 !important;
        padding: 4px 10px !important;
        min-height: 34px !important;
        height: 34px !important;
    }
    div.stMain div.stButton > button[kind="secondary"]:hover {
        background-color: #f8fafc !important;
        border-color: #94a3b8 !important;
    }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
        border: 1px solid #e2e8f0 !important;
        background-color: #ffffff !important;
    }

    .top-user-avatar {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: #3730a3;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 600;
        font-size: 0.78rem;
        color: white;
        flex-shrink: 0;
    }
    .status-badge {
        display: inline-block;
        padding: 1px 6px;
        border-radius: 9999px;
        font-size: 0.60rem;
        font-weight: 600;
        text-transform: uppercase;
    }
    .status-admin { background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
    .status-user { background-color: #eef2ff; color: #3730a3; border: 1px solid #c7d2fe; }
</style>
""", unsafe_allow_html=True)


# ----------------- 4. AUTHENTICATION & SESSION ENGINE -----------------
def init_session():
    if "user_authenticated" not in st.session_state:
        st.session_state.user_authenticated = False
    if "current_user" not in st.session_state:
        st.session_state.current_user = None
    if "current_name" not in st.session_state:
        st.session_state.current_name = None
    if "current_role" not in st.session_state:
        st.session_state.current_role = None
    if "allowed_tabs" not in st.session_state:
        st.session_state.allowed_tabs = []
    if "active_workspace" not in st.session_state:
        st.session_state.active_workspace = "Attendance"
    if "active_subtab" not in st.session_state:
        st.session_state.active_subtab = "Daily Muster Roll (Field Attendance)"
    if "selected_worker_card_id" not in st.session_state:
        st.session_state.selected_worker_card_id = None

init_session()

def login_screen():
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="text-align: center; margin-bottom: 24px;">
                <div style="display: flex; justify-content: center; margin-bottom: 10px;">{INDU_SAO_SVG_LOGO}</div>
                <h2 style="margin: 0; padding: 0; color: #0f172a; font-weight: 700; font-size: 1.55rem; letter-spacing: -0.02em;">{COMPANY_HEADER['NAME']}</h2>
                <div style="font-size: 0.82rem; color: #64748b; font-weight: 500; margin-top: 4px;">{COMPANY_HEADER['GST']} | {COMPANY_HEADER['VENDOR']}</div>
                <div style="font-size: 0.72rem; color: #059669; font-weight: 600; margin-top: 2px;">{COMPANY_HEADER['ISO']}</div>
            </div>
            """, 
            unsafe_allow_html=True
        )

        with st.container(border=True):
            st.markdown("##### Sign in to Workspace")
            st.caption("Enter your authorized credentials to continue")
            
            username = st.text_input("Username", placeholder="e.g. admin").strip()
            password = st.text_input("Password", type="password", placeholder="••••••••").strip()
            st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
            submitted = st.button("Continue →", use_container_width=True, type="primary")

            if submitted:
                if not username or not password:
                    st.warning("Please enter both Username and Password.")
                    return

                u_lower = username.lower()
                configured_master_pass = st.secrets.get("app_password", DEFAULT_ADMIN_PASS)

                if u_lower == DEFAULT_ADMIN_USER.lower() and password == configured_master_pass:
                    st.session_state.user_authenticated = True
                    st.session_state.current_user = DEFAULT_ADMIN_USER
                    st.session_state.current_name = DEFAULT_ADMIN_NAME
                    st.session_state.current_role = "ADMIN"
                    st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                    st.session_state.active_workspace = "Attendance"
                    st.session_state.active_subtab = "Daily Muster Roll (Field Attendance)"
                    st.rerun()

                users_df = load_sheet_df("app_users")
                if users_df.empty or "Username" not in users_df.columns:
                    st.error("Authentication service unavailable.")
                    return

                users_df["_u_clean"] = users_df["Username"].astype(str).str.strip().str.lower()
                user_match = users_df[users_df["_u_clean"] == u_lower]

                if not user_match.empty:
                    u_row = user_match.iloc[0]
                    u_pass = str(u_row.get("Password", "")).strip()
                    u_status = str(u_row.get("Status", "ACTIVE")).strip().upper()
                    u_role = str(u_row.get("Role", "USER")).strip().upper()
                    u_name = str(u_row.get("Full_Name", username)).strip()

                    if u_status != "ACTIVE":
                        st.error("Account access is disabled.")
                        return

                    if password == u_pass:
                        st.session_state.user_authenticated = True
                        st.session_state.current_user = str(u_row.get("Username"))
                        st.session_state.current_name = u_name if u_name else username
                        st.session_state.current_role = u_role

                        if u_role == "ADMIN":
                            st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                        else:
                            raw_tabs = str(u_row.get("Allowed_Tabs", ""))
                            permitted = []
                            for t in raw_tabs.split(","):
                                t_clean = t.strip()
                                if t_clean in TAB_REGISTRY:
                                    permitted.append(t_clean)
                                elif t_clean in TAB_ALIASES:
                                    permitted.append(TAB_ALIASES[t_clean])
                            st.session_state.allowed_tabs = list(set(permitted))

                        if "Daily Muster Roll (Field Attendance)" in st.session_state.allowed_tabs:
                            st.session_state.active_workspace = "Attendance"
                            st.session_state.active_subtab = "Daily Muster Roll (Field Attendance)"
                        elif st.session_state.allowed_tabs:
                            first_tab = st.session_state.allowed_tabs[0]
                            st.session_state.active_workspace = TAB_REGISTRY.get(first_tab, "Attendance")
                            st.session_state.active_subtab = first_tab

                        st.rerun()
                    else:
                        st.error("Invalid credentials provided.")
                else:
                    st.error("User profile not found.")

if not st.session_state.user_authenticated:
    login_screen()
    st.stop()


# ----------------- 5. SLEEK SIDEBAR TREE NAVIGATION -----------------
with st.sidebar:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 10px; padding: 2px 4px 12px 4px; margin-bottom: 6px; border-bottom: 1px solid #e2e8f0;">
            <div>{INDU_SAO_SVG_LOGO}</div>
            <div>
                <div style="font-size: 0.94rem; font-weight: 700; color: #0f172a;">{COMPANY_HEADER['NAME']}</div>
                <div style="font-size: 0.68rem; color: #64748b; font-weight: 500;">West Bokaro • ID47</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    for ws in WORKSPACES_TREE:
        ws_id = ws["id"]
        ws_icon = ws["icon"]
        ws_title = ws["title"]
        
        permitted_submenus = []
        for disp_name, reg_key in ws["submenus"]:
            if st.session_state.current_role == "ADMIN" or reg_key in st.session_state.allowed_tabs:
                permitted_submenus.append((disp_name, reg_key))

        if not permitted_submenus and st.session_state.current_role != "ADMIN":
            continue

        is_open = (st.session_state.active_workspace == ws_id)
        chevron = "▾" if is_open else "▸"

        # MAIN MENU HEADER (Bold, Dark Slate)
        if st.button(f"{chevron}  {ws_icon}  {ws_title}", key=f"nav_ws_{ws_id}", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = ws_id
            st.session_state.selected_worker_card_id = None
            if permitted_submenus and st.session_state.active_subtab not in [s[1] for s in permitted_submenus]:
                st.session_state.active_subtab = permitted_submenus[0][1]
            st.rerun()

        # SUB-MENU ITEMS (Indented with Vertical Tree Line)
        if is_open:
            for disp_name, reg_key in permitted_submenus:
                is_sub_active = (st.session_state.active_subtab == reg_key)
                btn_kind = "primary" if is_sub_active else "tertiary"
                
                if st.button(disp_name, key=f"nav_sub_{reg_key}", use_container_width=True, type=btn_kind):
                    st.session_state.active_subtab = reg_key
                    st.session_state.selected_worker_card_id = None
                    st.rerun()

    # ADMIN SECTION
    if st.session_state.current_role == "ADMIN":
        st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)
        is_admin_open = (st.session_state.active_workspace == "Admin")
        adm_chevron = "▾" if is_admin_open else "▸"
        
        if st.button(f"{adm_chevron}  ⚙️  Access & Settings", key="nav_ws_admin_settings", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = "Admin"
            st.session_state.active_subtab = "User Accounts & RBAC"
            st.session_state.selected_worker_card_id = None
            st.rerun()

        if is_admin_open:
            is_adm_sub = (st.session_state.active_subtab == "User Accounts & RBAC")
            if st.button("User Permissions (RBAC)", key="nav_sub_admin_rbac_item", use_container_width=True, type="primary" if is_adm_sub else "tertiary"):
                st.session_state.active_subtab = "User Accounts & RBAC"
                st.session_state.selected_worker_card_id = None
                st.rerun()


# ----------------- 6. CLEAN TOP BAR -----------------
user_display_name = st.session_state.get("current_name") or st.session_state.get("current_user") or "User"
name_parts = str(user_display_name).strip().split()
initials = "".join([p[0] for p in name_parts[:2] if p]).upper() if name_parts else "US"
badge_class = "status-admin" if st.session_state.current_role == "ADMIN" else "status-user"

avatar_html = f'<div class="top-user-avatar">{initials}</div>'

h_col1, h_col2, h_col3 = st.columns([2.8, 1.5, 0.8], gap="small")
with h_col1:
    ws_map = {"Attendance": "Worker Attendance", "Billing": "Tata Steel Billing", "Fuel": "Site Fuel Record", "Admin": "System Administration"}
    cur_ws_label = ws_map.get(st.session_state.active_workspace, "Workspace")
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 8px; height: 34px;">
            <span style="font-size: 0.84rem; color: #64748b; font-weight: 500;">{cur_ws_label}</span>
            <span style="color: #cbd5e1;">/</span>
            <span style="font-size: 0.86rem; color: #0f172a; font-weight: 600;">{st.session_state.active_subtab}</span>
        </div>
        """, 
        unsafe_allow_html=True
    )

with h_col2:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px; height: 34px;">
            {avatar_html}
            <div style="text-align: left; line-height: 1.15;">
                <div style="font-weight: 600; font-size: 0.8rem; color: #0f172a; white-space: nowrap;">{user_display_name}</div>
                <div><span class="status-badge {badge_class}">{st.session_state.current_role}</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with h_col3:
    if st.button("Sign Out", help="End Session", use_container_width=True, key="hdr_pwr_out", type="secondary"):
        st.session_state.user_authenticated = False
        st.rerun()

st.markdown("<hr style='margin-top: 10px; margin-bottom: 18px; border: none; border-top: 1px solid #f1f5f9;' />", unsafe_allow_html=True)


# ----------------- 7. MAIN VIEW ROUTING -----------------
current_view = st.session_state.active_subtab

# ----------------- VIEW 1: EMPLOYEE ENROLMENT -----------------
if current_view == "➕ Employee Enrolment":
    st.markdown("#### Employee Enrolment")
    st.caption("Onboard workforce members with identity and bank account particulars")

    entry_mode = st.radio("Enrolment Method", ["Single Employee Form", "Bulk Excel/CSV Upload"], horizontal=True)

    if entry_mode == "Single Employee Form":
        c_form, c_info = st.columns([1.35, 0.65], gap="large")
        with c_form:
            with st.container(border=True):
                w_df_current = load_sheet_df("worker_master")
                
                w_div = st.selectbox("Operational Division", ["Fleet Operations", "Civil Construction"], key="enrol_div")
                next_wid = generate_next_worker_id(w_div, w_df_current)
                st.caption(f"Assigned System ID: **{next_wid}**")

                w_name = st.text_input("Full Official Name", key="enrol_name").strip()
                w_ref = st.text_input("Identity Verification Reference", key="enrol_ref", help="Distinguishes duplicate names").strip()

                role_choices = FLEET_ROLES if w_div == "Fleet Operations" else CIVIL_ROLES
                sel_role = st.selectbox("Designation / Role", role_choices, key="enrol_role")
                
                custom_role = ""
                if sel_role == "Other (Custom Entry)":
                    custom_role = st.text_input("Specify Custom Role", key="enrol_custom_role").strip()

                c_w1, c_w2 = st.columns(2)
                with c_w1:
                    w_wage_type = st.selectbox("Wage Model", ["DAILY_WAGE", "MONTHLY_FIXED"], key="enrol_wage_type")
                with c_w2:
                    if w_wage_type == "DAILY_WAGE":
                        w_rate = st.number_input("Daily Wage Rate (₹)", min_value=100.0, value=650.0, step=50.0, key="enrol_rate_daily")
                    else:
                        w_rate = st.number_input("Monthly Salary (₹)", min_value=1000.0, value=18000.0, step=500.0, key="enrol_rate_monthly")

                w_contact = st.text_input("Contact Mobile Number", key="enrol_contact").strip()

                st.markdown("###### Bank Transfer Details")
                b_acc = st.text_input("Bank Account Number", key="enrol_b_acc").strip()
                c_b1, c_b2 = st.columns(2)
                with c_b1:
                    b_ifsc = st.text_input("IFSC Code", placeholder="e.g. BKID0004869", key="enrol_b_ifsc").strip().upper()
                with c_b2:
                    b_branch = st.text_input("Bank & Branch", placeholder="e.g. BOI SARUBERA", key="enrol_b_branch").strip().upper()

                w_status = st.selectbox("Employment Status", ["ACTIVE", "INACTIVE"], key="enrol_status")

                if st.button("Save Employee Record", type="primary", use_container_width=True):
                    final_role = custom_role if sel_role == "Other (Custom Entry)" and custom_role else sel_role
                    if not w_name:
                        st.error("Employee Name is mandatory.")
                    elif sel_role == "Other (Custom Entry)" and not custom_role:
                        st.error("Please specify custom role.")
                    else:
                        w_df = load_sheet_df("worker_master")
                        new_row = {
                            "Worker_ID": next_wid,
                            "Full_Name": w_name,
                            "Govt_ID_Ref": w_ref,
                            "Division": w_div,
                            "Designation": final_role,
                            "Wage_Type": w_wage_type,
                            "Base_Rate": w_rate,
                            "Contact_No": w_contact,
                            "Bank_Account_No": b_acc,
                            "Bank_IFSC_Code": b_ifsc,
                            "Bank_Branch_Details": b_branch,
                            "Status": w_status,
                            "Created_Date": datetime.date.today().strftime("%Y-%m-%d")
                        }
                        updated_w_df = pd.concat([w_df, pd.DataFrame([new_row])], ignore_index=True)
                        if save_sheet_df("worker_master", updated_w_df[WORKER_MASTER_COLS]):
                            st.success(f"Employee {w_name} ({next_wid}) enrolled.")
                            st.rerun()

        with c_info:
            with st.container(border=True):
                st.markdown("###### Routing Guidelines")
                st.caption("• **Bank of India (BOI):** IFSC codes starting with `BKID` automatically route to the BOI internal transfer batch.")
                st.caption("• **Other Banks (NEFT):** All non-BOI IFSC codes route to the NEFT covering sheet.")
                st.caption("• **Cash Settlement:** Leave Bank Account blank if the worker receives cash disbursements.")

    else:
        with st.container(border=True):
            st.markdown("###### Bulk Employee Roster Import")
            st.caption("Upload CSV or Excel file containing workforce details and bank accounts.")

            up_file = st.file_uploader("Upload CSV / XLSX File", type=["csv", "xlsx"])
            if up_file:
                if up_file.name.lower().endswith(".csv"):
                    b_df = pd.read_csv(up_file)
                else:
                    xl_b = pd.ExcelFile(up_file)
                    b_df = xl_b.parse(xl_b.sheet_names[0])
                b_df.columns = [str(c).strip() for c in b_df.columns]
                st.dataframe(b_df.head(5), use_container_width=True, hide_index=True)

                if st.button("Commit Bulk Roster", type="primary", use_container_width=True):
                    w_df = load_sheet_df("worker_master")
                    appended_rows = []
                    for _, b_row in b_df.iterrows():
                        div_val = str(b_row.get("Division", "Fleet Operations")).strip()
                        cur_merged = pd.concat([w_df, pd.DataFrame(appended_rows)], ignore_index=True)
                        new_gen_id = generate_next_worker_id(div_val, cur_merged)

                        raw_desig = str(b_row.get("Designation", "")).strip()
                        clean_desig = raw_desig if raw_desig and raw_desig.lower() != "nan" else "Unassigned"

                        appended_rows.append({
                            "Worker_ID": new_gen_id,
                            "Full_Name": str(b_row.get("Full_Name", "")).strip(),
                            "Govt_ID_Ref": str(b_row.get("Govt_ID_Ref", "")).strip(),
                            "Division": div_val,
                            "Designation": clean_desig,
                            "Wage_Type": str(b_row.get("Wage_Type", "DAILY_WAGE")).strip().upper(),
                            "Base_Rate": float(b_row.get("Base_Rate", 565.0) or 565.0),
                            "Contact_No": str(b_row.get("Contact_No", "")).strip(),
                            "Bank_Account_No": str(b_row.get("Bank_Account_No", "")).strip(),
                            "Bank_IFSC_Code": str(b_row.get("Bank_IFSC_Code", "")).strip().upper(),
                            "Bank_Branch_Details": str(b_row.get("Bank_Branch_Details", "")).strip().upper(),
                            "Status": str(b_row.get("Status", "ACTIVE")).strip().upper(),
                            "Created_Date": datetime.date.today().strftime("%Y-%m-%d")
                        })

                    fin_w_df = pd.concat([w_df, pd.DataFrame(appended_rows)], ignore_index=True)
                    if save_sheet_df("worker_master", fin_w_df[WORKER_MASTER_COLS]):
                        st.success(f"{len(appended_rows)} records imported successfully.")
                        st.rerun()


# ----------------- VIEW 2: EMPLOYEE MASTER REGISTER, EDITABLE PROFILE LEDGER & SELECTIVE 8-UP ATM ID CARDS -----------------
elif current_view == "📋 Employee Master Register":
    w_table = load_sheet_df("worker_master")
    muster_all = load_sheet_df("muster_roll")
    adv_all = load_sheet_df("worker_advances")
    tata_all = load_sheet_df("tata_form_xvii")

    # DRILLDOWN VIEW: OPEN-ABLE EMPLOYEE PROFILE & EDITABLE ATTENDANCE HISTORY
    if st.session_state.selected_worker_card_id:
        target_wid = st.session_state.selected_worker_card_id
        
        top_nav1, top_nav2 = st.columns([1.2, 2.5])
        with top_nav1:
            if st.button("← Back to Employee Master", type="secondary", use_container_width=True):
                st.session_state.selected_worker_card_id = None
                st.rerun()

        w_match = w_table[w_table["Worker_ID"].astype(str) == target_wid]
        if w_match.empty:
            st.error("Employee record not found.")
        else:
            emp = w_match.iloc[0]
            emp_name = str(emp.get("Full_Name", ""))
            emp_div = str(emp.get("Division", ""))
            emp_role = str(emp.get("Designation", ""))
            emp_wage_type = str(emp.get("Wage_Type", ""))
            emp_rate = float(pd.to_numeric(emp.get("Base_Rate", 0.0), errors="coerce") or 0.0)
            emp_acc = str(emp.get("Bank_Account_No", "")) or "Not Linked"
            emp_ifsc = str(emp.get("Bank_IFSC_Code", "")) or "N/A"
            emp_branch = str(emp.get("Bank_Branch_Details", "")) or "Cash Disbursement"
            emp_contact = str(emp.get("Contact_No", "")) or "N/A"

            with top_nav2:
                if FPDF_AVAILABLE:
                    single_card_pdf = generate_atm_id_cards_pdf(w_match)
                    st.download_button(
                        label=f"🪪 Download ATM ID Card PDF ({target_wid})",
                        data=single_card_pdf,
                        file_name=f"ID_Card_{target_wid}.pdf",
                        mime="application/pdf",
                        type="primary"
                    )

            with st.container(border=True):
                p_c1, p_c2 = st.columns([2.2, 1.3])
                with p_c1:
                    st.markdown(f"### 🪪 {emp_name} (`{target_wid}`)")
                    st.markdown(f"**Division:** {emp_div} &nbsp;|&nbsp; **Role:** {emp_role} &nbsp;|&nbsp; **Contact:** {emp_contact}")
                    st.markdown(f"**Wage Structure:** `{emp_wage_type}` @ **₹{emp_rate:,.0f}** &nbsp;|&nbsp; **Status:** `{emp.get('Status', 'ACTIVE')}`")
                with p_c2:
                    st.markdown("###### 🏦 Bank Account Mapping")
                    st.markdown(f"**A/C No:** `{emp_acc}`")
                    st.markdown(f"**IFSC:** `{emp_ifsc}` ({emp_branch})")

            emp_muster = pd.DataFrame()
            if not muster_all.empty and "Worker_ID" in muster_all.columns:
                emp_muster = muster_all[muster_all["Worker_ID"].astype(str) == target_wid].copy()

            emp_adv = pd.DataFrame()
            if not adv_all.empty and "Worker_ID" in adv_all.columns:
                emp_adv = adv_all[adv_all["Worker_ID"].astype(str) == target_wid].copy()

            emp_tata = pd.DataFrame()
            if not tata_all.empty and "Worker_Name" in tata_all.columns:
                emp_tata = tata_all[tata_all["Worker_Name"].astype(str).str.strip().str.upper() == emp_name.strip().upper()].copy()

            # YEAR + MONTH FILTER FOR EMPLOYEE LEDGER
            c_f1, c_f2, _ = st.columns([1.0, 1.2, 2.0])
            with c_f1:
                card_year_filter = st.selectbox("Filter Year", ["All Years", "2025", "2026", "2027"], index=2, key="card_y_flt")
            with c_f2:
                card_month_filter = st.selectbox("Filter Month", ["All Months"] + list(calendar.month_name)[1:], key="card_m_flt")

            if not emp_muster.empty:
                if card_year_filter != "All Years":
                    emp_muster = emp_muster[emp_muster["Date"].astype(str).str.startswith(card_year_filter)]
                if card_month_filter != "All Months":
                    m_idx = list(calendar.month_name).index(card_month_filter)
                    m_prefix = f"-{m_idx:02d}-"
                    emp_muster = emp_muster[emp_muster["Date"].astype(str).str.contains(m_prefix)]

            tot_p = len(emp_muster[emp_muster["Attendance"] == "P"]) if not emp_muster.empty else 0
            tot_hd = len(emp_muster[emp_muster["Attendance"] == "HD"]) if not emp_muster.empty else 0
            tot_a = len(emp_muster[emp_muster["Attendance"] == "A"]) if not emp_muster.empty else 0
            tot_ot = pd.to_numeric(emp_muster["OT_Hours"], errors="coerce").fillna(0).sum() if not emp_muster.empty else 0.0
            tot_adv_amt = pd.to_numeric(emp_adv["Amount"], errors="coerce").fillna(0).sum() if not emp_adv.empty else 0.0

            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Present Days (P)", f"{tot_p} d")
            k2.metric("Half Days (HD)", f"{tot_hd} d")
            k3.metric("Absent Days (A)", f"{tot_a} d")
            k4.metric("Overtime (OT)", f"{tot_ot:.1f} hrs")
            k5.metric("Total Advances", f"₹{tot_adv_amt:,.0f}")

            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

            # EDITABLE ATTENDANCE LEDGER + COMPLIANCE PAYOUTS
            det_c1, det_c2 = st.columns([1.5, 1.0], gap="large")
            with det_c1:
                with st.container(border=True):
                    st.markdown("###### 📅 Daily Field Attendance Log (Direct Edit)")
                    st.caption("Edit Status, Location, OT Hours, or Remarks below and click 'Save Attendance Changes' to sync everywhere")
                    
                    if not emp_muster.empty:
                        emp_muster_sorted = emp_muster.sort_values(by="Date", ascending=False).copy()
                        emp_muster_sorted["OT_Hours"] = pd.to_numeric(emp_muster_sorted["OT_Hours"], errors="coerce").fillna(0.0)
                        edit_cols = ["Date", "Attendance", "Duty_Location", "OT_Hours", "Remarks"]
                        
                        edited_emp_df = st.data_editor(
                            emp_muster_sorted[edit_cols],
                            use_container_width=True,
                            hide_index=True,
                            disabled=["Date"],
                            column_config={
                                "Attendance": st.column_config.SelectboxColumn("Status", options=["P", "HD", "A", "WO"], required=True),
                                "Duty_Location": st.column_config.SelectboxColumn("Location", options=["Tata Site (In-Plant)", "Private / Internal"], required=True),
                                "OT_Hours": st.column_config.NumberColumn("OT (Hrs)", min_value=0.0, max_value=16.0, step=0.5),
                                "Remarks": st.column_config.TextColumn("Remarks")
                            },
                            key=f"editor_att_{target_wid}"
                        )

                        if st.button("💾 Save Attendance Changes", type="primary", use_container_width=True, key=f"save_edit_att_{target_wid}"):
                            full_m_df = load_sheet_df("muster_roll")
                            if not full_m_df.empty:
                                for _, erow in edited_emp_df.iterrows():
                                    d_val = str(erow["Date"])
                                    mask = (full_m_df["Worker_ID"].astype(str) == target_wid) & (full_m_df["Date"].astype(str) == d_val)
                                    full_m_df.loc[mask, "Attendance"] = str(erow["Attendance"])
                                    full_m_df.loc[mask, "Duty_Location"] = str(erow["Duty_Location"])
                                    full_m_df.loc[mask, "OT_Hours"] = float(erow["OT_Hours"] or 0.0)
                                    full_m_df.loc[mask, "Remarks"] = str(erow["Remarks"] or "")
                                
                                if save_sheet_df("muster_roll", full_m_df[MUSTER_ROLL_COLS]):
                                    for k in list(st.session_state.keys()):
                                        if str(k).startswith("att_state_"):
                                            del st.session_state[k]
                                    st.success("Attendance updated and synced across all modules!")
                                    st.rerun()
                    else:
                        st.info("No field attendance logs recorded for this period.")

            with det_c2:
                with st.container(border=True):
                    st.markdown("###### 🏛️ Tata Form XVII Compliance Payouts")
                    if not emp_tata.empty:
                        disp_t_cols = [c for c in ["Wages_Period", "Approved_Days", "Net_Amount_Paid"] if c in emp_tata.columns]
                        st.dataframe(emp_tata[disp_t_cols], use_container_width=True, hide_index=True)
                    else:
                        st.info("No Form XVII statutory records linked yet.")

                with st.container(border=True):
                    st.markdown("###### 💵 Advance & Expense History")
                    if not emp_adv.empty:
                        disp_a_cols = [c for c in ["Date", "Amount", "Payment_Mode", "Remarks"] if c in emp_adv.columns]
                        st.dataframe(emp_adv[disp_a_cols], use_container_width=True, hide_index=True)
                    else:
                        st.info("No advances disbursed.")

    # MASTER DIRECTORY + SELECTIVE 8-UP ATM ID CARDS PDF DOWNLOADER + OPEN-ABLE EMPLOYEE INSPECTOR
    else:
        st.markdown("#### Employee Master Register & Selective ATM ID Cards")
        st.caption("Inspect/edit individual employee attendance ledgers or select specific employees to print 8-up ATM-sized ID cards")

        if w_table.empty or "Worker_ID" not in w_table.columns:
            st.info("No employee records found.")
        else:
            flt_c1, flt_c2, flt_c3 = st.columns([1.1, 1.2, 1.9])
            with flt_c1:
                f_div = st.selectbox("Division", ["All", "Fleet Operations", "Civil Construction"], key="reg_div_f")
            with flt_c2:
                f_bank = st.selectbox("Bank Routing", ["All", "BOI Accounts", "Other Banks (NEFT)", "Cash (No Bank)"], key="reg_bank_f")
            with flt_c3:
                f_search = st.text_input("Search Employee", placeholder="Filter by Name, ID, or Account...").strip().lower()

            disp_table = w_table.copy()
            if f_div != "All":
                disp_table = disp_table[disp_table["Division"] == f_div]
            if f_bank == "BOI Accounts":
                disp_table = disp_table[disp_table["Bank_IFSC_Code"].astype(str).str.startswith("BKID")]
            elif f_bank == "Other Banks (NEFT)":
                disp_table = disp_table[(~disp_table["Bank_IFSC_Code"].astype(str).str.startswith("BKID")) & (disp_table["Bank_Account_No"].astype(str) != "")]
            elif f_bank == "Cash (No Bank)":
                disp_table = disp_table[disp_table["Bank_Account_No"].astype(str) == ""]

            if f_search:
                disp_table = disp_table[
                    disp_table["Full_Name"].astype(str).str.lower().str.contains(f_search) |
                    disp_table["Worker_ID"].astype(str).str.lower().str.contains(f_search) |
                    disp_table["Bank_Account_No"].astype(str).str.lower().str.contains(f_search)
                ]

            act_c1, act_c2 = st.columns([1.25, 1.35], gap="medium")
            with act_c1:
                with st.container(border=True):
                    st.markdown("###### 🔍 Open Employee Profile & Edit Attendance")
                    emp_choices = {
                        f"{r['Worker_ID']} — {r['Full_Name']} ({r['Designation']})": str(r["Worker_ID"])
                        for _, r in disp_table.iterrows()
                    }
                    if emp_choices:
                        sc1, sc2 = st.columns([2.2, 1.0])
                        with sc1:
                            sel_emp_label = st.selectbox("Select Employee ID", list(emp_choices.keys()), label_visibility="collapsed")
                        with sc2:
                            if st.button("Open Profile →", type="primary", use_container_width=True):
                                st.session_state.selected_worker_card_id = emp_choices[sel_emp_label]
                                st.rerun()

            with act_c2:
                with st.container(border=True):
                    st.markdown("###### 🪪 Selective 8-Up ATM ID Cards (A4 PDF)")
                    all_card_options = {
                        f"{r['Worker_ID']} — {r['Full_Name']}": str(r["Worker_ID"])
                        for _, r in disp_table.iterrows()
                    }
                    sel_card_labels = st.multiselect(
                        "Select Employees for ID Cards (Leave empty to select all filtered)",
                        options=list(all_card_options.keys()),
                        placeholder="Choose specific employees to print cards..."
                    )
                    
                    target_wids_for_cards = [all_card_options[lbl] for lbl in sel_card_labels] if sel_card_labels else list(all_card_options.values())
                    cards_subset_df = disp_table[disp_table["Worker_ID"].astype(str).isin(target_wids_for_cards)]

                    if FPDF_AVAILABLE and not cards_subset_df.empty:
                        cards_pdf_bytes = generate_atm_id_cards_pdf(cards_subset_df)
                        pages_cnt = (len(cards_subset_df) + 7) // 8
                        btn_lbl = (
                            f"📥 Download Selected ID Cards PDF ({len(cards_subset_df)} Cards • {pages_cnt} Page)"
                            if sel_card_labels else
                            f"📥 Download All Filtered ID Cards PDF ({len(cards_subset_df)} Cards • {pages_cnt} Page)"
                        )
                        st.download_button(
                            label=btn_lbl,
                            data=cards_pdf_bytes,
                            file_name=f"M_s_INDU_SAO_ID_Cards_{datetime.date.today().strftime('%Y%m%d')}.pdf",
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True
                        )
                    elif not FPDF_AVAILABLE:
                        st.warning("Add 'fpdf2' to requirements.txt to enable PDF ID cards.")

            req_cols = ["Worker_ID", "Full_Name", "Division", "Designation", "Wage_Type", "Base_Rate", "Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details"]
            safe_cols = [c for c in req_cols if c in disp_table.columns]
            st.dataframe(disp_table[safe_cols], use_container_width=True, hide_index=True)


# ----------------- VIEW 3: DAILY FIELD MUSTER ROLL (WITH SELECTIVE MARKING & CLEAR SITE PURPOSE) -----------------
elif current_view == "Daily Muster Roll (Field Attendance)":
    st.markdown("#### Daily Field Muster Roll")
    st.caption("Log daily shift attendance and separate Tata In-Plant Compliance vs Private/Internal Site Work")

    w_master = load_sheet_df("worker_master")
    if w_master.empty or "Worker_ID" not in w_master.columns:
        st.warning("No employees available to mark attendance.")
    else:
        active_workers = w_master[w_master["Status"] == "ACTIVE"].copy()

        top_m1, top_m2, top_m3 = st.columns([1.1, 1.2, 1.2])
        with top_m1:
            muster_date = st.date_input("Date", value=datetime.date.today(), key="muster_dt_pick")
            muster_date_str = muster_date.strftime("%Y-%m-%d")
        with top_m2:
            sel_division = st.selectbox("Division", ["All Divisions", "Fleet Operations", "Civil Construction"], key="muster_div_filter")
        with top_m3:
            st.metric("Active Crew", len(active_workers))

        if sel_division != "All Divisions":
            active_workers = active_workers[active_workers["Division"] == sel_division]

        muster_df = load_sheet_df("muster_roll")
        day_records = pd.DataFrame()
        if not muster_df.empty and "Date" in muster_df.columns:
            day_records = muster_df[muster_df["Date"].astype(str) == muster_date_str]

        cache_key = f"att_state_{muster_date_str}"
        if cache_key not in st.session_state:
            st.session_state[cache_key] = {}
            for _, w in active_workers.iterrows():
                wid = str(w["Worker_ID"])
                def_att, def_ot, def_loc = "P", 0.0, "Tata Site (In-Plant)"

                if not day_records.empty and "Worker_ID" in day_records.columns:
                    match_prev = day_records[day_records["Worker_ID"].astype(str) == wid]
                    if not match_prev.empty:
                        r0 = match_prev.iloc[0]
                        def_att = str(r0.get("Attendance", "P"))
                        def_ot = float(r0.get("OT_Hours", 0.0) or 0.0)
                        def_loc = str(r0.get("Duty_Location", "Tata Site (In-Plant)"))

                st.session_state[cache_key][wid] = {
                    "att": def_att, "ot": def_ot, "loc": def_loc
                }

        # SELECTIVE BULK MARKING PANEL
        with st.container(border=True):
            st.markdown("###### ⚡ Selective Quick-Mark Tool")
            st.caption("Select specific workers below (or leave empty to apply to all shown workers). Note: 'Tata Site' counts toward Tata Form XVII & RA Billing; 'Private / Internal' tracks non-Tata site costing.")
            
            worker_map = {
                f"{r['Worker_ID']} — {r['Full_Name']}": str(r["Worker_ID"])
                for _, r in active_workers.iterrows()
            }
            
            sm_c1, sm_c2, sm_c3 = st.columns([2.0, 1.3, 1.0])
            with sm_c1:
                chosen_labels = st.multiselect(
                    "Choose Specific Workers",
                    options=list(worker_map.keys()),
                    placeholder="Leave empty for ALL workers, or pick specific names...",
                    label_visibility="collapsed"
                )
            with sm_c2:
                bulk_action = st.selectbox(
                    "Quick Action",
                    [
                        "Present — Tata Site (In-Plant)",
                        "Present — Private / Internal",
                        "Half Day (HD) — Tata Site",
                        "Absent (A)",
                        "Week Off (WO)"
                    ],
                    label_visibility="collapsed"
                )
            with sm_c3:
                if st.button("Apply to Selected", type="primary", use_container_width=True):
                    target_wids = [worker_map[l] for l in chosen_labels] if chosen_labels else list(worker_map.values())
                    for wid in target_wids:
                        if wid in st.session_state[cache_key]:
                            if bulk_action == "Present — Tata Site (In-Plant)":
                                st.session_state[cache_key][wid]["att"] = "P"
                                st.session_state[cache_key][wid]["loc"] = "Tata Site (In-Plant)"
                            elif bulk_action == "Present — Private / Internal":
                                st.session_state[cache_key][wid]["att"] = "P"
                                st.session_state[cache_key][wid]["loc"] = "Private / Internal"
                            elif bulk_action == "Half Day (HD) — Tata Site":
                                st.session_state[cache_key][wid]["att"] = "HD"
                                st.session_state[cache_key][wid]["loc"] = "Tata Site (In-Plant)"
                            elif bulk_action == "Absent (A)":
                                st.session_state[cache_key][wid]["att"] = "A"
                            elif bulk_action == "Week Off (WO)":
                                st.session_state[cache_key][wid]["att"] = "WO"
                    st.rerun()

        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
        grid_cols = st.columns(2, gap="medium")
        
        for idx, (_, w_row) in enumerate(active_workers.iterrows()):
            col_target = grid_cols[idx % 2]
            wid = str(w_row["Worker_ID"])
            wname = str(w_row["Full_Name"])
            wdiv = str(w_row["Division"])
            wrole = str(w_row["Designation"])
            cur_state = st.session_state[cache_key].get(wid, {})

            with col_target:
                with st.container(border=True):
                    st.markdown(f"**{wname}** (`{wid}`) • <span style='font-size: 0.78rem; color: #4f46e5; font-weight: 500;'>{wdiv}</span>", unsafe_allow_html=True)
                    st.caption(f"{wrole}")

                    r_c1, r_c2, r_c3 = st.columns([1.1, 1.3, 1.0])
                    with r_c1:
                        cur_state["att"] = st.selectbox("Status", ["P", "HD", "A", "WO"], index=["P", "HD", "A", "WO"].index(cur_state.get("att", "P")), key=f"att_s_{wid}")
                    with r_c2:
                        cur_state["loc"] = st.selectbox("Duty Site", ["Tata Site (In-Plant)", "Private / Internal"], index=["Tata Site (In-Plant)", "Private / Internal"].index(cur_state.get("loc", "Tata Site (In-Plant)")), key=f"loc_s_{wid}")
                    with r_c3:
                        cur_state["ot"] = st.number_input("OT (Hrs)", min_value=0.0, max_value=16.0, step=0.5, value=float(cur_state.get("ot", 0.0)), key=f"ot_s_{wid}")

            st.session_state[cache_key][wid] = cur_state

        st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
        if st.button("Save Daily Muster Roll", type="primary", use_container_width=True):
            marked_rows = []
            for _, w_row in active_workers.iterrows():
                wid = str(w_row["Worker_ID"])
                s_data = st.session_state[cache_key].get(wid, {})
                marked_rows.append({
                    "Date": muster_date_str,
                    "Worker_ID": wid,
                    "Worker_Name": str(w_row["Full_Name"]),
                    "Division": str(w_row["Division"]),
                    "Attendance": s_data.get("att", "P"),
                    "OT_Hours": s_data.get("ot", 0.0),
                    "Duty_Location": s_data.get("loc", "Tata Site (In-Plant)"),
                    "Advance_Amount": 0.0,
                    "Payment_Mode": "Cash",
                    "Supervisor": st.session_state.current_user,
                    "Remarks": ""
                })

            all_m = load_sheet_df("muster_roll")
            if not all_m.empty and "Date" in all_m.columns and "Worker_ID" in all_m.columns:
                w_ids = [r["Worker_ID"] for r in marked_rows]
                cond = (all_m["Date"].astype(str) == muster_date_str) & (all_m["Worker_ID"].astype(str).isin(w_ids))
                all_m = all_m[~cond]

            updated_m = pd.concat([all_m, pd.DataFrame(marked_rows)], ignore_index=True)
            if save_sheet_df("muster_roll", updated_m[MUSTER_ROLL_COLS]):
                st.success("Daily Muster Roll saved.")
                st.rerun()


# ----------------- VIEW 4: ADVANCE REGISTER -----------------
elif current_view == "Advance & Expense Register":
    st.markdown("#### Advance & Expense Register")
    st.caption("Disburse and track site advances and allowances")

    w_master = load_sheet_df("worker_master")
    worker_options = {}
    if not w_master.empty and "Worker_ID" in w_master.columns:
        for _, r in w_master[w_master["Status"] == "ACTIVE"].iterrows():
            worker_options[f"{r['Full_Name']} ({r['Worker_ID']})"] = (r['Worker_ID'], r['Full_Name'], r['Division'])

    c_adv1, c_adv2 = st.columns([1, 2], gap="large")
    with c_adv1:
        with st.container(border=True):
            st.markdown("###### Disburse Advance")
            if worker_options:
                with st.form("disburse_adv_form"):
                    a_date = st.date_input("Date", value=datetime.date.today())
                    sel_w = st.selectbox("Employee", list(worker_options.keys()))
                    a_amt = st.number_input("Amount (₹)", min_value=100.0, step=100.0, value=500.0)
                    a_mode = st.selectbox("Mode", ["Cash", "Online UPI", "Bank Transfer"])
                    a_rem = st.text_input("Remarks").strip()

                    if st.form_submit_button("Record Transaction", type="primary", use_container_width=True):
                        w_id, w_name, w_div = worker_options[sel_w]
                        adv_df = load_sheet_df("worker_advances")
                        new_adv = {
                            "Txn_ID": f"ADV-{int(time.time())}",
                            "Date": a_date.strftime("%Y-%m-%d"),
                            "Worker_ID": w_id,
                            "Worker_Name": w_name,
                            "Division": w_div,
                            "Amount": a_amt,
                            "Payment_Mode": a_mode,
                            "Supervisor": st.session_state.current_user,
                            "Remarks": a_rem
                        }
                        updated_adv = pd.concat([adv_df, pd.DataFrame([new_adv])], ignore_index=True)
                        if save_sheet_df("worker_advances", updated_adv[ADVANCE_LOG_COLS]):
                            st.success("Transaction recorded.")
                            st.rerun()

    with c_adv2:
        with st.container(border=True):
            st.markdown("###### Recent Advance Transactions")
            adv_history = load_sheet_df("worker_advances")
            if not adv_history.empty and "Txn_ID" in adv_history.columns:
                st.dataframe(adv_history[["Date", "Worker_Name", "Amount", "Payment_Mode", "Supervisor", "Remarks"]].tail(15), use_container_width=True, hide_index=True)
            else:
                st.info("No recorded transactions.")


# ----------------- VIEW 5: WAGES, FORM XVII & DIRECT PDF BANK LETTERS -----------------
elif current_view == "Monthly Wages & Payout Summary":
    st.markdown("#### Monthly Wages, Form XVII Audit & Direct PDF Bank Letters")
    st.caption("Reconcile Tata In-Plant field duty with Tata Steel Form XVII and download direct print-ready PDF covering letters")

    w_master = load_sheet_df("worker_master")
    muster_df = load_sheet_df("muster_roll")
    tata_df = load_sheet_df("tata_form_xvii")

    f_c1, f_c2 = st.columns([1, 1])
    with f_c1:
        sel_year = st.selectbox("Year", [2025, 2026, 2027], index=1)
    with f_c2:
        sel_month = st.selectbox("Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1)

    month_num = list(calendar.month_name).index(sel_month)
    target_ym = f"{sel_year}-{month_num:02d}"
    wage_period_str = f"{sel_month[:3].upper()}-{sel_year}"
    target_short = f"{sel_month[:3].lower()}-{str(sel_year)[-2:]}"

    def period_matches(val):
        s = str(val).strip().lower()
        return (target_ym in s) or (wage_period_str.lower() in s) or (target_short in s)

    with st.expander("📥 Import Tata Steel FORM XVII (Register of Wages)", expanded=tata_df.empty):
        st.markdown(f"Upload official **Form XVII [Rule 78(2)(a)]** for **{wage_period_str}**")
        xvii_file = st.file_uploader("Upload Form XVII (PDF / CSV / XLSX)", type=["pdf", "csv", "xlsx"])

        if xvii_file:
            x_df = pd.DataFrame()
            if xvii_file.name.lower().endswith(".pdf"):
                if not PYPDF_AVAILABLE:
                    st.error("⚠️ PDF reader ('pypdf') not installed. Add 'pypdf' to requirements.txt or upload CSV/Excel.")
                else:
                    with st.spinner("Extracting records from Form XVII PDF..."):
                        x_df = parse_tata_form_xvii_pdf(xvii_file)
            elif xvii_file.name.lower().endswith(".csv"):
                x_df = pd.read_csv(xvii_file)
                x_df.columns = [str(c).strip().replace(" ", "_") for c in x_df.columns]
            else:
                xl = pd.ExcelFile(xvii_file)
                target_sheet = xl.sheet_names[0]
                for s_name in xl.sheet_names:
                    temp_df = xl.parse(s_name, nrows=2)
                    clean_cols = [str(c).strip().replace(" ", "_") for c in temp_df.columns]
                    if "Net_Amount_Paid" in clean_cols or "Net_Amount" in clean_cols:
                        target_sheet = s_name
                        break
                x_df = xl.parse(target_sheet)
                x_df.columns = [str(c).strip().replace(" ", "_") for c in x_df.columns]

            if not x_df.empty and ("Net_Amount_Paid" in x_df.columns or "Net_Amount" in x_df.columns):
                net_col = "Net_Amount_Paid" if "Net_Amount_Paid" in x_df.columns else "Net_Amount"
                name_col_guess = "Worker_Name" if "Worker_Name" in x_df.columns else ("Name_of_Workman" if "Name_of_Workman" in x_df.columns else ("Name" if "Name" in x_df.columns else None))
                if name_col_guess:
                    x_df = x_df[~x_df[name_col_guess].astype(str).str.upper().str.contains("TOTAL", na=False)]

                net_sum = pd.to_numeric(x_df[net_col], errors="coerce").fillna(0).sum()
                st.success(f"Extracted {len(x_df)} records • Net Payable: ₹{net_sum:,.0f}")
                st.dataframe(x_df.head(5), use_container_width=True, hide_index=True)

                if st.button("Commit Form XVII Compliance Data", type="primary", use_container_width=True):
                    new_records = []
                    for _, xr in x_df.iterrows():
                        w_name_raw = str(
                            xr.get("Worker_Name") or xr.get("Name_of_Workman") or xr.get("Name") or ""
                        ).strip()
                        
                        if not w_name_raw or w_name_raw.lower() == "nan" or "TOTAL" in w_name_raw.upper():
                            continue

                        appr_days = float(
                            pd.to_numeric(xr.get("Approved_Days") or xr.get("No_of_days_worked") or xr.get("No._of_days_worked") or 0.0, errors="coerce") or 0.0
                        )
                        
                        net_paid = float(
                            pd.to_numeric(xr.get("Net_Amount_Paid") or xr.get("Net_Amount") or 0.0, errors="coerce") or 0.0
                        )

                        if net_paid <= 0:
                            continue

                        matched_wid = ""
                        if not w_master.empty and "Full_Name" in w_master.columns:
                            match_w = w_master[w_master["Full_Name"].astype(str).str.strip().str.upper() == w_name_raw.upper()]
                            if not match_w.empty:
                                matched_wid = str(match_w.iloc[0]["Worker_ID"])

                        new_records.append({
                            "Wages_Period": target_ym,
                            "Worker_ID": matched_wid,
                            "Worker_Name": w_name_raw.upper(),
                            "Approved_Days": appr_days,
                            "Net_Amount_Paid": net_paid,
                            "Uploaded_At": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })

                    if not tata_df.empty and "Wages_Period" in tata_df.columns:
                        tata_df = tata_df[~tata_df["Wages_Period"].apply(period_matches)]

                    final_tata = pd.concat([tata_df, pd.DataFrame(new_records)], ignore_index=True)
                    if save_sheet_df("tata_form_xvii", final_tata[TATA_COMPLIANCE_COLS]):
                        st.success(f"Committed {len(new_records)} workers for {wage_period_str}!")
                        st.rerun()
            else:
                if not xvii_file.name.lower().endswith(".pdf") or PYPDF_AVAILABLE:
                    st.error("Could not find 'Net_Amount_Paid' column in uploaded file.")

    st.markdown(f"###### Reconciliation Cockpit ({wage_period_str} Tata Site Duty vs Form XVII)")

    if not tata_df.empty and "Wages_Period" in tata_df.columns:
        tata_cur = tata_df[tata_df["Wages_Period"].apply(period_matches)]
    else:
        tata_cur = pd.DataFrame(columns=TATA_COMPLIANCE_COLS)

    d_start = f"{sel_year}-{month_num:02d}-01"
    d_end = f"{sel_year}-{month_num:02d}-{calendar.monthrange(sel_year, month_num)[1]:02d}"

    recon_rows = []
    boi_batch = []
    neft_batch = []

    if not w_master.empty and "Worker_ID" in w_master.columns:
        for _, w in w_master[w_master["Status"] == "ACTIVE"].iterrows():
            wid = str(w["Worker_ID"])
            wname = str(w["Full_Name"])
            b_acc = str(w.get("Bank_Account_No", "")).strip()
            b_ifsc = str(w.get("Bank_IFSC_Code", "")).strip().upper()
            b_branch = str(w.get("Bank_Branch_Details", "")).strip().upper()

            tata_field_days = 0.0
            pvt_field_days = 0.0
            if not muster_df.empty and "Date" in muster_df.columns and "Worker_ID" in muster_df.columns:
                w_m = muster_df[(muster_df["Worker_ID"].astype(str) == wid) & 
                               (muster_df["Date"].astype(str) >= d_start) & 
                               (muster_df["Date"].astype(str) <= d_end)]
                
                w_tata = w_m[w_m["Duty_Location"] == "Tata Site (In-Plant)"]
                tata_field_days = len(w_tata[w_tata["Attendance"] == "P"]) + (len(w_tata[w_tata["Attendance"] == "HD"]) * 0.5)

                w_pvt = w_m[w_m["Duty_Location"] == "Private / Internal"]
                pvt_field_days = len(w_pvt[w_pvt["Attendance"] == "P"]) + (len(w_pvt[w_pvt["Attendance"] == "HD"]) * 0.5)

            tata_approved_days = 0.0
            tata_net_paid = 0.0

            if not tata_cur.empty and "Worker_Name" in tata_cur.columns:
                tata_match = tata_cur[tata_cur["Worker_Name"].astype(str).str.strip().str.upper() == wname.strip().upper()]
                if not tata_match.empty:
                    tata_approved_days = float(pd.to_numeric(tata_match.iloc[0].get("Approved_Days", 0.0), errors="coerce") or 0.0)
                    tata_net_paid = float(pd.to_numeric(tata_match.iloc[0].get("Net_Amount_Paid", 0.0), errors="coerce") or 0.0)

            status_badge = "🟢 Matched"
            if tata_approved_days == 0 and tata_field_days > 0:
                status_badge = "🔴 No Punch (Field Only)"
            elif tata_approved_days != tata_field_days:
                status_badge = f"🟡 Variance ({tata_field_days - tata_approved_days:+.1f} d)"

            final_bank_transfer = tata_net_paid

            recon_rows.append({
                "Worker ID": wid,
                "Name": wname,
                "Tata Field Days": tata_field_days,
                "Private Days": pvt_field_days,
                "Form XVII Days": tata_approved_days,
                "Status": status_badge,
                "Locked Net (₹)": final_bank_transfer,
                "IFSC": b_ifsc,
                "Bank Details": b_branch
            })

            if final_bank_transfer > 0 and b_acc:
                item = {
                    "NAME": wname,
                    "AMOUNT": final_bank_transfer,
                    "BENEFICIARY ACCOUNT NO": b_acc,
                    "IFSC CODE": b_ifsc,
                    "BANK & BRANCH DETAILS": b_branch
                }
                if b_ifsc.startswith("BKID"):
                    boi_batch.append(item)
                else:
                    neft_batch.append(item)

    recon_df = pd.DataFrame(recon_rows)
    if not recon_df.empty:
        st.dataframe(recon_df, use_container_width=True, hide_index=True)

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
    st.markdown("###### Automated Bank Covering Letters (Direct PDF Download)")

    p_c1, p_c2 = st.columns([1.2, 1.2])
    with p_c1:
        chq_no = st.text_input("Cheque Number", value="238883", key="bank_chq_no").strip()
    with p_c2:
        chq_date = st.text_input("Cheque Date (DD.MM.YYYY)", value=datetime.date.today().strftime("%d.%m.%Y"), key="bank_chq_dt").strip()

    tot_boi = sum([r["AMOUNT"] for r in boi_batch])
    tot_neft = sum([r["AMOUNT"] for r in neft_batch])

    m1, m2, m3 = st.columns(3)
    m1.metric("Bank of India (BOI)", f"₹{tot_boi:,.0f}", f"{len(boi_batch)} Beneficiaries")
    m2.metric("Other Banks (NEFT)", f"₹{tot_neft:,.0f}", f"{len(neft_batch)} Beneficiaries")
    m3.metric("Total Transfer", f"₹{(tot_boi + tot_neft):,.0f}", "Compliance Locked")

    dl_c1, dl_c2 = st.columns(2)
    with dl_c1:
        if boi_batch:
            if FPDF_AVAILABLE:
                boi_pdf_bytes = generate_bank_authority_letter_pdf("BOI", boi_batch, chq_no, chq_date)
                st.download_button(
                    label=f"📥 Download BOI Covering Letter PDF ({len(boi_batch)} Workers • ₹{tot_boi:,.0f})",
                    data=boi_pdf_bytes,
                    file_name=f"BOI_Transfer_Letter_{wage_period_str}.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )
            else:
                st.warning("Add 'fpdf2' to requirements.txt for direct PDF download.")
        else:
            st.info("No Bank of India transfers in this batch.")

    with dl_c2:
        if neft_batch:
            if FPDF_AVAILABLE:
                neft_pdf_bytes = generate_bank_authority_letter_pdf("NEFT", neft_batch, chq_no, chq_date)
                st.download_button(
                    label=f"📥 Download NEFT Covering Letter PDF ({len(neft_batch)} Workers • ₹{tot_neft:,.0f})",
                    data=neft_pdf_bytes,
                    file_name=f"NEFT_Transfer_Letter_{wage_period_str}.pdf",
                    mime="application/pdf",
                    type="primary" if not boi_batch else "secondary",
                    use_container_width=True
                )
            else:
                st.warning("Add 'fpdf2' to requirements.txt for direct PDF download.")
        else:
            st.info("No NEFT transfers in this batch.")


# ----------------- VIEW 6: BILLING MODULE PLACEHOLDERS -----------------
elif current_view in ["Daily Logsheet Entry", "Monthly Statement & Abstract", "Locked RA Bills Repository", 
                      "Delivery Order (DO) Contract Management", "Vehicle Fleet Master Mapping", "Commercial Rate Master"]:
    st.markdown(f"#### {current_view}")
    st.caption("Commercial Fleet Logs, Contract Abstracts and Billing Repository")
    st.info(f"Module '{current_view}' is ready.")


# ----------------- VIEW 7: FUEL MODULE PLACEHOLDERS -----------------
elif current_view in ["Daily Diesel Issue Slip", "Fleet Fuel Benchmark Master", "Theft & Pilferage Audit"]:
    st.markdown(f"#### {current_view}")
    st.caption("Fleet Fuel Control, Bowser Dispatches and Anti-Theft Analytics")
    st.info(f"Module '{current_view}' is ready.")


# ----------------- VIEW 8: ADMIN & USER PERMISSIONS (ADMIN ONLY) -----------------
elif current_view == "User Accounts & RBAC" and st.session_state.current_role == "ADMIN":
    st.markdown("#### User Accounts & Access Control (RBAC)")
    st.caption("Provision credentials and configure sub-menu permissions per user")

    col_u1, col_u2 = st.columns([1.1, 1.9], gap="large")

    with col_u1:
        with st.container(border=True):
            st.markdown("###### Provision / Update User")
            
            with st.form("create_user_form", clear_on_submit=False):
                new_username = st.text_input("Username").strip()
                new_password = st.text_input("Password", type="password").strip()
                new_name = st.text_input("Full Name").strip()
                new_contact = st.text_input("Contact Number").strip()
                
                f_c1, f_c2 = st.columns(2)
                with f_c1:
                    new_role = st.selectbox("Role", ["USER", "ADMIN"])
                with f_c2:
                    new_status = st.selectbox("Status", ["ACTIVE", "INACTIVE"])

                st.markdown("**Sub-Menu Permissions:**")
                selected_permissions = []
                
                with st.expander("Worker Attendance", expanded=True):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Attendance":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                with st.expander("Tata Steel Billing", expanded=(new_role == "ADMIN")):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Billing":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                with st.expander("Site Fuel Record", expanded=(new_role == "ADMIN")):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Fuel":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                submit_user = st.form_submit_button("Save User Account", use_container_width=True, type="primary")

                if submit_user:
                    if not new_username or not new_password:
                        st.error("Username and password are required.")
                    else:
                        users_df = load_sheet_df("app_users")
                        users_df["_u_chk"] = users_df["Username"].astype(str).str.strip().str.lower()
                        has_admin = not users_df[users_df["_u_chk"] == DEFAULT_ADMIN_USER.lower()].empty
                        users_df = users_df[users_df["_u_chk"] != new_username.lower()].drop(columns=["_u_chk"])

                        rows_to_append = []
                        if not has_admin and new_username.lower() != DEFAULT_ADMIN_USER.lower():
                            rows_to_append.append({
                                "Username": DEFAULT_ADMIN_USER,
                                "Password": DEFAULT_ADMIN_PASS,
                                "Full_Name": DEFAULT_ADMIN_NAME,
                                "Role": "ADMIN",
                                "Allowed_Tabs": "ALL",
                                "Status": "ACTIVE",
                                "Contact_No": "",
                                "Emergency_Contact": "",
                                "Photo_Base64": ""
                            })

                        rows_to_append.append({
                            "Username": new_username,
                            "Password": new_password,
                            "Full_Name": new_name,
                            "Role": new_role,
                            "Allowed_Tabs": ",".join(selected_permissions) if new_role != "ADMIN" else "ALL",
                            "Status": new_status,
                            "Contact_No": new_contact,
                            "Emergency_Contact": "",
                            "Photo_Base64": ""
                        })

                        updated_users = pd.concat([users_df, pd.DataFrame(rows_to_append)], ignore_index=True)
                        if save_sheet_df("app_users", updated_users[USER_COLUMNS]):
                            st.success(f"User '{new_username}' saved.")
                            st.rerun()

    with col_u2:
        with st.container(border=True):
            st.markdown("###### Active System Users")
            users_table = load_sheet_df("app_users")
            if not users_table.empty and "Username" in users_table.columns:
                disp_cols = [c for c in ["Username", "Password", "Full_Name", "Role", "Status", "Allowed_Tabs"] if c in users_table.columns]
                st.dataframe(users_table[disp_cols], use_container_width=True, hide_index=True)
