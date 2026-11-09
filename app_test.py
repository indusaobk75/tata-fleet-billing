import io
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
    page_title="Tata Steel Mining ERP | Staging",
    page_icon="🏗️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------- 0. BULLETPROOF ADMIN MASTER CONFIG -----------------
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "Admin@2026"
DEFAULT_ADMIN_NAME = "Indu Sao (Owner)"

# ----------------- 1. DATABASE & CONFIG (TEST ISOLATION) -----------------
SHEET_NAME = st.secrets.get("test_spreadsheet_name", st.secrets.get("spreadsheet_name", "Tata_Steel_ERP_TEST_DB"))

USER_COLUMNS = [
    "Username", "Password", "Full_Name", "Role", "Allowed_Tabs", "Status",
    "Contact_No", "Emergency_Contact", "Photo_Base64"
]

@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if "gcp_json_raw" in st.secrets:
        creds_info = json.loads(st.secrets["gcp_json_raw"])
    elif "gcp_service_account" in st.secrets:
        creds_info = dict(st.secrets["gcp_service_account"])
    else:
        raise KeyError("Neither 'gcp_json_raw' nor 'gcp_service_account' found in secrets!")
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


# ----------------- 2. MASTER REGISTRY & ENTERPRISE UI STYLING -----------------
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

    # MODULE 3: WORKER ATTENDANCE
    "Worker Daily Muster Roll": "Attendance",
    "Driver & Operator Pool": "Attendance",
    "Monthly Attendance Summary": "Attendance"
}

st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 2rem; }

    /* Top Executive Navigation Bar */
    .top-navbar-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 10px 18px;
        margin-bottom: 14px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
    }
    .top-user-avatar {
        width: 38px;
        height: 38px;
        border-radius: 50%;
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 0.95rem;
        color: white;
        object-fit: cover;
    }
    .status-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 9999px;
        font-size: 0.68rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .status-admin { background-color: #064e3b; color: #34d399; border: 1px solid #059669; }
    .status-user { background-color: #1e3a8a; color: #93c5fd; border: 1px solid #2563eb; }

    /* Custom Professional Nav Button Styles */
    div[data-testid="stSidebar"] div.stButton > button {
        text-align: left;
        justify-content: flex-start;
        border-radius: 9px;
        font-weight: 500;
        padding: 10px 14px;
        margin-bottom: 4px;
        border: 1px solid #e2e8f0;
        background-color: #ffffff;
        color: #1e293b;
        transition: all 0.2s ease;
    }
    div[data-testid="stSidebar"] div.stButton > button:hover {
        background-color: #f1f5f9;
        border-color: #cbd5e1;
        color: #0f172a;
    }
    div[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #1e293b 0%, #334155 100%) !important;
        color: #ffffff !important;
        border-color: #1e293b !important;
        font-weight: 600 !important;
        box-shadow: 0 3px 6px -1px rgba(0, 0, 0, 0.15);
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

init_session()

def login_screen():
    c1, c2, c3 = st.columns([1, 1.4, 1])
    with c2:
        st.markdown(
            """
            <div style="text-align: center; margin-bottom: 24px; margin-top: 40px;">
                <h2 style="margin-bottom: 4px;">🏗️ Site Operations ERP</h2>
                <span style="font-size: 0.9rem; color: #64748b; font-weight: 500;">
                    M/S INDU SAO | Tata Steel West Bokaro Division
                </span>
            </div>
            """, 
            unsafe_allow_html=True
        )

        with st.container(border=True):
            st.markdown("##### Portal Authentication")
            st.caption("Sign in with your authorized operational credentials")
            
            username = st.text_input("Username / Operator ID", placeholder="e.g. admin or supervisor1").strip()
            password = st.text_input("Security Passkey", type="password", placeholder="••••••••").strip()
            submitted = st.button("Sign In to Workspace", use_container_width=True, type="primary")

            if submitted:
                if not username or not password:
                    st.warning("Please enter both Username and Passkey.")
                    return

                u_lower = username.lower()
                configured_master_pass = st.secrets.get("app_password", DEFAULT_ADMIN_PASS)

                # PRIORITY 1: BULLETPROOF ADMIN AUTHENTICATION
                if u_lower == DEFAULT_ADMIN_USER.lower() and password == configured_master_pass:
                    st.session_state.user_authenticated = True
                    st.session_state.current_user = DEFAULT_ADMIN_USER
                    st.session_state.current_name = DEFAULT_ADMIN_NAME
                    st.session_state.current_role = "ADMIN"
                    st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                    st.session_state.active_module = "📑 Tata Steel Billing Hub"
                    st.rerun()

                # PRIORITY 2: DATABASE USERS AUTHENTICATION
                users_df = load_sheet_df("app_users")
                
                if users_df.empty or "Username" not in users_df.columns:
                    st.error("Authentication database is offline or uninitialized. Use master admin credentials.")
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
                        st.error("Account access revoked. Contact system administrator.")
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
                            st.session_state.active_module = "📑 Tata Steel Billing Hub"
                        else:
                            raw_tabs = str(u_row.get("Allowed_Tabs", ""))
                            permitted = [t.strip() for t in raw_tabs.split(",") if t.strip() in TAB_REGISTRY]
                            st.session_state.allowed_tabs = permitted
                            
                            mods = set([TAB_REGISTRY[t] for t in permitted])
                            if "Billing" in mods:
                                st.session_state.active_module = "📑 Tata Steel Billing Hub"
                            elif "Fuel" in mods:
                                st.session_state.active_module = "⛽ Site Fuel Record"
                            elif "Attendance" in mods:
                                st.session_state.active_module = "👷 Worker Attendance"

                        st.rerun()
                    else:
                        st.error("Incorrect password entered.")
                else:
                    st.error("Authorized user profile not found.")

if not st.session_state.user_authenticated:
    login_screen()
    st.stop()


# ----------------- 4. CLEAN SIDEBAR (NAVIGATION ONLY) -----------------
with st.sidebar:
    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 20px;">
            <div style="background: #e0f2fe; padding: 8px; border-radius: 8px; display: inline-flex;">
                <span style="font-size: 1.3rem;">🏗️</span>
            </div>
            <div>
                <div style="font-size: 1.05rem; font-weight: 700; color: #0f172a; line-height: 1.2;">M/S INDU SAO</div>
                <div style="font-size: 0.72rem; color: #64748b; font-weight: 600; letter-spacing: 0.05em;">ENTERPRISE PORTAL</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.markdown("<div style='margin-bottom: 8px; font-size: 0.75rem; font-weight: 700; color: #64748b; letter-spacing: 0.06em; text-transform: uppercase;'>OPERATIONAL WORKSPACES</div>", unsafe_allow_html=True)

    available_modules = set()
    for tab_name in st.session_state.allowed_tabs:
        available_modules.add(TAB_REGISTRY.get(tab_name))

    module_options = []
    if "Billing" in available_modules:
        module_options.append("📑 Tata Steel Billing Hub")
    if "Fuel" in available_modules:
        module_options.append("⛽ Site Fuel Record")
    if "Attendance" in available_modules:
        module_options.append("👷 Worker Attendance")
    if st.session_state.current_role == "ADMIN":
        module_options.append("⚙️ Admin & User Permissions")

    if not module_options:
        st.warning("No operational modules have been provisioned for this account.")
        st.stop()

    if st.session_state.active_module not in module_options:
        st.session_state.active_module = module_options[0]

    # Clean Navigation Tiles
    for mod_title in module_options:
        is_active = (st.session_state.active_module == mod_title)
        btn_type = "primary" if is_active else "secondary"
        if st.button(mod_title, key=f"nav_tile_{mod_title}", use_container_width=True, type=btn_type):
            st.session_state.active_module = mod_title
            st.session_state.show_profile_box = False
            st.rerun()

    selected_module = st.session_state.active_module


# ----------------- 5. EXECUTIVE TOP-RIGHT HEADER STRIP -----------------
user_display_name = st.session_state.get("current_name") or st.session_state.get("current_user") or "User"
name_parts = str(user_display_name).strip().split()
initials = "".join([p[0] for p in name_parts[:2] if p]).upper() if name_parts else "US"
badge_class = "status-admin" if st.session_state.current_role == "ADMIN" else "status-user"

avatar_html = f'<div class="top-user-avatar">{initials}</div>'
if st.session_state.get("current_photo"):
    avatar_html = f'<img src="data:image/png;base64,{st.session_state.current_photo}" class="top-user-avatar" />'

# Header Container
with st.container():
    h_col1, h_col2, h_col3 = st.columns([2.2, 1.2, 0.6], gap="small")
    
    with h_col1:
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; gap: 12px; padding: 4px 0;">
                <div>
                    <span style="font-weight: 700; font-size: 1.15rem; color: #0f172a;">Site Operations ERP</span>
                    <span style="margin: 0 8px; color: #cbd5e1;">|</span>
                    <span style="font-size: 0.85rem; color: #64748b; font-weight: 500;">Tata Steel West Bokaro Division</span>
                </div>
            </div>
            """, 
            unsafe_allow_html=True
        )

    with h_col2:
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; justify-content: flex-end; gap: 10px; padding: 4px 0;">
                {avatar_html}
                <div style="text-align: left; line-height: 1.2;">
                    <div style="font-weight: 600; font-size: 0.88rem; color: #0f172a;">{user_display_name}</div>
                    <div style="display: flex; align-items: center; gap: 4px; margin-top: 2px;">
                        <span class="status-badge {badge_class}">{st.session_state.current_role}</span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    with h_col3:
        btn_c1, btn_c2 = st.columns(2)
        with btn_c1:
            if st.button("⚙️", help="My Profile & Security Settings", use_container_width=True):
                st.session_state.show_profile_box = not st.session_state.show_profile_box
                st.rerun()
        with btn_c2:
            if st.button("🚪", help="Logout Session", use_container_width=True):
                st.session_state.user_authenticated = False
                st.session_state.current_user = None
                st.session_state.current_name = None
                st.session_state.current_role = None
                st.session_state.allowed_tabs = []
                st.session_state.active_module = None
                st.session_state.show_profile_box = False
                st.rerun()

    st.markdown("<hr style='margin-top: 4px; margin-bottom: 16px; border: none; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)


# --- TOP PROFILE EDIT CARD (TOGGLED VIA ⚙️ BUTTON) ---
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
            st.caption(f"**Role Access:** `{st.session_state.current_role}`")
            st.caption(f"**Permitted Tabs:** `{len(st.session_state.allowed_tabs)} modules`")

        with p_c2:
            st.markdown("##### ✏️ Update Contact & Security Passkey")
            with st.form("top_profile_form"):
                f_name = st.text_input("Full Name", value=st.session_state.current_name or "")
                f_contact = st.text_input("Mobile / WhatsApp Number", value=st.session_state.current_contact or "")
                f_emer = st.text_input("Emergency Contact Number", value=st.session_state.current_emergency or "")
                f_pass = st.text_input("Update Passkey", placeholder="Type new password to change", type="password")
                f_photo = st.file_uploader("Upload Profile Picture", type=["png", "jpg", "jpeg"])

                sub_prof = st.form_submit_button("Save & Synchronize Profile", type="primary", use_container_width=True)

                if sub_prof:
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
                            st.success("Profile updated successfully!")
                            st.rerun()
                    else:
                        st.session_state.show_profile_box = False
                        st.info("Master Admin profile synchronized for this session.")
                        st.rerun()


# ----------------- 6. OPERATIONAL HUBS VIEWPORT -----------------

# --- HUB 1: TATA STEEL BILLING HUB ---
if selected_module == "📑 Tata Steel Billing Hub":
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
                st.info("RA Bill Cockpit with Dynamic RA Bill [Next] Dropdown and Direct Start/End Date Pickers.")
            elif tab_title == "Locked RA Bills Repository":
                st.info("Committed Bill Archive with PDF & Excel downloads.")
            elif tab_title == "Delivery Order (DO) Contract Management":
                st.info("Baseline Qty, Rate, and 1:1 LUMPSUM Fuel ledger setup.")
            elif tab_title == "Vehicle Fleet Master Mapping":
                st.info("Equipment asset IDs mapped to contract DOs.")
            elif tab_title == "Commercial Rate Master":
                st.info("Hiring rates, overtime rates, and fuel factors.")


# --- HUB 2: SITE FUEL RECORD (INDEPENDENT) ---
elif selected_module == "⛽ Site Fuel Record":
    st.markdown("### ⛽ Diesel & Fuel Management Hub")
    st.caption("Tanker Receipts, Daily Machine Issue Slips & Anti-Pilferage Audit (Decoupled System)")

    fuel_tabs = [t for t in st.session_state.allowed_tabs if TAB_REGISTRY.get(t) == "Fuel"]
    active_f_tabs = st.tabs(fuel_tabs)

    for i, tab_title in enumerate(fuel_tabs):
        with active_f_tabs[i]:
            st.subheader(tab_title)
            if tab_title == "Daily Diesel Issue Slip":
                st.info("Field Slip logger: Vehicle No, Driver, Meter Reading, Liters Issued, Slip Photo Upload.")
            elif tab_title == "Fleet Fuel Benchmark Master":
                st.info("Asset-wise benchmark consumption limits (L/Hr or KM/L).")
            elif tab_title == "Theft & Pilferage Audit":
                st.info("Variance engine: Logsheet Running Hours vs Diesel Slip Consumption.")


# --- HUB 3: WORKER ATTENDANCE (INDEPENDENT) ---
elif selected_module == "👷 Worker Attendance":
    st.markdown("### 👷 Site Muster Roll & Worker Operations")
    st.caption("Driver & Operator Shifts, Daily Site Muster, and Monthly Wage Summaries")

    att_tabs = [t for t in st.session_state.allowed_tabs if TAB_REGISTRY.get(t) == "Attendance"]
    active_a_tabs = st.tabs(att_tabs)

    for i, tab_title in enumerate(att_tabs):
        with active_a_tabs[i]:
            st.subheader(tab_title)
            if tab_title == "Worker Daily Muster Roll":
                st.info("Daily marking: P (Present), A (Absent), HD (Half Day), WO (Weekly Off), PH (Paid Holiday).")
            elif tab_title == "Driver & Operator Pool":
                st.info("Operator master directory: KYC, Contact, License, and Designated Machine.")
            elif tab_title == "Monthly Attendance Summary":
                st.info("Monthly attendance sheet consolidation for contractor wage payouts.")


# --- HUB 4: ADMIN COMMAND & RBAC MANAGER (ADMIN ONLY) ---
elif selected_module == "⚙️ Admin & User Permissions" and st.session_state.current_role == "ADMIN":
    st.markdown("### ⚙️ Identity & Access Management (RBAC)")
    st.caption("Provision employee credentials, define modular access boundaries, and audit live user credentials")

    col_u1, col_u2 = st.columns([1.1, 1.9], gap="large")

    with col_u1:
        with st.container(border=True):
            st.markdown("##### ➕ Provision / Update User")
            
            with st.form("create_user_form", clear_on_submit=False):
                new_username = st.text_input("Username (Unique ID)", placeholder="e.g. sup_ramesh").strip()
                new_password = st.text_input("Password (Plain Text)", placeholder="Set initial password").strip()
                new_name = st.text_input("Full Name", placeholder="e.g. Ramesh Kumar").strip()
                new_contact = st.text_input("Contact Number", placeholder="e.g. 9876543210").strip()
                
                f_c1, f_c2 = st.columns(2)
                with f_c1:
                    new_role = st.selectbox("Role", ["USER", "ADMIN"])
                with f_c2:
                    new_status = st.selectbox("Status", ["ACTIVE", "INACTIVE"])

                st.markdown("**Permitted Module Access:**")
                selected_permissions = []
                
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

                with st.expander("Worker Attendance Modules", expanded=True):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Attendance":
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

                        # BULLETPROOF ADMIN SAFEGUARD
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
                            st.success(f"User account '{new_username}' saved and synchronized!")
                            st.rerun()

    with col_u2:
        with st.container(border=True):
            st.markdown("##### 👥 Active Enterprise Users & Credentials Master")
            users_table = load_sheet_df("app_users")
            if users_table.empty or "Username" not in users_table.columns:
                st.info("No auxiliary user accounts found in 'app_users' ledger.")
            else:
                disp_cols = [c for c in ["Username", "Password", "Full_Name", "Role", "Contact_No", "Status", "Allowed_Tabs"] if c in users_table.columns]
                st.dataframe(users_table[disp_cols], use_container_width=True, hide_index=True)
                st.caption("🔒 All credentials shown in clear text for master administrative oversight. Re-submit Username on left to overwrite or reset.")
