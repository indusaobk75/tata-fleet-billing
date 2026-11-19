import io
import json
import time
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

try:
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

from worker_attendance import (
    WORKER_MASTER_COLS, MUSTER_ROLL_COLS, ADVANCE_LOG_COLS,
    TATA_FORM_XVI_COLS, TATA_COMPLIANCE_COLS, WAGE_MASTER_COLS,
    PPE_POLICY_COLS, PPE_ISSUE_COLS, render_attendance_module
)
from tata_billing import render_billing_module
from fuel_and_audit import (
    ATTENDANCE_AUDIT_COLS, FUEL_FLEET_COLS, FUEL_ISSUE_COLS,
    FUEL_STOCK_COLS, FUEL_RECOVERY_COLS, render_fuel_module
)

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
    "GST": "GST No. 20AUOPK2982K1ZC",
    "VENDOR": "Vendor Code - 47",
    "ISO": "ISO Certified Company 45001:2018"
}

SHEET_NAME = st.secrets.get("spreadsheet_name", st.secrets.get("test_spreadsheet_name", "Tata_Steel_Fleet_DB"))

USER_COLUMNS = [
    "Username", "Password", "Full_Name", "Role", "Allowed_Tabs", "Allowed_Division",
    "Status", "Contact_No", "Emergency_Contact", "Photo_Base64"
]


# ----------------- 1. GOOGLE SHEETS ENGINE -----------------
@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if "gcp_json_raw" in st.secrets:
        creds_info = json.loads(st.secrets["gcp_json_raw"])
    elif "gcp_service_account" in st.secrets:
        creds_info = dict(st.secrets["gcp_service_account"])
    else:
        raise KeyError("Service account configuration missing from secrets.")
    credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(credentials)


@st.cache_data(ttl=5, show_spinner=False)
def load_sheet_df(worksheet_name):
    schema_map = {
        "worker_master": WORKER_MASTER_COLS,
        "muster_roll": MUSTER_ROLL_COLS,
        "worker_advances": ADVANCE_LOG_COLS,
        "tata_form_xvi": TATA_FORM_XVI_COLS,
        "tata_form_xvii": TATA_COMPLIANCE_COLS,
        "app_users": USER_COLUMNS,
        "wage_rate_master": WAGE_MASTER_COLS,
        "ppe_policy_master": PPE_POLICY_COLS,
        "ppe_issue_log": PPE_ISSUE_COLS,
        "fuel_fleet_master": FUEL_FLEET_COLS,
        "fuel_issue_log": FUEL_ISSUE_COLS,
        "fuel_stock_log": FUEL_STOCK_COLS,
        "fuel_recovery_log": FUEL_RECOVERY_COLS,
        "attendance_audit_log": ATTENDANCE_AUDIT_COLS
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
                ws = sh.add_worksheet(title=worksheet_name, rows="100", cols="25")
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


def get_effective_admin_password(users_df):
    if not users_df.empty and "Username" in users_df.columns:
        adm_match = users_df[users_df["Username"].astype(str).str.strip().str.lower() == DEFAULT_ADMIN_USER.lower()]
        if not adm_match.empty:
            saved_p = str(adm_match.iloc[0].get("Password", "")).strip()
            if saved_p:
                return saved_p
    return str(st.secrets.get("app_password", DEFAULT_ADMIN_PASS)).strip()


# ----------------- 2. SHARED BRANDING & LETTERHEAD HELPERS -----------------
def build_exact_indu_sao_oval_logo_stream():
    if not PIL_AVAILABLE:
        return None
    W, H = 660, 450
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.ellipse([12, 10, W - 12, H - 10], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=14)
    navy, black, white = (12, 24, 108, 255), (10, 10, 10, 255), (255, 255, 255, 255)

    draw.polygon([(175, 285), (175, 185), (235, 148), (235, 285)], fill=navy)
    draw.polygon([(248, 285), (248, 140), (315, 98), (315, 285)], fill=navy)
    draw.polygon([(275, 285), (275, 182), (315, 158), (315, 285)], fill=white)
    draw.polygon([(328, 285), (328, 90), (398, 46), (398, 285)], fill=navy)

    draw.rectangle([414, 56, 423, 285], fill=black)
    draw.rectangle([438, 76, 447, 285], fill=black)
    draw.rectangle([462, 96, 471, 285], fill=black)
    draw.polygon([(308, 255), (378, 290), (308, 290)], fill=navy)

    draw.pieslice([145, 282, 515, 392], start=95, end=255, fill=navy)
    draw.pieslice([198, 294, 485, 366], start=90, end=265, fill=white)
    draw.rectangle([312, 326, 362, 388], fill=navy)
    draw.ellipse([312, 302, 362, 320], fill=navy)

    draw.pieslice([185, 305, 535, 422], start=335, end=105, fill=black)
    draw.pieslice([175, 312, 475, 398], start=330, end=110, fill=white)
    draw.rectangle([312, 326, 362, 388], fill=navy)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


def draw_indu_sao_letterhead(pdf):
    pdf.set_fill_color(46, 125, 50)
    pdf.polygon([(0, 47), (210, 36), (210, 39.5), (0, 50.5)], style="F")
    pdf.set_fill_color(244, 180, 0)
    pdf.polygon([(0, 43.5), (210, 32.5), (210, 36), (0, 47)], style="F")
    pdf.set_fill_color(56, 163, 241)
    pdf.polygon([(0, 0), (210, 0), (210, 32.5), (0, 43.5)], style="F")

    logo_stream = build_exact_indu_sao_oval_logo_stream()
    if logo_stream:
        pdf.image(logo_stream, x=7.5, y=3.0, w=48.0, h=32.5)

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(7, 36.5)
    pdf.cell(54, 4, "ISO Certified Company 45001:2018", align="L")

    pdf.set_xy(64, 4.5)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(138, 8, "M/s INDU SAO", ln=True)

    pdf.set_font("Helvetica", "B", 8.5)
    for line in [
        "Office Address: Sondiha, Chainpur, Dist Ramgarh, (Jharkhand)",
        "Pin No. 829134   Call 9234779896, 9661152652",
        "Email: indusao_bk@yahoo.com",
        "GST No. 20AUOPK2982K1ZC",
        "Vendor Code - 47"
    ]:
        pdf.set_x(64)
        pdf.cell(138, 4.2, line, ln=True)


# ----------------- 3. WORKSPACES & RBAC -----------------
TAB_REGISTRY = {
    "➕ Employee Enrolment": "Attendance",
    "📋 Employee Master Register": "Attendance",
    "⚖️ Statutory Wage Rates": "Attendance",
    "Daily Muster Roll (Field Attendance)": "Attendance",
    "Monthly Attendance Matrix": "Attendance",
    "🦺 PPE & Safety Gear Issue": "Attendance",
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

WORKSPACES_TREE = [
    {
        "id": "Attendance", "icon": "👷", "title": "Worker Attendance",
        "submenus": [
            ("Employee Enrolment", "➕ Employee Enrolment"),
            ("Master Register & ID Cards", "📋 Employee Master Register"),
            ("Statutory Wage Rates", "⚖️ Statutory Wage Rates"),
            ("Daily Field Muster Roll", "Daily Muster Roll (Field Attendance)"),
            ("Monthly Attendance Matrix", "Monthly Attendance Matrix"),
            ("PPE & Safety Gear Issue", "🦺 PPE & Safety Gear Issue"),
            ("Advance & Expenses", "Advance & Expense Register"),
            ("Wages, Slips & Bank Letters", "Monthly Wages & Payout Summary")
        ]
    },
    {
        "id": "Billing", "icon": "📑", "title": "Tata Steel Billing",
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
        "id": "Fuel", "icon": "⛽", "title": "Site Fuel Record",
        "submenus": [
            ("Daily Fuel & Bowser Slip", "Daily Diesel Issue Slip"),
            ("Vehicle Fuel Benchmark", "Fleet Fuel Benchmark Master"),
            ("Variance Audit & Recovery", "Theft & Pilferage Audit")
        ]
    }
]

INDU_SAO_SVG_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 120" style="width: 36px; height: 26px;"><ellipse cx="80" cy="60" rx="76" ry="56" fill="#ffffff" stroke="#1e293b" stroke-width="5"/><polygon points="54,90 54,42 70,30 70,90" fill="#1e293b"/><polygon points="76,90 76,22 92,12 92,90" fill="#3730a3"/><rect x="98" y="24" width="4" height="66" fill="#64748b"/><rect x="105" y="32" width="4" height="58" fill="#64748b"/><path d="M 32,76 C 50,98 112,98 128,78 C 114,90 58,90 32,76 Z" fill="#1e293b"/><circle cx="80" cy="74" r="5" fill="#3730a3"/></svg>"""

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }
    .stApp { background-color: #ffffff; }
    .block-container { padding-top: 3.2rem !important; padding-bottom: 2.5rem !important; padding-left: 2.0rem !important; padding-right: 2.0rem !important; max-width: 100% !important; }
    section[data-testid="stSidebar"][aria-expanded="true"] { background-color: #f8fafc !important; border-right: 1px solid #e2e8f0 !important; width: 275px !important; min-width: 275px !important; }
    section[data-testid="stSidebar"][aria-expanded="false"] { width: 0px !important; min-width: 0px !important; margin-left: -275px !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"] { width: 100% !important; min-height: 38px !important; text-align: left !important; justify-content: flex-start !important; background-color: transparent !important; border: none !important; box-shadow: none !important; font-size: 0.88rem !important; font-weight: 700 !important; color: #0f172a !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="tertiary"] { min-height: 33px !important; text-align: left !important; justify-content: flex-start !important; background-color: transparent !important; border: none !important; border-left: 2px solid #cbd5e1 !important; margin-left: 14px !important; width: calc(100% - 14px) !important; font-size: 0.82rem !important; color: #475569 !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="primary"] { width: calc(100% - 14px) !important; min-height: 34px !important; text-align: left !important; justify-content: flex-start !important; background-color: #e0e7ff !important; color: #1e3a8a !important; border: none !important; border-left: 3px solid #4f46e5 !important; border-radius: 0px 16px 16px 0px !important; margin-left: 14px !important; font-size: 0.83rem !important; font-weight: 600 !important; }
    div.stMain div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"], div.stDownloadButton > button[kind="primary"] { background-color: #1e293b !important; color: #ffffff !important; border: 1px solid #1e293b !important; border-radius: 8px !important; }
    .top-user-avatar { width: 32px; height: 32px; border-radius: 50%; background: #3730a3; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 0.78rem; color: white; }
    .status-badge { display: inline-block; padding: 1px 6px; border-radius: 9999px; font-size: 0.60rem; font-weight: 600; text-transform: uppercase; }
    .status-admin { background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
    .status-user { background-color: #eef2ff; color: #3730a3; border: 1px solid #c7d2fe; }
</style>
""", unsafe_allow_html=True)


# ----------------- 4. AUTHENTICATION & SESSION -----------------
def init_session():
    defaults = {
        "user_authenticated": False, "current_user": None, "current_name": None,
        "current_role": None, "allowed_tabs": [], "allowed_division": "All Divisions",
        "active_workspace": "Attendance", "active_subtab": "Daily Muster Roll (Field Attendance)",
        "selected_worker_card_id": None
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_session()


def login_screen():
    _, c2, _ = st.columns([1, 1.2, 1])
    with c2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        st.markdown(f"<div style='text-align: center; margin-bottom: 24px;'>{INDU_SAO_SVG_LOGO}<h2 style='margin: 8px 0 0 0; color: #0f172a;'>{COMPANY_HEADER['NAME']}</h2><div style='font-size: 0.82rem; color: #64748b;'>{COMPANY_HEADER['GST']} | {COMPANY_HEADER['VENDOR']}</div></div>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("##### Sign in to Workspace")
            username = st.text_input("Username").strip()
            password = st.text_input("Password", type="password").strip()
            if st.button("Continue →", use_container_width=True, type="primary"):
                u_lower = username.lower()
                users_df = load_sheet_df("app_users")
                master_pass = get_effective_admin_password(users_df)

                if u_lower == DEFAULT_ADMIN_USER.lower() and password == master_pass:
                    st.session_state.update({
                        "user_authenticated": True, "current_user": DEFAULT_ADMIN_USER,
                        "current_name": DEFAULT_ADMIN_NAME, "current_role": "ADMIN",
                        "allowed_tabs": list(TAB_REGISTRY.keys()), "allowed_division": "All Divisions"
                    })
                    st.rerun()

                if not users_df.empty and "Username" in users_df.columns:
                    users_df["_u_clean"] = users_df["Username"].astype(str).str.strip().str.lower()
                    u_match = users_df[users_df["_u_clean"] == u_lower]
                    if not u_match.empty:
                        u_row = u_match.iloc[0]
                        if str(u_row.get("Status", "ACTIVE")).strip().upper() == "ACTIVE" and password == str(u_row.get("Password", "")).strip():
                            u_role = str(u_row.get("Role", "USER")).strip().upper()
                            permitted = list(TAB_REGISTRY.keys()) if u_role == "ADMIN" else [t.strip() for t in str(u_row.get("Allowed_Tabs", "")).split(",") if t.strip() in TAB_REGISTRY]
                            raw_div_perm = str(u_row.get("Allowed_Division", "All Divisions")).strip()
                            if u_role == "ADMIN" or raw_div_perm not in ["Fleet Operations", "Civil Construction"]:
                                div_perm = "All Divisions"
                            else:
                                div_perm = raw_div_perm

                            st.session_state.update({
                                "user_authenticated": True, "current_user": str(u_row["Username"]),
                                "current_name": str(u_row.get("Full_Name", username)).strip() or username,
                                "current_role": u_role, "allowed_tabs": permitted, "allowed_division": div_perm
                            })
                            if permitted:
                                st.session_state.active_subtab = permitted[0]
                                st.session_state.active_workspace = TAB_REGISTRY.get(permitted[0], "Attendance")
                            st.rerun()
                st.error("Invalid credentials or inactive account.")


if not st.session_state.user_authenticated:
    login_screen()
    st.stop()


# ----------------- 5. SIDEBAR & TOP HEADER -----------------
with st.sidebar:
    st.markdown(f"<div style='display: flex; align-items: center; gap: 10px; padding: 2px 4px 12px 4px; margin-bottom: 6px; border-bottom: 1px solid #e2e8f0;'>{INDU_SAO_SVG_LOGO}<div><div style='font-size: 0.94rem; font-weight: 700; color: #0f172a;'>{COMPANY_HEADER['NAME']}</div><div style='font-size: 0.68rem; color: #64748b;'>West Bokaro • ID47</div></div></div>", unsafe_allow_html=True)

    for ws in WORKSPACES_TREE:
        ws_id = ws["id"]
        permitted_submenus = [(d, k) for d, k in ws["submenus"] if st.session_state.current_role == "ADMIN" or k in st.session_state.allowed_tabs]
        if not permitted_submenus and st.session_state.current_role != "ADMIN":
            continue

        is_open = (st.session_state.active_workspace == ws_id)
        chevron = "▾" if is_open else "▸"
        if st.button(f"{chevron}  {ws['icon']}  {ws['title']}", key=f"nav_ws_{ws_id}", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = ws_id
            st.session_state.selected_worker_card_id = None
            if permitted_submenus and st.session_state.active_subtab not in [s[1] for s in permitted_submenus]:
                st.session_state.active_subtab = permitted_submenus[0][1]
            st.rerun()

        if is_open:
            for disp_name, reg_key in permitted_submenus:
                is_sub_active = (st.session_state.active_subtab == reg_key)
                if st.button(disp_name, key=f"nav_sub_{reg_key}", use_container_width=True, type="primary" if is_sub_active else "tertiary"):
                    st.session_state.active_subtab = reg_key
                    st.session_state.selected_worker_card_id = None
                    st.rerun()

    if st.session_state.current_role == "ADMIN":
        st.markdown("<hr style='margin: 8px 0; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)
        is_adm = (st.session_state.active_workspace == "Admin")
        if st.button(f"{'▾' if is_adm else '▸'}  ⚙️  Access & Settings", key="nav_ws_admin", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = "Admin"
            st.session_state.active_subtab = "User Accounts & RBAC"
            st.rerun()
        if is_adm:
            if st.button("User Permissions & Password", key="nav_sub_rbac", use_container_width=True, type="primary"):
                st.session_state.active_subtab = "User Accounts & RBAC"
                st.rerun()

user_display_name = st.session_state.get("current_name") or st.session_state.get("current_user") or "User"
initials = "".join([p[0] for p in str(user_display_name).split()[:2] if p]).upper() or "US"
badge_class = "status-admin" if st.session_state.current_role == "ADMIN" else "status-user"
div_badge = f" • {st.session_state.allowed_division}" if st.session_state.allowed_division != "All Divisions" else ""

h1, h2, h3 = st.columns([2.8, 1.5, 0.8], gap="small")
with h1:
    ws_map = {"Attendance": "Worker Attendance", "Billing": "Tata Steel Billing", "Fuel": "Site Fuel Record", "Admin": "Administration"}
    st.markdown(f"<div style='display: flex; align-items: center; gap: 8px; height: 34px;'><span style='font-size: 0.84rem; color: #64748b;'>{ws_map.get(st.session_state.active_workspace, '')}</span><span style='color: #cbd5e1;'>/</span><span style='font-size: 0.86rem; color: #0f172a; font-weight: 600;'>{st.session_state.active_subtab}</span></div>", unsafe_allow_html=True)
with h2:
    st.markdown(f"<div style='display: flex; align-items: center; justify-content: flex-end; gap: 8px; height: 34px;'><div class='top-user-avatar'>{initials}</div><div style='line-height: 1.15;'><div style='font-weight: 600; font-size: 0.8rem; color: #0f172a;'>{user_display_name}</div><span class='status-badge {badge_class}'>{st.session_state.current_role}{div_badge}</span></div></div>", unsafe_allow_html=True)
with h3:
    if st.button("Sign Out", use_container_width=True, key="hdr_out", type="secondary"):
        st.session_state.user_authenticated = False
        st.rerun()

st.markdown("<hr style='margin-top: 10px; margin-bottom: 18px; border-top: 1px solid #f1f5f9;' />", unsafe_allow_html=True)


# ----------------- 6. MODULE DISPATCHER -----------------
current_view = st.session_state.active_subtab
mod_type = TAB_REGISTRY.get(current_view)

if mod_type == "Attendance":
    render_attendance_module(
        current_view, load_sheet_df, save_sheet_df,
        st.session_state.current_user, user_display_name, st.session_state.current_role,
        draw_indu_sao_letterhead, build_exact_indu_sao_oval_logo_stream,
        allowed_division=st.session_state.allowed_division
    )
elif mod_type == "Billing":
    render_billing_module(current_view, load_sheet_df, save_sheet_df)
elif mod_type == "Fuel":
    render_fuel_module(
        current_view, load_sheet_df, save_sheet_df,
        st.session_state.current_user, st.session_state.current_role, draw_indu_sao_letterhead
    )
elif current_view == "User Accounts & RBAC" and st.session_state.current_role == "ADMIN":
    st.markdown("#### User Accounts, Permissions & Admin Security")
    col_u1, col_u2 = st.columns([1.15, 1.85], gap="large")

    with col_u1:
        with st.container(border=True):
            st.markdown("###### 🔑 Change Master Admin Password")
            with st.form("change_admin_pass_form", clear_on_submit=True):
                cur_adm_p = st.text_input("Current Admin Password", type="password").strip()
                new_adm_p1 = st.text_input("New Admin Password", type="password").strip()
                new_adm_p2 = st.text_input("Confirm New Password", type="password").strip()
                if st.form_submit_button("Update Admin Password", type="primary", use_container_width=True):
                    users_df = load_sheet_df("app_users")
                    actual_cur_p = get_effective_admin_password(users_df)
                    if cur_adm_p != actual_cur_p:
                        st.error("Current Admin Password is incorrect.")
                    elif len(new_adm_p1) < 6:
                        st.error("New password must be at least 6 characters long.")
                    elif new_adm_p1 != new_adm_p2:
                        st.error("New password and confirmation do not match.")
                    else:
                        if not users_df.empty and "Username" in users_df.columns:
                            users_df = users_df[users_df["Username"].astype(str).str.strip().str.lower() != DEFAULT_ADMIN_USER.lower()]
                        adm_row = pd.DataFrame([{
                            "Username": DEFAULT_ADMIN_USER, "Password": new_adm_p1, "Full_Name": DEFAULT_ADMIN_NAME,
                            "Role": "ADMIN", "Allowed_Tabs": "ALL", "Allowed_Division": "All Divisions",
                            "Status": "ACTIVE", "Contact_No": "", "Emergency_Contact": "", "Photo_Base64": ""
                        }])
                        if save_sheet_df("app_users", pd.concat([adm_row, users_df], ignore_index=True)[USER_COLUMNS]):
                            st.success("Master Admin Password updated! Use the new password on your next login.")

        st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("###### Provision / Update User")
            with st.form("create_user_form", clear_on_submit=False):
                new_username = st.text_input("Username").strip()
                new_password = st.text_input("Password", type="password").strip()
                new_name = st.text_input("Full Name").strip()
                new_contact = st.text_input("Contact Number").strip()

                fc1, fc2 = st.columns(2)
                new_role = fc1.selectbox("Role", ["USER", "ADMIN"])
                new_status = fc2.selectbox("Status", ["ACTIVE", "INACTIVE"])

                new_allowed_div = st.selectbox(
                    "Allowed Workforce Division",
                    ["All Divisions", "Fleet Operations", "Civil Construction"],
                    help="Restricts this user to view and manage attendance ONLY for the selected division."
                )

                selected_permissions = []
                for grp_label, grp_id in [("Worker Attendance", "Attendance"), ("Tata Steel Billing", "Billing"), ("Site Fuel Record", "Fuel")]:
                    with st.expander(grp_label, expanded=(grp_id == "Attendance" or new_role == "ADMIN")):
                        for t_name, m_t in TAB_REGISTRY.items():
                            if m_t == grp_id:
                                if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=(new_role == "ADMIN")):
                                    selected_permissions.append(t_name)

                if st.form_submit_button("Save User Account", use_container_width=True, type="primary"):
                    if not new_username or not new_password:
                        st.error("Username and password are required.")
                    elif new_username.lower() == DEFAULT_ADMIN_USER.lower():
                        st.warning("Use the 'Change Master Admin Password' section above to update the main admin account.")
                    else:
                        users_df = load_sheet_df("app_users")
                        users_df["_u_chk"] = users_df["Username"].astype(str).str.strip().str.lower()
                        has_admin = not users_df[users_df["_u_chk"] == DEFAULT_ADMIN_USER.lower()].empty
                        users_df = users_df[users_df["_u_chk"] != new_username.lower()].drop(columns=["_u_chk"])

                        rows_to_add = []
                        if not has_admin:
                            rows_to_add.append({
                                "Username": DEFAULT_ADMIN_USER, "Password": get_effective_admin_password(users_df),
                                "Full_Name": DEFAULT_ADMIN_NAME, "Role": "ADMIN", "Allowed_Tabs": "ALL",
                                "Allowed_Division": "All Divisions", "Status": "ACTIVE",
                                "Contact_No": "", "Emergency_Contact": "", "Photo_Base64": ""
                            })
                        rows_to_add.append({
                            "Username": new_username, "Password": new_password, "Full_Name": new_name, "Role": new_role,
                            "Allowed_Tabs": ",".join(selected_permissions) if new_role != "ADMIN" else "ALL",
                            "Allowed_Division": "All Divisions" if new_role == "ADMIN" else new_allowed_div,
                            "Status": new_status, "Contact_No": new_contact, "Emergency_Contact": "", "Photo_Base64": ""
                        })
                        if save_sheet_df("app_users", pd.concat([users_df, pd.DataFrame(rows_to_add)], ignore_index=True)[USER_COLUMNS]):
                            st.success(f"User '{new_username}' saved ({new_allowed_div}).")
                            st.rerun()

    with col_u2:
        with st.container(border=True):
            st.markdown("###### Active System Users")
            users_table = load_sheet_df("app_users")
            if not users_table.empty and "Username" in users_table.columns:
                disp_u = users_table[users_table["Username"].astype(str).str.strip().str.lower() != DEFAULT_ADMIN_USER.lower()].copy()
                if disp_u.empty:
                    st.info("No additional supervisor/user accounts created yet.")
                else:
                    disp_u["Allowed_Division"] = disp_u["Allowed_Division"].replace("", "All Divisions")
                    st.dataframe(disp_u[[c for c in ["Username", "Password", "Full_Name", "Role", "Allowed_Division", "Status", "Allowed_Tabs"] if c in disp_u.columns]], use_container_width=True, hide_index=True)

                    st.markdown("---")
                    st.markdown("###### 🗑️ Revoke / Permanently Remove User")
                    non_admin_users = disp_u["Username"].astype(str).tolist()
                    rc1, rc2 = st.columns([2.0, 1.0])
                    with rc1:
                        u_to_revoke = st.selectbox("Select User to Remove", non_admin_users, label_visibility="collapsed")
                    with rc2:
                        if st.button("🗑️ Delete User", type="secondary", use_container_width=True):
                            clean_u = users_table[users_table["Username"].astype(str) != u_to_revoke]
                            if save_sheet_df("app_users", clean_u[USER_COLUMNS]):
                                st.success(f"User '{u_to_revoke}' permanently removed.")
                                st.rerun()
