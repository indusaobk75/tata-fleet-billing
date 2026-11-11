import io
import os
import json
import time
import base64
import datetime
import calendar
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

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
    "PIN_CALL": "Pin No. 829134 | Call: 9234779896, 9661152652",
    "EMAIL": "Email: indusao_bk@yahoo.com",
    "GST": "GST No. 20AUQPK2982K1ZC | Vendor Code - 47",
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
    for attempt in range(3):
        try:
            gc = get_gspread_client()
            sh = gc.open(SHEET_NAME)
            ws = sh.worksheet(worksheet_name)
            data = ws.get_all_records()
            return pd.DataFrame(data)
        except Exception as e:
            if "429" in str(e):
                time.sleep(1.5 * (attempt + 1))
                continue
            return pd.DataFrame()
    return pd.DataFrame()

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
            ws.update(prepared_values, value_input_option="USER_ENTERED")
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


# ----------------- 2. GRANULAR ACCESS & RBAC DEFINITION -----------------
TAB_REGISTRY = {
    # MODULE 1: CLIENT BILLING HUB
    "Daily Logsheet Entry": "Billing",
    "Monthly Statement & Abstract": "Billing",
    "Locked RA Bills Repository": "Billing",
    "Delivery Order (DO) Contract Management": "Billing",
    "Vehicle Fleet Master Mapping": "Billing",
    "Commercial Rate Master": "Billing",

    # MODULE 2: FUEL CONTROL
    "Daily Diesel Issue Slip": "Fuel",
    "Fleet Fuel Benchmark Master": "Fuel",
    "Theft & Pilferage Audit": "Fuel",

    # MODULE 3: WORKER ATTENDANCE (GRANULAR ACCESS)
    "➕ Employee Enrolment": "Attendance",
    "📋 Employee Master Register": "Attendance",
    "Daily Muster Roll (Field Attendance)": "Attendance",
    "Advance & Expense Register": "Attendance",
    "Monthly Wages & Payout Summary": "Attendance"
}

TAB_ALIASES = {
    "Worker Daily Muster Roll": "Daily Muster Roll (Field Attendance)",
    "Driver & Operator Pool": "📋 Employee Master Register",
    "Worker & Crew Directory": "📋 Employee Master Register",
    "Monthly Attendance Summary": "Monthly Wages & Payout Summary",
    "Advance & Khuraki Register": "Advance & Expense Register"
}

INDU_SAO_SVG_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 120" style="width: 44px; height: 34px; filter: drop-shadow(0 1px 2px rgba(0,0,0,0.15));"><ellipse cx="80" cy="60" rx="76" ry="56" fill="#ffffff" stroke="#1e293b" stroke-width="4.5"/><polygon points="54,90 54,42 70,30 70,90" fill="#0b2a75"/><polygon points="76,90 76,22 92,12 92,90" fill="#0b2a75"/><rect x="98" y="24" width="4" height="66" fill="#334155"/><rect x="105" y="32" width="4" height="58" fill="#334155"/><path d="M 32,76 C 50,98 112,98 128,78 C 114,90 58,90 32,76 Z" fill="#0b2a75"/><circle cx="80" cy="74" r="5" fill="#0b2a75"/></svg>"""

st.markdown("""
<style>
    .block-container { 
        padding-top: 3.2rem !important; 
        padding-bottom: 2rem !important;
        padding-left: 2rem !important;
        padding-right: 2rem !important;
        max-width: 100% !important;
        width: 100% !important;
    }
    section[data-testid="stSidebar"][aria-expanded="true"] {
        width: 275px !important;
        min-width: 275px !important;
    }
    .top-user-avatar {
        width: 36px;
        height: 36px;
        border-radius: 50%;
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 0.88rem;
        color: white;
        object-fit: cover;
    }
    .status-badge {
        display: inline-block;
        padding: 1px 7px;
        border-radius: 9999px;
        font-size: 0.65rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .status-admin { background-color: #064e3b; color: #34d399; border: 1px solid #059669; }
    .status-user { background-color: #1e3a8a; color: #93c5fd; border: 1px solid #2563eb; }

    div[data-testid="stSidebar"] div.stButton > button {
        text-align: left;
        justify-content: flex-start;
        border-radius: 8px;
        font-size: 0.88rem;
        font-weight: 500;
        padding: 8px 12px;
        margin-bottom: 4px;
        border: 1px solid #e2e8f0;
        background-color: #ffffff;
        color: #1e293b;
        transition: all 0.15s ease;
    }
    div[data-testid="stSidebar"] div.stButton > button:hover {
        background-color: #f1f5f9;
        border-color: #cbd5e1;
        color: #0f172a;
    }
    div[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
        background: #1e293b !important;
        color: #ffffff !important;
        border-color: #1e293b !important;
        font-weight: 600 !important;
        box-shadow: 0 2px 4px -1px rgba(0, 0, 0, 0.15);
    }
</style>
""", unsafe_allow_html=True)


# ----------------- 3. AUTHENTICATION & SESSION ENGINE -----------------
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
    if "current_contact" not in st.session_state:
        st.session_state.current_contact = ""
    if "current_emergency" not in st.session_state:
        st.session_state.current_emergency = ""
    if "current_photo" not in st.session_state:
        st.session_state.current_photo = ""
    if "active_module" not in st.session_state:
        st.session_state.active_module = None
    if "show_profile_box" not in st.session_state:
        st.session_state.show_profile_box = False
    if "selected_worker_drilldown" not in st.session_state:
        st.session_state.selected_worker_drilldown = None

init_session()

def login_screen():
    c1, c2, c3 = st.columns([1, 1.3, 1])
    with c2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="text-align: center; margin-bottom: 20px;">
                <div style="display: flex; justify-content: center; margin-bottom: 8px;">{INDU_SAO_SVG_LOGO}</div>
                <h2 style="margin: 0; padding: 0; color: #0f172a; font-weight: 700; font-size: 1.6rem;">{COMPANY_HEADER['NAME']}</h2>
                <div style="font-size: 0.85rem; color: #475569; font-weight: 600; margin-top: 4px;">{COMPANY_HEADER['GST']}</div>
                <div style="font-size: 0.72rem; color: #059669; font-weight: 600; margin-top: 2px;">{COMPANY_HEADER['ISO']}</div>
            </div>
            """, 
            unsafe_allow_html=True
        )

        with st.container(border=True):
            st.markdown("##### Portal Authentication")
            st.caption("Enter authorized enterprise credentials to access the workspace")
            
            username = st.text_input("Username").strip()
            password = st.text_input("Password", type="password").strip()
            submitted = st.button("Sign In", use_container_width=True, type="primary")

            if submitted:
                if not username or not password:
                    st.warning("Please provide both Username and Password.")
                    return

                u_lower = username.lower()
                configured_master_pass = st.secrets.get("app_password", DEFAULT_ADMIN_PASS)

                if u_lower == DEFAULT_ADMIN_USER.lower() and password == configured_master_pass:
                    st.session_state.user_authenticated = True
                    st.session_state.current_user = DEFAULT_ADMIN_USER
                    st.session_state.current_name = DEFAULT_ADMIN_NAME
                    st.session_state.current_role = "ADMIN"
                    st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                    st.session_state.active_module = "👷 Worker Attendance"
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
                        st.session_state.current_contact = str(u_row.get("Contact_No", ""))
                        st.session_state.current_emergency = str(u_row.get("Emergency_Contact", ""))
                        st.session_state.current_photo = str(u_row.get("Photo_Base64", ""))

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

                        mods = set([TAB_REGISTRY.get(t) for t in st.session_state.allowed_tabs if t in TAB_REGISTRY])
                        if "Attendance" in mods:
                            st.session_state.active_module = "👷 Worker Attendance"
                        elif "Billing" in mods:
                            st.session_state.active_module = "📑 Tata Steel Billing Hub"
                        elif "Fuel" in mods:
                            st.session_state.active_module = "⛽ Site Fuel Record"

                        st.rerun()
                    else:
                        st.error("Invalid credentials provided.")
                else:
                    st.error("User profile not found.")

if not st.session_state.user_authenticated:
    login_screen()
    st.stop()


# ----------------- 4. SIDEBAR NAVIGATION -----------------
with st.sidebar:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #e2e8f0;">
            <div>{INDU_SAO_SVG_LOGO}</div>
            <div>
                <div style="font-size: 0.98rem; font-weight: 700; color: #0f172a; line-height: 1.1;">{COMPANY_HEADER['NAME']}</div>
                <div style="font-size: 0.68rem; color: #475569; font-weight: 600;">Vendor Code: ID47</div>
                <div style="font-size: 0.62rem; color: #059669; font-weight: 600;">{COMPANY_HEADER['ISO']}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown("<div style='margin-bottom: 8px; font-size: 0.72rem; font-weight: 700; color: #64748b; letter-spacing: 0.06em; text-transform: uppercase;'>OPERATIONAL WORKSPACES</div>", unsafe_allow_html=True)

    available_modules = set()
    for tab_name in st.session_state.allowed_tabs:
        available_modules.add(TAB_REGISTRY.get(tab_name))

    module_options = []
    if "Attendance" in available_modules:
        module_options.append("👷 Worker Attendance")
    if "Billing" in available_modules:
        module_options.append("📑 Tata Steel Billing Hub")
    if "Fuel" in available_modules:
        module_options.append("⛽ Site Fuel Record")
    if st.session_state.current_role == "ADMIN":
        module_options.append("⚙️ Admin & User Permissions")

    if not module_options:
        st.warning("No operational modules have been assigned to this account.")
        if st.button("Reset Session & Sign In", use_container_width=True, type="primary"):
            st.session_state.user_authenticated = False
            st.rerun()
        st.stop()

    if st.session_state.active_module not in module_options:
        st.session_state.active_module = module_options[0]

    for mod_title in module_options:
        is_active = (st.session_state.active_module == mod_title)
        btn_type = "primary" if is_active else "secondary"
        if st.button(mod_title, key=f"nav_tile_{mod_title}", use_container_width=True, type=btn_type):
            st.session_state.active_module = mod_title
            st.session_state.show_profile_box = False
            st.session_state.selected_worker_drilldown = None
            st.rerun()

    selected_module = st.session_state.active_module


# ----------------- 5. EXECUTIVE TOP RESPONSIVE HEADER -----------------
user_display_name = st.session_state.get("current_name") or st.session_state.get("current_user") or "User"
name_parts = str(user_display_name).strip().split()
initials = "".join([p[0] for p in name_parts[:2] if p]).upper() if name_parts else "US"
badge_class = "status-admin" if st.session_state.current_role == "ADMIN" else "status-user"

avatar_html = f'<div class="top-user-avatar">{initials}</div>'
if st.session_state.get("current_photo"):
    avatar_html = f'<img src="data:image/png;base64,{st.session_state.current_photo}" class="top-user-avatar" />'

h_col1, h_col2, h_col3 = st.columns([3.0, 1.4, 0.8], gap="small")
with h_col1:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 10px; padding-top: 4px;">
            <div style="display: flex; align-items: center;">{INDU_SAO_SVG_LOGO}</div>
            <div>
                <span style="font-weight: 700; font-size: 1.05rem; color: #0f172a;">Site Operations ERP</span>
                <span style="color: #cbd5e1; margin: 0 6px;">|</span>
                <span style="font-size: 0.82rem; color: #475569; font-weight: 500;">Tata Steel West Bokaro (ID47)</span>
            </div>
        </div>
        """, 
        unsafe_allow_html=True
    )

with h_col2:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding-top: 3px;">
            {avatar_html}
            <div style="text-align: left; line-height: 1.2;">
                <div style="font-weight: 600; font-size: 0.85rem; color: #0f172a;">{user_display_name}</div>
                <div style="margin-top: 1px;"><span class="status-badge {badge_class}">{st.session_state.current_role}</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with h_col3:
    btn_c1, btn_c2 = st.columns(2)
    with btn_c1:
        if st.button("⚙️", help="Edit Profile", use_container_width=True, key="hdr_gear"):
            st.session_state.show_profile_box = not st.session_state.show_profile_box
            st.rerun()
    with btn_c2:
        if st.button("⏻", help="Sign Out", use_container_width=True, key="hdr_pwr"):
            st.session_state.user_authenticated = False
            st.rerun()

st.markdown("<hr style='margin-top: 6px; margin-bottom: 14px; border: none; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)


# ----------------- 6. REUSABLE LETTERHEAD & BANK LETTER GENERATOR -----------------
def generate_bank_authority_letter_html(bank_batch_type, records_list, cheque_no, cheque_date):
    """
    Renders formal print-ready covering authority letter and annexure
    for Bank of India or Other Banks (NEFT) matching official letterhead.
    """
    total_amount = sum([r.get("AMOUNT", 0.0) for r in records_list])
    
    rows_html = ""
    for idx, r in enumerate(records_list, start=1):
        amt_fmt = f"{r.get('AMOUNT', 0.0):.0f}"
        rows_html += f"""
        <tr>
            <td style="text-align: center;">{idx}</td>
            <td style="text-align: left; font-weight: 600;">{r.get('NAME', '')}</td>
            <td style="text-align: right; font-weight: 700;">{amt_fmt}</td>
            <td style="text-align: center; font-family: monospace;">{r.get('BENEFICIARY ACCOUNT NO', '')}</td>
            <td style="text-align: center; font-family: monospace;">{r.get('IFSC CODE', '')}</td>
            <td style="text-align: left;">{r.get('BANK & BRANCH DETAILS', '')}</td>
        </tr>
        """

    letter_title = "BANK OF INDIA INTERNAL TRANSFER" if bank_batch_type == "BOI" else "NEFT / OTHER BANKS TRANSFER"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{letter_title} - {COMPANY_HEADER['NAME']}</title>
<style>
    @page {{
        size: A4 portrait;
        margin: 12mm 15mm;
    }}
    body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
        color: #000;
        margin: 0;
        padding: 0;
        background: #fff;
    }}
    .letterhead {{
        border-bottom: 2px solid #0b2a75;
        padding-bottom: 8px;
        margin-bottom: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
    }}
    .company-title {{
        font-size: 22pt;
        font-weight: 800;
        color: #0b2a75;
        margin: 0;
        line-height: 1.1;
    }}
    .company-details {{
        font-size: 7.8pt;
        line-height: 1.35;
        color: #334155;
    }}
    .iso-badge {{
        font-size: 8pt;
        font-weight: 700;
        color: #064e3b;
        margin-top: 4px;
    }}
    .req-header {{
        text-align: center;
        font-size: 9.5pt;
        font-weight: 700;
        text-decoration: underline;
        margin-bottom: 10px;
    }}
    .req-body {{
        font-size: 8.8pt;
        line-height: 1.45;
        margin-bottom: 12px;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 8pt;
        margin-bottom: 14px;
    }}
    th, td {{
        border: 1px solid #000;
        padding: 4px 6px;
    }}
    th {{
        background-color: #f1f5f9;
        font-weight: 700;
        text-align: center;
        font-size: 7.8pt;
    }}
    .footer-sign {{
        margin-top: 25px;
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        page-break-inside: avoid;
    }}
    .sign-box {{
        text-align: left;
        font-size: 9pt;
        font-weight: 700;
    }}
    @media print {{
        body {{ background: transparent; }}
        .no-print {{ display: none; }}
    }}
</style>
</head>
<body onload="window.print()">
    <div class="letterhead">
        <div>
            <div class="company-title">{COMPANY_HEADER['NAME']}</div>
            <div class="company-details">
                {COMPANY_HEADER['ADDRESS']}<br>
                {COMPANY_HEADER['PIN_CALL']}<br>
                {COMPANY_HEADER['EMAIL']}<br>
                {COMPANY_HEADER['GST']}
            </div>
            <div class="iso-badge">{COMPANY_HEADER['ISO']}</div>
        </div>
        <div style="text-align: right;">
            <div style="font-weight: 700; font-size: 9pt;">BATCH: {letter_title}</div>
            <div style="font-size: 8.5pt; color: #475569;">Generated: {datetime.date.today().strftime('%d.%m.%Y')}</div>
        </div>
    </div>

    <div class="req-header">BANK DETAILS<br>SUB: Transfer of payment from our account to below A/C</div>

    <div class="req-body">
        <b>Dear Sir,</b><br>
        We request you to transfer <b>{total_amount:.0f}/-</b> from our current A/C NO- <b>{COMPANY_HEADER['CURRENT_AC']}</b> to following account as per the payment details given ch.no. <b>{cheque_no}</b> Dated: <b>{cheque_date}</b>
    </div>

    <table>
        <thead>
            <tr>
                <th style="width: 5%;">SL.NO</th>
                <th style="width: 26%;">NAME</th>
                <th style="width: 12%;">AMOUNT</th>
                <th style="width: 20%;">BENEFICIARY ACCOUNT NO</th>
                <th style="width: 14%;">IFSC CODE</th>
                <th style="width: 23%;">BANK & BRANCH DETAILS</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
            <tr style="font-weight: 800; background-color: #f8fafc;">
                <td colspan="2" style="text-align: right;">TOTAL:</td>
                <td style="text-align: right;">{total_amount:.0f}</td>
                <td colspan="3"></td>
            </tr>
        </tbody>
    </table>

    <div class="footer-sign">
        <div>
            <div style="font-size: 8pt; font-weight: 600;">THANKING YOU</div>
        </div>
        <div class="sign-box">
            <div>{COMPANY_HEADER['NAME']}</div>
            <div style="height: 45px;"></div>
            <div>Proprietor / Authorized Signatory</div>
        </div>
    </div>
</body>
</html>"""


# ----------------- 7. OPERATIONAL WORKSPACE: WORKER ATTENDANCE & PAYROLL -----------------
if selected_module == "👷 Worker Attendance":
    st.markdown("### 👷 Site Muster Roll & Worker Management Hub")
    st.caption("Fleet Crew, Civil Trades, Field Attendance, Tata Form XVII Reconciliation & Bank Payout Sheets")

    preferred_order = [
        "➕ Employee Enrolment",
        "📋 Employee Master Register",
        "Daily Muster Roll (Field Attendance)",
        "Advance & Expense Register",
        "Monthly Wages & Payout Summary"
    ]
    
    # Strictly enforce assigned granular permissions
    att_tabs = [t for t in preferred_order if t in st.session_state.allowed_tabs]

    if not att_tabs:
        st.warning("No accessible modules assigned in this workspace. Contact system administrator.")
    else:
        active_a_tabs = st.tabs(att_tabs)

        for i, tab_title in enumerate(att_tabs):
            with active_a_tabs[i]:
                
                # ----------------- TAB A: EMPLOYEE ENROLMENT -----------------
                if tab_title == "➕ Employee Enrolment":
                    st.markdown("##### ➕ Dedicated Employee Enrolment Portal")
                    st.caption("Onboard workforce members with full identity and bank account mapping")

                    entry_mode = st.radio("Enrolment Method", ["Single Employee Form", "Bulk Excel/CSV Upload"], horizontal=True)

                    if entry_mode == "Single Employee Form":
                        c_form, c_info = st.columns([1.3, 0.7], gap="large")
                        with c_form:
                            with st.container(border=True):
                                st.markdown("###### New Employee Onboarding")
                                w_df_current = load_sheet_df("worker_master")
                                
                                w_div = st.selectbox("Operational Division", ["Fleet Operations", "Civil Construction"], key="enrol_div")
                                next_wid = generate_next_worker_id(w_div, w_df_current)
                                st.info(f"System Assigned ID: **{next_wid}** (Auto-Generated)")

                                w_name = st.text_input("Full Official Name", key="enrol_name").strip()
                                w_ref = st.text_input("Identity Verification Reference", key="enrol_ref", help="Distinguishes duplicate names").strip()

                                role_choices = FLEET_ROLES if w_div == "Fleet Operations" else CIVIL_ROLES
                                sel_role = st.selectbox("Designation / Role", role_choices, key="enrol_role")
                                
                                custom_role = ""
                                if sel_role == "Other (Custom Entry)":
                                    custom_role = st.text_input("Specify Custom Role", key="enrol_custom_role").strip()

                                w_wage_type = st.selectbox("Wage Model", ["DAILY_WAGE", "MONTHLY_FIXED"], key="enrol_wage_type")
                                
                                if w_wage_type == "DAILY_WAGE":
                                    w_rate = st.number_input("Daily Wage Rate (₹ / Shift)", min_value=100.0, value=650.0, step=50.0, key="enrol_rate_daily")
                                else:
                                    w_rate = st.number_input("Monthly Fixed Base Salary (₹ / Month)", min_value=1000.0, value=18000.0, step=500.0, key="enrol_rate_monthly")

                                w_contact = st.text_input("Contact Mobile Number", key="enrol_contact").strip()

                                st.markdown("###### 🏦 Bank Transfer Particulars")
                                b_acc = st.text_input("Bank Account Number", key="enrol_b_acc").strip()
                                b_ifsc = st.text_input("Bank IFSC Code (e.g. BKID0004869, SBIN0007842)", key="enrol_b_ifsc").strip().upper()
                                b_branch = st.text_input("Bank & Branch Details (e.g. BOI SARUBERA, SBI RAMGARH)", key="enrol_b_branch").strip().upper()

                                w_status = st.selectbox("Initial Employment Status", ["ACTIVE", "INACTIVE"], key="enrol_status")

                                if st.button("Save & Commit Employee Record", type="primary", use_container_width=True):
                                    final_role = custom_role if sel_role == "Other (Custom Entry)" and custom_role else sel_role
                                    if not w_name:
                                        st.error("Employee Name is mandatory.")
                                    elif sel_role == "Other (Custom Entry)" and not custom_role:
                                        st.error("Please specify custom role.")
                                    else:
                                        w_df = load_sheet_df("worker_master")
                                        for col in WORKER_MASTER_COLS:
                                            if col not in w_df.columns:
                                                w_df[col] = ""

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
                                            st.success(f"Employee {w_name} ({next_wid}) successfully enrolled.")
                                            st.rerun()

                        with c_info:
                            with st.container(border=True):
                                st.markdown("###### ℹ️ Bank Transfer Routing Rules")
                                st.caption("• **Bank of India (BOI):** IFSC starting with `BKID` auto-routes to BOI Covering Sheet.")
                                st.caption("• **Other Banks:** Non-BOI IFSC codes auto-route to NEFT Covering Sheet.")
                                st.caption("• **Cash Wage Handling:** Leave Bank Account blank if employee receives cash payouts.")

                    else:
                        with st.container(border=True):
                            st.markdown("###### 📁 Bulk Upload Employee Roster (CSV / Excel)")
                            st.caption("Synchronize bulk workforce records including bank particulars.")

                            sample_df = pd.DataFrame([
                                {
                                    "Full_Name": "BAHAMUNI DEVI", "Govt_ID_Ref": "Ref-21", "Division": "Civil Construction",
                                    "Designation": "Civil Construction Helper", "Wage_Type": "DAILY_WAGE", "Base_Rate": 565.0,
                                    "Contact_No": "9876543210", "Bank_Account_No": "486910110006418", "Bank_IFSC_Code": "BKID0004869",
                                    "Bank_Branch_Details": "BOI SARUBERA", "Status": "ACTIVE"
                                },
                                {
                                    "Full_Name": "ANJALI DEVI", "Govt_ID_Ref": "Ref-67", "Division": "Civil Construction",
                                    "Designation": "Civil Construction Helper", "Wage_Type": "DAILY_WAGE", "Base_Rate": 565.0,
                                    "Contact_No": "9876543211", "Bank_Account_No": "36316450526", "Bank_IFSC_Code": "SBIN0009013",
                                    "Bank_Branch_Details": "SBI LALPANIA", "Status": "ACTIVE"
                                }
                            ])

                            st.download_button(
                                "📥 Download Bulk Import Template (CSV)",
                                data=sample_df.to_csv(index=False).encode('utf-8'),
                                file_name="Employee_Bulk_Bank_Template.csv",
                                mime="text/csv"
                            )

                            up_file = st.file_uploader("Upload CSV / XLSX File", type=["csv", "xlsx"])
                            if up_file:
                                b_df = pd.read_csv(up_file) if up_file.name.endswith(".csv") else pd.read_excel(up_file)
                                b_df.columns = [str(c).strip() for c in b_df.columns]
                                st.dataframe(b_df.head(4), use_container_width=True)

                                if st.button("Commit Bulk File", type="primary", use_container_width=True):
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
                                        st.success(f"{len(appended_rows)} workers synchronized successfully.")
                                        st.rerun()

                # ----------------- TAB B: EMPLOYEE MASTER REGISTER -----------------
                elif tab_title == "📋 Employee Master Register":
                    st.markdown("##### 📋 Employee Master Register & Bank Mapping")
                    st.caption("Active workforce directory with live bank verification status and ledger drilldowns")

                    w_table = load_sheet_df("worker_master")
                    if w_table.empty or "Worker_ID" not in w_table.columns:
                        st.info("No employee records found.")
                    else:
                        flt_c1, flt_c2, flt_c3 = st.columns([1.2, 1.2, 2.0])
                        with flt_c1:
                            f_div = st.selectbox("Division Filter", ["All", "Fleet Operations", "Civil Construction"], key="reg_div_f")
                        with flt_c2:
                            f_bank = st.selectbox("Bank Routing Filter", ["All", "BOI Accounts", "Other Banks (NEFT)", "Cash (No Bank)"], key="reg_bank_f")
                        with flt_c3:
                            f_search = st.text_input("Search Name, ID, or Account", placeholder="Filter by keyword...").strip().lower()

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

                        st.dataframe(
                            disp_table[["Worker_ID", "Full_Name", "Division", "Designation", "Wage_Type", "Base_Rate", "Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details"]],
                            use_container_width=True,
                            hide_index=True
                        )

                # ----------------- TAB C: DAILY MUSTER ROLL -----------------
                elif tab_title == "Daily Muster Roll (Field Attendance)":
                    st.markdown("##### 📅 Daily Muster Roll & Shift Attendance")
                    st.caption("Record field shift duty with location tagging (Tata Plant vs Private Works)")

                    w_master = load_sheet_df("worker_master")
                    if w_master.empty or "Worker_ID" not in w_master.columns:
                        st.warning("No employees available to mark attendance.")
                    else:
                        active_workers = w_master[w_master["Status"] == "ACTIVE"].copy()

                        top_m1, top_m2, top_m3 = st.columns([1.1, 1.2, 1.2])
                        with top_m1:
                            muster_date = st.date_input("Attendance Date", value=datetime.date.today(), key="muster_dt_pick")
                            muster_date_str = muster_date.strftime("%Y-%m-%d")
                        with top_m2:
                            sel_division = st.selectbox("Operational Division", ["All Divisions", "Fleet Operations", "Civil Construction"], key="muster_div_filter")
                        with top_m3:
                            st.metric("Total Active Crew", len(active_workers))

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
                                def_att, def_ot, def_loc, def_adv, def_mode, def_rem = "P", 0.0, "Tata Site (In-Plant)", 0.0, "Cash", ""

                                if not day_records.empty and "Worker_ID" in day_records.columns:
                                    match_prev = day_records[day_records["Worker_ID"].astype(str) == wid]
                                    if not match_prev.empty:
                                        r0 = match_prev.iloc[0]
                                        def_att = str(r0.get("Attendance", "P"))
                                        def_ot = float(r0.get("OT_Hours", 0.0) or 0.0)
                                        def_loc = str(r0.get("Duty_Location", "Tata Site (In-Plant)"))
                                        def_adv = float(r0.get("Advance_Amount", 0.0) or 0.0)
                                        def_mode = str(r0.get("Payment_Mode", "Cash"))
                                        def_rem = str(r0.get("Remarks", ""))

                                st.session_state[cache_key][wid] = {
                                    "selected": True, "att": def_att, "ot": def_ot, "loc": def_loc,
                                    "adv": def_adv, "mode": def_mode, "rem": def_rem
                                }

                        # Bulk Actions
                        with st.container(border=True):
                            st.markdown("###### ⚡ Bulk Actions")
                            b_c1, b_c2, b_c3 = st.columns(3)
                            with b_c1:
                                if st.button("Mark All Present (Tata Site)", use_container_width=True):
                                    for wid in active_workers["Worker_ID"].astype(str):
                                        if wid in st.session_state[cache_key]:
                                            st.session_state[cache_key][wid]["att"] = "P"
                                            st.session_state[cache_key][wid]["loc"] = "Tata Site (In-Plant)"
                                    st.rerun()
                            with b_c2:
                                if st.button("Mark All Present (Private Work)", use_container_width=True):
                                    for wid in active_workers["Worker_ID"].astype(str):
                                        if wid in st.session_state[cache_key]:
                                            st.session_state[cache_key][wid]["att"] = "P"
                                            st.session_state[cache_key][wid]["loc"] = "Private / Internal"
                                    st.rerun()
                            with b_c3:
                                if st.button("Mark All Absent", use_container_width=True):
                                    for wid in active_workers["Worker_ID"].astype(str):
                                        if wid in st.session_state[cache_key]:
                                            st.session_state[cache_key][wid]["att"] = "A"
                                    st.rerun()

                        st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
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
                                    st.markdown(f"**{wname}** (`{wid}`) • <span style='font-size: 0.8rem; color: #2563eb;'>{wdiv}</span>", unsafe_allow_html=True)
                                    st.caption(f"Role: {wrole}")

                                    r_c1, r_c2, r_c3 = st.columns([1.2, 1.2, 1.0])
                                    with r_c1:
                                        cur_state["att"] = st.selectbox("Status", ["P", "HD", "A", "WO"], index=["P", "HD", "A", "WO"].index(cur_state.get("att", "P")), key=f"att_s_{wid}")
                                    with r_c2:
                                        cur_state["loc"] = st.selectbox("Location", ["Tata Site (In-Plant)", "Private / Internal"], index=["Tata Site (In-Plant)", "Private / Internal"].index(cur_state.get("loc", "Tata Site (In-Plant)")), key=f"loc_s_{wid}")
                                    with r_c3:
                                        cur_state["ot"] = st.number_input("OT (Hrs)", min_value=0.0, max_value=16.0, step=0.5, value=float(cur_state.get("ot", 0.0)), key=f"ot_s_{wid}")

                            st.session_state[cache_key][wid] = cur_state

                        st.markdown("<div style='height: 14px;'></div>", unsafe_allow_html=True)
                        if st.button("💾 Save Daily Muster Roll", type="primary", use_container_width=True):
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
                            for col in MUSTER_ROLL_COLS:
                                if col not in all_m.columns:
                                    all_m[col] = ""

                            if not all_m.empty and "Date" in all_m.columns and "Worker_ID" in all_m.columns:
                                w_ids = [r["Worker_ID"] for r in marked_rows]
                                cond = (all_m["Date"].astype(str) == muster_date_str) & (all_m["Worker_ID"].astype(str).isin(w_ids))
                                all_m = all_m[~cond]

                            updated_m = pd.concat([all_m, pd.DataFrame(marked_rows)], ignore_index=True)
                            if save_sheet_df("muster_roll", updated_m[MUSTER_ROLL_COLS]):
                                st.success("Daily Muster Roll synchronized.")
                                st.rerun()

                # ----------------- TAB D: ADVANCE REGISTER -----------------
                elif tab_title == "Advance & Expense Register":
                    st.markdown("##### 💵 Advance & Field Expense Register")
                    st.caption("Record site allowances and lump-sum advances")

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

                                    if st.form_submit_button("Record Advance", type="primary", use_container_width=True):
                                        w_id, w_name, w_div = worker_options[sel_w]
                                        adv_df = load_sheet_df("worker_advances")
                                        for c in ADVANCE_LOG_COLS:
                                            if c not in adv_df.columns:
                                                adv_df[c] = ""
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
                                            st.success("Advance recorded successfully.")
                                            st.rerun()

                    with c_adv2:
                        with st.container(border=True):
                            st.markdown("###### 📜 Advance Ledger")
                            adv_history = load_sheet_df("worker_advances")
                            if not adv_history.empty and "Txn_ID" in adv_history.columns:
                                st.dataframe(adv_history[["Date", "Worker_Name", "Amount", "Payment_Mode", "Supervisor", "Remarks"]].tail(15), use_container_width=True, hide_index=True)
                            else:
                                st.info("No recorded transactions.")

                # ----------------- TAB E: MONTHLY WAGES & FORM XVII BANK AUTOMATION -----------------
                elif tab_title == "Monthly Wages & Payout Summary":
                    st.markdown("##### 📊 Monthly Wages, Form XVII Audit & Automated Bank Sheets")
                    st.caption("Reconcile field attendance with Tata Steel Form XVII and generate BOI / NEFT covering letters")

                    w_master = load_sheet_df("worker_master")
                    muster_df = load_sheet_df("muster_roll")
                    tata_df = load_sheet_df("tata_form_xvii")

                    # Period Selector
                    f_c1, f_c2 = st.columns([1, 1])
                    with f_c1:
                        sel_year = st.selectbox("Year", [2025, 2026, 2027], index=1)
                    with f_c2:
                        sel_month = st.selectbox("Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1)

                    wage_period_str = f"{sel_month[:3].upper()}-{sel_year}"

                    # Form XVII Importer
                    with st.expander("📥 Import Tata Steel FORM XVII (Register of Wages)", expanded=tata_df.empty):
                        st.markdown(f"Upload official **Form XVII [Rule 78(2)(a)]** for period **{wage_period_str}**")
                        xvii_file = st.file_uploader("Upload Form XVII (CSV / Excel)", type=["csv", "xlsx"])

                        sample_xvii = pd.DataFrame([
                            {"Name_of_Workman": "Bahamuni Devi", "Serial_No": "21", "No_of_days_worked": 14, "Net_Amount_Paid": 6901.0},
                            {"Name_of_Workman": "Anjali Devi", "Serial_No": "67", "No_of_days_worked": 3, "Net_Amount_Paid": 1479.0},
                            {"Name_of_Workman": "Devnaryan Soren", "Serial_No": "39", "No_of_days_worked": 25, "Net_Amount_Paid": 12816.0}
                        ])
                        st.download_button("📥 Sample Form XVII CSV", sample_xvii.to_csv(index=False).encode('utf-8'), "Sample_Form_XVII.csv", "text/csv")

                        if xvii_file:
                            x_df = pd.read_csv(xvii_file) if xvii_file.name.endswith(".csv") else pd.read_excel(xvii_file)
                            x_df.columns = [str(c).strip().replace(" ", "_") for c in x_df.columns]
                            st.write(f"Parsed Preview ({len(x_df)} workers):")
                            st.dataframe(x_df.head(4), use_container_width=True)

                            if st.button("Commit Form XVII Compliance Data", type="primary"):
                                for c in TATA_COMPLIANCE_COLS:
                                    if c not in tata_df.columns:
                                        tata_df[c] = ""

                                new_records = []
                                for _, xr in x_df.iterrows():
                                    w_name_raw = str(xr.get("Name_of_Workman", xr.get("Name", ""))).strip()
                                    appr_days = float(xr.get("No._of_days_worked", xr.get("No_of_days_worked", 0.0)) or 0.0)
                                    net_paid = float(xr.get("Net_Amount_Paid", xr.get("Net_Amount", 0.0)) or 0.0)

                                    # Match to master by name
                                    matched_wid = ""
                                    if not w_master.empty and "Full_Name" in w_master.columns:
                                        match_w = w_master[w_master["Full_Name"].astype(str).str.lower() == w_name_raw.lower()]
                                        if not match_w.empty:
                                            matched_wid = str(match_w.iloc[0]["Worker_ID"])

                                    new_records.append({
                                        "Wages_Period": wage_period_str,
                                        "Worker_ID": matched_wid,
                                        "Worker_Name": w_name_raw,
                                        "Approved_Days": appr_days,
                                        "Net_Amount_Paid": net_paid,
                                        "Uploaded_At": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    })

                                # Replace current month's records
                                if not tata_df.empty and "Wages_Period" in tata_df.columns:
                                    tata_df = tata_df[tata_df["Wages_Period"] != wage_period_str]

                                final_tata = pd.concat([tata_df, pd.DataFrame(new_records)], ignore_index=True)
                                if save_sheet_df("tata_form_xvii", final_tata[TATA_COMPLIANCE_COLS]):
                                    st.success(f"Form XVII for {wage_period_str} imported successfully.")
                                    st.rerun()

                    # Audit Cockpit & Bank Batch Engine
                    st.markdown("<hr style='margin: 12px 0; border: none; border-top: 1px solid #cbd5e1;' />", unsafe_allow_html=True)
                    st.markdown("###### 🔍 Attendance Reconciliation Cockpit (Field Duty vs Form XVII)")

                    tata_cur = tata_df[tata_df["Wages_Period"] == wage_period_str] if not tata_df.empty and "Wages_Period" in tata_df.columns else pd.DataFrame()

                    month_num = list(calendar.month_name).index(sel_month)
                    d_start = f"{sel_year}-{month_num:02d}-01"
                    d_end = f"{sel_year}-{month_num:02d}-{calendar.monthrange(sel_year, month_num)[1]:02d}"

                    # Calculate field days
                    recon_rows = []
                    boi_batch = []
                    neft_batch = []

                    if not w_master.empty:
                        for _, w in w_master[w_master["Status"] == "ACTIVE"].iterrows():
                            wid = str(w["Worker_ID"])
                            wname = str(w["Full_Name"])
                            b_acc = str(w.get("Bank_Account_No", "")).strip()
                            b_ifsc = str(w.get("Bank_IFSC_Code", "")).strip().upper()
                            b_branch = str(w.get("Bank_Branch_Details", "")).strip().upper()

                            # Supervisor days (Tata Site)
                            sup_days = 0.0
                            if not muster_df.empty and "Date" in muster_df.columns and "Worker_ID" in muster_df.columns:
                                w_m = muster_df[(muster_df["Worker_ID"].astype(str) == wid) & 
                                               (muster_df["Date"].astype(str) >= d_start) & 
                                               (muster_df["Date"].astype(str) <= d_end) & 
                                               (muster_df["Duty_Location"] == "Tata Site (In-Plant)")]
                                sup_days = len(w_m[w_m["Attendance"] == "P"]) + (len(w_m[w_m["Attendance"] == "HD"]) * 0.5)

                            # Tata Approved Data
                            tata_match = tata_cur[tata_cur["Worker_Name"].astype(str).str.lower() == wname.lower()]
                            tata_approved_days = float(tata_match.iloc[0]["Approved_Days"]) if not tata_match.empty else 0.0
                            tata_net_paid = float(tata_match.iloc[0]["Net_Amount_Paid"]) if not tata_match.empty else 0.0

                            status_badge = "🟢 Matched"
                            if tata_approved_days == 0 and sup_days > 0:
                                status_badge = "🔴 No Punch (Field Only)"
                            elif tata_approved_days != sup_days:
                                status_badge = f"🟡 Variance ({sup_days - tata_approved_days:+.1f} d)"

                            # Zero Compliance Breach Lock
                            final_bank_transfer = tata_net_paid

                            recon_rows.append({
                                "Worker ID": wid,
                                "Name": wname,
                                "Field Days": sup_days,
                                "Tata Approved Days": tata_approved_days,
                                "Variance Status": status_badge,
                                "Compliance Locked Net (₹)": final_bank_transfer,
                                "IFSC Code": b_ifsc,
                                "Bank Details": b_branch
                            })

                            # Routing
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

                    st.markdown("<hr style='margin: 16px 0; border: none; border-top: 1px solid #cbd5e1;' />", unsafe_allow_html=True)
                    st.markdown("###### 🏦 Automated Bank Covering Letters & Payment Batches")

                    p_c1, p_c2, p_c3 = st.columns([1.2, 1.2, 1.6])
                    with p_c1:
                        chq_no = st.text_input("Cheque Number", value="238883", key="bank_chq_no").strip()
                    with p_c2:
                        chq_date = st.text_input("Cheque Date (DD.MM.YYYY)", value=datetime.date.today().strftime("%d.%m.%Y"), key="bank_chq_dt").strip()

                    tot_boi = sum([r["AMOUNT"] for r in boi_batch])
                    tot_neft = sum([r["AMOUNT"] for r in neft_batch])

                    m1, m2, m3 = st.columns(3)
                    m1.metric("Bank of India Payout", f"₹{tot_boi:,.0f}", f"{len(boi_batch)} Beneficiaries")
                    m2.metric("Other Banks (NEFT) Payout", f"₹{tot_neft:,.0f}", f"{len(neft_batch)} Beneficiaries")
                    m3.metric("Total Compliance Transfer", f"₹{(tot_boi + tot_neft):,.0f}", "Zero Breach Verified")

                    dl_c1, dl_c2 = st.columns(2)
                    with dl_c1:
                        if boi_batch:
                            boi_html = generate_bank_authority_letter_html("BOI", boi_batch, chq_no, chq_date)
                            st.download_button(
                                label=f"📄 Download Bank of India Covering Letter ({len(boi_batch)} Workers • ₹{tot_boi:,.0f})",
                                data=boi_html,
                                file_name=f"BOI_Transfer_Letter_{wage_period_str}.html",
                                mime="text/html",
                                type="primary",
                                use_container_width=True
                            )
                        else:
                            st.info("No Bank of India transfers in this batch.")

                    with dl_c2:
                        if neft_batch:
                            neft_html = generate_bank_authority_letter_html("NEFT", neft_batch, chq_no, chq_date)
                            st.download_button(
                                label=f"📄 Download Other Banks (NEFT) Letter ({len(neft_batch)} Workers • ₹{tot_neft:,.0f})",
                                data=neft_html,
                                file_name=f"NEFT_Transfer_Letter_{wage_period_str}.html",
                                mime="primary" if not boi_batch else "secondary",
                                use_container_width=True
                            )
                        else:
                            st.info("No NEFT transfers in this batch.")


# --- HUB 2: TATA STEEL BILLING HUB ---
elif selected_module == "📑 Tata Steel Billing Hub":
    st.markdown("### 📑 Tata Steel Commercial & Billing Hub")
    st.caption("Fleet Duty Logs, RA Bill Calculations, Overtime Engine, and Asset Master")
    st.info("Commercial Billing modules active.")


# --- HUB 3: SITE FUEL RECORD ---
elif selected_module == "⛽ Site Fuel Record":
    st.markdown("### ⛽ Diesel & Fuel Management Hub")
    st.caption("Tanker Receipts, Daily Machine Issue Slips & Anti-Pilferage Audit")
    st.info("Diesel Management modules active.")


# --- HUB 4: ADMIN COMMAND & RBAC MANAGER (ADMIN ONLY) ---
elif selected_module == "⚙️ Admin & User Permissions" and st.session_state.current_role == "ADMIN":
    st.markdown("### ⚙️ Identity & Granular Access Management (RBAC)")
    st.caption("Provision credentials and configure exact module/tab access permissions per user")

    col_u1, col_u2 = st.columns([1.1, 1.9], gap="large")

    with col_u1:
        with st.container(border=True):
            st.markdown("##### ➕ Provision / Update User")
            
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

                st.markdown("**Granular Tab Permissions:**")
                selected_permissions = []
                
                with st.expander("Worker Attendance Modules (Granular Control)", expanded=True):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Attendance":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                with st.expander("Billing Modules", expanded=(new_role == "ADMIN")):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Billing":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                with st.expander("Fuel Control Modules", expanded=(new_role == "ADMIN")):
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
                        for col in USER_COLUMNS:
                            if col not in users_df.columns:
                                users_df[col] = ""

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
                            st.success(f"User account '{new_username}' saved.")
                            st.rerun()

    with col_u2:
        with st.container(border=True):
            st.markdown("##### 👥 Active System Users Ledger")
            users_table = load_sheet_df("app_users")
            if not users_table.empty and "Username" in users_table.columns:
                disp_cols = [c for c in ["Username", "Password", "Full_Name", "Role", "Status", "Allowed_Tabs"] if c in users_table.columns]
                st.dataframe(users_table[disp_cols], use_container_width=True, hide_index=True)
