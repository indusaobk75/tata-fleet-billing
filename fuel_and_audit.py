import io
import time
import smtplib
import datetime
import calendar
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pandas as pd
import streamlit as st

try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False

# ----------------- SCHEMAS & BENCHMARKS -----------------
ATTENDANCE_AUDIT_COLS = [
    "Audit_ID", "Timestamp", "Attendance_Date", "Logged_By_User", "Action_Type", "Summary"
]

FUEL_FLEET_COLS = [
    "Vehicle_No", "Machine_Type", "Meter_Type", "Standard_Norm", "Tolerance_Pct",
    "Assigned_Operator_ID", "Assigned_Operator_Name", "Status", "Remarks"
]

FUEL_ISSUE_COLS = [
    "Slip_ID", "Date", "Slip_No", "Vehicle_No", "Machine_Type", "Operator_Name",
    "Meter_Type", "Prev_Reading", "Curr_Reading", "Net_Run", "Liters_Issued",
    "Rate_Per_Liter", "Total_Amount", "Actual_Efficiency", "Expected_Norm",
    "Variance_Liters", "Audit_Status", "Fuel_Source", "Logged_By", "Remarks"
]

FUEL_STOCK_COLS = [
    "Stock_ID", "Date", "Source_Type", "Supplier_Ref", "Liters_Added",
    "Rate_Per_Liter", "Total_Cost", "Logged_By", "Remarks"
]

MACHINE_TYPE_DEFAULTS = {
    "Excavator (e.g. 215L / PC210)": ("HMR (Hours)", 17.5),
    "Hyva Tipper (10/12 Wheeler)": ("KMR (Kilometers)", 3.5),
    "JCB Backhoe Loader (3DX)": ("HMR (Hours)", 6.5),
    "Wheel Loader": ("HMR (Hours)", 14.0),
    "Dozer": ("HMR (Hours)", 22.0),
    "Hydra Crane": ("HMR (Hours)", 5.5),
    "Site Bolero / Pickup / Camper": ("KMR (Kilometers)", 11.5),
    "DG Set / Dewatering Pump": ("HMR (Hours)", 4.5),
    "Other Machine / Vehicle": ("HMR (Hours)", 10.0)
}


# ----------------- 1. 90-DAY ATTENDANCE AUDIT LOGGER -----------------
def log_attendance_activity(load_fn, save_fn, att_date_str, username, action_type, summary_str):
    """Logs who saved/edited attendance and automatically purges records older than 90 days."""
    audit_df = load_fn("attendance_audit_log")
    if audit_df.empty or "Timestamp" not in audit_df.columns:
        audit_df = pd.DataFrame(columns=ATTENDANCE_AUDIT_COLS)

    cutoff_dt = (datetime.datetime.now() - datetime.timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    if not audit_df.empty:
        audit_df = audit_df[audit_df["Timestamp"].astype(str) >= cutoff_dt].copy()

    new_entry = {
        "Audit_ID": f"AUD-{int(time.time())}",
        "Timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Attendance_Date": att_date_str,
        "Logged_By_User": str(username),
        "Action_Type": str(action_type),
        "Summary": str(summary_str)
    }
    updated_df = pd.concat([audit_df, pd.DataFrame([new_entry])], ignore_index=True)
    save_fn("attendance_audit_log", updated_df[ATTENDANCE_AUDIT_COLS])


# ----------------- 2. SITE FUEL EMAIL ALERT (SECRETS-ONLY, NO DEFAULT EMAIL) -----------------
def send_fuel_entry_email_alert(subject_line, html_body):
    """Sends SMTP email alert strictly from st.secrets without any hardcoded default addresses."""
    sender_email = str(st.secrets.get("smtp_sender_email", "")).strip()
    sender_pass = str(st.secrets.get("smtp_app_password", "")).strip()
    recipient_raw = str(st.secrets.get("alert_recipient_email", "")).strip()
    smtp_host = str(st.secrets.get("smtp_server", "smtp.gmail.com")).strip()
    smtp_port = int(st.secrets.get("smtp_port", 465))

    if not sender_email or not sender_pass or not recipient_raw:
        return False

    recipients = [r.strip() for r in recipient_raw.split(",") if r.strip()]
    if not recipients:
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject_line
        msg["From"] = f"M/s INDU SAO Site ERP <{sender_email}>"
        msg["To"] = ", ".join(recipients)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=8) as server:
            server.login(sender_email, sender_pass)
            server.sendmail(sender_email, recipients, msg.as_string())
        return True
    except Exception:
        return False


# ----------------- 3. FUEL AUDIT CALCULATOR & PDF REPORT -----------------
def evaluate_fuel_audit(meter_type, net_run, liters_issued, std_norm, tolerance_pct):
    if net_run <= 0:
        return 0.0, round(liters_issued, 2), "🔴 Meter Error / Zero Run"
    tol_factor = 1.0 + (float(tolerance_pct or 10.0) / 100.0)
    warn_factor = 1.0 + (float(tolerance_pct or 10.0) / 200.0)

    if str(meter_type).startswith("HMR"):
        actual_eff = round(liters_issued / net_run, 2)
        expected_liters = round(net_run * std_norm, 2)
    else:
        actual_eff = round(net_run / liters_issued, 2) if liters_issued > 0 else 0.0
        expected_liters = round(net_run / std_norm, 2) if std_norm > 0 else 0.0

    variance_liters = round(liters_issued - expected_liters, 2)
    if liters_issued > (expected_liters * tol_factor):
        status = f"🔴 PILFERAGE ALERT (+{variance_liters:.1f} L)"
    elif liters_issued > (expected_liters * warn_factor):
        status = f"🟡 Watchlist (+{variance_liters:.1f} L)"
    else:
        status = "🟢 Normal"
    return actual_eff, variance_liters, status


def generate_fuel_audit_pdf(audit_summary_df, period_label, diesel_rate_ref, letterhead_fn):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    letterhead_fn(pdf)

    pdf.set_y(55)
    pdf.set_left_margin(14)
    pdf.set_right_margin(14)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(182, 6, f"FLEET DIESEL CONSUMPTION & ANTI-PILFERAGE AUDIT ({period_label})", align="C", ln=True)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.cell(182, 4.5, f"Generated on: {datetime.date.today().strftime('%d.%m.%Y')}   |   Benchmark Rate: Rs. {diesel_rate_ref:.2f}/L", align="C", ln=True)
    pdf.ln(4)

    col_w = [26, 34, 22, 22, 24, 24, 30]
    headers = ["Vehicle No", "Machine Type", "Net Run", "Expected (L)", "Actual (L)", "Variance (L)", "Est. Loss (Rs)"]

    pdf.set_fill_color(241, 245, 249)
    pdf.set_draw_color(0, 0, 0)
    pdf.set_line_width(0.3)
    pdf.set_font("Helvetica", "B", 8)
    for w, h in zip(col_w, headers):
        pdf.cell(w, 7, h, border=1, align="C", fill=True)
    pdf.ln()

    pdf.set_font("Helvetica", "", 7.8)
    tot_act, tot_var, tot_loss = 0.0, 0.0, 0.0
    for _, r in audit_summary_df.iterrows():
        act_l = float(r.get("Actual_Liters", 0.0) or 0.0)
        exp_l = float(r.get("Expected_Liters", 0.0) or 0.0)
        var_l = float(r.get("Excess_Liters", 0.0) or 0.0)
        loss_rs = float(r.get("Est_Loss_Rs", 0.0) or 0.0)
        tot_act += act_l
        tot_var += max(0.0, var_l)
        tot_loss += max(0.0, loss_rs)

        pdf.set_font("Helvetica", "B", 7.8)
        pdf.cell(col_w[0], 6.5, str(r.get("Vehicle_No", ""))[:14], border=1, align="C")
        pdf.set_font("Helvetica", "", 7.5)
        pdf.cell(col_w[1], 6.5, str(r.get("Machine_Type", ""))[:20], border=1, align="L")
        pdf.cell(col_w[2], 6.5, str(r.get("Net_Run_Disp", "")), border=1, align="C")
        pdf.cell(col_w[3], 6.5, f"{exp_l:.1f}", border=1, align="R")
        pdf.cell(col_w[4], 6.5, f"{act_l:.1f}", border=1, align="R")
        pdf.set_font("Helvetica", "B", 7.8)
        pdf.cell(col_w[5], 6.5, f"{var_l:+.1f}", border=1, align="R")
        pdf.cell(col_w[6], 6.5, f"Rs.{loss_rs:,.0f}", border=1, align="R")
        pdf.ln()

    pdf.set_fill_color(248, 250, 252)
    pdf.set_font("Helvetica", "B", 8.2)
    pdf.cell(col_w[0] + col_w[1] + col_w[2] + col_w[3], 7, "TOTAL SUMMARY:", border=1, align="R", fill=True)
    pdf.cell(col_w[4], 7, f"{tot_act:.1f} L", border=1, align="R", fill=True)
    pdf.cell(col_w[5], 7, f"+{tot_var:.1f} L", border=1, align="R", fill=True)
    pdf.cell(col_w[6], 7, f"Rs.{tot_loss:,.0f}", border=1, align="R", fill=True)
    pdf.ln(14)

    y_s = pdf.get_y()
    pdf.set_xy(14, y_s + 10)
    pdf.set_font("Helvetica", "B", 8.5)
    pdf.cell(60, 5, "Site Fuel In-Charge", align="C")

    pdf.set_xy(136, y_s)
    pdf.cell(60, 5, "For M/s INDU SAO", align="C")
    pdf.set_xy(136, y_s + 10)
    pdf.cell(60, 5, "Authorised Signatory", align="C")

    pdf.set_fill_color(244, 180, 0)
    pdf.rect(0, 288, 210, 2.5, style="F")
    pdf.set_fill_color(56, 163, 241)
    pdf.rect(0, 290.5, 210, 6.5, style="F")

    return bytes(pdf.output())


# ----------------- 4. RENDER SITE FUEL RECORD VIEWS -----------------
def render_fuel_module(current_view, load_fn, save_fn, current_user, current_role, letterhead_fn):
    is_admin = (str(current_role).upper() == "ADMIN")

    if current_view == "Fleet Fuel Benchmark Master":
        st.markdown("#### Fleet Fuel Benchmark Master")
        st.caption("Register vehicles & machines with standard diesel norms (L/hr for HMR or km/L for KMR) and tolerance %")

        fleet_df = load_fn("fuel_fleet_master")
        w_master = load_fn("worker_master")

        op_choices = ["Unassigned"]
        op_id_map = {"Unassigned": ("", "Unassigned")}
        if not w_master.empty and "Worker_ID" in w_master.columns:
            for _, wr in w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].iterrows():
                lbl = f"{wr['Full_Name']} ({wr['Worker_ID']})"
                op_choices.append(lbl)
                op_id_map[lbl] = (str(wr["Worker_ID"]), str(wr["Full_Name"]))

        c_f1, c_f2 = st.columns([1.05, 1.95], gap="large")
        with c_f1:
            with st.container(border=True):
                st.markdown("###### Add / Update Vehicle & Norm")
                v_no = st.text_input("Vehicle / Machine No", placeholder="e.g. JH02-AW-4521 or EX-215L", key="ff_vno").strip().upper()
                m_type = st.selectbox("Machine Category", list(MACHINE_TYPE_DEFAULTS.keys()), key="ff_mtype")
                
                def_m_basis, def_norm_val = MACHINE_TYPE_DEFAULTS[m_type]
                meter_basis = st.selectbox(
                    "Meter Basis", ["HMR (Hours)", "KMR (Kilometers)"],
                    index=0 if def_m_basis.startswith("HMR") else 1, key=f"ff_mb_{m_type}"
                )
                norm_label = "Expected Norm (Liters / Hour)" if meter_basis.startswith("HMR") else "Expected Mileage (KM / Liter)"
                std_norm = st.number_input(norm_label, min_value=0.5, value=float(def_norm_val), step=0.5, key=f"ff_nm_{m_type}_{meter_basis}")
                tol_pct = st.number_input("Pilferage Tolerance (%)", min_value=1.0, max_value=30.0, value=10.0, step=1.0, key="ff_tol")
                sel_op = st.selectbox("Assigned Driver / Operator", op_choices, key="ff_op")
                v_status = st.selectbox("Status", ["ACTIVE", "BREAKDOWN", "INACTIVE"], key="ff_stat")
                v_rem = st.text_input("Remarks", key="ff_rem").strip()

                if st.button("💾 Save Machine Benchmark", type="primary", use_container_width=True):
                    if not v_no:
                        st.error("Vehicle / Machine Number is mandatory.")
                    else:
                        op_id, op_name = op_id_map.get(sel_op, ("", "Unassigned"))
                        new_v = {
                            "Vehicle_No": v_no, "Machine_Type": m_type, "Meter_Type": meter_basis,
                            "Standard_Norm": std_norm, "Tolerance_Pct": tol_pct,
                            "Assigned_Operator_ID": op_id, "Assigned_Operator_Name": op_name,
                            "Status": v_status, "Remarks": v_rem
                        }
                        if not fleet_df.empty and "Vehicle_No" in fleet_df.columns:
                            fleet_df = fleet_df[fleet_df["Vehicle_No"].astype(str).str.upper() != v_no]
                        upd_f = pd.concat([fleet_df, pd.DataFrame([new_v])], ignore_index=True)
                        if save_fn("fuel_fleet_master", upd_f[FUEL_FLEET_COLS]):
                            st.success(f"Saved benchmark for {v_no}.")
                            st.rerun()

        with c_f2:
            with st.container(border=True):
                st.markdown("###### Registered Fleet & Standard Norms")
                if fleet_df.empty or "Vehicle_No" not in fleet_df.columns:
                    st.info("No vehicles registered yet.")
                else:
                    disp_f = fleet_df.copy()
                    disp_f["Norm"] = disp_f.apply(
                        lambda r: f"{r['Standard_Norm']} {'L/hr' if str(r['Meter_Type']).startswith('HMR') else 'km/L'} (±{r['Tolerance_Pct']}%)", axis=1
                    )
                    st.dataframe(disp_f[["Vehicle_No", "Machine_Type", "Meter_Type", "Norm", "Assigned_Operator_Name", "Status"]], use_container_width=True, hide_index=True)

                    if is_admin:
                        dc1, dc2 = st.columns([2.0, 1.0])
                        with dc1:
                            v_del = st.selectbox("Remove Vehicle (Admin Only)", fleet_df["Vehicle_No"].astype(str).tolist(), label_visibility="collapsed")
                        with dc2:
                            if st.button("🗑️ Delete Vehicle", type="secondary", use_container_width=True):
                                clean_f = fleet_df[fleet_df["Vehicle_No"].astype(str) != v_del]
                                if save_fn("fuel_fleet_master", clean_f[FUEL_FLEET_COLS]):
                                    st.success(f"Removed {v_del}.")
                                    st.rerun()

    elif current_view == "Daily Diesel Issue Slip":
        st.markdown("#### Daily Diesel Issue Slip & Bowser Stock Tracker")
        st.caption("Issue diesel with automatic previous meter lookup, instant pilferage alerts, and email notifications")

        fleet_df = load_fn("fuel_fleet_master")
        issue_df = load_fn("fuel_issue_log")
        stock_df = load_fn("fuel_stock_log")

        tot_inward = pd.to_numeric(stock_df["Liters_Added"], errors="coerce").fillna(0.0).sum() if not stock_df.empty else 0.0
        tot_issued = pd.to_numeric(issue_df["Liters_Issued"], errors="coerce").fillna(0.0).sum() if not issue_df.empty else 0.0
        bowser_out = pd.to_numeric(
            issue_df[issue_df["Fuel_Source"].astype(str) == "Site Bowser / Tank"]["Liters_Issued"], errors="coerce"
        ).fillna(0.0).sum() if not issue_df.empty and "Fuel_Source" in issue_df.columns else 0.0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Tanker Stock Purchased", f"{tot_inward:,.1f} L")
        k2.metric("Bowser Issued", f"{bowser_out:,.1f} L")
        k3.metric("Live Bowser Balance", f"{(tot_inward - bowser_out):,.1f} L")
        k4.metric("Total Diesel Consumed", f"{tot_issued:,.1f} L")

        with st.expander("➕ Record Diesel Tanker / Bowser Stock Inward", expanded=False):
            with st.form("fuel_stock_inward_form"):
                sc1, sc2, sc3, sc4 = st.columns(4)
                with sc1:
                    st_date = st.date_input("Purchase Date", value=datetime.date.today())
                with sc2:
                    st_src = st.selectbox("Source Type", ["IndianOil / BPCL Tanker", "Retail Pump Barrel", "Opening Stock"])
                with sc3:
                    st_lit = st.number_input("Diesel Added (Liters)", min_value=10.0, value=2000.0, step=100.0)
                with sc4:
                    st_rt = st.number_input("Rate (₹/L)", min_value=50.0, value=92.5, step=0.5)
                st_ref = st.text_input("Invoice / Challan Ref").strip()

                if st.form_submit_button("Add Stock to Site Bowser", type="primary", use_container_width=True):
                    new_st = {
                        "Stock_ID": f"STK-{int(time.time())}", "Date": st_date.strftime("%Y-%m-%d"),
                        "Source_Type": st_src, "Supplier_Ref": st_ref, "Liters_Added": st_lit,
                        "Rate_Per_Liter": st_rt, "Total_Cost": round(st_lit * st_rt, 2),
                        "Logged_By": current_user, "Remarks": st_ref
                    }
                    upd_st = pd.concat([stock_df, pd.DataFrame([new_st])], ignore_index=True)
                    if save_fn("fuel_stock_log", upd_st[FUEL_STOCK_COLS]):
                        mail_html = (
                            f"<h3>M/s INDU SAO — New Diesel Stock Inward</h3>"
                            f"<p><b>Date:</b> {new_st['Date']} | <b>Source:</b> {st_src}</p>"
                            f"<p><b>Quantity Added:</b> {st_lit:,.1f} Liters @ Rs.{st_rt}/L (Total: Rs.{new_st['Total_Cost']:,.0f})</p>"
                            f"<p><b>Ref:</b> {st_ref or 'N/A'} | <b>Logged By:</b> {current_user}</p>"
                        )
                        send_fuel_entry_email_alert(f"[Site Fuel] Tanker Stock Added: +{st_lit:,.0f} L ({new_st['Date']})", mail_html)
                        st.success(f"Added {st_lit:,.1f} L to Bowser stock!")
                        st.rerun()

        if fleet_df.empty or "Vehicle_No" not in fleet_df.columns:
            st.warning("⚠️ Register vehicles in **Fleet Fuel Benchmark Master** first.")
            return

        active_f = fleet_df[fleet_df["Status"].astype(str).str.upper() == "ACTIVE"].copy()
        if active_f.empty:
            active_f = fleet_df.copy()

        c_slip, c_rec = st.columns([1.15, 1.85], gap="large")
        with c_slip:
            with st.container(border=True):
                st.markdown("###### ⛽ Issue New Diesel Slip")
                v_opts = {f"{r['Vehicle_No']} — {r['Machine_Type']}": str(r["Vehicle_No"]) for _, r in active_f.iterrows()}
                sel_v_lbl = st.selectbox("Vehicle / Machine", list(v_opts.keys()), key="dsl_vsel")
                sel_v = v_opts[sel_v_lbl]
                v_inf = active_f[active_f["Vehicle_No"].astype(str) == sel_v].iloc[0]

                m_basis = str(v_inf.get("Meter_Type", "HMR (Hours)"))
                std_n = float(pd.to_numeric(v_inf.get("Standard_Norm", 10.0), errors="coerce") or 10.0)
                tol_p = float(pd.to_numeric(v_inf.get("Tolerance_Pct", 10.0), errors="coerce") or 10.0)
                def_op = str(v_inf.get("Assigned_Operator_Name", "Unassigned"))

                last_rd = 0.0
                if not issue_df.empty and "Vehicle_No" in issue_df.columns:
                    vh = issue_df[issue_df["Vehicle_No"].astype(str) == sel_v]
                    if not vh.empty:
                        last_rd = float(pd.to_numeric(vh["Curr_Reading"], errors="coerce").fillna(0.0).max())

                dc1, dc2 = st.columns(2)
                with dc1:
                    s_date = st.date_input("Issue Date", value=datetime.date.today(), key="dsl_dt")
                with dc2:
                    s_no = st.text_input("Slip / Coupon No", value=f"DSL-{len(issue_df)+101}", key="dsl_sn").strip()

                op_in = st.text_input("Operator / Driver", value=def_op, key=f"dsl_op_{sel_v}").strip()
                f_src = st.selectbox("Fuel Source", ["Site Bowser / Tank", "Authorized Petrol Pump (Credit)"], key="dsl_src")

                u_lbl = "HMR (Hrs)" if m_basis.startswith("HMR") else "KMR (KM)"
                rc1, rc2 = st.columns(2)
                with rc1:
                    prev_r = st.number_input(f"Prev {u_lbl}", min_value=0.0, value=last_rd, step=1.0, key=f"dsl_pr_{sel_v}")
                with rc2:
                    curr_r = st.number_input(f"Curr {u_lbl}", min_value=0.0, value=last_rd, step=1.0, key=f"dsl_cr_{sel_v}")

                lc1, lc2 = st.columns(2)
                with lc1:
                    lit_out = st.number_input("Liters Issued", min_value=1.0, value=50.0, step=5.0, key="dsl_lit")
                with lc2:
                    rt_out = st.number_input("Rate / Liter (₹)", min_value=50.0, value=92.5, step=0.5, key="dsl_rt")

                net_r = round(curr_r - prev_r, 2)
                act_eff, var_l, live_st = evaluate_fuel_audit(m_basis, net_r, lit_out, std_n, tol_p)
                eff_u = "L/hr" if m_basis.startswith("HMR") else "km/L"

                st.info(f"**Net Run:** `{net_r:.1f} {u_lbl}` | **Avg:** `{act_eff:.2f} {eff_u}` (Norm: {std_n})\n\n**Audit:** {live_st} | **Amount:** `₹{lit_out * rt_out:,.0f}`")
                s_rem = st.text_input("Remarks", key="dsl_rem").strip()

                if st.button("💾 Save Diesel Issue Slip", type="primary", use_container_width=True):
                    if curr_r <= prev_r:
                        st.error(f"Current {u_lbl} must be greater than Previous {u_lbl}!")
                    else:
                        new_slp = {
                            "Slip_ID": f"SLP-{int(time.time())}", "Date": s_date.strftime("%Y-%m-%d"),
                            "Slip_No": s_no, "Vehicle_No": sel_v, "Machine_Type": str(v_inf.get("Machine_Type", "")),
                            "Operator_Name": op_in, "Meter_Type": m_basis, "Prev_Reading": prev_r,
                            "Curr_Reading": curr_r, "Net_Run": net_r, "Liters_Issued": lit_out,
                            "Rate_Per_Liter": rt_out, "Total_Amount": round(lit_out * rt_out, 2),
                            "Actual_Efficiency": act_eff, "Expected_Norm": std_n, "Variance_Liters": var_l,
                            "Audit_Status": live_st, "Fuel_Source": f_src, "Logged_By": current_user, "Remarks": s_rem
                        }
                        upd_iss = pd.concat([issue_df, pd.DataFrame([new_slp])], ignore_index=True)
                        if save_fn("fuel_issue_log", upd_iss[FUEL_ISSUE_COLS]):
                            alert_prefix = "[🔴 PILFERAGE ALERT]" if "PILFERAGE" in live_st else "[Site Fuel]"
                            mail_subj = f"{alert_prefix} Diesel Slip {s_no}: {sel_v} ({lit_out:.0f} L)"
                            mail_body = (
                                f"<h3>M/s INDU SAO — New Diesel Issue Entry</h3>"
                                f"<p><b>Date:</b> {new_slp['Date']} | <b>Slip No:</b> {s_no}</p>"
                                f"<p><b>Vehicle:</b> {sel_v} ({new_slp['Machine_Type']}) | <b>Operator:</b> {op_in}</p>"
                                f"<p><b>Meter ({u_lbl}):</b> {prev_r} → {curr_r} (<b>Net Run:</b> {net_r})</p>"
                                f"<p><b>Diesel Issued:</b> {lit_out:.1f} L @ Rs.{rt_out}/L = <b>Rs.{new_slp['Total_Amount']:,.0f}</b></p>"
                                f"<p><b>Efficiency:</b> {act_eff} {eff_u} (Benchmark: {std_n} {eff_u})</p>"
                                f"<p><b>Audit Status:</b> <b>{live_st}</b></p>"
                                f"<p><b>Source:</b> {f_src} | <b>Logged By:</b> {current_user}</p>"
                            )
                            send_fuel_entry_email_alert(mail_subj, mail_body)
                            st.success(f"Slip {s_no} saved ({live_st})!")
                            st.rerun()

        with c_rec:
            with st.container(border=True):
                st.markdown("###### 📋 Recent Diesel Issue Slips")
                if issue_df.empty or "Slip_ID" not in issue_df.columns:
                    st.info("No diesel slips issued yet.")
                else:
                    disp_i = issue_df.iloc[::-1].head(20).copy()
                    st.dataframe(disp_i[["Date", "Slip_No", "Vehicle_No", "Operator_Name", "Net_Run", "Liters_Issued", "Actual_Efficiency", "Audit_Status"]], use_container_width=True, hide_index=True)

            if is_admin and not issue_df.empty and "Slip_ID" in issue_df.columns:
                with st.expander("🛠️ Admin Only: Edit or Delete Diesel Slips & Stock Entries", expanded=False):
                    st.caption("Only Admin can modify or delete logged fuel slips.")
                    edit_df = issue_df.iloc[::-1].head(30).copy()
                    for nc in ["Prev_Reading", "Curr_Reading", "Liters_Issued", "Rate_Per_Liter"]:
                        edit_df[nc] = pd.to_numeric(edit_df[nc], errors="coerce").fillna(0.0)

                    edited_slips = st.data_editor(
                        edit_df[["Slip_ID", "Date", "Slip_No", "Vehicle_No", "Operator_Name", "Prev_Reading", "Curr_Reading", "Liters_Issued", "Rate_Per_Liter", "Remarks"]],
                        use_container_width=True, hide_index=True, disabled=["Slip_ID", "Vehicle_No"], key="adm_fuel_slip_editor"
                    )

                    ec1, ec2 = st.columns(2)
                    with ec1:
                        if st.button("💾 Save Admin Slip Edits", type="primary", use_container_width=True):
                            for _, er in edited_slips.iterrows():
                                sid = str(er["Slip_ID"])
                                mask = issue_df["Slip_ID"].astype(str) == sid
                                if mask.any():
                                    v_code = str(issue_df.loc[mask, "Vehicle_No"].iloc[0])
                                    m_b = str(issue_df.loc[mask, "Meter_Type"].iloc[0])
                                    s_n = float(pd.to_numeric(issue_df.loc[mask, "Expected_Norm"].iloc[0], errors="coerce") or 10.0)
                                    t_p = 10.0
                                    if not fleet_df.empty and "Vehicle_No" in fleet_df.columns:
                                        fm = fleet_df[fleet_df["Vehicle_No"].astype(str) == v_code]
                                        if not fm.empty:
                                            t_p = float(pd.to_numeric(fm.iloc[0].get("Tolerance_Pct", 10.0), errors="coerce") or 10.0)

                                    pr = float(er["Prev_Reading"] or 0.0)
                                    cr = float(er["Curr_Reading"] or 0.0)
                                    lt = float(er["Liters_Issued"] or 0.0)
                                    rt = float(er["Rate_Per_Liter"] or 0.0)
                                    nr = round(cr - pr, 2)
                                    ae, vl, ast_str = evaluate_fuel_audit(m_b, nr, lt, s_n, t_p)

                                    issue_df.loc[mask, ["Date", "Slip_No", "Operator_Name", "Prev_Reading", "Curr_Reading", "Net_Run", "Liters_Issued", "Rate_Per_Liter", "Total_Amount", "Actual_Efficiency", "Variance_Liters", "Audit_Status", "Remarks"]] = [
                                        str(er["Date"]), str(er["Slip_No"]), str(er["Operator_Name"]), pr, cr, nr, lt, rt, round(lt * rt, 2), ae, vl, ast_str, str(er["Remarks"] or "")
                                    ]

                            if save_fn("fuel_issue_log", issue_df[FUEL_ISSUE_COLS]):
                                st.success("Admin edits saved and re-audited!")
                                st.rerun()

                    with ec2:
                        del_opts = {f"{r['Slip_No']} — {r['Vehicle_No']} ({r['Date']} • {r['Liters_Issued']}L)": str(r["Slip_ID"]) for _, r in edit_df.iterrows()}
                        if del_opts:
                            sel_del_lbl = st.selectbox("Select Slip to Delete", list(del_opts.keys()), label_visibility="collapsed")
                            if st.button("🗑️ Delete Selected Slip", type="secondary", use_container_width=True):
                                clean_iss = issue_df[issue_df["Slip_ID"].astype(str) != del_opts[sel_del_lbl]]
                                if save_fn("fuel_issue_log", clean_iss[FUEL_ISSUE_COLS]):
                                    st.success("Diesel slip deleted by Admin.")
                                    st.rerun()

    elif current_view == "Theft & Pilferage Audit":
        st.markdown("#### Anti-Pilferage & Fleet Fuel Efficiency Audit")
        st.caption("Detect excess diesel consumption, suspicious fuel drops, and meter anomalies across your fleet")

        issue_df = load_fn("fuel_issue_log")
        fleet_df = load_fn("fuel_fleet_master")

        ac1, ac2, ac3 = st.columns([1.0, 1.2, 1.3])
        with ac1:
            aud_year = st.selectbox("Year", [2025, 2026, 2027], index=1, key="aud_yr")
        with ac2:
            aud_month = st.selectbox("Month", ["All Months"] + list(calendar.month_name)[1:], index=datetime.date.today().month, key="aud_mo")
        with ac3:
            v_list = ["All Vehicles"] + (fleet_df["Vehicle_No"].astype(str).tolist() if not fleet_df.empty and "Vehicle_No" in fleet_df.columns else [])
            aud_veh = st.selectbox("Vehicle", v_list, key="aud_veh")

        if issue_df.empty or "Slip_ID" not in issue_df.columns:
            st.info("No diesel issue records available for audit yet.")
            return

        sub_aud = issue_df[issue_df["Date"].astype(str).str.startswith(str(aud_year))].copy()
        if aud_month != "All Months":
            m_idx = list(calendar.month_name).index(aud_month)
            sub_aud = sub_aud[sub_aud["Date"].astype(str).str.startswith(f"{aud_year}-{m_idx:02d}")]
        if aud_veh != "All Vehicles":
            sub_aud = sub_aud[sub_aud["Vehicle_No"].astype(str) == aud_veh]

        if sub_aud.empty:
            st.warning("No diesel slips found for selected period.")
            return

        for nc in ["Net_Run", "Liters_Issued", "Total_Amount", "Variance_Liters", "Rate_Per_Liter", "Expected_Norm"]:
            sub_aud[nc] = pd.to_numeric(sub_aud[nc], errors="coerce").fillna(0.0)

        tot_l = sub_aud["Liters_Issued"].sum()
        tot_c = sub_aud["Total_Amount"].sum()
        alerts_df = sub_aud[sub_aud["Audit_Status"].astype(str).str.contains("PILFERAGE|Error", na=False)]
        exc_l = sub_aud[sub_aud["Variance_Liters"] > 0]["Variance_Liters"].sum()
        avg_r = sub_aud["Rate_Per_Liter"].mean() or 92.5
        est_loss = exc_l * avg_r

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Total Diesel Issued", f"{tot_l:,.1f} L", f"₹{tot_c:,.0f}")
        k2.metric("Pilferage Alerts", f"{len(alerts_df)} Slips", "Exceeds Tolerance")
        k3.metric("Excess Diesel Consumed", f"+{exc_l:,.1f} L", "Above Norm")
        k4.metric("Estimated Leakage Loss", f"₹{est_loss:,.0f}", f"@ ₹{avg_r:.2f}/L")

        summary_rows = []
        for v_no_g, grp in sub_aud.groupby("Vehicle_No"):
            m_type_g = str(grp.iloc[0]["Machine_Type"])
            m_basis_g = str(grp.iloc[0]["Meter_Type"])
            net_r_sum = grp["Net_Run"].sum()
            act_l_sum = grp["Liters_Issued"].sum()
            var_l_sum = grp["Variance_Liters"].sum()
            exp_l_sum = round(act_l_sum - var_l_sum, 1)
            loss_g = max(0.0, var_l_sum) * (grp["Rate_Per_Liter"].mean() or avg_r)
            u_short = "Hrs" if m_basis_g.startswith("HMR") else "KM"

            summary_rows.append({
                "Vehicle_No": v_no_g, "Machine_Type": m_type_g,
                "Net_Run_Disp": f"{net_r_sum:.1f} {u_short}",
                "Expected_Liters": exp_l_sum, "Actual_Liters": round(act_l_sum, 1),
                "Excess_Liters": round(var_l_sum, 1), "Est_Loss_Rs": round(loss_g, 0),
                "Status": "🔴 High Leakage" if var_l_sum > (exp_l_sum * 0.10) else ("🟡 Watch" if var_l_sum > 0 else "🟢 Efficient")
            })

        sum_df = pd.DataFrame(summary_rows)
        with st.container(border=True):
            st.markdown("###### 📊 Vehicle / Machine-Wise Fuel Summary")
            st.dataframe(sum_df, use_container_width=True, hide_index=True)

            if FPDF_AVAILABLE and not sum_df.empty:
                p_lbl = f"{aud_month} {aud_year}" if aud_month != "All Months" else f"Year {aud_year}"
                aud_pdf = generate_fuel_audit_pdf(sum_df, p_lbl, avg_r, letterhead_fn)
                st.download_button(
                    label="📥 Download Fuel & Anti-Pilferage Audit PDF",
                    data=aud_pdf, file_name=f"M_s_INDU_SAO_Fuel_Audit_{aud_year}.pdf",
                    mime="application/pdf", type="primary"
                )

        with st.container(border=True):
            st.markdown("###### 🚨 Flagged Exception Slips")
            flagged = sub_aud[~sub_aud["Audit_Status"].astype(str).str.startswith("🟢")]
            if flagged.empty:
                st.success("Zero pilferage alerts! All slips are within standard norms.")
            else:
                st.dataframe(flagged[["Date", "Slip_No", "Vehicle_No", "Operator_Name", "Net_Run", "Liters_Issued", "Actual_Efficiency", "Expected_Norm", "Variance_Liters", "Audit_Status"]], use_container_width=True, hide_index=True)
