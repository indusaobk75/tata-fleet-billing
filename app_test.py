import io
import json
import time
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

# ----------------- 0. BULLETPROOF CREDENTIALS CONFIG -----------------
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "Admin@2026"
DEFAULT_ADMIN_NAME = "Indu Sao (Owner)"

# ----------------- 1. DATABASE & CONFIG (TEST ISOLATION) -----------------
SHEET_NAME = st.secrets.get("test_spreadsheet_name", st.secrets.get("spreadsheet_name", "Tata_Steel_ERP_TEST_DB"))

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


# ----------------- 2. MASTER REGISTRY & CSS STYLING -----------------
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

# Custom Professional UI Styling
st.markdown("""
<style>
    /* Global Clean Font & Header Adjustments */
    .block-container { padding-top: 1.8rem; padding-bottom: 2rem; }
    
    /* Enterprise Sidebar Card */
    .profile-card {
        background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 16px;
        color: #f8fafc;
        margin-bottom: 1rem;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .profile-avatar {
        width: 44px;
        height: 44px;
        border-radius: 50%;
        background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 700;
        font-size: 1.1rem;
        color: white;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    .status-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 9999px;
        font-size: 0.72rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .status-admin { background-color: #064e3b; color: #34d399; border: 1px solid #059669; }
    .status-user { background-color: #1e3a8a; color: #93c5fd; border: 1px solid #2563eb; }

    /* Nav Item Customization */
    div[data-testid="stSidebar"] div[role="radiogroup"] > label {
        background-color: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 8px 12px;
        margin-bottom: 6px;
        transition: all 0.2s ease-in-out;
    }
    div[data-testid="stSidebar"] div[role="radiogroup"] > label:hover {
        background-color: #f1f5f9;
        border-color: #cbd5e1;
    }
</style>
""", unsafe_allow_html=True)


# ----------------- 3. BULLETPROOF AUTHENTICATION -----------------
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

init_session()

def login_screen():
    c1, c2, c3 = st.columns([1, 1.4, 1])
    with c2:
        st.markdown(
            """
            <div style="text-align: center; margin-bottom: 24px;">
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
            st.caption("Sign in with your assigned operational credentials")
            
            username = st.text_input("Username / Operator ID", placeholder="e.g. admin or supervisor").strip()
            password = st.text_input("Security Passkey", type="password", placeholder="••••••••").strip()
            submitted = st.button("Sign In to Workspace", use_container_width=True, type="primary")

            if submitted:
                if not username or not password:
                    st.warning("Please fill in both Username and Passkey.")
                    return

                u_lower = username.lower()
                configured_master_pass = st.secrets.get("app_password", DEFAULT_ADMIN_PASS)

                # PRIORITY 1: BULLETPROOF ADMIN AUTHENTICATION
                # Never depends on Google Sheet status, network latency, or accidental sheet wipes
                if u_lower == DEFAULT_ADMIN_USER.lower() and password == configured_master_pass:
                    st.session_state.user_authenticated = True
                    st.session_state.current_user = DEFAULT_ADMIN_USER
                    st.session_state.current_name = DEFAULT_ADMIN_NAME
                    st.session_state.current_role = "ADMIN"
                    st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                    st.rerun()

                # PRIORITY 2: GOOGLE SHEETS USER VERIFICATION
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

                        if u_role == "ADMIN":
                            st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                        else:
                            raw_tabs = str(u_row.get("Allowed_Tabs", ""))
                            permitted = [t.strip() for t in raw_tabs.split(",") if t.strip() in TAB_REGISTRY]
                            st.session_state.allowed_tabs = permitted

                        st.rerun()
                    else:
                        st.error("Incorrect password entered.")
                else:
                    st.error("Authorized user profile not found.")

if not st.session_state.user_authenticated:
    login_screen()
    st.stop()


# ----------------- 4. PROFESSIONAL COLLAPSIBLE SIDEBAR -----------------
with st.sidebar:
    # 1. Branding Header
    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 14px;">
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

    # 2. Executive Aesthetic User Card
    initials = "".join([part[0] for part in st.session_state.current_name.split()[:2]]).upper() or "US"
    badge_class = "status-admin" if st.session_state.current_role == "ADMIN" else "status-user"

    st.markdown(
        f"""
        <div class="profile-card">
            <div style="display: flex; align-items: center; gap: 12px;">
                <div class="profile-avatar">{initials}</div>
                <div style="overflow: hidden;">
                    <div style="font-weight: 600; font-size: 0.92rem; white-space: nowrap; text-overflow: ellipsis; overflow: hidden;">
                        {st.session_state.current_name}
                    </div>
                    <div style="display: flex; align-items: center; gap: 6px; margin-top: 4px;">
                        <span class="status-badge {badge_class}">{st.session_state.current_role}</span>
                        <span style="color: #94a3b8; font-size: 0.75rem;">• @{st.session_state.current_user}</span>
                    </div>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # 3. Collapsible Environment Details Drawer
    with st.expander("🛠️ Session & Security Controls", expanded=False):
        st.caption(f"**Database:** `{SHEET_NAME}`")
        st.caption(f"**Role Privileges:** `{st.session_state.current_role}`")
        st.caption(f"**Permitted Tabs:** `{len(st.session_state.allowed_tabs)} active`")
        
        if st.button("🚪 Terminate Session", use_container_width=True, type="secondary"):
            st.session_state.user_authenticated = False
            st.session_state.current_user = None
            st.session_state.current_name = None
            st.session_state.current_role = None
            st.session_state.allowed_tabs = []
            st.rerun()

    st.markdown("<div style='margin-top: 10px; margin-bottom: 8px; font-size: 0.75rem; font-weight: 700; color: #64748b; letter-spacing: 0.06em; text-transform: uppercase;'>OPERATIONAL WORKSPACES</div>", unsafe_allow_html=True)

    # 4. Filter Available Workspace Modules
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

    selected_module = st.radio("Navigation", module_options, label_visibility="collapsed")


# ----------------- 5. OPERATIONAL HUBS VIEWPORT -----------------

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


# --- HUB 4: ADMIN COMMAND & RBAC MANAGER ---
elif selected_module == "⚙️ Admin & User Permissions" and st.session_state.current_role == "ADMIN":
    st.markdown("### ⚙️ Identity & Access Management (RBAC)")
    st.caption("Provision employee credentials and define modular access boundaries across site operations")

    col_u1, col_u2 = st.columns([1.2, 1.8], gap="large")

    with col_u1:
        with st.container(border=True):
            st.markdown("##### ➕ Provision / Update User")
            
            with st.form("create_user_form", clear_on_submit=False):
                new_username = st.text_input("Username (Unique ID)", placeholder="e.g. sup_ramesh").strip()
                new_password = st.text_input("Password", placeholder="••••••••").strip()
                new_name = st.text_input("Full Name", placeholder="e.g. Ramesh Kumar").strip()
                
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
                        req_u_cols = ["Username", "Password", "Full_Name", "Role", "Allowed_Tabs", "Status"]
                        if users_df.empty or "Username" not in users_df.columns:
                            users_df = pd.DataFrame(columns=req_u_cols)

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
                                "Status": "ACTIVE"
                            })

                        rows_to_append.append({
                            "Username": new_username,
                            "Password": new_password,
                            "Full_Name": new_name,
                            "Role": new_role,
                            "Allowed_Tabs": ",".join(selected_permissions) if new_role != "ADMIN" else "ALL",
                            "Status": new_status
                        })

                        updated_users = pd.concat([users_df, pd.DataFrame(rows_to_append)], ignore_index=True)
                        if save_sheet_df("app_users", updated_users[req_u_cols]):
                            st.success(f"User account '{new_username}' synced with central database!")
                            st.rerun()

    with col_u2:
        with st.container(border=True):
            st.markdown("##### 👥 Active Enterprise Users")
            users_table = load_sheet_df("app_users")
            if users_table.empty or "Username" not in users_table.columns:
                st.info("No auxiliary user accounts found in 'app_users' ledger.")
            else:
                disp_cols = [c for c in ["Username", "Full_Name", "Role", "Status", "Allowed_Tabs"] if c in users_table.columns]
                st.dataframe(users_table[disp_cols], use_container_width=True, hide_index=True)
                st.caption("Re-submit the same Username in the creation form to modify permissions or change account status.")
