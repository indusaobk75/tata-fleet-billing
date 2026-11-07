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
    page_title="TEST - Tata Steel Site ERP & Operations",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------- 1. DATABASE & CONFIG (TEST ISOLATION) -----------------
# Secrets mein 'test_spreadsheet_name' de sakte hain, default 'Tata_Steel_ERP_TEST_DB' rahega
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

@st.cache_data(ttl=10, show_spinner=False)
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


# ----------------- 2. MASTER TAB REGISTRY -----------------
# Saare available operational tabs jo Admin kisi bhi user ko assign kar sakta hai
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


# ----------------- 3. AUTHENTICATION & SESSION ENGINE -----------------
def init_session():
    if "user_authenticated" not in st.session_state:
        st.session_state.user_authenticated = False
    if "current_user" not in st.session_state:
        st.session_state.current_user = None
    if "current_role" not in st.session_state:
        st.session_state.current_role = None
    if "allowed_tabs" not in st.session_state:
        st.session_state.allowed_tabs = []

init_session()

def login_screen():
    c1, c2, c3 = st.columns([1, 2, 1])
    with c2:
        st.markdown("### 🔐 Fleet & Site ERP Portal (STAGING TEST)")
        st.caption("Vendor: M/S INDU SAO | Coal Projects, West Bokaro Division")
        st.info("Selective Role-Based Login: Enter your registered credentials to access your permitted workspace.")

        with st.form("erp_login_form"):
            username = st.text_input("Username / Employee ID").strip()
            password = st.text_input("Password", type="password").strip()
            submitted = st.form_submit_button("Sign In to Portal", use_container_width=True)

            if submitted:
                if not username or not password:
                    st.error("Please enter both username and password.")
                    return

                users_df = load_sheet_df("app_users")
                
                # Default Superadmin fallback agar sheet abhi empty ho
                if users_df.empty or "Username" not in users_df.columns:
                    master_pass = st.secrets.get("app_password", "Admin@2026")
                    if username.lower() == "admin" and password == master_pass:
                        st.session_state.user_authenticated = True
                        st.session_state.current_user = "admin"
                        st.session_state.current_role = "ADMIN"
                        st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                        st.success("Master Admin initialized.")
                        st.rerun()
                    else:
                        st.error("Invalid credentials.")
                        return

                # Normal User Database Verification
                users_df["_u_clean"] = users_df["Username"].astype(str).str.strip().str.lower()
                user_match = users_df[users_df["_u_clean"] == username.lower()]

                if not user_match.empty:
                    u_row = user_match.iloc[0]
                    u_pass = str(u_row.get("Password", "")).strip()
                    u_status = str(u_row.get("Status", "ACTIVE")).strip().upper()
                    u_role = str(u_row.get("Role", "USER")).strip().upper()

                    if u_status != "ACTIVE":
                        st.error("Your user account has been disabled. Please contact the administrator.")
                        return

                    if password == u_pass:
                        st.session_state.user_authenticated = True
                        st.session_state.current_user = str(u_row.get("Username"))
                        st.session_state.current_role = u_role

                        if u_role == "ADMIN":
                            st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                        else:
                            raw_tabs = str(u_row.get("Allowed_Tabs", ""))
                            permitted = [t.strip() for t in raw_tabs.split(",") if t.strip() in TAB_REGISTRY]
                            st.session_state.allowed_tabs = permitted

                        st.success("Login confirmed! Loading permitted workspace...")
                        st.rerun()
                    else:
                        st.error("Invalid username or password entered.")
                else:
                    st.error("User not found in authorized database.")

if not st.session_state.user_authenticated:
    login_screen()
    st.stop()


# ----------------- 4. SIDEBAR USER PROFILE & NAVIGATION -----------------
with st.sidebar:
    st.markdown("### 👤 User Workspace")
    st.write(f"Logged in as: **{st.session_state.current_user}**")
    st.caption(f"Role: `{st.session_state.current_role}` | DB: `{SHEET_NAME}`")

    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.user_authenticated = False
        st.session_state.current_user = None
        st.session_state.current_role = None
        st.session_state.allowed_tabs = []
        st.rerun()

    st.markdown("---")

    # Module Navigation Selector
    # User ke paas kaunse modules ke tabs maujood hain unhi ko dikhayenge
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
        st.warning("No tabs have been allocated to your account by the Admin.")
        st.stop()

    selected_module = st.radio("Select Operational Hub:", module_options)


# ----------------- 5. MODULE HUBS ROUTING -----------------

# --- HUB 1: TATA STEEL BILLING HUB ---
if selected_module == "📑 Tata Steel Billing Hub":
    st.title("📑 Tata Steel Billing & Contract Hub")
    st.caption("Commercial Fleet Logistics & Contract Running Billing | Vendor: M/S INDU SAO (ID47)")

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
    st.title("⛽ Site Fuel & Diesel Control System")
    st.caption("Direct Pump & Tanker Diesel Inflow-Outflow, Slips & Anti-Theft Ledger (Independent from Logsheets)")

    fuel_tabs = [t for t in st.session_state.allowed_tabs if TAB_REGISTRY.get(t) == "Fuel"]
    active_f_tabs = st.tabs(fuel_tabs)

    for i, tab_title in enumerate(fuel_tabs):
        with active_f_tabs[i]:
            st.subheader(tab_title)
            if tab_title == "Daily Diesel Issue Slip":
                st.info("Form to record date, vehicle, driver, last/current reading, fuel filled, rate, slip number and receipt photo.")
            elif tab_title == "Fleet Fuel Benchmark Master":
                st.info("Tank capacities, fuel metrics (L/Hr or km/L), and baseline targets.")
            elif tab_title == "Theft & Pilferage Audit":
                st.info("Automated variance analysis, expected fuel check, and driver integrity alerts.")


# --- HUB 3: WORKER ATTENDANCE (INDEPENDENT) ---
elif selected_module == "👷 Worker Attendance":
    st.title("👷 Driver & Site Worker Attendance")
    st.caption("Daily Shift Attendance, Muster Roll, and Operator Tracking (Independent from Vehicle Billing)")

    att_tabs = [t for t in st.session_state.allowed_tabs if TAB_REGISTRY.get(t) == "Attendance"]
    active_a_tabs = st.tabs(att_tabs)

    for i, tab_title in enumerate(att_tabs):
        with active_a_tabs[i]:
            st.subheader(tab_title)
            if tab_title == "Worker Daily Muster Roll":
                st.info("Daily marking of Present (P), Absent (A), Half-Day (HD), Weekly Off (WO), and Holiday (PH).")
            elif tab_title == "Driver & Operator Pool":
                st.info("Register drivers, helpers, operators with mobile numbers, Aadhaar, and designations.")
            elif tab_title == "Monthly Attendance Summary":
                st.info("Monthly muster roll export and wage calculation base.")


# --- HUB 4: ADMIN COMMAND & PERMISSION MANAGER (ADMIN ONLY) ---
elif selected_module == "⚙️ Admin & User Permissions" and st.session_state.current_role == "ADMIN":
    st.title("⚙️ User Accounts & Tab Permission Manager")
    st.caption("Create employee logins and selectively control which operational tabs each user is allowed to access.")

    col_u1, col_u2 = st.columns([1.2, 1.8])

    with col_u1:
        st.markdown("#### ➕ Add / Update User")
        with st.form("create_user_form"):
            new_username = st.text_input("Username (Unique ID)", placeholder="e.g., supervisor_wb").strip()
            new_password = st.text_input("Password", placeholder="Set password").strip()
            new_name = st.text_input("Full Name", placeholder="e.g., Ramesh Kumar").strip()
            new_role = st.selectbox("Role", ["USER", "ADMIN"])
            new_status = st.selectbox("Status", ["ACTIVE", "INACTIVE"])

            st.markdown("**Select Permitted Tabs for this User:**")
            selected_permissions = []
            
            # Group checkboxes by Hub for clean organization
            st.caption("Billing Hub Tabs:")
            for t_name, m_type in TAB_REGISTRY.items():
                if m_type == "Billing":
                    if st.checkbox(t_name, key=f"perm_{t_name}", value=(new_role == "ADMIN")):
                        selected_permissions.append(t_name)

            st.caption("Fuel Control Tabs:")
            for t_name, m_type in TAB_REGISTRY.items():
                if m_type == "Fuel":
                    if st.checkbox(t_name, key=f"perm_{t_name}", value=(new_role == "ADMIN")):
                        selected_permissions.append(t_name)

            st.caption("Worker Attendance Tabs:")
            for t_name, m_type in TAB_REGISTRY.items():
                if m_type == "Attendance":
                    if st.checkbox(t_name, key=f"perm_{t_name}", value=(new_role == "ADMIN")):
                        selected_permissions.append(t_name)

            submit_user = st.form_submit_button("Save User Account & Permissions", use_container_width=True)

            if submit_user:
                if not new_username or not new_password:
                    st.error("Username and password are required.")
                else:
                    users_df = load_sheet_df("app_users")
                    req_u_cols = ["Username", "Password", "Full_Name", "Role", "Allowed_Tabs", "Status"]
                    if users_df.empty or "Username" not in users_df.columns:
                        users_df = pd.DataFrame(columns=req_u_cols)

                    # Remove existing user if updating
                    users_df["_u_chk"] = users_df["Username"].astype(str).str.strip().str.lower()
                    users_df = users_df[users_df["_u_chk"] != new_username.lower()].drop(columns=["_u_chk"])

                    new_row = {
                        "Username": new_username,
                        "Password": new_password,
                        "Full_Name": new_name,
                        "Role": new_role,
                        "Allowed_Tabs": ",".join(selected_permissions) if new_role != "ADMIN" else "ALL",
                        "Status": new_status
                    }
                    updated_users = pd.concat([users_df, pd.DataFrame([new_row])], ignore_index=True)
                    if save_sheet_df("app_users", updated_users[req_u_cols]):
                        st.success(f"User account '{new_username}' saved successfully with customized permissions!")
                        st.rerun()

    with col_u2:
        st.markdown("#### 👥 Registered System Users")
        users_table = load_sheet_df("app_users")
        if users_table.empty or "Username" not in users_table.columns:
            st.info("No users registered in 'app_users' sheet yet.")
        else:
            disp_cols = [c for c in ["Username", "Full_Name", "Role", "Status", "Allowed_Tabs"] if c in users_table.columns]
            st.dataframe(users_table[disp_cols], use_container_width=True)

            st.caption("To change permissions or deactivate an employee, re-enter their username in the left form and re-save.")
