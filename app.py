import calendar
import datetime
import io
import json
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
      st.markdown("### 🔐 Tata Steel Fleet Operations Portal")
      st.caption("M/S INDU SAO (ID47) | Coal Projects, West Bokaro")
      st.markdown("---")

      st.info(
          "**Confidential Internal Workspace**\n\n"
          "This portal contains proprietary commercial data and contract ledger records. "
          "Access is strictly restricted to authorized site supervisors and billing personnel. "
          "Please verify your credentials to proceed."
      )

      with st.form("secure_login_gate"):
        entered_pass = st.text_input(
            "Security Passkey",
            type="password",
            placeholder="Enter authorized passkey...",
        )
        submit_auth = st.form_submit_button("Authenticate & Enter System")

        if submit_auth:
          master_password = st.secrets.get("app_password", "TataSteel@2026")
          if entered_pass == master_password:
            st.session_state.authenticated = True
            st.success("Authentication successful. Loading workspace...")
            st.rerun()
          else:
            st.error("Authentication failed: Invalid security passkey. Access denied.")

    st.stop()

check_access()

st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
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


@st.cache_data(ttl=120, show_spinner=False)
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


def save_sheet_df(worksheet_name, df):
  for attempt in range(3):
    try:
      gc = get_gspread_client()
      sh = gc.open(SHEET_NAME)
      try:
        ws = sh.worksheet(worksheet_name)
      except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=worksheet_name, rows="100", cols="20")
      ws.clear()
      df_clean = df.fillna("")
      ws.update([df_clean.columns.values.tolist()] + df_clean.values.tolist())
      st.cache_data.clear()
      return True
    except Exception as e:
      err_str = str(e)
      if "429" in err_str:
        time.sleep(2 * (attempt + 1))
        continue
      st.error(f"Error saving {worksheet_name}: {e}")
      return False
  return False


# ----------------- 2. UNIVERSAL CALCULATION & ENGINE -----------------
def normalize_do(do_str):
  if not do_str:
    return ""
  return str(do_str).split("/")[0].strip()


def compute_shift_and_ot(date_str, start_t, end_t, m_start, m_end, bd_hrs, remarks):
  rem_upper = str(remarks).upper() if pd.notna(remarks) else ""

  is_sunday = False
  try:
    if datetime.datetime.strptime(str(date_str).strip(), "%d-%m-%Y").weekday() == 6:
      is_sunday = True
  except Exception:
    pass
  if "SUNDAY" in rem_upper:
    is_sunday = True

  bd_hours_val = float(bd_hrs) if pd.notna(bd_hrs) and str(bd_hrs).strip() != "" else 0.0
  is_breakdown = ("BREAKDOWN" in rem_upper) or (bd_hours_val >= 4.0)

  m_s = float(m_start) if pd.notna(m_start) and str(m_start).strip() != "" else 0.0
  m_e = float(m_end) if pd.notna(m_end) and str(m_end).strip() != "" else 0.0
  run_units = max(0.0, m_e - m_s) if (m_e > 0 and m_e >= m_s) else 0.0

  ot_hours = 0.0
  actual_working_hours = 0.0

  if pd.notna(end_t) and str(end_t).strip() and not is_breakdown:
    try:
      clean_start = str(start_t).strip().upper() if pd.notna(start_t) and str(start_t).strip() else "08:00 AM"
      clean_end = str(end_t).strip().upper()

      def parse_t(t_val):
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
      actual_working_hours = max(0.0, gross_work - bd_hours_val)

      cutoff_t = pd.to_datetime("06:00 PM", format="%I:%M %p")
      diff = (t_out - cutoff_t).total_seconds() / 3600.0
      if diff < 0 and "AM" in clean_end:
        diff += 24.0
      ot_hours = round(max(0.0, diff - bd_hours_val), 2)
    except Exception:
      ot_hours = 0.0
      actual_working_hours = 8.0 if not is_breakdown else 0.0
  elif not is_breakdown and not is_sunday:
    actual_working_hours = 8.0

  if is_breakdown:
    disp_shift = "0"
    billable_shift = 0.0
    actual_working_hours = 0.0
  elif is_sunday:
    disp_shift = ""
    billable_shift = 0.0
    actual_working_hours = 0.0
  else:
    disp_shift = "1"
    billable_shift = 1.0

  return disp_shift, ot_hours, run_units, billable_shift, actual_working_hours


def generate_full_month_statement(vehicle_no, year, month, raw_logs_df):
  num_days = calendar.monthrange(year, month)[1]
  veh_logs = raw_logs_df[raw_logs_df["Vehicle_No"].astype(str).str.strip().str.upper() == str(vehicle_no).strip().upper()].copy()

  full_rows = []
  for day in range(1, num_days + 1):
    d_str = "{:02d}-{:02d}-{}".format(day, month, year)
    d_obj = datetime.date(year, month, day)
    is_sun = d_obj.weekday() == 6

    match = veh_logs[veh_logs["DATE"] == d_str]

    if not match.empty:
      r = match.iloc[0]
      disp_s, ot, r_units, _, act_hrs = compute_shift_and_ot(
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
          "TOTAL_RUNNING_KM": str(r_units),
          "ACTUAL_WORK_HRS": str(round(act_hrs, 2)),
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
          "ACTUAL_WORK_HRS": "0.0",
          "BREAKDOWN_TIME_HRS": "0.0",
          "INVOLVEMENT": "",
          "REMARKS": "SUNDAY" if is_sun else "",
      })

  return pd.DataFrame(full_rows)


def get_live_do_position(df_master, df_ledger, do_no, target_ra_bill_no=None):
  norm_target = normalize_do(do_no)
  do_items = df_master[df_master["DO_No"].astype(str).apply(normalize_do) == norm_target].copy()
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
          (df_ledger["DO_No"].astype(str).apply(normalize_do) == norm_target)
          & (df_ledger["SrvLnNo"].astype(str) == str(srv_ln))
          & (df_ledger["Status"] == "Approved")
      ]
      if target_ra_bill_no:
        prior_bills = prior_bills[prior_bills["RA_Bill_No"].astype(str) != str(target_ra_bill_no)]

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


# ----------------- 3. PROFESSIONAL PDF GENERATOR (LANDSCAPE FULL FILL) -----------------
def generate_bill_pdf_report(ra_bill_no, do_no, bill_period, abs_df, total_bill_amt, veh_statements, veh_fuel_meta, market_diesel_rate):
  pdf_buffer = io.BytesIO()
  
  # Landscape A4 with optimized margins
  doc = SimpleDocTemplate(
      pdf_buffer,
      pagesize=landscape(A4),
      rightMargin=14,
      leftMargin=14,
      topMargin=12,
      bottomMargin=12
  )
  elements = []
  styles = getSampleStyleSheet()

  c_navy = colors.HexColor("#1A365D")
  c_blue = colors.HexColor("#2B6CB0")
  c_border = colors.HexColor("#CBD5E0")
  c_row_alt = colors.HexColor("#F7FAFC")

  comp_title = ParagraphStyle('CompTitle', fontName='Helvetica-Bold', fontSize=15, alignment=1, spaceAfter=2, textColor=c_navy)
  abstract_heading = ParagraphStyle('AbsHeading', fontName='Helvetica-Bold', fontSize=11, alignment=1, spaceAfter=4, textColor=colors.HexColor("#2D3748"))
  sub_meta = ParagraphStyle('SubMeta', fontName='Helvetica', fontSize=7.5, alignment=1, spaceAfter=6, textColor=colors.HexColor("#4A5568"))

  cell_h = ParagraphStyle('CellH', fontName='Helvetica-Bold', fontSize=8, alignment=1, textColor=colors.white, leading=9.5)
  cell_d = ParagraphStyle('CellD', fontName='Helvetica', fontSize=7.5, leading=9, alignment=1)
  cell_l = ParagraphStyle('CellL', fontName='Helvetica', fontSize=7.5, leading=9, alignment=0)
  cell_r = ParagraphStyle('CellR', fontName='Helvetica', fontSize=7.5, leading=9, alignment=2)
  cell_bold_r = ParagraphStyle('CellBR', fontName='Helvetica-Bold', fontSize=7.5, leading=9, alignment=2)

  # ================= PAGE 1: CONSOLIDATED ABSTRACT =================
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
          Paragraph("<b>Location:</b> West Bokaro Division", cell_l),
          Paragraph("<b>Diesel Rate (1st Day):</b> ₹{:,.2f} / Ltr".format(market_diesel_rate) if market_diesel_rate > 0 else "<b>Contract Rate Basis</b>", cell_l),
      ]
  ]
  t_meta = Table(meta_rows, colWidths=[240, 165, 240, 165])
  t_meta.setStyle(TableStyle([
      ('BOX', (0,0), (-1,-1), 1, c_navy),
      ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EDF2F7")),
      ('GRID', (0,0), (-1,-1), 0.5, c_border),
      ('TOPPADDING', (0,0), (-1,-1), 3),
      ('BOTTOMPADDING', (0,0), (-1,-1), 3),
  ]))
  elements.append(t_meta)
  elements.append(Spacer(1, 6))

  headers = ["SrL", "Description of Scope", "Unit", "BOQ Rate", "BOQ Qty", "Prev Qty", "Prev Amount (₹)", "This Qty", "This Amount (₹)", "Cumul Qty", "Cumul Amount (₹)"]
  abs_table_data = [[Paragraph(f"<b>{h}</b>", cell_h) for h in headers]]

  for _, r in abs_df.iterrows():
    abs_table_data.append([
        Paragraph(str(r["W.O SrL No"]), cell_d),
        Paragraph(str(r["Description"]), cell_l),
        Paragraph(str(r["Unit"]), cell_d),
        Paragraph(f"{float(r['BOQ Rate']):,.2f}", cell_r),
        Paragraph(f"{float(r['BOQ Qty']):,.2f}", cell_d),
        Paragraph(f"{float(r['Prev Qty']):,.2f}", cell_d),
        Paragraph(f"{float(r['Prev Amount']):,.2f}", cell_r),
        Paragraph(f"{float(r['This Bill Qty']):,.2f}", cell_d),
        Paragraph(f"{float(r['This Bill Amount']):,.2f}", cell_bold_r),
        Paragraph(f"{float(r['Cumulative Qty']):,.2f}", cell_d),
        Paragraph(f"{float(r['Cumulative Amount']):,.2f}", cell_bold_r),
    ])

  t_abs = Table(abs_table_data, colWidths=[28, 222, 45, 55, 45, 45, 75, 45, 80, 50, 90])
  t_abs.setStyle(TableStyle([
      ('BACKGROUND', (0,0), (-1,0), c_navy),
      ('GRID', (0,0), (-1,-1), 0.5, c_border),
      ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
      ('TOPPADDING', (0,0), (-1,-1), 4),
      ('BOTTOMPADDING', (0,0), (-1,-1), 4),
      ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, c_row_alt]),
  ]))
  elements.append(t_abs)
  elements.append(Spacer(1, 6))

  in_words = num2words(int(total_bill_amt), lang="en_IN").upper()
  total_box = [
      [
          Paragraph(f"<b>AMOUNT IN WORDS:</b> RUPEES {in_words} ONLY", cell_l),
          Paragraph(f"<b>NET INVOICE VALUE: ₹{total_bill_amt:,.2f}</b>", ParagraphStyle('TotNet', parent=cell_bold_r, fontSize=8.5, textColor=c_navy))
      ]
  ]
  t_tot = Table(total_box, colWidths=[580, 230])
  t_tot.setStyle(TableStyle([
      ('BOX', (0,0), (-1,-1), 1, c_navy),
      ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#E2E8F0")),
      ('TOPPADDING', (0,0), (-1,-1), 5),
      ('BOTTOMPADDING', (0,0), (-1,-1), 5),
  ]))
  elements.append(t_tot)
  elements.append(Spacer(1, 35))

  sig_data = [
      [
          Paragraph("____________________________<br/><b>Prepared By</b><br/>Site Supervisor", cell_d),
          Paragraph("____________________________<br/><b>For M/S INDU SAO</b><br/>Authorized Signatory", cell_d),
          Paragraph("____________________________<br/><b>Verified & Certified By</b><br/>Engineer-in-Charge (Tata Steel Ltd)", cell_d)
      ]
  ]
  t_sig = Table(sig_data, colWidths=[265, 280, 265])
  t_sig.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER')]))
  elements.append(t_sig)

  # ================= PAGE 2 ONWARDS: INDIVIDUAL VEHICLE STATEMENTS (LANDSCAPE FULL FILL) =================
  for veh, s_df in veh_statements.items():
    elements.append(PageBreak())

    elements.append(Paragraph("M/S INDU SAO", ParagraphStyle('VPTitle', parent=comp_title, fontSize=12, spaceAfter=1)))
    elements.append(Paragraph(f"EQUIPMENT DUTY LOG STATEMENT - {veh}", ParagraphStyle('VPHead', parent=abstract_heading, fontSize=9.5, spaceAfter=2)))
    elements.append(Paragraph(f"Work Order: <b>{do_no}</b> | RA Bill: <b>{ra_bill_no}</b> | Period: <b>{bill_period}</b>", ParagraphStyle('VPPeriod', parent=sub_meta, spaceAfter=4)))

    # 1. Summary Strip Placed AT THE TOP of the Statement
    v_working_shifts = sum(1 for _, r in s_df.iterrows() if str(r.get("NO_OF_SHIFTS", "")).strip() == "1")
    v_ot_hrs = sum(float(r.get("OVERTIME_HRS", 0) or 0) for _, r in s_df.iterrows())
    v_run_units = sum(float(r.get("TOTAL_RUNNING_KM", 0) or 0) for _, r in s_df.iterrows())
    v_act_work_hrs = sum(float(r.get("ACTUAL_WORK_HRS", 0) or 0) for _, r in s_df.iterrows())
    v_bd_hrs = sum(float(r.get("BREAKDOWN_TIME_HRS", 0) or 0) for _, r in s_df.iterrows())

    meta_info = veh_fuel_meta.get(veh, {"type": "KM_BASED", "factor": 0.0})
    f_type = meta_info.get("type", "KM_BASED")
    f_factor = float(meta_info.get("factor", 0.0) or 0.0)

    if f_type == "HOUR_BASED":
      v_litres = round(v_act_work_hrs * f_factor, 2)
      v_fuel_claim = round(v_litres * market_diesel_rate, 2)
      col5_text = f"<b>Engine Run:</b> {v_act_work_hrs:.1f} Hrs"
      col6_text = f"<b>Cons (@{f_factor:.1f}L/H):</b> {v_litres:.1f} L | <b>Fuel Claim (@₹{market_diesel_rate:.2f}): ₹{v_fuel_claim:,.2f}</b>"
    else:
      v_fuel_claim = round(v_run_units * f_factor, 2)
      col5_text = f"<b>Total Run:</b> {v_run_units:.1f} KM"
      col6_text = f"<b>Fuel Claim (@₹{f_factor:.2f}/KM): ₹{v_fuel_claim:,.2f}</b>"

    v_summary_data = [
        [
            Paragraph(f"<b>Vehicle ID:</b> {veh}", cell_l),
            Paragraph(f"<b>Shifts Worked:</b> {v_working_shifts} / 26", cell_l),
            Paragraph(f"<b>Breakdown:</b> {v_bd_hrs:.1f} Hrs", cell_l),
            Paragraph(f"<b>Overtime:</b> {v_ot_hrs:.1f} Hrs", cell_l),
            Paragraph(col5_text, cell_l),
            Paragraph(col6_text, cell_bold_r),
        ]
    ]
    t_v_sum = Table(v_summary_data, colWidths=[120, 120, 110, 110, 120, 230])
    t_v_sum.setStyle(TableStyle([
        ('BOX', (0,0), (-1,-1), 1, c_blue),
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#EBF8FF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#BEE3F8")),
        ('TOPPADDING', (0,0), (-1,-1), 2.5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.5),
    ]))
    elements.append(t_v_sum)
    elements.append(Spacer(1, 4))

    # 2. Sequential Calendar Statement Table (Landscape - Balanced Widths totaling 810 pt)
    st_headers = ["SL", "Date", "Shift Start", "Shift End", "Shift", "OT (Hrs)", "Meter In", "Meter Out", "Run (KM)", "B/D (Hrs)", "Involvement", "Remarks"]
    st_data = [[Paragraph(f"<b>{h}</b>", cell_h) for h in st_headers]]

    for _, row in s_df.iterrows():
      st_data.append([
          Paragraph(str(row.get("SL_NO", "")), cell_d),
          Paragraph(str(row.get("DATE", "")), cell_d),
          Paragraph(str(row.get("START_TIME", "")), cell_d),
          Paragraph(str(row.get("END_TIME", "")), cell_d),
          Paragraph(str(row.get("NO_OF_SHIFTS", "")), cell_d),
          Paragraph(str(row.get("OVERTIME_HRS", "0")), cell_d),
          Paragraph(str(row.get("METER_START", "0")), cell_d),
          Paragraph(str(row.get("METER_END", "0")), cell_d),
          Paragraph(str(row.get("TOTAL_RUNNING_KM", "0.0")), cell_d),
          Paragraph(str(row.get("BREAKDOWN_TIME_HRS", "0.0")), cell_d),
          Paragraph(str(row.get("INVOLVEMENT", ""))[:20], cell_l),
          Paragraph(str(row.get("REMARKS", ""))[:24], cell_l),
      ])

    # Widths totaling 810 pt - Fits full landscape width perfectly
    t_stmt = Table(st_data, colWidths=[24, 60, 68, 68, 40, 48, 62, 62, 54, 54, 135, 135])
    t_stmt.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), c_navy),
        ('GRID', (0,0), (-1,-1), 0.4, c_border),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 2.0),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2.0),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, c_row_alt]),
    ]))
    elements.append(t_stmt)

  doc.build(elements)
  return pdf_buffer.getvalue()


# ----------------- 4. STREAMLIT APP NAVIGATION -----------------
st.title("Tata Steel Fleet Billing & DO Ledger System")
st.caption("Vendor: M/S INDU SAO (ID47) | Coal Projects, West Bokaro")

tabs = st.tabs([
    "Daily Logsheet Entry",
    "Monthly Statement & Abstract",
    "DO Upload & Baseline Setup",
    "Vehicle Master Mapping",
    "Equipment Rate Master",
])

df_master_do = load_sheet_df("master_do_items")
df_vehs = load_sheet_df("master_vehicles")
df_logs = load_sheet_df("fleet_daily_logsheets")
df_ledger = load_sheet_df("ra_bills_ledger")
df_rates = load_sheet_df("fleet_rate_master")

# Default Rate Schedule if empty
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


# --- TAB 1: DAILY ENTRY ---
with tabs[0]:
  st.subheader("Daily Field Duty Slip Entry & Verification")
  active_vehs = df_vehs[df_vehs["Vehicle_No"].astype(str).str.strip() != ""]

  if active_vehs.empty:
    st.warning("No vehicles registered. Please configure fleet entries in 'Vehicle Master Mapping' tab.")
  else:
    col_t1, col_t2 = st.columns(2)
    selected_veh = col_t1.selectbox("Select Deployed Vehicle / Asset ID", active_vehs["Vehicle_No"].unique(), key="daily_form_veh")
    selected_date = col_t2.date_input("Select Operating Date", datetime.date.today(), key="daily_form_date")
    d_date_str = "{:02d}-{:02d}-{}".format(selected_date.day, selected_date.month, selected_date.year)

    v_match = active_vehs[active_vehs["Vehicle_No"] == selected_veh]
    if not v_match.empty:
      v_row = v_match.iloc[0]
      st.caption("Asset Category: **{}** | Work Order Reference: **{}**".format(v_row.get("Equipment_Family"), v_row.get("Mapped_DO_No")))

    existing_record = pd.DataFrame()
    if not df_logs.empty:
      existing_record = df_logs[(df_logs["Vehicle_No"] == selected_veh) & (df_logs["DATE"] == d_date_str)]
    is_already_submitted = not existing_record.empty

    if is_already_submitted:
      st.warning("Notice: Existing duty log detected for Date {}. Form is loaded in Correction / Overwrite mode.".format(d_date_str))
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
      e_time = c2.text_input("Shift End Time (OT applies after 06:00 PM)", def_end)
      bd_hrs = c3.number_input("Breakdown Duration (Hours)", value=def_bd, step=0.5)

      c4, c5, c6 = st.columns(3)
      m_start = c4.number_input("Odometer / Hour Meter Start Reading", value=def_mstart, min_value=0)
      m_end = c5.number_input("Odometer / Hour Meter End Reading", value=def_mend, min_value=0)
      dept = c6.text_input("Site Indentor / Dept (Optional)", def_dept)

      remarks = st.text_input("Operation Remarks (SUNDAY / NORMAL / BREAKDOWN / IDLE)", def_rem)

      btn_label = "Update / Override Existing Entry" if is_already_submitted else "Submit & Log Duty Slip"
      if st.form_submit_button(btn_label):
        disp_s, ot, r_units, _, act_hrs = compute_shift_and_ot(d_date_str, s_time, e_time, m_start, m_end, bd_hrs, remarks)

        if is_already_submitted:
          df_logs = df_logs[~((df_logs["Vehicle_No"] == selected_veh) & (df_logs["DATE"] == d_date_str))]

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
            "TOTAL_RUNNING_KM": str(r_units),
            "ACTUAL_WORK_HRS": str(round(act_hrs, 2)),
            "BREAKDOWN_TIME_HRS": str(bd_hrs),
            "INVOLVEMENT": dept,
            "REMARKS": remarks,
        }
        df_logs = pd.concat([df_logs, pd.DataFrame([new_row])], ignore_index=True)
        if save_sheet_df("fleet_daily_logsheets", df_logs):
          st.success(f"Duty slip recorded to Google Sheets for vehicle {selected_veh}.")
          st.rerun()


# --- TAB 2: BILLING & MONTHLY STATEMENT ---
with tabs[1]:
  st.subheader("Monthly Statement Generation & Financial Abstract")

  available_dos = df_master_do["DO_No"].dropna().unique() if not df_master_do.empty else []
  if len(available_dos) == 0:
    st.info("No active Delivery Orders available. Please upload or register an Order under 'DO Upload' tab.")
  else:
    col_d1, col_d2, col_d3, col_d4 = st.columns(4)
    chosen_do = col_d1.selectbox("Select Target Work Order / DO", available_dos)
    ra_bill_num = col_d2.text_input("RA Bill Number (e.g., 08)", value="08")

    today = datetime.date.today()
    bill_m = col_d3.selectbox("Billing Month", list(range(1, 13)), index=today.month - 1)
    bill_y = col_d3.selectbox("Billing Year", [2025, 2026, 2027], index=1)

    market_diesel_rate = col_d4.number_input(
        "1st Day Diesel Market Rate (₹/Ltr)",
        value=92.50,
        step=0.25,
        help="Excavator / JCB etc. ke liye har mahine ki 1st tarikh ka official rate"
    )

    m_name = calendar.month_name[bill_m].upper()
    num_days = calendar.monthrange(bill_y, bill_m)[1]
    bill_period = "01-{}-{} TO {:02d}-{}-{}".format(m_name[:3], bill_y, num_days, m_name[:3], bill_y)

    norm_chosen_do = normalize_do(chosen_do)
    assigned_vehs_df = df_vehs[df_vehs["Mapped_DO_No"].astype(str).apply(normalize_do) == norm_chosen_do]
    assigned_vehs = assigned_vehs_df["Vehicle_No"].tolist()

    st.write("**Billing Tenure:** `{}` | **Attached Equipment Units:** {}".format(bill_period, ", ".join(assigned_vehs) if assigned_vehs else "None"))

    live_do_view = get_live_do_position(df_master_do, df_ledger, chosen_do, target_ra_bill_no=ra_bill_num)

    veh_full_statements = {}
    veh_fuel_meta = {}
    total_group_billable_shifts = 0.0
    total_group_ot = 0.0
    total_calculated_fuel_amount = 0.0

    for v in assigned_vehs:
      v_statement = generate_full_month_statement(v, bill_y, bill_m, df_logs)
      veh_full_statements[v] = v_statement

      v_row_match = assigned_vehs_df[assigned_vehs_df["Vehicle_No"] == v]
      f_type = "KM_BASED"
      f_factor = 0.0
      if not v_row_match.empty:
        f_type = str(v_row_match.iloc[0].get("Fuel_Type", "KM_BASED")).strip()
        f_factor = float(v_row_match.iloc[0].get("Consumption_Factor", 0.0) or 0.0)

      veh_fuel_meta[v] = {"type": f_type, "factor": f_factor}

      v_run_units = 0.0
      v_work_hours = 0.0
      for _, r in v_statement.iterrows():
        _, ot_val, run_u, b_shift, act_h = compute_shift_and_ot(
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
        v_run_units += run_u
        v_work_hours += act_h

      if f_type == "HOUR_BASED":
        total_calculated_fuel_amount += round(v_work_hours * f_factor * market_diesel_rate, 2)
      else:
        total_calculated_fuel_amount += round(v_run_units * f_factor, 2)

    if assigned_vehs:
      st.markdown("---")
      chosen_view_veh = st.selectbox("Select Equipment Logsheet to Review & Edit:", assigned_vehs)
      st.info("Displaying sequential calendar statement (Days 1 to {}) for {}.".format(num_days, chosen_view_veh))

      edited_stmt = st.data_editor(
          veh_full_statements[chosen_view_veh],
          num_rows="fixed",
          use_container_width=True,
          key="stmt_edit_{}".format(chosen_view_veh),
      )

      col_st1, col_st2 = st.columns(2)
      if col_st1.button("Save Verified Adjustments for {}".format(chosen_view_veh)):
        df_logs = df_logs[
            ~(
                (df_logs["Vehicle_No"] == chosen_view_veh)
                & (df_logs["DATE"].astype(str).str.endswith("-{:02d}-{}".format(bill_m, bill_y)))
            )
        ]
        valid_rows = edited_stmt[(edited_stmt["START_TIME"].str.strip() != "") | (edited_stmt["REMARKS"].str.strip() != "")]
        df_logs = pd.concat([df_logs, valid_rows], ignore_index=True)
        if save_sheet_df("fleet_daily_logsheets", df_logs):
          st.success("Google Sheets synchronized successfully.")
          st.rerun()

      csv_buf = edited_stmt.to_csv(index=False).encode('utf-8')
      col_st2.download_button(
          f"Download {chosen_view_veh} Statement (CSV)",
          data=csv_buf,
          file_name=f"Statement_{chosen_view_veh}_{bill_period}.csv",
          mime="text/csv",
      )

    num_vehs = max(1, len(assigned_vehs))
    standard_month_shifts = 26.0 * num_vehs
    month_qty = round(min(standard_month_shifts, total_group_billable_shifts) / standard_month_shifts * num_vehs, 3)

    abstract_rows = []
    total_this_bill = 0.0

    for _, row in live_do_view.iterrows():
      sl = row["SrvLnNo"]
      unit = str(row.get("Unit", "")).strip().upper()
      rate = float(row.get("Rate", 0.0) or 0.0)
      desc = str(row.get("Description", "")).upper()

      if "MONTH" in unit:
        this_qty = month_qty
        this_amt = round(this_qty * rate, 2)
      elif "HOUR" in unit:
        this_qty = total_group_ot
        this_amt = round(this_qty * rate, 2)
      elif "KM" in unit or "FUEL" in desc or "REIMBUR" in desc or "LUMPSUM" in unit:
        if "LUMPSUM" in unit or rate == 1.0:
          this_qty = round(total_calculated_fuel_amount, 2)
          this_amt = this_qty
        else:
          this_qty = round(total_calculated_fuel_amount / (rate if rate > 0 else 1.0), 2)
          this_amt = round(total_calculated_fuel_amount, 2)
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
    st.subheader("RA Bill {} Billing Abstract".format(ra_bill_num))
    st.dataframe(
        abs_df[["W.O SrL No", "Description", "Unit", "Prev Amount", "This Bill Amount", "Cumulative Amount"]],
        use_container_width=True,
    )

    in_words = num2words(int(total_this_bill), lang="en_IN").upper()
    st.write("**Net Invoice Value:** ₹{:,.2f} | **In Words:** {} RUPEES ONLY".format(total_this_bill, in_words))

    col_btn1, col_btn2, col_btn3 = st.columns(3)

    buf_xl = io.BytesIO()
    with pd.ExcelWriter(buf_xl, engine="openpyxl") as writer:
      abs_df.to_excel(writer, sheet_name="Page1_Abstract", index=False)
      for v, s_df in veh_full_statements.items():
        s_df.to_excel(writer, sheet_name=f"Stmt_{v.replace('-', '_')}"[:31], index=False)

    clean_do = str(chosen_do).replace("/", "_")
    col_btn1.download_button(
        "📊 Download Excel Package",
        data=buf_xl.getvalue(),
        file_name=f"Tata_RA_Bill_{ra_bill_num}_{clean_do}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    pdf_bytes = generate_bill_pdf_report(ra_bill_num, chosen_do, bill_period, abs_df, total_this_bill, veh_full_statements, veh_fuel_meta, market_diesel_rate)
    col_btn2.download_button(
        "📄 Download Official PDF (Abstract + Statements)",
        data=pdf_bytes,
        file_name=f"Tata_Official_RA_Bill_{ra_bill_num}_{clean_do}.pdf",
        mime="application/pdf",
    )

    if col_btn3.button("Lock RA Bill {}".format(ra_bill_num)):
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
      if save_sheet_df("ra_bills_ledger", df_ledger):
        st.success("RA Bill committed to Google Sheets ledger!")
        st.rerun()


# --- TAB 3: DO UPLOAD ---
with tabs[2]:
  st.subheader("1. Register / Amend Delivery Order (DO) Contract")
  uploaded_pdf = st.file_uploader("Upload Official Tata Steel DO (PDF Format)", type=["pdf"], key="pdf_uploader_clean")

  if uploaded_pdf is not None:
    with pdfplumber.open(uploaded_pdf) as pdf:
      first_page_text = pdf.pages[0].extract_text() or ""

    wo_match = re.search(r"Order\s*No[.:\s]*-*(\d{10}/\d{2,3})", first_page_text, re.IGNORECASE)
    detected_wo = wo_match.group(1).strip() if wo_match else None

    if not detected_wo:
      final_wo = st.text_input("Enter SAP Work Order Number Manually (e.g., 3700981341/109):").strip()
    else:
      st.success("Extracted Order Number: **{}**".format(detected_wo))
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
          st.success("Contract terms saved to Google Sheets for Order {}.".format(final_wo))
          st.rerun()

  st.markdown("---")
  st.subheader("2. Contract Opening Baseline Configuration")
  st.caption("Edit rates, BOQ, baseline or set custom Fuel_Rate_KM per line item:")
  edited_master = st.data_editor(df_master_do, num_rows="dynamic", key="baseline_editor_clean")
  if st.button("Save Contract Baseline"):
    if save_sheet_df("master_do_items", edited_master):
      st.success("Contract baseline updated in Google Sheets.")
      st.rerun()


# --- TAB 4: VEHICLE MASTER MAPPING ---
with tabs[3]:
  st.subheader("Fleet Asset Mapping")
  st.caption("Map deployed vehicle registration numbers or Asset IDs to contract DO with their fuel consumption mode:")

  with st.expander("➕ Add New Vehicle to Fleet (Auto-Detect Terms)", expanded=df_vehs.empty):
    col_v1, col_v2 = st.columns(2)
    new_v_no = col_v1.text_input("Asset ID / Reg No (e.g., JH02BY7955 or EXC-01)").strip().upper()

    available_categories = list(rate_dict.keys())
    new_fam = col_v2.selectbox("Equipment Category (PDF Schedule)", available_categories)

    preset_mode = rate_dict.get(new_fam, {}).get("mode", "KM_BASED")
    preset_factor = rate_dict.get(new_fam, {}).get("factor", 0.0)

    col_v3, col_v4, col_v5 = st.columns(3)
    available_dos_list = df_master_do["DO_No"].dropna().unique().tolist() if not df_master_do.empty else []
    if available_dos_list:
      new_do = col_v3.selectbox("Mapped Tata Steel DO / WO", available_dos_list)
    else:
      new_do = col_v3.text_input("Mapped DO / WO No (e.g., 3700991558/109)").strip()

    new_f_type = col_v4.selectbox("Fuel Mode", ["KM_BASED", "HOUR_BASED"], index=0 if preset_mode == "KM_BASED" else 1)
    new_f_factor = col_v5.number_input("Consumption Factor (₹/KM or Ltr/Hr)", value=float(preset_factor), step=0.5)

    if st.button("Save & Register Vehicle"):
      if not new_v_no:
        st.error("Kripya Asset ID / Vehicle Number darj karein.")
      elif not df_vehs.empty and new_v_no in df_vehs["Vehicle_No"].astype(str).values:
        st.warning(f"Vehicle {new_v_no} pehle se registered hai.")
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
          st.success(f"Vehicle {new_v_no} registered successfully!")
          st.rerun()

  st.markdown("---")
  st.write("**Registered Fleet Vehicles (Edit fuel mode & factor directly in table below):**")

  v_editor = st.data_editor(
      df_vehs[req_veh_cols],
      num_rows="dynamic",
      use_container_width=True,
      key="vehicle_mapper_clean",
  )

  if st.button("Update Fleet Registry Changes"):
    valid_vehs = v_editor[v_editor["Vehicle_No"].astype(str).str.strip() != ""]
    if save_sheet_df("master_vehicles", valid_vehs):
      st.success("Fleet registry updated in Google Sheets.")
      st.rerun()


# --- TAB 5: EQUIPMENT RATE & POLICY MASTER ---
with tabs[4]:
  st.subheader("⚙️ Fleet Categories & Fuel Policy Master (No Code Editing Needed)")
  st.caption(
      "Yahan se aap kisi bhi machine ka Monthly Hiring, OT Rate, Fuel Mode (KM/Hour) "
      "aur Consumption Factor directly edit kar sakte hain. 'Save' dabate hi aage ke sabhi bills naye rate se banenge."
  )

  edited_rates = st.data_editor(
      df_rates,
      num_rows="dynamic",
      use_container_width=True,
      key="rate_master_editor_live",
      column_config={
          "Category_Name": st.column_config.TextColumn("Equipment Description", required=True),
          "Monthly_Rate": st.column_config.NumberColumn("Monthly Hiring (₹)", format="₹%d"),
          "OT_Rate": st.column_config.NumberColumn("Overtime Rate (₹/Hr)", format="₹%.2f"),
          "Fuel_Mode": st.column_config.SelectboxColumn("Fuel Mode", options=["KM_BASED", "HOUR_BASED"], required=True),
          "Fuel_Factor": st.column_config.NumberColumn("Consumption Factor (₹/Km or Ltr/Hr)", format="%.2f"),
          "Unit_Display": st.column_config.TextColumn("Terms Label"),
      }
  )

  if st.button("💾 Save Revised Master Rates to Cloud", type="primary"):
    if save_sheet_df("fleet_rate_master", edited_rates):
      st.success("Master rates Google Sheets me update ho gaye!")
      st.rerun()
