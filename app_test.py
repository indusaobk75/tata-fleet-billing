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
    page_title="M/s INDU SAO | Tata Steel Site ERP",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------- 0. SYSTEM MASTER CONFIGURATION -----------------
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "Admin@2026"
DEFAULT_ADMIN_NAME = "Indu Sao (Owner)"

# ----------------- 1. DATABASE & SECRETS CONFIGURATION -----------------
SHEET_NAME = st.secrets.get("test_spreadsheet_name", st.secrets.get("spreadsheet_name", "Tata_Steel_ERP_TEST_DB"))

USER_COLUMNS = [
    "Username", "Password", "Full_Name", "Role", "Allowed_Tabs", "Status",
    "Contact_No", "Emergency_Contact", "Photo_Base64"
]

WORKER_MASTER_COLS = [
    "Worker_ID", "Full_Name", "Govt_ID_Ref", "Division", "Designation", "Wage_Type", 
    "Base_Rate", "Contact_No", "Status", "Created_Date"
]

MUSTER_ROLL_COLS = [
    "Date", "Worker_ID", "Worker_Name", "Division", "Attendance", "OT_Hours", 
    "Advance_Amount", "Payment_Mode", "Supervisor", "Remarks"
]

ADVANCE_LOG_COLS = [
    "Txn_ID", "Date", "Worker_ID", "Worker_Name", "Division", "Amount", 
    "Payment_Mode", "Supervisor", "Remarks"
]

FLEET_ROLES = [
    "Hyva Tipper Driver",
    "Excavator Operator (Heavy)",
    "JCB Backhoe Operator",
    "Wheel Loader Operator",
    "Dozer Operator",
    "Hydra Crane Operator",
    "Heavy Equipment Mechanic",
    "Auto Electrician",
    "Tyre Fitter / Service Mechanic",
    "Fleet Maintenance Helper",
    "Fleet Supervisor",
    "Other (Custom Entry)"
]

CIVIL_ROLES = [
    "Mason (Rajmistri)",
    "Shuttering / Framework Carpenter",
    "Gypsum & False Ceiling Installer",
    "Civil Painter (Putty / Coating)",
    "Steel & Bar Bender",
    "Welder & Structural Fabricator",
    "Plumber & Pipe Fitter",
    "Rigger / Scaffolding Specialist",
    "Tile & Stone Layer",
    "Civil Construction Helper",
    "Civil Site Supervisor",
    "Other (Custom Entry)"
]

@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if "gcp_json_raw" in st.secrets:
        creds_info = json.loads(st.secrets["gcp_json_raw"])
    elif "gcp_service_account" in st.secrets:
        creds_info = dict(st.secrets["gcp_service_account"])
    else:
        raise KeyError("Neither 'gcp_json_raw' nor 'gcp_service_account' found in secrets configuration.")
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
            st.error(f"Error persisting data to {worksheet_name}: {e}")
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


# ----------------- 2. MASTER REGISTRY & USER INTERFACE STYLING -----------------
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

    # MODULE 3: WORKER ATTENDANCE (SPLIT & ENHANCED)
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
    .top-action-btn button {
        border-radius: 8px !important;
        font-size: 1.05rem !important;
        padding: 4px 8px !important;
        font-weight: 600 !important;
    }
    .top-logout-btn button {
        color: #ef4444 !important;
        border-color: #fecaca !important;
        background-color: #fef2f2 !important;
    }
    .top-logout-btn button:hover {
        background-color: #fee2e2 !important;
        border-color: #fca5a5 !important;
        color: #dc2626 !important;
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

if st.session_state.user_authenticated and not st.session_state.allowed_tabs:
    st.session_state.user_authenticated = False
    st.session_state.current_user = None
    st.session_state.current_name = None
    st.session_state.current_role = None
    st.session_state.active_module = None

def login_screen():
    c1, c2, c3 = st.columns([1, 1.3, 1])
    with c2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="text-align: center; margin-bottom: 20px;">
                <div style="display: flex; justify-content: center; margin-bottom: 8px;">{INDU_SAO_SVG_LOGO}</div>
                <h2 style="margin: 0; padding: 0; color: #0f172a; font-weight: 700; font-size: 1.6rem;">M/s INDU SAO</h2>
                <div style="font-size: 0.85rem; color: #475569; font-weight: 600; margin-top: 4px;">Vendor Code: ID47 | Tata Steel West Bokaro Division</div>
                <div style="font-size: 0.72rem; color: #059669; font-weight: 600; margin-top: 2px;">ISO Certified Company 45001:2018</div>
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
                    st.error("Authentication service unavailable. Please contact the administrator.")
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
                        st.error("Account access is disabled. Please contact the administrator.")
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
                            st.session_state.active_module = "👷 Worker Attendance"
                        else:
                            raw_tabs = str(u_row.get("Allowed_Tabs", ""))
                            permitted = []
                            for t in raw_tabs.split(","):
                                t_clean = t.strip()
                                if t_clean in TAB_REGISTRY:
                                    permitted.append(t_clean)
                                elif t_clean in TAB_ALIASES:
                                    permitted.append(TAB_ALIASES[t_clean])
                            
                            if not permitted:
                                permitted = [
                                    "➕ Employee Enrolment",
                                    "📋 Employee Master Register",
                                    "Daily Muster Roll (Field Attendance)",
                                    "Advance & Expense Register",
                                    "Monthly Wages & Payout Summary"
                                ]

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


# ----------------- 4. COMPACT PROFESSIONAL SIDEBAR -----------------
with st.sidebar:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #e2e8f0;">
            <div>{INDU_SAO_SVG_LOGO}</div>
            <div>
                <div style="font-size: 0.98rem; font-weight: 700; color: #0f172a; line-height: 1.1;">M/s INDU SAO</div>
                <div style="font-size: 0.68rem; color: #475569; font-weight: 600;">Vendor Code: ID47</div>
                <div style="font-size: 0.62rem; color: #059669; font-weight: 600;">ISO 45001:2018</div>
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
            st.session_state.current_user = None
            st.session_state.current_name = None
            st.session_state.current_role = None
            st.session_state.allowed_tabs = []
            st.session_state.active_module = None
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


# ----------------- 5. EXECUTIVE TOP RESPONSIVE HEADER STRIP -----------------
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
        st.markdown('<div class="top-action-btn">', unsafe_allow_html=True)
        if st.button("⚙️", help="Edit Profile & Password", use_container_width=True, key="hdr_gear"):
            st.session_state.show_profile_box = not st.session_state.show_profile_box
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with btn_c2:
        st.markdown('<div class="top-action-btn top-logout-btn">', unsafe_allow_html=True)
        if st.button("⏻", help="Sign Out", use_container_width=True, key="hdr_pwr"):
            st.session_state.user_authenticated = False
            st.session_state.current_user = None
            st.session_state.current_name = None
            st.session_state.current_role = None
            st.session_state.allowed_tabs = []
            st.session_state.active_module = None
            st.session_state.show_profile_box = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

st.markdown("<hr style='margin-top: 6px; margin-bottom: 14px; border: none; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)

if st.session_state.show_profile_box:
    with st.container(border=True):
        p_c1, p_c2 = st.columns([1, 2], gap="medium")
        with p_c1:
            st.markdown("##### 👤 Profile Overview")
            if st.session_state.get("current_photo"):
                st.markdown(f'<img src="data:image/png;base64,{st.session_state.current_photo}" style="width: 100px; height: 100px; border-radius: 50%; object-fit: cover; margin-bottom: 12px; border: 2px solid #3b82f6;" />', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="top-user-avatar" style="width: 80px; height: 80px; font-size: 1.8rem; margin-bottom: 12px;">{initials}</div>', unsafe_allow_html=True)
            st.caption(f"**Username:** `@{st.session_state.current_user}`")
            st.caption(f"**Role:** `{st.session_state.current_role}`")
        with p_c2:
            st.markdown("##### ✏️ Update Contact & Password")
            with st.form("top_profile_form"):
                f_name = st.text_input("Full Name", value=st.session_state.current_name or "")
                f_contact = st.text_input("Contact Number", value=st.session_state.current_contact or "")
                f_emer = st.text_input("Emergency Contact Number", value=st.session_state.current_emergency or "")
                f_pass = st.text_input("New Password", type="password")
                f_photo = st.file_uploader("Upload Profile Photo", type=["png", "jpg", "jpeg"])

                if st.form_submit_button("Update Profile", type="primary", use_container_width=True):
                    users_df = load_sheet_df("app_users")
                    for col in USER_COLUMNS:
                        if col not in users_df.columns:
                            users_df[col] = ""
                    users_df["_u_clean"] = users_df["Username"].astype(str).str.strip().str.lower()
                    c_u = st.session_state.current_user.lower()

                    photo_b64 = st.session_state.current_photo
                    if f_photo:
                        photo_b64 = base64.b64encode(f_photo.read()).decode("utf-8")

                    if not users_df[users_df["_u_clean"] == c_u].empty:
                        idx = users_df[users_df["_u_clean"] == c_u].index[0]
                        users_df.at[idx, "Full_Name"] = f_name
                        users_df.at[idx, "Contact_No"] = f_contact
                        users_df.at[idx, "Emergency_Contact"] = f_emer
                        if f_pass.strip():
                            users_df.at[idx, "Password"] = f_pass.strip()
                        if photo_b64:
                            users_df.at[idx, "Photo_Base64"] = photo_b64

                        if save_sheet_df("app_users", users_df[USER_COLUMNS]):
                            st.session_state.current_name = f_name
                            st.session_state.current_contact = f_contact
                            st.session_state.current_emergency = f_emer
                            st.session_state.current_photo = photo_b64
                            st.session_state.show_profile_box = False
                            st.success("Profile successfully updated.")
                            st.rerun()
                    else:
                        st.session_state.show_profile_box = False
                        st.info("Administrative profile synchronized for the active session.")
                        st.rerun()


# ----------------- 6. REUSABLE A4 MULTI-PAGE BATCH PASS ENGINE -----------------
def generate_a4_batch_id_sheet_html(workers_list):
    """
    Renders ATM CR80 cards in batches of 8 per A4 page with cutting guides.
    Automatically handles multi-page pagination.
    """
    if not workers_list:
        return ""

    chunks = [workers_list[i:i + 8] for i in range(0, len(workers_list), 8)]
    total_pages = len(chunks)
    
    pages_html = ""
    for page_idx, chunk in enumerate(chunks, start=1):
        cards_html = ""
        for w in chunk:
            wid = str(w.get("Worker_ID", ""))
            wname = str(w.get("Full_Name", ""))
            wdiv = str(w.get("Division", ""))
            wrole = str(w.get("Designation", ""))
            wcontact = str(w.get("Contact_No", "N/A"))
            
            cards_html += f"""
            <div class="atm-card">
                <div class="card-header">
                    <div>
                        <div class="co-title">M/s INDU SAO</div>
                        <div class="co-sub">Vendor Code: ID47 | Tata Steel West Bokaro</div>
                    </div>
                    <div class="iso-tag">ISO 45001</div>
                </div>
                <div class="card-body">
                    <div class="photo-slot">PHOTO</div>
                    <div class="info-col">
                        <div class="worker-name">{wname}</div>
                        <div class="worker-trade">{wrole}</div>
                        <div class="meta-row">
                            <div><b>ID:</b> <span class="wid-badge">{wid}</span></div>
                            <div><b>Div:</b> {wdiv}</div>
                            <div><b>Mob:</b> {wcontact}</div>
                            <div><b>Ref:</b> [ID Verified]</div>
                        </div>
                    </div>
                </div>
                <div class="card-footer">
                    <div>Site Access Pass</div>
                    <div style="font-weight: 600; color: #0f172a;">Auth Signatory</div>
                </div>
            </div>
            """

        pages_html += f"""
        <div class="sheet-page">
            {cards_html}
            <div class="page-footer-note">Page {page_idx} of {total_pages} • M/s INDU SAO Authorized Site Passes</div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Batch ID Passes - A4 Multi-Page Print</title>
<style>
    @page {{
        size: A4 portrait;
        margin: 8mm 10mm;
    }}
    * {{
        box-sizing: border-box;
    }}
    body {{
        margin: 0;
        padding: 0;
        background: #f1f5f9;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }}
    .sheet-page {{
        width: 210mm;
        min-height: 297mm;
        margin: 0 auto 10mm auto;
        padding: 6mm 6mm;
        background: #ffffff;
        display: grid;
        grid-template-columns: repeat(2, 85.6mm);
        grid-auto-rows: 53.98mm;
        gap: 5.5mm 8mm;
        justify-content: center;
        page-break-after: always;
        break-after: page;
        position: relative;
    }}
    .page-footer-note {{
        grid-column: span 2;
        text-align: center;
        font-size: 5pt;
        color: #94a3b8;
        padding-top: 1mm;
    }}
    .atm-card {{
        width: 85.6mm;
        height: 53.98mm;
        background: #ffffff;
        border: 1px dashed #94a3b8;
        border-radius: 3.5mm;
        padding: 3mm 4mm;
        position: relative;
        overflow: hidden;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
    }}
    .card-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        border-bottom: 1.2px solid #0b2a75;
        padding-bottom: 1.2mm;
    }}
    .co-title {{ font-size: 8pt; font-weight: 800; color: #0b2a75; line-height: 1; }}
    .co-sub {{ font-size: 5pt; font-weight: 600; color: #475569; margin-top: 0.4mm; }}
    .iso-tag {{ font-size: 4.5pt; background: #064e3b; color: #34d399; padding: 0.6mm 1.2mm; border-radius: 0.8mm; font-weight: 700; }}
    .card-body {{ display: flex; gap: 2.5mm; align-items: center; margin-top: 0.8mm; }}
    .photo-slot {{
        width: 17mm; height: 21mm; border: 0.8px dashed #94a3b8; border-radius: 1.5mm;
        background: #f8fafc; display: flex; align-items: center; justify-content: center;
        font-size: 4.8pt; color: #94a3b8; text-align: center; font-weight: 700;
    }}
    .info-col {{ flex: 1; line-height: 1.22; }}
    .worker-name {{ font-size: 7.8pt; font-weight: 800; color: #0f172a; text-transform: uppercase; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 55mm; }}
    .worker-trade {{ font-size: 6.2pt; font-weight: 700; color: #2563eb; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 55mm; }}
    .meta-row {{ font-size: 5.2pt; color: #334155; margin-top: 0.8mm; }}
    .wid-badge {{ background: #e2e8f0; padding: 0.3mm 1mm; border-radius: 0.8mm; font-weight: 700; color: #0f172a; }}
    .card-footer {{
        border-top: 0.8px solid #cbd5e1; padding-top: 0.8mm;
        display: flex; justify-content: space-between; align-items: flex-end; font-size: 4.6pt; color: #64748b;
    }}
    @media print {{
        body {{ background: transparent; }}
        .sheet-page {{ margin: 0; padding: 0; box-shadow: none; width: auto; min-height: auto; }}
        .atm-card {{ border: 1px dashed #cbd5e1 !important; }}
    }}
</style>
</head>
<body onload="window.print()">
    {pages_html}
</body>
</html>"""


# ----------------- 7. OPERATIONAL HUBS VIEWPORT -----------------

# --- HUB 1: WORKER ATTENDANCE & MULTI-TRADE SYSTEM ---
if selected_module == "👷 Worker Attendance":
    st.markdown("### 👷 Site Muster Roll & Worker Management Hub")
    st.caption("Fleet Crew, Civil Trades, Shift Attendance, Site Advances, and Monthly Payroll Statements")

    att_tabs = [t for t in st.session_state.allowed_tabs if TAB_REGISTRY.get(t) == "Attendance"]
    active_a_tabs = st.tabs(att_tabs)

    for i, tab_title in enumerate(att_tabs):
        with active_a_tabs[i]:
            
            # ----------------- TAB A: EMPLOYEE ENROLMENT -----------------
            if tab_title == "➕ Employee Enrolment":
                st.markdown("##### ➕ Dedicated Employee Enrolment Portal")
                st.caption("Register individual workforce members or bulk import site roster using templates")

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
                            w_ref = st.text_input("Identity Verification Reference", key="enrol_ref", help="Used to distinguish duplicate names").strip()

                            role_choices = FLEET_ROLES if w_div == "Fleet Operations" else CIVIL_ROLES
                            sel_role = st.selectbox("Designation / Role", role_choices, key="enrol_role")
                            
                            # Dynamic field for Other Role
                            custom_role = ""
                            if sel_role == "Other (Custom Entry)":
                                custom_role = st.text_input("Specify Custom Role", key="enrol_custom_role").strip()

                            # Interactive Reactive Wage Model
                            w_wage_type = st.selectbox("Wage Model", ["DAILY_WAGE", "MONTHLY_FIXED"], key="enrol_wage_type")
                            
                            if w_wage_type == "DAILY_WAGE":
                                w_rate = st.number_input("Daily Wage Rate (₹ / Shift)", min_value=100.0, value=650.0, step=50.0, key="enrol_rate_daily")
                            else:
                                w_rate = st.number_input("Monthly Fixed Base Salary (₹ / Month)", min_value=1000.0, value=18000.0, step=500.0, key="enrol_rate_monthly")

                            w_contact = st.text_input("Contact Mobile Number", key="enrol_contact").strip()
                            w_status = st.selectbox("Initial Employment Status", ["ACTIVE", "INACTIVE"], key="enrol_status")

                            if st.button("Save & Commit Employee Record", type="primary", use_container_width=True):
                                final_role = custom_role if sel_role == "Other (Custom Entry)" and custom_role else sel_role
                                if not w_name:
                                    st.error("Employee Name is required.")
                                elif sel_role == "Other (Custom Entry)" and not custom_role:
                                    st.error("Please specify the custom role name.")
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
                                        "Status": w_status,
                                        "Created_Date": datetime.date.today().strftime("%Y-%m-%d")
                                    }
                                    updated_w_df = pd.concat([w_df, pd.DataFrame([new_row])], ignore_index=True)
                                    if save_sheet_df("worker_master", updated_w_df[WORKER_MASTER_COLS]):
                                        st.success(f"Employee {w_name} ({next_wid}) registered successfully.")
                                        st.rerun()

                    with c_info:
                        with st.container(border=True):
                            st.markdown("###### ℹ️ Enrolment Rules")
                            st.caption("• **Auto Worker IDs:** Assigned sequentially per division (`FLT-1001` or `CIV-1001`).")
                            st.caption("• **Overtime Calculation:** OT hourly rate is automatically derived from standard 8-hour shift base.")
                            st.caption("• **Distinction Ref:** Keep government verification references clean for audit readiness.")

                else:
                    with st.container(border=True):
                        st.markdown("###### 📁 Bulk Upload Employee Roster")
                        st.caption("Upload CSV or Excel file. Blank designations are automatically assigned as 'Unassigned'.")

                        sample_df = pd.DataFrame([
                            {
                                "Full_Name": "Ramesh Kumar", "Govt_ID_Ref": "Ref-01", "Division": "Fleet Operations",
                                "Designation": "Hyva Tipper Driver", "Wage_Type": "MONTHLY_FIXED", "Base_Rate": 18000.0,
                                "Contact_No": "9876543210", "Status": "ACTIVE"
                            },
                            {
                                "Full_Name": "Suresh Mistri", "Govt_ID_Ref": "Ref-02", "Division": "Civil Construction",
                                "Designation": "", "Wage_Type": "DAILY_WAGE", "Base_Rate": 700.0,
                                "Contact_No": "9876543211", "Status": "ACTIVE"
                            }
                        ])

                        st.download_button(
                            "📥 Download Clean Template (CSV)",
                            data=sample_df.to_csv(index=False).encode('utf-8'),
                            file_name="Employee_Import_Template.csv",
                            mime="text/csv"
                        )

                        up_file = st.file_uploader("Upload Excel / CSV File", type=["csv", "xlsx"])
                        if up_file:
                            b_df = pd.read_csv(up_file) if up_file.name.endswith(".csv") else pd.read_excel(up_file)
                            b_df.columns = [str(c).strip() for c in b_df.columns]
                            st.write(f"Parsed Preview ({len(b_df)} records):")
                            st.dataframe(b_df.head(4), use_container_width=True)

                            if st.button("Commit Bulk Records", type="primary", use_container_width=True):
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
                                        "Base_Rate": float(b_row.get("Base_Rate", 600.0) or 600.0),
                                        "Contact_No": str(b_row.get("Contact_No", "")).strip(),
                                        "Status": str(b_row.get("Status", "ACTIVE")).strip().upper(),
                                        "Created_Date": datetime.date.today().strftime("%Y-%m-%d")
                                    })

                                fin_w_df = pd.concat([w_df, pd.DataFrame(appended_rows)], ignore_index=True)
                                if save_sheet_df("worker_master", fin_w_df[WORKER_MASTER_COLS]):
                                    st.success(f"{len(appended_rows)} workers successfully synchronized.")
                                    st.rerun()

            # ----------------- TAB B: EMPLOYEE MASTER REGISTER & 360 DRILLDOWN -----------------
            elif tab_title == "📋 Employee Master Register":
                st.markdown("##### 📋 Employee Master Register & Field Passes")
                st.caption("Active workforce records, interactive attendance drilldowns, and zero-waste A4 multi-pass printing")

                w_table = load_sheet_df("worker_master")
                if w_table.empty or "Worker_ID" not in w_table.columns:
                    st.info("No employee profiles found. Register employees under '➕ Employee Enrolment'.")
                else:
                    # Top Metrics Overview
                    t_tot = len(w_table[w_table["Status"] == "ACTIVE"])
                    t_flt = len(w_table[(w_table["Status"] == "ACTIVE") & (w_table["Division"] == "Fleet Operations")])
                    t_civ = len(w_table[(w_table["Status"] == "ACTIVE") & (w_table["Division"] == "Civil Construction")])
                    
                    sm1, sm2, sm3, sm4 = st.columns(4)
                    sm1.metric("Active Strength", t_tot)
                    sm2.metric("Fleet Crew", t_flt)
                    sm3.metric("Civil Trades", t_civ)
                    sm4.metric("Unassigned Roles", len(w_table[w_table["Designation"] == "Unassigned"]))

                    st.markdown("<hr style='margin: 8px 0 14px 0; border: none; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)

                    # Filters & Batch Actions Toolbar
                    flt_c1, flt_c2, flt_c3 = st.columns([1.2, 1.2, 2.0])
                    with flt_c1:
                        f_div = st.selectbox("Filter Division", ["All", "Fleet Operations", "Civil Construction"], key="reg_div_f")
                    with flt_c2:
                        f_wage = st.selectbox("Filter Wage Model", ["All", "DAILY_WAGE", "MONTHLY_FIXED"], key="reg_wage_f")
                    with flt_c3:
                        f_search = st.text_input("Search Employee Name or ID", placeholder="Type name or ID to filter...").strip().lower()

                    disp_table = w_table.copy()
                    if f_div != "All":
                        disp_table = disp_table[disp_table["Division"] == f_div]
                    if f_wage != "All":
                        disp_table = disp_table[disp_table["Wage_Type"] == f_wage]
                    if f_search:
                        disp_table = disp_table[
                            disp_table["Full_Name"].astype(str).str.lower().str.contains(f_search) |
                            disp_table["Worker_ID"].astype(str).str.lower().str.contains(f_search)
                        ]

                    # Multi-Page A4 Batch Pass Download Trigger
                    all_selected_records = disp_table.to_dict(orient="records")
                    tot_sel = len(all_selected_records)
                    tot_pages = (tot_sel + 7) // 8 if tot_sel > 0 else 0

                    btn_c, _ = st.columns([2.2, 1.8])
                    with btn_c:
                        if tot_sel > 0:
                            st.download_button(
                                label=f"🖨️ Download Batch ID Passes ({tot_sel} Cards • {tot_pages} A4 Sheet{'s' if tot_pages > 1 else ''})",
                                data=generate_a4_batch_id_sheet_html(all_selected_records),
                                file_name=f"Batch_Passes_{tot_sel}_Workers_{datetime.date.today().strftime('%Y%m%d')}.html",
                                mime="text/html",
                                type="primary",
                                use_container_width=True,
                                help="Download ATM-sized passes arranged 8 per A4 sheet with cutting guides"
                            )

                    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

                    # Interactive Directory Table
                    th1, th2, th3, th4, th5, th6 = st.columns([1.1, 2.0, 1.3, 1.6, 1.2, 1.4])
                    with th1: st.markdown("**ID (Clickable)**")
                    with th2: st.markdown("**Employee Name**")
                    with th3: st.markdown("**Division**")
                    with th4: st.markdown("**Designation / Role**")
                    with th5: st.markdown("**Base Rate**")
                    with th6: st.markdown("**Profile Actions**")
                    st.markdown("<hr style='margin: 4px 0 10px 0; border: none; border-top: 1px solid #cbd5e1;' />", unsafe_allow_html=True)

                    for _, w_row in disp_table.iterrows():
                        wid = str(w_row.get("Worker_ID", ""))
                        wname = str(w_row.get("Full_Name", ""))
                        wdiv = str(w_row.get("Division", ""))
                        wrole = str(w_row.get("Designation", ""))
                        wwage = str(w_row.get("Wage_Type", ""))
                        wrate = float(w_row.get("Base_Rate", 0.0) or 0.0)

                        r1, r2, r3, r4, r5, r6 = st.columns([1.1, 2.0, 1.3, 1.6, 1.2, 1.4])
                        with r1:
                            if st.button(f"🔍 {wid}", key=f"drill_{wid}", help="Click to open 360° Monthly Attendance & In-Place Editor"):
                                st.session_state.selected_worker_drilldown = wid
                                st.rerun()
                        with r2: st.markdown(f"**{wname}**")
                        with r3: st.caption(wdiv)
                        with r4: st.caption(wrole)
                        with r5: st.caption(f"₹{wrate:,.0f} {'/d' if wwage == 'DAILY_WAGE' else '/m'}")
                        with r6:
                            # Single card instant download
                            single_card_html = generate_a4_batch_id_sheet_html([w_row.to_dict()])
                            st.download_button(
                                label="🪪 Card",
                                data=single_card_html,
                                file_name=f"Pass_{wid}_{wname.replace(' ', '_')}.html",
                                mime="text/html",
                                key=f"dl_single_{wid}",
                                help="Download individual pass"
                            )
                        st.markdown("<hr style='margin: 4px 0; border: none; border-top: 1px dashed #f1f5f9;' />", unsafe_allow_html=True)

                    # ----------------- 360° ATTENDANCE DRILLDOWN & IN-PLACE EDITOR -----------------
                    if st.session_state.selected_worker_drilldown:
                        sel_wid = st.session_state.selected_worker_drilldown
                        w_match = w_table[w_table["Worker_ID"].astype(str) == sel_wid]
                        
                        if not w_match.empty:
                            w_curr = w_match.iloc[0]
                            st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)
                            with st.container(border=True):
                                c_top1, c_top2 = st.columns([3.0, 1.0])
                                with c_top1:
                                    st.markdown(f"#### 🔍 360° Attendance Ledger: **{w_curr['Full_Name']}** (`{sel_wid}`)")
                                    st.caption(f"Division: **{w_curr['Division']}** | Role: **{w_curr['Designation']}** | Model: **{w_curr['Wage_Type']}**")
                                with c_top2:
                                    if st.button("✖️ Close Ledger", use_container_width=True):
                                        st.session_state.selected_worker_drilldown = None
                                        st.rerun()

                                # Month Selector for Worker
                                m_c1, m_c2, _ = st.columns([1.2, 1.2, 2.0])
                                with m_c1:
                                    drill_year = st.selectbox("Select Year", [2025, 2026, 2027], index=1, key="drill_yr")
                                with m_c2:
                                    drill_month = st.selectbox("Select Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1, key="drill_mo")

                                d_month_num = list(calendar.month_name).index(drill_month)
                                d_start_str = f"{drill_year}-{d_month_num:02d}-01"
                                d_last_day = calendar.monthrange(drill_year, d_month_num)[1]
                                d_end_str = f"{drill_year}-{d_month_num:02d}-{d_last_day:02d}"

                                muster_df = load_sheet_df("muster_roll")
                                worker_logs = pd.DataFrame()
                                if not muster_df.empty and "Date" in muster_df.columns:
                                    worker_logs = muster_df[
                                        (muster_df["Worker_ID"].astype(str) == sel_wid) &
                                        (muster_df["Date"].astype(str) >= d_start_str) &
                                        (muster_df["Date"].astype(str) <= d_end_str)
                                    ].sort_values(by="Date", ascending=True)

                                # Scorecard Metrics
                                p_cnt = len(worker_logs[worker_logs["Attendance"] == "P"]) if not worker_logs.empty else 0
                                hd_cnt = len(worker_logs[worker_logs["Attendance"] == "HD"]) if not worker_logs.empty else 0
                                a_cnt = len(worker_logs[worker_logs["Attendance"] == "A"]) if not worker_logs.empty else 0
                                tot_ot = pd.to_numeric(worker_logs["OT_Hours"], errors="coerce").sum() if not worker_logs.empty else 0.0
                                tot_adv = pd.to_numeric(worker_logs["Advance_Amount"], errors="coerce").sum() if not worker_logs.empty else 0.0

                                sc1, sc2, sc3, sc4, sc5 = st.columns(5)
                                sc1.metric("Present (P)", p_cnt)
                                sc2.metric("Half Days (HD)", hd_cnt)
                                sc3.metric("Absent (A)", a_cnt)
                                sc4.metric("OT Hours", f"{tot_ot:.1f} Hrs")
                                sc5.metric("Spot Advances", f"₹{tot_adv:,.0f}")

                                st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)

                                # In-Place Attendance Correction Form
                                st.markdown("###### ✏️ In-Place Shift Correction")
                                with st.form(f"edit_attendance_worker_{sel_wid}"):
                                    ec1, ec2, ec3, ec4, ec5 = st.columns([1.2, 1.0, 1.0, 1.2, 1.2])
                                    with ec1:
                                        edit_date = st.date_input("Target Date to Correct", value=datetime.date.today(), key=f"e_dt_{sel_wid}")
                                    with ec2:
                                        edit_att = st.selectbox("Status", ["P", "HD", "A", "WO"], key=f"e_att_{sel_wid}")
                                    with ec3:
                                        edit_ot = st.number_input("OT Hours", min_value=0.0, max_value=16.0, step=0.5, value=0.0, key=f"e_ot_{sel_wid}")
                                    with ec4:
                                        edit_adv = st.number_input("Advance (₹)", min_value=0.0, step=100.0, value=0.0, key=f"e_adv_{sel_wid}")
                                    with ec5:
                                        edit_rem = st.text_input("Remarks", placeholder="Adjustment reason", key=f"e_rem_{sel_wid}").strip()

                                    if st.form_submit_button("Update Shift Record", type="primary", use_container_width=True):
                                        edit_dt_str = edit_date.strftime("%Y-%m-%d")
                                        all_m = load_sheet_df("muster_roll")
                                        for c in MUSTER_ROLL_COLS:
                                            if c not in all_m.columns:
                                                all_m[c] = ""

                                        # Drop existing row for that date/worker
                                        if not all_m.empty and "Date" in all_m.columns and "Worker_ID" in all_m.columns:
                                            all_m = all_m[~((all_m["Date"].astype(str) == edit_dt_str) & (all_m["Worker_ID"].astype(str) == sel_wid))]

                                        corrected_row = {
                                            "Date": edit_dt_str,
                                            "Worker_ID": sel_wid,
                                            "Worker_Name": w_curr["Full_Name"],
                                            "Division": w_curr["Division"],
                                            "Attendance": edit_att,
                                            "OT_Hours": edit_ot,
                                            "Advance_Amount": edit_adv,
                                            "Payment_Mode": "Cash" if edit_adv > 0 else "",
                                            "Supervisor": st.session_state.current_user,
                                            "Remarks": f"[Admin Edit] {edit_rem}".strip()
                                        }
                                        updated_m = pd.concat([all_m, pd.DataFrame([corrected_row])], ignore_index=True)
                                        if save_sheet_df("muster_roll", updated_m[MUSTER_ROLL_COLS]):
                                            st.success(f"Shift record for {edit_dt_str} updated successfully.")
                                            st.rerun()

                                # Historical Log Table
                                st.markdown("###### 📜 Month Shift Log")
                                if worker_logs.empty:
                                    st.info(f"No shift entries recorded for {drill_month} {drill_year}.")
                                else:
                                    st.dataframe(
                                        worker_logs[["Date", "Attendance", "OT_Hours", "Advance_Amount", "Supervisor", "Remarks"]],
                                        use_container_width=True,
                                        hide_index=True
                                    )

            # ----------------- TAB C: DAILY MUSTER ROLL -----------------
            elif tab_title == "Daily Muster Roll (Field Attendance)":
                st.markdown("##### 📅 Daily Muster Roll & Shift Attendance")
                st.caption("Apply bulk status updates or adjust individual cards for attendance, direct overtime hours, and spot advances")

                w_master = load_sheet_df("worker_master")
                if w_master.empty or "Worker_ID" not in w_master.columns:
                    st.warning("Please register employees in '➕ Employee Enrolment' first.")
                else:
                    active_workers = w_master[w_master["Status"] == "ACTIVE"].copy()

                    top_m1, top_m2, top_m3 = st.columns([1.1, 1.2, 1.2])
                    with top_m1:
                        muster_date = st.date_input("Attendance Date", value=datetime.date.today(), key="muster_dt_pick")
                    with top_m2:
                        sel_division = st.selectbox("Operational Division", ["All Divisions", "Fleet Operations", "Civil Construction"], key="muster_div_filter")
                    with top_m3:
                        muster_date_str = muster_date.strftime("%Y-%m-%d")
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
                            def_att = "P"
                            def_ot = 0.0
                            def_adv = 0.0
                            def_mode = "Cash"
                            def_rem = ""

                            if not day_records.empty and "Worker_ID" in day_records.columns:
                                match_prev = day_records[day_records["Worker_ID"].astype(str) == wid]
                                if not match_prev.empty:
                                    r0 = match_prev.iloc[0]
                                    def_att = str(r0.get("Attendance", "P"))
                                    def_ot = float(r0.get("OT_Hours", 0.0) or 0.0)
                                    def_adv = float(r0.get("Advance_Amount", 0.0) or 0.0)
                                    def_mode = str(r0.get("Payment_Mode", "Cash"))
                                    def_rem = str(r0.get("Remarks", ""))

                            st.session_state[cache_key][wid] = {
                                "selected": True,
                                "att": def_att,
                                "ot": def_ot,
                                "adv": def_adv,
                                "mode": def_mode,
                                "rem": def_rem
                            }

                    with st.container(border=True):
                        st.markdown("###### ⚡ Bulk Batch Actions")
                        ba_col1, ba_col2, ba_col3, ba_col4 = st.columns([1.2, 1, 1, 1])

                        with ba_col1:
                            all_checked = st.checkbox("Select / Deselect All", value=True, key="bulk_chk_master")
                            if st.button("Apply Selection", use_container_width=True):
                                for wid in active_workers["Worker_ID"].astype(str):
                                    if wid in st.session_state[cache_key]:
                                        st.session_state[cache_key][wid]["selected"] = all_checked
                                st.rerun()

                        with ba_col2:
                            if st.button("✅ Mark Selected [P]", use_container_width=True):
                                for wid in active_workers["Worker_ID"].astype(str):
                                    if wid in st.session_state[cache_key] and st.session_state[cache_key][wid]["selected"]:
                                        st.session_state[cache_key][wid]["att"] = "P"
                                st.rerun()

                        with ba_col3:
                            if st.button("❌ Mark Selected [A]", use_container_width=True):
                                for wid in active_workers["Worker_ID"].astype(str):
                                    if wid in st.session_state[cache_key] and st.session_state[cache_key][wid]["selected"]:
                                        st.session_state[cache_key][wid]["att"] = "A"
                                st.rerun()

                        with ba_col4:
                            if st.button("🔄 Reset All to [P]", use_container_width=True):
                                for wid in active_workers["Worker_ID"].astype(str):
                                    if wid in st.session_state[cache_key]:
                                        st.session_state[cache_key][wid]["att"] = "P"
                                st.rerun()

                    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
                    st.markdown(f"###### 👥 Attendance Cards ({len(active_workers)} Employees)")
                    
                    grid_cols = st.columns(2, gap="medium")
                    
                    for idx, (_, w_row) in enumerate(active_workers.iterrows()):
                        col_target = grid_cols[idx % 2]
                        wid = str(w_row["Worker_ID"])
                        wname = str(w_row["Full_Name"])
                        wdiv = str(w_row["Division"])
                        wrole = str(w_row["Designation"])
                        w_type = str(w_row.get("Wage_Type", "DAILY_WAGE"))

                        cur_state = st.session_state[cache_key].get(wid, {
                            "selected": True, "att": "P", "ot": 0.0, "adv": 0.0, "mode": "Cash", "rem": ""
                        })

                        with col_target:
                            with st.container(border=True):
                                top_c1, top_c2 = st.columns([0.15, 0.85])
                                with top_c1:
                                    cur_state["selected"] = st.checkbox("", value=cur_state["selected"], key=f"sel_cb_{wid}", label_visibility="collapsed")
                                with top_c2:
                                    st.markdown(
                                        f"""
                                        <div style="display: flex; justify-content: space-between; align-items: center;">
                                            <div>
                                                <span style="font-weight: 700; font-size: 0.95rem; color: #0f172a;">{wname}</span>
                                                <span style="font-size: 0.75rem; background: #f1f5f9; padding: 2px 6px; border-radius: 4px; color: #475569; margin-left: 6px;">{wid}</span>
                                            </div>
                                            <span style="font-size: 0.72rem; color: #2563eb; font-weight: 600;">{wdiv}</span>
                                        </div>
                                        <div style="font-size: 0.76rem; color: #64748b; margin-top: 1px;">{wrole} • {w_type}</div>
                                        """,
                                        unsafe_allow_html=True
                                    )

                                st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #f1f5f9;' />", unsafe_allow_html=True)

                                chip_c1, chip_c2 = st.columns([1.8, 1.2])
                                with chip_c1:
                                    att_options = ["P", "HD", "A", "WO"]
                                    cur_idx = att_options.index(cur_state["att"]) if cur_state["att"] in att_options else 0
                                    cur_state["att"] = st.radio(
                                        "Attendance Status",
                                        att_options,
                                        index=cur_idx,
                                        horizontal=True,
                                        key=f"rad_att_{wid}",
                                        label_visibility="collapsed"
                                    )
                                with chip_c2:
                                    cur_state["ot"] = st.number_input(
                                        "OT Hours",
                                        min_value=0.0,
                                        max_value=16.0,
                                        value=float(cur_state["ot"]),
                                        step=0.5,
                                        key=f"card_ot_{wid}"
                                    )

                                with st.expander("💵 Spot Advance & Remarks", expanded=(cur_state["adv"] > 0)):
                                    d_c1, d_c2 = st.columns(2)
                                    with d_c1:
                                        cur_state["adv"] = st.number_input(
                                            "Advance Amount (₹)",
                                            min_value=0.0,
                                            value=float(cur_state["adv"]),
                                            step=100.0,
                                            key=f"card_adv_{wid}"
                                        )
                                    with d_c2:
                                        mode_opts = ["Cash", "Online UPI", "Bank Transfer"]
                                        cur_mode_idx = mode_opts.index(cur_state["mode"]) if cur_state["mode"] in mode_opts else 0
                                        cur_state["mode"] = st.selectbox(
                                            "Payment Mode",
                                            mode_opts,
                                            index=cur_mode_idx,
                                            key=f"card_mode_{wid}"
                                        )
                                    cur_state["rem"] = st.text_input(
                                        "Remarks / Details",
                                        value=cur_state["rem"],
                                        key=f"card_rem_{wid}"
                                    )

                        st.session_state[cache_key][wid] = cur_state

                    st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

                    if st.button("💾 Commit Daily Muster Roll", type="primary", use_container_width=True):
                        marked_rows = []
                        for _, w_row in active_workers.iterrows():
                            wid = str(w_row["Worker_ID"])
                            wname = str(w_row["Full_Name"])
                            wdiv = str(w_row["Division"])
                            s_data = st.session_state[cache_key].get(wid, {})

                            marked_rows.append({
                                "Date": muster_date_str,
                                "Worker_ID": wid,
                                "Worker_Name": wname,
                                "Division": wdiv,
                                "Attendance": s_data.get("att", "P"),
                                "OT_Hours": s_data.get("ot", 0.0),
                                "Advance_Amount": s_data.get("adv", 0.0),
                                "Payment_Mode": s_data.get("mode", "Cash") if s_data.get("adv", 0.0) > 0 else "",
                                "Supervisor": st.session_state.current_user,
                                "Remarks": s_data.get("rem", "")
                            })

                        all_muster = load_sheet_df("muster_roll")
                        for col in MUSTER_ROLL_COLS:
                            if col not in all_muster.columns:
                                all_muster[col] = ""

                        if not all_muster.empty and "Date" in all_muster.columns and "Worker_ID" in all_muster.columns:
                            m_wids = [r["Worker_ID"] for r in marked_rows]
                            cond = (all_muster["Date"].astype(str) == muster_date_str) & (all_muster["Worker_ID"].astype(str).isin(m_wids))
                            all_muster = all_muster[~cond]

                        updated_muster = pd.concat([all_muster, pd.DataFrame(marked_rows)], ignore_index=True)

                        adv_to_log = [r for r in marked_rows if r["Advance_Amount"] > 0]
                        if adv_to_log:
                            adv_df = load_sheet_df("worker_advances")
                            for col in ADVANCE_LOG_COLS:
                                if col not in adv_df.columns:
                                    adv_df[col] = ""

                            new_advs = []
                            for a in adv_to_log:
                                new_advs.append({
                                    "Txn_ID": f"M-ADV-{int(time.time())}-{a['Worker_ID']}",
                                    "Date": muster_date_str,
                                    "Worker_ID": a["Worker_ID"],
                                    "Worker_Name": a["Worker_Name"],
                                    "Division": a["Division"],
                                    "Amount": a["Advance_Amount"],
                                    "Payment_Mode": a["Payment_Mode"],
                                    "Supervisor": a["Supervisor"],
                                    "Remarks": f"Daily Spot Advance: {a['Remarks']}".strip()
                                })
                            updated_adv = pd.concat([adv_df, pd.DataFrame(new_advs)], ignore_index=True)
                            save_sheet_df("worker_advances", updated_adv[ADVANCE_LOG_COLS])

                        if save_sheet_df("muster_roll", updated_muster[MUSTER_ROLL_COLS]):
                            st.success(f"Attendance for {muster_date_str} committed and synchronized successfully.")
                            st.rerun()

            # ----------------- TAB D: DEDICATED ADVANCE REGISTER -----------------
            elif tab_title == "Advance & Expense Register":
                st.markdown("##### 💵 Advance & Field Expense Register")
                st.caption("Record lump-sum cash advances, bank/UPI disbursements, and site allowances")

                a_col1, a_col2 = st.columns([1.1, 1.9], gap="large")

                w_master = load_sheet_df("worker_master")
                worker_options = {}
                if not w_master.empty and "Worker_ID" in w_master.columns:
                    for _, r in w_master[w_master["Status"] == "ACTIVE"].iterrows():
                        worker_options[f"{r['Full_Name']} ({r['Worker_ID']}) - {r['Division']}"] = (r['Worker_ID'], r['Full_Name'], r['Division'])

                with a_col1:
                    with st.container(border=True):
                        st.markdown("###### Record Advance Payment")
                        if not worker_options:
                            st.warning("No active employees found.")
                        else:
                            with st.form("disburse_advance_form", clear_on_submit=True):
                                adv_date = st.date_input("Disbursement Date", value=datetime.date.today())
                                sel_w_key = st.selectbox("Select Employee", list(worker_options.keys()))
                                adv_amount = st.number_input("Advance Amount (₹)", min_value=50.0, step=100.0, value=500.0)
                                adv_mode = st.selectbox("Payment Mode", ["Cash", "Online UPI", "Bank Transfer", "Voucher"])
                                adv_remarks = st.text_input("Remarks / Purpose").strip()

                                submit_adv = st.form_submit_button("Disburse Advance", type="primary", use_container_width=True)

                                if submit_adv:
                                    w_id, w_name, w_div = worker_options[sel_w_key]
                                    adv_df = load_sheet_df("worker_advances")
                                    for col in ADVANCE_LOG_COLS:
                                        if col not in adv_df.columns:
                                            adv_df[col] = ""

                                    new_txn = {
                                        "Txn_ID": f"ADV-{int(time.time())}",
                                        "Date": adv_date.strftime("%Y-%m-%d"),
                                        "Worker_ID": w_id,
                                        "Worker_Name": w_name,
                                        "Division": w_div,
                                        "Amount": adv_amount,
                                        "Payment_Mode": adv_mode,
                                        "Supervisor": st.session_state.current_user,
                                        "Remarks": adv_remarks
                                    }
                                    updated_adv_df = pd.concat([adv_df, pd.DataFrame([new_txn])], ignore_index=True)
                                    if save_sheet_df("worker_advances", updated_adv_df[ADVANCE_LOG_COLS]):
                                        st.success(f"Advance of ₹{adv_amount:,.2f} recorded for {w_name} via {adv_mode}.")
                                        st.rerun()

                with a_col2:
                    with st.container(border=True):
                        st.markdown("###### 📜 Advance Disbursement Ledger")
                        adv_history = load_sheet_df("worker_advances")
                        if adv_history.empty or "Txn_ID" not in adv_history.columns:
                            st.info("No recorded advance transactions found.")
                        else:
                            m_cash = pd.to_numeric(adv_history[adv_history["Payment_Mode"] == "Cash"]["Amount"], errors="coerce").sum()
                            m_online = pd.to_numeric(adv_history[adv_history["Payment_Mode"] == "Online UPI"]["Amount"], errors="coerce").sum()
                            m_bank = pd.to_numeric(adv_history[adv_history["Payment_Mode"] == "Bank Transfer"]["Amount"], errors="coerce").sum()

                            sc1, sc2, sc3 = st.columns(3)
                            sc1.metric("Cash Disbursed", f"₹{m_cash:,.2f}")
                            sc2.metric("Online UPI Disbursed", f"₹{m_online:,.2f}")
                            sc3.metric("Bank Disbursed", f"₹{m_bank:,.2f}")

                            st.dataframe(
                                adv_history[["Date", "Worker_Name", "Division", "Amount", "Payment_Mode", "Supervisor", "Remarks"]].tail(25),
                                use_container_width=True,
                                hide_index=True
                            )

            # ----------------- TAB E: MONTHLY WAGE ABSTRACT -----------------
            elif tab_title == "Monthly Wages & Payout Summary":
                st.markdown("##### 📊 Monthly Attendance Summary & Wage Statement")
                st.caption("Consolidated gross wages, automatic 8-hour overtime calculation, total advance deductions, and net payouts")

                w_master = load_sheet_df("worker_master")
                muster_df = load_sheet_df("muster_roll")
                adv_df = load_sheet_df("worker_advances")

                f_c1, f_c2, f_c3 = st.columns([1, 1, 1.2])
                with f_c1:
                    sel_year = st.selectbox("Calendar Year", [2025, 2026, 2027], index=1)
                with f_c2:
                    sel_month = st.selectbox("Payroll Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1)
                with f_c3:
                    f_div_sum = st.selectbox("Filter Division", ["All", "Fleet Operations", "Civil Construction"])

                month_num = list(calendar.month_name).index(sel_month)
                start_date_str = f"{sel_year}-{month_num:02d}-01"
                last_day = calendar.monthrange(sel_year, month_num)[1]
                end_date_str = f"{sel_year}-{month_num:02d}-{last_day:02d}"

                if w_master.empty or "Worker_ID" not in w_master.columns:
                    st.info("Employee master directory is empty.")
                else:
                    workers = w_master.copy()
                    if f_div_sum != "All":
                        workers = workers[workers["Division"] == f_div_sum]

                    summary_rows = []
                    for _, w in workers.iterrows():
                        wid = str(w["Worker_ID"])
                        wname = str(w["Full_Name"])
                        wdiv = str(w["Division"])
                        wtype = str(w.get("Wage_Type", "DAILY_WAGE"))
                        rate = float(w.get("Base_Rate", 0.0) or 0.0)

                        p_days = 0.0
                        hd_days = 0.0
                        tot_ot_hrs = 0.0

                        if not muster_df.empty and "Date" in muster_df.columns and "Worker_ID" in muster_df.columns:
                            w_att = muster_df[(muster_df["Worker_ID"].astype(str) == wid) & 
                                              (muster_df["Date"].astype(str) >= start_date_str) & 
                                              (muster_df["Date"].astype(str) <= end_date_str)]
                            p_days = len(w_att[w_att["Attendance"] == "P"])
                            hd_days = len(w_att[w_att["Attendance"] == "HD"])
                            tot_ot_hrs = pd.to_numeric(w_att["OT_Hours"], errors="coerce").sum()

                        total_effective_attendance = p_days + (hd_days * 0.5)

                        # Automatic 8-Hour Overtime Shift Engine
                        if wtype == "DAILY_WAGE":
                            base_earnings = total_effective_attendance * rate
                            effective_hourly_rate = (rate / 8.0) if rate > 0 else 0.0
                        else:
                            per_day_sal = rate / 30.0 if rate > 0 else 0.0
                            base_earnings = total_effective_attendance * per_day_sal
                            effective_hourly_rate = (per_day_sal / 8.0) if per_day_sal > 0 else 0.0

                        ot_earnings = tot_ot_hrs * effective_hourly_rate
                        gross_earnings = base_earnings + ot_earnings

                        w_advances = 0.0
                        if not adv_df.empty and "Date" in adv_df.columns and "Worker_ID" in adv_df.columns:
                            matched_adv = adv_df[(adv_df["Worker_ID"].astype(str) == wid) & 
                                                 (adv_df["Date"].astype(str) >= start_date_str) & 
                                                 (adv_df["Date"].astype(str) <= end_date_str)]
                            w_advances = pd.to_numeric(matched_adv["Amount"], errors="coerce").sum()

                        net_payable = gross_earnings - w_advances

                        summary_rows.append({
                            "Employee ID": wid,
                            "Full Name": wname,
                            "Division": wdiv,
                            "Wage Model": wtype,
                            "Base Rate (₹)": rate,
                            "Effective Shifts": total_effective_attendance,
                            "OT Hours": tot_ot_hrs,
                            "Base Earnings (₹)": round(base_earnings, 2),
                            "OT Earnings (₹)": round(ot_earnings, 2),
                            "Gross Earnings (₹)": round(gross_earnings, 2),
                            "Advance Deductions (₹)": round(w_advances, 2),
                            "Net Payable (₹)": round(net_payable, 2)
                        })

                    sum_df = pd.DataFrame(summary_rows)
                    if not sum_df.empty:
                        t_gross = sum_df["Gross Earnings (₹)"].sum()
                        t_adv = sum_df["Advance Deductions (₹)"].sum()
                        t_net = sum_df["Net Payable (₹)"].sum()

                        m1, m2, m3 = st.columns(3)
                        m1.metric("Total Gross Earnings", f"₹{t_gross:,.2f}")
                        m2.metric("Total Deductions", f"₹{t_adv:,.2f}")
                        m3.metric("Total Net Settlement", f"₹{t_net:,.2f}")

                        st.dataframe(sum_df, use_container_width=True, hide_index=True)

                        csv_exp = sum_df.to_csv(index=False).encode('utf-8')
                        st.download_button(
                            label=f"📥 Export Payroll Abstract ({sel_month} {sel_year}) CSV",
                            data=csv_exp,
                            file_name=f"Payroll_Statement_{sel_month}_{sel_year}.csv",
                            mime="text/csv",
                            type="primary"
                        )


# --- HUB 2: TATA STEEL BILLING HUB ---
elif selected_module == "📑 Tata Steel Billing Hub":
    st.markdown("### 📑 Tata Steel Commercial & Billing Hub")
    st.caption("Fleet Duty Logs, RA Bill Calculations, Overtime Engine, and Asset Master")

    billing_tabs = [t for t in st.session_state.allowed_tabs if TAB_REGISTRY.get(t) == "Billing"]
    active_b_tabs = st.tabs(billing_tabs)

    for i, tab_title in enumerate(billing_tabs):
        with active_b_tabs[i]:
            st.subheader(tab_title)
            if tab_title == "Daily Logsheet Entry":
                st.info("Field Duty Slips & Bulk Calendar Upload (06:30 PM & 10-Hr Overtime Engine).")
            elif tab_title == "Monthly Statement & Abstract":
                st.info("RA Bill Cockpit with Dynamic RA Bill Dropdown and Date Pickers.")
            elif tab_title == "Locked RA Bills Repository":
                st.info("Committed Bill Archive with PDF & Excel export options.")
            elif tab_title == "Delivery Order (DO) Contract Management":
                st.info("Baseline Quantity, Rate, and Lumpsum Fuel ledger setup.")
            elif tab_title == "Vehicle Fleet Master Mapping":
                st.info("Equipment asset IDs mapped to contract DOs.")
            elif tab_title == "Commercial Rate Master":
                st.info("Contractual hiring rates, overtime rates, and fuel factors.")


# --- HUB 3: SITE FUEL RECORD (INDEPENDENT) ---
elif selected_module == "⛽ Site Fuel Record":
    st.markdown("### ⛽ Diesel & Fuel Management Hub")
    st.caption("Tanker Receipts, Daily Machine Issue Slips & Anti-Pilferage Audit")

    fuel_tabs = [t for t in st.session_state.allowed_tabs if TAB_REGISTRY.get(t) == "Fuel"]
    active_f_tabs = st.tabs(fuel_tabs)

    for i, tab_title in enumerate(fuel_tabs):
        with active_f_tabs[i]:
            st.subheader(tab_title)
            if tab_title == "Daily Diesel Issue Slip":
                st.info("Field Slip logger: Vehicle Number, Driver, Meter Reading, Liters Issued, and Slip Photo.")
            elif tab_title == "Fleet Fuel Benchmark Master":
                st.info("Asset-wise benchmark consumption limits (Liters/Hour or KM/Liter).")
            elif tab_title == "Theft & Pilferage Audit":
                st.info("Variance engine: Logsheet Running Hours vs Diesel Slip Consumption.")


# --- HUB 4: ADMIN COMMAND & RBAC MANAGER (ADMIN ONLY) ---
elif selected_module == "⚙️ Admin & User Permissions" and st.session_state.current_role == "ADMIN":
    st.markdown("### ⚙️ Identity & Access Management (RBAC)")
    st.caption("Provision credentials, define modular access boundaries, and manage user accounts")

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

                st.markdown("**Permitted Module Access:**")
                selected_permissions = []
                
                with st.expander("Worker Attendance Modules", expanded=True):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Attendance":
                            if st.checkbox(t_name, key=f"p_{t_name}", value=(new_role == "ADMIN")):
                                selected_permissions.append(t_name)

                with st.expander("Billing Modules", expanded=(new_role == "ADMIN")):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Billing":
                            if st.checkbox(t_name, key=f"p_{t_name}", value=(new_role == "ADMIN")):
                                selected_permissions.append(t_name)

                with st.expander("Fuel Control Modules", expanded=True):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Fuel":
                            if st.checkbox(t_name, key=f"p_{t_name}", value=(new_role == "ADMIN")):
                                selected_permissions.append(t_name)

                submit_user = st.form_submit_button("Save User Permissions", use_container_width=True, type="primary")

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
                            st.success(f"User account '{new_username}' saved successfully.")
                            st.rerun()

    with col_u2:
        with st.container(border=True):
            st.markdown("##### 👥 Active System Users & Credentials Ledger")
            users_table = load_sheet_df("app_users")
            if users_table.empty or "Username" not in users_table.columns:
                st.info("No system user records found in the database.")
            else:
                disp_cols = [c for c in ["Username", "Password", "Full_Name", "Role", "Contact_No", "Status", "Allowed_Tabs"] if c in users_table.columns]
                st.dataframe(users_table[disp_cols], use_container_width=True, hide_index=True)
                st.caption("Re-submit a username on the left to overwrite or update an account.")
