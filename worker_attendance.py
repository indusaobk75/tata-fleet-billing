import io
import time
import re
import base64
import datetime
import calendar
import pandas as pd
import streamlit as st

from fuel_and_audit import log_attendance_activity

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

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ----------------- SCHEMAS & CONSTANTS -----------------
BASE_DIVISIONS = [
    "Fleet Operations",
    "Civil Construction",
    "Office Staff",
    "Cleaning & Housekeeping",
    "Workshop & Maintenance"
]

SKILL_CATEGORIES = ["Unskilled", "Semi-Skilled", "Skilled", "Highly Skilled"]
WEEK_DAYS_OPTIONS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "No Fixed Week-Off"]

DEFAULT_EPF_WAGE_CEILING = 25000.0
DEFAULT_EPF_RATE_PCT = 12.0
DEFAULT_ESIC_WAGE_CEILING = 21000.0
DEFAULT_ESIC_RATE_PCT = 0.75

STATUTORY_CONFIG_COLS = [
    "Config_Key", "Config_Value", "Effective_From", "Updated_By", "Remarks"
]

DEFAULT_STATUTORY_CONFIG = [
    {"Config_Key": "EPF_WAGE_CEILING", "Config_Value": 25000.0, "Effective_From": "2026-04-01", "Updated_By": "System", "Remarks": "EPF Monthly Wage Ceiling (INR)"},
    {"Config_Key": "EPF_RATE_PCT", "Config_Value": 12.0, "Effective_From": "2026-04-01", "Updated_By": "System", "Remarks": "Employee EPF Share (%)"},
    {"Config_Key": "ESIC_WAGE_CEILING", "Config_Value": 21000.0, "Effective_From": "2026-04-01", "Updated_By": "System", "Remarks": "ESIC Gross Eligibility Ceiling (INR)"},
    {"Config_Key": "ESIC_RATE_PCT", "Config_Value": 0.75, "Effective_From": "2026-04-01", "Updated_By": "System", "Remarks": "Employee ESIC Share (%)"}
]

WAGE_MASTER_COLS = [
    "Skill_Category", "Basic_Pay", "VDA_Allowance", "Total_Daily_Rate", "Effective_From", "Remarks"
]

DEFAULT_WAGE_RATES = [
    {"Skill_Category": "Unskilled", "Basic_Pay": 350.0, "VDA_Allowance": 215.0, "Total_Daily_Rate": 565.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"},
    {"Skill_Category": "Semi-Skilled", "Basic_Pay": 400.0, "VDA_Allowance": 250.0, "Total_Daily_Rate": 650.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"},
    {"Skill_Category": "Skilled", "Basic_Pay": 494.0, "VDA_Allowance": 300.0, "Total_Daily_Rate": 794.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"},
    {"Skill_Category": "Highly Skilled", "Basic_Pay": 565.0, "VDA_Allowance": 350.0, "Total_Daily_Rate": 915.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"}
]

PPE_POLICY_COLS = [
    "PPE_Item", "Validity_Days", "Recovery_Cost", "Exchange_Rule", "Remarks"
]

DEFAULT_PPE_POLICIES = [
    {"PPE_Item": "Safety Shoes", "Validity_Days": 180, "Recovery_Cost": 650.0, "Exchange_Rule": "6 Months Free Validity; Old Pair Return Required", "Remarks": "IS 15298 Steel Toe"},
    {"PPE_Item": "Safety Helmet", "Validity_Days": 365, "Recovery_Cost": 300.0, "Exchange_Rule": "12 Months Free Validity; Breakage Return Only", "Remarks": "IS 2925 Industrial"},
    {"PPE_Item": "Safety Goggles", "Validity_Days": 60, "Recovery_Cost": 100.0, "Exchange_Rule": "2 Months Free Validity", "Remarks": "Anti-Scratch Clear/Tinted"},
    {"PPE_Item": "Hand Gloves (Pair)", "Validity_Days": 30, "Recovery_Cost": 80.0, "Exchange_Rule": "1 Month Free Validity; 1-for-1 Exchange", "Remarks": "Cotton / Leather"},
    {"PPE_Item": "Reflective Safety Jacket", "Validity_Days": 180, "Recovery_Cost": 150.0, "Exchange_Rule": "6 Months Free Validity", "Remarks": "High-Visibility"},
    {"PPE_Item": "Dust Mask / Respirator", "Validity_Days": 15, "Recovery_Cost": 50.0, "Exchange_Rule": "15 Days Free Validity", "Remarks": "N95 / FFP2"},
    {"PPE_Item": "Ear Plugs / Ear Muff", "Validity_Days": 90, "Recovery_Cost": 120.0, "Exchange_Rule": "3 Months Free Validity", "Remarks": "High-Noise Zone"},
    {"PPE_Item": "Gumboots (Pair)", "Validity_Days": 180, "Recovery_Cost": 500.0, "Exchange_Rule": "6 Months Free Validity", "Remarks": "Monsoon / Slurry Duty"},
    {"PPE_Item": "Measuring Tape (5m)", "Validity_Days": 180, "Recovery_Cost": 150.0, "Exchange_Rule": "6 Months Validity; Return Old Tape", "Remarks": "Site Civil / Layout Tool"}
]

PPE_ISSUE_COLS = [
    "Issue_ID", "Issue_Date", "Worker_ID", "Worker_Name", "Division", "PPE_Item",
    "Quantity", "Issue_Mode", "Recovery_Amount", "Validity_Days", "Next_Eligible_Date",
    "Old_Item_Returned", "Issued_By", "Remarks"
]

WORKER_MASTER_COLS = [
    "Worker_ID", "Full_Name", "Father_Husband_Name", "Govt_ID_Ref", "Division", "Designation",
    "Wage_Type", "Skill_Category", "Base_Rate", "Week_Off_Day", "Contact_No",
    "Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details", "Status", "Created_Date", "Photo_Base64"
]

MUSTER_ROLL_COLS = [
    "Date", "Worker_ID", "Worker_Name", "Division", "Attendance", "OT_Hours",
    "Duty_Location", "Advance_Amount", "Payment_Mode", "Supervisor", "Remarks"
]

ADVANCE_LOG_COLS = [
    "Txn_ID", "Date", "Worker_ID", "Worker_Name", "Division", "Amount",
    "Payment_Mode", "Supervisor", "Remarks"
]

TATA_FORM_XVI_COLS = [
    "Wages_Period", "Worker_ID", "Worker_Name", "Father_Husband_Name",
    "Punch_Days_Count", "Punch_Dates_List", "Uploaded_At"
]

TATA_COMPLIANCE_COLS = [
    "Wages_Period", "Worker_ID", "Worker_Name", "Register_Sl_No",
    "Approved_Days", "Net_Amount_Paid", "Uploaded_At"
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

GENERAL_STAFF_ROLES = [
    "Site Accountant / Clerk", "Data Entry Operator", "Storekeeper / Timekeeper",
    "Site Engineer / Coordinator", "Office Boy / Peon", "Housekeeping / Cleaning Staff",
    "Sweeper / Sanitation Worker", "Security Guard / Watchman", "Other (Custom Entry)"
]

COMPANY_CURRENT_AC = "482025110000105"


# ----------------- VALIDATION, NORMALIZATION & ID HELPERS -----------------
def clean_excel_cell(val):
    """Safely converts Excel/CSV values to clean strings, stripping NaN/None and trailing '.0'."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in ["nan", "none", "null", "nat", "n/a", "-", "--"]:
        return ""
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s


def normalize_skill_category(raw_skill, role_str="", rate_val=0.0, wage_type="DAILY_WAGE"):
    """Normalizes values like 'UN SKILLED', 'SEMI SKILLED', 'HIGHLY SKILLED' to exact system categories."""
    if str(wage_type or "").strip().upper() == "MONTHLY_FIXED":
        return "Monthly Fixed"
    s = re.sub(r"[\s\-_]+", "", clean_excel_cell(raw_skill).upper())
    if s in ["UNSKILLED", "US", "HELPER"]:
        return "Unskilled"
    if s in ["SEMISKILLED", "SS"]:
        return "Semi-Skilled"
    if s in ["SKILLED", "SK"]:
        return "Skilled"
    if s in ["HIGHLYSKILLED", "HS", "HIGHSKILLED"]:
        return "Highly Skilled"
    if s in ["MONTHLYFIXED", "FIXED", "MONTHLY"]:
        return "Monthly Fixed"
    return infer_skill_category_from_role_or_rate(role_str, rate_val, wage_type)


def validate_and_clean_employee_fields(contact_raw, govt_id_raw, bank_acc_raw, ifsc_raw):
    """
    Auto-cleans Excel NaN/.0/spaces/dashes and validates:
    - Mobile: 10 digits (strips +91 / leading 0)
    - Govt ID (12-digit ID): 12 digits (strips spaces/dashes)
    - Bank Account: 9 to 18 digits (strips spaces/dashes)
    - IFSC Code: 11-char RBI standard (4 letters + '0' + 6 alphanumeric; auto-fixes 5th char 'O'->'0')
    Returns (cleaned_dict, error_list).
    """
    errors = []

    mob_raw = clean_excel_cell(contact_raw)
    mob_clean = re.sub(r"[\s\-\(\)\+]", "", mob_raw)
    if mob_clean.startswith("91") and len(mob_clean) == 12:
        mob_clean = mob_clean[2:]
    elif mob_clean.startswith("0") and len(mob_clean) == 11:
        mob_clean = mob_clean[1:]
    if mob_clean and not re.fullmatch(r"\d{10}", mob_clean):
        errors.append(f"Mobile '{mob_raw}' must be 10 digits.")

    gid_raw = clean_excel_cell(govt_id_raw)
    gid_clean = re.sub(r"[\s\-]", "", gid_raw)
    if gid_clean and not re.fullmatch(r"\d{12}", gid_clean):
        errors.append(f"Govt ID '{gid_raw}' must be 12 digits.")

    acc_raw = clean_excel_cell(bank_acc_raw)
    acc_clean = re.sub(r"[\s\-]", "", acc_raw)
    if acc_clean and not re.fullmatch(r"\d{9,18}", acc_clean):
        errors.append(f"Bank A/C '{acc_raw}' must be 9-18 digits.")

    ifsc_raw_c = clean_excel_cell(ifsc_raw)
    ifsc_clean = re.sub(r"\s+", "", ifsc_raw_c).upper()
    if len(ifsc_clean) == 11 and ifsc_clean[4] == "O":
        ifsc_clean = ifsc_clean[:4] + "0" + ifsc_clean[5:]
    if ifsc_clean and not re.fullmatch(r"^[A-Z]{4}0[A-Z0-9]{6}$", ifsc_clean):
        errors.append(f"IFSC '{ifsc_raw_c}' must be 11 chars (4 letters + 0 + 6 branch chars, e.g. BKID0004820).")

    return {
        "Contact_No": mob_clean,
        "Govt_ID_Ref": gid_clean,
        "Bank_Account_No": acc_clean,
        "Bank_IFSC_Code": ifsc_clean
    }, errors


def is_valid_bank_for_transfer(acc_str, ifsc_str):
    acc_c = re.sub(r"[\s\-]", "", clean_excel_cell(acc_str))
    ifsc_c = re.sub(r"\s+", "", clean_excel_cell(ifsc_str)).upper()
    return bool(re.fullmatch(r"\d{9,18}", acc_c) and re.fullmatch(r"^[A-Z]{4}0[A-Z0-9]{6}$", ifsc_c))


def normalize_division_name(raw_div):
    """Normalizes common department variations cleanly while supporting custom departments."""
    s = re.sub(r"\s+", " ", clean_excel_cell(raw_div))
    if not s:
        return "Civil Construction"
    sl = s.lower()
    if sl in ["fleet", "fleet operations", "transport", "machinery", "equipment"]:
        return "Fleet Operations"
    if sl in ["civil", "civil construction", "civil work", "civil work (4501)", "construction"]:
        return "Civil Construction"
    if sl in ["office", "office staff", "admin", "staff", "accounts"]:
        return "Office Staff"
    if sl in ["cleaning", "cleaning department", "cleaning operation", "cleaning & housekeeping", "housekeeping", "sanitation"]:
        return "Cleaning & Housekeeping"
    if sl in ["workshop", "workshop & maintenance", "maintenance", "garage"]:
        return "Workshop & Maintenance"
    return s


def get_available_divisions(w_df=None):
    """Returns all base divisions plus any custom divisions stored in worker_master."""
    divs = list(BASE_DIVISIONS)
    if w_df is not None and not w_df.empty and "Division" in w_df.columns:
        for d in w_df["Division"].astype(str).unique().tolist():
            dc = d.strip()
            if dc and dc.lower() != "nan" and dc not in divs:
                divs.append(dc)
    return divs


def parse_allowed_divisions_list(allowed_division_str):
    """Parses comma-separated allowed divisions into a clean list (empty list = All Divisions)."""
    raw = str(allowed_division_str or "").strip()
    if not raw or "all divisions" in raw.lower():
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def generate_next_worker_id(division, existing_df):
    """Generates permanent, division-neutral sequential IDs: IS-1001, IS-1002, IS-1003..."""
    if existing_df is None or existing_df.empty or "Worker_ID" not in existing_df.columns:
        return "IS-1001"
    existing_ids = existing_df["Worker_ID"].astype(str).tolist()
    matched_nums = []
    for wid in existing_ids:
        w_up = wid.strip().upper()
        if w_up.startswith("IS-"):
            try:
                num = int(w_up.split("-")[1])
                matched_nums.append(num)
            except (IndexError, ValueError):
                continue
    if not matched_nums:
        return "IS-1001"
    return f"IS-{max(matched_nums) + 1}"


def normalize_duty_type(val):
    s = str(val).strip()
    if s in ["Punch", "Tata Site (In-Plant)"]:
        return "Punch"
    return "Non-Punch"


def normalize_week_off(val):
    s = clean_excel_cell(val)
    if s in WEEK_DAYS_OPTIONS:
        return s
    return "Sunday"


def normalize_name_key(val):
    return re.sub(r"\s+", " ", clean_excel_cell(val).upper())


def count_working_days_for_worker(year_val, month_val, week_off_day):
    days_in_m = calendar.monthrange(year_val, month_val)[1]
    wo_norm = normalize_week_off(week_off_day)
    if wo_norm == "No Fixed Week-Off":
        return days_in_m
    wo_count = 0
    for d in range(1, days_in_m + 1):
        dt = datetime.date(year_val, month_val, d)
        if dt.strftime("%A") == wo_norm:
            wo_count += 1
    return max(1, days_in_m - wo_count)


def infer_skill_category_from_role_or_rate(role_str, rate_val, wage_type="DAILY_WAGE"):
    if str(wage_type).upper() == "MONTHLY_FIXED":
        return "Monthly Fixed"
    r = str(role_str).lower()
    if "supervisor" in r or "excavator" in r or "crane" in r or "dozer" in r or "engineer" in r:
        return "Highly Skilled"
    if "driver" in r or "operator" in r or "mason" in r or "welder" in r or "electrician" in r or "mechanic" in r or "carpenter" in r or "plumber" in r or "tile" in r or "fitter" in r or "accountant" in r:
        return "Skilled"
    if "rigger" in r or "painter" in r or "gypsum" in r or "bender" in r or "storekeeper" in r:
        return "Semi-Skilled"
    try:
        rv = float(rate_val or 0)
        if rv >= 850:
            return "Highly Skilled"
        elif rv >= 700:
            return "Skilled"
        elif rv >= 600:
            return "Semi-Skilled"
    except Exception:
        pass
    return "Unskilled"


def compress_photo_to_base64(uploaded_img):
    if not uploaded_img:
        return ""
    try:
        if PIL_AVAILABLE:
            im = Image.open(uploaded_img).convert("RGB")
            im.thumbnail((200, 240))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=78)
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        else:
            raw = uploaded_img.getvalue()
            if len(raw) < 42000:
                return base64.b64encode(raw).decode("utf-8")
            return ""
    except Exception:
        return ""


def resolve_worker_photo_b64(emp_row, load_media_fn=None):
    """Fetches photo from dedicated media_store (or falls back to legacy inline base64)."""
    wid = str(emp_row.get("Worker_ID", "")).strip()
    raw_val = str(emp_row.get("Photo_Base64", "")).strip()
    if raw_val == "SAVED" and load_media_fn:
        return load_media_fn(f"EMP_{wid}")
    if len(raw_val) > 50:
        return raw_val
    if load_media_fn and wid:
        return load_media_fn(f"EMP_{wid}")
    return ""


def get_active_statutory_config(load_fn):
    """Loads dynamic EPF/ESIC wage ceilings & percentages from Google Sheets (with safe defaults)."""
    cfg = {
        "EPF_WAGE_CEILING": DEFAULT_EPF_WAGE_CEILING,
        "EPF_RATE_PCT": DEFAULT_EPF_RATE_PCT,
        "ESIC_WAGE_CEILING": DEFAULT_ESIC_WAGE_CEILING,
        "ESIC_RATE_PCT": DEFAULT_ESIC_RATE_PCT,
        "Effective_From": "2026-04-01"
    }
    try:
        df = load_fn("statutory_config")
        if df is not None and not df.empty and "Config_Key" in df.columns and "Config_Value" in df.columns:
            for _, r in df.iterrows():
                k = str(r.get("Config_Key", "")).strip()
                if k in cfg and k != "Effective_From":
                    val = pd.to_numeric(r.get("Config_Value"), errors="coerce")
                    if pd.notna(val) and float(val) >= 0:
                        cfg[k] = float(val)
                eff = str(r.get("Effective_From", "")).strip()
                if eff:
                    cfg["Effective_From"] = eff
    except Exception:
        pass
    return cfg


def get_active_wage_rates_df(load_fn):
    df = load_fn("wage_rate_master")
    if df.empty or "Skill_Category" not in df.columns:
        return pd.DataFrame(DEFAULT_WAGE_RATES)
    existing_cats = df["Skill_Category"].astype(str).tolist()
    missing = [d for d in DEFAULT_WAGE_RATES if d["Skill_Category"] not in existing_cats]
    if missing:
        df = pd.concat([df, pd.DataFrame(missing)], ignore_index=True)
    for col in ["Basic_Pay", "VDA_Allowance", "Total_Daily_Rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def get_active_ppe_policies_df(load_fn):
    df = load_fn("ppe_policy_master")
    if df.empty or "PPE_Item" not in df.columns:
        return pd.DataFrame(DEFAULT_PPE_POLICIES)
    existing_items = df["PPE_Item"].astype(str).tolist()
    missing = [d for d in DEFAULT_PPE_POLICIES if d["PPE_Item"] not in existing_items]
    if missing:
        df = pd.concat([df, pd.DataFrame(missing)], ignore_index=True)
    df["Validity_Days"] = pd.to_numeric(df["Validity_Days"], errors="coerce").fillna(180).astype(int)
    df["Recovery_Cost"] = pd.to_numeric(df["Recovery_Cost"], errors="coerce").fillna(0.0)
    return df


def build_bulk_employee_template_excel(default_div="Fleet Operations"):
    sample_rows = [
        {
            "Full_Name": "RAMESH KUMAR MAHTO",
            "Father_Husband_Name": "BIRBAL MAHTO",
            "Govt_ID_Ref": "234567891234",
            "Division": "Fleet Operations",
            "Designation": "Excavator Operator (Heavy)",
            "Wage_Type": "DAILY_WAGE",
            "Skill_Category": "Highly Skilled",
            "Base_Rate": 915,
            "Week_Off_Day": "Sunday",
            "Contact_No": "9835100001",
            "Bank_Account_No": "482010110001234",
            "Bank_IFSC_Code": "BKID0004820",
            "Bank_Branch_Details": "BOI CHAINPUR",
            "Status": "ACTIVE"
        },
        {
            "Full_Name": "SONI DEVI",
            "Father_Husband_Name": "UGAN MAHTO",
            "Govt_ID_Ref": "345678912345",
            "Division": "Civil Construction",
            "Designation": "Civil Construction Helper",
            "Wage_Type": "DAILY_WAGE",
            "Skill_Category": "Unskilled",
            "Base_Rate": 565,
            "Week_Off_Day": "Sunday",
            "Contact_No": "9835100002",
            "Bank_Account_No": "334455667788",
            "Bank_IFSC_Code": "SBIN0001234",
            "Bank_Branch_Details": "SBI WEST BOKARO",
            "Status": "ACTIVE"
        },
        {
            "Full_Name": "MANOJ SINGH",
            "Father_Husband_Name": "RAMCHANDRA SINGH",
            "Govt_ID_Ref": "456789123456",
            "Division": "Office Staff",
            "Designation": "Site Accountant / Clerk",
            "Wage_Type": "MONTHLY_FIXED",
            "Skill_Category": "Monthly Fixed",
            "Base_Rate": 22000,
            "Week_Off_Day": "Sunday",
            "Contact_No": "9835100003",
            "Bank_Account_No": "482010110005678",
            "Bank_IFSC_Code": "BKID0004820",
            "Bank_Branch_Details": "BOI CHAINPUR",
            "Status": "ACTIVE"
        }
    ]
    df_tpl = pd.DataFrame(sample_rows)
    allowed_list = parse_allowed_divisions_list(default_div)
    if len(allowed_list) == 1:
        df_tpl["Division"] = allowed_list[0]
    buf = io.BytesIO()
    try:
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df_tpl.to_excel(writer, sheet_name="Employee_Enrolment", index=False)
        return buf.getvalue(), "xlsx"
    except Exception:
        return df_tpl.to_csv(index=False).encode("utf-8"), "csv"


def parse_and_validate_bulk_roster_df(b_df, existing_w_df, rate_lookup, is_div_restricted, allowed_div_list):
    """
    Validates a bulk Excel/CSV DataFrame and separates it into:
    - valid_rows: list of clean dicts ready for worker_master (assigned sequential IS-#### IDs)
    - error_rows: list of dicts with Excel_Row, Full_Name, Error_Reason + original columns
    """
    valid_rows = []
    error_rows = []

    for r_idx, b_row in b_df.iterrows():
        fname_chk = clean_excel_cell(b_row.get("Full_Name", ""))
        if not fname_chk:
            continue

        raw_div = b_row.get("Division", "")
        div_val = normalize_division_name(raw_div)
        if is_div_restricted and div_val.lower() not in {x.lower() for x in allowed_div_list}:
            div_val = allowed_div_list[0]

        cleaned_f, row_errs = validate_and_clean_employee_fields(
            b_row.get("Contact_No", ""),
            b_row.get("Govt_ID_Ref", ""),
            b_row.get("Bank_Account_No", ""),
            b_row.get("Bank_IFSC_Code", "")
        )
        if row_errs:
            err_dict = {
                "Excel_Row": r_idx + 2,
                "Full_Name": fname_chk,
                "Error_Reason": "; ".join(row_errs)
            }
            for col_k in b_df.columns:
                if col_k not in err_dict:
                    err_dict[col_k] = clean_excel_cell(b_row.get(col_k, ""))
            error_rows.append(err_dict)
            continue

        cur_merged = pd.concat([existing_w_df, pd.DataFrame(valid_rows)], ignore_index=True)
        new_gen_id = generate_next_worker_id("", cur_merged)
        raw_desig = clean_excel_cell(b_row.get("Designation", ""))
        clean_desig = raw_desig if raw_desig else "Unassigned"
        w_type_val = clean_excel_cell(b_row.get("Wage_Type", "DAILY_WAGE")).upper() or "DAILY_WAGE"
        if w_type_val not in ["DAILY_WAGE", "MONTHLY_FIXED"]:
            w_type_val = "DAILY_WAGE"

        raw_rate_num = pd.to_numeric(clean_excel_cell(b_row.get("Base_Rate", "")), errors="coerce")
        b_rate_val = float(raw_rate_num) if (pd.notna(raw_rate_num) and float(raw_rate_num) > 0) else 0.0

        clean_skill = normalize_skill_category(
            b_row.get("Skill_Category", ""),
            clean_desig,
            b_rate_val,
            w_type_val
        )
        if b_rate_val <= 0:
            b_rate_val = float(rate_lookup.get(clean_skill, 565.0)) if w_type_val == "DAILY_WAGE" else 18000.0

        f_husb = clean_excel_cell(b_row.get("Father_Husband_Name", ""))
        stat_val = clean_excel_cell(b_row.get("Status", "ACTIVE")).upper() or "ACTIVE"
        if stat_val not in ["ACTIVE", "INACTIVE"]:
            stat_val = "ACTIVE"

        valid_rows.append({
            "Worker_ID": new_gen_id, "Full_Name": fname_chk,
            "Father_Husband_Name": f_husb,
            "Govt_ID_Ref": cleaned_f["Govt_ID_Ref"], "Division": div_val,
            "Designation": clean_desig, "Wage_Type": w_type_val, "Skill_Category": clean_skill,
            "Base_Rate": b_rate_val, "Week_Off_Day": normalize_week_off(b_row.get("Week_Off_Day", "Sunday")),
            "Contact_No": cleaned_f["Contact_No"],
            "Bank_Account_No": cleaned_f["Bank_Account_No"],
            "Bank_IFSC_Code": cleaned_f["Bank_IFSC_Code"],
            "Bank_Branch_Details": clean_excel_cell(b_row.get("Bank_Branch_Details", "")).upper(),
            "Status": stat_val,
            "Created_Date": datetime.date.today().strftime("%Y-%m-%d"), "Photo_Base64": ""
        })

    return valid_rows, error_rows


# ----------------- EXACT TATA STEEL FORM XVI & FORM XVII PARSERS -----------------
def parse_tata_form_xvi_file(uploaded_file, target_ym):
    """
    Parses Tata Steel Form XVI (Biometric Muster Roll) from PDF, CSV, or XLSX.
    Extracts Worker_Name, Father_Husband_Name, Punch_Days_Count, and Punch_Dates_List.
    Automatically skips zero-attendance rows.
    """
    y_i = int(target_ym.split("-")[0])
    m_i = int(target_ym.split("-")[1])
    max_days = calendar.monthrange(y_i, m_i)[1]
    fname = uploaded_file.name.lower()

    parsed_records = []

    if fname.endswith(".csv") or fname.endswith(".xlsx"):
        df = pd.read_csv(uploaded_file, dtype=str) if fname.endswith(".csv") else pd.ExcelFile(uploaded_file).parse(0, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]

        name_col = None
        for cand in ["Worker_Name", "Name of Worker", "Employee Name", "Employee_Name", "Name_of_Workman", "Name", "NAME"]:
            if cand in df.columns:
                name_col = cand
                break
        if not name_col:
            for c in df.columns:
                if "name" in c.lower() and "father" not in c.lower() and "husband" not in c.lower():
                    name_col = c
                    break
        if not name_col:
            return pd.DataFrame()

        father_col = None
        for fc in ["Father_Husband_Name", "Father's/Husband's Name", "Father Name", "Father_Name"]:
            if fc in df.columns:
                father_col = fc
                break
        if not father_col:
            for c in df.columns:
                if "father" in c.lower() or "husband" in c.lower():
                    father_col = c
                    break

        day_col_map = {}
        for c in df.columns:
            c_clean = str(c).strip()
            if c_clean.isdigit() and 1 <= int(c_clean) <= max_days:
                day_col_map[int(c_clean)] = c
            elif re.match(r"^(day|d|0)[_\-\s]*0?(\d{1,2})$", c_clean, re.IGNORECASE):
                d_num = int(re.sub(r"\D", "", c_clean))
                if 1 <= d_num <= max_days:
                    day_col_map[d_num] = c
            elif c_clean.startswith(target_ym + "-") and len(c_clean) >= 10:
                try:
                    d_num = int(c_clean.split("-")[2][:2])
                    if 1 <= d_num <= max_days:
                        day_col_map[d_num] = c
                except Exception:
                    pass

        for _, row in df.iterrows():
            wname = normalize_name_key(row.get(name_col, ""))
            if not wname or "TOTAL" in wname:
                continue
            fname_val = normalize_name_key(row.get(father_col, "")) if father_col else ""

            punch_dates = []
            if day_col_map:
                for d_num in sorted(day_col_map.keys()):
                    val = clean_excel_cell(row.get(day_col_map[d_num], "")).upper()
                    if val in ["P", "PRESENT", "1", "1.0", "HD", "HALF", "P/2", "IN", "NH", "NHP"]:
                        punch_dates.append(f"{target_ym}-{d_num:02d}")
            elif "Punch_Dates_List" in df.columns:
                raw_list = clean_excel_cell(row.get("Punch_Dates_List", ""))
                punch_dates = [x.strip() for x in raw_list.split(",") if x.strip()]

            p_cnt = float(len(punch_dates))
            for tot_c in ["Total Attendance", "Punch_Days_Count", "Total_Days", "Present_Days", "Punch Days", "Days"]:
                if tot_c in df.columns:
                    v_num = pd.to_numeric(clean_excel_cell(row.get(tot_c, 0)), errors="coerce")
                    if pd.notna(v_num) and float(v_num) >= 0:
                        p_cnt = float(v_num)
                        break

            if p_cnt <= 0:
                continue

            parsed_records.append({
                "Worker_Name": wname,
                "Father_Husband_Name": fname_val,
                "Punch_Days_Count": p_cnt,
                "Punch_Dates_List": ",".join(punch_dates)
            })

    elif fname.endswith(".pdf") and PYPDF_AVAILABLE:
        reader = pypdf.PdfReader(uploaded_file)
        full_lines = []
        ignore_kw = [
            "FORM XVI", "MUSTER ROLL", "Name and address", "WAGES PERIOD",
            "West Bokaro", "INDU SAO", "Serial No", "Father", "Husband",
            "Name of", "Worker", "DATES", "Total", "Remark", "Attendan",
            "Nature and location", "Principal Employer", "Establishment", "For the Month"
        ]
        for page in reader.pages:
            txt = page.extract_text() or ""
            for raw_ln in txt.split("\n"):
                ln = raw_ln.strip()
                if not ln:
                    continue
                if any(k.lower() in ln.lower() for k in ignore_kw):
                    continue
                if re.match(r"^(01|1\s+2\s+3\s+4|16\s+15|17\s+18|19\s+20)", ln):
                    continue
                full_lines.append(ln)

        entries = []
        cur_buf = []
        for ln in full_lines:
            if re.match(r"^\d{1,3}\s+[A-Za-z]", ln) and cur_buf:
                entries.append(" ".join(cur_buf))
                cur_buf = [ln]
            else:
                cur_buf.append(ln)
        if cur_buf:
            entries.append(" ".join(cur_buf))

        for ent in entries:
            ent_clean = re.sub(r"\s+", " ", ent).strip()
            g_match = re.search(r"\b(Male|Female)\b", ent_clean, re.IGNORECASE)
            if not g_match:
                continue

            prefix_part = ent_clean[:g_match.start()].strip()
            suffix_part = ent_clean[g_match.end():].strip()

            prefix_no_sl = re.sub(r"^\d{1,3}\s+", "", prefix_part).strip()
            name_words = [w for w in prefix_no_sl.split() if not w.isdigit()]
            if len(name_words) < 2:
                continue

            if len(name_words) == 2:
                w_name = name_words[0].upper()
                f_name = name_words[1].upper()
            elif len(name_words) == 3:
                w_name = " ".join(name_words[:2]).upper()
                f_name = name_words[2].upper()
            else:
                w_name = " ".join(name_words[:-2]).upper()
                f_name = " ".join(name_words[-2:]).upper()

            toks = suffix_part.split()
            day_marks = []
            tot_att_val = None

            for idx_t, tk in enumerate(toks):
                tk_up = tk.upper().rstrip(",.")
                if tk_up in ["P", "NH", "NHP"]:
                    day_marks.append("P")
                elif tk_up in ["HD", "P/2"]:
                    day_marks.append("HD")
                elif tk_up in ["AB", "A", "0", "O", "40", "42", "20", "2", "Ο", "-"]:
                    day_marks.append("A")
                elif tk_up.isdigit() and 0 <= int(tk_up) <= 31 and len(day_marks) >= 15:
                    tot_att_val = float(tk_up)
                    break

            p_rem = re.search(r"P\s*:\s*-?\s*(\d+)", suffix_part, re.IGNORECASE)
            nh_rem = re.search(r"NHP?\s*:\s*-?\s*(\d+)", suffix_part, re.IGNORECASE)
            if p_rem:
                calc_from_rem = float(p_rem.group(1)) + (float(nh_rem.group(1)) if nh_rem else 0.0)
                if tot_att_val is None or tot_att_val == 0:
                    tot_att_val = calc_from_rem

            if tot_att_val is None:
                tot_att_val = float(len([m for m in day_marks if m == "P"]) + 0.5 * len([m for m in day_marks if m == "HD"]))

            if tot_att_val <= 0:
                continue

            punch_dates = []
            for d_idx, mk in enumerate(day_marks[:max_days], start=1):
                if mk in ["P", "HD"]:
                    punch_dates.append(f"{target_ym}-{d_idx:02d}")

            parsed_records.append({
                "Worker_Name": normalize_name_key(w_name),
                "Father_Husband_Name": normalize_name_key(f_name),
                "Punch_Days_Count": float(tot_att_val),
                "Punch_Dates_List": ",".join(punch_dates)
            })

    return pd.DataFrame(parsed_records)


def parse_tata_form_xvii_pdf(uploaded_pdf_file):
    """
    Parses Tata Steel Form XVII (Register of Wages) PDF accurately:
    Extracts Worker_Name, Register_Sl_No, Approved_Days, and Net_Amount_Paid.
    """
    if not PYPDF_AVAILABLE:
        return pd.DataFrame()
    reader = pypdf.PdfReader(uploaded_pdf_file)
    records = []
    header_keywords = [
        "FORM XVII", "REGISTER OF WAGES", "Name and address", "WAGES PERIOD",
        "Certified that the amount", "Signatory of the Certifying", "Total:-",
        "CIVIL WORK", "West Bokaro", "INDU SAO", "Serial No.", "Designation",
        "Basic Wages", "Dearness", "Overtime", "Other Cash", "Net Amount",
        "Signature", "Initial of", "Units of"
    ]
    all_lines = []
    for page in reader.pages:
        txt = page.extract_text()
        if not txt:
            continue
        page_lines = [l.strip() for l in txt.split("\n") if l.strip()]
        for l in page_lines:
            if any(k.lower() in l.lower() for k in header_keywords):
                continue
            if re.search(r"^1\s+2\s+3\s+4\s+5", l):
                continue
            all_lines.append(l)

    buffer = ""
    for line in all_lines:
        buffer = (buffer + " " + line).strip()
        rate_match = re.search(r"\b\d{3}\+\d{3}[\=\-\s]\d{3}\b", buffer)
        if rate_match:
            parts = buffer.split()
            if len(parts) >= 7 and parts[-1].isdigit():
                formula_idx = -1
                for p_i, p in enumerate(parts):
                    if re.search(r"\d{3}\+\d{3}", p):
                        formula_idx = p_i
                        break
                if formula_idx >= 3:
                    days_worked = float(parts[formula_idx - 1])
                    net_amount = float(parts[-1])

                    pre_tokens = parts[:formula_idx - 1]
                    if pre_tokens and pre_tokens[0].isdigit():
                        pre_tokens = pre_tokens[1:]

                    reg_sl_no = ""
                    if pre_tokens and pre_tokens[-1].isdigit():
                        reg_sl_no = pre_tokens[-1]
                        name_tokens = pre_tokens[:-1]
                    else:
                        name_tokens = [t for t in pre_tokens if not t.isdigit()]

                    clean_name = normalize_name_key(" ".join(name_tokens))
                    if clean_name and net_amount > 0:
                        records.append({
                            "Worker_Name": clean_name,
                            "Register_Sl_No": reg_sl_no,
                            "Approved_Days": days_worked,
                            "Net_Amount_Paid": net_amount
                        })
                    buffer = ""
    return pd.DataFrame(records)


# ----------------- PAYROLL & ADVANCE CARRY-FORWARD ENGINE -----------------
def calculate_statutory_pf_esic(punch_wages_only, stat_cfg=None):
    """
    Applies dynamic EPF & ESIC wage ceilings and rates ONLY on Punch (Form XVI/XVII) wages.
    Non-Punch wages are completely exempt from PF & ESIC deductions.
    """
    if punch_wages_only <= 0:
        return 0.0, 0.0
    cfg = stat_cfg or {}
    epf_cap = float(cfg.get("EPF_WAGE_CEILING", DEFAULT_EPF_WAGE_CEILING))
    epf_pct = float(cfg.get("EPF_RATE_PCT", DEFAULT_EPF_RATE_PCT)) / 100.0
    esic_cap = float(cfg.get("ESIC_WAGE_CEILING", DEFAULT_ESIC_WAGE_CEILING))
    esic_pct = float(cfg.get("ESIC_RATE_PCT", DEFAULT_ESIC_RATE_PCT)) / 100.0

    pf_eligible_wage = min(float(punch_wages_only), epf_cap) if epf_cap > 0 else float(punch_wages_only)
    pf_amt = round(pf_eligible_wage * epf_pct, 0)
    esic_amt = round(float(punch_wages_only) * esic_pct, 0) if (esic_cap <= 0 or float(punch_wages_only) <= esic_cap) else 0.0
    return pf_amt, esic_amt


def classify_advance_category(payment_mode, remarks):
    pm = str(payment_mode or "").strip().lower()
    rm = str(remarks or "").strip().lower()
    if "theft" in pm or "theft" in rm or "loss" in pm or "loss" in rm or "stolen" in rm or "damage" in rm:
        return "Theft/Loss"
    if "ppe" in pm or "ppe" in rm or "tool" in pm or "tool" in rm or "safety" in rm:
        return "PPE/Tools"
    if "fuel" in pm or "fuel" in rm or "diesel" in pm or "diesel" in rm or "petrol" in rm:
        return "Fuel"
    return "Cash/UPI"


def compute_monthly_payroll_and_advances(w_master, muster_df, adv_df, tata_df, sel_year, month_num, stat_cfg=None):
    target_ym = f"{sel_year}-{month_num:02d}"
    all_periods = set([target_ym])
    if not adv_df.empty and "Date" in adv_df.columns:
        for d in adv_df["Date"].astype(str):
            if len(d) >= 7 and d[:7] <= target_ym:
                all_periods.add(d[:7])
    if not muster_df.empty and "Date" in muster_df.columns:
        for d in muster_df["Date"].astype(str):
            if len(d) >= 7 and d[:7] <= target_ym:
                all_periods.add(d[:7])
    sorted_periods = sorted(list(all_periods))

    carry_fwd_map = {}
    results_for_target = {}

    for ym in sorted_periods:
        y_i = int(ym.split("-")[0])
        m_i = int(ym.split("-")[1])
        p_start = f"{y_i}-{m_i:02d}-01"
        p_end = f"{y_i}-{m_i:02d}-{calendar.monthrange(y_i, m_i)[1]:02d}"
        m_abbr = calendar.month_name[m_i][:3].lower()
        y_short = str(y_i)[-2:]

        def _p_match(v):
            s = str(v).strip().lower()
            return (ym in s) or (f"{m_abbr}-{y_i}" in s) or (f"{m_abbr}-{y_short}" in s)

        tata_p_df = pd.DataFrame(columns=TATA_COMPLIANCE_COLS)
        if not tata_df.empty and "Wages_Period" in tata_df.columns:
            tata_p_df = tata_df[tata_df["Wages_Period"].apply(_p_match)]

        for _, w in w_master.iterrows():
            wid = str(w["Worker_ID"])
            wname = str(w["Full_Name"])
            wfather = str(w.get("Father_Husband_Name", "")).strip()
            wdiv = str(w.get("Division", ""))
            wrole = str(w.get("Designation", ""))
            wskill = str(w.get("Skill_Category", ""))
            wwtype = str(w.get("Wage_Type", "DAILY_WAGE")).upper()
            wrate = float(pd.to_numeric(w.get("Base_Rate", 0.0), errors="coerce") or 0.0)
            w_wo = normalize_week_off(w.get("Week_Off_Day", "Sunday"))
            b_acc = str(w.get("Bank_Account_No", "")).strip()
            b_ifsc = str(w.get("Bank_IFSC_Code", "")).strip().upper()
            b_branch = str(w.get("Bank_Branch_Details", "")).strip().upper()

            opening_adv = carry_fwd_map.get(wid, 0.0)

            new_adv = 0.0
            adv_cat_totals = {"Cash/UPI": 0.0, "PPE/Tools": 0.0, "Fuel": 0.0, "Theft/Loss": 0.0}
            if not adv_df.empty and "Date" in adv_df.columns and "Worker_ID" in adv_df.columns:
                wa = adv_df[(adv_df["Worker_ID"].astype(str) == wid) &
                            (adv_df["Date"].astype(str) >= p_start) &
                            (adv_df["Date"].astype(str) <= p_end)]
                for _, a_rec in wa.iterrows():
                    amt_v = float(pd.to_numeric(a_rec.get("Amount", 0.0), errors="coerce") or 0.0)
                    if amt_v > 0:
                        new_adv += amt_v
                        cat_k = classify_advance_category(a_rec.get("Payment_Mode", ""), a_rec.get("Remarks", ""))
                        adv_cat_totals[cat_k] = adv_cat_totals.get(cat_k, 0.0) + amt_v

            total_adv_due = opening_adv + new_adv

            punch_days = 0.0
            non_punch_days = 0.0
            paid_leave_days = 0.0
            ot_hrs = 0.0
            if not muster_df.empty and "Date" in muster_df.columns and "Worker_ID" in muster_df.columns:
                wm = muster_df[(muster_df["Worker_ID"].astype(str) == wid) &
                               (muster_df["Date"].astype(str) >= p_start) &
                               (muster_df["Date"].astype(str) <= p_end)].copy()
                wm["Duty_Location"] = wm["Duty_Location"].apply(normalize_duty_type)

                wp = wm[wm["Duty_Location"] == "Punch"]
                punch_days = len(wp[wp["Attendance"] == "P"]) + (len(wp[wp["Attendance"] == "HD"]) * 0.5)

                wnp = wm[wm["Duty_Location"] == "Non-Punch"]
                non_punch_days = len(wnp[wnp["Attendance"] == "P"]) + (len(wnp[wnp["Attendance"] == "HD"]) * 0.5)

                paid_leave_days = float(len(wm[wm["Attendance"] == "L"]))
                ot_hrs = float(pd.to_numeric(wm["OT_Hours"], errors="coerce").fillna(0.0).sum())

            f17_days = 0.0
            f17_net = 0.0
            if not tata_p_df.empty:
                tm_by_id = tata_p_df[tata_p_df["Worker_ID"].astype(str).str.strip() == wid]
                if not tm_by_id.empty:
                    tm = tm_by_id
                else:
                    same_name_count = len(w_master[w_master["Full_Name"].apply(normalize_name_key) == normalize_name_key(wname)])
                    if same_name_count <= 1:
                        tm = tata_p_df[
                            (tata_p_df["Worker_ID"].astype(str).str.strip() == "") &
                            (tata_p_df["Worker_Name"].apply(normalize_name_key) == normalize_name_key(wname))
                        ]
                    else:
                        tm = pd.DataFrame()

                if not tm.empty:
                    f17_days = float(pd.to_numeric(tm["Approved_Days"], errors="coerce").fillna(0.0).sum())
                    f17_net = float(pd.to_numeric(tm["Net_Amount_Paid"], errors="coerce").fillna(0.0).sum())

            is_f17_higher = False

            if wwtype == "MONTHLY_FIXED":
                working_days_in_month = count_working_days_for_worker(y_i, m_i, w_wo)
                per_day_fixed = wrate / float(working_days_in_month)
                total_paid_days = min(float(working_days_in_month), punch_days + non_punch_days + paid_leave_days)
                gross_total = round(per_day_fixed * total_paid_days, 0)
                pf_ded = 0.0
                esic_ded = 0.0
                if f17_net > gross_total and f17_net > 0:
                    is_f17_higher = True
                adjustable_pool = max(0.0, gross_total - f17_net)
                adv_deducted = min(total_adv_due, adjustable_pool)
                closing_adv = max(0.0, total_adv_due - adv_deducted)
                non_punch_take_home = max(0.0, adjustable_pool - adv_deducted)
                total_net_payable = f17_net + non_punch_take_home
            else:
                total_paid_days = punch_days + non_punch_days
                punch_gross = round(punch_days * wrate, 0)
                ot_pay = (wrate / 8.0) * ot_hrs
                non_punch_gross = round((non_punch_days * wrate) + ot_pay, 0)
                gross_total = punch_gross + non_punch_gross

                # PF & ESIC calculated strictly on Punch wages ONLY (zero on Non-Punch)
                pf_ded, esic_ded = calculate_statutory_pf_esic(punch_gross, stat_cfg)
                agreed_net_before_adv = max(0.0, gross_total - pf_ded - esic_ded)

                if f17_net > 0:
                    if f17_net > agreed_net_before_adv and non_punch_days == 0:
                        is_f17_higher = True
                    balance_topup_pool = max(0.0, agreed_net_before_adv - f17_net)
                else:
                    balance_topup_pool = agreed_net_before_adv

                adv_deducted = min(total_adv_due, balance_topup_pool)
                closing_adv = max(0.0, total_adv_due - adv_deducted)
                non_punch_take_home = max(0.0, balance_topup_pool - adv_deducted)
                total_net_payable = f17_net + non_punch_take_home

            carry_fwd_map[wid] = closing_adv

            if ym == target_ym:
                results_for_target[wid] = {
                    "Worker_ID": wid, "Worker_Name": wname, "Father_Husband_Name": wfather,
                    "Division": wdiv, "Designation": wrole, "Skill_Category": wskill,
                    "Wage_Type": wwtype, "Base_Rate": wrate, "Week_Off_Day": w_wo,
                    "Status": str(w.get("Status", "ACTIVE")).upper(),
                    "Bank_Account_No": b_acc, "Bank_IFSC_Code": b_ifsc, "Bank_Branch_Details": b_branch,
                    "Punch_Days": punch_days, "Non_Punch_Days": non_punch_days,
                    "Paid_Leave_Days": paid_leave_days, "Total_Days": total_paid_days,
                    "OT_Hours": ot_hrs, "Form_XVII_Days": f17_days, "Form_XVII_Net": f17_net,
                    "Is_F17_Higher": is_f17_higher,
                    "Gross_Wages": gross_total, "PF_Deduction": pf_ded, "ESIC_Deduction": esic_ded,
                    "Opening_Advance": opening_adv, "Month_New_Advance": new_adv,
                    "Adv_Cash_UPI": adv_cat_totals["Cash/UPI"],
                    "Adv_PPE_Tools": adv_cat_totals["PPE/Tools"],
                    "Adv_Fuel": adv_cat_totals["Fuel"],
                    "Adv_Theft_Loss": adv_cat_totals["Theft/Loss"],
                    "Total_Advance_Due": total_adv_due, "Advance_Deducted": adv_deducted,
                    "Closing_Carry_Forward": closing_adv, "Non_Punch_Net_Pay": non_punch_take_home,
                    "Net_Payable": total_net_payable
                }
    return results_for_target


# ----------------- PDF GENERATORS (BANK LETTER, 8-UP ID CARDS, BRIEF 3-UP SALARY SLIPS) -----------------
def generate_bank_authority_letter_pdf(bank_batch_type, records_list, cheque_no, cheque_date, letterhead_fn, letter_subtitle="SUB: Transfer of payment from our account to below A/C"):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    letterhead_fn(pdf)

    pdf.set_y(56)
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
    pdf.cell(170, 5.5, letter_subtitle, align="C", ln=True)

    pdf.ln(4)
    total_amount = sum([float(r.get("AMOUNT", 0.0) or 0.0) for r in records_list])
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(170, 5, "Dear Sir,", ln=True)
    pdf.set_font("Helvetica", "", 9.5)
    body_txt = (
        f"We request you to transfer Rs. {total_amount:.0f}/- from our Current A/C No. "
        f"{COMPANY_CURRENT_AC} to following account as per the payment details given "
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

    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(170, 5, "THANKING YOU", ln=True)
    pdf.ln(8)

    y_sign = pdf.get_y()
    pdf.set_xy(20, y_sign)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.cell(65, 5, "M/s INDU SAO", align="C")

    pdf.set_xy(20, y_sign + 18)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(65, 5, "Proprietor", align="C")

    pdf.set_xy(125, y_sign + 18)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(65, 5, "Bank Sign & Stamp", align="C")

    pdf.set_fill_color(244, 180, 0)
    pdf.rect(0, 288, 210, 2.5, style="F")
    pdf.set_fill_color(56, 163, 241)
    pdf.rect(0, 290.5, 210, 6.5, style="F")

    return bytes(pdf.output())


def generate_atm_id_cards_pdf(workers_df, logo_fn, load_media_fn=None):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    card_w, card_h = 85.6, 54.0
    margin_x, margin_y = 14.0, 14.0
    gap_x, gap_y = 10.8, 16.0

    records = workers_df.to_dict(orient="records")
    for idx, emp in enumerate(records):
        slot = idx % 8
        if slot == 0:
            pdf.add_page()

        col, row = slot % 2, slot // 2
        x0 = margin_x + col * (card_w + gap_x)
        y0 = margin_y + row * (card_h + gap_y)

        pdf.set_draw_color(100, 116, 139)
        pdf.set_line_width(0.35)
        pdf.rect(x0, y0, card_w, card_h, style="D")

        pdf.set_fill_color(56, 163, 241)
        pdf.rect(x0, y0, card_w, 12.5, style="F")
        pdf.set_fill_color(244, 180, 0)
        pdf.rect(x0, y0 + 12.5, card_w, 1.3, style="F")

        card_logo = logo_fn()
        if card_logo:
            pdf.image(card_logo, x=x0 + 2.2, y=y0 + 1.8, w=12.8, h=8.8)

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "B", 10.5)
        pdf.set_xy(x0 + 16.5, y0 + 1.8)
        pdf.cell(66, 4.5, "M/s INDU SAO", ln=True)
        pdf.set_font("Helvetica", "B", 6.2)
        pdf.set_x(x0 + 16.5)
        pdf.cell(66, 3.5, "TATA STEEL WEST BOKARO | VENDOR CODE: ID47")

        wid = str(emp.get("Worker_ID", ""))
        pdf.set_fill_color(15, 23, 42)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Courier", "B", 8.5)
        pdf.set_xy(x0 + 57.5, y0 + 16.0)
        pdf.cell(25.0, 5.5, wid, align="C", fill=True)

        pdf.set_draw_color(148, 163, 184)
        pdf.set_fill_color(248, 250, 252)
        pdf.rect(x0 + 3.5, y0 + 16.0, 19.0, 23.0, style="DF")

        photo_b64 = resolve_worker_photo_b64(emp, load_media_fn)
        photo_drawn = False
        if photo_b64 and len(photo_b64) > 50:
            try:
                img_stream = io.BytesIO(base64.b64decode(photo_b64))
                pdf.image(img_stream, x=x0 + 3.7, y=y0 + 16.2, w=18.6, h=22.6)
                photo_drawn = True
            except Exception:
                photo_drawn = False

        if not photo_drawn:
            pdf.set_text_color(100, 116, 139)
            pdf.set_font("Helvetica", "", 6)
            pdf.set_xy(x0 + 3.5, y0 + 25.5)
            pdf.cell(19.0, 3.5, "PHOTO", align="C")

        wname = str(emp.get("Full_Name", "")).upper()[:24]
        wfather = str(emp.get("Father_Husband_Name", "")).strip().upper()[:22]
        wrole = str(emp.get("Designation", ""))[:26]
        wdiv = str(emp.get("Division", ""))
        wphone = str(emp.get("Contact_No", "")) or "N/A"
        wref = str(emp.get("Govt_ID_Ref", "")) or wid

        pdf.set_text_color(15, 23, 42)
        pdf.set_font("Helvetica", "B", 9.2)
        pdf.set_xy(x0 + 24.5, y0 + 21.8)
        pdf.cell(59, 4.2, wname)

        pdf.set_font("Helvetica", "B", 7.0)
        pdf.set_text_color(55, 48, 163)
        pdf.set_xy(x0 + 24.5, y0 + 26.2)
        pdf.cell(59, 3.6, wrole)

        pdf.set_text_color(51, 65, 85)
        pdf.set_font("Helvetica", "", 6.4)
        pdf.set_xy(x0 + 24.5, y0 + 30.0)
        if wfather:
            pdf.cell(59, 3.3, f"S/o W/o: {wfather}")
        else:
            pdf.cell(59, 3.3, f"Dept: {wdiv}")
        pdf.set_xy(x0 + 24.5, y0 + 33.5)
        pdf.cell(59, 3.3, f"ID: {wref} | Mob: {wphone}")

        pdf.set_fill_color(241, 245, 249)
        pdf.rect(x0, y0 + 44.5, card_w, 9.5, style="F")
        pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "B", 6.2)
        pdf.set_xy(x0 + 3.5, y0 + 46.2)
        pdf.cell(46, 3.5, "ISO 45001:2018 Certified")
        pdf.set_xy(x0 + 3.5, y0 + 49.5)
        pdf.set_font("Helvetica", "", 5.8)
        pdf.cell(46, 3.2, "Sondiha, Chainpur, Ramgarh (JH)")

        pdf.set_font("Helvetica", "B", 6.3)
        pdf.set_xy(x0 + 49.0, y0 + 49.0)
        pdf.cell(35, 3.5, "Authorised Signatory", align="C")

    return bytes(pdf.output())


def generate_compact_salary_slips_pdf(slip_records, wage_period_label, logo_fn, stat_cfg=None):
    """Generates brief, uncluttered 3-up A4 salary slips with category-wise advance breakup."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    cfg = stat_cfg or {}
    epf_pct_disp = f"{float(cfg.get('EPF_RATE_PCT', DEFAULT_EPF_RATE_PCT)):g}%"
    esic_pct_disp = f"{float(cfg.get('ESIC_RATE_PCT', DEFAULT_ESIC_RATE_PCT)):g}%"

    slip_w, slip_h = 186.0, 86.0
    margin_x, margin_y, gap_y = 12.0, 10.0, 9.0

    for idx, s in enumerate(slip_records):
        slot = idx % 3
        if slot == 0:
            pdf.add_page()

        x0 = margin_x
        y0 = margin_y + slot * (slip_h + gap_y)

        pdf.set_draw_color(30, 41, 59)
        pdf.set_line_width(0.4)
        pdf.rect(x0, y0, slip_w, slip_h, style="D")

        pdf.set_fill_color(56, 163, 241)
        pdf.rect(x0, y0, slip_w, 12.5, style="F")
        pdf.set_fill_color(244, 180, 0)
        pdf.rect(x0, y0 + 12.5, slip_w, 1.2, style="F")

        logo_s = logo_fn()
        if logo_s:
            pdf.image(logo_s, x=x0 + 2.5, y=y0 + 1.4, w=14.0, h=9.6)

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "B", 11.2)
        pdf.set_xy(x0 + 18.5, y0 + 1.8)
        pdf.cell(108, 4.8, "M/s INDU SAO")
        pdf.set_font("Helvetica", "B", 6.6)
        pdf.set_xy(x0 + 18.5, y0 + 6.8)
        pdf.cell(108, 3.8, "Sondiha, Chainpur, Ramgarh (JH) | GST: 20AUOPK2982K1ZC | Vendor: ID47")

        pdf.set_fill_color(15, 23, 42)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 7.8)
        pdf.set_xy(x0 + 132.0, y0 + 2.8)
        pdf.cell(51.0, 6.8, f"PAY SLIP: {wage_period_label}", align="C", fill=True)

        pdf.set_text_color(15, 23, 42)
        pdf.set_font("Helvetica", "B", 7.6)
        pdf.set_xy(x0 + 3.0, y0 + 15.2)
        f_txt = f" (S/o W/o {s['Father_Husband_Name']})" if str(s.get("Father_Husband_Name", "")).strip() else ""
        pdf.cell(108, 4.3, f"{s['Worker_Name']}{f_txt} [{s['Worker_ID']}]")
        pdf.cell(72, 4.3, f"{s['Designation']} [{s['Skill_Category']}]", align="R")

        pdf.set_font("Helvetica", "", 7.2)
        pdf.set_xy(x0 + 3.0, y0 + 19.6)
        b_disp = s["Bank_Account_No"] if s["Bank_Account_No"] else "Cash"
        i_disp = s["Bank_IFSC_Code"] if s["Bank_IFSC_Code"] else "N/A"
        pdf.cell(94, 4.0, f"Dept: {s['Division']}  |  Rate: Rs.{s['Base_Rate']:.0f}")
        pdf.cell(86, 4.0, f"A/C: {b_disp} ({i_disp})", align="R")

        t_y = y0 + 25.0
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(100, 116, 139)
        pdf.set_line_width(0.25)
        pdf.set_font("Helvetica", "B", 6.8)

        w_cols = [18, 20, 16, 26, 22, 22, 24, 38]
        h_cols = ["Punch", "Non-Punch", "OT Hrs", "Gross", f"PF ({epf_pct_disp})", f"ESIC ({esic_pct_disp})", "Adv. Ded.", "NET PAYABLE"]
        pdf.set_xy(x0, t_y)
        for wc, hc in zip(w_cols, h_cols):
            pdf.cell(wc, 6.2, hc, border=1, align="C", fill=True)

        pdf.set_xy(x0, t_y + 6.2)
        pdf.set_font("Helvetica", "", 7.4)
        pdf.cell(w_cols[0], 7.2, f"{s['Punch_Days']:.1f} d", border=1, align="C")
        pdf.cell(w_cols[1], 7.2, f"{s['Non_Punch_Days']:.1f} d", border=1, align="C")
        pdf.cell(w_cols[2], 7.2, f"{s['OT_Hours']:.1f} h", border=1, align="C")
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.cell(w_cols[3], 7.2, f"Rs.{s['Gross_Wages']:,.0f}", border=1, align="R")
        pdf.set_font("Helvetica", "", 7.4)
        pdf.cell(w_cols[4], 7.2, f"Rs.{s['PF_Deduction']:,.0f}", border=1, align="R")
        pdf.cell(w_cols[5], 7.2, f"Rs.{s['ESIC_Deduction']:,.0f}", border=1, align="R")
        pdf.cell(w_cols[6], 7.2, f"Rs.{s['Advance_Deducted']:,.0f}", border=1, align="R")
        pdf.set_fill_color(236, 253, 245)
        pdf.set_font("Helvetica", "B", 8.0)
        pdf.cell(w_cols[7], 7.2, f"Rs.{s['Net_Payable']:,.0f}/-", border=1, align="R", fill=True)

        # Brief category-wise advance breakup (including Theft/Loss)
        adv_parts = []
        if float(s.get("Opening_Advance", 0)) > 0:
            adv_parts.append(f"Prev. Bal: Rs.{s['Opening_Advance']:,.0f}")
        if float(s.get("Adv_Cash_UPI", 0)) > 0:
            adv_parts.append(f"Cash/UPI: Rs.{s['Adv_Cash_UPI']:,.0f}")
        if float(s.get("Adv_PPE_Tools", 0)) > 0:
            adv_parts.append(f"PPE/Tools: Rs.{s['Adv_PPE_Tools']:,.0f}")
        if float(s.get("Adv_Fuel", 0)) > 0:
            adv_parts.append(f"Fuel: Rs.{s['Adv_Fuel']:,.0f}")
        if float(s.get("Adv_Theft_Loss", 0)) > 0:
            adv_parts.append(f"Theft/Loss: Rs.{s['Adv_Theft_Loss']:,.0f}")
        if float(s.get("Closing_Carry_Forward", 0)) > 0:
            adv_parts.append(f"Bal. Next Month: Rs.{s['Closing_Carry_Forward']:,.0f}")

        if adv_parts:
            pdf.set_font("Helvetica", "B", 6.8)
            pdf.set_text_color(30, 41, 59)
            pdf.set_xy(x0 + 3.0, y0 + 40.5)
            pdf.cell(180, 4.0, "Adv. Breakup: " + "  |  ".join(adv_parts))

        pdf.set_text_color(15, 23, 42)
        pdf.set_font("Helvetica", "B", 7.6)
        pdf.set_xy(x0 + 8.0, y0 + 73.5)
        pdf.cell(55, 4.5, "Employee Sign / Thumb", align="C")

        pdf.set_xy(x0 + 118.0, y0 + 67.5)
        pdf.cell(60, 4.2, "For M/s INDU SAO", align="C")
        pdf.set_xy(x0 + 118.0, y0 + 76.0)
        pdf.cell(60, 4.2, "Authorised Signatory", align="C")

        if slot < 2:
            cut_y = y0 + slip_h + (gap_y / 2.0)
            pdf.set_draw_color(148, 163, 184)
            pdf.set_line_width(0.2)
            for cx in range(int(x0), int(x0 + slip_w), 4):
                pdf.line(cx, cut_y, cx + 2, cut_y)

    return bytes(pdf.output())


# ----------------- MULTI-DIVISION FILTER HELPER -----------------
def filter_workers_by_allowed_division(df, allowed_division):
    if df is None or df.empty or "Division" not in df.columns:
        return df
    allowed_list = parse_allowed_divisions_list(allowed_division)
    if not allowed_list:
        return df
    allowed_lower = {x.lower() for x in allowed_list}
    return df[df["Division"].astype(str).str.strip().str.lower().isin(allowed_lower)].copy()


# ----------------- MAIN RENDER ENTRYPOINT FOR ATTENDANCE MODULE -----------------
def render_attendance_module(
    current_view, load_fn, save_fn, current_user, user_display_name, current_role,
    letterhead_fn, logo_fn, allowed_division="All Divisions",
    load_media_fn=None, save_media_fn=None
):
    is_admin = (str(current_role).upper() == "ADMIN")
    allowed_div_list = [] if is_admin else parse_allowed_divisions_list(allowed_division)
    is_div_restricted = (len(allowed_div_list) > 0)

    # 1. EMPLOYEE ENROLMENT
    if current_view == "➕ Employee Enrolment":
        st.markdown("#### Employee Enrolment")
        st.caption("Permanent IS-1001 Employee Codes, Multi-Department Support, and Auto-Validated Mobile / Govt ID / Bank / IFSC")

        wage_rates_df = get_active_wage_rates_df(load_fn)
        rate_lookup = dict(zip(wage_rates_df["Skill_Category"], wage_rates_df["Total_Daily_Rate"]))
        entry_mode = st.radio("Enrolment Method", ["Single Employee Form", "Bulk Excel/CSV Upload"], horizontal=True)

        if entry_mode == "Single Employee Form":
            c_form, c_info = st.columns([1.35, 0.65], gap="large")
            with c_form:
                with st.container(border=True):
                    w_df_current = load_fn("worker_master")
                    next_wid = generate_next_worker_id("", w_df_current)
                    st.caption(f"Assigned Permanent Employee Code: **{next_wid}** (Remains permanent across department transfers)")

                    all_known_divs = get_available_divisions(w_df_current)
                    if is_div_restricted:
                        div_choices = allowed_div_list
                    else:
                        div_choices = all_known_divs + ["Other (Custom Department)"]

                    w_div_sel = st.selectbox("Department / Division", div_choices, disabled=(is_div_restricted and len(div_choices) == 1), key="enrol_div")
                    custom_div_in = ""
                    if w_div_sel == "Other (Custom Department)":
                        custom_div_in = st.text_input("Specify Custom Department / Division Name", placeholder="e.g. Security / Stores / Canteen", key="enrol_custom_div").strip()

                    c_np1, c_np2 = st.columns([1.6, 1.0])
                    with c_np1:
                        w_name = st.text_input("Full Official Name", key="enrol_name").strip()
                        w_father = st.text_input("Father's / Husband's Name", placeholder="Distinguishes duplicate names in Form XVI/XVII", key="enrol_father").strip()
                        w_ref = st.text_input("12-Digit Govt Identity Number (Optional)", placeholder="e.g. 234567891234", key="enrol_ref").strip()
                    with c_np2:
                        w_photo_file = st.file_uploader("Passport Photo (Optional)", type=["jpg", "jpeg", "png"], key="enrol_photo")
                        if w_photo_file:
                            st.image(w_photo_file, width=85)

                    if w_div_sel == "Fleet Operations":
                        role_choices = FLEET_ROLES
                    elif w_div_sel == "Civil Construction":
                        role_choices = CIVIL_ROLES
                    else:
                        role_choices = GENERAL_STAFF_ROLES + [r for r in CIVIL_ROLES if r != "Other (Custom Entry)"]

                    sel_role = st.selectbox("Designation / Role", role_choices, key="enrol_role")
                    custom_role = st.text_input("Specify Custom Role", key="enrol_custom_role").strip() if sel_role == "Other (Custom Entry)" else ""

                    c_w1, c_w2, c_w3 = st.columns([1.0, 1.1, 1.0])
                    with c_w1:
                        w_wage_type = st.selectbox("Wage Model", ["DAILY_WAGE", "MONTHLY_FIXED"], key="enrol_wage_type")
                    with c_w2:
                        if w_wage_type == "DAILY_WAGE":
                            w_skill_cat = st.selectbox("Skill Category", SKILL_CATEGORIES, key="enrol_skill_cat")
                        else:
                            w_skill_cat = "Monthly Fixed"
                            st.text_input("Skill Category", value="Monthly Fixed", disabled=True, key="enrol_skill_fixed")
                    with c_w3:
                        if w_wage_type == "DAILY_WAGE":
                            auto_rate_val = float(rate_lookup.get(w_skill_cat, 565.0))
                            w_rate = st.number_input("Agreed Daily Wage Rate (₹)", min_value=100.0, value=auto_rate_val, step=10.0, key=f"enrol_rate_{w_skill_cat}")
                        else:
                            w_rate = st.number_input("Agreed Monthly Salary (₹)", min_value=1000.0, value=18000.0, step=500.0, key="enrol_rate_monthly")

                    c_wo1, c_wo2 = st.columns(2)
                    with c_wo1:
                        w_week_off = st.selectbox("Fixed Weekly Off Day", WEEK_DAYS_OPTIONS, index=0, key="enrol_week_off")
                    with c_wo2:
                        w_contact = st.text_input("Contact Mobile Number (10 Digits)", placeholder="e.g. 9835100001", key="enrol_contact").strip()

                    st.markdown("###### Bank Transfer Details (Auto-Cleaned & Validated)")
                    b_acc = st.text_input("Bank Account Number (Spaces auto-removed)", placeholder="e.g. 482010110001234", key="enrol_b_acc").strip()
                    c_b1, c_b2 = st.columns(2)
                    with c_b1:
                        b_ifsc = st.text_input("IFSC Code (11 Characters)", placeholder="e.g. BKID0004869", key="enrol_b_ifsc").strip().upper()
                    with c_b2:
                        b_branch = st.text_input("Bank & Branch", placeholder="e.g. BOI SARUBERA", key="enrol_b_branch").strip().upper()

                    w_status = st.selectbox("Employment Status", ["ACTIVE", "INACTIVE"], key="enrol_status")

                    if st.button("Save Employee Record", type="primary", use_container_width=True):
                        final_div = normalize_division_name(custom_div_in) if w_div_sel == "Other (Custom Department)" else w_div_sel
                        final_role = custom_role if sel_role == "Other (Custom Entry)" and custom_role else sel_role

                        cleaned_fields, val_errors = validate_and_clean_employee_fields(w_contact, w_ref, b_acc, b_ifsc)

                        if not w_name:
                            st.error("Employee Name is mandatory.")
                        elif w_div_sel == "Other (Custom Department)" and not custom_div_in:
                            st.error("Please type the custom Department / Division name.")
                        elif sel_role == "Other (Custom Entry)" and not custom_role:
                            st.error("Please specify custom role.")
                        elif val_errors:
                            for err_msg in val_errors:
                                st.error(f"❌ {err_msg}")
                        else:
                            w_df = load_fn("worker_master")
                            fresh_wid = generate_next_worker_id("", w_df)
                            b64_photo = compress_photo_to_base64(w_photo_file)
                            photo_flag = ""
                            if b64_photo:
                                if save_media_fn and save_media_fn(f"EMP_{fresh_wid}", b64_photo):
                                    photo_flag = "SAVED"
                                else:
                                    photo_flag = b64_photo

                            new_row = {
                                "Worker_ID": fresh_wid, "Full_Name": w_name,
                                "Father_Husband_Name": w_father, "Govt_ID_Ref": cleaned_fields["Govt_ID_Ref"],
                                "Division": final_div, "Designation": final_role, "Wage_Type": w_wage_type,
                                "Skill_Category": w_skill_cat, "Base_Rate": w_rate, "Week_Off_Day": w_week_off,
                                "Contact_No": cleaned_fields["Contact_No"], "Bank_Account_No": cleaned_fields["Bank_Account_No"],
                                "Bank_IFSC_Code": cleaned_fields["Bank_IFSC_Code"],
                                "Bank_Branch_Details": b_branch, "Status": w_status,
                                "Created_Date": datetime.date.today().strftime("%Y-%m-%d"),
                                "Photo_Base64": photo_flag
                            }
                            updated_w_df = pd.concat([w_df, pd.DataFrame([new_row])], ignore_index=True)
                            if save_fn("worker_master", updated_w_df[WORKER_MASTER_COLS]):
                                st.success(f"Employee {w_name} ({fresh_wid}) enrolled in {final_div}!")
                                st.rerun()

            with c_info:
                with st.container(border=True):
                    st.markdown("###### Validation & ID Rules")
                    st.caption("• **Employee Code:** Permanent `IS-1001` series (stays fixed even if department changes).")
                    st.caption("• **Mobile No:** Exact 10 digits (`+91` / spaces auto-cleaned).")
                    st.caption("• **Govt ID:** Exact 12 digits (spaces auto-removed).")
                    st.caption("• **Bank A/C:** Spaces/dashes auto-removed.")
                    st.caption("• **IFSC Code:** 11 chars (`4 letters + 0 + 6 branch chars`).")
                    st.markdown("---")
                    st.markdown("###### Active Daily Wage Rates")
                    for _, rr in wage_rates_df.iterrows():
                        st.caption(f"• **{rr['Skill_Category']}:** ₹{float(rr['Total_Daily_Rate']):,.0f}/day")

        else:
            with st.container(border=True):
                st.markdown("###### 1. Download Sample Bulk Enrolment Template")
                tpl_bytes, tpl_ext = build_bulk_employee_template_excel(allowed_division if is_div_restricted else "Fleet Operations")
                tpl_mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if tpl_ext == "xlsx" else "text/csv"
                st.download_button(
                    label=f"📥 Download Sample Employee Enrolment Template (.{tpl_ext})",
                    data=tpl_bytes,
                    file_name=f"M_s_INDU_SAO_Employee_Enrolment_Template.{tpl_ext}",
                    mime=tpl_mime,
                    type="secondary"
                )

                st.markdown("---")
                st.markdown("###### 2. Upload Completed Employee Roster")
                up_file = st.file_uploader("Upload CSV / XLSX File", type=["csv", "xlsx"])
                if up_file:
                    b_df = pd.read_csv(up_file, dtype=str) if up_file.name.lower().endswith(".csv") else pd.ExcelFile(up_file).parse(0, dtype=str)
                    b_df.columns = [str(c).strip() for c in b_df.columns]
                    st.dataframe(b_df.head(5), use_container_width=True, hide_index=True)

                    w_df_live = load_fn("worker_master")
                    valid_rows, error_rows = parse_and_validate_bulk_roster_df(
                        b_df, w_df_live, rate_lookup, is_div_restricted, allowed_div_list
                    )

                    if not error_rows:
                        if valid_rows:
                            st.success(f"✅ All **{len(valid_rows)} rows** passed validation and are ready to import!")
                            if st.button(f"🚀 Commit All {len(valid_rows)} Workers", type="primary", use_container_width=True):
                                fin_w_df = pd.concat([w_df_live, pd.DataFrame(valid_rows)], ignore_index=True)
                                if save_fn("worker_master", fin_w_df[WORKER_MASTER_COLS]):
                                    st.success(f"Successfully imported {len(valid_rows)} workers with permanent IS-series IDs!")
                                    st.rerun()
                        else:
                            st.warning("No valid employee rows found in the uploaded file.")
                    else:
                        st.warning(f"⚠️ Found **{len(valid_rows)} valid rows** and **{len(error_rows)} rows with validation issues**.")

                        act_b1, act_b2 = st.columns([1.3, 1.0])
                        with act_b1:
                            if valid_rows:
                                if st.button(f"✅ Import {len(valid_rows)} Valid Workers Now (Skip {len(error_rows)} Error Rows)", type="primary", use_container_width=True):
                                    fin_w_df = pd.concat([w_df_live, pd.DataFrame(valid_rows)], ignore_index=True)
                                    if save_fn("worker_master", fin_w_df[WORKER_MASTER_COLS]):
                                        st.success(f"Imported {len(valid_rows)} valid workers! ({len(error_rows)} error rows were skipped).")
                                        st.rerun()
                            else:
                                st.error("0 valid rows available to import. Please fix the errors below.")

                        with act_b2:
                            err_df_export = pd.DataFrame(error_rows)
                            st.download_button(
                                label=f"📥 Download {len(error_rows)} Skipped Error Rows (CSV)",
                                data=err_df_export.to_csv(index=False).encode("utf-8"),
                                file_name=f"Skipped_Error_Rows_{datetime.date.today().strftime('%Y%m%d')}.csv",
                                mime="text/csv",
                                type="secondary",
                                use_container_width=True
                            )

                        with st.expander(f"🔍 View {len(error_rows)} Skipped / Error Rows Details", expanded=True):
                            st.dataframe(pd.DataFrame(error_rows)[["Excel_Row", "Full_Name", "Error_Reason"]], use_container_width=True, hide_index=True)

    # 2. STATUTORY WAGE RATES & EDITABLE EPF/ESIC CEILING MASTER
    elif current_view == "⚖️ Statutory Wage Rates":
        st.markdown("#### Statutory Minimum Wage & PF/ESIC Ceiling Master")
        wage_rates_df = get_active_wage_rates_df(load_fn)
        stat_cfg = get_active_statutory_config(load_fn)
        w_master = filter_workers_by_allowed_division(load_fn("worker_master"), allowed_division if is_div_restricted else "All Divisions")

        c_kpi1, c_kpi2, c_kpi3, c_kpi4 = st.columns(4)
        kpi_cols = [c_kpi1, c_kpi2, c_kpi3, c_kpi4]
        for idx, cat in enumerate(SKILL_CATEGORIES):
            cnt = len(w_master[(w_master["Status"].astype(str).str.upper() == "ACTIVE") & (w_master["Skill_Category"].astype(str) == cat)]) if not w_master.empty and "Skill_Category" in w_master.columns else 0
            cur_r = float(wage_rates_df[wage_rates_df["Skill_Category"] == cat]["Total_Daily_Rate"].iloc[0])
            kpi_cols[idx].metric(f"{cat}", f"₹{cur_r:,.0f} / day", f"{cnt} Active Workers")

        with st.container(border=True):
            st.markdown("###### 📋 Category-Wise Daily Minimum Wage Rates")
            edited_rates_df = st.data_editor(
                wage_rates_df[WAGE_MASTER_COLS], use_container_width=True, hide_index=True, disabled=["Skill_Category"], key="wage_rate_editor"
            )
            b_col1, b_col2 = st.columns([1.4, 1.0])
            with b_col1:
                if st.button("🚀 Save Rates & Update All Daily Wage Workers", type="primary", use_container_width=True):
                    cleaned_rates, new_rate_map = [], {}
                    for _, r in edited_rates_df.iterrows():
                        cat = str(r["Skill_Category"])
                        b_pay = float(pd.to_numeric(r["Basic_Pay"], errors="coerce") or 0.0)
                        vda_pay = float(pd.to_numeric(r["VDA_Allowance"], errors="coerce") or 0.0)
                        tot_pay = float(pd.to_numeric(r["Total_Daily_Rate"], errors="coerce") or 0.0)
                        orig_row = wage_rates_df[wage_rates_df["Skill_Category"] == cat].iloc[0]
                        if (b_pay != float(orig_row["Basic_Pay"]) or vda_pay != float(orig_row["VDA_Allowance"])) and tot_pay == float(orig_row["Total_Daily_Rate"]):
                            tot_pay = b_pay + vda_pay
                        elif tot_pay <= 0:
                            tot_pay = b_pay + vda_pay
                        new_rate_map[cat] = tot_pay
                        cleaned_rates.append({
                            "Skill_Category": cat, "Basic_Pay": b_pay, "VDA_Allowance": vda_pay, "Total_Daily_Rate": tot_pay,
                            "Effective_From": str(r["Effective_From"]).strip() or datetime.date.today().strftime("%Y-%m-%d"),
                            "Remarks": str(r["Remarks"]).strip()
                        })
                    save_fn("wage_rate_master", pd.DataFrame(cleaned_rates)[WAGE_MASTER_COLS])

                    full_w_master = load_fn("worker_master")
                    updated_count = 0
                    if not full_w_master.empty and "Wage_Type" in full_w_master.columns:
                        for idx, wr in full_w_master.iterrows():
                            if str(wr.get("Wage_Type", "")).upper() == "DAILY_WAGE":
                                w_cat = normalize_skill_category(wr.get("Skill_Category", ""), wr.get("Designation", ""), wr.get("Base_Rate", 0), "DAILY_WAGE")
                                full_w_master.at[idx, "Skill_Category"] = w_cat
                                if w_cat in new_rate_map:
                                    full_w_master.at[idx, "Base_Rate"] = new_rate_map[w_cat]
                                    updated_count += 1
                        save_fn("worker_master", full_w_master[WORKER_MASTER_COLS])
                    st.success(f"Updated rates for **{updated_count} Daily Wage Workers**!")
                    st.rerun()

            with b_col2:
                if st.button("🔄 Auto-Assign Skill Categories & Defaults", type="secondary", use_container_width=True):
                    full_w_master = load_fn("worker_master")
                    if not full_w_master.empty:
                        assigned = 0
                        for idx, wr in full_w_master.iterrows():
                            norm_cat = normalize_skill_category(wr.get("Skill_Category", ""), wr.get("Designation", ""), wr.get("Base_Rate", 0), wr.get("Wage_Type", "DAILY_WAGE"))
                            if str(wr.get("Skill_Category", "")).strip() != norm_cat:
                                full_w_master.at[idx, "Skill_Category"] = norm_cat
                                assigned += 1
                            if str(wr.get("Week_Off_Day", "")).strip() not in WEEK_DAYS_OPTIONS:
                                full_w_master.at[idx, "Week_Off_Day"] = "Sunday"
                        if save_fn("worker_master", full_w_master[WORKER_MASTER_COLS]):
                            st.success(f"Normalized {assigned} skill categories & default Week-Off days!")
                            st.rerun()

        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("###### 🏛️ Statutory EPF & ESIC Wage Ceiling & Contribution Settings")
            st.caption("Update government EPF / ESIC wage ceilings or percentage rates anytime without modifying code. Applies strictly to Punch wages (Non-Punch wages have zero PF/ESIC).")

            sc1, sc2, sc3, sc4, sc5 = st.columns([1.15, 1.0, 1.15, 1.0, 1.0])
            with sc1:
                new_epf_cap = st.number_input("EPF Wage Ceiling (₹)", min_value=0.0, value=float(stat_cfg["EPF_WAGE_CEILING"]), step=1000.0, disabled=not is_admin)
            with sc2:
                new_epf_pct = st.number_input("EPF Employee Share (%)", min_value=0.0, max_value=25.0, value=float(stat_cfg["EPF_RATE_PCT"]), step=0.25, disabled=not is_admin)
            with sc3:
                new_esic_cap = st.number_input("ESIC Wage Ceiling (₹)", min_value=0.0, value=float(stat_cfg["ESIC_WAGE_CEILING"]), step=1000.0, disabled=not is_admin)
            with sc4:
                new_esic_pct = st.number_input("ESIC Employee Share (%)", min_value=0.0, max_value=10.0, value=float(stat_cfg["ESIC_RATE_PCT"]), step=0.05, disabled=not is_admin)
            with sc5:
                eff_dt_str = st.text_input("Effective From", value=str(stat_cfg.get("Effective_From", "2026-04-01")), disabled=not is_admin).strip()

            if is_admin:
                if st.button("💾 Save EPF & ESIC Ceiling Rules", type="primary"):
                    cfg_rows = [
                        {"Config_Key": "EPF_WAGE_CEILING", "Config_Value": float(new_epf_cap), "Effective_From": eff_dt_str, "Updated_By": current_user, "Remarks": "EPF Monthly Wage Ceiling (INR)"},
                        {"Config_Key": "EPF_RATE_PCT", "Config_Value": float(new_epf_pct), "Effective_From": eff_dt_str, "Updated_By": current_user, "Remarks": "Employee EPF Share (%)"},
                        {"Config_Key": "ESIC_WAGE_CEILING", "Config_Value": float(new_esic_cap), "Effective_From": eff_dt_str, "Updated_By": current_user, "Remarks": "ESIC Gross Eligibility Ceiling (INR)"},
                        {"Config_Key": "ESIC_RATE_PCT", "Config_Value": float(new_esic_pct), "Effective_From": eff_dt_str, "Updated_By": current_user, "Remarks": "Employee ESIC Share (%)"}
                    ]
                    if save_fn("statutory_config", pd.DataFrame(cfg_rows)[STATUTORY_CONFIG_COLS]):
                        st.success(f"Saved! EPF Cap: ₹{new_epf_cap:,.0f} ({new_epf_pct:g}%) | ESIC Cap: ₹{new_esic_cap:,.0f} ({new_esic_pct:g}%).")
                        st.rerun()

    # 3. EMPLOYEE MASTER REGISTER & OPEN PROFILE
    elif current_view == "📋 Employee Master Register":
        w_table_full = load_fn("worker_master")
        w_table = filter_workers_by_allowed_division(w_table_full, allowed_division if is_div_restricted else "All Divisions")
        muster_all = load_fn("muster_roll")
        adv_all = load_fn("worker_advances")
        tata_all = load_fn("tata_form_xvii")
        ppe_all = load_fn("ppe_issue_log")
        stat_cfg = get_active_statutory_config(load_fn)

        if st.session_state.selected_worker_card_id:
            target_wid = st.session_state.selected_worker_card_id
            top_nav1, top_nav2 = st.columns([1.2, 2.5])
            with top_nav1:
                if st.button("← Back to Employee Master", type="secondary", use_container_width=True):
                    st.session_state.selected_worker_card_id = None
                    st.rerun()

            w_match = w_table[w_table["Worker_ID"].astype(str) == target_wid]
            if w_match.empty:
                st.error("Employee record not found or access restricted.")
                return

            emp = w_match.iloc[0]
            emp_name = str(emp.get("Full_Name", ""))
            emp_father = str(emp.get("Father_Husband_Name", "")).strip()
            emp_ref = str(emp.get("Govt_ID_Ref", ""))
            emp_div = str(emp.get("Division", "Civil Construction"))
            emp_role = str(emp.get("Designation", ""))
            emp_wage_type = str(emp.get("Wage_Type", "DAILY_WAGE"))
            emp_skill = normalize_skill_category(emp.get("Skill_Category", ""), emp_role, emp.get("Base_Rate", 0), emp_wage_type)
            emp_rate = float(pd.to_numeric(emp.get("Base_Rate", 0.0), errors="coerce") or 0.0)
            emp_wo = normalize_week_off(emp.get("Week_Off_Day", "Sunday"))
            emp_acc = str(emp.get("Bank_Account_No", ""))
            emp_ifsc = str(emp.get("Bank_IFSC_Code", ""))
            emp_branch = str(emp.get("Bank_Branch_Details", ""))
            emp_contact = str(emp.get("Contact_No", ""))
            emp_status = str(emp.get("Status", "ACTIVE")).upper()
            emp_photo_b64 = resolve_worker_photo_b64(emp, load_media_fn)

            with top_nav2:
                if FPDF_AVAILABLE:
                    st.download_button(
                        label=f"🪪 Download ATM ID Card PDF ({target_wid})",
                        data=generate_atm_id_cards_pdf(w_match, logo_fn, load_media_fn),
                        file_name=f"ID_Card_{target_wid}.pdf", mime="application/pdf", type="primary"
                    )

            with st.container(border=True):
                p_c0, p_c1, p_c2 = st.columns([0.65, 1.85, 1.25])
                with p_c0:
                    if emp_photo_b64 and len(emp_photo_b64) > 50:
                        st.markdown(f'<img src="data:image/jpeg;base64,{emp_photo_b64}" style="width: 92px; height: 110px; object-fit: cover; border-radius: 8px; border: 1px solid #cbd5e1;" />', unsafe_allow_html=True)
                    else:
                        st.markdown('<div style="width: 92px; height: 110px; background: #f1f5f9; border: 1px dashed #94a3b8; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 0.72rem; color: #64748b;">No Photo</div>', unsafe_allow_html=True)
                    new_photo_up = st.file_uploader("Update Photo", type=["jpg", "jpeg", "png"], key=f"upd_photo_{target_wid}", label_visibility="collapsed")
                    if new_photo_up and st.button("Save Photo", key=f"save_ph_{target_wid}", type="primary", use_container_width=True):
                        b64_new = compress_photo_to_base64(new_photo_up)
                        if b64_new:
                            p_flag = b64_new
                            if save_media_fn and save_media_fn(f"EMP_{target_wid}", b64_new):
                                p_flag = "SAVED"
                            w_table_full.loc[w_table_full["Worker_ID"].astype(str) == target_wid, "Photo_Base64"] = p_flag
                            if save_fn("worker_master", w_table_full[WORKER_MASTER_COLS]):
                                st.success("Photo updated!")
                                st.rerun()

                with p_c1:
                    f_disp = f" • S/o W/o: **{emp_father}**" if emp_father else ""
                    st.markdown(f"### {emp_name} (`{target_wid}`)")
                    st.markdown(f"**Department:** {emp_div}{f_disp}  |  **Role:** {emp_role}")
                    st.markdown(f"**Category:** `{emp_skill}` ({emp_wage_type})  |  **Rate:** **₹{emp_rate:,.0f}**  |  **Off:** `{emp_wo}`  |  **Status:** `{emp_status}`")

                with p_c2:
                    st.markdown("###### 🏦 Bank Account Mapping")
                    st.markdown(f"**A/C No:** `{emp_acc or 'Not Linked'}`")
                    st.markdown(f"**IFSC:** `{emp_ifsc or 'N/A'}` ({emp_branch or 'Cash Disbursement'})")

            with st.expander("✏️ Edit Employee Details / Shift Department or Delete Record", expanded=False):
                all_edit_divs = allowed_div_list if is_div_restricted else get_available_divisions(w_table_full)
                if emp_div and emp_div not in all_edit_divs:
                    all_edit_divs.append(emp_div)

                with st.form(f"edit_full_emp_form_{target_wid}"):
                    ed_c1, ed_c2, ed_c3 = st.columns(3)
                    with ed_c1:
                        ed_name = st.text_input("Full Official Name", value=emp_name).strip()
                        ed_father = st.text_input("Father's / Husband's Name", value=emp_father).strip()
                        ed_ref = st.text_input("12-Digit Govt Identity Number", value=emp_ref).strip()
                        ed_contact = st.text_input("Contact Mobile No (10 Digits)", value=emp_contact).strip()
                    with ed_c2:
                        ed_div = st.selectbox("Department / Division (Transfer Anytime)", all_edit_divs, index=all_edit_divs.index(emp_div) if emp_div in all_edit_divs else 0, disabled=(is_div_restricted and len(all_edit_divs) == 1))
                        ed_role = st.text_input("Designation / Role", value=emp_role).strip()
                        ed_wo = st.selectbox("Fixed Weekly Off Day", WEEK_DAYS_OPTIONS, index=WEEK_DAYS_OPTIONS.index(emp_wo))
                    with ed_c3:
                        wt_opts = ["DAILY_WAGE", "MONTHLY_FIXED"]
                        ed_wtype = st.selectbox("Wage Model", wt_opts, index=wt_opts.index(emp_wage_type) if emp_wage_type in wt_opts else 0)
                        sk_opts = SKILL_CATEGORIES + ["Monthly Fixed"]
                        ed_skill = st.selectbox("Skill Category", sk_opts, index=sk_opts.index(emp_skill) if emp_skill in sk_opts else 0)
                        ed_rate = st.number_input("Agreed Base Rate (₹)", min_value=0.0, value=emp_rate, step=10.0)

                    eb_c1, eb_c2, eb_c3, eb_c4 = st.columns([1.2, 1.0, 1.2, 0.9])
                    with eb_c1:
                        ed_acc = st.text_input("Bank Account No", value=emp_acc).strip()
                    with eb_c2:
                        ed_ifsc = st.text_input("IFSC Code (11 Chars)", value=emp_ifsc).strip().upper()
                    with eb_c3:
                        ed_branch = st.text_input("Bank & Branch Details", value=emp_branch).strip().upper()
                    with eb_c4:
                        stat_opts = ["ACTIVE", "INACTIVE"]
                        ed_status = st.selectbox("Status", stat_opts, index=stat_opts.index(emp_status) if emp_status in stat_opts else 0)

                    if st.form_submit_button("💾 Save Updated Employee Profile", type="primary", use_container_width=True):
                        cleaned_ed, ed_errs = validate_and_clean_employee_fields(ed_contact, ed_ref, ed_acc, ed_ifsc)
                        if not ed_name:
                            st.error("Employee Name is required.")
                        elif ed_errs:
                            for em in ed_errs:
                                st.error(f"❌ {em}")
                        else:
                            mask_w = w_table_full["Worker_ID"].astype(str) == target_wid
                            w_table_full.loc[mask_w, ["Full_Name", "Father_Husband_Name", "Govt_ID_Ref", "Contact_No", "Division", "Designation", "Week_Off_Day", "Status", "Wage_Type", "Skill_Category", "Base_Rate", "Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details"]] = [
                                ed_name, ed_father, cleaned_ed["Govt_ID_Ref"], cleaned_ed["Contact_No"], ed_div, ed_role, ed_wo, ed_status, ed_wtype, ed_skill, ed_rate, cleaned_ed["Bank_Account_No"], cleaned_ed["Bank_IFSC_Code"], ed_branch
                            ]
                            if save_fn("worker_master", w_table_full[WORKER_MASTER_COLS]):
                                st.success("Profile updated!")
                                st.rerun()

                if is_admin:
                    st.markdown("---")
                    del_c1, del_c2, del_c3 = st.columns([1.4, 1.4, 1.0])
                    with del_c1:
                        confirm_del = st.checkbox(f"Confirm delete {emp_name} ({target_wid})", key=f"chk_del_{target_wid}")
                    with del_c2:
                        also_del_logs = st.checkbox("Also delete attendance & advance logs", value=False, key=f"chk_del_logs_{target_wid}")
                    with del_c3:
                        if st.button("🗑️ Delete Permanently", type="secondary", use_container_width=True, key=f"btn_del_{target_wid}"):
                            if confirm_del:
                                save_fn("worker_master", w_table_full[w_table_full["Worker_ID"].astype(str) != target_wid][WORKER_MASTER_COLS])
                                if also_del_logs:
                                    if not muster_all.empty:
                                        save_fn("muster_roll", muster_all[muster_all["Worker_ID"].astype(str) != target_wid][MUSTER_ROLL_COLS])
                                    if not adv_all.empty:
                                        save_fn("worker_advances", adv_all[adv_all["Worker_ID"].astype(str) != target_wid][ADVANCE_LOG_COLS])
                                st.session_state.selected_worker_card_id = None
                                st.rerun()

            today_dt = datetime.date.today()
            cur_payroll_map = compute_monthly_payroll_and_advances(w_table_full, muster_all, adv_all, tata_all, today_dt.year, today_dt.month, stat_cfg)
            emp_cf_bal = cur_payroll_map.get(target_wid, {}).get("Closing_Carry_Forward", 0.0)

            emp_muster = muster_all[muster_all["Worker_ID"].astype(str) == target_wid].copy() if not muster_all.empty else pd.DataFrame()
            if not emp_muster.empty:
                emp_muster["Duty_Location"] = emp_muster["Duty_Location"].apply(normalize_duty_type)

            tot_p = len(emp_muster[emp_muster["Attendance"] == "P"]) if not emp_muster.empty else 0
            tot_hd = len(emp_muster[emp_muster["Attendance"] == "HD"]) if not emp_muster.empty else 0
            tot_l = len(emp_muster[emp_muster["Attendance"] == "L"]) if not emp_muster.empty else 0
            tot_ot = pd.to_numeric(emp_muster["OT_Hours"], errors="coerce").fillna(0).sum() if not emp_muster.empty else 0.0

            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Present (P)", f"{tot_p} d")
            k2.metric("Half Days (HD)", f"{tot_hd} d")
            k3.metric("Paid Leaves (L)", f"{tot_l} d")
            k4.metric("Overtime (OT)", f"{tot_ot:.1f} hrs")
            k5.metric("Pending Advance Bal.", f"₹{emp_cf_bal:,.0f}")

            if not ppe_all.empty and "Worker_ID" in ppe_all.columns:
                emp_ppe = ppe_all[ppe_all["Worker_ID"].astype(str) == target_wid].copy()
                if not emp_ppe.empty:
                    with st.container(border=True):
                        st.markdown("###### 🦺 Issued PPE & Site Tools History")
                        st.dataframe(
                            emp_ppe[["Issue_Date", "PPE_Item", "Quantity", "Issue_Mode", "Recovery_Amount", "Next_Eligible_Date", "Remarks"]],
                            use_container_width=True, hide_index=True
                        )

            if not emp_muster.empty and is_admin:
                with st.container(border=True):
                    st.markdown("###### 📅 Daily Field Attendance Log (Admin Direct Edit)")
                    emp_muster_sorted = emp_muster.sort_values(by="Date", ascending=False).copy()
                    emp_muster_sorted["OT_Hours"] = pd.to_numeric(emp_muster_sorted["OT_Hours"], errors="coerce").fillna(0.0)
                    edited_emp_df = st.data_editor(
                        emp_muster_sorted[["Date", "Attendance", "Duty_Location", "OT_Hours", "Remarks"]],
                        use_container_width=True, hide_index=True, disabled=["Date"],
                        column_config={
                            "Attendance": st.column_config.SelectboxColumn("Status", options=["P", "HD", "L", "A", "WO"], required=True),
                            "Duty_Location": st.column_config.SelectboxColumn("Punch Type", options=["Punch", "Non-Punch"], required=True),
                            "OT_Hours": st.column_config.NumberColumn("OT (Hrs)", min_value=0.0, max_value=16.0, step=0.5),
                            "Remarks": st.column_config.TextColumn("Remarks")
                        },
                        key=f"editor_att_{target_wid}"
                    )
                    if st.button("💾 Save Attendance Changes", type="primary", use_container_width=True, key=f"save_edit_att_{target_wid}"):
                        full_m_df = load_fn("muster_roll")
                        for _, erow in edited_emp_df.iterrows():
                            d_val = str(erow["Date"])
                            mask = (full_m_df["Worker_ID"].astype(str) == target_wid) & (full_m_df["Date"].astype(str) == d_val)
                            full_m_df.loc[mask, ["Attendance", "Duty_Location", "OT_Hours", "Remarks"]] = [
                                str(erow["Attendance"]), str(erow["Duty_Location"]), float(erow["OT_Hours"] or 0.0), str(erow["Remarks"] or "")
                            ]
                        if save_fn("muster_roll", full_m_df[MUSTER_ROLL_COLS]):
                            log_attendance_activity(load_fn, save_fn, "PROFILE_EDIT", f"{user_display_name} ({current_user})", "PROFILE_ATT_EDIT", f"Edited {emp_name} ({target_wid})")
                            for k in list(st.session_state.keys()):
                                if str(k).startswith("att_state_"):
                                    del st.session_state[k]
                            st.success("Attendance updated!")
                            st.rerun()

        else:
            st.markdown("#### Employee Master Register & Selective ATM ID Cards")
            if w_table.empty or "Worker_ID" not in w_table.columns:
                st.info("No employee records found for your assigned department(s).")
                return

            avail_reg_divs = allowed_div_list if is_div_restricted else get_available_divisions(w_table)
            flt_c1, flt_c2, flt_c3, flt_c4 = st.columns([1.1, 1.0, 1.0, 1.6])
            with flt_c1:
                div_filter_opts = avail_reg_divs if (is_div_restricted and len(avail_reg_divs) == 1) else (["All"] + avail_reg_divs)
                f_div = st.selectbox("Department / Division", div_filter_opts, disabled=(is_div_restricted and len(avail_reg_divs) == 1), key="reg_div_f")
            with flt_c2:
                f_skill = st.selectbox("Skill Category", ["All"] + SKILL_CATEGORIES + ["Monthly Fixed"], key="reg_skill_f")
            with flt_c3:
                f_status = st.selectbox("Status", ["All", "ACTIVE", "INACTIVE"], index=1, key="reg_stat_f")
            with flt_c4:
                f_search = st.text_input("Search Employee", placeholder="Filter by Name, Father Name, IS-ID, or Account...").strip().lower()

            disp_table = w_table.copy()
            if f_div != "All":
                disp_table = disp_table[disp_table["Division"].astype(str).str.strip() == f_div]
            if f_skill != "All":
                disp_table = disp_table[disp_table["Skill_Category"].astype(str) == f_skill]
            if f_status != "All":
                disp_table = disp_table[disp_table["Status"].astype(str).str.upper() == f_status]
            if f_search:
                disp_table = disp_table[
                    disp_table["Full_Name"].astype(str).str.lower().str.contains(f_search) |
                    disp_table["Father_Husband_Name"].astype(str).str.lower().str.contains(f_search) |
                    disp_table["Worker_ID"].astype(str).str.lower().str.contains(f_search) |
                    disp_table["Bank_Account_No"].astype(str).str.lower().str.contains(f_search)
                ]

            act_c1, act_c2 = st.columns([1.25, 1.35], gap="medium")
            with act_c1:
                with st.container(border=True):
                    st.markdown("###### 🔍 Open Profile (Edit / Shift Dept / Photo / PPE)")
                    emp_choices = {
                        f"{r['Worker_ID']} — {r['Full_Name']}" + (f" (S/o W/o {r['Father_Husband_Name']})" if str(r.get("Father_Husband_Name", "")).strip() else f" ({r['Designation']})"): str(r["Worker_ID"])
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
                    all_card_options = {f"{r['Worker_ID']} — {r['Full_Name']}": str(r["Worker_ID"]) for _, r in disp_table.iterrows()}
                    sel_card_labels = st.multiselect("Select Employees for ID Cards", options=list(all_card_options.keys()), placeholder="Leave empty for all filtered...")
                    target_wids_for_cards = [all_card_options[lbl] for lbl in sel_card_labels] if sel_card_labels else list(all_card_options.values())
                    cards_subset_df = disp_table[disp_table["Worker_ID"].astype(str).isin(target_wids_for_cards)]

                    if FPDF_AVAILABLE and not cards_subset_df.empty:
                        if st.button(f"⚡ Prepare ID Cards PDF ({len(cards_subset_df)} Cards)", type="secondary", use_container_width=True):
                            st.session_state["prepared_id_cards_pdf"] = generate_atm_id_cards_pdf(cards_subset_df, logo_fn, load_media_fn)
                            st.session_state["prepared_id_cards_cnt"] = len(cards_subset_df)
                        if st.session_state.get("prepared_id_cards_pdf"):
                            pages_cnt = (st.session_state.get("prepared_id_cards_cnt", 1) + 7) // 8
                            st.download_button(
                                label=f"📥 Download ID Cards PDF ({st.session_state.get('prepared_id_cards_cnt')} Cards • {pages_cnt} Page)",
                                data=st.session_state["prepared_id_cards_pdf"],
                                file_name=f"M_s_INDU_SAO_ID_Cards_{datetime.date.today().strftime('%Y%m%d')}.pdf",
                                mime="application/pdf", type="primary", use_container_width=True
                            )

            disp_table_view = disp_table.copy()
            disp_table_view["Photo"] = disp_table_view["Photo_Base64"].apply(lambda x: "✅" if str(x).strip() else "—")
            disp_table_view["Week_Off_Day"] = disp_table_view["Week_Off_Day"].apply(normalize_week_off)
            req_cols = ["Worker_ID", "Full_Name", "Father_Husband_Name", "Photo", "Division", "Designation", "Skill_Category", "Base_Rate", "Bank_Account_No", "Status"]
            st.dataframe(disp_table_view[[c for c in req_cols if c in disp_table_view.columns]], use_container_width=True, hide_index=True)

    # 4. DAILY FIELD MUSTER ROLL (MULTI-DIVISION SCOPED)
    elif current_view == "Daily Muster Roll (Field Attendance)":
        st.markdown("#### Daily Field Muster Roll")
        st.caption("Multi-Department Attendance Lock, Employee Week-Off Lock, Paid Leave (L), and 90-Day Audit Trail")

        w_master = filter_workers_by_allowed_division(load_fn("worker_master"), allowed_division if is_div_restricted else "All Divisions")
        if w_master.empty or "Worker_ID" not in w_master.columns:
            st.warning("No employees available in your assigned department(s).")
            return

        active_workers = w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].copy()
        muster_df = load_fn("muster_roll")

        avail_m_divs = allowed_div_list if is_div_restricted else get_available_divisions(w_master)
        if is_div_restricted and len(avail_m_divs) == 1:
            m_div_opts = avail_m_divs
        else:
            m_div_opts = ["All Assigned Departments" if is_div_restricted else "All Divisions"] + avail_m_divs

        top_m1, top_m2, top_m3 = st.columns([1.1, 1.2, 1.2])
        with top_m1:
            muster_date = st.date_input("Select Date", value=datetime.date.today(), key="muster_dt_pick")
            muster_date_str = muster_date.strftime("%Y-%m-%d")
            selected_weekday_name = muster_date.strftime("%A")
        with top_m2:
            sel_division = st.selectbox("Department / Division", m_div_opts, disabled=(len(m_div_opts) == 1), key="muster_div_filter")
        with top_m3:
            st.metric(f"Active Crew ({selected_weekday_name})", len(active_workers))

        is_all_scope = sel_division in ["All Divisions", "All Assigned Departments"]
        if not is_all_scope:
            active_workers = active_workers[active_workers["Division"].astype(str).str.strip() == sel_division]

        allowed_wids_set = set(active_workers["Worker_ID"].astype(str).tolist())
        day_records = muster_df[muster_df["Date"].astype(str) == muster_date_str] if not muster_df.empty and "Date" in muster_df.columns else pd.DataFrame()
        if not day_records.empty and "Worker_ID" in day_records.columns:
            div_day_records = day_records[day_records["Worker_ID"].astype(str).isin(allowed_wids_set)].copy()
        else:
            div_day_records = pd.DataFrame()

        is_date_already_logged = (not div_day_records.empty)
        supervisor_locked_out = is_date_already_logged and (not is_admin)

        admin_unlock_wo = False
        if is_admin:
            admin_unlock_wo = st.checkbox(f"🔓 Admin Approval: Unlock Weekly-Off Duty for {muster_date.strftime('%d %b %Y')} ({selected_weekday_name})", value=False, key=f"adm_wo_unlock_{muster_date_str}")

        ym_prefix = muster_date.strftime("%Y-%m")
        m_dates_sub = muster_df[muster_df["Date"].astype(str).str.startswith(ym_prefix)] if not muster_df.empty and "Date" in muster_df.columns else pd.DataFrame()
        if not m_dates_sub.empty and "Worker_ID" in m_dates_sub.columns:
            m_dates_sub = m_dates_sub[m_dates_sub["Worker_ID"].astype(str).isin(allowed_wids_set)]
        logged_dates_this_month = sorted(m_dates_sub["Date"].astype(str).unique().tolist()) if not m_dates_sub.empty else []

        if is_date_already_logged:
            sup_by = str(div_day_records.iloc[0].get("Supervisor", "Admin"))
            p_cnt = len(div_day_records[div_day_records["Attendance"] == "P"])
            wo_cnt = len(div_day_records[div_day_records["Attendance"] == "WO"])
            if supervisor_locked_out:
                st.error(f"🔒 **LOCKED (ALREADY SUBMITTED)** for **{muster_date.strftime('%d %b %Y')}** ({len(div_day_records)} workers • {p_cnt} Present, {wo_cnt} Week-Off • Logged by `{sup_by}`). Contact Admin for changes.")
            else:
                st.success(f"🟢 **SAVED / ALREADY LOGGED** for **{muster_date.strftime('%d %b %Y')}** ({len(div_day_records)} workers • {p_cnt} Present, {wo_cnt} Week-Off • Logged by `{sup_by}`). Admin Edit Mode Active.")
        else:
            st.warning(f"🟡 **PENDING — NOT LOGGED YET** for **{muster_date.strftime('%d %b %Y')} ({selected_weekday_name})**.")

        days_in_m = calendar.monthrange(muster_date.year, muster_date.month)[1]
        logged_day_nums = {int(d.split("-")[2]) for d in logged_dates_this_month if len(d.split("-")) == 3}
        badges_html = "".join([
            f"<span style='display:inline-block; background:#dcfce7; color:#166534; border:1px solid #86efac; border-radius:5px; padding:1px 6px; margin:2px; font-size:0.74rem; font-weight:700;'>{d_num:02d} ✓</span>"
            if d_num in logged_day_nums else
            f"<span style='display:inline-block; background:#f8fafc; color:#94a3b8; border:1px solid #e2e8f0; border-radius:5px; padding:1px 6px; margin:2px; font-size:0.74rem;'>{d_num:02d}</span>"
            for d_num in range(1, days_in_m + 1)
        ])
        st.markdown(f"<div style='background:#ffffff; border:1px solid #e2e8f0; border-radius:10px; padding:8px 12px; margin-bottom:10px;'><div style='font-size:0.76rem; font-weight:600; color:#334155; margin-bottom:4px;'>📅 {muster_date.strftime('%B %Y')} Entry Status ({sel_division}):</div><div>{badges_html}</div></div>", unsafe_allow_html=True)

        cache_key = f"att_state_{muster_date_str}_{sel_division}"
        if cache_key not in st.session_state:
            st.session_state[cache_key] = {}
            for _, w in active_workers.iterrows():
                wid = str(w["Worker_ID"])
                w_wo_day = normalize_week_off(w.get("Week_Off_Day", "Sunday"))
                def_att = "WO" if (w_wo_day == selected_weekday_name) else "P"
                def_ot, def_loc = 0.0, "Punch"
                if not day_records.empty and "Worker_ID" in day_records.columns:
                    match_prev = day_records[day_records["Worker_ID"].astype(str) == wid]
                    if not match_prev.empty:
                        r0 = match_prev.iloc[0]
                        def_att = str(r0.get("Attendance", def_att))
                        def_ot = float(r0.get("OT_Hours", 0.0) or 0.0)
                        def_loc = normalize_duty_type(r0.get("Duty_Location", "Punch"))
                st.session_state[cache_key][wid] = {"att": def_att, "ot": def_ot, "loc": def_loc}

        if not supervisor_locked_out:
            with st.container(border=True):
                st.markdown("###### ⚡ Selective Quick-Mark Tool")
                worker_map = {
                    f"{r['Worker_ID']} — {r['Full_Name']}" + (f" (S/o W/o {r['Father_Husband_Name']})" if str(r.get("Father_Husband_Name", "")).strip() else ""): str(r["Worker_ID"])
                    for _, r in active_workers.iterrows()
                }
                wo_lookup_map = {str(r["Worker_ID"]): (normalize_week_off(r.get("Week_Off_Day", "Sunday")) == selected_weekday_name) for _, r in active_workers.iterrows()}

                sm_c1, sm_c2, sm_c3 = st.columns([2.0, 1.3, 1.0])
                with sm_c1:
                    chosen_labels = st.multiselect("Choose Workers", options=list(worker_map.keys()), placeholder="Leave empty for ALL unlocked workers...", label_visibility="collapsed")
                with sm_c2:
                    bulk_action = st.selectbox("Quick Action", ["Present — Punch", "Present — Non-Punch", "Half Day (HD) — Punch", "Half Day (HD) — Non-Punch", "Paid Leave (L)", "Absent (A)", "Week Off (WO)"], label_visibility="collapsed")
                with sm_c3:
                    if st.button("Apply to Selected", type="primary", use_container_width=True):
                        target_wids = [worker_map[l] for l in chosen_labels] if chosen_labels else list(worker_map.values())
                        for wid in target_wids:
                            if wo_lookup_map.get(wid, False) and not admin_unlock_wo:
                                continue
                            if wid in st.session_state[cache_key]:
                                if bulk_action == "Present — Punch":
                                    st.session_state[cache_key][wid].update({"att": "P", "loc": "Punch"})
                                elif bulk_action == "Present — Non-Punch":
                                    st.session_state[cache_key][wid].update({"att": "P", "loc": "Non-Punch"})
                                elif bulk_action == "Half Day (HD) — Punch":
                                    st.session_state[cache_key][wid].update({"att": "HD", "loc": "Punch"})
                                elif bulk_action == "Half Day (HD) — Non-Punch":
                                    st.session_state[cache_key][wid].update({"att": "HD", "loc": "Non-Punch"})
                                elif bulk_action == "Paid Leave (L)":
                                    st.session_state[cache_key][wid]["att"] = "L"
                                elif bulk_action == "Absent (A)":
                                    st.session_state[cache_key][wid]["att"] = "A"
                                elif bulk_action == "Week Off (WO)":
                                    st.session_state[cache_key][wid]["att"] = "WO"
                        st.rerun()

        grid_cols = st.columns(2, gap="medium")
        att_options_list = ["P", "HD", "L", "A", "WO"]

        for idx, (_, w_row) in enumerate(active_workers.iterrows()):
            col_target = grid_cols[idx % 2]
            wid = str(w_row["Worker_ID"])
            wname, wdiv = str(w_row["Full_Name"]), str(w_row["Division"])
            wfather = str(w_row.get("Father_Husband_Name", "")).strip()
            wrole, wskill = str(w_row["Designation"]), str(w_row.get("Skill_Category", ""))
            w_wo_day = normalize_week_off(w_row.get("Week_Off_Day", "Sunday"))
            is_wo_locked = (w_wo_day == selected_weekday_name) and (not admin_unlock_wo)
            card_disabled = supervisor_locked_out or is_wo_locked

            cur_state = st.session_state[cache_key].get(wid, {})
            if is_wo_locked and cur_state.get("att") not in ["WO"]:
                has_saved_working = False
                if not day_records.empty and "Worker_ID" in day_records.columns:
                    prev_r = day_records[day_records["Worker_ID"].astype(str) == wid]
                    if not prev_r.empty and str(prev_r.iloc[0].get("Attendance")) in ["P", "HD"]:
                        has_saved_working = True
                if not has_saved_working:
                    cur_state["att"] = "WO"
                    cur_state["ot"] = 0.0

            cur_att_val = cur_state.get("att", "P") if cur_state.get("att", "P") in att_options_list else "P"
            cur_loc_norm = normalize_duty_type(cur_state.get("loc", "Punch"))

            with col_target:
                with st.container(border=True):
                    lock_tag = f"  •  <span style='color:#dc2626; font-size:0.74rem; font-weight:600;'>🔒 Weekly Off ({w_wo_day})</span>" if is_wo_locked else ""
                    f_sub = f" (S/o W/o {wfather})" if wfather else ""
                    st.markdown(f"**{wname}**{f_sub} (`{wid}`) • <span style='font-size: 0.78rem; color: #4f46e5; font-weight: 500;'>{wdiv}</span>{lock_tag}", unsafe_allow_html=True)
                    st.caption(f"{wrole} ({wskill}) • Assigned Off: {w_wo_day}")

                    r_c1, r_c2, r_c3 = st.columns([1.1, 1.3, 1.0])
                    with r_c1:
                        cur_state["att"] = st.selectbox("Status", att_options_list, index=att_options_list.index(cur_att_val), disabled=card_disabled, key=f"att_s_{wid}")
                    with r_c2:
                        cur_state["loc"] = st.selectbox("Punch Type", ["Punch", "Non-Punch"], index=["Punch", "Non-Punch"].index(cur_loc_norm), disabled=card_disabled, key=f"loc_s_{wid}")
                    with r_c3:
                        cur_state["ot"] = st.number_input("OT (Hrs)", min_value=0.0, max_value=16.0, step=0.5, value=float(cur_state.get("ot", 0.0)), disabled=card_disabled, key=f"ot_s_{wid}")
            st.session_state[cache_key][wid] = cur_state

        if not supervisor_locked_out:
            if st.button("Save Daily Muster Roll", type="primary", use_container_width=True):
                marked_rows = []
                p_tot, hd_tot, l_tot, a_tot, wo_tot = 0, 0, 0, 0, 0
                for _, w_row in active_workers.iterrows():
                    wid = str(w_row["Worker_ID"])
                    s_data = st.session_state[cache_key].get(wid, {})
                    att_v = s_data.get("att", "P")
                    if att_v == "P": p_tot += 1
                    elif att_v == "HD": hd_tot += 1
                    elif att_v == "L": l_tot += 1
                    elif att_v == "A": a_tot += 1
                    elif att_v == "WO": wo_tot += 1

                    marked_rows.append({
                        "Date": muster_date_str, "Worker_ID": wid, "Worker_Name": str(w_row["Full_Name"]),
                        "Division": str(w_row["Division"]), "Attendance": att_v, "OT_Hours": s_data.get("ot", 0.0),
                        "Duty_Location": normalize_duty_type(s_data.get("loc", "Punch")),
                        "Advance_Amount": 0.0, "Payment_Mode": "Cash", "Supervisor": current_user,
                        "Remarks": "Admin WO Approved" if (normalize_week_off(w_row.get("Week_Off_Day", "Sunday")) == selected_weekday_name and att_v in ["P", "HD"]) else ""
                    })

                all_m = load_fn("muster_roll")
                if not all_m.empty and "Date" in all_m.columns and "Worker_ID" in all_m.columns:
                    w_ids = [r["Worker_ID"] for r in marked_rows]
                    all_m = all_m[~((all_m["Date"].astype(str) == muster_date_str) & (all_m["Worker_ID"].astype(str).isin(w_ids)))]

                updated_m = pd.concat([all_m, pd.DataFrame(marked_rows)], ignore_index=True)
                if save_fn("muster_roll", updated_m[MUSTER_ROLL_COLS]):
                    act_lbl = "ADMIN_UPDATE" if is_date_already_logged else "NEW_SUBMISSION"
                    sum_txt = f"{sel_division}: P={p_tot}, HD={hd_tot}, L={l_tot}, A={a_tot}, WO={wo_tot}"
                    log_attendance_activity(load_fn, save_fn, muster_date_str, f"{user_display_name} ({current_user})", act_lbl, sum_txt)
                    st.success(f"Daily Muster Roll for {muster_date_str} saved!")
                    st.rerun()

        if is_admin:
            with st.expander("📜 Attendance Submission Audit Log (Last 90 Days • Auto-Purged)", expanded=False):
                aud_log_df = load_fn("attendance_audit_log")
                if aud_log_df.empty or "Timestamp" not in aud_log_df.columns:
                    st.info("No attendance audit logs recorded yet.")
                else:
                    st.dataframe(aud_log_df.iloc[::-1][["Timestamp", "Attendance_Date", "Logged_By_User", "Action_Type", "Summary"]], use_container_width=True, hide_index=True)

    # 5. MONTHLY ATTENDANCE MATRIX
    elif current_view == "Monthly Attendance Matrix":
        st.markdown("#### Monthly Attendance Matrix (Excel Register)")
        w_master = filter_workers_by_allowed_division(load_fn("worker_master"), allowed_division if is_div_restricted else "All Divisions")
        muster_df = filter_workers_by_allowed_division(load_fn("muster_roll"), allowed_division if is_div_restricted else "All Divisions")

        avail_mat_divs = allowed_div_list if is_div_restricted else get_available_divisions(w_master)
        if is_div_restricted and len(avail_mat_divs) == 1:
            mat_div_opts = avail_mat_divs
        else:
            mat_div_opts = ["All Assigned Departments" if is_div_restricted else "All Divisions"] + avail_mat_divs

        mc1, mc2, mc3 = st.columns([1.0, 1.2, 1.3])
        with mc1:
            mat_year = st.selectbox("Year", [2025, 2026, 2027], index=1, key="mat_yr")
        with mc2:
            mat_month = st.selectbox("Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1, key="mat_mo")
        with mc3:
            mat_div = st.selectbox("Department / Division", mat_div_opts, disabled=(len(mat_div_opts) == 1), key="mat_dv")

        m_num = list(calendar.month_name).index(mat_month)
        ym_str = f"{mat_year}-{m_num:02d}"
        days_cnt = calendar.monthrange(mat_year, m_num)[1]

        if muster_df.empty or "Date" not in muster_df.columns:
            st.info(f"No attendance data recorded for {mat_month} {mat_year}.")
            return

        m_sub = muster_df[muster_df["Date"].astype(str).str.startswith(ym_str)].copy()
        m_sub["Duty_Location"] = m_sub["Duty_Location"].apply(normalize_duty_type)
        if mat_div not in ["All Divisions", "All Assigned Departments"] and "Division" in m_sub.columns:
            m_sub = m_sub[m_sub["Division"].astype(str).str.strip() == mat_div]

        present_wids = m_sub[m_sub["Attendance"].isin(["P", "HD", "L"])]["Worker_ID"].astype(str).unique().tolist() if not m_sub.empty else []
        if not present_wids:
            st.warning(f"No employee has any Present ('P', 'HD', or 'L') days in {mat_month} {mat_year}.")
            return

        matrix_rows = []
        for wid in sorted(present_wids):
            w_logs = m_sub[m_sub["Worker_ID"].astype(str) == wid]
            w_name = str(w_logs.iloc[0].get("Worker_Name", ""))
            w_div_val = str(w_logs.iloc[0].get("Division", ""))
            w_desig, w_skill_v, w_fath_v = "", "", ""
            if not w_master.empty and "Worker_ID" in w_master.columns:
                wm_hit = w_master[w_master["Worker_ID"].astype(str) == wid]
                if not wm_hit.empty:
                    w_name = str(wm_hit.iloc[0].get("Full_Name", w_name))
                    w_fath_v = str(wm_hit.iloc[0].get("Father_Husband_Name", ""))
                    w_desig = str(wm_hit.iloc[0].get("Designation", ""))
                    w_skill_v = str(wm_hit.iloc[0].get("Skill_Category", ""))

            row_dict = {"Worker ID": wid, "Employee Name": w_name, "Father/Husband": w_fath_v, "Category": w_skill_v, "Designation": w_desig, "Department": w_div_val}
            day_map = {int(str(lr["Date"]).split("-")[2]): str(lr["Attendance"]) for _, lr in w_logs.iterrows() if len(str(lr["Date"]).split("-")) == 3}
            for d_i in range(1, days_cnt + 1):
                row_dict[f"{d_i:02d}"] = day_map.get(d_i, "")

            punch_p = len(w_logs[(w_logs["Duty_Location"] == "Punch") & (w_logs["Attendance"] == "P")]) + (len(w_logs[(w_logs["Duty_Location"] == "Punch") & (w_logs["Attendance"] == "HD")]) * 0.5)
            non_punch_p = len(w_logs[(w_logs["Duty_Location"] == "Non-Punch") & (w_logs["Attendance"] == "P")]) + (len(w_logs[(w_logs["Duty_Location"] == "Non-Punch") & (w_logs["Attendance"] == "HD")]) * 0.5)
            paid_l = len(w_logs[w_logs["Attendance"] == "L"])
            tot_ot = pd.to_numeric(w_logs["OT_Hours"], errors="coerce").fillna(0.0).sum()

            row_dict["Punch Days"] = punch_p
            row_dict["Non-Punch Days"] = non_punch_p
            row_dict["Paid Leave"] = paid_l
            row_dict["Total Days"] = punch_p + non_punch_p + paid_l
            row_dict["OT Hrs"] = tot_ot
            matrix_rows.append(row_dict)

        matrix_df = pd.DataFrame(matrix_rows)
        st.dataframe(matrix_df, use_container_width=True, hide_index=True)
        st.download_button(
            label=f"📥 Export {mat_month} {mat_year} Attendance Register (CSV)",
            data=matrix_df.to_csv(index=False).encode("utf-8"),
            file_name=f"M_s_INDU_SAO_Attendance_Matrix_{ym_str}.csv", mime="text/csv", type="primary"
        )

    # 6. PPE, SAFETY GEAR & SITE TOOLS ISSUE REGISTER (WITH DYNAMIC NEW ITEM ADDITION)
    elif current_view == "🦺 PPE & Safety Gear Issue":
        st.markdown("#### PPE, Safety Gear & Site Tools Issue Register")
        st.caption("Track safety gear & site tools (Measuring Tapes, Helmets, Shoes, etc.) with validity timelines, custom item creation, and wage recovery")

        w_master = filter_workers_by_allowed_division(load_fn("worker_master"), allowed_division if is_div_restricted else "All Divisions")
        ppe_policy_df = get_active_ppe_policies_df(load_fn)
        ppe_log_df = load_fn("ppe_issue_log")

        active_w = w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].copy() if not w_master.empty and "Status" in w_master.columns else pd.DataFrame()
        if active_w.empty:
            st.warning("No active employees found in your assigned department(s).")
            return

        c_ppe1, c_ppe2 = st.columns([1.15, 1.85], gap="large")

        with c_ppe1:
            with st.container(border=True):
                st.markdown("###### 🦺 Issue Safety Gear / Site Tool")
                w_map = {f"{r['Worker_ID']} — {r['Full_Name']}": (str(r["Worker_ID"]), str(r["Full_Name"]), str(r["Division"])) for _, r in active_w.iterrows()}
                sel_w_lbl = st.selectbox("Select Employee", list(w_map.keys()), key="ppe_w_sel")
                w_id, w_name, w_div = w_map[sel_w_lbl]

                item_list = ppe_policy_df["PPE_Item"].astype(str).tolist() + ["➕ Add New Item / Tool..."]
                sel_item_raw = st.selectbox("Select Item / Tool", item_list, key="ppe_item_sel")

                is_creating_new_item = (sel_item_raw == "➕ Add New Item / Tool...")
                if is_creating_new_item:
                    new_item_name = st.text_input("New Item / Tool Name", placeholder="e.g. Measuring Tape (5m) / Spirit Level", key="ppe_new_item_name").strip()
                    ni_c1, ni_c2 = st.columns(2)
                    with ni_c1:
                        val_days = int(st.number_input("Free Validity (Days)", min_value=1, max_value=1825, value=180, step=15, key="ppe_new_val_days"))
                    with ni_c2:
                        rec_unit_cost = float(st.number_input("Lost/Early Recovery Cost (₹)", min_value=0.0, value=150.0, step=10.0, key="ppe_new_rec_cost"))
                    exch_rule = f"{val_days} Days Validity; Return Old Item"
                    sel_item = new_item_name
                else:
                    sel_item = sel_item_raw
                    pol_row = ppe_policy_df[ppe_policy_df["PPE_Item"].astype(str) == sel_item].iloc[0]
                    val_days = int(pol_row["Validity_Days"])
                    rec_unit_cost = float(pol_row["Recovery_Cost"])
                    exch_rule = str(pol_row["Exchange_Rule"])

                p_c1, p_c2 = st.columns(2)
                with p_c1:
                    iss_date = st.date_input("Issue Date", value=datetime.date.today(), key="ppe_iss_dt")
                with p_c2:
                    iss_qty = st.number_input("Quantity", min_value=1, max_value=20, value=1, step=1, key="ppe_qty")

                is_eligible_free = True
                next_due_dt = iss_date + datetime.timedelta(days=val_days)
                days_remaining = 0

                if sel_item and not ppe_log_df.empty and "Worker_ID" in ppe_log_df.columns and "PPE_Item" in ppe_log_df.columns:
                    prev_issues = ppe_log_df[
                        (ppe_log_df["Worker_ID"].astype(str) == w_id) &
                        (ppe_log_df["PPE_Item"].astype(str) == sel_item)
                    ].copy()
                    if not prev_issues.empty:
                        last_rec = prev_issues.iloc[-1]
                        last_dt_str = str(last_rec.get("Issue_Date", ""))
                        last_due_str = str(last_rec.get("Next_Eligible_Date", ""))
                        try:
                            last_due_parsed = datetime.datetime.strptime(last_due_str, "%Y-%m-%d").date()
                            if iss_date < last_due_parsed:
                                is_eligible_free = False
                                days_remaining = (last_due_parsed - iss_date).days
                                st.error(
                                    f"🔴 **Within Validity Lock ({days_remaining} Days Remaining)**\n\n"
                                    f"Last Issued: `{last_dt_str}` | Next Free Issue Due: **`{last_due_str}`**\n\n"
                                    f"Policy: *{exch_rule}*"
                                )
                            else:
                                st.success(
                                    f"🟢 **Eligible for Free Issue (FOC)**\n\n"
                                    f"Previous item issued on `{last_dt_str}` has completed its {val_days}-day validity period."
                                )
                        except Exception:
                            st.success(f"🟢 **Eligible for Free Issue (FOC)** • Validity: {val_days} Days")
                    else:
                        st.success(f"🟢 **First-Time Issue — Eligible Free of Cost (FOC)** • Validity: {val_days} Days")
                else:
                    st.success(f"🟢 **First-Time Issue — Eligible Free of Cost (FOC)** • Validity: {val_days} Days")

                mode_options = [
                    "FREE (Eligible / First Issue)",
                    "PAID / LOST RECOVERY (Deduct from Wages)",
                    "DAMAGED EXCHANGE (Old Returned)"
                ]
                default_mode_idx = 0 if is_eligible_free else 1
                iss_mode = st.selectbox("Issue Mode", mode_options, index=default_mode_idx, key=f"ppe_mode_{w_id}_{sel_item}_{is_eligible_free}")

                old_returned = st.checkbox("Old / Damaged Item Returned by Employee", value=False, key="ppe_old_ret")

                if iss_mode.startswith("PAID"):
                    rec_amount = st.number_input("Recovery Amount to Deduct from Wages (₹)", min_value=0.0, value=float(rec_unit_cost * iss_qty), step=10.0, key=f"ppe_rec_{sel_item}_{iss_qty}")
                else:
                    rec_amount = 0.0

                ppe_rem = st.text_input("Remarks / Condition Note", placeholder="e.g. Site layout work / Lost on site", key="ppe_rem").strip()

                if st.button("💾 Confirm & Issue Item", type="primary", use_container_width=True):
                    if is_creating_new_item and not sel_item:
                        st.error("Please type the name of the new item / tool.")
                    elif iss_mode.startswith("DAMAGED") and not old_returned and not ppe_rem:
                        st.error("Please confirm old item return or enter a damage justification remark.")
                    elif (not is_eligible_free) and iss_mode.startswith("FREE") and (not is_admin):
                        st.error("This item is still within its validity period. Select 'Paid / Lost Recovery' or 'Damaged Exchange', or request Admin approval.")
                    else:
                        if is_creating_new_item and sel_item not in ppe_policy_df["PPE_Item"].astype(str).tolist():
                            new_pol_row = {
                                "PPE_Item": sel_item,
                                "Validity_Days": int(val_days),
                                "Recovery_Cost": float(rec_unit_cost),
                                "Exchange_Rule": exch_rule,
                                "Remarks": "Added via Issue Form"
                            }
                            upd_pol_df = pd.concat([ppe_policy_df, pd.DataFrame([new_pol_row])], ignore_index=True)
                            save_fn("ppe_policy_master", upd_pol_df[PPE_POLICY_COLS])

                        iss_date_str = iss_date.strftime("%Y-%m-%d")
                        new_ppe_row = {
                            "Issue_ID": f"PPE-{int(time.time())}",
                            "Issue_Date": iss_date_str,
                            "Worker_ID": w_id,
                            "Worker_Name": w_name,
                            "Division": w_div,
                            "PPE_Item": sel_item,
                            "Quantity": int(iss_qty),
                            "Issue_Mode": iss_mode,
                            "Recovery_Amount": float(rec_amount),
                            "Validity_Days": val_days,
                            "Next_Eligible_Date": next_due_dt.strftime("%Y-%m-%d"),
                            "Old_Item_Returned": "Yes" if old_returned else "No",
                            "Issued_By": current_user,
                            "Remarks": ppe_rem
                        }
                        upd_ppe = pd.concat([ppe_log_df, pd.DataFrame([new_ppe_row])], ignore_index=True)
                        saved_ok = save_fn("ppe_issue_log", upd_ppe[PPE_ISSUE_COLS])

                        if saved_ok and rec_amount > 0:
                            adv_cur = load_fn("worker_advances")
                            new_adv_ded = {
                                "Txn_ID": f"PPE-REC-{int(time.time())}",
                                "Date": iss_date_str,
                                "Worker_ID": w_id,
                                "Worker_Name": w_name,
                                "Division": w_div,
                                "Amount": float(rec_amount),
                                "Payment_Mode": "PPE/Tools",
                                "Supervisor": current_user,
                                "Remarks": f"PPE/Tool Recovery: {sel_item} (Qty {iss_qty})"
                            }
                            upd_adv = pd.concat([adv_cur, pd.DataFrame([new_adv_ded])], ignore_index=True)
                            save_fn("worker_advances", upd_adv[ADVANCE_LOG_COLS])

                        if saved_ok:
                            st.success(f"Issued {iss_qty} x {sel_item} to {w_name} (Next Free Issue Date: {next_due_dt.strftime('%Y-%m-%d')}).")
                            st.rerun()

        with c_ppe2:
            with st.container(border=True):
                st.markdown("###### 📋 Recent PPE & Site Tools Issuance Log")
                disp_ppe = filter_workers_by_allowed_division(ppe_log_df, allowed_division if is_div_restricted else "All Divisions")
                if disp_ppe.empty or "Issue_ID" not in disp_ppe.columns:
                    st.info("No PPE or site tools issued yet.")
                else:
                    show_cols = ["Issue_Date", "Worker_ID", "Worker_Name", "PPE_Item", "Quantity", "Issue_Mode", "Recovery_Amount", "Next_Eligible_Date"]
                    st.dataframe(disp_ppe.iloc[::-1][show_cols], use_container_width=True, hide_index=True)

                    csv_ppe = disp_ppe.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        label="📥 Export PPE & Tools Register (CSV)",
                        data=csv_ppe,
                        file_name=f"M_s_INDU_SAO_PPE_Tools_Register_{datetime.date.today().strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        type="secondary"
                    )

            with st.expander("⚙️ PPE & Site Tools Master List (Click '+ Add row' below table to add new items)", expanded=False):
                st.caption("Add new items (Measuring Tapes, Raincoats, Tools, etc.) or edit free validity days and recovery rates.")
                edited_pol = st.data_editor(
                    ppe_policy_df[PPE_POLICY_COLS],
                    use_container_width=True,
                    hide_index=True,
                    num_rows="dynamic" if is_admin else "fixed",
                    disabled=[] if is_admin else PPE_POLICY_COLS,
                    key="ppe_policy_editor"
                )
                if is_admin and st.button("💾 Save PPE & Tools Master List", type="primary"):
                    clean_pol = edited_pol[edited_pol["PPE_Item"].astype(str).str.strip() != ""].copy()
                    clean_pol["Validity_Days"] = pd.to_numeric(clean_pol["Validity_Days"], errors="coerce").fillna(180).astype(int)
                    clean_pol["Recovery_Cost"] = pd.to_numeric(clean_pol["Recovery_Cost"], errors="coerce").fillna(0.0)
                    if save_fn("ppe_policy_master", clean_pol[PPE_POLICY_COLS]):
                        st.success("PPE & Site Tools master list updated!")
                        st.rerun()

    # 7. ADVANCE, SITE THEFT/LOSS SHARED RECOVERY & CARRY-FORWARD LEDGER
    elif current_view == "Advance & Expense Register":
        st.markdown("#### Advance, Site Loss Recovery & Carry-Forward Ledger")
        w_master = filter_workers_by_allowed_division(load_fn("worker_master"), allowed_division if is_div_restricted else "All Divisions")
        muster_df = load_fn("muster_roll")
        adv_history = load_fn("worker_advances")
        tata_df = load_fn("tata_form_xvii")
        stat_cfg = get_active_statutory_config(load_fn)

        active_w_df = w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].copy() if not w_master.empty and "Worker_ID" in w_master.columns else pd.DataFrame()
        worker_options = {f"{r['Full_Name']} ({r['Worker_ID']})": (r['Worker_ID'], r['Full_Name'], r['Division']) for _, r in active_w_df.iterrows()} if not active_w_df.empty else {}

        c_adv1, c_adv2 = st.columns([1.05, 1.95], gap="large")
        with c_adv1:
            with st.container(border=True):
                st.markdown("###### 💵 Disburse Individual Advance")
                if worker_options:
                    with st.form("disburse_adv_form"):
                        a_date = st.date_input("Date", value=datetime.date.today())
                        sel_w = st.selectbox("Employee", list(worker_options.keys()))
                        a_amt = st.number_input("Amount (₹)", min_value=50.0, step=100.0, value=500.0)
                        a_mode = st.selectbox("Category / Mode", ["Cash", "Online UPI", "Bank Transfer", "Fuel Advance", "PPE/Tools", "Theft / Loss Recovery"])
                        a_rem = st.text_input("Remarks").strip()
                        if st.form_submit_button("Record Transaction", type="primary", use_container_width=True):
                            w_id, w_name, w_div = worker_options[sel_w]
                            new_adv = {
                                "Txn_ID": f"ADV-{int(time.time())}", "Date": a_date.strftime("%Y-%m-%d"),
                                "Worker_ID": w_id, "Worker_Name": w_name, "Division": w_div,
                                "Amount": a_amt, "Payment_Mode": a_mode, "Supervisor": current_user, "Remarks": a_rem
                            }
                            updated_adv = pd.concat([adv_history, pd.DataFrame([new_adv])], ignore_index=True)
                            if save_fn("worker_advances", updated_adv[ADVANCE_LOG_COLS]):
                                st.success("Transaction recorded.")
                                st.rerun()

            st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

            # SITE THEFT / LOSS BULK SHARED RECOVERY
            with st.container(border=True):
                st.markdown("###### 🚨 Site Theft / Loss Shared Recovery")
                st.caption("Auto-fetches workers engaged on the incident date and splits recovery equally or custom-wise.")

                avail_th_divs = allowed_div_list if is_div_restricted else get_available_divisions(w_master)
                th_div_opts = avail_th_divs if (is_div_restricted and len(avail_th_divs) == 1) else (["All Divisions"] + avail_th_divs)

                th_c1, th_c2 = st.columns(2)
                with th_c1:
                    th_date = st.date_input("Incident Date", value=datetime.date.today(), key="theft_dt_pick")
                    th_date_str = th_date.strftime("%Y-%m-%d")
                with th_c2:
                    th_div = st.selectbox("Site / Division", th_div_opts, disabled=(len(th_div_opts) == 1), key="theft_div_pick")

                th_item = st.text_input("Stolen / Lost Item Description", placeholder="e.g. Copper Wire Coil / Bosch Grinder", key="theft_item_txt").strip()
                th_tot_loss = st.number_input("Total Recovery Amount (₹)", min_value=0.0, value=2000.0, step=100.0, key="theft_tot_amt")
                th_split_mode = st.radio("Split Method", ["Equal Split", "Custom Split"], horizontal=True, key="theft_split_mode")

                # Find workers engaged (Present P or HD) on th_date_str
                scoped_active_w = active_w_df.copy()
                if th_div != "All Divisions" and not scoped_active_w.empty:
                    scoped_active_w = scoped_active_w[scoped_active_w["Division"].astype(str).str.strip() == th_div]

                engaged_wids_default = []
                if not muster_df.empty and "Date" in muster_df.columns and "Worker_ID" in muster_df.columns:
                    m_hit = muster_df[
                        (muster_df["Date"].astype(str) == th_date_str) &
                        (muster_df["Attendance"].isin(["P", "HD"]))
                    ]
                    if not m_hit.empty:
                        engaged_set = set(m_hit["Worker_ID"].astype(str).tolist())
                        engaged_wids_default = [str(r["Worker_ID"]) for _, r in scoped_active_w.iterrows() if str(r["Worker_ID"]) in engaged_set]

                all_th_worker_map = {
                    f"{r['Worker_ID']} — {r['Full_Name']}": (str(r["Worker_ID"]), str(r["Full_Name"]), str(r["Division"]))
                    for _, r in scoped_active_w.iterrows()
                }
                default_th_labels = [
                    lbl for lbl, (wid_v, _, _) in all_th_worker_map.items() if wid_v in engaged_wids_default
                ]

                if engaged_wids_default:
                    st.info(f"📋 Auto-detected **{len(engaged_wids_default)} workers** present on `{th_date_str}`.")
                else:
                    st.caption(f"No attendance logged yet for `{th_date_str}` — select engaged workers manually below:")

                sel_th_labels = st.multiselect(
                    "Engaged Workers Responsible",
                    options=list(all_th_worker_map.keys()),
                    default=default_th_labels,
                    key=f"theft_w_multi_{th_date_str}_{th_div}"
                )

                if sel_th_labels and th_tot_loss > 0:
                    cnt_sel = len(sel_th_labels)
                    equal_share = round(th_tot_loss / cnt_sel, 0)
                    split_preview_rows = []
                    for i_s, lbl in enumerate(sel_th_labels):
                        wid_v, wname_v, wdiv_v = all_th_worker_map[lbl]
                        # Adjust last worker by rounding diff so sum matches th_tot_loss exactly
                        share_amt = round(th_tot_loss - (equal_share * (cnt_sel - 1)), 0) if i_s == cnt_sel - 1 else equal_share
                        split_preview_rows.append({
                            "Worker_ID": wid_v,
                            "Worker_Name": wname_v,
                            "Division": wdiv_v,
                            "Recovery_Amount": float(max(0.0, share_amt))
                        })

                    split_df = pd.DataFrame(split_preview_rows)
                    if th_split_mode == "Custom Split":
                        edited_split_df = st.data_editor(
                            split_df,
                            use_container_width=True,
                            hide_index=True,
                            disabled=["Worker_ID", "Worker_Name", "Division"],
                            key=f"theft_custom_ed_{th_date_str}_{cnt_sel}"
                        )
                    else:
                        st.dataframe(split_df[["Worker_ID", "Worker_Name", "Recovery_Amount"]], use_container_width=True, hide_index=True)
                        edited_split_df = split_df

                    tot_assigned = float(pd.to_numeric(edited_split_df["Recovery_Amount"], errors="coerce").fillna(0.0).sum())
                    st.caption(f"Total Recovery Allocated: **₹{tot_assigned:,.0f}** across **{cnt_sel} workers**")

                    if st.button("💾 Post Shared Recovery to Advance Ledger", type="primary", use_container_width=True, key="btn_post_theft_rec"):
                        if not th_item:
                            st.error("Please enter the stolen/lost item description.")
                        elif tot_assigned <= 0:
                            st.error("Total recovery amount must be greater than ₹0.")
                        else:
                            base_ts = int(time.time())
                            new_rec_rows = []
                            for idx_r, sr in edited_split_df.iterrows():
                                amt_r = float(pd.to_numeric(sr["Recovery_Amount"], errors="coerce") or 0.0)
                                if amt_r > 0:
                                    new_rec_rows.append({
                                        "Txn_ID": f"THF-{base_ts}-{idx_r + 1}",
                                        "Date": th_date_str,
                                        "Worker_ID": str(sr["Worker_ID"]),
                                        "Worker_Name": str(sr["Worker_Name"]),
                                        "Division": str(sr["Division"]),
                                        "Amount": amt_r,
                                        "Payment_Mode": "Theft / Loss Recovery",
                                        "Supervisor": current_user,
                                        "Remarks": f"Site Loss/Theft Recovery: {th_item} ({th_date_str})"
                                    })
                            if new_rec_rows:
                                updated_adv = pd.concat([adv_history, pd.DataFrame(new_rec_rows)], ignore_index=True)
                                if save_fn("worker_advances", updated_adv[ADVANCE_LOG_COLS]):
                                    st.success(f"Posted ₹{tot_assigned:,.0f} recovery across {len(new_rec_rows)} workers!")
                                    st.rerun()

        with c_adv2:
            with st.container(border=True):
                st.markdown("###### 📊 Worker-Wise Advance & Recovery Balance (Carry-Forward Status)")
                today_dt = datetime.date.today()
                payroll_now = compute_monthly_payroll_and_advances(w_master, muster_df, adv_history, tata_df, today_dt.year, today_dt.month, stat_cfg)
                bal_rows = [
                    {
                        "Worker ID": wid, "Name": p["Worker_Name"], "Opening Adv. (₹)": p["Opening_Advance"],
                        "This Month Adv. (₹)": p["Month_New_Advance"], "Adjusted in Wages (₹)": p["Advance_Deducted"],
                        "Carry Forward Next Month (₹)": p["Closing_Carry_Forward"]
                    }
                    for wid, p in payroll_now.items() if p["Total_Advance_Due"] > 0 or p["Closing_Carry_Forward"] > 0
                ]
                if bal_rows:
                    st.dataframe(pd.DataFrame(bal_rows), use_container_width=True, hide_index=True)
                else:
                    st.info("All employee advances and recoveries are fully settled (₹0 pending balance).")

            with st.container(border=True):
                st.markdown("###### Recent Advance & Recovery Transactions")
                disp_adv = filter_workers_by_allowed_division(adv_history, allowed_division if is_div_restricted else "All Divisions")
                if not disp_adv.empty and "Txn_ID" in disp_adv.columns:
                    st.dataframe(disp_adv.iloc[::-1][["Date", "Worker_Name", "Amount", "Payment_Mode", "Supervisor", "Remarks"]].head(20), use_container_width=True, hide_index=True)

    # 8. MONTHLY WAGES, FORM XVI + DUPLICATE RESOLVER, FORM XVII + DUPLICATE RESOLVER, SLIPS & BANK LETTERS
    elif current_view == "Monthly Wages & Payout Summary":
        st.markdown("#### Monthly Wages, Biometric Audit, Salary Slips & Bank Letters")
        w_master_full = load_fn("worker_master")
        w_master = filter_workers_by_allowed_division(w_master_full, allowed_division if is_div_restricted else "All Divisions")
        muster_df = load_fn("muster_roll")
        adv_df = load_fn("worker_advances")
        f16_df = load_fn("tata_form_xvi")
        tata_df = load_fn("tata_form_xvii")
        stat_cfg = get_active_statutory_config(load_fn)

        epf_cap_disp = f"₹{float(stat_cfg['EPF_WAGE_CEILING']):,.0f}"
        epf_pct_disp = f"{float(stat_cfg['EPF_RATE_PCT']):g}%"
        esic_cap_disp = f"₹{float(stat_cfg['ESIC_WAGE_CEILING']):,.0f}"
        esic_pct_disp = f"{float(stat_cfg['ESIC_RATE_PCT']):g}%"

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

        f16_month_df = f16_df[f16_df["Wages_Period"].apply(period_matches)].copy() if (not f16_df.empty and "Wages_Period" in f16_df.columns) else pd.DataFrame(columns=TATA_FORM_XVI_COLS)
        is_f16_uploaded_for_month = (not f16_month_df.empty)

        with st.expander(f"📋 Step 1: Import Tata Steel FORM XVI (Biometric Muster Roll) — {'✅ Synced' if is_f16_uploaded_for_month else '⚠️ Required First'}", expanded=(not is_f16_uploaded_for_month)):
            st.caption("Parses Form XVI PDF/Excel, skips 0-attendance workers, auto-matches Father/Husband Name, and prompts Duplicate Name Resolver if needed.")
            xvi_file = st.file_uploader("Upload Tata Steel Form XVI (PDF / CSV / XLSX)", type=["pdf", "csv", "xlsx"], key="f16_uploader")
            if xvi_file:
                parsed_16 = parse_tata_form_xvi_file(xvi_file, target_ym)
                if parsed_16.empty:
                    st.error("Unable to parse active worker punch records from this Form XVI file.")
                else:
                    full_w_all = load_fn("worker_master")
                    st.success(f"Extracted {len(parsed_16)} active workers (zero-attendance workers skipped) • Total Days: {parsed_16['Punch_Days_Count'].sum():.0f}")

                    resolved_f16_wids = {}
                    ambiguous_f16_rows = []

                    for idx16, r16 in parsed_16.iterrows():
                        wn_up = normalize_name_key(r16["Worker_Name"])
                        fn_up = normalize_name_key(r16.get("Father_Husband_Name", ""))

                        wm_hits = full_w_all[full_w_all["Full_Name"].apply(normalize_name_key) == wn_up] if (not full_w_all.empty and "Full_Name" in full_w_all.columns) else pd.DataFrame()
                        same_in_pdf = len(parsed_16[parsed_16["Worker_Name"].apply(normalize_name_key) == wn_up])

                        if len(wm_hits) == 1 and same_in_pdf == 1:
                            resolved_f16_wids[idx16] = str(wm_hits.iloc[0]["Worker_ID"])
                        elif len(wm_hits) > 1 and fn_up:
                            f_hits = wm_hits[wm_hits["Father_Husband_Name"].apply(normalize_name_key) == fn_up]
                            if len(f_hits) == 1:
                                resolved_f16_wids[idx16] = str(f_hits.iloc[0]["Worker_ID"])
                            else:
                                ambiguous_f16_rows.append((idx16, r16, wm_hits))
                        elif len(wm_hits) > 1 or same_in_pdf > 1:
                            ambiguous_f16_rows.append((idx16, r16, wm_hits if not wm_hits.empty else full_w_all))
                        else:
                            resolved_f16_wids[idx16] = ""

                    if ambiguous_f16_rows:
                        st.warning(f"⚠️ **Duplicate Name Resolver ({len(ambiguous_f16_rows)} Ambiguous Records):** Select the exact Worker ID for each same-name entry below before syncing:")
                        for idx16, r16, cand_df in ambiguous_f16_rows:
                            opt_map = {"-- Skip / Unmapped --": ""}
                            for _, cw in cand_df.iterrows():
                                f_lbl = f" | S/o W/o: {cw['Father_Husband_Name']}" if str(cw.get("Father_Husband_Name", "")).strip() else ""
                                opt_map[f"{cw['Worker_ID']} — {cw['Full_Name']}{f_lbl} ({cw['Designation']})"] = str(cw["Worker_ID"])
                            chosen_opt = st.selectbox(
                                f"PDF Row: **{r16['Worker_Name']}** | Father/Husband in PDF: **{r16.get('Father_Husband_Name') or 'N/A'}** | Punch Days: **{r16['Punch_Days_Count']:.0f}**",
                                list(opt_map.keys()),
                                key=f"f16_dup_res_{idx16}"
                            )
                            resolved_f16_wids[idx16] = opt_map[chosen_opt]

                    st.dataframe(parsed_16[["Worker_Name", "Father_Husband_Name", "Punch_Days_Count"]].head(10), use_container_width=True, hide_index=True)

                    if st.button("🔄 Sync Form XVI & Auto-Correct Muster Roll", type="primary", use_container_width=True):
                        full_m_all = load_fn("muster_roll")
                        if not full_m_all.empty and "Duty_Location" in full_m_all.columns:
                            full_m_all["Duty_Location"] = full_m_all["Duty_Location"].apply(normalize_duty_type)

                        f16_rows_to_save = []
                        auto_corrected_cnt = 0

                        for idx16, r16 in parsed_16.iterrows():
                            wn_up = normalize_name_key(r16["Worker_Name"])
                            fn_up = normalize_name_key(r16.get("Father_Husband_Name", ""))
                            p_dates = [d.strip() for d in str(r16["Punch_Dates_List"]).split(",") if d.strip()]
                            matched_wid = resolved_f16_wids.get(idx16, "")

                            f16_rows_to_save.append({
                                "Wages_Period": target_ym,
                                "Worker_ID": matched_wid,
                                "Worker_Name": wn_up,
                                "Father_Husband_Name": fn_up,
                                "Punch_Days_Count": float(r16["Punch_Days_Count"]),
                                "Punch_Dates_List": ",".join(p_dates),
                                "Uploaded_At": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            })

                            if matched_wid and p_dates and not full_m_all.empty:
                                mask_fix = (
                                    (full_m_all["Worker_ID"].astype(str) == matched_wid) &
                                    (full_m_all["Date"].astype(str).isin(p_dates)) &
                                    (full_m_all["Duty_Location"] == "Non-Punch")
                                )
                                fix_n = int(mask_fix.sum())
                                if fix_n > 0:
                                    full_m_all.loc[mask_fix, "Duty_Location"] = "Punch"
                                    full_m_all.loc[mask_fix, "Remarks"] = "Auto-Synced to Punch via Form XVI"
                                    auto_corrected_cnt += fix_n

                        if auto_corrected_cnt > 0:
                            save_fn("muster_roll", full_m_all[MUSTER_ROLL_COLS])

                        other_f16 = f16_df[~f16_df["Wages_Period"].apply(period_matches)].copy() if (not f16_df.empty and "Wages_Period" in f16_df.columns) else pd.DataFrame(columns=TATA_FORM_XVI_COLS)
                        final_f16 = pd.concat([other_f16, pd.DataFrame(f16_rows_to_save)], ignore_index=True)

                        if save_fn("tata_form_xvi", final_f16[TATA_FORM_XVI_COLS]):
                            st.success(f"Form XVI synchronized for {wage_period_str}! Auto-corrected {auto_corrected_cnt} 'Non-Punch' entries to 'Punch'.")
                            st.rerun()

        if is_f16_uploaded_for_month:
            with st.expander("🚨 Form XVI Biometric Audit & Ghost Punch Discrepancy Report", expanded=False):
                audit_rows = []
                m_ym = muster_df[muster_df["Date"].astype(str).str.startswith(target_ym)].copy() if (not muster_df.empty and "Date" in muster_df.columns) else pd.DataFrame()
                if not m_ym.empty:
                    m_ym["Duty_Location"] = m_ym["Duty_Location"].apply(normalize_duty_type)

                for _, wr in w_master.iterrows():
                    wid = str(wr["Worker_ID"])
                    wn = normalize_name_key(wr["Full_Name"])
                    f16_hit = f16_month_df[f16_month_df["Worker_ID"].astype(str).str.strip() == wid]
                    if f16_hit.empty:
                        same_cnt = len(w_master[w_master["Full_Name"].apply(normalize_name_key) == wn])
                        if same_cnt <= 1:
                            f16_hit = f16_month_df[f16_month_df["Worker_Name"].apply(normalize_name_key) == wn]

                    f16_dates = set()
                    f16_tot_days = 0.0
                    if not f16_hit.empty:
                        f16_tot_days = float(pd.to_numeric(f16_hit["Punch_Days_Count"], errors="coerce").fillna(0.0).sum())
                        for dl in f16_hit["Punch_Dates_List"].astype(str):
                            for d_item in dl.split(","):
                                if d_item.strip():
                                    f16_dates.add(d_item.strip())

                    sup_punch_dates = set()
                    sup_punch_days = 0.0
                    if not m_ym.empty:
                        wm_p = m_ym[(m_ym["Worker_ID"].astype(str) == wid) & (m_ym["Duty_Location"] == "Punch") & (m_ym["Attendance"].isin(["P", "HD"]))]
                        sup_punch_dates = set(wm_p["Date"].astype(str).tolist())
                        sup_punch_days = len(wm_p[wm_p["Attendance"] == "P"]) + (len(wm_p[wm_p["Attendance"] == "HD"]) * 0.5)

                    if f16_tot_days == 0 and sup_punch_days == 0:
                        continue

                    ghost_dates = sorted(list(sup_punch_dates - f16_dates)) if f16_dates else []
                    missing_field_dates = sorted(list(f16_dates - sup_punch_dates)) if f16_dates else []

                    if f16_tot_days == sup_punch_days and not ghost_dates:
                        aud_status = "🟢 Verified Match"
                    elif sup_punch_days > f16_tot_days:
                        aud_status = f"🔴 Ghost Punch Alert (+{sup_punch_days - f16_tot_days:.1f} d unverified)"
                    else:
                        aud_status = f"🟡 Missing in Field Log ({f16_tot_days - sup_punch_days:+.1f} d)"

                    audit_rows.append({
                        "Worker ID": wid,
                        "Employee Name": wn,
                        "Form XVI Punch": f16_tot_days,
                        "Field Punch": sup_punch_days,
                        "Audit Status": aud_status,
                        "Ghost Punch Dates (Absent in Form XVI)": ", ".join([d[-2:] for d in ghost_dates]) if ghost_dates else "—",
                        "Missing Field Dates (Present in Form XVI)": ", ".join([d[-2:] for d in missing_field_dates]) if missing_field_dates else "—"
                    })

                if audit_rows:
                    st.dataframe(pd.DataFrame(audit_rows), use_container_width=True, hide_index=True)
                else:
                    st.info("No biometric punch discrepancies detected for this period.")

        if not is_f16_uploaded_for_month:
            st.warning(f"⚠️ **Action Required:** Please upload and sync **Tata Steel Form XVI (Biometric Muster Roll)** for **{wage_period_str}** in Step 1 above before importing Form XVII.")
        else:
            with st.expander("📥 Step 2: Import Tata Steel FORM XVII (Register of Wages)", expanded=tata_df.empty):
                xvii_file = st.file_uploader("Upload Form XVII (PDF / CSV / XLSX)", type=["pdf", "csv", "xlsx"], key="f17_uploader")
                if xvii_file:
                    x_df = pd.DataFrame()
                    if xvii_file.name.lower().endswith(".pdf"):
                        if PYPDF_AVAILABLE:
                            x_df = parse_tata_form_xvii_pdf(xvii_file)
                    elif xvii_file.name.lower().endswith(".csv"):
                        x_df = pd.read_csv(xvii_file, dtype=str)
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
                        x_df = xl.parse(target_sheet, dtype=str)
                        x_df.columns = [str(c).strip().replace(" ", "_") for c in x_df.columns]

                    if not x_df.empty and ("Net_Amount_Paid" in x_df.columns or "Net_Amount" in x_df.columns):
                        net_col = "Net_Amount_Paid" if "Net_Amount_Paid" in x_df.columns else "Net_Amount"
                        name_col_guess = "Worker_Name" if "Worker_Name" in x_df.columns else ("Name_of_Workman" if "Name_of_Workman" in x_df.columns else ("Name" if "Name" in x_df.columns else None))
                        if name_col_guess:
                            x_df = x_df[~x_df[name_col_guess].astype(str).str.upper().str.contains("TOTAL", na=False)].reset_index(drop=True)

                        net_sum = pd.to_numeric(x_df[net_col], errors="coerce").fillna(0).sum()
                        st.success(f"Extracted {len(x_df)} records • Net Payable: ₹{net_sum:,.0f}")

                        full_w_all = load_fn("worker_master")
                        resolved_f17_wids = {}
                        ambiguous_f17_rows = []

                        for idx17, xr in x_df.iterrows():
                            w_name_raw = normalize_name_key(xr.get("Worker_Name") or xr.get("Name_of_Workman") or xr.get("Name") or "")
                            if not w_name_raw:
                                continue
                            appr_d = float(pd.to_numeric(xr.get("Approved_Days") or xr.get("No_of_days_worked") or 0.0, errors="coerce") or 0.0)
                            wm_hits = full_w_all[full_w_all["Full_Name"].apply(normalize_name_key) == w_name_raw] if (not full_w_all.empty and "Full_Name" in full_w_all.columns) else pd.DataFrame()
                            same_in_17 = len(x_df[x_df[name_col_guess].apply(normalize_name_key) == w_name_raw]) if name_col_guess else 1

                            if len(wm_hits) == 1 and same_in_17 == 1:
                                resolved_f17_wids[idx17] = str(wm_hits.iloc[0]["Worker_ID"])
                            elif len(wm_hits) > 1 or same_in_17 > 1:
                                f16_same = f16_month_df[
                                    (f16_month_df["Worker_Name"].apply(normalize_name_key) == w_name_raw) &
                                    (pd.to_numeric(f16_month_df["Punch_Days_Count"], errors="coerce") == appr_d) &
                                    (f16_month_df["Worker_ID"].astype(str).str.strip() != "")
                                ]
                                default_guess_wid = str(f16_same.iloc[0]["Worker_ID"]) if len(f16_same) == 1 else ""
                                ambiguous_f17_rows.append((idx17, xr, wm_hits if not wm_hits.empty else full_w_all, default_guess_wid))
                            else:
                                resolved_f17_wids[idx17] = ""

                        if ambiguous_f17_rows:
                            st.warning(f"⚠️ **Duplicate Name Resolver ({len(ambiguous_f17_rows)} Same-Name Entries):** Confirm exact Worker ID for each entry below:")
                            for idx17, xr, cand_df, def_wid in ambiguous_f17_rows:
                                opt_map = {"-- Unmapped --": ""}
                                def_idx = 0
                                for _, cw in cand_df.iterrows():
                                    f_lbl = f" | S/o W/o: {cw['Father_Husband_Name']}" if str(cw.get("Father_Husband_Name", "")).strip() else ""
                                    ac_lbl = f" | A/C: ...{str(cw.get('Bank_Account_No', ''))[-4:]}" if str(cw.get("Bank_Account_No", "")).strip() else ""
                                    lbl_str = f"{cw['Worker_ID']} — {cw['Full_Name']}{f_lbl}{ac_lbl}"
                                    opt_map[lbl_str] = str(cw["Worker_ID"])
                                    if def_wid and str(cw["Worker_ID"]) == def_wid:
                                        def_idx = len(opt_map) - 1

                                reg_sl = clean_excel_cell(xr.get("Register_Sl_No", ""))
                                d_val = float(pd.to_numeric(xr.get("Approved_Days", 0), errors="coerce") or 0)
                                n_val = float(pd.to_numeric(xr.get(net_col, 0), errors="coerce") or 0)
                                chosen_17 = st.selectbox(
                                    f"Form XVII Row: **{xr.get(name_col_guess)}** | Reg. Sl. No: **`{reg_sl or 'N/A'}`** | Days: **{d_val:.0f}** | Net Pay: **₹{n_val:,.0f}**",
                                    list(opt_map.keys()),
                                    index=def_idx,
                                    key=f"f17_dup_res_{idx17}"
                                )
                                resolved_f17_wids[idx17] = opt_map[chosen_17]

                        st.dataframe(x_df.head(8), use_container_width=True, hide_index=True)

                        import_mode = st.radio("Import Mode", ["Replace Month Data", "Append / Merge with Existing Month"], horizontal=True)
                        if st.button("Commit Form XVII Compliance Data", type="primary", use_container_width=True):
                            new_records = []
                            for idx17, xr in x_df.iterrows():
                                w_name_raw = normalize_name_key(xr.get("Worker_Name") or xr.get("Name_of_Workman") or xr.get("Name") or "")
                                if not w_name_raw or "TOTAL" in w_name_raw:
                                    continue
                                appr_days = float(pd.to_numeric(xr.get("Approved_Days") or xr.get("No_of_days_worked") or 0.0, errors="coerce") or 0.0)
                                net_paid = float(pd.to_numeric(xr.get("Net_Amount_Paid") or xr.get("Net_Amount") or 0.0, errors="coerce") or 0.0)
                                if net_paid <= 0:
                                    continue
                                matched_wid = resolved_f17_wids.get(idx17, "")
                                reg_sl_v = clean_excel_cell(xr.get("Register_Sl_No", ""))
                                new_records.append({
                                    "Wages_Period": target_ym, "Worker_ID": matched_wid,
                                    "Worker_Name": w_name_raw, "Register_Sl_No": reg_sl_v,
                                    "Approved_Days": appr_days, "Net_Amount_Paid": net_paid,
                                    "Uploaded_At": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                })

                            new_df = pd.DataFrame(new_records)
                            if import_mode.startswith("Replace"):
                                if not tata_df.empty and "Wages_Period" in tata_df.columns:
                                    tata_df = tata_df[~tata_df["Wages_Period"].apply(period_matches)]
                                final_tata = pd.concat([tata_df, new_df], ignore_index=True)
                            else:
                                existing_m = tata_df[tata_df["Wages_Period"].apply(period_matches)].copy() if not tata_df.empty and "Wages_Period" in tata_df.columns else pd.DataFrame(columns=TATA_COMPLIANCE_COLS)
                                other_m = tata_df[~tata_df["Wages_Period"].apply(period_matches)].copy() if not tata_df.empty and "Wages_Period" in tata_df.columns else tata_df.copy()
                                combined_m = pd.concat([existing_m, new_df], ignore_index=True)
                                combined_m["Approved_Days"] = pd.to_numeric(combined_m["Approved_Days"], errors="coerce").fillna(0.0)
                                combined_m["Net_Amount_Paid"] = pd.to_numeric(combined_m["Net_Amount_Paid"], errors="coerce").fillna(0.0)
                                combined_m["_grp_key"] = combined_m.apply(lambda r: str(r["Worker_ID"]).strip() if str(r["Worker_ID"]).strip() else str(r["Worker_Name"]).strip(), axis=1)
                                merged_month_df = combined_m.groupby("_grp_key", as_index=False).agg({
                                    "Wages_Period": "first", "Worker_ID": "first", "Worker_Name": "first",
                                    "Register_Sl_No": "first", "Approved_Days": "sum", "Net_Amount_Paid": "sum", "Uploaded_At": "last"
                                })
                                final_tata = pd.concat([other_m, merged_month_df[TATA_COMPLIANCE_COLS]], ignore_index=True)

                            if save_fn("tata_form_xvii", final_tata[TATA_COMPLIANCE_COLS]):
                                st.success(f"Committed {len(new_records)} records!")
                                st.rerun()

        payroll_results = compute_monthly_payroll_and_advances(w_master, muster_df, adv_df, tata_df, sel_year, month_num, stat_cfg)
        recon_rows = []
        f17_boi_batch, f17_neft_batch = [], []
        np_boi_batch, np_neft_batch = [], []
        f17_missing_bank_list = []
        np_missing_bank_list = []
        salary_slip_records = []

        for wid, p in payroll_results.items():
            if p["Status"] != "ACTIVE" and p["Total_Days"] == 0 and p["Form_XVII_Net"] == 0 and p["Closing_Carry_Forward"] == 0:
                continue
            punch_days, f17_days, f17_net = p["Punch_Days"], p["Form_XVII_Days"], p["Form_XVII_Net"]
            np_net = p["Non_Punch_Net_Pay"]

            if p["Is_F17_Higher"]:
                status_badge = "🔵 Form XVII Higher (Update Master Rate)"
            elif f17_days == punch_days and f17_days > 0:
                status_badge = "🟢 Matched"
            elif f17_days == 0 and punch_days > 0:
                status_badge = "🔴 No Punch (Field Only)"
            else:
                status_badge = f"🟡 Variance ({punch_days - f17_days:+.1f} d)"

            if p["Total_Days"] > 0 or f17_net > 0 or p["Total_Advance_Due"] > 0:
                salary_slip_records.append(p)

            recon_rows.append({
                "Worker ID": wid, "Name": p["Worker_Name"], "Father/Husband": p.get("Father_Husband_Name", ""),
                "Punch": punch_days, "Non-Punch": p["Non_Punch_Days"],
                "Leave (L)": p["Paid_Leave_Days"], "Form XVII": f17_days, "Status": status_badge,
                "Form XVII Bank (₹)": f17_net, "Balance Top-Up / Non-Punch Net (₹)": np_net,
                "Adv. Ded. (₹)": p["Advance_Deducted"], "Carry Fwd Adv (₹)": p["Closing_Carry_Forward"]
            })

            has_valid_bank = is_valid_bank_for_transfer(p["Bank_Account_No"], p["Bank_IFSC_Code"])

            if f17_net > 0:
                if has_valid_bank:
                    item_17 = {
                        "NAME": p["Worker_Name"], "AMOUNT": f17_net, "BENEFICIARY ACCOUNT NO": p["Bank_Account_No"],
                        "IFSC CODE": p["Bank_IFSC_Code"], "BANK & BRANCH DETAILS": p["Bank_Branch_Details"]
                    }
                    if p["Bank_IFSC_Code"].startswith("BKID"):
                        f17_boi_batch.append(item_17)
                    else:
                        f17_neft_batch.append(item_17)
                else:
                    missing_reason = []
                    if not clean_excel_cell(p["Bank_Account_No"]):
                        missing_reason.append("Missing Account No")
                    elif not re.fullmatch(r"\d{9,18}", clean_excel_cell(p["Bank_Account_No"])):
                        missing_reason.append("Invalid Account No")
                    if not clean_excel_cell(p["Bank_IFSC_Code"]):
                        missing_reason.append("Missing IFSC")
                    elif not re.fullmatch(r"^[A-Z]{4}0[A-Z0-9]{6}$", clean_excel_cell(p["Bank_IFSC_Code"]).upper()):
                        missing_reason.append("Invalid IFSC")

                    f17_missing_bank_list.append({
                        "Worker_ID": wid,
                        "Full_Name": p["Worker_Name"],
                        "Father_Husband_Name": p.get("Father_Husband_Name", ""),
                        "Form_XVII_Net": f17_net,
                        "Issue": ", ".join(missing_reason),
                        "Bank_Account_No": clean_excel_cell(p["Bank_Account_No"]),
                        "Bank_IFSC_Code": clean_excel_cell(p["Bank_IFSC_Code"]),
                        "Bank_Branch_Details": clean_excel_cell(p["Bank_Branch_Details"])
                    })

            if np_net > 0:
                if has_valid_bank:
                    item_np = {
                        "NAME": p["Worker_Name"], "AMOUNT": np_net, "BENEFICIARY ACCOUNT NO": p["Bank_Account_No"],
                        "IFSC CODE": p["Bank_IFSC_Code"], "BANK & BRANCH DETAILS": p["Bank_Branch_Details"]
                    }
                    if p["Bank_IFSC_Code"].startswith("BKID"):
                        np_boi_batch.append(item_np)
                    else:
                        np_neft_batch.append(item_np)
                else:
                    np_missing_bank_list.append({
                        "Worker ID": wid,
                        "Name": p["Worker_Name"],
                        "Non-Punch Net (₹)": np_net,
                        "Bank A/C": clean_excel_cell(p["Bank_Account_No"]) or "Missing",
                        "IFSC": clean_excel_cell(p["Bank_IFSC_Code"]) or "Missing"
                    })

        if recon_rows:
            st.dataframe(pd.DataFrame(recon_rows), use_container_width=True, hide_index=True)

        with st.container(border=True):
            st.markdown(f"###### 🧾 Worker Salary Slips (3 Slips per A4 Page • PF {epf_cap_disp} Cap @ {epf_pct_disp} & ESIC {esic_cap_disp} Cap @ {esic_pct_disp} on Punch Only)")
            if salary_slip_records and FPDF_AVAILABLE:
                slip_options = {f"{r['Worker_ID']} — {r['Worker_Name']} (Net: ₹{r['Net_Payable']:,.0f})": r["Worker_ID"] for r in salary_slip_records}
                sc_1, sc_2 = st.columns([2.1, 1.1])
                with sc_1:
                    sel_slip_labels = st.multiselect("Select Employees for Slips", options=list(slip_options.keys()), placeholder="Leave empty for all...", label_visibility="collapsed")
                with sc_2:
                    chosen_slip_wids = [slip_options[l] for l in sel_slip_labels] if sel_slip_labels else list(slip_options.values())
                    filtered_slips = [r for r in salary_slip_records if r["Worker_ID"] in chosen_slip_wids]
                    st.download_button(
                        label=f"📥 Download Salary Slips PDF ({len(filtered_slips)} Slips)",
                        data=generate_compact_salary_slips_pdf(filtered_slips, wage_period_str, logo_fn, stat_cfg),
                        file_name=f"M_s_INDU_SAO_Salary_Slips_{wage_period_str}.pdf", mime="application/pdf", type="primary", use_container_width=True
                    )

        st.markdown("###### 🏦 Bank Covering Letters — Set 1: Tata Steel Form XVII Compliance (Punch Payouts Only)")

        # STRICT COMPLIANCE GUARDRAIL FOR MISSING BANK DATA IN FORM XVII
        if f17_missing_bank_list:
            tot_missing_17 = sum([x["Form_XVII_Net"] for x in f17_missing_bank_list])
            st.error(
                f"🚨 **MANDATORY COMPLIANCE ALERT:** **{len(f17_missing_bank_list)} Form XVII workers** (Total: **₹{tot_missing_17:,.0f}**) have missing or invalid Bank Account / IFSC details and are currently **excluded from the Bank Covering Letters**!"
            )
            with st.container(border=True):
                st.markdown("###### ⚡ Quick-Fix Missing Form XVII Bank Details (Update Directly Here)")
                st.caption("Fill in Bank Account No, 11-character IFSC Code, and Branch below and click Save — these workers will immediately appear in the BOI/NEFT Bank Letters.")
                miss_df = pd.DataFrame(f17_missing_bank_list)
                edited_miss_df = st.data_editor(
                    miss_df,
                    use_container_width=True,
                    hide_index=True,
                    disabled=["Worker_ID", "Full_Name", "Father_Husband_Name", "Form_XVII_Net", "Issue"],
                    key=f"quick_bank_fix_{target_ym}"
                )
                if st.button("💾 Save Bank Details & Refresh Bank Letters", type="primary", key=f"btn_save_bank_fix_{target_ym}"):
                    fix_errors = []
                    updates_to_apply = {}
                    for _, mr in edited_miss_df.iterrows():
                        wid_m = str(mr["Worker_ID"]).strip()
                        acc_m = clean_excel_cell(mr["Bank_Account_No"])
                        ifsc_m = clean_excel_cell(mr["Bank_IFSC_Code"]).upper()
                        br_m = clean_excel_cell(mr["Bank_Branch_Details"]).upper()
                        if acc_m or ifsc_m:
                            c_res, c_errs = validate_and_clean_employee_fields("", "", acc_m, ifsc_m)
                            if c_errs:
                                fix_errors.append(f"{mr['Full_Name']} ({wid_m}): " + "; ".join(c_errs))
                            else:
                                updates_to_apply[wid_m] = (c_res["Bank_Account_No"], c_res["Bank_IFSC_Code"], br_m)

                    if fix_errors:
                        for fe in fix_errors:
                            st.warning(f"❌ {fe}")
                    elif updates_to_apply:
                        latest_wm = load_fn("worker_master")
                        for wid_u, (u_acc, u_ifsc, u_br) in updates_to_apply.items():
                            mask_u = latest_wm["Worker_ID"].astype(str) == wid_u
                            latest_wm.loc[mask_u, ["Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details"]] = [u_acc, u_ifsc, u_br]
                        if save_fn("worker_master", latest_wm[WORKER_MASTER_COLS]):
                            st.success(f"Updated bank details for {len(updates_to_apply)} workers!")
                            st.rerun()

        p_c1, p_c2 = st.columns(2)
        with p_c1:
            chq_no_17 = st.text_input("Cheque Number (Form XVII Compliance)", value="238883", key="bank_chq_no_17").strip()
        with p_c2:
            chq_date_17 = st.text_input("Cheque Date (DD.MM.YYYY)", value=datetime.date.today().strftime("%d.%m.%Y"), key="bank_chq_dt_17").strip()

        tot_f17_boi = sum([r["AMOUNT"] for r in f17_boi_batch])
        tot_f17_neft = sum([r["AMOUNT"] for r in f17_neft_batch])

        dl_c1, dl_c2 = st.columns(2)
        with dl_c1:
            if f17_boi_batch and FPDF_AVAILABLE:
                st.download_button(
                    label=f"📥 Download Form XVII BOI Letter ({len(f17_boi_batch)} Workers • ₹{tot_f17_boi:,.0f})",
                    data=generate_bank_authority_letter_pdf("BOI", f17_boi_batch, chq_no_17, chq_date_17, letterhead_fn, f"SUB: Transfer of Form XVII Wages ({wage_period_str}) to below A/C"),
                    file_name=f"FormXVII_BOI_Transfer_Letter_{wage_period_str}.pdf", mime="application/pdf", type="primary", use_container_width=True
                )
        with dl_c2:
            if f17_neft_batch and FPDF_AVAILABLE:
                st.download_button(
                    label=f"📥 Download Form XVII NEFT Letter ({len(f17_neft_batch)} Workers • ₹{tot_f17_neft:,.0f})",
                    data=generate_bank_authority_letter_pdf("NEFT", f17_neft_batch, chq_no_17, chq_date_17, letterhead_fn, f"SUB: NEFT Transfer of Form XVII Wages ({wage_period_str}) to below A/C"),
                    file_name=f"FormXVII_NEFT_Transfer_Letter_{wage_period_str}.pdf", mime="application/pdf", type="primary", use_container_width=True
                )

        if np_boi_batch or np_neft_batch or np_missing_bank_list:
            st.markdown("---")
            st.markdown("###### 🏦 Bank Covering Letters — Set 2: Internal Non-Punch, Fixed & Differential Top-Up Payouts")
            if np_missing_bank_list:
                st.warning(f"⚠️ **{len(np_missing_bank_list)} Non-Punch / Top-Up workers** have no valid Bank A/C linked and will be paid via **Cash**:")
                st.dataframe(pd.DataFrame(np_missing_bank_list), use_container_width=True, hide_index=True)

            np_c1, np_c2 = st.columns(2)
            with np_c1:
                chq_no_np = st.text_input("Cheque Number (Internal Balance Payout)", value="238884", key="bank_chq_no_np").strip()
            with np_c2:
                chq_date_np = st.text_input("Cheque Date (DD.MM.YYYY)", value=datetime.date.today().strftime("%d.%m.%Y"), key="bank_chq_dt_np").strip()

            tot_np_boi = sum([r["AMOUNT"] for r in np_boi_batch])
            tot_np_neft = sum([r["AMOUNT"] for r in np_neft_batch])

            ndl_c1, ndl_c2 = st.columns(2)
            with ndl_c1:
                if np_boi_batch and FPDF_AVAILABLE:
                    st.download_button(
                        label=f"📥 Download Non-Punch / Top-Up BOI Letter ({len(np_boi_batch)} Workers • ₹{tot_np_boi:,.0f})",
                        data=generate_bank_authority_letter_pdf("BOI", np_boi_batch, chq_no_np, chq_date_np, letterhead_fn, f"SUB: Transfer of Balance / Non-Punch Wages ({wage_period_str}) to below A/C"),
                        file_name=f"NonPunch_TopUp_BOI_Letter_{wage_period_str}.pdf", mime="application/pdf", type="secondary", use_container_width=True
                    )
            with ndl_c2:
                if np_neft_batch and FPDF_AVAILABLE:
                    st.download_button(
                        label=f"📥 Download Non-Punch / Top-Up NEFT Letter ({len(np_neft_batch)} Workers • ₹{tot_np_neft:,.0f})",
                        data=generate_bank_authority_letter_pdf("NEFT", np_neft_batch, chq_no_np, chq_date_np, letterhead_fn, f"SUB: NEFT Transfer of Balance / Non-Punch Wages ({wage_period_str}) to below A/C"),
                        file_name=f"NonPunch_TopUp_NEFT_Letter_{wage_period_str}.pdf", mime="application/pdf", type="secondary", use_container_width=True
                    )
