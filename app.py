import calendar
import datetime
import io
import json
import re
from google.oauth2.service_account import Credentials
import gspread
from num2words import num2words
import pandas as pd
import pdfplumber
import streamlit as st

st.set_page_config(
    page_title="Tata Steel Fleet Billing System",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ----------------- 1. GOOGLE SHEETS CONNECTION -----------------
SHEET_NAME = st.secrets.get("spreadsheet_name", "Tata_Steel_Fleet_DB")


@st.cache_resource
def get_gspread_client():
  scopes = [
      "https://www.googleapis.com/auth/spreadsheets",
      "https://www.googleapis.com/auth/drive",
  ]
  if "gcp_json_raw" in st.secrets:
    creds_info = json.loads(st.secrets["gcp_json_raw"])
  elif "gcp_service_account" in st.secrets:
    creds_info = dict(st.secrets["gcp_service_account"])
  else:
    raise KeyError(
        "Neither 'gcp_json_raw' nor 'gcp_service_account' found in secrets!"
    )

  credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
  return gspread.authorize(credentials)


def load_sheet_df(worksheet_name):
  try:
    gc = get_gspread_client()
    sh = gc.open(SHEET_NAME)
    ws = sh.worksheet(worksheet_name)
    data = ws.get_all_records()
    return pd.DataFrame(data)
  except Exception as e:
    st.error(f"Error loading {worksheet_name}: {e}")
    return pd.DataFrame()


def save_sheet_df(worksheet_name, df):
  try:
    gc = get_gspread_client()
    sh = gc.open(SHEET_NAME)
    ws = sh.worksheet(worksheet_name)
    ws.clear()
    df_clean = df.fillna("")
    ws.update([df_clean.columns.values.tolist()] + df_clean.values.tolist())
  except Exception as e:
    st.error(f"Error saving {worksheet_name}: {e}")


# ----------------- 2. CALCULATION & TEMPLATE ENGINE -----------------
def compute_shift_and_ot(
    date_str, start_t, end_t, m_start, m_end, bd_hrs, remarks
):
  rem_upper = str(remarks).upper() if pd.notna(remarks) else ""

  is_sunday = False
  try:
    if (
        datetime.datetime.strptime(str(date_str).strip(), "%d-%m-%Y").weekday()
        == 6
    ):
      is_sunday = True
  except Exception:
    pass
  if "SUNDAY" in rem_upper:
    is_sunday = True

  is_breakdown = ("BREAKDOWN" in rem_upper) or (
      pd.notna(bd_hrs) and float(bd_hrs) >= 4.0
  )
  run_km = (
      max(0.0, float(m_end) - float(m_start))
      if (pd.notna(m_end) and float(m_end) > 0)
      else 0.0
  )

  ot_hours = 0.0
  if pd.notna(end_t) and str(end_t).strip() and not is_breakdown:
    try:
      cutoff_t = pd.to_datetime("06:00 PM", format="%I:%M %p")
      actual_end_t = pd.to_datetime(str(end_t).strip(), format="%I:%M %p")
      diff = (actual_end_t - cutoff_t).total_seconds() / 3600.0
      if diff < 0 and "AM" in str(end_t).upper():
        diff += 24.0
      eff_ot = max(0.0, diff - float(bd_hrs if pd.notna(bd_hrs) else 0))
      ot_hours = round(eff_ot, 2)
    except Exception:
      ot_hours = 0.0

  if is_breakdown:
    disp_shift = "0"
    billable_shift = 0
  elif is_sunday:
    disp_shift = ""
    billable_shift = 1
  else:
    disp_shift = "1"
    billable_shift = 1

  return disp_shift, ot_hours, run_km, billable_shift


def create_statement_template(vehicle_no, year, month):
  num_days = calendar.monthrange(year, month)[1]
  rows = []
  for day in range(1, num_days + 1):
    d_str = "{:02d}-{:02d}-{}".format(day, month, year)
    d_obj = datetime.date(year, month, day)
    is_sun = d_obj.weekday() == 6
    rows.append({
        "SL_NO": day,
        "DATE": d_str,
        "START_TIME": "" if is_sun else "08:00 AM",
        "END_TIME": "" if is_sun else "06:00 PM",
        "METER_START": 0,
        "METER_END": 0,
        "BREAKDOWN_TIME_HRS": 0.0,
        "INVOLVEMENT": "",
        "REMARKS": "SUNDAY" if is_sun else "",
    })
  df_tpl = pd.DataFrame(rows)
  buf = io.BytesIO()
  with pd.ExcelWriter(buf, engine="openpyxl") as writer:
    df_tpl.to_excel(writer, sheet_name="Statement_Template", index=False)
  return buf.getvalue()


def process_uploaded_statement_excel(uploaded_file, vehicle_no):
  df_up = pd.read_excel(uploaded_file)
  required_cols = [
      "DATE",
      "START_TIME",
      "END_TIME",
      "METER_START",
      "METER_END",
      "BREAKDOWN_TIME_HRS",
      "INVOLVEMENT",
      "REMARKS",
  ]
  if not all(col in df_up.columns for col in required_cols):
    return None, "Invalid Format: Excel column headers match nahi kar rahe hain."

  parsed_entries = []
  for _, r in df_up.iterrows():
    d_str = str(r["DATE"]).strip()
    if len(d_str) > 10 and " " in d_str:
      d_str = d_str.split(" ")[0]
    s_time = (
        str(r["START_TIME"]).strip()
        if (pd.notna(r["START_TIME"]) and str(r["START_TIME"]).strip() != "nan")
        else ""
    )
    e_time = (
        str(r["END_TIME"]).strip()
        if (pd.notna(r["END_TIME"]) and str(r["END_TIME"]).strip() != "nan")
        else ""
    )
    bd_hrs = (
        float(r["BREAKDOWN_TIME_HRS"])
        if pd.notna(r["BREAKDOWN_TIME_HRS"])
        else 0.0
    )
    m_start = float(r["METER_START"]) if pd.notna(r["METER_START"]) else 0.0
    m_end = float(r["METER_END"]) if pd.notna(r["METER_END"]) else 0.0
    invol = (
        str(r["INVOLVEMENT"]).strip()
        if (pd.notna(r["INVOLVEMENT"]) and str(r["INVOLVEMENT"]).strip() != "nan")
        else ""
    )
    rem = (
        str(r["REMARKS"]).strip()
        if (pd.notna(r["REMARKS"]) and str(r["REMARKS"]).strip() != "nan")
        else ""
    )

    disp_s, ot, r_km, _ = compute_shift_and_ot(
        d_str, s_time, e_time, m_start, m_end, bd_hrs, rem
    )
    day_val = d_str.split("-")[0] if "-" in d_str else "1"

    parsed_entries.append({
        "Vehicle_No": vehicle_no,
        "SL_NO": str(int(float(day_val))),
        "DATE": d_str,
        "START_TIME": s_time,
        "END_TIME": e_time,
        "NO_OF_SHIFTS": disp_s,
        "OVERTIME_HRS": str(ot) if ot > 0 else "0",
        "METER_START": str(int(m_start)),
        "METER_END": str(int(m_end)),
        "TOTAL_RUNNING_KM": str(r_km),
        "BREAKDOWN_TIME_HRS": str(bd_hrs),
        "INVOLVEMENT": invol,
        "REMARKS": rem,
    })

  return pd.DataFrame(parsed_entries), "Success"


def generate_full_month_statement(vehicle_no, year, month, raw_logs_df):
  num_days = calendar.monthrange(year, month)[1]
  veh_logs = raw_logs_df[raw_logs_df["Vehicle_No"] == vehicle_no].copy()

  full_rows = []
  for day in range(1, num_days + 1):
    d_str = "{:02d}-{:02d}-{}".format(day, month, year)
    d_obj = datetime.date(year, month, day)
    is_sun = d_obj.weekday() == 6

    match = veh_logs[veh_logs["DATE"] == d_str]

    if not match.empty:
      r = match.iloc[0]
      disp_s, ot, r_km, b_s = compute_shift_and_ot(
          d_str,
          r.get("START_TIME", ""),
          r.get("END_TIME", ""),
          r.get("METER_START", 0),
          r.get("METER_END", 0),
          r.get("BREAKDOWN_TIME_HRS", 0),
          r.get("REMARKS", ""),
      )
      full_rows.append({
          "Vehicle_No": vehicle_no,
          "SL_NO": str(day),
          "DATE": d_str,
          "START_TIME": str(r.get("START_TIME", "")),
          "END_TIME": str(r.get("END_TIME", "")),
          "NO_OF_SHIFTS": disp_s,
          "OVERTIME_HRS": str(ot) if ot > 0 else "0",
          "METER_START": str(r.get("METER_START", "0")),
          "METER_END": str(r.get("METER_END", "0")),
          "TOTAL_RUNNING_KM": str(r_km),
          "BREAKDOWN_TIME_HRS": str(r.get("BREAKDOWN_TIME_HRS", "0.0")),
          "INVOLVEMENT": str(r.get("INVOLVEMENT", "")),
          "REMARKS": str(r.get("REMARKS", "")),
      })
    else:
      full_rows.append({
          "Vehicle_No": vehicle_no,
          "SL_NO": str(day),
          "DATE": d_str,
          "START_TIME": "",
          "END_TIME": "",
          "NO_OF_SHIFTS": "",
          "OVERTIME_HRS": "0",
          "METER_START": "0",
          "METER_END": "0",
          "TOTAL_RUNNING_KM": "0.0",
          "BREAKDOWN_TIME_HRS": "0.0",
          "INVOLVEMENT": "",
          "REMARKS": "SUNDAY" if is_sun else "",
      })

  return pd.DataFrame(full_rows)


def get_live_do_position(df_master, df_ledger, do_no, target_ra_bill_no=None):
  do_items = df_master[df_master["DO_No"] == do_no].copy()
  positions = []

  for _, row in do_items.iterrows():
    srv_ln = row["SrvLnNo"]
    boq_qty = float(row.get("BOQ_Qty", 0.0) or 0.0)
    init_qty = float(row.get("Initial_Baseline_Qty", 0.0) or 0.0)
    init_amt = float(row.get("Initial_Baseline_Amt", 0.0) or 0.0)

    prior_qty = 0.0
    prior_amt = 0.0
    if not df_ledger.empty:
      prior_bills = df_ledger[
          (df_ledger["DO_No"] == do_no)
          & (df_ledger["SrvLnNo"].astype(str) == str(srv_ln))
          & (df_ledger["Status"] == "Approved")
      ]
      if target_ra_bill_no:
        prior_bills = prior_bills[
            prior_bills["RA_Bill_No"].astype(str) != str(target_ra_bill_no)
        ]

      prior_qty = prior_bills["Billed_Qty"].astype(float).sum()
      prior_amt = prior_bills["Billed_Amt"].astype(float).sum()

    prev_qty = init_qty + prior_qty
    prev_amt = init_amt + prior_amt
    balance_qty = boq_qty - prev_qty

    positions.append({
        "DO_No": do_no,
        "SrvLnNo": srv_ln,
        "Equipment_Family": row.get("Equipment_Family", "General"),
        "Description": row.get("Description", ""),
        "Unit": row.get("Unit", "Month"),
        "BOQ_Qty": boq_qty,
        "Rate": float(row.get("Rate", 0.0) or 0.0),
        "OT_Rate": float(row.get("OT_Rate", 0.0) or 0.0),
        "Fuel_Rate_KM": float(row.get("Fuel_Rate_KM", 0.0) or 0.0),
        "Prev_Qty": round(prev_qty, 3),
        "Prev_Amt": round(prev_amt, 2),
        "Balance_BOQ_Qty": round(balance_qty, 3),
    })

  return pd.DataFrame(positions)


def apply_do_amendment(existing_master_df, new_extracted_items):
  df = existing_master_df.copy()
  for item in new_extracted_items:
    do_no = item["DO_No"]
    srv_ln = item["SrvLnNo"]
    new_boq_qty = float(item["BOQ_Qty"])
    new_rate = float(item["Rate"])

    match_idx = df[
        (df["DO_No"] == do_no) & (df["SrvLnNo"].astype(str) == str(srv_ln))
    ].index
    if not match_idx.empty:
      i = match_idx[0]
      df.at[i, "BOQ_Qty"] = new_boq_qty
      df.at[i, "Rate"] = new_rate
      if item.get("OT_Rate", 0) > 0:
        df.at[i, "OT_Rate"] = item["OT_Rate"]
      if item.get("Fuel_Rate_KM", 0) > 0:
        df.at[i, "Fuel_Rate_KM"] = item["Fuel_Rate_KM"]
    else:
      new_row = {
          "DO_No": do_no,
          "SrvLnNo": srv_ln,
          "Equipment_Family": item.get("Equipment_Family", "General"),
          "Description": item.get("Description", ""),
          "Unit": item.get("Unit", "Month"),
          "BOQ_Qty": new_boq_qty,
          "Rate": new_rate,
          "OT_Rate": item.get("OT_Rate", 0.0),
          "Fuel_Rate_KM": item.get("Fuel_Rate_KM", 0.0),
          "Initial_Baseline_Qty": 0.0,
          "Initial_Baseline_Amt": 0.0,
      }
      df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
  return df


def extract_data_from_tata_do(pdf_file):
  extracted_items = []
  wo_no = None

  with pdfplumber.open(pdf_file) as pdf:
    first_page_text = pdf.pages[0].extract_text() or ""

  wo_match = re.search(
      r"Order\s*No[.:\s]*-*(\d{10}/\d{2,3})", first_page_text, re.IGNORECASE
  )
  if wo_match:
    wo_no = wo_match.group(1).strip()
  else:
    sec_match = re.search(r"\b(\d{10}/\d{2,3})\b", first_page_text)
    wo_no = sec_match.group(1).strip() if sec_match else None

  pattern = (
      r"(\d+)\s+(\d{2})\s+([A-Za-z0-9/\s,.\-#]+?)\s+([\d,]+\.?\d*)\s*(Months?|Hours?|LUMPSUM|KM)\s+([\d,]+\.?\d*)\s*INR"
  )
  matches = re.findall(pattern, first_page_text, re.IGNORECASE)

  for m in matches:
    _, srv_ln, desc, qty_str, unit, rate_str = m
    desc_clean = desc.strip().replace("\n", " ")
    qty = float(qty_str.replace(",", ""))
    rate = float(rate_str.replace(",", ""))

    if "BACKHOE" in desc_clean.upper() or "JCB" in desc_clean.upper():
      family = "Backhoe / JCB"
    elif "CAMPER" in desc_clean.upper():
      family = "Camper"
    elif "TRAILOR" in desc_clean.upper():
      family = "Trailor-40MT"
    else:
      family = desc_clean[:20]

    ot_rate = rate if unit.lower() in ["hours", "hour"] else 0.0
    fuel_rate = 40.0 if "TRAILOR" in family.upper() else 12.0

    extracted_items.append({
        "DO_No": wo_no,
        "SrvLnNo": int(srv_ln),
        "Equipment_Family": family,
        "Description": desc_clean,
        "Unit": unit.strip(),
        "BOQ_Qty": qty,
        "Rate": rate,
        "OT_Rate": ot_rate,
        "Fuel_Rate_KM": fuel_rate,
        "Initial_Baseline_Qty": 0.0,
        "Initial_Baseline_Amt": 0.0,
    })

  return wo_no, extracted_items


# ----------------- 3. APP INTERFACE -----------------
st.title("Tata Steel Fleet Billing & DO Ledger System")
st.caption("Vendor: M/S INDU SAO (ID47) | Coal Projects, West Bokaro")

tabs = st.tabs([
    "Daily Logsheet Entry",
    "Monthly Statement & Abstract",
    "DO Upload & Baseline Setup",
    "Vehicle Master Mapping",
])

# Load data live from Google Sheets
df_master_do = load_sheet_df("master_do_items")
df_vehs = load_sheet_df("master_vehicles")
df_logs = load_sheet_df("fleet_daily_logsheets")
df_ledger = load_sheet_df("ra_bills_ledger")

if "Equipment_Family" not in df_vehs.columns and not df_vehs.empty:
  df_vehs["Equipment_Family"] = df_vehs.get("Item_Category", "General")
if "Mapped_DO_No" not in df_vehs.columns and not df_vehs.empty:
  df_vehs["Mapped_DO_No"] = df_vehs.get("DO_No", "N/A")

# --- TAB 1: DAILY ENTRY, EDIT & EXCEL IMPORT ---
with tabs[0]:
  st.subheader("Daily Field Duty Slip Entry & Verification")
  if df_vehs.empty:
    st.warning(
        "No vehicles registered. Please configure fleet entries in 'Vehicle"
        " Master Mapping' tab."
    )
  else:
    with st.expander("📥 Bulk Statement Import / Export (Offline Excel Option)"):
      st.caption(
          "Aap Excel file bhar kar yahan upload kar sakte hain. System Google"
          " Sheets me data sync kar dega."
      )
      c_dl1, c_dl2, c_dl3 = st.columns(3)
      sel_dl_veh = c_dl1.selectbox(
          "Select Vehicle for Template",
          df_vehs["Vehicle_No"].unique(),
          key="sel_dl_veh",
      )
      t_month = c_dl2.selectbox(
          "Template Month",
          list(range(1, 13)),
          index=datetime.date.today().month - 1,
          key="t_month",
      )
      t_year = c_dl3.selectbox(
          "Template Year", [2025, 2026, 2027], index=1, key="t_year"
      )

      excel_template = create_statement_template(sel_dl_veh, t_year, t_month)
      clean_dl_veh = str(sel_dl_veh).replace("-", "_")
      st.download_button(
          label="📄 Download Official Blank Template (Excel)",
          data=excel_template,
          file_name="Tata_Statement_Template_{}_{}_{:02d}.xlsx".format(
              clean_dl_veh, t_year, t_month
          ),
          mime=(
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          ),
      )

      st.markdown("---")
      st.write("**Upload Completed Statement Excel File:**")
      up_excel = st.file_uploader(
          "Upload Completed Statement Sheet",
          type=["xlsx", "xls"],
          key="bulk_excel_up",
      )

      if up_excel is not None:
        if st.button("Sync & Import to Google Sheets"):
          imported_df, msg = process_uploaded_statement_excel(
              up_excel, sel_dl_veh
          )
          if imported_df is not None:
            month_suffix = "-{:02d}-{}".format(t_month, t_year)
            if not df_logs.empty:
              df_logs = df_logs[
                  ~(
                      (df_logs["Vehicle_No"] == sel_dl_veh)
                      & (df_logs["DATE"].astype(str).str.endswith(month_suffix))
                  )
              ]
            df_logs = pd.concat([df_logs, imported_df], ignore_index=True)
            save_sheet_df("fleet_daily_logsheets", df_logs)
            st.success("Google Sheets synchronized successfully!")
            st.rerun()
          else:
            st.error(msg)

    st.markdown("---")

    col_t1, col_t2 = st.columns(2)
    selected_veh = col_t1.selectbox(
        "Select Deployed Vehicle",
        df_vehs["Vehicle_No"].unique(),
        key="daily_form_veh",
    )
    selected_date = col_t2.date_input(
        "Select Operating Date", datetime.date.today(), key="daily_form_date"
    )
    d_date_str = "{:02d}-{:02d}-{}".format(
        selected_date.day, selected_date.month, selected_date.year
    )

    v_match = df_vehs[df_vehs["Vehicle_No"] == selected_veh]
    if not v_match.empty:
      v_row = v_match.iloc[0]
      st.caption(
          "Asset Category: **{}** | Work Order Reference: **{}**".format(
              v_row.get("Equipment_Family"), v_row.get("Mapped_DO_No")
          )
      )

    existing_record = pd.DataFrame()
    if not df_logs.empty:
      existing_record = df_logs[
          (df_logs["Vehicle_No"] == selected_veh)
          & (df_logs["DATE"] == d_date_str)
      ]
    is_already_submitted = not existing_record.empty

    if is_already_submitted:
      st.warning(
          "Notice: Existing duty log detected for Date {}. Form is loaded in"
          " Correction / Overwrite mode.".format(d_date_str)
      )
      rec = existing_record.iloc[0]
      def_start = str(rec.get("START_TIME", "08:00 AM"))
      def_end = str(rec.get("END_TIME", "06:00 PM"))
      def_bd = float(rec.get("BREAKDOWN_TIME_HRS", 0.0) or 0.0)
      def_mstart = int(float(rec.get("METER_START", 0) or 0))
      def_mend = int(float(rec.get("METER_END", 0) or 0))
      def_dept = str(rec.get("INVOLVEMENT", ""))
      def_rem = str(rec.get("REMARKS", ""))
    else:
      st.info("Status: Ready for New Log Entry | Date: {}".format(d_date_str))
      def_start = "08:00 AM"
      def_end = "06:00 PM"
      def_bd = 0.0
      def_mstart = 0
      def_mend = 0
      def_dept = ""
      def_rem = ""

    with st.form("duty_form"):
      c1, c2, c3 = st.columns(3)
      s_time = c1.text_input("Shift Start Time", def_start)
      e_time = c2.text_input(
          "Shift End Time (OT applies after 06:00 PM)", def_end
      )
      bd_hrs = c3.number_input(
          "Breakdown Duration (Hours)", value=def_bd, step=0.5
      )

      c4, c5, c6 = st.columns(3)
      m_start = c4.number_input(
          "Odometer Start Reading", value=def_mstart, min_value=0
      )
      m_end = c5.number_input(
          "Odometer End Reading", value=def_mend, min_value=0
      )
      dept = c6.text_input("Site Indentor / Dept (Optional)", def_dept)

      remarks = st.text_input(
          "Operation Remarks (SUNDAY / NORMAL / BREAKDOWN / IDLE)", def_rem
      )

      btn_label = (
          "Update / Override Existing Entry"
          if is_already_submitted
          else "Submit & Log Duty Slip"
      )
      if st.form_submit_button(btn_label):
        disp_s, ot, r_km, _ = compute_shift_and_ot(
            d_date_str, s_time, e_time, m_start, m_end, bd_hrs, remarks
        )

        if is_already_submitted:
          df_logs = df_logs[
              ~(
                  (df_logs["Vehicle_No"] == selected_veh)
                  & (df_logs["DATE"] == d_date_str)
              )
          ]

        day_num = selected_date.day
        new_row = {
            "Vehicle_No": selected_veh,
            "SL_NO": str(day_num),
            "DATE": d_date_str,
            "START_TIME": s_time,
            "END_TIME": e_time,
            "NO_OF_SHIFTS": disp_s,
            "OVERTIME_HRS": str(ot) if ot > 0 else "0",
            "METER_START": str(m_start),
            "METER_END": str(m_end),
            "TOTAL_RUNNING_KM": str(r_km),
            "BREAKDOWN_TIME_HRS": str(bd_hrs),
            "INVOLVEMENT": dept,
            "REMARKS": remarks,
        }
        df_logs = pd.concat(
            [df_logs, pd.DataFrame([new_row])], ignore_index=True
        )
        save_sheet_df("fleet_daily_logsheets", df_logs)
        st.success(
            f"Duty slip recorded to Google Sheets for vehicle {selected_veh}."
        )
        st.rerun()


# --- TAB 2: BILLING & FULL MONTH STATEMENT ---
with tabs[1]:
  st.subheader("Monthly Statement Generation & Financial Abstract")

  available_dos = (
      df_master_do["DO_No"].dropna().unique()
      if not df_master_do.empty
      else []
  )
  if len(available_dos) == 0:
    st.info(
        "No active Delivery Orders available. Please upload or register an"
        " Order under 'DO Upload' tab."
    )
  else:
    col_d1, col_d2, col_d3 = st.columns(3)
    chosen_do = col_d1.selectbox("Select Target Work Order / DO", available_dos)
    ra_bill_num = col_d2.text_input("RA Bill Number (e.g., 08)", value="08")

    today = datetime.date.today()
    bill_m = col_d3.selectbox(
        "Billing Month", list(range(1, 13)), index=today.month - 1
    )
    bill_y = col_d3.selectbox("Billing Year", [2025, 2026, 2027], index=1)
    m_name = calendar.month_name[bill_m].upper()
    num_days = calendar.monthrange(bill_y, bill_m)[1]
    bill_period = "01-{}-{} TO {:02d}-{}-{}".format(
        m_name[:3], bill_y, num_days, m_name[:3], bill_y
    )

    assigned_vehs = df_vehs[df_vehs["Mapped_DO_No"] == chosen_do][
        "Vehicle_No"
    ].tolist()
    st.write(
        "**Billing Tenure:** `{}` | **Attached Equipment Units:** {}".format(
            bill_period, ", ".join(assigned_vehs)
        )
    )

    live_do_view = get_live_do_position(
        df_master_do, df_ledger, chosen_do, target_ra_bill_no=ra_bill_num
    )

    veh_full_statements = {}
    total_group_billable_shifts = 0
    total_group_ot = 0.0
    total_group_km = 0.0

    for v in assigned_vehs:
      v_statement = generate_full_month_statement(v, bill_y, bill_m, df_logs)
      veh_full_statements[v] = v_statement

      for _, r in v_statement.iterrows():
        _, ot_val, km_val, b_shift = compute_shift_and_ot(
            r["DATE"],
            r["START_TIME"],
            r["END_TIME"],
            r["METER_START"],
            r["METER_END"],
            r["BREAKDOWN_TIME_HRS"],
            r["REMARKS"],
        )
        total_group_billable_shifts += b_shift
        total_group_ot += ot_val
        total_group_km += km_val

    if assigned_vehs:
      chosen_view_veh = st.selectbox(
          "Select Equipment Logsheet to Review:", assigned_vehs
      )
      st.info(
          "Displaying sequential calendar statement (Days 1 to {}) for {}."
          " Audit verification active.".format(num_days, chosen_view_veh)
      )

      edited_stmt = st.data_editor(
          veh_full_statements[chosen_view_veh],
          num_rows="fixed",
          use_container_width=True,
          key="stmt_edit_{}".format(chosen_view_veh),
      )

      if st.button("Save Verified Adjustments for {}".format(chosen_view_veh)):
        df_logs = df_logs[
            ~(
                (df_logs["Vehicle_No"] == chosen_view_veh)
                & (
                    df_logs["DATE"]
                    .astype(str)
                    .str.endswith("-{:02d}-{}".format(bill_m, bill_y))
                )
            )
        ]
        valid_rows = edited_stmt[
            (edited_stmt["START_TIME"].str.strip() != "")
            | (edited_stmt["REMARKS"].str.strip() != "")
        ]
        df_logs = pd.concat([df_logs, valid_rows], ignore_index=True)
        save_sheet_df("fleet_daily_logsheets", df_logs)
        st.success("Google Sheets synchronized successfully.")
        st.rerun()

    month_qty = round(
        min(26.0 * max(1, len(assigned_vehs)), total_group_billable_shifts)
        / 26.0,
        3,
    )

    abstract_rows = []
    total_this_bill = 0.0

    for _, row in live_do_view.iterrows():
      sl = row["SrvLnNo"]
      unit = str(row.get("Unit", "")).strip()
      rate = float(row.get("Rate", 0.0) or 0.0)
      desc = str(row.get("Description", ""))

      if unit.lower() in ["months", "month"]:
        this_qty = month_qty
        this_amt = round(this_qty * rate, 2)
      elif unit.lower() in ["hours", "hour"]:
        this_qty = total_group_ot
        this_amt = round(this_qty * rate, 2)
      elif "fuel" in desc.lower():
        f_rate = float(row.get("Fuel_Rate_KM", 0.0) or 0.0)
        this_qty = (
            round(total_group_km * f_rate, 2) if rate == 1.0 else total_group_km
        )
        this_amt = round(this_qty * rate, 2)
      else:
        this_qty = 0.0
        this_amt = 0.0

      cum_qty = round(row["Prev_Qty"] + this_qty, 3)
      cum_amt = round(row["Prev_Amt"] + this_amt, 2)
      total_this_bill += this_amt

      abstract_rows.append({
          "W.O SrL No": sl,
          "Description": desc,
          "Unit": unit,
          "BOQ Rate": rate,
          "BOQ Qty": row["BOQ_Qty"],
          "Prev Qty": row["Prev_Qty"],
          "Prev Amount": row["Prev_Amt"],
          "This Bill Qty": this_qty,
          "This Bill Amount": this_amt,
          "Cumulative Qty": cum_qty,
          "Cumulative Amount": cum_amt,
      })

    abs_df = pd.DataFrame(abstract_rows)

    st.markdown("---")
    st.subheader("RA Bill {} Billing Abstract".format(ra_bill_num))
    st.dataframe(
        abs_df[[
            "W.O SrL No",
            "Description",
            "Unit",
            "Prev Amount",
            "This Bill Amount",
            "Cumulative Amount",
        ]],
        use_container_width=True,
    )

    in_words = num2words(int(total_this_bill), lang="en_IN").upper()
    st.write(
        "**Net Invoice Value:** ₹{:,.2f} | **In Words:** {} RUPEES ONLY".format(
            total_this_bill, in_words
        )
    )

    col_btn1, col_btn2 = st.columns(2)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
      abs_df.to_excel(writer, sheet_name="Page1_Abstract", index=False)
      for v, s_df in veh_full_statements.items():
        s_df.to_excel(
            writer,
            sheet_name="Stmt_{}".format(v.replace("-", "_"))[:31],
            index=False,
        )

    clean_do = str(chosen_do).replace("/", "_")
    col_btn1.download_button(
        "Download Official Excel Package (Abstract + Statements)",
        data=buf.getvalue(),
        file_name="Tata_RA_Bill_{}_{}.xlsx".format(ra_bill_num, clean_do),
        mime=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
    )

    if col_btn2.button("Finalize & Lock RA Bill {}".format(ra_bill_num)):
      df_ledger = df_ledger[
          ~(
              (df_ledger["DO_No"] == chosen_do)
              & (df_ledger["RA_Bill_No"].astype(str) == str(ra_bill_num))
          )
      ]
      new_entries = []
      for _, r in abs_df.iterrows():
        new_entries.append({
            "DO_No": chosen_do,
            "RA_Bill_No": str(ra_bill_num),
            "Bill_Period": bill_period,
            "SrvLnNo": r["W.O SrL No"],
            "Billed_Qty": r["This Bill Qty"],
            "Billed_Amt": r["This Bill Amount"],
            "Status": "Approved",
        })
      df_ledger = pd.concat(
          [df_ledger, pd.DataFrame(new_entries)], ignore_index=True
      )
      save_sheet_df("ra_bills_ledger", df_ledger)
      st.success("RA Bill committed to Google Sheets ledger!")
      st.rerun()

    with st.expander("Ledger Revision Controls"):
      st.caption(
          "Revert a previously committed abstract to release quantities back to"
          " the DO balance."
      )
      if st.button("Void RA Bill {}".format(ra_bill_num)):
        match = (df_ledger["DO_No"] == chosen_do) & (
            df_ledger["RA_Bill_No"].astype(str) == str(ra_bill_num)
        )
        if match.any():
          df_ledger.loc[match, "Status"] = "Void"
          save_sheet_df("ra_bills_ledger", df_ledger)
          st.warning("RA Bill has been voided in Google Sheets.")
          st.rerun()
        else:
          st.info("No matching finalized record found.")


# --- TAB 3: DO UPLOAD & BASELINE ---
with tabs[2]:
  st.subheader("1. Register / Amend Delivery Order (DO) Contract")
  uploaded_pdf = st.file_uploader(
      "Upload Official Tata Steel DO (PDF Format)",
      type=["pdf"],
      key="pdf_uploader_clean",
  )

  if uploaded_pdf is not None:
    detected_wo, parsed_items = extract_data_from_tata_do(uploaded_pdf)

    if not detected_wo:
      final_wo = st.text_input(
          "Enter SAP Work Order Number Manually (e.g., 3700981341/109):"
      ).strip()
    else:
      st.success("Extracted Order Number: **{}**".format(detected_wo))
      final_wo = detected_wo

    if parsed_items and final_wo:
      for itm in parsed_items:
        itm["DO_No"] = final_wo
      preview_df = pd.DataFrame(parsed_items)
      st.dataframe(preview_df, use_container_width=True)

      if st.button("Confirm & Apply Contract Scope"):
        df_master_do = apply_do_amendment(df_master_do, parsed_items)
        save_sheet_df("master_do_items", df_master_do)
        st.success(
            "Contract terms saved to Google Sheets for Order {}.".format(
                final_wo
            )
        )
        st.rerun()

  st.markdown("---")
  st.subheader("2. Contract Opening Baseline Configuration")
  st.caption(
      "Configure cumulative historical billing for active contracts prior to"
      " system cutover:"
  )

  edited_master = st.data_editor(
      df_master_do, num_rows="dynamic", key="baseline_editor_clean"
  )

  if st.button("Save Contract Baseline"):
    save_sheet_df("master_do_items", edited_master)
    st.success("Contract baseline updated in Google Sheets.")
    st.rerun()


# --- TAB 4: VEHICLE MASTER MAPPING ---
with tabs[3]:
  st.subheader("Fleet Asset Mapping")
  st.caption("Map deployed vehicle registration numbers to contract line item categories:")

  # Agar Google Sheet nayi/khali hai, toh standard columns enforce karein
  required_veh_cols = ["Vehicle_No", "Equipment_Family", "Mapped_DO_No"]
  for col in required_veh_cols:
    if col not in df_vehs.columns:
      df_vehs[col] = ""

  # 1. Direct Web Form se Nayi Gaadi Add Karein
  with st.expander("➕ Add New Vehicle to Fleet (Direct Entry)", expanded=df_vehs.empty):
    with st.form("add_vehicle_form"):
      col_v1, col_v2, col_v3 = st.columns(3)
      new_v_no = col_v1.text_input("Vehicle Registration No (e.g., JH-02-BC-1234)").strip().upper()
      
      fam_options = ["Backhoe / JCB", "Camper", "Trailor-40MT", "Tipper / Dumper", "Water Tanker", "Other"]
      new_fam = col_v2.selectbox("Equipment Family / Category", fam_options)
      
      available_dos = df_master_do["DO_No"].dropna().unique().tolist() if not df_master_do.empty else []
      if available_dos:
        new_do = col_v3.selectbox("Mapped Tata Steel DO / WO", available_dos)
      else:
        new_do = col_v3.text_input("Mapped DO / WO No (e.g., 3700981341/109)").strip()
      
      if st.form_submit_button("Save & Register Vehicle"):
        if not new_v_no:
          st.error("Kripya Vehicle Registration Number darj karein.")
        elif not df_vehs.empty and new_v_no in df_vehs["Vehicle_No"].astype(str).values:
          st.warning(f"Vehicle {new_v_no} pehle se registered hai.")
        else:
          new_row = pd.DataFrame([{
              "Vehicle_No": new_v_no,
              "Equipment_Family": new_fam,
              "Mapped_DO_No": new_do
          }])
          df_vehs = pd.concat([df_vehs[df_vehs["Vehicle_No"] != ""], new_row], ignore_index=True)
          save_sheet_df("master_vehicles", df_vehs[required_veh_cols])
          st.success(f"Vehicle {new_v_no} successfully registered to Google Sheets!")
          st.rerun()

  st.markdown("---")
  st.write("**Registered Fleet Vehicles (Edit directly in table below):**")
  
  # Table editor - cells me direct click karke bhi edit kar sakte hain
  v_editor = st.data_editor(
      df_vehs[required_veh_cols],
      num_rows="dynamic",
      use_container_width=True,
      key="vehicle_mapper_clean"
  )

  if st.button("Update Fleet Registry Changes"):
    valid_vehs = v_editor[v_editor["Vehicle_No"].astype(str).str.strip() != ""]
    save_sheet_df("master_vehicles", valid_vehs)
    st.success("Fleet registry updated in Google Sheets.")
    st.rerun()
