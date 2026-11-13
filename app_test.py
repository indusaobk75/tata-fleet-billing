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

# Safe PDF library import
try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

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


# ----------------- 2. HIERARCHICAL WORKSPACES & RBAC -----------------
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
            ("Master Register", "📋 Employee Master Register"),
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

# ----------------- SLEEK MODERN PILL SIDEBAR STYLING -----------------
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
        padding-top: 2.8rem !important; 
        padding-bottom: 2.5rem !important;
        padding-left: 2.2rem !important;
        padding-right: 2.2rem !important;
        max-width: 100% !important;
    }

    /* Sleek Gemini-Style Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #f8fafc !important;
        border-right: 1px solid #e2e8f0 !important;
        width: 265px !important;
        min-width: 265px !important;
    }
    section[data-testid="stSidebar"] > div:first-child {
        padding-top: 1rem !important;
        padding-left: 0.75rem !important;
        padding-right: 0.75rem !important;
    }

    /* Compact spacing between sidebar elements */
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {
        gap: 0.25rem !important;
    }

    /* Clean Ghost Sidebar Buttons */
    section[data-testid="stSidebar"] div.stButton > button {
        width: 100% !important;
        min-height: 36px !important;
        height: 36px !important;
        text-align: left !important;
        justify-content: flex-start !important;
        background-color: transparent !important;
        border: none !important;
        box-shadow: none !important;
        border-radius: 8px !important;
        padding: 4px 12px !important;
        margin: 0px !important;
        font-size: 0.84rem !important;
        font-weight: 500 !important;
        color: #334155 !important;
        transition: background-color 0.15s ease, color 0.15s ease !important;
    }

    section[data-testid="stSidebar"] div.stButton > button:hover {
        background-color: #eef2f6 !important;
        color: #0f172a !important;
    }

    /* Active Pill Item */
    section[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
        background-color: #e0e7ff !important;
        color: #1e3a8a !important;
        font-weight: 600 !important;
        border-radius: 20px !important;
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
    if "active_workspace" not in st.session_state:
        st.session_state.active_workspace = "Attendance"
    if "active_subtab" not in st.session_state:
        st.session_state.active_subtab = "Daily Muster Roll (Field Attendance)"

init_session()

def login_screen():
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        st.markdown("<div style='height: 50px;'></div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="text-align: center; margin-bottom: 24px;">
                <div style="display: flex; justify-content: center; margin-bottom: 10px;">{INDU_SAO_SVG_LOGO}</div>
                <h2 style="margin: 0; padding: 0; color: #0f172a; font-weight: 700; font-size: 1.55rem; letter-spacing: -0.02em;">{COMPANY_HEADER['NAME']}</h2>
                <div style="font-size: 0.82rem; color: #64748b; font-weight: 500; margin-top: 4px;">{COMPANY_HEADER['GST']}</div>
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


# ----------------- 4. SLEEK SIDEBAR TREE NAVIGATION (SINGLE INSTANCE) -----------------
with st.sidebar:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 10px; padding: 2px 4px 12px 4px; margin-bottom: 8px; border-bottom: 1px solid #e2e8f0;">
            <div>{INDU_SAO_SVG_LOGO}</div>
            <div>
                <div style="font-size: 0.92rem; font-weight: 700; color: #0f172a;">{COMPANY_HEADER['NAME']}</div>
                <div style="font-size: 0.68rem; color: #64748b; font-weight: 500;">West Bokaro • ID47</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown("<div style='padding: 0 10px; margin-bottom: 4px; font-size: 0.66rem; font-weight: 600; color: #94a3b8; letter-spacing: 0.08em; text-transform: uppercase;'>Workspaces</div>", unsafe_allow_html=True)

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

        if st.button(f"{chevron} {ws_icon} {ws_title}", key=f"nav_ws_{ws_id}", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = ws_id
            if permitted_submenus and st.session_state.active_subtab not in [s[1] for s in permitted_submenus]:
                st.session_state.active_subtab = permitted_submenus[0][1]
            st.rerun()

        if is_open:
            for disp_name, reg_key in permitted_submenus:
                is_sub_active = (st.session_state.active_subtab == reg_key)
                btn_kind = "primary" if is_sub_active else "secondary"
                
                if st.button(f"•  {disp_name}", key=f"nav_sub_{reg_key}", use_container_width=True, type=btn_kind):
                    st.session_state.active_subtab = reg_key
                    st.rerun()

    if st.session_state.current_role == "ADMIN":
        st.markdown("<div style='margin-top: 8px; padding: 0 10px; font-size: 0.66rem; font-weight: 600; color: #94a3b8; letter-spacing: 0.08em; text-transform: uppercase;'>System</div>", unsafe_allow_html=True)
        is_admin_open = (st.session_state.active_workspace == "Admin")
        adm_chevron = "▾" if is_admin_open else "▸"
        
        if st.button(f"{adm_chevron} ⚙️ Access & Settings", key="nav_ws_admin_settings", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = "Admin"
            st.session_state.active_subtab = "User Accounts & RBAC"
            st.rerun()

        if is_admin_open:
            is_adm_sub = (st.session_state.active_subtab == "User Accounts & RBAC")
            if st.button("•  User Permissions (RBAC)", key="nav_sub_admin_rbac_item", use_container_width=True, type="primary" if is_adm_sub else "secondary"):
                st.session_state.active_subtab = "User Accounts & RBAC"
                st.rerun()


# ----------------- 5. CLEAN TOP BAR -----------------
user_display_name = st.session_state.get("current_name") or st.session_state.get("current_user") or "User"
name_parts = str(user_display_name).strip().split()
initials = "".join([p[0] for p in name_parts[:2] if p]).upper() if name_parts else "US"
badge_class = "status-admin" if st.session_state.current_role == "ADMIN" else "status-user"

avatar_html = f'<div class="top-user-avatar">{initials}</div>'

h_col1, h_col2, h_col3 = st.columns([3.0, 1.5, 0.7], gap="small")
with h_col1:
    ws_map = {"Attendance": "Worker Attendance", "Billing": "Tata Steel Billing", "Fuel": "Site Fuel Record", "Admin": "System Administration"}
    cur_ws_label = ws_map.get(st.session_state.active_workspace, "Workspace")
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 8px; padding-top: 6px;">
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
        <div style="display: flex; align-items: center; justify-content: flex-end; gap: 9px; padding-top: 3px;">
            {avatar_html}
            <div style="text-align: left; line-height: 1.2;">
                <div style="font-weight: 600; font-size: 0.8rem; color: #0f172a;">{user_display_name}</div>
                <div><span class="status-badge {badge_class}">{st.session_state.current_role}</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with h_col3:
    if st.button("Sign Out", help="End Session", use_container_width=True, key="hdr_pwr_out"):
        st.session_state.user_authenticated = False
        st.rerun()

st.markdown("<hr style='margin-top: 8px; margin-bottom: 18px; border: none; border-top: 1px solid #f1f5f9;' />", unsafe_allow_html=True)


# ----------------- 6. COVERING AUTHORITY LETTER GENERATOR -----------------
def generate_bank_authority_letter_html(bank_batch_type, records_list, cheque_no, cheque_date):
    total_amount = sum([float(r.get("AMOUNT", 0.0) or 0.0) for r in records_list])
    
    rows_html = ""
    for idx, r in enumerate(records_list, start=1):
        amt_fmt = f"{float(r.get('AMOUNT', 0.0) or 0.0):.0f}"
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
                b_df = pd.read_csv(up_file) if up_file.name.endswith(".csv") else pd.read_excel(up_file)
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


# ----------------- VIEW 2: EMPLOYEE MASTER REGISTER -----------------
elif current_view == "📋 Employee Master Register":
    st.markdown("#### Employee Master Register")
    st.caption("Active workforce directory with verified bank routing details")

    w_table = load_sheet_df("worker_master")
    if w_table.empty or "Worker_ID" not in w_table.columns:
        st.info("No employee records found.")
    else:
        flt_c1, flt_c2, flt_c3 = st.columns([1.1, 1.2, 2.0])
        with flt_c1:
            f_div = st.selectbox("Division", ["All", "Fleet Operations", "Civil Construction"], key="reg_div_f")
        with flt_c2:
            f_bank = st.selectbox("Bank Routing", ["All", "BOI Accounts", "Other Banks (NEFT)", "Cash (No Bank)"], key="reg_bank_f")
        with flt_c3:
            f_search = st.text_input("Search", placeholder="Filter by name, ID, or account number...").strip().lower()

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

        req_cols = ["Worker_ID", "Full_Name", "Division", "Designation", "Wage_Type", "Base_Rate", "Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details"]
        safe_cols = [c for c in req_cols if c in disp_table.columns]

        st.dataframe(
            disp_table[safe_cols],
            use_container_width=True,
            hide_index=True
        )


# ----------------- VIEW 3: DAILY FIELD MUSTER ROLL -----------------
elif current_view == "Daily Muster Roll (Field Attendance)":
    st.markdown("#### Daily Field Muster Roll")
    st.caption("Log daily shift attendance and deployment location (Tata Plant vs Private Works)")

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
                    st.markdown(f"**{wname}** (`{wid}`) • <span style='font-size: 0.78rem; color: #4f46e5; font-weight: 500;'>{wdiv}</span>", unsafe_allow_html=True)
                    st.caption(f"{wrole}")

                    r_c1, r_c2, r_c3 = st.columns([1.1, 1.3, 1.0])
                    with r_c1:
                        cur_state["att"] = st.selectbox("Status", ["P", "HD", "A", "WO"], index=["P", "HD", "A", "WO"].index(cur_state.get("att", "P")), key=f"att_s_{wid}")
                    with r_c2:
                        cur_state["loc"] = st.selectbox("Location", ["Tata Site (In-Plant)", "Private / Internal"], index=["Tata Site (In-Plant)", "Private / Internal"].index(cur_state.get("loc", "Tata Site (In-Plant)")), key=f"loc_s_{wid}")
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


# ----------------- VIEW 5: WAGES, FORM XVII & BANK LETTERS -----------------
elif current_view == "Monthly Wages & Payout Summary":
    st.markdown("#### Monthly Wages, Form XVII Audit & Bank Batches")
    st.caption("Reconcile field duty with Tata Steel Form XVII and generate BOI / NEFT covering letters")

    w_master = load_sheet_df("worker_master")
    muster_df = load_sheet_df("muster_roll")
    tata_df = load_sheet_df("tata_form_xvii")

    f_c1, f_c2 = st.columns([1, 1])
    with f_c1:
        sel_year = st.selectbox("Year", [2025, 2026, 2027], index=1)
    with f_c2:
        sel_month = st.selectbox("Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1)

    wage_period_str = f"{sel_month[:3].upper()}-{sel_year}"

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
                x_df = pd.read_excel(xvii_file)
                x_df.columns = [str(c).strip().replace(" ", "_") for c in x_df.columns]

            if not x_df.empty:
                st.success(f"Extracted {len(x_df)} records • Net Payable: ₹{x_df['Net_Amount_Paid'].sum():,.0f}")
                st.dataframe(x_df.head(5), use_container_width=True, hide_index=True)

                if st.button("Commit Form XVII Compliance Data", type="primary", use_container_width=True):
                    new_records = []
                    for _, xr in x_df.iterrows():
                        w_name_raw = str(xr.get("Worker_Name", xr.get("Name_of_Workman", xr.get("Name", "")))).strip()
                        appr_days = float(xr.get("Approved_Days", xr.get("No._of_days_worked", xr.get("No_of_days_worked", 0.0))) or 0.0)
                        net_paid = float(xr.get("Net_Amount_Paid", xr.get("Net_Amount", 0.0)) or 0.0)

                        matched_wid = ""
                        if not w_master.empty and "Full_Name" in w_master.columns:
                            match_w = w_master[w_master["Full_Name"].astype(str).str.upper() == w_name_raw.upper()]
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

                    if not tata_df.empty and "Wages_Period" in tata_df.columns:
                        tata_df = tata_df[tata_df["Wages_Period"] != wage_period_str]

                    final_tata = pd.concat([tata_df, pd.DataFrame(new_records)], ignore_index=True)
                    if save_sheet_df("tata_form_xvii", final_tata[TATA_COMPLIANCE_COLS]):
                        st.success(f"Form XVII for {wage_period_str} committed.")
                        st.rerun()
            else:
                if not xvii_file.name.lower().endswith(".pdf") or PYPDF_AVAILABLE:
                    st.error("Could not parse records from uploaded file.")

    st.markdown("###### Reconciliation Cockpit (Field Duty vs Form XVII)")

    if not tata_df.empty and "Wages_Period" in tata_df.columns:
        tata_cur = tata_df[tata_df["Wages_Period"] == wage_period_str]
    else:
        tata_cur = pd.DataFrame(columns=TATA_COMPLIANCE_COLS)

    month_num = list(calendar.month_name).index(sel_month)
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

            sup_days = 0.0
            if not muster_df.empty and "Date" in muster_df.columns and "Worker_ID" in muster_df.columns:
                w_m = muster_df[(muster_df["Worker_ID"].astype(str) == wid) & 
                               (muster_df["Date"].astype(str) >= d_start) & 
                               (muster_df["Date"].astype(str) <= d_end) & 
                               (muster_df["Duty_Location"] == "Tata Site (In-Plant)")]
                sup_days = len(w_m[w_m["Attendance"] == "P"]) + (len(w_m[w_m["Attendance"] == "HD"]) * 0.5)

            tata_approved_days = 0.0
            tata_net_paid = 0.0

            if not tata_cur.empty and "Worker_Name" in tata_cur.columns:
                tata_match = tata_cur[tata_cur["Worker_Name"].astype(str).str.upper() == wname.upper()]
                if not tata_match.empty:
                    tata_approved_days = float(tata_match.iloc[0].get("Approved_Days", 0.0) or 0.0)
                    tata_net_paid = float(tata_match.iloc[0].get("Net_Amount_Paid", 0.0) or 0.0)

            status_badge = "🟢 Matched"
            if tata_approved_days == 0 and sup_days > 0:
                status_badge = "🔴 No Punch (Field Only)"
            elif tata_approved_days != sup_days:
                status_badge = f"🟡 Variance ({sup_days - tata_approved_days:+.1f} d)"

            final_bank_transfer = tata_net_paid

            recon_rows.append({
                "Worker ID": wid,
                "Name": wname,
                "Field Days": sup_days,
                "Tata Days": tata_approved_days,
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
    st.markdown("###### Automated Bank Covering Letters")

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
            boi_html = generate_bank_authority_letter_html("BOI", boi_batch, chq_no, chq_date)
            st.download_button(
                label=f"Download BOI Covering Letter ({len(boi_batch)} Workers • ₹{tot_boi:,.0f})",
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
                label=f"Download NEFT Covering Letter ({len(neft_batch)} Workers • ₹{tot_neft:,.0f})",
                data=neft_html,
                file_name=f"NEFT_Transfer_Letter_{wage_period_str}.html",
                mime="text/html",
                type="primary" if not boi_batch else "secondary",
                use_container_width=True
            )
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
