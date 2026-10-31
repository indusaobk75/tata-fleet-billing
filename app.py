import calendar
import datetime
import io
import json
import math
import re
import time
from google.oauth2.service_account import Credentials
import gspread
from num2words import num2words
import pandas as pd
import pdfplumber
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
import streamlit as st

st.set_page_config(
    page_title="Tata Steel Fleet Billing System",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ----------------- 0. SECURITY & ACCESS CONTROL -----------------
def check_access():
  if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

  if not st.session_state.authenticated:
    col_l1, col_l2, col_l3 = st.columns([1, 2, 1])
    with col_l2:
      st.markdown("### 🔐 Fleet Operations & Billing Portal")
      st.caption("Vendor: M/S INDU SAO (Vendor Code: ID47) | Coal Projects, West Bokaro")
      st.markdown("---")

      st.info(
          "**Confidential Commercial Workspace**\n\n"
          "This system contains proprietary billing records and client contract abstracts. "
          "Access is restricted to authorized site supervisors and billing executives. "
          "Please authenticate with your designated security passkey to continue."
      )

      with st.form("secure_login_gate"):
        entered_pass = st.text_input(
            "Security Passkey",
            type="password",
            placeholder="Enter authorized passkey...",
        )
        submit_auth = st.form_submit_button("Authenticate & Access Workspace")

        if submit_auth:
          master_password = st.secrets.get("app_password", "TataSteel@2026")
          if entered_pass == master_password:
            st.session_state.authenticated = True
            st.success("Authentication confirmed. Initializing application...")
            st.rerun()
          else:
            st.error("Access Denied: Invalid security passkey entered.")

    st.stop()

check_access()

# CLEAN CSS: HIDES MAIN MENU AND FOOTER WITHOUT AFFECTING CALENDAR DATEPICKER HEADERS
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header[data-testid="stHeader"] {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)


# ----------------- 1. GOOGLE SHEETS CONNECTION & SMART CACHE -----------------
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
    raise KeyError("Neither 'gcp_json_raw' nor 'gcp_service_account' found in secrets!")

  credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
  return gspread.authorize(credentials)


@st.cache_data(ttl=15, show_spinner=False)
def load_sheet_df(worksheet_name):
  for attempt in range(3):
    try:
      gc = get_gspread_client()
      sh = gc.open(SHEET_NAME)
      ws = sh.worksheet(worksheet_name)
      data = ws.get_all_records()
      return pd.DataFrame(data)
    except Exception as e:
      err_str = str(e)
      if "429" in err_str:
        time.sleep(2 * (attempt + 1))
        continue
      return pd.DataFrame()
  return pd.DataFrame()


def safe_serialize_cell(v):
  if pd.isna(v) or v is None:
    return ""
  if isinstance(v, (datetime.date, datetime.datetime)):
    return v.strftime("%d-%m-%Y")
  return str(v).strip()


def save_sheet_df(worksheet_name, df):
  if df is None:
    return False

  # Safe cleanup to prevent JSON serialization error
  df_to_save = df.copy()
  temp_drop_cols = [c for c in df_to_save.columns if "parsed_dt" in str(c).lower()]
  if temp_drop_cols:
    df_to_save = df_to_save.drop(columns=temp_drop_cols)

  # Convert every cell cleanly to standard string
  prepared_values = []
  headers = [str(c) for c in df_to_save.columns.tolist()]
  prepared_values.append(headers)

  for row in df_to_save.values.tolist():
    prepared_values.append([safe_serialize_cell(c) for c in row])

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
      err_str = str(e)
      if "429" in err_str:
        time.sleep(2 * (attempt + 1))
        continue
      st.error(f"Error persisting to {worksheet_name}: {e}")
      return False
  return False


# ----------------- 2. UNIVERSAL CALCULATION & ENGINE -----------------
def normalize_do(do_str):
  if not do_str:
    return ""
  return str(do_str).split("/")[0].strip()


def clean_text_cell(val):
  if pd.isna(val) or val is None:
    return "-"
  s = str(val).strip()
  if s.lower() in ["nan", "none", "null", ""]:
    return "-"
  return s


def parse_any_date(date_val):
  if pd.isna(date_val) or not str(date_val).strip():
    return None
  if isinstance(date_val, (datetime.date, datetime.datetime)):
    return date_val if isinstance(date_val, datetime.date) else date_val.date()
  s = str(date_val).strip().split(" ")[0].replace("/", "-")
  for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%d-%m-%y", "%m-%d-%Y"):
    try:
      return datetime.datetime.strptime(s, fmt).date()
    except Exception:
      pass
  try:
    parts = s.split("-")
    if len(parts) == 3:
      d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
      if y < 100:
        y += 2000
      return datetime.date(y, m, d)
  except Exception:
    pass
  return None


def format_12hr_time(t_val):
  if pd.isna(t_val) or not str(t_val).strip():
    return "-"
  s = str(t_val).strip()
  if s.lower() in ["nan", "none", "null", "-", "no work"]:
    return "-"
  try:
    if "PM" in s.upper() or "AM" in s.upper():
      dt = pd.to_datetime(s.upper(), format="%I:%M %p")
      return dt.strftime("%I:%M %p")
    elif len(s.split(":")) == 3:
      dt = pd.to_datetime(s, format="%H:%M:%S")
      return dt.strftime("%I:%M %p")
    elif len(s.split(":")) == 2:
      dt = pd.to_datetime(s, format="%H:%M")
      return dt.strftime("%I:%M %p")
  except Exception:
    pass
  return s


def compute_shift_and_ot(date_str, start_t, end_t, m_start, m_end, bd_hrs, remarks):
  rem_upper = str(remarks).upper() if pd.notna(remarks) else ""

  is_sunday = False
  d_obj = parse_any_date(date_str)
  if d_obj and d_obj.weekday() == 6:
    is_sunday = True
  if "SUNDAY" in rem_upper:
    is_sunday = True

  try:
    bd_hours_val = float(bd_hrs) if pd.notna(bd_hrs) and str(bd_hrs).strip() != "" and str(bd_hrs).lower() != "nan" else 0.0
  except Exception:
    bd_hours_val = 0.0

  try:
    m_s = float(m_start) if pd.notna(m_start) and str(m_start).strip() != "" and str(m_start).lower() != "nan" else 0.0
  except Exception:
    m_s = 0.0

  try:
    m_e = float(m_end) if pd.notna(m_end) and str(m_end).strip() != "" and str(m_end).lower() != "nan" else 0.0
  except Exception:
    m_e = 0.0

  run_units = round(max(0.0, m_e - m_s), 2) if (m_e > 0 and m_e >= m_s) else 0.0

  ot_hours = 0.0
  gross_work_hours = 0.0

  clean_start = str(start_t).strip().upper() if pd.notna(start_t) and str(start_t).strip() and str(start_t).lower() != "nan" else ""
  clean_end = str(end_t).strip().upper() if pd.notna(end_t) and str(end_t).strip() and str(end_t).lower() != "nan" else ""

  if clean_start and clean_end and "NO WORK" not in clean_start and "NO WORK" not in clean_end and clean_start != "-" and clean_end != "-":
    try:
      def parse_t(t_val):
        t_val = t_val.strip()
        if "PM" in t_val or "AM" in t_val:
          return pd.to_datetime(t_val, format="%I:%M %p")
        elif len(t_val.split(":")) == 3:
          return pd.to_datetime(t_val, format="%H:%M:%S")
        else:
          return pd.to_datetime(t_val, format="%H:%M")

      t_in = parse_t(clean_start)
      t_out = parse_t(clean_end)

      gross_work = (t_out - t_in).total_seconds() / 3600.0
      if gross_work < 0:
        gross_work += 24.0
      gross_work_hours = gross_work

      # Overtime Calculation: Beyond 06:00 PM minus breakdown hours
      cutoff_t = pd.to_datetime("06:00 PM", format="%I:%M %p")
      diff = (t_out - cutoff_t).total_seconds() / 3600.0
      if diff < 0 and "AM" in clean_end:
        diff += 24.0
      ot_hours = round(max(0.0, diff - bd_hours_val), 2)
    except Exception:
      gross_work_hours = 9.0
      ot_hours = 0.0
  elif not is_sunday:
    gross_work_hours = 9.0

  actual_working_hours = round(max(0.0, gross_work_hours - bd_hours_val), 2)

  # COMMERCIAL SHIFT LOGIC:
  # 1. Sunday Rule: Shift is ALWAYS blank / 0 (Paid week-off), only OT beyond 6 PM counts.
  if is_sunday:
    disp_shift = ""
    billable_shift = 0.0
  # 2. Regular Days (Monday-Saturday):
  elif "NO WORK" in rem_upper or "NO WORK" in clean_start:
    disp_shift = "0"
    billable_shift = 0.0
  # 3. Hourly Breakdown Deduction Rule on 9-Hour Base Shift:
  elif bd_hours_val >= 2.0:
    net_shift_hrs = max(0.0, 9.0 - bd_hours_val)
    billable_shift = round(net_shift_hrs / 9.0, 2)
    disp_shift = f"{billable_shift:.2f}" if billable_shift > 0 else "0"
  else:
    disp_shift = "1"
    billable_shift = 1.0

  return disp_shift, ot_hours, run_units, billable_shift, actual_working_hours


def get_tenure_calendar_metrics(start_dt, end_dt):
  total_days = (end_dt - start_dt).days + 1
  sundays_count = 0
  curr = start_dt
  while curr <= end_dt:
    if curr.weekday() == 6:
      sundays_count += 1
    curr += datetime.timedelta(days=1)
  base_shifts = max(1, total_days - sundays_count)
  return total_days, sundays_count, base_shifts


def generate_tenure_statement(vehicle_no, start_dt, end_dt, raw_logs_df):
  if raw_logs_df is None or raw_logs_df.empty or "Vehicle_No" not in raw_logs_df.columns:
    veh_logs = pd.DataFrame()
  else:
    v_clean = str(vehicle_no).strip().upper()
    veh_logs = raw_logs_df[raw_logs_df["Vehicle_No"].astype(str).str.strip().str.upper() == v_clean].copy()
    if not veh_logs.empty and "DATE" in veh_logs.columns:
      veh_logs["_dt"] = veh_logs["DATE"].apply(parse_any_date)

  full_rows = []
  curr = start_dt
  day_idx = 1
  while curr <= end_dt:
    d_str = curr.strftime("%d-%m-%Y")
    is_sun = curr.weekday() == 6

    match = pd.DataFrame()
    if not veh_logs.empty and "_dt" in veh_logs.columns:
      match = veh_logs[veh_logs["_dt"] == curr]

    if not match.empty:
      r = match.iloc[0]
      raw_s = clean_text_cell(r.get("START_TIME", ""))
      raw_e = clean_text_cell(r.get("END_TIME", ""))
      raw_rem = clean_text_cell(r.get("REMARKS", ""))
      raw_inv = clean_text_cell(r.get("INVOLVEMENT", ""))

      disp_s, ot, r_units, _, act_hrs = compute_shift_and_ot(
          d_str,
          raw_s,
          raw_e,
          r.get("METER_START", 0),
          r.get("METER_END", 0),
          r.get("BREAKDOWN_TIME_HRS", 0),
          raw_rem,
      )

      s_12hr = format_12hr_time(raw_s) if raw_s != "-" else "-"
      e_12hr = format_12hr_time(raw_e) if raw_e != "-" else "-"

      try:
        raw_bd = float(r.get("BREAKDOWN_TIME_HRS", 0.0) or 0.0)
        bd_disp = f"{raw_bd:.1f}" if raw_bd > 0 else "0.0"
      except Exception:
        bd_disp = "0.0"

      try:
        ms_val = float(r.get("METER_START", 0.0) or 0.0)
        ms_disp = f"{ms_val:.1f}" if ms_val > 0 else "-"
      except Exception:
        ms_disp = "-"

      try:
        me_val = float(r.get("METER_END", 0.0) or 0.0)
        me_disp = f"{me_val:.1f}" if me_val > 0 else "-"
      except Exception:
        me_disp = "-"

      full_rows.append({
          "Vehicle_No": vehicle_no,
          "SL_NO": str(day_idx),
          "DATE": d_str,
          "START_TIME": s_12hr,
          "END_TIME": e_12hr,
          "NO_OF_SHIFTS": disp_s,
          "OVERTIME_HRS": f"{ot:.2f}" if ot > 0 else "0",
          "METER_START": ms_disp,
          "METER_END": me_disp,
          "TOTAL_RUNNING_KM": f"{r_units:.2f}",
          "ACTUAL_WORK_HRS": f"{act_hrs:.2f}",
          "BREAKDOWN_TIME_HRS": bd_disp,
          "INVOLVEMENT": raw_inv if raw_inv != "-" else "",
          "REMARKS": raw_rem if raw_rem != "-" else ("SUNDAY" if is_sun else "-"),
      })
    else:
      full_rows.append({
          "Vehicle_No": vehicle_no,
          "SL_NO": str(day_idx),
          "DATE": d_str,
          "START_TIME": "-",
          "END_TIME": "-",
          "NO_OF_SHIFTS": "",
          "OVERTIME_HRS": "0",
          "METER_START": "-",
          "METER_END": "-",
          "TOTAL_RUNNING_KM": "0.00",
          "ACTUAL_WORK_HRS": "0.00",
          "BREAKDOWN_TIME_HRS": "0.0",
          "INVOLVEMENT": "-",
          "REMARKS": "SUNDAY" if is_sun else "-",
      })
    curr += datetime.timedelta(days=1)
    day_idx += 1

  return pd.DataFrame(full_rows)


def get_live_do_position(df_master, df_ledger, do_no, target_ra_bill_no=None):
  norm_target = normalize_do(do_no)
  if df_master.empty or "DO_No" not in df_master.columns:
    return pd.DataFrame()

  do_items = df_master[df_master["DO_No"].astype(str).apply(normalize_do) == norm_target].copy()
  positions = []

  for _, row in do_items.iterrows():
    srv_ln = row["SrvLnNo"]
    boq_qty = float(row.get("BOQ_Qty", 0.0) or 0.0)
    init_qty = float(row.get("Initial_Baseline_Qty", 0.0) or 0.0)
    init_amt = float(row.get("Initial_Baseline_Amt", 0.0) or 0.0)

    prior_qty = 0.0
    prior_amt = 0.0
    if not df_ledger.empty and "DO_No" in df_ledger.columns and "SrvLnNo" in df_ledger.columns:
      prior_bills = df_ledger[
          (df_ledger["DO_No"].astype(str).apply(normalize_do) == norm_target)
          & (df_ledger["SrvLnNo"].astype(str) == str(srv_ln))
          & (df_ledger["Status"] == "Approved")
      ]
      if target_ra_bill_no and "RA_Bill_No" in prior_bills.columns:
        prior_bills = prior_bills[prior_bills["RA_Bill_No"].astype(str) != str(target_ra_bill_no)]

      prior_qty = prior_bills["Billed_Qty"].astype(float).sum()
      prior_amt = prior_bills["Billed_Amt"].astype(float).sum()

    prev_qty = round(init_qty + prior_qty, 3)
    prev_amt = round(init_amt + prior_amt, 2)
    balance_qty = round(boq_qty - prev_qty, 3)

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
        "Prev_Qty": prev_qty,
        "Prev_Amt": prev_amt,
        "Balance_BOQ_Qty": balance_qty,
    })

  return pd.DataFrame(positions)


def apply_do_amendment(existing_master_df, new_extracted_items):
  required_cols = [
      "DO_No", "SrvLnNo", "Equipment_Family", "Description", "Unit",
      "BOQ_Qty", "Rate", "OT_Rate", "Fuel_Rate_KM", "Initial_Baseline_Qty", "Initial_Baseline_Amt"
  ]
  if existing_master_df.empty or "DO_No" not in existing_master_df.columns:
    df = pd.DataFrame(columns=required_cols)
  else:
    df = existing_master_df.copy()

  for item in new_extracted_items:
    do_no = item["DO_No"]
    srv_ln = item["SrvLnNo"]
    new_boq_qty = float(item["BOQ_Qty"])
    new_rate = float(item["Rate"])

    if not df.empty and "DO_No" in df.columns:
      match_idx = df[(df["DO_No"].astype(str).apply(normalize_do) == normalize_do(do_no)) & (df["SrvLnNo"].astype(str) == str(srv_ln))].index
    else:
      match_idx = pd.Index([])

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


# ----------------- 3. RE-DESIGNED PROFESSIONAL PDF GENERATOR -----------------
def generate_bill_pdf_report(ra_bill_no, do_no, bill_period, abs_df, total_bill_amt, veh_statements, veh_fuel_meta, market_diesel_rate, base_shifts=26, hold_notes=[]):
  pdf_buffer = io.BytesIO()
  
  doc = SimpleDocTemplate(
      pdf_buffer,
      pagesize=landscape(A4),
      rightMargin=14,
      leftMargin=14,
      topMargin=10,
      bottomMargin=10
  )
  elements = []
  styles = getSampleStyleSheet()

  c_navy = colors.HexColor("#1A365D")
  c_blue = colors.HexColor("#2B6CB0")
  c_border = colors.HexColor("#CBD5E0")
  c_row_alt = colors.HexColor("#F8FAFC")

  comp_title = ParagraphStyle('CompTitle', fontName='Helvetica-Bold', fontSize=15, alignment=1, spaceAfter=2, textColor=c_navy)
  abstract_heading = ParagraphStyle('AbsHeading', fontName='Helvetica-Bold', fontSize=11, alignment=1, spaceAfter=3, textColor=colors.HexColor("#2D3748"))
  sub_meta = ParagraphStyle('SubMeta', fontName='Helvetica', fontSize=7.5, alignment=1, spaceAfter=5, textColor=colors.HexColor("#4A5568"))

  cell_h = ParagraphStyle('CellH', fontName='Helvetica-Bold', fontSize=7.5, alignment=1, textColor=colors.white, leading=9)
  cell_d = ParagraphStyle('CellD', fontName='Helvetica', fontSize=7.2, leading=8.5, alignment=1)
  cell_l = ParagraphStyle('CellL', fontName='Helvetica', fontSize=7.2, leading=8.5, alignment=0)
  cell_r = ParagraphStyle('CellR', fontName='Helvetica', fontSize=7.2, leading=8.5, alignment=2)
  cell_bold_r = ParagraphStyle('CellBR', fontName='Helvetica-Bold', fontSize=7.2, leading=8.5, alignment=2)
  cell_rem = ParagraphStyle('CellRem', fontName='Helvetica', fontSize=7, leading=8, alignment=0)
  hold_style = ParagraphStyle('HoldStyle', fontName='Helvetica-Bold', fontSize=7.5, leading=9, textColor=colors.HexColor("#C53030"))

  # PAGE 1: CONSOLIDATED ABSTRACT
  elements.append(Paragraph("M/S INDU SAO", comp_title))
  elements.append(Paragraph("ABSTRACT", abstract_heading))
  elements.append(Paragraph("COAL PROJECTS, WEST BOKARO | TATA STEEL CONTRACT LEDGER STATEMENT", sub_meta))

  meta_rows = [
      [
          Paragraph(f"<b>Work Order / DO No:</b> {do_no}", cell_l),
          Paragraph(f"<b>RA Bill No:</b> {ra_bill_no}", cell_l),
          Paragraph(f"<b>Billing Period:</b> {bill_period}", cell_l),
          Paragraph(f"<b>Date:</b> {datetime.date.today().strftime('%d-%b-%Y')}", cell_l),
      ],
      [
          Paragraph("<b>Vendor Code:</b> ID47", cell_l),
          Paragraph("<b>Scope:</b> Heavy Equipment & Fleet Hiring", cell_l),
          Paragraph(f"<b>Location:</b> West Bokaro Division (Base Shifts: <b>{base_shifts}</b>)", cell_l),
          Paragraph("<b>Diesel Rate (1st Day):</b> ₹{:,.2f} / Ltr".format(market_diesel_rate) if market_diesel_rate > 0 else "<b>Contract Rate Basis</b>", cell_l),
      ]
  ]
  t_meta = Table(meta_rows, colWidths=[240, 165, 240, 165])
  t_meta.setStyle(TableStyle([
      ('BOX', (0,0), (-1,-1), 1, c_navy),
      ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EDF2F7")),
      ('GRID', (0,0), (-1,-1), 0.5, c_border),
      ('TOPPADDING', (0,0), (-1,-1), 2.5),
      ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
  ]))
  elements.append(t_meta)
  elements.append(Spacer(1, 4))

  sorted_abs_df = abs_df.copy()
  if "W.O SrL No" in sorted_abs_df.columns:
    sorted_abs_df["srl_sort"] = pd.to_numeric(sorted_abs_df["W.O SrL No"], errors="coerce").fillna(999)
    sorted_abs_df = sorted_abs_df.sort_values(by="srl_sort").drop(columns=["srl_sort"])

  headers = ["SrL", "Description of Scope", "Unit", "BOQ Rate", "BOQ Qty", "Prev Qty", "Prev Amount (₹)", "This Qty", "This Amount (₹)", "Cumul Qty", "Cumul Amount (₹)"]
  abs_table_data = [[Paragraph(f"<b>{h}</b>", cell_h) for h in headers]]

  for _, r in sorted_abs_df.iterrows():
    abs_table_data.append([
        Paragraph(str(r["W.O SrL No"]), cell_d),
        Paragraph(str(r["Description"]), cell_l),
        Paragraph(str(r["Unit"]), cell_d),
        Paragraph(f"{float(r['BOQ Rate']):,.2f}", cell_r),
        Paragraph(f"{float(r['BOQ Qty']):,.3f}", cell_d),
        Paragraph(f"{float(r['Prev Qty']):,.3f}", cell_d),
        Paragraph(f"{float(r['Prev Amount']):,.2f}", cell_r),
        Paragraph(f"{float(r['This Bill Qty']):,.3f}", cell_d),
        Paragraph(f"{float(r['This Bill Amount']):,.2f}", cell_bold_r),
        Paragraph(f"{float(r['Cumulative Qty']):,.3f}", cell_d),
        Paragraph(f"{float(r['Cumulative Amount']):,.2f}", cell_bold_r),
    ])

  t_abs = Table(abs_table_data, colWidths=[26, 218, 44, 56, 46, 46, 78, 46, 80, 50, 120])
  t_abs.setStyle(TableStyle([
      ('BACKGROUND', (0,0), (-1,0), c_navy),
      ('GRID', (0,0), (-1,-1), 0.5, c_border),
      ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
      ('TOPPADDING', (0,0), (-1,-1), 3.0),
      ('BOTTOMPADDING', (0,0), (-1,-1), 3.0),
      ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, c_row_alt]),
  ]))
  elements.append(t_abs)
  elements.append(Spacer(1, 4))

  if hold_notes:
    for hn in hold_notes:
      elements.append(Paragraph(f"⚠️ <b>DO RESTRICTION HOLD:</b> {hn}", hold_style))
    elements.append(Spacer(1, 3))

  in_words = num2words(int(round(total_bill_amt)), lang="en_IN").upper()
  total_box = [
      [
          Paragraph(f"<b>AMOUNT IN WORDS:</b> RUPEES {in_words} ONLY", cell_l),
          Paragraph(f"<b>NET INVOICE VALUE: ₹{total_bill_amt:,.2f}</b>", ParagraphStyle('TotNet', parent=cell_bold_r, fontSize=8.5, textColor=c_navy))
      ]
  ]
  t_tot = Table(total_box, colWidths=[570, 240])
  t_tot.setStyle(TableStyle([
      ('BOX', (0,0), (-1,-1), 1, c_navy),
      ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#E2E8F0")),
      ('TOPPADDING', (0,0), (-1,-1), 4.5),
      ('BOTTOMPADDING', (0,0), (-1,-1), 4.5),
  ]))
  elements.append(t_tot)

  elements.append(Spacer(1, 48))

  sig_data = [
      [
          Paragraph("___________________________________<br/><b>Prepared By</b><br/>Site Supervisor", cell_d),
          Paragraph("___________________________________<br/><b>For M/S INDU SAO</b><br/>Authorized Signatory", cell_d),
          Paragraph("___________________________________<br/><b>Verified & Certified By</b><br/>Engineer-in-Charge (Tata Steel Ltd)", cell_d)
      ]
  ]
  t_sig = Table(sig_data, colWidths=[265, 280, 265])
  t_sig.setStyle(TableStyle([
      ('ALIGN', (0,0), (-1,-1), 'CENTER'),
      ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
  ]))
  elements.append(t_sig)

  # PAGE 2 ONWARDS: INDIVIDUAL VEHICLE STATEMENTS
  for veh, s_df in veh_statements.items():
    elements.append(PageBreak())

    meta_info = veh_fuel_meta.get(veh, {"type": "KM_BASED", "factor": 0.0, "m_rate": 0.0, "ot_rate": 0.0, "desc": veh, "hold_qty": 0.0})
    f_type = meta_info.get("type", "KM_BASED")
    f_factor = float(meta_info.get("factor", 0.0) or 0.0)
    m_rate = float(meta_info.get("m_rate", 0.0) or 0.0)
    ot_rate = float(meta_info.get("ot_rate", 0.0) or 0.0)
    veh_desc = meta_info.get("desc", veh)
    v_hold_qty = float(meta_info.get("hold_qty", 0.0) or 0.0)

    elements.append(Paragraph("M/S INDU SAO", ParagraphStyle('VPTitle', parent=comp_title, fontSize=11, spaceAfter=1)))
    elements.append(Paragraph(f"EQUIPMENT DUTY LOG STATEMENT - {veh} [{veh_desc}]", ParagraphStyle('VPHead', parent=abstract_heading, fontSize=9, spaceAfter=2)))
    elements.append(Paragraph(f"Work Order: <b>{do_no}</b> | RA Bill: <b>{ra_bill_no}</b> | Period: <b>{bill_period}</b>", ParagraphStyle('VPPeriod', parent=sub_meta, spaceAfter=3)))

    v_working_shifts = 0.0
    v_ot_hrs = 0.0
    v_meter_run_units = 0.0
    v_bd_hrs = 0.0

    for _, r in s_df.iterrows():
      s_str = str(r.get("NO_OF_SHIFTS", "")).strip()
      try:
        if s_str != "":
          v_working_shifts += float(s_str)
      except Exception:
        pass

      try:
        v_ot_hrs += float(r.get("OVERTIME_HRS", 0) or 0)
      except Exception:
        pass

      try:
        v_meter_run_units += float(r.get("TOTAL_RUNNING_KM", 0) or 0)
      except Exception:
        pass

      try:
        v_bd_hrs += float(r.get("BREAKDOWN_TIME_HRS", 0) or 0)
      except Exception:
        pass

    if f_type == "HOUR_BASED":
      v_litres = round(v_meter_run_units * f_factor, 2)
      v_fuel_claim = round(v_litres * market_diesel_rate, 2)
      col5_text = f"<b>Engine Run:</b> {v_meter_run_units:.2f} Hrs"
      col6_text = f"<b>Cons (@{f_factor:.1f}L/H):</b> {v_litres:.2f} L | <b>Fuel: ₹{v_fuel_claim:,.2f}</b>"
      run_col_header = "Run (HRS)"
    else:
      v_fuel_claim = round(v_meter_run_units * f_factor, 2)
      col5_text = f"<b>Total Run:</b> {v_meter_run_units:.2f} KM"
      col6_text = f"<b>Fuel Claim (@₹{f_factor:.2f}/KM): ₹{v_fuel_claim:,.2f}</b>"
      run_col_header = "Run (KM)"

    v_summary_data = [
        [
            Paragraph(f"<b>Equipment ID:</b> {veh} ({veh_desc})", cell_l),
            Paragraph(f"<b>Shifts Worked:</b> {v_working_shifts:.2f} / {base_shifts}", cell_l),
            Paragraph(f"<b>Breakdown:</b> {v_bd_hrs:.1f} Hrs", cell_l),
            Paragraph(f"<b>Overtime:</b> {v_ot_hrs:.2f} Hrs", cell_l),
            Paragraph(col5_text, cell_l),
            Paragraph(col6_text, cell_bold_r),
        ]
    ]
    t_v_sum = Table(v_summary_data, colWidths=[130, 110, 110, 110, 120, 230])
    t_v_sum.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, c_blue),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EBF8FF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#BEE3F8")),
        ('TOPPADDING', (0,0), (-1,-1), 2.0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.0),
    ]))
    elements.append(t_v_sum)
    elements.append(Spacer(1, 3))

    st_headers = ["SL", "Date", "Shift Start", "Shift End", "Shift", "OT (Hrs)", "Meter In", "Meter Out", run_col_header, "B/D (Hrs)", "Involvement", "Remarks"]
    st_data = [[Paragraph(f"<b>{h}</b>", cell_h) for h in st_headers]]

    for _, row in s_df.iterrows():
      st_data.append([
          Paragraph(clean_text_cell(row.get("SL_NO", "")), cell_d),
          Paragraph(clean_text_cell(row.get("DATE", "")), cell_d),
          Paragraph(clean_text_cell(row.get("START_TIME", "-")), cell_d),
          Paragraph(clean_text_cell(row.get("END_TIME", "-")), cell_d),
          Paragraph(clean_text_cell(row.get("NO_OF_SHIFTS", "")), cell_d),
          Paragraph(clean_text_cell(row.get("OVERTIME_HRS", "0")), cell_d),
          Paragraph(clean_text_cell(row.get("METER_START", "-")), cell_d),
          Paragraph(clean_text_cell(row.get("METER_END", "-")), cell_d),
          Paragraph(clean_text_cell(row.get("TOTAL_RUNNING_KM", "0.00")), cell_d),
          Paragraph(clean_text_cell(row.get("BREAKDOWN_TIME_HRS", "0.0")), cell_d),
          Paragraph(clean_text_cell(row.get("INVOLVEMENT", "-")), cell_l),
          Paragraph(clean_text_cell(row.get("REMARKS", "-")), cell_rem),
      ])

    t_stmt = Table(st_data, colWidths=[22, 58, 64, 64, 34, 44, 58, 58, 54, 48, 110, 196])
    t_stmt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_navy),
        ('GRID', (0,0), (-1,-1), 0.4, c_border),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 1.1),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1.1),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, c_row_alt]),
    ]))
    elements.append(t_stmt)
    elements.append(Spacer(1, 4))

    v_actual_month_qty = round(v_working_shifts / float(base_shifts), 3)
    v_billed_month_qty = max(0.0, round(v_actual_month_qty - v_hold_qty, 3))
    v_hire_amt = round(v_billed_month_qty * m_rate, 2)
    v_ot_amt = round(v_ot_hrs * ot_rate, 2)

    if f_type == "HOUR_BASED":
      v_fuel_qty = round(v_meter_run_units * f_factor, 2)
      v_fuel_uom = "LITER"
      v_fuel_rate_disp = market_diesel_rate
      v_fuel_amt = round(v_fuel_qty * market_diesel_rate, 2)
      fuel_desc_str = f"Billing of Fuel Consumption ({f_factor:.1f} Ltr/Hr)"
    else:
      v_fuel_qty = round(v_meter_run_units, 2)
      v_fuel_uom = "KM"
      v_fuel_rate_disp = f_factor
      v_fuel_amt = round(v_fuel_qty * f_factor, 2)
      fuel_desc_str = f"Billing of Fuel Consumption (₹{f_factor:.2f}/Km)"

    v_total_veh_bill = round(v_hire_amt + v_ot_amt + v_fuel_amt, 2)

    hire_qty_label = f"{v_billed_month_qty:.3f}"
    if v_hold_qty > 0:
      hire_qty_label += f"<br/><font color='#C53030'>({v_hold_qty:.3f} HOLD)</font>"

    mini_headers = ["SRL.NO", "Description of Scope", "UOM", "QTY", "RATE / QTY (₹)", "AMOUNT (₹)"]
    mini_abs_data = [
        [Paragraph(f"<b>{h}</b>", cell_h) for h in mini_headers],
        [
            Paragraph("10", cell_d),
            Paragraph(f"Hiring Charges for {veh_desc}", cell_l),
            Paragraph("Month", cell_d),
            Paragraph(hire_qty_label, cell_d),
            Paragraph(f"{m_rate:,.2f}", cell_r),
            Paragraph(f"{v_hire_amt:,.2f}", cell_bold_r),
        ],
        [
            Paragraph("20", cell_d),
            Paragraph("Overtime (OT) Charges", cell_l),
            Paragraph("HOUR", cell_d),
            Paragraph(f"{v_ot_hrs:.2f}", cell_d),
            Paragraph(f"{ot_rate:,.2f}", cell_r),
            Paragraph(f"{v_ot_amt:,.2f}", cell_bold_r),
        ],
        [
            Paragraph("30", cell_d),
            Paragraph(fuel_desc_str, cell_l),
            Paragraph(v_fuel_uom, cell_d),
            Paragraph(f"{v_fuel_qty:.2f}", cell_d),
            Paragraph(f"{v_fuel_rate_disp:,.2f}", cell_r),
            Paragraph(f"{v_fuel_amt:,.2f}", cell_bold_r),
        ],
        [
            Paragraph("<b>TOTAL</b>", cell_d),
            Paragraph(f"<b>EQUIPMENT BILL VALUE - {veh} ({veh_desc}) (Shifts: {v_working_shifts:.2f}/{base_shifts}, B/D: {v_bd_hrs:.1f} Hrs)</b>", cell_l),
            Paragraph("", cell_d),
            Paragraph("", cell_d),
            Paragraph("", cell_d),
            Paragraph(f"<b>₹{v_total_veh_bill:,.2f}</b>", cell_bold_r),
        ]
    ]

    t_mini_abs = Table(mini_abs_data, colWidths=[45, 395, 60, 75, 110, 125])
    t_mini_abs.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_blue),
        ('GRID', (0,0), (-1,-1), 0.4, c_border),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 1.8),
        ('BOTTOMPADDING', (0,0), (-1,-1), 1.8),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor("#E2E8F0")),
    ]))
    elements.append(t_mini_abs)

  doc.build(elements)
  return pdf_buffer.getvalue()


# ----------------- 4. STREAMLIT APP NAVIGATION -----------------
st.title("Tata Steel Fleet Operations & Billing System")
st.caption("Contractor: M/S INDU SAO (Vendor Code: ID47) | Coal Projects, West Bokaro Division")

tabs = st.tabs([
    "Daily Logsheet Entry",
    "Monthly Statement & Abstract",
    "Delivery Order (DO) Contract Management",
    "Vehicle Fleet Master Mapping",
    "Equipment Rate & Fuel Policy Master",
])

df_master_do = load_sheet_df("master_do_items")
df_vehs = load_sheet_df("master_vehicles")
df_logs = load_sheet_df("fleet_daily_logsheets")
df_ledger = load_sheet_df("ra_bills_ledger")
df_rates = load_sheet_df("fleet_rate_master")

required_log_cols = ["Vehicle_No", "SL_NO", "DATE", "START_TIME", "END_TIME", "NO_OF_SHIFTS", "OVERTIME_HRS", "METER_START", "METER_END", "TOTAL_RUNNING_KM", "ACTUAL_WORK_HRS", "BREAKDOWN_TIME_HRS", "INVOLVEMENT", "REMARKS"]
if df_logs.empty or "Vehicle_No" not in df_logs.columns:
  df_logs = pd.DataFrame(columns=required_log_cols)

required_ledger_cols = ["DO_No", "RA_Bill_No", "Bill_Period", "SrvLnNo", "Billed_Qty", "Billed_Amt", "Status"]
if df_ledger.empty or "DO_No" not in df_ledger.columns:
  df_ledger = pd.DataFrame(columns=required_ledger_cols)

if df_rates.empty or "Category_Name" not in df_rates.columns:
  default_schedule = [
      {"Category_Name": "Backhoe / JCB", "Monthly_Rate": 100000.0, "OT_Rate": 299.0, "Fuel_Mode": "HOUR_BASED", "Fuel_Factor": 5.50, "Unit_Display": "Ltr/Hr"},
      {"Category_Name": "Water Sprinkler (12,000 Litres)", "Monthly_Rate": 108000.0, "OT_Rate": 322.0, "Fuel_Mode": "KM_BASED", "Fuel_Factor": 22.0, "Unit_Display": "₹/Km"},
      {"Category_Name": "Drinking Water Tanker (5,000 Litres)", "Monthly_Rate": 76500.0, "OT_Rate": 229.0, "Fuel_Mode": "KM_BASED", "Fuel_Factor": 16.0, "Unit_Display": "₹/Km"},
      {"Category_Name": "Hywa (12 Tyres)", "Monthly_Rate": 140000.0, "OT_Rate": 374.0, "Fuel_Mode": "KM_BASED", "Fuel_Factor": 40.0, "Unit_Display": "₹/Km"},
      {"Category_Name": "Camper (Support Vehicle)", "Monthly_Rate": 40000.0, "OT_Rate": 90.0, "Fuel_Mode": "KM_BASED", "Fuel_Factor": 9.0, "Unit_Display": "₹/Km"},
      {"Category_Name": "Vibratory Roller (10T Capacity)", "Monthly_Rate": 115000.0, "OT_Rate": 340.0, "Fuel_Mode": "HOUR_BASED", "Fuel_Factor": 9.0, "Unit_Display": "Ltr/Hr"},
      {"Category_Name": "Baby Roller (1T Capacity)", "Monthly_Rate": 31500.0, "OT_Rate": 70.0, "Fuel_Mode": "HOUR_BASED", "Fuel_Factor": 8.0, "Unit_Display": "Ltr/Hr"},
      {"Category_Name": "Excavator (220 Class with Breaker)", "Monthly_Rate": 170000.0, "OT_Rate": 450.0, "Fuel_Mode": "HOUR_BASED", "Fuel_Factor": 10.0, "Unit_Display": "Ltr/Hr"},
      {"Category_Name": "Excavator (300 Class with Breaker)", "Monthly_Rate": 225000.0, "OT_Rate": 590.0, "Fuel_Mode": "HOUR_BASED", "Fuel_Factor": 14.0, "Unit_Display": "Ltr/Hr"},
      {"Category_Name": "F-23 Crane", "Monthly_Rate": 140000.0, "OT_Rate": 300.0, "Fuel_Mode": "HOUR_BASED", "Fuel_Factor": 3.0, "Unit_Display": "Ltr/Hr"},
      {"Category_Name": "Trailer (40 MT)", "Monthly_Rate": 140000.0, "OT_Rate": 299.0, "Fuel_Mode": "KM_BASED", "Fuel_Factor": 40.0, "Unit_Display": "₹/Km"},
      {"Category_Name": "Diesel Bowser (800 Litres Capacity)", "Monthly_Rate": 50000.0, "OT_Rate": 135.0, "Fuel_Mode": "KM_BASED", "Fuel_Factor": 9.0, "Unit_Display": "₹/Km"},
  ]
  df_rates = pd.DataFrame(default_schedule)

rate_dict = {}
for _, r in df_rates.iterrows():
  rate_dict[str(r["Category_Name"]).strip()] = {
      "mode": str(r.get("Fuel_Mode", "KM_BASED")).strip(),
      "factor": float(r.get("Fuel_Factor", 0.0) or 0.0),
      "m_rate": float(r.get("Monthly_Rate", 0.0) or 0.0),
      "ot_rate": float(r.get("OT_Rate", 0.0) or 0.0),
  }

req_veh_cols = ["Vehicle_No", "Equipment_Family", "Mapped_DO_No", "Fuel_Type", "Consumption_Factor"]
for col in req_veh_cols:
  if col not in df_vehs.columns:
    df_vehs[col] = "KM_BASED" if col == "Fuel_Type" else ("0.0" if col == "Consumption_Factor" else "")


# --- TAB 1: DAILY ENTRY (SAFE UPDATE PROTECTION) ---
with tabs[0]:
  st.subheader("Daily Field Duty Slip Entry & Verification")

  with st.expander("📥 Bulk Logsheet Upload (Excel / CSV) & Template Generator", expanded=False):
    st.markdown("##### 1. Generate Pre-Filled Monthly Calendar Template")
    col_sel_v, col_sel_m, col_sel_y = st.columns(3)
    
    vehs_options = {}
    if not df_vehs.empty and "Vehicle_No" in df_vehs.columns:
      for _, vr in df_vehs[df_vehs["Vehicle_No"].astype(str).str.strip() != ""].iterrows():
        v_id = str(vr["Vehicle_No"]).strip()
        v_f = str(vr.get("Equipment_Family", "")).strip()
        disp_txt = f"{v_id} [{v_f}]" if v_f else v_id
        vehs_options[v_id] = disp_txt

    if not vehs_options:
      vehs_options = {"NL01DA1034(ROLLER)": "NL01DA1034(ROLLER) [Vibratory Roller]", "JH02BY7955": "JH02BY7955 [Trailer 40MT]"}

    selected_tpl_key = col_sel_v.selectbox("Target Equipment", options=list(vehs_options.keys()), format_func=lambda x: vehs_options[x], key="tpl_veh_select")
    today = datetime.date.today()
    tpl_month = col_sel_m.selectbox("Billing Month", list(range(1, 13)), index=today.month - 1, key="tpl_month_select")
    tpl_year = col_sel_y.selectbox("Billing Year", [2025, 2026, 2027], index=1, key="tpl_year_select")

    num_days_in_m = calendar.monthrange(tpl_year, tpl_month)[1]
    template_rows = []
    for day in range(1, num_days_in_m + 1):
      d_str = "{:02d}-{:02d}-{}".format(day, tpl_month, tpl_year)
      d_obj = datetime.date(tpl_year, tpl_month, day)
      is_sun = d_obj.weekday() == 6
      template_rows.append({
          "SL_NO": day,
          "Vehicle_No": selected_tpl_key,
          "DATE": d_str,
          "START_TIME": "-" if is_sun else "08:00 AM",
          "END_TIME": "-" if is_sun else "06:00 PM",
          "METER_START": "",
          "METER_END": "",
          "BREAKDOWN_TIME_HRS": 0.0,
          "INVOLVEMENT": "-" if is_sun else "TSL Operations",
          "REMARKS": "SUNDAY" if is_sun else "-",
      })

    buf_tpl = io.BytesIO()
    with pd.ExcelWriter(buf_tpl, engine="openpyxl") as writer:
      pd.DataFrame(template_rows).to_excel(writer, sheet_name=f"{selected_tpl_key[:15]}_Logsheet", index=False)

    m_name_tpl = calendar.month_name[tpl_month].upper()
    st.download_button(
        f"📥 Download Structured Excel Template ({selected_tpl_key} - {m_name_tpl} {tpl_year})",
        data=buf_tpl.getvalue(),
        file_name=f"Logsheet_Template_{selected_tpl_key}_{m_name_tpl}_{tpl_year}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    st.markdown("---")
    st.markdown("##### 2. Upload Completed Logsheet File")
    uploaded_log_file = st.file_uploader("Select Completed Excel / CSV Logsheet", type=["xlsx", "xls", "csv"], key="bulk_log_uploader")

    if uploaded_log_file is not None:
      try:
        if uploaded_log_file.name.endswith(".csv"):
          up_df = pd.read_csv(uploaded_log_file)
        else:
          up_df = pd.read_excel(uploaded_log_file)

        st.write("**Data Preview:**")
        st.dataframe(up_df.head(), use_container_width=True)

        if st.button("🚀 Process & Synchronize Records to Cloud"):
          new_processed_rows = []
          for _, r in up_df.iterrows():
            d_val = clean_text_cell(r.get("DATE", ""))
            if d_val == "-":
              continue
            v_val = clean_text_cell(r.get("Vehicle_No", "")).upper()
            s_t = clean_text_cell(r.get("START_TIME", ""))
            e_t = clean_text_cell(r.get("END_TIME", ""))
            m_s = r.get("METER_START", 0)
            m_e = r.get("METER_END", 0)
            bd = r.get("BREAKDOWN_TIME_HRS", 0.0)
            rem = clean_text_cell(r.get("REMARKS", ""))
            inv = clean_text_cell(r.get("INVOLVEMENT", ""))

            disp_s, ot, r_units, _, act_hrs = compute_shift_and_ot(d_val, s_t, e_t, m_s, m_e, bd, rem)
            parsed_d = parse_any_date(d_val)
            day_str = str(parsed_d.day) if parsed_d else (d_val.split("-")[0] if "-" in d_val else "1")

            new_processed_rows.append({
                "Vehicle_No": v_val,
                "SL_NO": str(day_str),
                "DATE": d_val,
                "START_TIME": format_12hr_time(s_t) if s_t != "-" else "-",
                "END_TIME": format_12hr_time(e_t) if e_t != "-" else "-",
                "NO_OF_SHIFTS": disp_s,
                "OVERTIME_HRS": f"{ot:.2f}" if ot > 0 else "0",
                "METER_START": str(m_s) if str(m_s).lower() != "nan" else "",
                "METER_END": str(m_e) if str(m_e).lower() != "nan" else "",
                "TOTAL_RUNNING_KM": f"{r_units:.2f}",
                "ACTUAL_WORK_HRS": f"{act_hrs:.2f}",
                "BREAKDOWN_TIME_HRS": str(bd) if str(bd).lower() != "nan" else "0.0",
                "INVOLVEMENT": inv if inv != "-" else "",
                "REMARKS": rem if rem != "-" else "",
            })

          if new_processed_rows:
            fresh_logs = load_sheet_df("fleet_daily_logsheets")
            if fresh_logs.empty or "Vehicle_No" not in fresh_logs.columns:
              fresh_logs = pd.DataFrame(columns=required_log_cols)

            new_df = pd.DataFrame(new_processed_rows)
            for _, nr in new_df.iterrows():
              n_v = str(nr["Vehicle_No"]).strip().upper()
              n_d = parse_any_date(nr["DATE"])
              fresh_logs["_chk_d"] = fresh_logs["DATE"].apply(parse_any_date)
              fresh_logs = fresh_logs[~((fresh_logs["Vehicle_No"].astype(str).str.strip().str.upper() == n_v) & (fresh_logs["_chk_d"] == n_d))]
              fresh_logs = fresh_logs.drop(columns=["_chk_d"])

            merged_df = pd.concat([fresh_logs, new_df], ignore_index=True)
            if save_sheet_df("fleet_daily_logsheets", merged_df[required_log_cols]):
              st.success(f"Success: {len(new_processed_rows)} records successfully synced to Google Sheets!")
              st.rerun()
      except Exception as err:
        st.error(f"Error parsing logsheet file: {err}")

  st.markdown("---")

  # SINGLE DUTY SLIP ENTRY FORM
  active_vehs = df_vehs[df_vehs["Vehicle_No"].astype(str).str.strip() != ""]

  if active_vehs.empty:
    st.warning("No vehicles registered. Please configure fleet entries under the 'Vehicle Fleet Master Mapping' tab.")
  else:
    col_t1, col_t2 = st.columns(2)
    selected_veh = col_t1.selectbox(
        "Select Equipment Asset",
        options=list(vehs_options.keys()),
        format_func=lambda x: vehs_options.get(x, x),
        key="daily_form_veh"
    )
    selected_date = col_t2.date_input("Operating Date", datetime.date.today(), key="daily_form_date")
    d_date_str = "{:02d}-{:02d}-{}".format(selected_date.day, selected_date.month, selected_date.year)

    v_match = active_vehs[active_vehs["Vehicle_No"] == selected_veh]
    if not v_match.empty:
      v_row = v_match.iloc[0]
      st.caption("Category: **{}** | Work Order Reference: **{}**".format(v_row.get("Equipment_Family"), v_row.get("Mapped_DO_No")))

    existing_record = pd.DataFrame()
    if not df_logs.empty and "Vehicle_No" in df_logs.columns and "DATE" in df_logs.columns:
      df_logs["_temp_dt"] = df_logs["DATE"].apply(parse_any_date)
      existing_record = df_logs[(df_logs["Vehicle_No"] == selected_veh) & (df_logs["_temp_dt"] == selected_date)]
      df_logs = df_logs.drop(columns=["_temp_dt"])
    is_already_submitted = not existing_record.empty

    if is_already_submitted:
      st.warning("Notice: Existing entry detected for {}. Loaded in edit/correction mode.".format(d_date_str))
      rec = existing_record.iloc[0]
      def_start = format_12hr_time(clean_text_cell(rec.get("START_TIME", "08:00 AM")))
      def_end = format_12hr_time(clean_text_cell(rec.get("END_TIME", "06:00 PM")))
      try:
        def_bd = float(rec.get("BREAKDOWN_TIME_HRS", 0.0) or 0.0)
      except Exception:
        def_bd = 0.0
      try:
        def_mstart = float(rec.get("METER_START", 0) or 0)
      except Exception:
        def_mstart = 0.0
      try:
        def_mend = float(rec.get("METER_END", 0) or 0)
      except Exception:
        def_mend = 0.0
      def_dept = clean_text_cell(rec.get("INVOLVEMENT", ""))
      def_rem = clean_text_cell(rec.get("REMARKS", ""))
    else:
      st.info("Ready for duty log entry | Date: {}".format(d_date_str))
      def_start = "08:00 AM"
      def_end = "06:00 PM"
      def_bd = 0.0
      def_mstart = 0.0
      def_mend = 0.0
      def_dept = ""
      def_rem = ""

    with st.form("duty_form"):
      c1, c2, c3 = st.columns(3)
      s_time = c1.text_input("Shift Start Time (e.g., 08:00 AM)", def_start if def_start != "-" else "08:00 AM")
      e_time = c2.text_input("Shift End Time (e.g., 06:00 PM)", def_end if def_end != "-" else "06:00 PM")
      bd_hrs = c3.number_input("Breakdown Duration (Hours)", value=float(def_bd), step=0.5)

      c4, c5, c6 = st.columns(3)
      m_start = c4.number_input("Meter Start Reading", value=float(def_mstart), min_value=0.0)
      m_end = c5.number_input("Meter End Reading", value=float(def_mend), min_value=0.0)
      dept = c6.text_input("Site Department / Indentor", def_dept if def_dept != "-" else "")

      remarks = st.text_input("Operational Remarks (NORMAL / SUNDAY / BREAKDOWN / IDLE)", def_rem if def_rem != "-" else "")

      btn_label = "Update Existing Duty Record" if is_already_submitted else "Submit & Save Duty Slip"
      if st.form_submit_button(btn_label):
        disp_s, ot, r_units, _, act_hrs = compute_shift_and_ot(d_date_str, s_time, e_time, m_start, m_end, bd_hrs, remarks)

        # Fresh pull from sheets to avoid wiping out other records
        fresh_logs = load_sheet_df("fleet_daily_logsheets")
        if fresh_logs.empty or "Vehicle_No" not in fresh_logs.columns:
          fresh_logs = pd.DataFrame(columns=required_log_cols)

        # Remove ONLY this vehicle on this specific date
        fresh_logs["_date_cmp"] = fresh_logs["DATE"].apply(parse_any_date)
        fresh_logs = fresh_logs[~((fresh_logs["Vehicle_No"].astype(str).str.strip().str.upper() == selected_veh.strip().upper()) & (fresh_logs["_date_cmp"] == selected_date))]
        fresh_logs = fresh_logs.drop(columns=["_date_cmp"])

        day_num = selected_date.day
        new_row = {
            "Vehicle_No": selected_veh,
            "SL_NO": str(day_num),
            "DATE": d_date_str,
            "START_TIME": format_12hr_time(s_time),
            "END_TIME": format_12hr_time(e_time),
            "NO_OF_SHIFTS": disp_s,
            "OVERTIME_HRS": f"{ot:.2f}" if ot > 0 else "0",
            "METER_START": f"{m_start:.1f}",
            "METER_END": f"{m_end:.1f}",
            "TOTAL_RUNNING_KM": f"{r_units:.2f}",
            "ACTUAL_WORK_HRS": f"{act_hrs:.2f}",
            "BREAKDOWN_TIME_HRS": f"{bd_hrs:.1f}",
            "INVOLVEMENT": dept,
            "REMARKS": remarks,
        }
        updated_all_logs = pd.concat([fresh_logs, pd.DataFrame([new_row])], ignore_index=True)
        if save_sheet_df("fleet_daily_logsheets", updated_all_logs[required_log_cols]):
          st.success(f"Duty slip successfully recorded for {selected_veh} on {d_date_str}.")
          st.rerun()


# --- TAB 2: BILLING & STATEMENT (PURE CLEAN DATE PICKERS + RELIABLE DOWNLOADS) ---
with tabs[1]:
  st.subheader("Billing Statement & Financial Abstract Generation")

  available_dos = df_master_do["DO_No"].dropna().unique() if not df_master_do.empty else []
  if len(available_dos) == 0:
    st.info("No active Delivery Orders available. Please upload a contract under 'Delivery Order Management'.")
  else:
    col_d1, col_d2, col_d3, col_d4, col_d5 = st.columns([1.5, 1.0, 1.2, 1.2, 1.3])
    chosen_do = col_d1.selectbox("Work Order / DO Selection", available_dos)
    ra_bill_num = col_d2.text_input("RA Bill Number (e.g., 08)", value="08")

    today = datetime.date.today()
    default_start_dt = today.replace(day=1)
    last_day_m = calendar.monthrange(today.year, today.month)[1]
    default_end_dt = today.replace(day=last_day_m)

    start_date_input = col_d3.date_input("Start Date", value=default_start_dt)
    end_date_input = col_d4.date_input("End Date", value=default_end_dt)

    if start_date_input > end_date_input:
      st.error("Error: Start Date must be on or before End Date.")
      st.stop()

    market_diesel_rate = col_d5.number_input(
        "1st Day Diesel Rate (₹/Ltr)",
        value=101.06,
        step=0.25,
        help="IOCL prevailing rate on tenure start date applicable for heavy machinery fuel claims."
    )

    p_days, p_sundays, base_billable_shifts = get_tenure_calendar_metrics(start_date_input, end_date_input)
    bill_period = "{} TO {}".format(
        start_date_input.strftime("%d-%b-%Y").upper(),
        end_date_input.strftime("%d-%b-%Y").upper()
    )

    norm_chosen_do = normalize_do(chosen_do)

    is_bill_already_locked = False
    if not df_ledger.empty and "DO_No" in df_ledger.columns and "RA_Bill_No" in df_ledger.columns:
      locked_match = df_ledger[
          (df_ledger["DO_No"].astype(str).apply(normalize_do) == norm_chosen_do)
          & (df_ledger["RA_Bill_No"].astype(str) == str(ra_bill_num))
          & (df_ledger["Status"] == "Approved")
      ]
      is_bill_already_locked = not locked_match.empty

    if is_bill_already_locked:
      st.error(f"🔒 **LOCKED COMMERCIAL RECORD**: RA Bill **{ra_bill_num}** for Order **{chosen_do}** is already finalized and committed to the ledger. Editing or regenerating is blocked unless this bill is first Unlocked/Deleted.")

    assigned_vehs_df = df_vehs[df_vehs["Mapped_DO_No"].astype(str).apply(normalize_do) == norm_chosen_do]
    assigned_vehs = assigned_vehs_df["Vehicle_No"].tolist()

    assigned_labels = [f"{v} [{assigned_vehs_df[assigned_vehs_df['Vehicle_No']==v].iloc[0].get('Equipment_Family', '')}]" for v in assigned_vehs]
    
    st.info(
        f"📅 **Tenure:** `{bill_period}` | **Duration:** {p_days} Days | **Sundays Deducted:** {p_sundays} | **Standard Base Shifts:** **{base_billable_shifts} Shifts**\n\n"
        f"🚜 **Attached Equipment Units:** {', '.join(assigned_labels) if assigned_labels else 'None'}"
    )

    live_do_view = get_live_do_position(df_master_do, df_ledger, chosen_do, target_ra_bill_no=ra_bill_num)

    veh_full_statements = {}
    veh_fuel_meta = {}
    veh_metrics = {}

    for v in assigned_vehs:
      v_statement = generate_tenure_statement(v, start_date_input, end_date_input, df_logs)
      veh_full_statements[v] = v_statement

      v_row_match = assigned_vehs_df[assigned_vehs_df["Vehicle_No"] == v]
      f_type = "KM_BASED"
      f_factor = 0.0
      v_fam = "General"
      if not v_row_match.empty:
        f_type = str(v_row_match.iloc[0].get("Fuel_Type", "KM_BASED")).strip()
        f_factor = float(v_row_match.iloc[0].get("Consumption_Factor", 0.0) or 0.0)
        v_fam = str(v_row_match.iloc[0].get("Equipment_Family", "General")).strip()

      rule_preset = rate_dict.get(v_fam, {})
      m_rate_val = rule_preset.get("m_rate", 0.0)
      ot_rate_val = rule_preset.get("ot_rate", 0.0)

      if m_rate_val == 0.0 and not live_do_view.empty:
        m_row = live_do_view[live_do_view["Unit"].astype(str).str.upper().str.contains("MONTH")]
        if not m_row.empty:
          m_rate_val = float(m_row.iloc[0].get("Rate", 0.0) or 0.0)

      if ot_rate_val == 0.0 and not live_do_view.empty:
        ot_row = live_do_view[live_do_view["Unit"].astype(str).str.upper().str.contains("HOUR")]
        if not ot_row.empty:
          ot_rate_val = float(ot_row.iloc[0].get("Rate", 0.0) or 0.0)

      v_shifts = 0.0
      v_ot = 0.0
      v_meter_run = 0.0
      v_bd_hours = 0.0

      for _, r in v_statement.iterrows():
        s_val = str(r.get("NO_OF_SHIFTS", "")).strip()
        try:
          if s_val != "":
            v_shifts += float(s_val)
        except Exception:
          pass

        try:
          v_ot += float(r.get("OVERTIME_HRS", 0) or 0)
        except Exception:
          pass

        try:
          v_meter_run += float(r.get("TOTAL_RUNNING_KM", 0) or 0)
        except Exception:
          pass

        try:
          v_bd_hours += float(r.get("BREAKDOWN_TIME_HRS", 0) or 0)
        except Exception:
          pass

      # Pro-rata based on DYNAMIC billable shifts for the chosen tenure
      v_month_qty = round(v_shifts / float(base_billable_shifts), 3)

      if f_type == "HOUR_BASED":
        v_fuel_cost = round(v_meter_run * f_factor * market_diesel_rate, 2)
      else:
        v_fuel_cost = round(v_meter_run * f_factor, 2)

      veh_metrics[v] = {
          "family": v_fam,
          "shifts": v_shifts,
          "month_qty": v_month_qty,
          "ot_hours": v_ot,
          "run_units": v_meter_run,
          "bd_hours": v_bd_hours,
          "fuel_cost": v_fuel_cost,
          "hold_qty": 0.0,
      }

      veh_fuel_meta[v] = {
          "type": f_type,
          "factor": f_factor,
          "m_rate": m_rate_val,
          "ot_rate": ot_rate_val,
          "desc": v_fam if v_fam != "General" else v,
          "hold_qty": 0.0,
      }

    if assigned_vehs:
      st.markdown("---")
      chosen_view_veh = st.selectbox(
          "Select Equipment Duty Log to Audit & Edit:",
          options=assigned_vehs,
          format_func=lambda x: f"{x} [{assigned_vehs_df[assigned_vehs_df['Vehicle_No']==x].iloc[0].get('Equipment_Family', '')}]" if not assigned_vehs_df[assigned_vehs_df['Vehicle_No']==x].empty else x
      )
      st.write(f"Displaying sequential calendar entries ({start_date_input.strftime('%d-%b')} to {end_date_input.strftime('%d-%b-%Y')}) for **{chosen_view_veh}**.")

      edited_stmt = st.data_editor(
          veh_full_statements[chosen_view_veh],
          num_rows="fixed",
          use_container_width=True,
          key=f"stmt_edit_{chosen_view_veh}",
          disabled=is_bill_already_locked,
      )

      col_st1, col_st2 = st.columns(2)
      if col_st1.button(f"Save Verified Adjustments for {chosen_view_veh}", disabled=is_bill_already_locked):
        fresh_logs = load_sheet_df("fleet_daily_logsheets")
        if fresh_logs.empty or "Vehicle_No" not in fresh_logs.columns:
          fresh_logs = pd.DataFrame(columns=required_log_cols)

        fresh_logs["_dt_filter"] = fresh_logs["DATE"].apply(parse_any_date)
        fresh_logs = fresh_logs[
            ~(
                (fresh_logs["Vehicle_No"].astype(str).str.strip().str.upper() == chosen_view_veh.strip().upper())
                & (fresh_logs["_dt_filter"].apply(lambda d: start_date_input <= d <= end_date_input if d else False))
            )
        ]
        fresh_logs = fresh_logs.drop(columns=["_dt_filter"])

        valid_rows = edited_stmt[(edited_stmt["START_TIME"].astype(str).str.strip() != "") | (edited_stmt["REMARKS"].astype(str).str.strip() != "")]
        merged_adjustments = pd.concat([fresh_logs, valid_rows], ignore_index=True)
        if save_sheet_df("fleet_daily_logsheets", merged_adjustments[required_log_cols]):
          st.success(f"Verified adjustments preserved and synchronized for {chosen_view_veh}.")
          st.rerun()

      csv_buf = edited_stmt.to_csv(index=False).encode('utf-8')
      col_st2.download_button(
          f"Export {chosen_view_veh} Statement (CSV)",
          data=csv_buf,
          file_name=f"Statement_{chosen_view_veh}_{bill_period}.csv",
          mime="text/csv",
      )

    abstract_rows = []
    total_this_bill = 0.0
    hold_notes = []

    for _, row in live_do_view.iterrows():
      sl = row["SrvLnNo"]
      unit = str(row.get("Unit", "")).strip().upper()
      rate = float(row.get("Rate", 0.0) or 0.0)
      desc = str(row.get("Description", "")).upper()
      do_fam = str(row.get("Equipment_Family", "")).upper()
      balance_boq = float(row.get("Balance_BOQ_Qty", 0.0) or 0.0)

      matching_v_entries = [
          (v_name, m) for v_name, m in veh_metrics.items()
          if (m["family"].upper() in desc)
          or (m["family"].upper() in do_fam)
          or (any(word in desc for word in m["family"].upper().split() if len(word) > 2))
      ]

      if not matching_v_entries:
        matching_v_entries = [(v_name, m) for v_name, m in veh_metrics.items()]

      matching_metrics = [m for _, m in matching_v_entries]
      line_shifts_qty = sum(m["month_qty"] for m in matching_metrics)
      line_ot_qty = sum(m["ot_hours"] for m in matching_metrics)
      line_fuel_cost = sum(m["fuel_cost"] for m in matching_metrics)

      hold_qty_for_line = 0.0

      if "MONTH" in unit:
        computed_qty = round(line_shifts_qty, 3)
        if balance_boq > 0 and computed_qty > balance_boq:
          this_qty = round(balance_boq, 3)
          hold_qty_for_line = round(computed_qty - balance_boq, 3)
          hold_notes.append(f"Line {sl} ({row.get('Description')}): Actual Qty {computed_qty:.3f} exceeded available DO balance {balance_boq:.3f}. Billed: {this_qty:.3f} | {hold_qty_for_line:.3f} HOLD due to insufficient DO quantity.")
          for v_name, _ in matching_v_entries:
            if v_name in veh_fuel_meta:
              veh_fuel_meta[v_name]["hold_qty"] = hold_qty_for_line
        else:
          this_qty = computed_qty
        this_amt = round(this_qty * rate, 2)
      elif "HOUR" in unit:
        computed_qty = round(line_ot_qty, 2)
        if balance_boq > 0 and computed_qty > balance_boq:
          this_qty = round(balance_boq, 2)
          hold_qty_for_line = round(computed_qty - balance_boq, 2)
          hold_notes.append(f"Line {sl} (OT): Actual Qty {computed_qty:.2f} exceeded available DO balance {balance_boq:.2f}. Billed: {this_qty:.2f} | {hold_qty_for_line:.2f} HOLD.")
        else:
          this_qty = computed_qty
        this_amt = round(this_qty * rate, 2)
      elif "KM" in unit or "FUEL" in desc or "REIMBUR" in desc or "LUMPSUM" in unit:
        if "LUMPSUM" in unit or rate == 1.0:
          computed_qty = round(line_fuel_cost, 2)
          if balance_boq > 0 and computed_qty > balance_boq:
            this_qty = round(balance_boq, 2)
            hold_qty_for_line = round(computed_qty - balance_boq, 2)
            hold_notes.append(f"Line {sl} (Fuel): Actual Value ₹{computed_qty:,.2f} exceeded DO Balance ₹{balance_boq:,.2f}. Billed: ₹{this_qty:,.2f} | ₹{hold_qty_for_line:,.2f} HOLD.")
          else:
            this_qty = computed_qty
          this_amt = this_qty
        else:
          computed_qty = round(line_fuel_cost / (rate if rate > 0 else 1.0), 2)
          this_qty = computed_qty
          this_amt = round(line_fuel_cost, 2)
      else:
        this_qty = 0.0
        this_amt = 0.0

      cum_qty = round(row["Prev_Qty"] + this_qty, 3)
      cum_amt = round(row["Prev_Amt"] + this_amt, 2)
      total_this_bill += this_amt

      abstract_rows.append({
          "W.O SrL No": sl,
          "Description": row.get("Description", ""),
          "Unit": row.get("Unit", ""),
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
    st.subheader(f"Consolidated Billing Abstract (RA Bill {ra_bill_num})")
    st.dataframe(
        abs_df[["W.O SrL No", "Description", "Unit", "Prev Amount", "This Bill Amount", "Cumulative Amount"]],
        use_container_width=True,
    )

    if hold_notes:
      for hn in hold_notes:
        st.warning(hn)

    in_words = num2words(int(round(total_this_bill)), lang="en_IN").upper()
    st.write(f"**Net Invoice Value:** ₹{total_this_bill:,.2f} | **Amount in Words:** {in_words} RUPEES ONLY")

    def commit_and_lock_bill():
      global df_ledger
      if df_ledger.empty or "DO_No" not in df_ledger.columns:
        df_ledger = pd.DataFrame(columns=required_ledger_cols)
      else:
        df_ledger = df_ledger[~((df_ledger["DO_No"].astype(str).apply(normalize_do) == norm_chosen_do) & (df_ledger["RA_Bill_No"].astype(str) == str(ra_bill_num)))]

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
      df_ledger = pd.concat([df_ledger, pd.DataFrame(new_entries)], ignore_index=True)
      save_sheet_df("ra_bills_ledger", df_ledger)

    clean_do = str(chosen_do).replace("/", "_")
    buf_xl = io.BytesIO()
    with pd.ExcelWriter(buf_xl, engine="openpyxl") as writer:
      abs_df.to_excel(writer, sheet_name="Page1_Abstract", index=False)
      for v, s_df in veh_full_statements.items():
        s_df.to_excel(writer, sheet_name=f"Stmt_{v.replace('-', '_')}"[:31], index=False)
    excel_payload = buf_xl.getvalue()

    pdf_payload = generate_bill_pdf_report(ra_bill_num, chosen_do, bill_period, abs_df, total_this_bill, veh_full_statements, veh_fuel_meta, market_diesel_rate, base_shifts=base_billable_shifts, hold_notes=hold_notes)

    # RELIABLE AUTO-LOCK & DOWNLOAD MODAL
    @st.dialog("Commercial Audit: Lock RA Bill & Release Download")
    def confirm_lock_and_download_modal(doc_type):
      st.markdown(f"### 🔒 Authorize Final Billing Lock")
      st.write(
          f"You are downloading the **{doc_type}** for **RA Bill {ra_bill_num}** ({chosen_do}).\n\n"
          f"**Tenure:** `{bill_period}` ({base_billable_shifts} Base Working Shifts)\n\n"
          "Per Tata Steel contract governance, this bill will be **Committed & Locked** in the commercial ledger to prevent audit discrepancies."
      )

      modal_state_key = f"bill_locked_in_modal_{ra_bill_num}_{clean_do}"
      if modal_state_key not in st.session_state:
        st.session_state[modal_state_key] = False

      if not st.session_state[modal_state_key]:
        col_m1, col_m2 = st.columns(2)
        if col_m1.button("❌ Cancel (Keep Editing)", use_container_width=True):
          st.rerun()

        if col_m2.button("🔒 Confirm Lock & Prepare File", type="primary", use_container_width=True):
          commit_and_lock_bill()
          st.session_state[modal_state_key] = True
          st.rerun()
      else:
        st.success("✅ Bill has been locked in the commercial ledger!")
        if doc_type == "Official PDF":
          st.download_button(
              "⬇️ Click Here to Download Official PDF",
              data=pdf_payload,
              file_name=f"Tata_Official_RA_Bill_{ra_bill_num}_{clean_do}.pdf",
              mime="application/pdf",
              use_container_width=True,
              type="primary"
          )
        else:
          st.download_button(
              "⬇️ Click Here to Download Excel Workbook",
              data=excel_payload,
              file_name=f"Tata_RA_Bill_{ra_bill_num}_{clean_do}.xlsx",
              mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              use_container_width=True,
              type="primary"
          )
        st.caption("You can now close this dialog.")

    col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)

    # 1. Excel Workbook Export
    if is_bill_already_locked:
      col_btn1.download_button(
          "📊 Download Excel Workbook",
          data=excel_payload,
          file_name=f"Tata_RA_Bill_{ra_bill_num}_{clean_do}.xlsx",
          mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      )
    else:
      if col_btn1.button("📊 Export Excel Workbook"):
        confirm_lock_and_download_modal("Excel Workbook")

    # 2. PDF Export
    if is_bill_already_locked:
      col_btn2.download_button(
          "📄 Download Official PDF",
          data=pdf_payload,
          file_name=f"Tata_Official_RA_Bill_{ra_bill_num}_{clean_do}.pdf",
          mime="application/pdf",
      )
    else:
      if col_btn2.button("📄 Generate Official PDF"):
        confirm_lock_and_download_modal("Official PDF")

    # 3. Manual Lock
    if col_btn3.button("🔒 Finalize & Lock Bill", disabled=is_bill_already_locked):
      commit_and_lock_bill()
      st.success(f"RA Bill {ra_bill_num} committed and locked in commercial ledger.")
      st.rerun()

    # 4. Delete / Unlock
    if col_btn4.button("🗑️ Delete / Unlock Bill", help="Unlock this bill from ledger to allow modifications or re-billing."):
      if not df_ledger.empty and "DO_No" in df_ledger.columns and "RA_Bill_No" in df_ledger.columns:
        df_ledger = df_ledger[~((df_ledger["DO_No"].astype(str).apply(normalize_do) == norm_chosen_do) & (df_ledger["RA_Bill_No"].astype(str) == str(ra_bill_num)))]
        if save_sheet_df("ra_bills_ledger", df_ledger):
          modal_state_key = f"bill_locked_in_modal_{ra_bill_num}_{clean_do}"
          if modal_state_key in st.session_state:
            del st.session_state[modal_state_key]
          st.success(f"RA Bill {ra_bill_num} unlocked and removed from ledger.")
          st.rerun()
      else:
        st.info(f"No ledger records found for RA Bill {ra_bill_num}.")


# --- TAB 3: DO UPLOAD & BASELINE SETUP ---
with tabs[2]:
  st.subheader("1. Register / Amend SAP Delivery Order (DO) Contract")
  uploaded_pdf = st.file_uploader("Upload Official Tata Steel Work Order (PDF Format)", type=["pdf"], key="pdf_uploader_clean")

  if uploaded_pdf is not None:
    with pdfplumber.open(uploaded_pdf) as pdf:
      first_page_text = pdf.pages[0].extract_text() or ""

    wo_match = re.search(r"Order\s*No[.:\s]*-*(\d{10}/\d{2,3})", first_page_text, re.IGNORECASE)
    detected_wo = wo_match.group(1).strip() if wo_match else None

    if not detected_wo:
      final_wo = st.text_input("Enter SAP Work Order Reference Manually (e.g., 3700981341/109):").strip()
    else:
      st.success(f"Extracted Order Reference: **{detected_wo}**")
      final_wo = detected_wo

    pattern = r"(\d+)\s+(\d{2})\s+([A-Za-z0-9/\s,.\-#]+?)\s+([\d,]+\.?\d*)\s*(Months?|Hours?|LUMPSUM|KM)\s+([\d,]+\.?\d*)\s*INR"
    matches = re.findall(pattern, first_page_text, re.IGNORECASE)
    parsed_items = []
    for m in matches:
      _, srv_ln, desc, qty_str, unit, rate_str = m
      desc_clean = desc.strip().replace("\n", " ")
      qty = float(qty_str.replace(",", ""))
      rate = float(rate_str.replace(",", ""))

      family = "Backhoe / JCB" if "BACKHOE" in desc_clean.upper() or "JCB" in desc_clean.upper() else ("Camper" if "CAMPER" in desc_clean.upper() else ("Trailor-40MT" if "TRAILOR" in desc_clean.upper() else desc_clean[:20]))
      ot_rate = rate if unit.lower() in ["hours", "hour"] else 0.0

      parsed_items.append({
          "DO_No": final_wo,
          "SrvLnNo": int(srv_ln),
          "Equipment_Family": family,
          "Description": desc_clean,
          "Unit": unit.strip(),
          "BOQ_Qty": qty,
          "Rate": rate,
          "OT_Rate": ot_rate,
          "Fuel_Rate_KM": 0.0,
          "Initial_Baseline_Qty": 0.0,
          "Initial_Baseline_Amt": 0.0,
      })

    if parsed_items and final_wo:
      preview_df = pd.DataFrame(parsed_items)
      st.dataframe(preview_df, use_container_width=True)

      if st.button("Confirm & Apply Contract Scope"):
        df_master_do = apply_do_amendment(df_master_do, parsed_items)
        if save_sheet_df("master_do_items", df_master_do):
          st.success(f"Contract terms registered to Google Sheets for Order {final_wo}.")
          st.rerun()

  st.markdown("---")
  st.subheader("2. Opening Baseline & Rate Ledger Configuration")
  st.caption("Review BOQ values, baseline quantities, and rates across contract line items. Decimal inputs (e.g., 8.072) are fully supported:")

  baseline_columns = [
      "DO_No", "SrvLnNo", "Equipment_Family", "Description", "Unit",
      "BOQ_Qty", "Rate", "OT_Rate", "Fuel_Rate_KM", "Initial_Baseline_Qty", "Initial_Baseline_Amt"
  ]
  for col in baseline_columns:
    if col not in df_master_do.columns:
      df_master_do[col] = 0.0 if ("Qty" in col or "Amt" in col or "Rate" in col) else ""

  num_cols = ["BOQ_Qty", "Rate", "OT_Rate", "Fuel_Rate_KM", "Initial_Baseline_Qty", "Initial_Baseline_Amt"]
  for c in num_cols:
    df_master_do[c] = pd.to_numeric(df_master_do[c].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0).astype(float)

  edited_master = st.data_editor(
      df_master_do[baseline_columns],
      num_rows="dynamic",
      use_container_width=True,
      key="baseline_editor_clean",
      column_config={
          "DO_No": st.column_config.TextColumn("DO Reference", required=True),
          "SrvLnNo": st.column_config.NumberColumn("Line No", step=10),
          "Equipment_Family": st.column_config.TextColumn("Category"),
          "Description": st.column_config.TextColumn("Description"),
          "Unit": st.column_config.TextColumn("Unit"),
          "BOQ_Qty": st.column_config.NumberColumn("BOQ Quantity", step=0.001, format="%.3f"),
          "Rate": st.column_config.NumberColumn("Contract Rate (₹)", step=0.01, format="%.2f"),
          "OT_Rate": st.column_config.NumberColumn("OT Rate (₹)", step=0.01, format="%.2f"),
          "Fuel_Rate_KM": st.column_config.NumberColumn("Fuel Rate (₹/KM)", step=0.01, format="%.2f"),
          "Initial_Baseline_Qty": st.column_config.NumberColumn("Prior Billed Qty (Baseline)", step=0.001, format="%.3f"),
          "Initial_Baseline_Amt": st.column_config.NumberColumn("Prior Billed Amt (Baseline)", step=0.01, format="%.2f"),
      }
  )

  if st.button("💾 Save Contract Baseline Updates", type="primary"):
    for c in num_cols:
      edited_master[c] = pd.to_numeric(edited_master[c].astype(str).str.replace(",", ""), errors="coerce").fillna(0.0).astype(float)

    for idx in edited_master.index:
      b_qty = float(edited_master.at[idx, "Initial_Baseline_Qty"])
      b_rate = float(edited_master.at[idx, "Rate"])
      b_unit = str(edited_master.at[idx, "Unit"]).strip().upper()
      curr_amt = float(edited_master.at[idx, "Initial_Baseline_Amt"])

      if b_qty > 0:
        if "MONTH" in b_unit or "HOUR" in b_unit:
          edited_master.at[idx, "Initial_Baseline_Amt"] = round(b_qty * b_rate, 2)
        elif "LUMPSUM" in b_unit or "KM" in b_unit:
          if b_rate == 1.0 or b_rate == 0.0:
            if curr_amt == 0.0:
              edited_master.at[idx, "Initial_Baseline_Amt"] = round(b_qty, 2)
          else:
            edited_master.at[idx, "Initial_Baseline_Amt"] = round(b_qty * b_rate, 2)

    clean_save_df = edited_master.copy()
    clean_save_df["BOQ_Qty"] = clean_save_df["BOQ_Qty"].apply(lambda x: f"{float(x):.3f}")
    clean_save_df["Initial_Baseline_Qty"] = clean_save_df["Initial_Baseline_Qty"].apply(lambda x: f"{float(x):.3f}")
    clean_save_df["Rate"] = clean_save_df["Rate"].apply(lambda x: f"{float(x):.2f}")
    clean_save_df["OT_Rate"] = clean_save_df["OT_Rate"].apply(lambda x: f"{float(x):.2f}")
    clean_save_df["Fuel_Rate_KM"] = clean_save_df["Fuel_Rate_KM"].apply(lambda x: f"{float(x):.2f}")
    clean_save_df["Initial_Baseline_Amt"] = clean_save_df["Initial_Baseline_Amt"].apply(lambda x: f"{float(x):.2f}")

    if save_sheet_df("master_do_items", clean_save_df):
      st.success("Contract baseline updated successfully! Amounts automatically computed and 3-decimal precision saved.")
      st.rerun()


# --- TAB 4: VEHICLE MASTER MAPPING ---
with tabs[3]:
  st.subheader("Fleet Asset Mapping & Deployment")
  st.caption("Map deployed vehicle registration numbers and asset tags to corresponding delivery orders:")

  with st.expander("➕ Register Deployed Asset to Fleet", expanded=df_vehs.empty):
    col_v1, col_v2 = st.columns(2)
    new_v_no = col_v1.text_input("Asset ID / Registration Number (e.g., JH02BY7955 or EXC-01)").strip().upper()

    available_categories = list(rate_dict.keys())
    new_fam = col_v2.selectbox("Equipment Classification Category", available_categories)

    preset_mode = rate_dict.get(new_fam, {}).get("mode", "KM_BASED")
    preset_factor = rate_dict.get(new_fam, {}).get("factor", 0.0)

    col_v3, col_v4, col_v5 = st.columns(3)
    available_dos_list = df_master_do["DO_No"].dropna().unique().tolist() if not df_master_do.empty else []
    if available_dos_list:
      new_do = col_v3.selectbox("Mapped Contract Delivery Order", available_dos_list)
    else:
      new_do = col_v3.text_input("Delivery Order Reference (e.g., 3700991558/109)").strip()

    new_f_type = col_v4.selectbox("Fuel Computation Basis", ["KM_BASED", "HOUR_BASED"], index=0 if preset_mode == "KM_BASED" else 1)
    new_f_factor = col_v5.number_input("Consumption Factor (₹/KM or Ltr/Hr)", value=float(preset_factor), step=0.1, format="%.2f")

    if st.button("Register Equipment Asset"):
      if not new_v_no:
        st.error("Please enter a valid Equipment Asset ID or Registration Number.")
      elif not df_vehs.empty and new_v_no in df_vehs["Vehicle_No"].astype(str).values:
        st.warning(f"Equipment {new_v_no} is already registered in fleet master.")
      else:
        new_row = pd.DataFrame([{
            "Vehicle_No": new_v_no,
            "Equipment_Family": new_fam,
            "Mapped_DO_No": new_do,
            "Fuel_Type": new_f_type,
            "Consumption_Factor": str(new_f_factor)
        }])
        df_vehs = pd.concat([df_vehs[df_vehs["Vehicle_No"] != ""], new_row], ignore_index=True)
        if save_sheet_df("master_vehicles", df_vehs[req_veh_cols]):
          st.success(f"Equipment {new_v_no} registered successfully.")
          st.rerun()

  st.markdown("---")
  st.write("**Registered Fleet Master Registry (Inline Table Editor):**")

  for col in ["Consumption_Factor"]:
    if col in df_vehs.columns:
      df_vehs[col] = pd.to_numeric(df_vehs[col], errors="coerce").fillna(0.0)

  v_editor = st.data_editor(
      df_vehs[req_veh_cols],
      num_rows="dynamic",
      use_container_width=True,
      key="vehicle_mapper_clean",
      column_config={
          "Vehicle_No": st.column_config.TextColumn("Asset ID / Reg No", required=True),
          "Equipment_Family": st.column_config.TextColumn("Category"),
          "Mapped_DO_No": st.column_config.TextColumn("Contract DO"),
          "Fuel_Type": st.column_config.SelectboxColumn("Fuel Mode", options=["KM_BASED", "HOUR_BASED"]),
          "Consumption_Factor": st.column_config.NumberColumn("Factor (₹/Km or Ltr/Hr)", step=0.1, format="%.2f"),
      }
  )

  if st.button("Save Fleet Registry Updates"):
    valid_vehs = v_editor[v_editor["Vehicle_No"].astype(str).str.strip() != ""]
    if save_sheet_df("master_vehicles", valid_vehs):
      st.success("Fleet registry updated in Google Sheets.")
      st.rerun()


# --- TAB 5: EQUIPMENT RATE & POLICY MASTER ---
with tabs[4]:
  st.subheader("⚙️ Commercial Terms & Machinery Policy Master")
  st.caption(
      "Configure standard hiring rates, overtime terms, and fuel reimbursement norms. "
      "Modifications saved here are automatically applied to subsequent billing calculations."
  )

  for col in ["Monthly_Rate", "OT_Rate", "Fuel_Factor"]:
    if col in df_rates.columns:
      df_rates[col] = pd.to_numeric(df_rates[col], errors="coerce").fillna(0.0)

  edited_rates = st.data_editor(
      df_rates,
      num_rows="dynamic",
      use_container_width=True,
      key="rate_master_editor_live",
      column_config={
          "Category_Name": st.column_config.TextColumn("Equipment Description", required=True),
          "Monthly_Rate": st.column_config.NumberColumn("Monthly Hiring (₹)", format="₹%.2f", step=100.0),
          "OT_Rate": st.column_config.NumberColumn("Overtime Rate (₹/Hr)", format="₹%.2f", step=1.0),
          "Fuel_Mode": st.column_config.SelectboxColumn("Fuel Mode", options=["KM_BASED", "HOUR_BASED"], required=True),
          "Fuel_Factor": st.column_config.NumberColumn("Consumption Factor (₹/Km or Ltr/Hr)", format="%.2f", step=0.1),
          "Unit_Display": st.column_config.TextColumn("Unit Description"),
      }
  )

  if st.button("💾 Save Revised Commercial Terms to Cloud", type="primary"):
    if save_sheet_df("fleet_rate_master", edited_rates):
      st.success("Commercial rate terms synchronized successfully.")
      st.rerun()
