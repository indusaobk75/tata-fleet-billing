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
SKILL_CATEGORIES = ["Unskilled", "Semi-Skilled", "Skilled", "Highly Skilled"]
WEEK_DAYS_OPTIONS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "No Fixed Week-Off"]

WAGE_MASTER_COLS = [
    "Skill_Category", "Basic_Pay", "VDA_Allowance", "Total_Daily_Rate", "Effective_From", "Remarks"
]

DEFAULT_WAGE_RATES = [
    {"Skill_Category": "Unskilled", "Basic_Pay": 350.0, "VDA_Allowance": 215.0, "Total_Daily_Rate": 565.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"},
    {"Skill_Category": "Semi-Skilled", "Basic_Pay": 400.0, "VDA_Allowance": 250.0, "Total_Daily_Rate": 650.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"},
    {"Skill_Category": "Skilled", "Basic_Pay": 494.0, "VDA_Allowance": 300.0, "Total_Daily_Rate": 794.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"},
    {"Skill_Category": "Highly Skilled", "Basic_Pay": 565.0, "VDA_Allowance": 350.0, "Total_Daily_Rate": 915.0, "Effective_From": "2026-10-01", "Remarks": "Tata Steel West Bokaro / Central Rate"}
]

WORKER_MASTER_COLS = [
    "Worker_ID", "Full_Name", "Govt_ID_Ref", "Division", "Designation", "Wage_Type",
    "Skill_Category", "Base_Rate", "Week_Off_Day", "Contact_No", "Bank_Account_No",
    "Bank_IFSC_Code", "Bank_Branch_Details", "Status", "Created_Date", "Photo_Base64"
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

COMPANY_CURRENT_AC = "482025110000105"


# ----------------- HELPERS -----------------
def normalize_duty_type(val):
    s = str(val).strip()
    if s in ["Punch", "Tata Site (In-Plant)"]:
        return "Punch"
    return "Non-Punch"


def normalize_week_off(val):
    s = str(val).strip()
    if s in WEEK_DAYS_OPTIONS:
        return s
    return "Sunday"


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
    if "supervisor" in r or "excavator" in r or "crane" in r or "dozer" in r:
        return "Highly Skilled"
    if "driver" in r or "operator" in r or "mason" in r or "welder" in r or "electrician" in r or "mechanic" in r or "carpenter" in r or "plumber" in r or "tile" in r or "fitter" in r:
        return "Skilled"
    if "rigger" in r or "painter" in r or "gypsum" in r or "bender" in r:
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
                    name = " ".join(pre_days[:s_idx]) if s_idx != -1 else " ".join(pre_days)
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


# ----------------- PAYROLL & ADVANCE CARRY-FORWARD ENGINE -----------------
def compute_monthly_payroll_and_advances(w_master, muster_df, adv_df, tata_df, sel_year, month_num):
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
            if not adv_df.empty and "Date" in adv_df.columns and "Worker_ID" in adv_df.columns:
                wa = adv_df[(adv_df["Worker_ID"].astype(str) == wid) &
                            (adv_df["Date"].astype(str) >= p_start) &
                            (adv_df["Date"].astype(str) <= p_end)]
                new_adv = float(pd.to_numeric(wa["Amount"], errors="coerce").fillna(0.0).sum())

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
            if not tata_p_df.empty and "Worker_Name" in tata_p_df.columns:
                tm = tata_p_df[tata_p_df["Worker_Name"].astype(str).str.strip().str.upper() == wname.strip().upper()]
                if not tm.empty:
                    f17_days = float(pd.to_numeric(tm["Approved_Days"], errors="coerce").fillna(0.0).sum())
                    f17_net = float(pd.to_numeric(tm["Net_Amount_Paid"], errors="coerce").fillna(0.0).sum())

            if wwtype == "MONTHLY_FIXED":
                working_days_in_month = count_working_days_for_worker(y_i, m_i, w_wo)
                per_day_fixed = wrate / float(working_days_in_month)
                total_paid_days = min(float(working_days_in_month), punch_days + non_punch_days + paid_leave_days)
                gross_total = round(per_day_fixed * total_paid_days, 0)
                pf_ded = 0.0
                esic_ded = 0.0
                adjustable_pool = max(0.0, gross_total - f17_net)
                adv_deducted = min(total_adv_due, adjustable_pool)
                closing_adv = max(0.0, total_adv_due - adv_deducted)
                non_punch_take_home = max(0.0, adjustable_pool - adv_deducted)
                total_net_payable = f17_net + non_punch_take_home
            else:
                total_paid_days = punch_days + non_punch_days
                ot_pay = (wrate / 8.0) * ot_hrs
                if f17_net > 0:
                    np_gross = round((non_punch_days * wrate) + ot_pay, 0)
                    pf_ded = round(np_gross * 0.12, 0)
                    esic_ded = round(np_gross * 0.0075, 0)
                    np_net_before_adv = max(0.0, np_gross - pf_ded - esic_ded)
                    f17_approx_gross = round(f17_net / 0.8725, 0)
                    gross_total = f17_approx_gross + np_gross
                else:
                    gross_total = round((total_paid_days * wrate) + ot_pay, 0)
                    pf_ded = round(gross_total * 0.12, 0)
                    esic_ded = round(gross_total * 0.0075, 0)
                    np_net_before_adv = max(0.0, gross_total - pf_ded - esic_ded)

                adv_deducted = min(total_adv_due, np_net_before_adv)
                closing_adv = max(0.0, total_adv_due - adv_deducted)
                non_punch_take_home = max(0.0, np_net_before_adv - adv_deducted)
                total_net_payable = f17_net + non_punch_take_home

            carry_fwd_map[wid] = closing_adv

            if ym == target_ym:
                results_for_target[wid] = {
                    "Worker_ID": wid, "Worker_Name": wname, "Division": wdiv,
                    "Designation": wrole, "Skill_Category": wskill, "Wage_Type": wwtype,
                    "Base_Rate": wrate, "Week_Off_Day": w_wo,
                    "Status": str(w.get("Status", "ACTIVE")).upper(),
                    "Bank_Account_No": b_acc, "Bank_IFSC_Code": b_ifsc, "Bank_Branch_Details": b_branch,
                    "Punch_Days": punch_days, "Non_Punch_Days": non_punch_days,
                    "Paid_Leave_Days": paid_leave_days, "Total_Days": total_paid_days,
                    "OT_Hours": ot_hrs, "Form_XVII_Days": f17_days, "Form_XVII_Net": f17_net,
                    "Gross_Wages": gross_total, "PF_Deduction": pf_ded, "ESIC_Deduction": esic_ded,
                    "Opening_Advance": opening_adv, "Month_New_Advance": new_adv,
                    "Total_Advance_Due": total_adv_due, "Advance_Deducted": adv_deducted,
                    "Closing_Carry_Forward": closing_adv, "Non_Punch_Net_Pay": non_punch_take_home,
                    "Net_Payable": total_net_payable
                }
    return results_for_target


# ----------------- PDF GENERATORS (BANK LETTER, 8-UP ID CARDS, 3-UP SALARY SLIPS) -----------------
def generate_bank_authority_letter_pdf(bank_batch_type, records_list, cheque_no, cheque_date, letterhead_fn):
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
    pdf.cell(170, 5.5, "SUB: Transfer of payment from our account to below A/C", align="C", ln=True)

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


def generate_atm_id_cards_pdf(workers_df, logo_fn):
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

        photo_b64 = str(emp.get("Photo_Base64", "")).strip()
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


def generate_compact_salary_slips_pdf(slip_records, wage_period_label, logo_fn):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

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
        pdf.cell(94, 4.3, f"Name: {s['Worker_Name']} ({s['Worker_ID']})")
        pdf.cell(86, 4.3, f"{s['Designation']} [{s['Skill_Category']}]", align="R")

        pdf.set_font("Helvetica", "", 7.2)
        pdf.set_xy(x0 + 3.0, y0 + 19.6)
        b_disp = s["Bank_Account_No"] if s["Bank_Account_No"] else "Cash"
        i_disp = s["Bank_IFSC_Code"] if s["Bank_IFSC_Code"] else "N/A"
        pdf.cell(94, 4.0, f"Div: {s['Division']}  |  Rate: Rs.{s['Base_Rate']:.0f} ({s['Wage_Type']})")
        pdf.cell(86, 4.0, f"Bank A/C: {b_disp} ({i_disp})", align="R")

        t_y = y0 + 25.0
        pdf.set_fill_color(241, 245, 249)
        pdf.set_draw_color(100, 116, 139)
        pdf.set_line_width(0.25)
        pdf.set_font("Helvetica", "B", 7.0)

        w_cols = [22, 22, 28, 24, 24, 28, 38]
        h_cols = ["Total Days", "OT Hrs", "Gross Wages", "PF (12%)", "ESIC (0.75%)", "Adv. Ded.", "NET PAYABLE"]
        pdf.set_xy(x0, t_y)
        for wc, hc in zip(w_cols, h_cols):
            pdf.cell(wc, 6.2, hc, border=1, align="C", fill=True)

        pdf.set_xy(x0, t_y + 6.2)
        pdf.set_font("Helvetica", "", 7.6)
        pdf.cell(w_cols[0], 7.2, f"{s['Total_Days']:.1f} d", border=1, align="C")
        pdf.cell(w_cols[1], 7.2, f"{s['OT_Hours']:.1f} h", border=1, align="C")
        pdf.set_font("Helvetica", "B", 7.6)
        pdf.cell(w_cols[2], 7.2, f"Rs.{s['Gross_Wages']:,.0f}", border=1, align="R")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.cell(w_cols[3], 7.2, f"Rs.{s['PF_Deduction']:,.0f}", border=1, align="R")
        pdf.cell(w_cols[4], 7.2, f"Rs.{s['ESIC_Deduction']:,.0f}", border=1, align="R")
        pdf.cell(w_cols[5], 7.2, f"Rs.{s['Advance_Deducted']:,.0f}", border=1, align="R")
        pdf.set_fill_color(236, 253, 245)
        pdf.set_font("Helvetica", "B", 8.0)
        pdf.cell(w_cols[6], 7.2, f"Rs.{s['Net_Payable']:,.0f}/-", border=1, align="R", fill=True)

        pdf.set_font("Helvetica", "B", 6.8)
        pdf.set_text_color(30, 41, 59)
        pdf.set_xy(x0 + 3.0, y0 + 40.5)
        f17_str = f"Form XVII Bank Payout: Rs.{s['Form_XVII_Net']:,.0f} ({s['Form_XVII_Days']:.1f} d)" if s['Form_XVII_Net'] > 0 else "Form XVII Bank Payout: Rs.0"
        np_str = f"Balance Non-Punch/Fixed Net: Rs.{s['Non_Punch_Net_Pay']:,.0f}"
        cf_str = f"Closing Adv. Bal (Next Month): Rs.{s['Closing_Carry_Forward']:,.0f}"
        pdf.cell(180, 4.0, f"{f17_str}   |   {np_str}   |   {cf_str}")

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


# ----------------- MAIN RENDER ENTRYPOINT FOR ATTENDANCE MODULE -----------------
def render_attendance_module(current_view, load_fn, save_fn, current_user, user_display_name, current_role, letterhead_fn, logo_fn):
    is_admin = (str(current_role).upper() == "ADMIN")

    # 1. EMPLOYEE ENROLMENT
    if current_view == "➕ Employee Enrolment":
        st.markdown("#### Employee Enrolment")
        st.caption("Onboard workforce members with statutory skill category, fixed Week-Off day, photo, and bank particulars")

        wage_rates_df = get_active_wage_rates_df(load_fn)
        rate_lookup = dict(zip(wage_rates_df["Skill_Category"], wage_rates_df["Total_Daily_Rate"]))
        entry_mode = st.radio("Enrolment Method", ["Single Employee Form", "Bulk Excel/CSV Upload"], horizontal=True)

        if entry_mode == "Single Employee Form":
            c_form, c_info = st.columns([1.35, 0.65], gap="large")
            with c_form:
                with st.container(border=True):
                    w_df_current = load_fn("worker_master")
                    w_div = st.selectbox("Operational Division", ["Fleet Operations", "Civil Construction"], key="enrol_div")
                    next_wid = generate_next_worker_id(w_div, w_df_current)
                    st.caption(f"Assigned System ID: **{next_wid}**")

                    c_np1, c_np2 = st.columns([1.6, 1.0])
                    with c_np1:
                        w_name = st.text_input("Full Official Name", key="enrol_name").strip()
                        w_ref = st.text_input("Identity Verification Reference", key="enrol_ref").strip()
                    with c_np2:
                        w_photo_file = st.file_uploader("Passport Photo (Optional)", type=["jpg", "jpeg", "png"], key="enrol_photo")
                        if w_photo_file:
                            st.image(w_photo_file, width=85)

                    role_choices = FLEET_ROLES if w_div == "Fleet Operations" else CIVIL_ROLES
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
                            w_rate = st.number_input("Daily Wage Rate (₹)", min_value=100.0, value=auto_rate_val, step=10.0, key=f"enrol_rate_{w_skill_cat}")
                        else:
                            w_rate = st.number_input("Monthly Salary (₹)", min_value=1000.0, value=18000.0, step=500.0, key="enrol_rate_monthly")

                    c_wo1, c_wo2 = st.columns(2)
                    with c_wo1:
                        w_week_off = st.selectbox("Fixed Weekly Off Day", WEEK_DAYS_OPTIONS, index=0, key="enrol_week_off")
                    with c_wo2:
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
                            w_df = load_fn("worker_master")
                            new_row = {
                                "Worker_ID": next_wid, "Full_Name": w_name, "Govt_ID_Ref": w_ref,
                                "Division": w_div, "Designation": final_role, "Wage_Type": w_wage_type,
                                "Skill_Category": w_skill_cat, "Base_Rate": w_rate, "Week_Off_Day": w_week_off,
                                "Contact_No": w_contact, "Bank_Account_No": b_acc, "Bank_IFSC_Code": b_ifsc,
                                "Bank_Branch_Details": b_branch, "Status": w_status,
                                "Created_Date": datetime.date.today().strftime("%Y-%m-%d"),
                                "Photo_Base64": compress_photo_to_base64(w_photo_file)
                            }
                            updated_w_df = pd.concat([w_df, pd.DataFrame([new_row])], ignore_index=True)
                            if save_fn("worker_master", updated_w_df[WORKER_MASTER_COLS]):
                                st.success(f"Employee {w_name} ({next_wid}) enrolled (Week-Off: {w_week_off}).")
                                st.rerun()

            with c_info:
                with st.container(border=True):
                    st.markdown("###### Active Statutory Daily Rates")
                    for _, rr in wage_rates_df.iterrows():
                        st.caption(f"• **{rr['Skill_Category']}:** ₹{float(rr['Total_Daily_Rate']):,.0f}/day")
                    st.markdown("---")
                    st.caption("On an employee's assigned Week-Off day, daily attendance automatically locks to **WO**.")

        else:
            with st.container(border=True):
                up_file = st.file_uploader("Upload CSV / XLSX File", type=["csv", "xlsx"])
                if up_file:
                    b_df = pd.read_csv(up_file) if up_file.name.lower().endswith(".csv") else pd.ExcelFile(up_file).parse(0)
                    b_df.columns = [str(c).strip() for c in b_df.columns]
                    st.dataframe(b_df.head(5), use_container_width=True, hide_index=True)

                    if st.button("Commit Bulk Roster", type="primary", use_container_width=True):
                        w_df = load_fn("worker_master")
                        appended_rows = []
                        for _, b_row in b_df.iterrows():
                            div_val = str(b_row.get("Division", "Fleet Operations")).strip()
                            cur_merged = pd.concat([w_df, pd.DataFrame(appended_rows)], ignore_index=True)
                            new_gen_id = generate_next_worker_id(div_val, cur_merged)
                            raw_desig = str(b_row.get("Designation", "")).strip()
                            clean_desig = raw_desig if raw_desig and raw_desig.lower() != "nan" else "Unassigned"
                            w_type_val = str(b_row.get("Wage_Type", "DAILY_WAGE")).strip().upper()
                            b_rate_val = float(b_row.get("Base_Rate", 565.0) or 565.0)
                            raw_skill = str(b_row.get("Skill_Category", "")).strip()
                            if not raw_skill or raw_skill.lower() == "nan":
                                raw_skill = infer_skill_category_from_role_or_rate(clean_desig, b_rate_val, w_type_val)

                            appended_rows.append({
                                "Worker_ID": new_gen_id, "Full_Name": str(b_row.get("Full_Name", "")).strip(),
                                "Govt_ID_Ref": str(b_row.get("Govt_ID_Ref", "")).strip(), "Division": div_val,
                                "Designation": clean_desig, "Wage_Type": w_type_val, "Skill_Category": raw_skill,
                                "Base_Rate": b_rate_val, "Week_Off_Day": normalize_week_off(b_row.get("Week_Off_Day", "Sunday")),
                                "Contact_No": str(b_row.get("Contact_No", "")).strip(),
                                "Bank_Account_No": str(b_row.get("Bank_Account_No", "")).strip(),
                                "Bank_IFSC_Code": str(b_row.get("Bank_IFSC_Code", "")).strip().upper(),
                                "Bank_Branch_Details": str(b_row.get("Bank_Branch_Details", "")).strip().upper(),
                                "Status": str(b_row.get("Status", "ACTIVE")).strip().upper(),
                                "Created_Date": datetime.date.today().strftime("%Y-%m-%d"), "Photo_Base64": ""
                            })

                        fin_w_df = pd.concat([w_df, pd.DataFrame(appended_rows)], ignore_index=True)
                        if save_fn("worker_master", fin_w_df[WORKER_MASTER_COLS]):
                            st.success(f"{len(appended_rows)} records imported.")
                            st.rerun()

    # 2. STATUTORY WAGE RATES
    elif current_view == "⚖️ Statutory Wage Rates":
        st.markdown("#### Statutory Minimum Wage Rate Master (Category-Wise)")
        wage_rates_df = get_active_wage_rates_df(load_fn)
        w_master = load_fn("worker_master")

        c_kpi1, c_kpi2, c_kpi3, c_kpi4 = st.columns(4)
        kpi_cols = [c_kpi1, c_kpi2, c_kpi3, c_kpi4]
        for idx, cat in enumerate(SKILL_CATEGORIES):
            cnt = len(w_master[(w_master["Status"].astype(str).str.upper() == "ACTIVE") & (w_master["Skill_Category"].astype(str) == cat)]) if not w_master.empty and "Skill_Category" in w_master.columns else 0
            cur_r = float(wage_rates_df[wage_rates_df["Skill_Category"] == cat]["Total_Daily_Rate"].iloc[0])
            kpi_cols[idx].metric(f"{cat}", f"₹{cur_r:,.0f} / day", f"{cnt} Active Workers")

        with st.container(border=True):
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

                    updated_count = 0
                    if not w_master.empty and "Wage_Type" in w_master.columns:
                        for idx, wr in w_master.iterrows():
                            if str(wr.get("Wage_Type", "")).upper() == "DAILY_WAGE":
                                w_cat = str(wr.get("Skill_Category", "")).strip()
                                if not w_cat or w_cat not in new_rate_map:
                                    w_cat = infer_skill_category_from_role_or_rate(wr.get("Designation", ""), wr.get("Base_Rate", 0), "DAILY_WAGE")
                                    w_master.at[idx, "Skill_Category"] = w_cat
                                if w_cat in new_rate_map:
                                    w_master.at[idx, "Base_Rate"] = new_rate_map[w_cat]
                                    updated_count += 1
                        save_fn("worker_master", w_master[WORKER_MASTER_COLS])
                    st.success(f"Updated rates for **{updated_count} Daily Wage Workers**!")
                    st.rerun()

            with b_col2:
                if st.button("🔄 Auto-Assign Skill Categories & Defaults", type="secondary", use_container_width=True):
                    if not w_master.empty:
                        assigned = 0
                        for idx, wr in w_master.iterrows():
                            cur_cat = str(wr.get("Skill_Category", "")).strip()
                            if not cur_cat or cur_cat.lower() == "nan":
                                w_master.at[idx, "Skill_Category"] = infer_skill_category_from_role_or_rate(wr.get("Designation", ""), wr.get("Base_Rate", 0), wr.get("Wage_Type", "DAILY_WAGE"))
                                assigned += 1
                            if str(wr.get("Week_Off_Day", "")).strip() not in WEEK_DAYS_OPTIONS:
                                w_master.at[idx, "Week_Off_Day"] = "Sunday"
                        if save_fn("worker_master", w_master[WORKER_MASTER_COLS]):
                            st.success(f"Assigned {assigned} categories & default Week-Off days!")
                            st.rerun()

    # 3. EMPLOYEE MASTER REGISTER & OPEN PROFILE
    elif current_view == "📋 Employee Master Register":
        w_table = load_fn("worker_master")
        muster_all = load_fn("muster_roll")
        adv_all = load_fn("worker_advances")
        tata_all = load_fn("tata_form_xvii")

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
                return

            emp = w_match.iloc[0]
            emp_name = str(emp.get("Full_Name", ""))
            emp_ref = str(emp.get("Govt_ID_Ref", ""))
            emp_div = str(emp.get("Division", "Fleet Operations"))
            emp_role = str(emp.get("Designation", ""))
            emp_wage_type = str(emp.get("Wage_Type", "DAILY_WAGE"))
            emp_skill = str(emp.get("Skill_Category", "")).strip() or infer_skill_category_from_role_or_rate(emp_role, emp.get("Base_Rate", 0), emp_wage_type)
            emp_rate = float(pd.to_numeric(emp.get("Base_Rate", 0.0), errors="coerce") or 0.0)
            emp_wo = normalize_week_off(emp.get("Week_Off_Day", "Sunday"))
            emp_acc = str(emp.get("Bank_Account_No", ""))
            emp_ifsc = str(emp.get("Bank_IFSC_Code", ""))
            emp_branch = str(emp.get("Bank_Branch_Details", ""))
            emp_contact = str(emp.get("Contact_No", ""))
            emp_status = str(emp.get("Status", "ACTIVE")).upper()
            emp_photo_b64 = str(emp.get("Photo_Base64", "")).strip()

            with top_nav2:
                if FPDF_AVAILABLE:
                    st.download_button(
                        label=f"🪪 Download ATM ID Card PDF ({target_wid})",
                        data=generate_atm_id_cards_pdf(w_match, logo_fn),
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
                            w_table.loc[w_table["Worker_ID"].astype(str) == target_wid, "Photo_Base64"] = b64_new
                            if save_fn("worker_master", w_table[WORKER_MASTER_COLS]):
                                st.success("Photo updated!")
                                st.rerun()

                with p_c1:
                    st.markdown(f"### {emp_name} (`{target_wid}`)")
                    st.markdown(f"**Division:** {emp_div}  |  **Role:** {emp_role}  |  **Fixed Week-Off:** `{emp_wo}`")
                    st.markdown(f"**Category:** `{emp_skill}` ({emp_wage_type})  |  **Rate:** **₹{emp_rate:,.0f}**  |  **Status:** `{emp_status}`")

                with p_c2:
                    st.markdown("###### 🏦 Bank Account Mapping")
                    st.markdown(f"**A/C No:** `{emp_acc or 'Not Linked'}`")
                    st.markdown(f"**IFSC:** `{emp_ifsc or 'N/A'}` ({emp_branch or 'Cash Disbursement'})")

            with st.expander("✏️ Edit Employee Details or Delete Record", expanded=False):
                with st.form(f"edit_full_emp_form_{target_wid}"):
                    ed_c1, ed_c2, ed_c3 = st.columns(3)
                    with ed_c1:
                        ed_name = st.text_input("Full Official Name", value=emp_name).strip()
                        ed_ref = st.text_input("Identity Verification Ref", value=emp_ref).strip()
                        ed_contact = st.text_input("Contact Mobile No", value=emp_contact).strip()
                    with ed_c2:
                        div_opts = ["Fleet Operations", "Civil Construction"]
                        ed_div = st.selectbox("Division", div_opts, index=div_opts.index(emp_div) if emp_div in div_opts else 0)
                        ed_role = st.text_input("Designation / Role", value=emp_role).strip()
                        ed_wo = st.selectbox("Fixed Weekly Off Day", WEEK_DAYS_OPTIONS, index=WEEK_DAYS_OPTIONS.index(emp_wo))
                    with ed_c3:
                        wt_opts = ["DAILY_WAGE", "MONTHLY_FIXED"]
                        ed_wtype = st.selectbox("Wage Model", wt_opts, index=wt_opts.index(emp_wage_type) if emp_wage_type in wt_opts else 0)
                        sk_opts = SKILL_CATEGORIES + ["Monthly Fixed"]
                        ed_skill = st.selectbox("Skill Category", sk_opts, index=sk_opts.index(emp_skill) if emp_skill in sk_opts else 0)
                        ed_rate = st.number_input("Base Rate (₹)", min_value=0.0, value=emp_rate, step=10.0)

                    eb_c1, eb_c2, eb_c3, eb_c4 = st.columns([1.2, 1.0, 1.2, 0.9])
                    with eb_c1:
                        ed_acc = st.text_input("Bank Account No", value=emp_acc).strip()
                    with eb_c2:
                        ed_ifsc = st.text_input("IFSC Code", value=emp_ifsc).strip().upper()
                    with eb_c3:
                        ed_branch = st.text_input("Bank & Branch Details", value=emp_branch).strip().upper()
                    with eb_c4:
                        stat_opts = ["ACTIVE", "INACTIVE"]
                        ed_status = st.selectbox("Status", stat_opts, index=stat_opts.index(emp_status) if emp_status in stat_opts else 0)

                    if st.form_submit_button("💾 Save Updated Employee Profile", type="primary", use_container_width=True):
                        if ed_name:
                            mask_w = w_table["Worker_ID"].astype(str) == target_wid
                            w_table.loc[mask_w, ["Full_Name", "Govt_ID_Ref", "Contact_No", "Division", "Designation", "Week_Off_Day", "Status", "Wage_Type", "Skill_Category", "Base_Rate", "Bank_Account_No", "Bank_IFSC_Code", "Bank_Branch_Details"]] = [
                                ed_name, ed_ref, ed_contact, ed_div, ed_role, ed_wo, ed_status, ed_wtype, ed_skill, ed_rate, ed_acc, ed_ifsc, ed_branch
                            ]
                            if save_fn("worker_master", w_table[WORKER_MASTER_COLS]):
                                st.success(f"Profile updated!")
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
                                save_fn("worker_master", w_table[w_table["Worker_ID"].astype(str) != target_wid][WORKER_MASTER_COLS])
                                if also_del_logs:
                                    if not muster_all.empty:
                                        save_fn("muster_roll", muster_all[muster_all["Worker_ID"].astype(str) != target_wid][MUSTER_ROLL_COLS])
                                    if not adv_all.empty:
                                        save_fn("worker_advances", adv_all[adv_all["Worker_ID"].astype(str) != target_wid][ADVANCE_LOG_COLS])
                                st.session_state.selected_worker_card_id = None
                                st.rerun()

            today_dt = datetime.date.today()
            cur_payroll_map = compute_monthly_payroll_and_advances(w_table, muster_all, adv_all, tata_all, today_dt.year, today_dt.month)
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
                st.info("No employee records found.")
                return

            flt_c1, flt_c2, flt_c3, flt_c4 = st.columns([1.0, 1.0, 1.1, 1.6])
            with flt_c1:
                f_div = st.selectbox("Division", ["All", "Fleet Operations", "Civil Construction"], key="reg_div_f")
            with flt_c2:
                f_skill = st.selectbox("Skill Category", ["All"] + SKILL_CATEGORIES + ["Monthly Fixed"], key="reg_skill_f")
            with flt_c3:
                f_status = st.selectbox("Status", ["All", "ACTIVE", "INACTIVE"], index=1, key="reg_stat_f")
            with flt_c4:
                f_search = st.text_input("Search Employee", placeholder="Filter by Name, ID, or Account...").strip().lower()

            disp_table = w_table.copy()
            if f_div != "All":
                disp_table = disp_table[disp_table["Division"] == f_div]
            if f_skill != "All":
                disp_table = disp_table[disp_table["Skill_Category"].astype(str) == f_skill]
            if f_status != "All":
                disp_table = disp_table[disp_table["Status"].astype(str).str.upper() == f_status]
            if f_search:
                disp_table = disp_table[
                    disp_table["Full_Name"].astype(str).str.lower().str.contains(f_search) |
                    disp_table["Worker_ID"].astype(str).str.lower().str.contains(f_search) |
                    disp_table["Bank_Account_No"].astype(str).str.lower().str.contains(f_search)
                ]

            act_c1, act_c2 = st.columns([1.25, 1.35], gap="medium")
            with act_c1:
                with st.container(border=True):
                    st.markdown("###### 🔍 Open Profile (Edit / Delete / Photo)")
                    emp_choices = {f"{r['Worker_ID']} — {r['Full_Name']} ({r['Designation']})": str(r["Worker_ID"]) for _, r in disp_table.iterrows()}
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
                        cards_pdf_bytes = generate_atm_id_cards_pdf(cards_subset_df, logo_fn)
                        pages_cnt = (len(cards_subset_df) + 7) // 8
                        st.download_button(
                            label=f"📥 Download ID Cards PDF ({len(cards_subset_df)} Cards • {pages_cnt} Page)",
                            data=cards_pdf_bytes, file_name=f"M_s_INDU_SAO_ID_Cards_{datetime.date.today().strftime('%Y%m%d')}.pdf",
                            mime="application/pdf", type="primary", use_container_width=True
                        )

            disp_table_view = disp_table.copy()
            disp_table_view["Photo"] = disp_table_view["Photo_Base64"].apply(lambda x: "✅" if str(x).strip() else "—")
            disp_table_view["Week_Off_Day"] = disp_table_view["Week_Off_Day"].apply(normalize_week_off)
            req_cols = ["Worker_ID", "Full_Name", "Photo", "Division", "Designation", "Skill_Category", "Base_Rate", "Week_Off_Day", "Bank_Account_No", "Status"]
            st.dataframe(disp_table_view[[c for c in req_cols if c in disp_table_view.columns]], use_container_width=True, hide_index=True)

    # 4. DAILY FIELD MUSTER ROLL (SUPERVISOR DUPLICATE LOCK + 90-DAY AUDIT LOG + WEEK-OFF LOCK)
    elif current_view == "Daily Muster Roll (Field Attendance)":
        st.markdown("#### Daily Field Muster Roll")
        st.caption("Supervisor Duplicate Lock, Employee Week-Off Lock, Paid Leave (L), and 90-Day Audit Trail")

        w_master = load_fn("worker_master")
        if w_master.empty or "Worker_ID" not in w_master.columns:
            st.warning("No employees available.")
            return

        active_workers = w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].copy()
        muster_df = load_fn("muster_roll")

        top_m1, top_m2, top_m3 = st.columns([1.1, 1.2, 1.2])
        with top_m1:
            muster_date = st.date_input("Select Date", value=datetime.date.today(), key="muster_dt_pick")
            muster_date_str = muster_date.strftime("%Y-%m-%d")
            selected_weekday_name = muster_date.strftime("%A")
        with top_m2:
            sel_division = st.selectbox("Division", ["All Divisions", "Fleet Operations", "Civil Construction"], key="muster_div_filter")
        with top_m3:
            st.metric(f"Active Crew ({selected_weekday_name})", len(active_workers))

        if sel_division != "All Divisions":
            active_workers = active_workers[active_workers["Division"] == sel_division]

        day_records = muster_df[muster_df["Date"].astype(str) == muster_date_str] if not muster_df.empty and "Date" in muster_df.columns else pd.DataFrame()
        div_day_records = day_records[day_records["Division"] == sel_division] if (sel_division != "All Divisions" and not day_records.empty and "Division" in day_records.columns) else day_records.copy()

        is_date_already_logged = (not div_day_records.empty)
        supervisor_locked_out = is_date_already_logged and (not is_admin)

        admin_unlock_wo = False
        if is_admin:
            admin_unlock_wo = st.checkbox(f"🔓 Admin Approval: Unlock Weekly-Off Duty for {muster_date.strftime('%d %b %Y')} ({selected_weekday_name})", value=False, key=f"adm_wo_unlock_{muster_date_str}")

        ym_prefix = muster_date.strftime("%Y-%m")
        logged_dates_this_month = sorted(muster_df[muster_df["Date"].astype(str).str.startswith(ym_prefix)]["Date"].astype(str).unique().tolist()) if not muster_df.empty and "Date" in muster_df.columns else []

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
        st.markdown(f"<div style='background:#ffffff; border:1px solid #e2e8f0; border-radius:10px; padding:8px 12px; margin-bottom:10px;'><div style='font-size:0.76rem; font-weight:600; color:#334155; margin-bottom:4px;'>📅 {muster_date.strftime('%B %Y')} Entry Status:</div><div>{badges_html}</div></div>", unsafe_allow_html=True)

        cache_key = f"att_state_{muster_date_str}"
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
                worker_map = {f"{r['Worker_ID']} — {r['Full_Name']}": str(r["Worker_ID"]) for _, r in active_workers.iterrows()}
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
                    st.markdown(f"**{wname}** (`{wid}`) • <span style='font-size: 0.78rem; color: #4f46e5; font-weight: 500;'>{wdiv}</span>{lock_tag}", unsafe_allow_html=True)
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
        w_master = load_fn("worker_master")
        muster_df = load_fn("muster_roll")

        mc1, mc2, mc3 = st.columns([1.0, 1.2, 1.3])
        with mc1:
            mat_year = st.selectbox("Year", [2025, 2026, 2027], index=1, key="mat_yr")
        with mc2:
            mat_month = st.selectbox("Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1, key="mat_mo")
        with mc3:
            mat_div = st.selectbox("Division", ["All Divisions", "Fleet Operations", "Civil Construction"], key="mat_dv")

        m_num = list(calendar.month_name).index(mat_month)
        ym_str = f"{mat_year}-{m_num:02d}"
        days_cnt = calendar.monthrange(mat_year, m_num)[1]

        if muster_df.empty or "Date" not in muster_df.columns:
            st.info(f"No attendance data recorded for {mat_month} {mat_year}.")
            return

        m_sub = muster_df[muster_df["Date"].astype(str).str.startswith(ym_str)].copy()
        m_sub["Duty_Location"] = m_sub["Duty_Location"].apply(normalize_duty_type)
        if mat_div != "All Divisions" and "Division" in m_sub.columns:
            m_sub = m_sub[m_sub["Division"] == mat_div]

        present_wids = m_sub[m_sub["Attendance"].isin(["P", "HD", "L"])]["Worker_ID"].astype(str).unique().tolist() if not m_sub.empty else []
        if not present_wids:
            st.warning(f"No employee has any Present ('P', 'HD', or 'L') days in {mat_month} {mat_year}.")
            return

        matrix_rows = []
        for wid in sorted(present_wids):
            w_logs = m_sub[m_sub["Worker_ID"].astype(str) == wid]
            w_name = str(w_logs.iloc[0].get("Worker_Name", ""))
            w_div_val = str(w_logs.iloc[0].get("Division", ""))
            w_desig, w_skill_v = "", ""
            if not w_master.empty and "Worker_ID" in w_master.columns:
                wm_hit = w_master[w_master["Worker_ID"].astype(str) == wid]
                if not wm_hit.empty:
                    w_name = str(wm_hit.iloc[0].get("Full_Name", w_name))
                    w_desig = str(wm_hit.iloc[0].get("Designation", ""))
                    w_skill_v = str(wm_hit.iloc[0].get("Skill_Category", ""))

            row_dict = {"Worker ID": wid, "Employee Name": w_name, "Category": w_skill_v, "Designation": w_desig, "Division": w_div_val}
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

    # 6. ADVANCE & EXPENSE REGISTER
    elif current_view == "Advance & Expense Register":
        st.markdown("#### Advance & Expense Register (With Carry-Forward Ledger)")
        w_master = load_fn("worker_master")
        muster_df = load_fn("muster_roll")
        adv_history = load_fn("worker_advances")
        tata_df = load_fn("tata_form_xvii")

        worker_options = {f"{r['Full_Name']} ({r['Worker_ID']})": (r['Worker_ID'], r['Full_Name'], r['Division']) for _, r in w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].iterrows()} if not w_master.empty and "Worker_ID" in w_master.columns else {}

        c_adv1, c_adv2 = st.columns([1.0, 2.0], gap="large")
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
                            new_adv = {
                                "Txn_ID": f"ADV-{int(time.time())}", "Date": a_date.strftime("%Y-%m-%d"),
                                "Worker_ID": w_id, "Worker_Name": w_name, "Division": w_div,
                                "Amount": a_amt, "Payment_Mode": a_mode, "Supervisor": current_user, "Remarks": a_rem
                            }
                            updated_adv = pd.concat([adv_history, pd.DataFrame([new_adv])], ignore_index=True)
                            if save_fn("worker_advances", updated_adv[ADVANCE_LOG_COLS]):
                                st.success("Transaction recorded.")
                                st.rerun()

        with c_adv2:
            with st.container(border=True):
                st.markdown("###### 📊 Worker-Wise Advance Balance & Carry-Forward Status")
                today_dt = datetime.date.today()
                payroll_now = compute_monthly_payroll_and_advances(w_master, muster_df, adv_history, tata_df, today_dt.year, today_dt.month)
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
                    st.info("All employee advances are fully settled (₹0 pending balance).")

            with st.container(border=True):
                st.markdown("###### Recent Advance Transactions")
                if not adv_history.empty and "Txn_ID" in adv_history.columns:
                    st.dataframe(adv_history[["Date", "Worker_Name", "Amount", "Payment_Mode", "Supervisor", "Remarks"]].tail(15), use_container_width=True, hide_index=True)

    # 7. MONTHLY WAGES, FORM XVII, 3-UP SALARY SLIPS & BANK LETTERS
    elif current_view == "Monthly Wages & Payout Summary":
        st.markdown("#### Monthly Wages, Form XVII Audit, Salary Slips & Bank Letters")
        w_master = load_fn("worker_master")
        muster_df = load_fn("muster_roll")
        adv_df = load_fn("worker_advances")
        tata_df = load_fn("tata_form_xvii")

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
            xvii_file = st.file_uploader("Upload Form XVII (PDF / CSV / XLSX)", type=["pdf", "csv", "xlsx"])
            if xvii_file:
                x_df = pd.DataFrame()
                if xvii_file.name.lower().endswith(".pdf"):
                    if PYPDF_AVAILABLE:
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

                    import_mode = st.radio("Import Mode", ["Replace Month Data", "Append / Merge with Existing Month"], horizontal=True)
                    if st.button("Commit Form XVII Compliance Data", type="primary", use_container_width=True):
                        new_records = []
                        for _, xr in x_df.iterrows():
                            w_name_raw = str(xr.get("Worker_Name") or xr.get("Name_of_Workman") or xr.get("Name") or "").strip()
                            if not w_name_raw or w_name_raw.lower() == "nan" or "TOTAL" in w_name_raw.upper():
                                continue
                            appr_days = float(pd.to_numeric(xr.get("Approved_Days") or xr.get("No_of_days_worked") or 0.0, errors="coerce") or 0.0)
                            net_paid = float(pd.to_numeric(xr.get("Net_Amount_Paid") or xr.get("Net_Amount") or 0.0, errors="coerce") or 0.0)
                            if net_paid <= 0:
                                continue
                            matched_wid = ""
                            if not w_master.empty and "Full_Name" in w_master.columns:
                                match_w = w_master[w_master["Full_Name"].astype(str).str.strip().str.upper() == w_name_raw.upper()]
                                if not match_w.empty:
                                    matched_wid = str(match_w.iloc[0]["Worker_ID"])
                            new_records.append({
                                "Wages_Period": target_ym, "Worker_ID": matched_wid, "Worker_Name": w_name_raw.upper(),
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
                            merged_month_df = combined_m.groupby("Worker_Name", as_index=False).agg({
                                "Wages_Period": "first", "Worker_ID": "first", "Approved_Days": "sum", "Net_Amount_Paid": "sum", "Uploaded_At": "last"
                            })
                            final_tata = pd.concat([other_m, merged_month_df[TATA_COMPLIANCE_COLS]], ignore_index=True)

                        if save_fn("tata_form_xvii", final_tata[TATA_COMPLIANCE_COLS]):
                            st.success(f"Committed {len(new_records)} records!")
                            st.rerun()

        payroll_results = compute_monthly_payroll_and_advances(w_master, muster_df, adv_df, tata_df, sel_year, month_num)
        recon_rows, boi_batch, neft_batch, salary_slip_records = [], [], [], []

        for wid, p in payroll_results.items():
            if p["Status"] != "ACTIVE" and p["Total_Days"] == 0 and p["Form_XVII_Net"] == 0 and p["Closing_Carry_Forward"] == 0:
                continue
            punch_days, f17_days, f17_net = p["Punch_Days"], p["Form_XVII_Days"], p["Form_XVII_Net"]
            status_badge = "🟢 Matched" if (f17_days == punch_days and f17_days > 0) else ("🔴 No Punch (Field Only)" if f17_days == 0 and punch_days > 0 else f"🟡 Variance ({punch_days - f17_days:+.1f} d)")

            if p["Total_Days"] > 0 or f17_net > 0 or p["Total_Advance_Due"] > 0:
                salary_slip_records.append(p)

            recon_rows.append({
                "Worker ID": wid, "Name": p["Worker_Name"], "Punch": punch_days, "Non-Punch": p["Non_Punch_Days"],
                "Leave (L)": p["Paid_Leave_Days"], "Form XVII": f17_days, "Status": status_badge,
                "Form XVII Bank (₹)": f17_net, "Non-Punch/Fixed Net (₹)": p["Non_Punch_Net_Pay"],
                "Adv. Ded. (₹)": p["Advance_Deducted"], "Carry Fwd Adv (₹)": p["Closing_Carry_Forward"]
            })

            if f17_net > 0 and p["Bank_Account_No"]:
                item = {
                    "NAME": p["Worker_Name"], "AMOUNT": f17_net, "BENEFICIARY ACCOUNT NO": p["Bank_Account_No"],
                    "IFSC CODE": p["Bank_IFSC_Code"], "BANK & BRANCH DETAILS": p["Bank_Branch_Details"]
                }
                if p["Bank_IFSC_Code"].startswith("BKID"):
                    boi_batch.append(item)
                else:
                    neft_batch.append(item)

        if recon_rows:
            st.dataframe(pd.DataFrame(recon_rows), use_container_width=True, hide_index=True)

        with st.container(border=True):
            st.markdown("###### 🧾 Worker Salary Slips (3 Slips per A4 Page • PF, ESIC, Form XVII & Carry-Forward Advance)")
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
                        data=generate_compact_salary_slips_pdf(filtered_slips, wage_period_str, logo_fn),
                        file_name=f"M_s_INDU_SAO_Salary_Slips_{wage_period_str}.pdf", mime="application/pdf", type="primary", use_container_width=True
                    )

        st.markdown("###### Automated Bank Covering Letters (Strict Form XVII Bank Payouts)")
        p_c1, p_c2 = st.columns(2)
        with p_c1:
            chq_no = st.text_input("Cheque Number", value="238883", key="bank_chq_no").strip()
        with p_c2:
            chq_date = st.text_input("Cheque Date (DD.MM.YYYY)", value=datetime.date.today().strftime("%d.%m.%Y"), key="bank_chq_dt").strip()

        tot_boi = sum([r["AMOUNT"] for r in boi_batch])
        tot_neft = sum([r["AMOUNT"] for r in neft_batch])

        dl_c1, dl_c2 = st.columns(2)
        with dl_c1:
            if boi_batch and FPDF_AVAILABLE:
                st.download_button(
                    label=f"📥 Download BOI Covering Letter PDF ({len(boi_batch)} Workers • ₹{tot_boi:,.0f})",
                    data=generate_bank_authority_letter_pdf("BOI", boi_batch, chq_no, chq_date, letterhead_fn),
                    file_name=f"BOI_Transfer_Letter_{wage_period_str}.pdf", mime="application/pdf", type="primary", use_container_width=True
                )
        with dl_c2:
            if neft_batch and FPDF_AVAILABLE:
                st.download_button(
                    label=f"📥 Download NEFT Covering Letter PDF ({len(neft_batch)} Workers • ₹{tot_neft:,.0f})",
                    data=generate_bank_authority_letter_pdf("NEFT", neft_batch, chq_no, chq_date, letterhead_fn),
                    file_name=f"NEFT_Transfer_Letter_{wage_period_str}.pdf", mime="application/pdf", type="primary", use_container_width=True
                )
