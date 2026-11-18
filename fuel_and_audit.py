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
                                f"<p><b>Meter ({u_lbl}):</b> {prev_r} &rarr; {curr_r} (<b>Net Run:</b> {net_r})</p>"
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
                st.dataframe(flagged[["Date", "Slip_No", "Vehicle_No", "Operator_Name", "Net_Run", "Liters_Issued", "Actual_Efficiency", "Expected_Norm", "Variance_Liters", "Audit_Status"]], use_container_width=True, hide_index=True)import io
import os
import json
import time
import re
import base64
import datetime
import calendar
import pandas as pd
import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

from fuel_and_audit import (
    ATTENDANCE_AUDIT_COLS,
    FUEL_FLEET_COLS,
    FUEL_ISSUE_COLS,
    FUEL_STOCK_COLS,
    log_attendance_activity,
    render_fuel_module
)

# Safe optional library imports
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
    from PIL import Image, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

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
    "ADDRESS": "Office Address: Sondiha, Chainpur, Dist Ramgarh, (Jharkhand)",
    "PIN_CALL": "Pin No. 829134   Call 9234779896, 9661152652",
    "EMAIL": "Email: indusao_bk@yahoo.com",
    "GST": "GST No. 20AUOPK2982K1ZC",
    "VENDOR": "Vendor Code - 47",
    "ISO": "ISO Certified Company 45001:2018",
    "CURRENT_AC": "482025110000105"
}

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

# ----------------- 1. DATABASE & SECRETS CONFIGURATION -----------------
SHEET_NAME = st.secrets.get("test_spreadsheet_name", st.secrets.get("spreadsheet_name", "Tata_Steel_ERP_TEST_DB"))

USER_COLUMNS = [
    "Username", "Password", "Full_Name", "Role", "Allowed_Tabs", "Status",
    "Contact_No", "Emergency_Contact", "Photo_Base64"
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

@st.cache_resource
def get_gspread_client():
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if "gcp_json_raw" in st.secrets:
        creds_info = json.loads(st.secrets["gcp_json_raw"])
    elif "gcp_service_account" in st.secrets:
        creds_info = dict(st.secrets["gcp_service_account"])
    else:
        raise KeyError("Service account configuration missing from application secrets.")
    credentials = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(credentials)

@st.cache_data(ttl=5, show_spinner=False)
def load_sheet_df(worksheet_name):
    schema_map = {
        "worker_master": WORKER_MASTER_COLS,
        "muster_roll": MUSTER_ROLL_COLS,
        "worker_advances": ADVANCE_LOG_COLS,
        "tata_form_xvii": TATA_COMPLIANCE_COLS,
        "app_users": USER_COLUMNS,
        "wage_rate_master": WAGE_MASTER_COLS,
        "fuel_fleet_master": FUEL_FLEET_COLS,
        "fuel_issue_log": FUEL_ISSUE_COLS,
        "fuel_stock_log": FUEL_STOCK_COLS,
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

def get_active_wage_rates_df():
    df = load_sheet_df("wage_rate_master")
    if df.empty or "Skill_Category" not in df.columns:
        return pd.DataFrame(DEFAULT_WAGE_RATES)
    existing_cats = df["Skill_Category"].astype(str).tolist()
    missing = [d for d in DEFAULT_WAGE_RATES if d["Skill_Category"] not in existing_cats]
    if missing:
        df = pd.concat([df, pd.DataFrame(missing)], ignore_index=True)
    for col in ["Basic_Pay", "VDA_Allowance", "Total_Daily_Rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df

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
                            
                    if s_idx != -1:
                        name = " ".join(pre_days[:s_idx])
                    else:
                        name = " ".join(pre_days)
                        
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


# ----------------- 2. PAYROLL & ADVANCE CARRY-FORWARD ENGINE -----------------
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
                    "Worker_ID": wid,
                    "Worker_Name": wname,
                    "Division": wdiv,
                    "Designation": wrole,
                    "Skill_Category": wskill,
                    "Wage_Type": wwtype,
                    "Base_Rate": wrate,
                    "Week_Off_Day": w_wo,
                    "Status": str(w.get("Status", "ACTIVE")).upper(),
                    "Bank_Account_No": b_acc,
                    "Bank_IFSC_Code": b_ifsc,
                    "Bank_Branch_Details": b_branch,
                    "Punch_Days": punch_days,
                    "Non_Punch_Days": non_punch_days,
                    "Paid_Leave_Days": paid_leave_days,
                    "Total_Days": total_paid_days,
                    "OT_Hours": ot_hrs,
                    "Form_XVII_Days": f17_days,
                    "Form_XVII_Net": f17_net,
                    "Gross_Wages": gross_total,
                    "PF_Deduction": pf_ded,
                    "ESIC_Deduction": esic_ded,
                    "Opening_Advance": opening_adv,
                    "Month_New_Advance": new_adv,
                    "Total_Advance_Due": total_adv_due,
                    "Advance_Deducted": adv_deducted,
                    "Closing_Carry_Forward": closing_adv,
                    "Non_Punch_Net_Pay": non_punch_take_home,
                    "Net_Payable": total_net_payable
                }

    return results_for_target


# ----------------- 3. DIRECT PDF GENERATORS (LETTERHEAD, ID CARDS & SALARY SLIPS) -----------------
def build_exact_indu_sao_oval_logo_stream():
    if not PIL_AVAILABLE:
        return None
    W, H = 660, 450
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    draw.ellipse([12, 10, W - 12, H - 10], fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), width=14)
    navy = (12, 24, 108, 255)
    black = (10, 10, 10, 255)
    white = (255, 255, 255, 255)

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
    else:
        pdf.set_fill_color(255, 255, 255)
        pdf.set_draw_color(0, 0, 0)
        pdf.ellipse(x=8, y=4, w=48, h=30, style="DF")

    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_xy(7, 36.5)
    pdf.cell(54, 4, "ISO Certified Company 45001:2018", align="L")

    pdf.set_xy(64, 4.5)
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(138, 8, "M/s INDU SAO", ln=True)

    pdf.set_font("Helvetica", "B", 8.5)
    info_lines = [
        "Office Address: Sondiha, Chainpur, Dist Ramgarh, (Jharkhand)",
        "Pin No. 829134   Call 9234779896, 9661152652",
        "Email: indusao_bk@yahoo.com",
        "GST No. 20AUOPK2982K1ZC",
        "Vendor Code - 47"
    ]
    for line in info_lines:
        pdf.set_x(64)
        pdf.cell(138, 4.2, line, ln=True)


def generate_bank_authority_letter_pdf(bank_batch_type, records_list, cheque_no, cheque_date):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    draw_indu_sao_letterhead(pdf)

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
        f"{COMPANY_HEADER['CURRENT_AC']} to following account as per the payment details given "
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


def generate_atm_id_cards_pdf(workers_df):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    card_w = 85.6
    card_h = 54.0
    margin_x = 14.0
    margin_y = 14.0
    gap_x = 10.8
    gap_y = 16.0

    records = workers_df.to_dict(orient="records")
    for idx, emp in enumerate(records):
        slot = idx % 8
        if slot == 0:
            pdf.add_page()

        col = slot % 2
        row = slot // 2
        x0 = margin_x + col * (card_w + gap_x)
        y0 = margin_y + row * (card_h + gap_y)

        pdf.set_draw_color(100, 116, 139)
        pdf.set_line_width(0.35)
        pdf.rect(x0, y0, card_w, card_h, style="D")

        pdf.set_fill_color(56, 163, 241)
        pdf.rect(x0, y0, card_w, 12.5, style="F")
        pdf.set_fill_color(244, 180, 0)
        pdf.rect(x0, y0 + 12.5, card_w, 1.3, style="F")

        card_logo_stream = build_exact_indu_sao_oval_logo_stream()
        if card_logo_stream:
            pdf.image(card_logo_stream, x=x0 + 2.2, y=y0 + 1.8, w=12.8, h=8.8)
        else:
            pdf.set_fill_color(255, 255, 255)
            pdf.set_draw_color(0, 0, 0)
            pdf.ellipse(x0 + 2.5, y0 + 2.0, 12.0, 8.5, style="DF")

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
                img_bytes = base64.b64decode(photo_b64)
                img_stream = io.BytesIO(img_bytes)
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


def generate_compact_salary_slips_pdf(slip_records, wage_period_label):
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)

    slip_w = 186.0
    slip_h = 86.0
    margin_x = 12.0
    margin_y = 10.0
    gap_y = 9.0

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

        logo_s = build_exact_indu_sao_oval_logo_stream()
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


# ----------------- 4. HIERARCHICAL WORKSPACES & RBAC -----------------
TAB_REGISTRY = {
    "➕ Employee Enrolment": "Attendance",
    "📋 Employee Master Register": "Attendance",
    "⚖️ Statutory Wage Rates": "Attendance",
    "Daily Muster Roll (Field Attendance)": "Attendance",
    "Monthly Attendance Matrix": "Attendance",
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

TAB_ALIASES = {
    "Worker Daily Muster Roll": "Daily Muster Roll (Field Attendance)",
    "Driver & Operator Pool": "📋 Employee Master Register",
    "Worker & Crew Directory": "📋 Employee Master Register",
    "Monthly Attendance Summary": "Monthly Wages & Payout Summary",
    "Advance & Khuraki Register": "Advance & Expense Register"
}

WORKSPACES_TREE = [
    {
        "id": "Attendance",
        "icon": "👷",
        "title": "Worker Attendance",
        "submenus": [
            ("Employee Enrolment", "➕ Employee Enrolment"),
            ("Master Register & ID Cards", "📋 Employee Master Register"),
            ("Statutory Wage Rates", "⚖️ Statutory Wage Rates"),
            ("Daily Field Muster Roll", "Daily Muster Roll (Field Attendance)"),
            ("Monthly Attendance Matrix", "Monthly Attendance Matrix"),
            ("Advance & Expenses", "Advance & Expense Register"),
            ("Wages, Slips & Bank Letters", "Monthly Wages & Payout Summary")
        ]
    },
    {
        "id": "Billing",
        "icon": "📑",
        "title": "Tata Steel Billing",
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
        "id": "Fuel",
        "icon": "⛽",
        "title": "Site Fuel Record",
        "submenus": [
            ("Daily Diesel Slip", "Daily Diesel Issue Slip"),
            ("Fuel Benchmark Master", "Fleet Fuel Benchmark Master"),
            ("Anti-Pilferage Audit", "Theft & Pilferage Audit")
        ]
    }
]

INDU_SAO_SVG_LOGO = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 160 120" style="width: 36px; height: 26px;"><ellipse cx="80" cy="60" rx="76" ry="56" fill="#ffffff" stroke="#1e293b" stroke-width="5"/><polygon points="54,90 54,42 70,30 70,90" fill="#1e293b"/><polygon points="76,90 76,22 92,12 92,90" fill="#3730a3"/><rect x="98" y="24" width="4" height="66" fill="#64748b"/><rect x="105" y="32" width="4" height="58" fill="#64748b"/><path d="M 32,76 C 50,98 112,98 128,78 C 114,90 58,90 32,76 Z" fill="#1e293b"/><circle cx="80" cy="74" r="5" fill="#3730a3"/></svg>"""

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important; }
    .stApp { background-color: #ffffff; }
    .block-container { padding-top: 3.6rem !important; padding-bottom: 2.5rem !important; padding-left: 2.2rem !important; padding-right: 2.2rem !important; max-width: 100% !important; }
    section[data-testid="stSidebar"] { background-color: #f8fafc !important; border-right: 1px solid #e2e8f0 !important; width: 275px !important; min-width: 275px !important; }
    section[data-testid="stSidebar"] > div:first-child { padding-top: 1.1rem !important; padding-left: 0.75rem !important; padding-right: 0.75rem !important; }
    section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] { gap: 0.15rem !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"] { width: 100% !important; min-height: 38px !important; height: 38px !important; text-align: left !important; justify-content: flex-start !important; background-color: transparent !important; border: none !important; box-shadow: none !important; border-radius: 8px !important; padding: 6px 10px !important; margin-top: 4px !important; font-size: 0.88rem !important; font-weight: 700 !important; color: #0f172a !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="secondary"]:hover { background-color: #e2e8f0 !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="tertiary"] { min-height: 33px !important; height: 33px !important; text-align: left !important; justify-content: flex-start !important; background-color: transparent !important; border: none !important; border-left: 2px solid #cbd5e1 !important; box-shadow: none !important; border-radius: 0px 8px 8px 0px !important; padding: 4px 12px 4px 16px !important; margin-left: 14px !important; width: calc(100% - 14px) !important; font-size: 0.82rem !important; font-weight: 400 !important; color: #475569 !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="tertiary"]:hover { background-color: #f1f5f9 !important; color: #0f172a !important; border-left: 2px solid #64748b !important; }
    section[data-testid="stSidebar"] div.stButton > button[kind="primary"] { width: calc(100% - 14px) !important; min-height: 34px !important; height: 34px !important; text-align: left !important; justify-content: flex-start !important; background-color: #e0e7ff !important; color: #1e3a8a !important; border: none !important; border-left: 3px solid #4f46e5 !important; border-radius: 0px 16px 16px 0px !important; padding: 4px 12px 4px 15px !important; margin-left: 14px !important; font-size: 0.83rem !important; font-weight: 600 !important; box-shadow: none !important; }
    div.stMain div.stButton > button[kind="primary"], div.stFormSubmitButton > button[kind="primary"], div.stDownloadButton > button[kind="primary"] { background-color: #1e293b !important; color: #ffffff !important; border: 1px solid #1e293b !important; border-radius: 8px !important; font-weight: 500 !important; }
    div.stMain div.stButton > button[kind="primary"]:hover { background-color: #334155 !important; }
    div.stMain div.stButton > button[kind="secondary"], div.stDownloadButton > button[kind="secondary"] { background-color: #ffffff !important; color: #334155 !important; border: 1px solid #cbd5e1 !important; border-radius: 8px !important; font-size: 0.8rem !important; font-weight: 500 !important; padding: 4px 10px !important; min-height: 34px !important; height: 34px !important; }
    div.stMain div.stButton > button[kind="secondary"]:hover { background-color: #f8fafc !important; border-color: #94a3b8 !important; }
    div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 12px !important; border: 1px solid #e2e8f0 !important; background-color: #ffffff !important; }
    .top-user-avatar { width: 32px; height: 32px; border-radius: 50%; background: #3730a3; display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 0.78rem; color: white; flex-shrink: 0; }
    .status-badge { display: inline-block; padding: 1px 6px; border-radius: 9999px; font-size: 0.60rem; font-weight: 600; text-transform: uppercase; }
    .status-admin { background-color: #ecfdf5; color: #047857; border: 1px solid #a7f3d0; }
    .status-user { background-color: #eef2ff; color: #3730a3; border: 1px solid #c7d2fe; }
</style>
""", unsafe_allow_html=True)


# ----------------- 5. AUTHENTICATION & SESSION ENGINE -----------------
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
    if "active_workspace" not in st.session_state:
        st.session_state.active_workspace = "Attendance"
    if "active_subtab" not in st.session_state:
        st.session_state.active_subtab = "Daily Muster Roll (Field Attendance)"
    if "selected_worker_card_id" not in st.session_state:
        st.session_state.selected_worker_card_id = None

init_session()

def login_screen():
    c1, c2, c3 = st.columns([1, 1.2, 1])
    with c2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="text-align: center; margin-bottom: 24px;">
                <div style="display: flex; justify-content: center; margin-bottom: 10px;">{INDU_SAO_SVG_LOGO}</div>
                <h2 style="margin: 0; padding: 0; color: #0f172a; font-weight: 700; font-size: 1.55rem;">{COMPANY_HEADER['NAME']}</h2>
                <div style="font-size: 0.82rem; color: #64748b; font-weight: 500; margin-top: 4px;">{COMPANY_HEADER['GST']} | {COMPANY_HEADER['VENDOR']}</div>
                <div style="font-size: 0.72rem; color: #059669; font-weight: 600; margin-top: 2px;">{COMPANY_HEADER['ISO']}</div>
            </div>
            """, 
            unsafe_allow_html=True
        )

        with st.container(border=True):
            st.markdown("##### Sign in to Workspace")
            st.caption("Enter your authorized credentials to continue")
            
            username = st.text_input("Username", placeholder="e.g. admin").strip()
            password = st.text_input("Password", type="password", placeholder="••••••••").strip()
            st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
            submitted = st.button("Continue →", use_container_width=True, type="primary")

            if submitted:
                if not username or not password:
                    st.warning("Please enter both Username and Password.")
                    return

                u_lower = username.lower()
                configured_master_pass = st.secrets.get("app_password", DEFAULT_ADMIN_PASS)

                if u_lower == DEFAULT_ADMIN_USER.lower() and password == configured_master_pass:
                    st.session_state.user_authenticated = True
                    st.session_state.current_user = DEFAULT_ADMIN_USER
                    st.session_state.current_name = DEFAULT_ADMIN_NAME
                    st.session_state.current_role = "ADMIN"
                    st.session_state.allowed_tabs = list(TAB_REGISTRY.keys())
                    st.session_state.active_workspace = "Attendance"
                    st.session_state.active_subtab = "Daily Muster Roll (Field Attendance)"
                    st.rerun()

                users_df = load_sheet_df("app_users")
                if users_df.empty or "Username" not in users_df.columns:
                    st.error("Authentication service unavailable.")
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
                        st.error("Account access is disabled.")
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
                            permitted = []
                            for t in raw_tabs.split(","):
                                t_clean = t.strip()
                                if t_clean in TAB_REGISTRY:
                                    permitted.append(t_clean)
                                elif t_clean in TAB_ALIASES:
                                    permitted.append(TAB_ALIASES[t_clean])
                            st.session_state.allowed_tabs = list(set(permitted))

                        if "Daily Muster Roll (Field Attendance)" in st.session_state.allowed_tabs:
                            st.session_state.active_workspace = "Attendance"
                            st.session_state.active_subtab = "Daily Muster Roll (Field Attendance)"
                        elif st.session_state.allowed_tabs:
                            first_tab = st.session_state.allowed_tabs[0]
                            st.session_state.active_workspace = TAB_REGISTRY.get(first_tab, "Attendance")
                            st.session_state.active_subtab = first_tab

                        st.rerun()
                    else:
                        st.error("Invalid credentials provided.")
                else:
                    st.error("User profile not found.")

if not st.session_state.user_authenticated:
    login_screen()
    st.stop()


# ----------------- 6. SLEEK SIDEBAR TREE NAVIGATION -----------------
with st.sidebar:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 10px; padding: 2px 4px 12px 4px; margin-bottom: 6px; border-bottom: 1px solid #e2e8f0;">
            <div>{INDU_SAO_SVG_LOGO}</div>
            <div>
                <div style="font-size: 0.94rem; font-weight: 700; color: #0f172a;">{COMPANY_HEADER['NAME']}</div>
                <div style="font-size: 0.68rem; color: #64748b; font-weight: 500;">West Bokaro • ID47</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    for ws in WORKSPACES_TREE:
        ws_id = ws["id"]
        ws_icon = ws["icon"]
        ws_title = ws["title"]
        
        permitted_submenus = []
        for disp_name, reg_key in ws["submenus"]:
            if st.session_state.current_role == "ADMIN" or reg_key in st.session_state.allowed_tabs:
                permitted_submenus.append((disp_name, reg_key))

        if not permitted_submenus and st.session_state.current_role != "ADMIN":
            continue

        is_open = (st.session_state.active_workspace == ws_id)
        chevron = "▾" if is_open else "▸"

        if st.button(f"{chevron}  {ws_icon}  {ws_title}", key=f"nav_ws_{ws_id}", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = ws_id
            st.session_state.selected_worker_card_id = None
            if permitted_submenus and st.session_state.active_subtab not in [s[1] for s in permitted_submenus]:
                st.session_state.active_subtab = permitted_submenus[0][1]
            st.rerun()

        if is_open:
            for disp_name, reg_key in permitted_submenus:
                is_sub_active = (st.session_state.active_subtab == reg_key)
                btn_kind = "primary" if is_sub_active else "tertiary"
                
                if st.button(disp_name, key=f"nav_sub_{reg_key}", use_container_width=True, type=btn_kind):
                    st.session_state.active_subtab = reg_key
                    st.session_state.selected_worker_card_id = None
                    st.rerun()

    if st.session_state.current_role == "ADMIN":
        st.markdown("<hr style='margin: 8px 0; border: none; border-top: 1px solid #e2e8f0;' />", unsafe_allow_html=True)
        is_admin_open = (st.session_state.active_workspace == "Admin")
        adm_chevron = "▾" if is_admin_open else "▸"
        
        if st.button(f"{adm_chevron}  ⚙️  Access & Settings", key="nav_ws_admin_settings", use_container_width=True, type="secondary"):
            st.session_state.active_workspace = "Admin"
            st.session_state.active_subtab = "User Accounts & RBAC"
            st.session_state.selected_worker_card_id = None
            st.rerun()

        if is_admin_open:
            is_adm_sub = (st.session_state.active_subtab == "User Accounts & RBAC")
            if st.button("User Permissions (RBAC)", key="nav_sub_admin_rbac_item", use_container_width=True, type="primary" if is_adm_sub else "tertiary"):
                st.session_state.active_subtab = "User Accounts & RBAC"
                st.session_state.selected_worker_card_id = None
                st.rerun()


# ----------------- 7. CLEAN TOP BAR -----------------
user_display_name = st.session_state.get("current_name") or st.session_state.get("current_user") or "User"
name_parts = str(user_display_name).strip().split()
initials = "".join([p[0] for p in name_parts[:2] if p]).upper() if name_parts else "US"
badge_class = "status-admin" if st.session_state.current_role == "ADMIN" else "status-user"
avatar_html = f'<div class="top-user-avatar">{initials}</div>'

h_col1, h_col2, h_col3 = st.columns([2.8, 1.5, 0.8], gap="small")
with h_col1:
    ws_map = {"Attendance": "Worker Attendance", "Billing": "Tata Steel Billing", "Fuel": "Site Fuel Record", "Admin": "System Administration"}
    cur_ws_label = ws_map.get(st.session_state.active_workspace, "Workspace")
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; gap: 8px; height: 34px;">
            <span style="font-size: 0.84rem; color: #64748b; font-weight: 500;">{cur_ws_label}</span>
            <span style="color: #cbd5e1;">/</span>
            <span style="font-size: 0.86rem; color: #0f172a; font-weight: 600;">{st.session_state.active_subtab}</span>
        </div>
        """, 
        unsafe_allow_html=True
    )

with h_col2:
    st.markdown(
        f"""
        <div style="display: flex; align-items: center; justify-content: flex-end; gap: 8px; height: 34px;">
            {avatar_html}
            <div style="text-align: left; line-height: 1.15;">
                <div style="font-weight: 600; font-size: 0.8rem; color: #0f172a; white-space: nowrap;">{user_display_name}</div>
                <div><span class="status-badge {badge_class}">{st.session_state.current_role}</span></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

with h_col3:
    if st.button("Sign Out", help="End Session", use_container_width=True, key="hdr_pwr_out", type="secondary"):
        st.session_state.user_authenticated = False
        st.rerun()

st.markdown("<hr style='margin-top: 10px; margin-bottom: 18px; border: none; border-top: 1px solid #f1f5f9;' />", unsafe_allow_html=True)


# ----------------- 8. MAIN VIEW ROUTING -----------------
current_view = st.session_state.active_subtab

# ----------------- VIEW 1: EMPLOYEE ENROLMENT -----------------
if current_view == "➕ Employee Enrolment":
    st.markdown("#### Employee Enrolment")
    st.caption("Onboard workforce members with statutory skill category, fixed Week-Off day, photo, and bank particulars")

    wage_rates_df = get_active_wage_rates_df()
    rate_lookup = dict(zip(wage_rates_df["Skill_Category"], wage_rates_df["Total_Daily_Rate"]))

    entry_mode = st.radio("Enrolment Method", ["Single Employee Form", "Bulk Excel/CSV Upload"], horizontal=True)

    if entry_mode == "Single Employee Form":
        c_form, c_info = st.columns([1.35, 0.65], gap="large")
        with c_form:
            with st.container(border=True):
                w_df_current = load_sheet_df("worker_master")
                
                w_div = st.selectbox("Operational Division", ["Fleet Operations", "Civil Construction"], key="enrol_div")
                next_wid = generate_next_worker_id(w_div, w_df_current)
                st.caption(f"Assigned System ID: **{next_wid}**")

                c_np1, c_np2 = st.columns([1.6, 1.0])
                with c_np1:
                    w_name = st.text_input("Full Official Name", key="enrol_name").strip()
                    w_ref = st.text_input("Identity Verification Reference", key="enrol_ref", help="Distinguishes duplicate names").strip()
                with c_np2:
                    w_photo_file = st.file_uploader("Passport Photo (Optional)", type=["jpg", "jpeg", "png"], key="enrol_photo")
                    if w_photo_file:
                        st.image(w_photo_file, width=85)

                role_choices = FLEET_ROLES if w_div == "Fleet Operations" else CIVIL_ROLES
                sel_role = st.selectbox("Designation / Role", role_choices, key="enrol_role")
                
                custom_role = ""
                if sel_role == "Other (Custom Entry)":
                    custom_role = st.text_input("Specify Custom Role", key="enrol_custom_role").strip()

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
                        w_df = load_sheet_df("worker_master")
                        photo_b64_str = compress_photo_to_base64(w_photo_file)
                        new_row = {
                            "Worker_ID": next_wid, "Full_Name": w_name, "Govt_ID_Ref": w_ref,
                            "Division": w_div, "Designation": final_role, "Wage_Type": w_wage_type,
                            "Skill_Category": w_skill_cat, "Base_Rate": w_rate, "Week_Off_Day": w_week_off,
                            "Contact_No": w_contact, "Bank_Account_No": b_acc, "Bank_IFSC_Code": b_ifsc,
                            "Bank_Branch_Details": b_branch, "Status": w_status,
                            "Created_Date": datetime.date.today().strftime("%Y-%m-%d"), "Photo_Base64": photo_b64_str
                        }
                        updated_w_df = pd.concat([w_df, pd.DataFrame([new_row])], ignore_index=True)
                        if save_sheet_df("worker_master", updated_w_df[WORKER_MASTER_COLS]):
                            st.success(f"Employee {w_name} ({next_wid}) enrolled (Week-Off: {w_week_off}).")
                            st.rerun()

        with c_info:
            with st.container(border=True):
                st.markdown("###### Active Statutory Daily Rates")
                for _, rr in wage_rates_df.iterrows():
                    st.caption(f"• **{rr['Skill_Category']}:** ₹{float(rr['Total_Daily_Rate']):,.0f}/day *(Basic ₹{float(rr['Basic_Pay']):,.0f} + VDA ₹{float(rr['VDA_Allowance']):,.0f})*")
                st.markdown("---")
                st.markdown("###### Weekly-Off Lock Rule")
                st.caption("On an employee's assigned Week-Off day, daily attendance automatically locks to **WO**. Only Admin can override/approve duty on that day.")

    else:
        with st.container(border=True):
            st.markdown("###### Bulk Employee Roster Import")
            up_file = st.file_uploader("Upload CSV / XLSX File", type=["csv", "xlsx"])
            if up_file:
                if up_file.name.lower().endswith(".csv"):
                    b_df = pd.read_csv(up_file)
                else:
                    xl_b = pd.ExcelFile(up_file)
                    b_df = xl_b.parse(xl_b.sheet_names[0])
                b_df.columns = [str(c).strip() for c in b_df.columns]
                st.dataframe(b_df.head(5), use_container_width=True, hide_index=True)

                if st.button("Commit Bulk Roster", type="primary", use_container_width=True):
                    w_df = load_sheet_df("worker_master")
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

                        wo_val = normalize_week_off(b_row.get("Week_Off_Day", "Sunday"))

                        appended_rows.append({
                            "Worker_ID": new_gen_id, "Full_Name": str(b_row.get("Full_Name", "")).strip(),
                            "Govt_ID_Ref": str(b_row.get("Govt_ID_Ref", "")).strip(), "Division": div_val,
                            "Designation": clean_desig, "Wage_Type": w_type_val, "Skill_Category": raw_skill,
                            "Base_Rate": b_rate_val, "Week_Off_Day": wo_val,
                            "Contact_No": str(b_row.get("Contact_No", "")).strip(),
                            "Bank_Account_No": str(b_row.get("Bank_Account_No", "")).strip(),
                            "Bank_IFSC_Code": str(b_row.get("Bank_IFSC_Code", "")).strip().upper(),
                            "Bank_Branch_Details": str(b_row.get("Bank_Branch_Details", "")).strip().upper(),
                            "Status": str(b_row.get("Status", "ACTIVE")).strip().upper(),
                            "Created_Date": datetime.date.today().strftime("%Y-%m-%d"), "Photo_Base64": ""
                        })

                    fin_w_df = pd.concat([w_df, pd.DataFrame(appended_rows)], ignore_index=True)
                    if save_sheet_df("worker_master", fin_w_df[WORKER_MASTER_COLS]):
                        st.success(f"{len(appended_rows)} records imported successfully.")
                        st.rerun()


# ----------------- VIEW 2: STATUTORY WAGE RATE MASTER & 1-CLICK BULK REVISION -----------------
elif current_view == "⚖️ Statutory Wage Rates":
    st.markdown("#### Statutory Minimum Wage Rate Master (Category-Wise)")
    st.caption("Update Basic + VDA/DA rates for Unskilled, Semi-Skilled, Skilled, and Highly Skilled categories and sync all workers in one click")

    wage_rates_df = get_active_wage_rates_df()
    w_master = load_sheet_df("worker_master")

    c_kpi1, c_kpi2, c_kpi3, c_kpi4 = st.columns(4)
    kpi_cols = [c_kpi1, c_kpi2, c_kpi3, c_kpi4]
    for idx, cat in enumerate(SKILL_CATEGORIES):
        cnt = 0
        if not w_master.empty and "Skill_Category" in w_master.columns:
            cnt = len(w_master[(w_master["Status"].astype(str).str.upper() == "ACTIVE") & (w_master["Skill_Category"].astype(str) == cat)])
        cur_r = float(wage_rates_df[wage_rates_df["Skill_Category"] == cat]["Total_Daily_Rate"].iloc[0])
        kpi_cols[idx].metric(f"{cat}", f"₹{cur_r:,.0f} / day", f"{cnt} Active Workers")

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown("###### Edit Category Wage Rates (Basic + VDA = Total Daily Rate)")
        edited_rates_df = st.data_editor(
            wage_rates_df[WAGE_MASTER_COLS],
            use_container_width=True,
            hide_index=True,
            disabled=["Skill_Category"],
            column_config={
                "Skill_Category": st.column_config.TextColumn("Skill Category"),
                "Basic_Pay": st.column_config.NumberColumn("Basic / Day (₹)", min_value=0.0, step=1.0),
                "VDA_Allowance": st.column_config.NumberColumn("VDA / DA (₹)", min_value=0.0, step=1.0),
                "Total_Daily_Rate": st.column_config.NumberColumn("Total Daily Rate (₹)", min_value=0.0, step=1.0),
                "Effective_From": st.column_config.TextColumn("Effective Date (YYYY-MM-DD)"),
                "Remarks": st.column_config.TextColumn("Notification / Circular Ref")
            },
            key="wage_rate_editor"
        )

        b_col1, b_col2 = st.columns([1.4, 1.0])
        with b_col1:
            if st.button("🚀 Save Rates & Update All Daily Wage Workers", type="primary", use_container_width=True):
                cleaned_rates = []
                new_rate_map = {}
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
                        "Skill_Category": cat, "Basic_Pay": b_pay, "VDA_Allowance": vda_pay,
                        "Total_Daily_Rate": tot_pay,
                        "Effective_From": str(r["Effective_From"]).strip() or datetime.date.today().strftime("%Y-%m-%d"),
                        "Remarks": str(r["Remarks"]).strip()
                    })

                rates_saved = save_sheet_df("wage_rate_master", pd.DataFrame(cleaned_rates)[WAGE_MASTER_COLS])

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

                    save_sheet_df("worker_master", w_master[WORKER_MASTER_COLS])

                if rates_saved:
                    st.success(f"Updated Statutory Rates and revised Base_Rate for **{updated_count} Daily Wage Workers** across the ERP!")
                    st.rerun()

        with b_col2:
            if st.button("🔄 Auto-Assign Skill Categories & Defaults", type="secondary", use_container_width=True):
                if not w_master.empty:
                    assigned = 0
                    for idx, wr in w_master.iterrows():
                        cur_cat = str(wr.get("Skill_Category", "")).strip()
                        if not cur_cat or cur_cat.lower() == "nan":
                            inferred = infer_skill_category_from_role_or_rate(
                                wr.get("Designation", ""), wr.get("Base_Rate", 0), wr.get("Wage_Type", "DAILY_WAGE")
                            )
                            w_master.at[idx, "Skill_Category"] = inferred
                            assigned += 1
                        cur_wo = str(wr.get("Week_Off_Day", "")).strip()
                        if not cur_wo or cur_wo not in WEEK_DAYS_OPTIONS:
                            w_master.at[idx, "Week_Off_Day"] = "Sunday"
                    if save_sheet_df("worker_master", w_master[WORKER_MASTER_COLS]):
                        st.success(f"Updated {assigned} worker categories & default Week-Off days!")
                        st.rerun()


# ----------------- VIEW 3: EMPLOYEE MASTER REGISTER, FULL EDIT/DELETE PROFILE & SELECTIVE ID CARDS -----------------
elif current_view == "📋 Employee Master Register":
    w_table = load_sheet_df("worker_master")
    muster_all = load_sheet_df("muster_roll")
    adv_all = load_sheet_df("worker_advances")
    tata_all = load_sheet_df("tata_form_xvii")

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
        else:
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
                    single_card_pdf = generate_atm_id_cards_pdf(w_match)
                    st.download_button(
                        label=f"🪪 Download ATM ID Card PDF ({target_wid})",
                        data=single_card_pdf,
                        file_name=f"ID_Card_{target_wid}.pdf",
                        mime="application/pdf",
                        type="primary"
                    )

            with st.container(border=True):
                p_c0, p_c1, p_c2 = st.columns([0.65, 1.85, 1.25])
                with p_c0:
                    if emp_photo_b64 and len(emp_photo_b64) > 50:
                        st.markdown(
                            f'<img src="data:image/jpeg;base64,{emp_photo_b64}" style="width: 92px; height: 110px; object-fit: cover; border-radius: 8px; border: 1px solid #cbd5e1;" />',
                            unsafe_allow_html=True
                        )
                    else:
                        st.markdown(
                            '<div style="width: 92px; height: 110px; background: #f1f5f9; border: 1px dashed #94a3b8; border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 0.72rem; color: #64748b;">No Photo</div>',
                            unsafe_allow_html=True
                        )
                    new_photo_up = st.file_uploader("Update Photo", type=["jpg", "jpeg", "png"], key=f"upd_photo_{target_wid}", label_visibility="collapsed")
                    if new_photo_up:
                        if st.button("Save Photo", key=f"save_ph_{target_wid}", type="primary", use_container_width=True):
                            b64_new = compress_photo_to_base64(new_photo_up)
                            if b64_new:
                                w_table.loc[w_table["Worker_ID"].astype(str) == target_wid, "Photo_Base64"] = b64_new
                                if save_sheet_df("worker_master", w_table[WORKER_MASTER_COLS]):
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

            with st.expander("✏️ Edit Employee Details or Delete / Deactivate Record", expanded=False):
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
                        if not ed_name:
                            st.error("Employee Name cannot be empty.")
                        else:
                            mask_w = w_table["Worker_ID"].astype(str) == target_wid
                            w_table.loc[mask_w, "Full_Name"] = ed_name
                            w_table.loc[mask_w, "Govt_ID_Ref"] = ed_ref
                            w_table.loc[mask_w, "Contact_No"] = ed_contact
                            w_table.loc[mask_w, "Division"] = ed_div
                            w_table.loc[mask_w, "Designation"] = ed_role
                            w_table.loc[mask_w, "Week_Off_Day"] = ed_wo
                            w_table.loc[mask_w, "Status"] = ed_status
                            w_table.loc[mask_w, "Wage_Type"] = ed_wtype
                            w_table.loc[mask_w, "Skill_Category"] = ed_skill
                            w_table.loc[mask_w, "Base_Rate"] = ed_rate
                            w_table.loc[mask_w, "Bank_Account_No"] = ed_acc
                            w_table.loc[mask_w, "Bank_IFSC_Code"] = ed_ifsc
                            w_table.loc[mask_w, "Bank_Branch_Details"] = ed_branch

                            if save_sheet_df("worker_master", w_table[WORKER_MASTER_COLS]):
                                st.success(f"Profile for {ed_name} ({target_wid}) updated everywhere!")
                                st.rerun()

                st.markdown("<hr style='margin: 12px 0; border-top: 1px solid #fee2e2;' />", unsafe_allow_html=True)
                st.markdown("###### 🗑️ Permanent Delete Zone")
                del_c1, del_c2, del_c3 = st.columns([1.4, 1.4, 1.0])
                with del_c1:
                    confirm_del = st.checkbox(f"Confirm permanent deletion of {emp_name} ({target_wid})", key=f"chk_del_{target_wid}")
                with del_c2:
                    also_del_logs = st.checkbox("Also delete attendance & advance logs", value=False, key=f"chk_del_logs_{target_wid}")
                with del_c3:
                    if st.button("🗑️ Delete Permanently", type="secondary", use_container_width=True, key=f"btn_del_{target_wid}"):
                        if not confirm_del:
                            st.warning("Please tick the confirmation checkbox first.")
                        else:
                            w_table_new = w_table[w_table["Worker_ID"].astype(str) != target_wid]
                            save_sheet_df("worker_master", w_table_new[WORKER_MASTER_COLS])

                            if also_del_logs:
                                if not muster_all.empty and "Worker_ID" in muster_all.columns:
                                    m_clean = muster_all[muster_all["Worker_ID"].astype(str) != target_wid]
                                    save_sheet_df("muster_roll", m_clean[MUSTER_ROLL_COLS])
                                if not adv_all.empty and "Worker_ID" in adv_all.columns:
                                    a_clean = adv_all[adv_all["Worker_ID"].astype(str) != target_wid]
                                    save_sheet_df("worker_advances", a_clean[ADVANCE_LOG_COLS])

                            st.session_state.selected_worker_card_id = None
                            st.success(f"Employee {emp_name} ({target_wid}) deleted.")
                            st.rerun()

            today_dt = datetime.date.today()
            cur_payroll_map = compute_monthly_payroll_and_advances(w_table, muster_all, adv_all, tata_all, today_dt.year, today_dt.month)
            emp_cf_bal = cur_payroll_map.get(target_wid, {}).get("Closing_Carry_Forward", 0.0)

            emp_muster = pd.DataFrame()
            if not muster_all.empty and "Worker_ID" in muster_all.columns:
                emp_muster = muster_all[muster_all["Worker_ID"].astype(str) == target_wid].copy()
                emp_muster["Duty_Location"] = emp_muster["Duty_Location"].apply(normalize_duty_type)

            emp_adv = pd.DataFrame()
            if not adv_all.empty and "Worker_ID" in adv_all.columns:
                emp_adv = adv_all[adv_all["Worker_ID"].astype(str) == target_wid].copy()

            emp_tata = pd.DataFrame()
            if not tata_all.empty and "Worker_Name" in tata_all.columns:
                emp_tata = tata_all[tata_all["Worker_Name"].astype(str).str.strip().str.upper() == emp_name.strip().upper()].copy()

            c_f1, c_f2, _ = st.columns([1.0, 1.2, 2.0])
            with c_f1:
                card_year_filter = st.selectbox("Filter Year", ["All Years", "2025", "2026", "2027"], index=2, key="card_y_flt")
            with c_f2:
                card_month_filter = st.selectbox("Filter Month", ["All Months"] + list(calendar.month_name)[1:], key="card_m_flt")

            if not emp_muster.empty:
                if card_year_filter != "All Years":
                    emp_muster = emp_muster[emp_muster["Date"].astype(str).str.startswith(card_year_filter)]
                if card_month_filter != "All Months":
                    m_idx = list(calendar.month_name).index(card_month_filter)
                    m_prefix = f"-{m_idx:02d}-"
                    emp_muster = emp_muster[emp_muster["Date"].astype(str).str.contains(m_prefix)]

            tot_p = len(emp_muster[emp_muster["Attendance"] == "P"]) if not emp_muster.empty else 0
            tot_hd = len(emp_muster[emp_muster["Attendance"] == "HD"]) if not emp_muster.empty else 0
            tot_l = len(emp_muster[emp_muster["Attendance"] == "L"]) if not emp_muster.empty else 0
            tot_ot = pd.to_numeric(emp_muster["OT_Hours"], errors="coerce").fillna(0).sum() if not emp_muster.empty else 0.0

            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("Present Days (P)", f"{tot_p} d")
            k2.metric("Half Days (HD)", f"{tot_hd} d")
            k3.metric("Paid Leaves (L)", f"{tot_l} d")
            k4.metric("Overtime (OT)", f"{tot_ot:.1f} hrs")
            k5.metric("Pending Advance Bal.", f"₹{emp_cf_bal:,.0f}")

            st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

            det_c1, det_c2 = st.columns([1.5, 1.0], gap="large")
            with det_c1:
                with st.container(border=True):
                    st.markdown("###### 📅 Daily Field Attendance Log (Direct Edit)")
                    st.caption("Edit Status (`P`, `HD`, `L` Paid Leave, `A`, `WO`), Punch Type, OT Hours, or Remarks")
                    
                    if not emp_muster.empty:
                        emp_muster_sorted = emp_muster.sort_values(by="Date", ascending=False).copy()
                        emp_muster_sorted["OT_Hours"] = pd.to_numeric(emp_muster_sorted["OT_Hours"], errors="coerce").fillna(0.0)
                        edit_cols = ["Date", "Attendance", "Duty_Location", "OT_Hours", "Remarks"]
                        
                        edited_emp_df = st.data_editor(
                            emp_muster_sorted[edit_cols],
                            use_container_width=True,
                            hide_index=True,
                            disabled=["Date"],
                            column_config={
                                "Attendance": st.column_config.SelectboxColumn("Status", options=["P", "HD", "L", "A", "WO"], required=True),
                                "Duty_Location": st.column_config.SelectboxColumn("Punch Type", options=["Punch", "Non-Punch"], required=True),
                                "OT_Hours": st.column_config.NumberColumn("OT (Hrs)", min_value=0.0, max_value=16.0, step=0.5),
                                "Remarks": st.column_config.TextColumn("Remarks")
                            },
                            key=f"editor_att_{target_wid}"
                        )

                        if st.button("💾 Save Attendance Changes", type="primary", use_container_width=True, key=f"save_edit_att_{target_wid}"):
                            full_m_df = load_sheet_df("muster_roll")
                            if not full_m_df.empty:
                                for _, erow in edited_emp_df.iterrows():
                                    d_val = str(erow["Date"])
                                    mask = (full_m_df["Worker_ID"].astype(str) == target_wid) & (full_m_df["Date"].astype(str) == d_val)
                                    full_m_df.loc[mask, "Attendance"] = str(erow["Attendance"])
                                    full_m_df.loc[mask, "Duty_Location"] = str(erow["Duty_Location"])
                                    full_m_df.loc[mask, "OT_Hours"] = float(erow["OT_Hours"] or 0.0)
                                    full_m_df.loc[mask, "Remarks"] = str(erow["Remarks"] or "")
                                
                                if save_sheet_df("muster_roll", full_m_df[MUSTER_ROLL_COLS]):
                                    log_attendance_activity(
                                        load_sheet_df, save_sheet_df, "PROFILE_EDIT",
                                        st.session_state.current_user, "PROFILE_ATT_EDIT",
                                        f"Edited attendance for {emp_name} ({target_wid})"
                                    )
                                    for k in list(st.session_state.keys()):
                                        if str(k).startswith("att_state_"):
                                            del st.session_state[k]
                                    st.success("Attendance updated and synced across all modules!")
                                    st.rerun()
                    else:
                        st.info("No field attendance logs recorded for this period.")

            with det_c2:
                with st.container(border=True):
                    st.markdown("###### 🏛️ Tata Form XVII Compliance Payouts")
                    if not emp_tata.empty:
                        disp_t_cols = [c for c in ["Wages_Period", "Approved_Days", "Net_Amount_Paid"] if c in emp_tata.columns]
                        st.dataframe(emp_tata[disp_t_cols], use_container_width=True, hide_index=True)
                    else:
                        st.info("No Form XVII statutory records linked yet.")

                with st.container(border=True):
                    st.markdown("###### 💵 Advance & Expense History")
                    if not emp_adv.empty:
                        disp_a_cols = [c for c in ["Date", "Amount", "Payment_Mode", "Remarks"] if c in emp_adv.columns]
                        st.dataframe(emp_adv[disp_a_cols], use_container_width=True, hide_index=True)
                    else:
                        st.info("No advances disbursed.")

    else:
        st.markdown("#### Employee Master Register & Selective ATM ID Cards")
        st.caption("Open any employee profile to edit details/week-off/photo/delete, or select specific employees to print 8-up ATM ID cards")

        if w_table.empty or "Worker_ID" not in w_table.columns:
            st.info("No employee records found.")
        else:
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
                    st.markdown("###### 🔍 Open Profile (Edit / Delete / Photo / Attendance)")
                    emp_choices = {
                        f"{r['Worker_ID']} — {r['Full_Name']} ({r['Designation']})": str(r["Worker_ID"])
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
                    all_card_options = {
                        f"{r['Worker_ID']} — {r['Full_Name']}": str(r["Worker_ID"])
                        for _, r in disp_table.iterrows()
                    }
                    sel_card_labels = st.multiselect(
                        "Select Employees for ID Cards (Leave empty to select all filtered)",
                        options=list(all_card_options.keys()),
                        placeholder="Choose specific employees to print cards..."
                    )
                    
                    target_wids_for_cards = [all_card_options[lbl] for lbl in sel_card_labels] if sel_card_labels else list(all_card_options.values())
                    cards_subset_df = disp_table[disp_table["Worker_ID"].astype(str).isin(target_wids_for_cards)]

                    if FPDF_AVAILABLE and not cards_subset_df.empty:
                        cards_pdf_bytes = generate_atm_id_cards_pdf(cards_subset_df)
                        pages_cnt = (len(cards_subset_df) + 7) // 8
                        btn_lbl = (
                            f"📥 Download Selected ID Cards PDF ({len(cards_subset_df)} Cards • {pages_cnt} Page)"
                            if sel_card_labels else
                            f"📥 Download All Filtered ID Cards PDF ({len(cards_subset_df)} Cards • {pages_cnt} Page)"
                        )
                        st.download_button(
                            label=btn_lbl,
                            data=cards_pdf_bytes,
                            file_name=f"M_s_INDU_SAO_ID_Cards_{datetime.date.today().strftime('%Y%m%d')}.pdf",
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True
                        )
                    elif not FPDF_AVAILABLE:
                        st.warning("Add 'fpdf2' to requirements.txt to enable PDF ID cards.")

            disp_table_view = disp_table.copy()
            disp_table_view["Photo"] = disp_table_view["Photo_Base64"].apply(lambda x: "✅" if str(x).strip() else "—")
            disp_table_view["Week_Off_Day"] = disp_table_view["Week_Off_Day"].apply(normalize_week_off)
            req_cols = ["Worker_ID", "Full_Name", "Photo", "Division", "Designation", "Skill_Category", "Base_Rate", "Week_Off_Day", "Bank_Account_No", "Status"]
            safe_cols = [c for c in req_cols if c in disp_table_view.columns]
            st.dataframe(disp_table_view[safe_cols], use_container_width=True, hide_index=True)


# ----------------- VIEW 4: DAILY FIELD MUSTER ROLL (SUPERVISOR DUPLICATE LOCK + 90-DAY AUDIT LOG + WEEK-OFF LOCK) -----------------
elif current_view == "Daily Muster Roll (Field Attendance)":
    st.markdown("#### Daily Field Muster Roll")
    st.caption("Log daily shift attendance with Supervisor Duplicate Lock, Employee Week-Off Lock, Paid Leave (L), and 90-Day Audit Trail")

    w_master = load_sheet_df("worker_master")
    is_admin = (str(st.session_state.current_role).upper() == "ADMIN")

    if w_master.empty or "Worker_ID" not in w_master.columns:
        st.warning("No employees available to mark attendance.")
    else:
        active_workers = w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].copy()
        muster_df = load_sheet_df("muster_roll")

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

        day_records = pd.DataFrame()
        if not muster_df.empty and "Date" in muster_df.columns:
            day_records = muster_df[muster_df["Date"].astype(str) == muster_date_str]

        # Check if this date (or selected division on this date) is already logged
        div_day_records = day_records.copy()
        if sel_division != "All Divisions" and not div_day_records.empty and "Division" in div_day_records.columns:
            div_day_records = div_day_records[div_day_records["Division"] == sel_division]

        is_date_already_logged = (not div_day_records.empty)
        supervisor_locked_out = is_date_already_logged and (not is_admin)

        admin_unlock_wo = False
        if is_admin:
            admin_unlock_wo = st.checkbox(
                f"🔓 Admin Approval: Unlock Weekly-Off Duty for {muster_date.strftime('%d %b %Y')} ({selected_weekday_name})",
                value=False,
                key=f"adm_wo_unlock_{muster_date_str}"
            )

        ym_prefix = muster_date.strftime("%Y-%m")
        logged_dates_this_month = []
        if not muster_df.empty and "Date" in muster_df.columns:
            m_dates = muster_df[muster_df["Date"].astype(str).str.startswith(ym_prefix)]["Date"].astype(str).unique().tolist()
            logged_dates_this_month = sorted(m_dates)

        if is_date_already_logged:
            sup_by = str(div_day_records.iloc[0].get("Supervisor", "Admin"))
            p_cnt = len(div_day_records[div_day_records["Attendance"] == "P"])
            wo_cnt = len(div_day_records[div_day_records["Attendance"] == "WO"])
            if supervisor_locked_out:
                st.error(f"🔒 **LOCKED (ALREADY SUBMITTED)** for **{muster_date.strftime('%d %b %Y')}** ({len(div_day_records)} workers • {p_cnt} Present, {wo_cnt} Week-Off • Logged by `{sup_by}`). Supervisors cannot overwrite a submitted date; contact Admin for corrections.")
            else:
                st.success(f"🟢 **SAVED / ALREADY LOGGED** for **{muster_date.strftime('%d %b %Y')}** ({len(div_day_records)} workers • {p_cnt} Present, {wo_cnt} Week-Off • Logged by `{sup_by}`). Admin Edit Mode Active.")
        else:
            st.warning(f"🟡 **PENDING — NOT LOGGED YET** for **{muster_date.strftime('%d %b %Y')} ({selected_weekday_name})**. Mark attendance below and click 'Save Daily Muster Roll'.")

        days_in_m = calendar.monthrange(muster_date.year, muster_date.month)[1]
        logged_day_nums = {int(d.split("-")[2]) for d in logged_dates_this_month if len(d.split("-")) == 3}
        badges_html = ""
        for d_num in range(1, days_in_m + 1):
            if d_num in logged_day_nums:
                badges_html += f"<span style='display:inline-block; background:#dcfce7; color:#166534; border:1px solid #86efac; border-radius:5px; padding:1px 6px; margin:2px; font-size:0.74rem; font-weight:700;'>{d_num:02d} ✓</span>"
            else:
                badges_html += f"<span style='display:inline-block; background:#f8fafc; color:#94a3b8; border:1px solid #e2e8f0; border-radius:5px; padding:1px 6px; margin:2px; font-size:0.74rem;'>{d_num:02d}</span>"

        st.markdown(
            f"""
            <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:10px; padding:8px 12px; margin-bottom:10px;">
                <div style="font-size:0.76rem; font-weight:600; color:#334155; margin-bottom:4px;">📅 {muster_date.strftime('%B %Y')} Entry Status (Green ✓ = Saved, Gray = Pending):</div>
                <div>{badges_html}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

        cache_key = f"att_state_{muster_date_str}"
        if cache_key not in st.session_state:
            st.session_state[cache_key] = {}
            for _, w in active_workers.iterrows():
                wid = str(w["Worker_ID"])
                w_wo_day = normalize_week_off(w.get("Week_Off_Day", "Sunday"))
                is_wo_today = (w_wo_day == selected_weekday_name)

                def_att = "WO" if is_wo_today else "P"
                def_ot = 0.0
                def_loc = "Punch"

                if not day_records.empty and "Worker_ID" in day_records.columns:
                    match_prev = day_records[day_records["Worker_ID"].astype(str) == wid]
                    if not match_prev.empty:
                        r0 = match_prev.iloc[0]
                        def_att = str(r0.get("Attendance", def_att))
                        def_ot = float(r0.get("OT_Hours", 0.0) or 0.0)
                        def_loc = normalize_duty_type(r0.get("Duty_Location", "Punch"))

                st.session_state[cache_key][wid] = {
                    "att": def_att, "ot": def_ot, "loc": def_loc
                }

        if not supervisor_locked_out:
            with st.container(border=True):
                st.markdown("###### ⚡ Selective Quick-Mark Tool")
                st.caption("Select specific workers below (or leave empty to apply to all unlocked workers).")
                
                worker_map = {
                    f"{r['Worker_ID']} — {r['Full_Name']}": str(r["Worker_ID"])
                    for _, r in active_workers.iterrows()
                }
                wo_lookup_map = {
                    str(r["Worker_ID"]): (normalize_week_off(r.get("Week_Off_Day", "Sunday")) == selected_weekday_name)
                    for _, r in active_workers.iterrows()
                }
                
                sm_c1, sm_c2, sm_c3 = st.columns([2.0, 1.3, 1.0])
                with sm_c1:
                    chosen_labels = st.multiselect(
                        "Choose Specific Workers",
                        options=list(worker_map.keys()),
                        placeholder="Leave empty for ALL unlocked workers, or pick specific names...",
                        label_visibility="collapsed"
                    )
                with sm_c2:
                    bulk_action = st.selectbox(
                        "Quick Action",
                        [
                            "Present — Punch",
                            "Present — Non-Punch",
                            "Half Day (HD) — Punch",
                            "Half Day (HD) — Non-Punch",
                            "Paid Leave (L)",
                            "Absent (A)",
                            "Week Off (WO)"
                        ],
                        label_visibility="collapsed"
                    )
                with sm_c3:
                    if st.button("Apply to Selected", type="primary", use_container_width=True):
                        target_wids = [worker_map[l] for l in chosen_labels] if chosen_labels else list(worker_map.values())
                        for wid in target_wids:
                            if wo_lookup_map.get(wid, False) and not admin_unlock_wo:
                                continue
                            if wid in st.session_state[cache_key]:
                                if bulk_action == "Present — Punch":
                                    st.session_state[cache_key][wid]["att"] = "P"
                                    st.session_state[cache_key][wid]["loc"] = "Punch"
                                elif bulk_action == "Present — Non-Punch":
                                    st.session_state[cache_key][wid]["att"] = "P"
                                    st.session_state[cache_key][wid]["loc"] = "Non-Punch"
                                elif bulk_action == "Half Day (HD) — Punch":
                                    st.session_state[cache_key][wid]["att"] = "HD"
                                    st.session_state[cache_key][wid]["loc"] = "Punch"
                                elif bulk_action == "Half Day (HD) — Non-Punch":
                                    st.session_state[cache_key][wid]["att"] = "HD"
                                    st.session_state[cache_key][wid]["loc"] = "Non-Punch"
                                elif bulk_action == "Paid Leave (L)":
                                    st.session_state[cache_key][wid]["att"] = "L"
                                elif bulk_action == "Absent (A)":
                                    st.session_state[cache_key][wid]["att"] = "A"
                                elif bulk_action == "Week Off (WO)":
                                    st.session_state[cache_key][wid]["att"] = "WO"
                        st.rerun()

        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
        grid_cols = st.columns(2, gap="medium")
        att_options_list = ["P", "HD", "L", "A", "WO"]
        
        for idx, (_, w_row) in enumerate(active_workers.iterrows()):
            col_target = grid_cols[idx % 2]
            wid = str(w_row["Worker_ID"])
            wname = str(w_row["Full_Name"])
            wdiv = str(w_row["Division"])
            wrole = str(w_row["Designation"])
            wskill = str(w_row.get("Skill_Category", ""))
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

            cur_att_val = cur_state.get("att", "P")
            if cur_att_val not in att_options_list:
                cur_att_val = "P"
            cur_loc_norm = normalize_duty_type(cur_state.get("loc", "Punch"))

            with col_target:
                with st.container(border=True):
                    lock_tag = f"  •  <span style='color:#dc2626; font-size:0.74rem; font-weight:600;'>🔒 Weekly Off ({w_wo_day})</span>" if is_wo_locked else ""
                    st.markdown(f"**{wname}** (`{wid}`) • <span style='font-size: 0.78rem; color: #4f46e5; font-weight: 500;'>{wdiv}</span>{lock_tag}", unsafe_allow_html=True)
                    st.caption(f"{wrole} ({wskill}) • Assigned Off: {w_wo_day}")

                    r_c1, r_c2, r_c3 = st.columns([1.1, 1.3, 1.0])
                    with r_c1:
                        cur_state["att"] = st.selectbox(
                            "Status", att_options_list, index=att_options_list.index(cur_att_val),
                            disabled=card_disabled, key=f"att_s_{wid}"
                        )
                    with r_c2:
                        cur_state["loc"] = st.selectbox(
                            "Punch Type", ["Punch", "Non-Punch"], index=["Punch", "Non-Punch"].index(cur_loc_norm),
                            disabled=card_disabled, key=f"loc_s_{wid}"
                        )
                    with r_c3:
                        cur_state["ot"] = st.number_input(
                            "OT (Hrs)", min_value=0.0, max_value=16.0, step=0.5, value=float(cur_state.get("ot", 0.0)),
                            disabled=card_disabled, key=f"ot_s_{wid}"
                        )

            st.session_state[cache_key][wid] = cur_state

        st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
        if not supervisor_locked_out:
            if st.button("Save Daily Muster Roll", type="primary", use_container_width=True):
                marked_rows = []
                p_total, hd_total, l_total, a_total, wo_total = 0, 0, 0, 0, 0
                for _, w_row in active_workers.iterrows():
                    wid = str(w_row["Worker_ID"])
                    s_data = st.session_state[cache_key].get(wid, {})
                    att_v = s_data.get("att", "P")
                    if att_v == "P": p_total += 1
                    elif att_v == "HD": hd_total += 1
                    elif att_v == "L": l_total += 1
                    elif att_v == "A": a_total += 1
                    elif att_v == "WO": wo_total += 1

                    marked_rows.append({
                        "Date": muster_date_str,
                        "Worker_ID": wid,
                        "Worker_Name": str(w_row["Full_Name"]),
                        "Division": str(w_row["Division"]),
                        "Attendance": att_v,
                        "OT_Hours": s_data.get("ot", 0.0),
                        "Duty_Location": normalize_duty_type(s_data.get("loc", "Punch")),
                        "Advance_Amount": 0.0,
                        "Payment_Mode": "Cash",
                        "Supervisor": st.session_state.current_user,
                        "Remarks": "Admin WO Approved" if (normalize_week_off(w_row.get("Week_Off_Day", "Sunday")) == selected_weekday_name and att_v in ["P", "HD"]) else ""
                    })

                all_m = load_sheet_df("muster_roll")
                if not all_m.empty and "Date" in all_m.columns and "Worker_ID" in all_m.columns:
                    w_ids = [r["Worker_ID"] for r in marked_rows]
                    cond = (all_m["Date"].astype(str) == muster_date_str) & (all_m["Worker_ID"].astype(str).isin(w_ids))
                    all_m = all_m[~cond]

                updated_m = pd.concat([all_m, pd.DataFrame(marked_rows)], ignore_index=True)
                if save_sheet_df("muster_roll", updated_m[MUSTER_ROLL_COLS]):
                    act_lbl = "ADMIN_UPDATE" if is_date_already_logged else "NEW_SUBMISSION"
                    sum_txt = f"{sel_division}: P={p_total}, HD={hd_total}, L={l_total}, A={a_total}, WO={wo_total}"
                    log_attendance_activity(
                        load_sheet_df, save_sheet_df, muster_date_str,
                        f"{user_display_name} ({st.session_state.current_user})", act_lbl, sum_txt
                    )
                    st.success(f"Daily Muster Roll for {muster_date_str} saved!")
                    st.rerun()

        # ADMIN-ONLY 90-DAY ATTENDANCE ACTIVITY LOG VIEWER
        if is_admin:
            with st.expander("📜 Attendance Submission Audit Log (Last 90 Days • Auto-Purged)", expanded=False):
                aud_log_df = load_sheet_df("attendance_audit_log")
                if aud_log_df.empty or "Timestamp" not in aud_log_df.columns:
                    st.info("No attendance audit logs recorded yet.")
                else:
                    st.dataframe(aud_log_df.iloc[::-1][["Timestamp", "Attendance_Date", "Logged_By_User", "Action_Type", "Summary"]], use_container_width=True, hide_index=True)


# ----------------- VIEW 5: MONTHLY ATTENDANCE MATRIX -----------------
elif current_view == "Monthly Attendance Matrix":
    st.markdown("#### Monthly Attendance Matrix (Excel Register)")
    st.caption("Day-by-day attendance register showing only employees with at least 1 Present, Half-Day, or Paid Leave in the selected month")

    w_master = load_sheet_df("worker_master")
    muster_df = load_sheet_df("muster_roll")

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
    else:
        m_sub = muster_df[muster_df["Date"].astype(str).str.startswith(ym_str)].copy()
        m_sub["Duty_Location"] = m_sub["Duty_Location"].apply(normalize_duty_type)
        if mat_div != "All Divisions" and "Division" in m_sub.columns:
            m_sub = m_sub[m_sub["Division"] == mat_div]

        if m_sub.empty:
            st.info(f"No attendance entries found for {mat_month} {mat_year}.")
        else:
            present_wids = m_sub[m_sub["Attendance"].isin(["P", "HD", "L"])]["Worker_ID"].astype(str).unique().tolist()
            
            if not present_wids:
                st.warning(f"No employee has any Present ('P', 'HD', or 'L') days in {mat_month} {mat_year}.")
            else:
                matrix_rows = []
                for wid in sorted(present_wids):
                    w_logs = m_sub[m_sub["Worker_ID"].astype(str) == wid]
                    w_name = str(w_logs.iloc[0].get("Worker_Name", ""))
                    w_div_val = str(w_logs.iloc[0].get("Division", ""))
                    
                    w_desig = ""
                    w_skill_v = ""
                    if not w_master.empty and "Worker_ID" in w_master.columns:
                        wm_hit = w_master[w_master["Worker_ID"].astype(str) == wid]
                        if not wm_hit.empty:
                            w_name = str(wm_hit.iloc[0].get("Full_Name", w_name))
                            w_desig = str(wm_hit.iloc[0].get("Designation", ""))
                            w_skill_v = str(wm_hit.iloc[0].get("Skill_Category", ""))

                    row_dict = {
                        "Worker ID": wid,
                        "Employee Name": w_name,
                        "Category": w_skill_v,
                        "Designation": w_desig,
                        "Division": w_div_val
                    }

                    day_map = {}
                    for _, lr in w_logs.iterrows():
                        d_parts = str(lr["Date"]).split("-")
                        if len(d_parts) == 3:
                            day_map[int(d_parts[2])] = str(lr["Attendance"])

                    for d_i in range(1, days_cnt + 1):
                        row_dict[f"{d_i:02d}"] = day_map.get(d_i, "")

                    punch_p = len(w_logs[(w_logs["Duty_Location"] == "Punch") & (w_logs["Attendance"] == "P")]) + (
                        len(w_logs[(w_logs["Duty_Location"] == "Punch") & (w_logs["Attendance"] == "HD")]) * 0.5
                    )
                    non_punch_p = len(w_logs[(w_logs["Duty_Location"] == "Non-Punch") & (w_logs["Attendance"] == "P")]) + (
                        len(w_logs[(w_logs["Duty_Location"] == "Non-Punch") & (w_logs["Attendance"] == "HD")]) * 0.5
                    )
                    paid_l = len(w_logs[w_logs["Attendance"] == "L"])
                    tot_present = punch_p + non_punch_p + paid_l
                    tot_ot = pd.to_numeric(w_logs["OT_Hours"], errors="coerce").fillna(0.0).sum()

                    row_dict["Punch Days"] = punch_p
                    row_dict["Non-Punch Days"] = non_punch_p
                    row_dict["Paid Leave"] = paid_l
                    row_dict["Total Days"] = tot_present
                    row_dict["OT Hrs"] = tot_ot

                    matrix_rows.append(row_dict)

                matrix_df = pd.DataFrame(matrix_rows)

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Active Present Workforce", f"{len(matrix_df)} Workers")
                k2.metric("Total Punch Man-Days", f"{matrix_df['Punch Days'].sum():.1f}")
                k3.metric("Total Non-Punch Man-Days", f"{matrix_df['Non-Punch Days'].sum():.1f}")
                k4.metric("Total Overtime Hours", f"{matrix_df['OT Hrs'].sum():.1f} hrs")

                st.dataframe(matrix_df, use_container_width=True, hide_index=True)

                csv_export = matrix_df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label=f"📥 Export {mat_month} {mat_year} Attendance Register (Excel / CSV)",
                    data=csv_export,
                    file_name=f"M_s_INDU_SAO_Attendance_Matrix_{ym_str}.csv",
                    mime="text/csv",
                    type="primary"
                )


# ----------------- VIEW 6: ADVANCE REGISTER WITH RUNNING CARRY-FORWARD LEDGER -----------------
elif current_view == "Advance & Expense Register":
    st.markdown("#### Advance & Expense Register (With Carry-Forward Ledger)")
    st.caption("Site advances deduct ONLY from Non-Punch / Fixed wages. Any unrecovered balance automatically carries forward to the next month.")

    w_master = load_sheet_df("worker_master")
    muster_df = load_sheet_df("muster_roll")
    adv_history = load_sheet_df("worker_advances")
    tata_df = load_sheet_df("tata_form_xvii")

    worker_options = {}
    if not w_master.empty and "Worker_ID" in w_master.columns:
        for _, r in w_master[w_master["Status"].astype(str).str.upper() == "ACTIVE"].iterrows():
            worker_options[f"{r['Full_Name']} ({r['Worker_ID']})"] = (r['Worker_ID'], r['Full_Name'], r['Division'])

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
                            "Txn_ID": f"ADV-{int(time.time())}",
                            "Date": a_date.strftime("%Y-%m-%d"),
                            "Worker_ID": w_id,
                            "Worker_Name": w_name,
                            "Division": w_div,
                            "Amount": a_amt,
                            "Payment_Mode": a_mode,
                            "Supervisor": st.session_state.current_user,
                            "Remarks": a_rem
                        }
                        updated_adv = pd.concat([adv_history, pd.DataFrame([new_adv])], ignore_index=True)
                        if save_sheet_df("worker_advances", updated_adv[ADVANCE_LOG_COLS]):
                            st.success("Transaction recorded.")
                            st.rerun()

    with c_adv2:
        with st.container(border=True):
            st.markdown("###### 📊 Worker-Wise Advance Balance & Carry-Forward Status")
            today_dt = datetime.date.today()
            payroll_now = compute_monthly_payroll_and_advances(w_master, muster_df, adv_history, tata_df, today_dt.year, today_dt.month)
            
            bal_rows = []
            for wid, pdata in payroll_now.items():
                if pdata["Total_Advance_Due"] > 0 or pdata["Closing_Carry_Forward"] > 0:
                    bal_rows.append({
                        "Worker ID": wid,
                        "Name": pdata["Worker_Name"],
                        "Opening Adv. (₹)": pdata["Opening_Advance"],
                        "This Month Adv. (₹)": pdata["Month_New_Advance"],
                        "Adjusted in Wages (₹)": pdata["Advance_Deducted"],
                        "Carry Forward Next Month (₹)": pdata["Closing_Carry_Forward"]
                    })
            if bal_rows:
                bal_df = pd.DataFrame(bal_rows)
                st.dataframe(bal_df, use_container_width=True, hide_index=True)
            else:
                st.info("All employee advances are fully settled (₹0 pending balance).")

        with st.container(border=True):
            st.markdown("###### Recent Advance Transactions")
            if not adv_history.empty and "Txn_ID" in adv_history.columns:
                st.dataframe(adv_history[["Date", "Worker_Name", "Amount", "Payment_Mode", "Supervisor", "Remarks"]].tail(15), use_container_width=True, hide_index=True)
            else:
                st.info("No recorded transactions.")


# ----------------- VIEW 7: WAGES, FORM XVII, 3-UP SALARY SLIPS & DIRECT PDF BANK LETTERS -----------------
elif current_view == "Monthly Wages & Payout Summary":
    st.markdown("#### Monthly Wages, Form XVII Audit, Salary Slips & Bank Letters")
    st.caption("Strict Form XVII bank transfers, Non-Punch/Fixed payroll (with PF/ESIC/Paid Leave/Carry-Forward), and 3-up Salary Slips")

    w_master = load_sheet_df("worker_master")
    muster_df = load_sheet_df("muster_roll")
    adv_df = load_sheet_df("worker_advances")
    tata_df = load_sheet_df("tata_form_xvii")

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
        st.markdown(f"Upload official **Form XVII [Rule 78(2)(a)]** for **{wage_period_str}**")
        xvii_file = st.file_uploader("Upload Form XVII (PDF / CSV / XLSX)", type=["pdf", "csv", "xlsx"])

        if xvii_file:
            x_df = pd.DataFrame()
            if xvii_file.name.lower().endswith(".pdf"):
                if not PYPDF_AVAILABLE:
                    st.error("⚠️ PDF reader ('pypdf') not installed. Add 'pypdf' to requirements.txt or upload CSV/Excel.")
                else:
                    with st.spinner("Extracting records from Form XVII PDF..."):
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

                import_mode = st.radio(
                    "Import Mode for Same Month",
                    [
                        "Replace Month Data (Overwrite existing entries for this month)",
                        "Append / Merge with Existing Month (Combine multiple bills/divisions)"
                    ],
                    horizontal=True
                )

                if st.button("Commit Form XVII Compliance Data", type="primary", use_container_width=True):
                    new_records = []
                    for _, xr in x_df.iterrows():
                        w_name_raw = str(
                            xr.get("Worker_Name") or xr.get("Name_of_Workman") or xr.get("Name") or ""
                        ).strip()
                        
                        if not w_name_raw or w_name_raw.lower() == "nan" or "TOTAL" in w_name_raw.upper():
                            continue

                        appr_days = float(
                            pd.to_numeric(xr.get("Approved_Days") or xr.get("No_of_days_worked") or xr.get("No._of_days_worked") or 0.0, errors="coerce") or 0.0
                        )
                        
                        net_paid = float(
                            pd.to_numeric(xr.get("Net_Amount_Paid") or xr.get("Net_Amount") or 0.0, errors="coerce") or 0.0
                        )

                        if net_paid <= 0:
                            continue

                        matched_wid = ""
                        if not w_master.empty and "Full_Name" in w_master.columns:
                            match_w = w_master[w_master["Full_Name"].astype(str).str.strip().str.upper() == w_name_raw.upper()]
                            if not match_w.empty:
                                matched_wid = str(match_w.iloc[0]["Worker_ID"])

                        new_records.append({
                            "Wages_Period": target_ym,
                            "Worker_ID": matched_wid,
                            "Worker_Name": w_name_raw.upper(),
                            "Approved_Days": appr_days,
                            "Net_Amount_Paid": net_paid,
                            "Uploaded_At": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        })

                    new_df = pd.DataFrame(new_records)
                    if import_mode.startswith("Replace"):
                        if not tata_df.empty and "Wages_Period" in tata_df.columns:
                            tata_df = tata_df[~tata_df["Wages_Period"].apply(period_matches)]
                        final_tata = pd.concat([tata_df, new_df], ignore_index=True)
                    else:
                        existing_m = pd.DataFrame(columns=TATA_COMPLIANCE_COLS)
                        other_m = tata_df.copy()
                        if not tata_df.empty and "Wages_Period" in tata_df.columns:
                            existing_m = tata_df[tata_df["Wages_Period"].apply(period_matches)].copy()
                            other_m = tata_df[~tata_df["Wages_Period"].apply(period_matches)].copy()
                        
                        combined_m = pd.concat([existing_m, new_df], ignore_index=True)
                        combined_m["Approved_Days"] = pd.to_numeric(combined_m["Approved_Days"], errors="coerce").fillna(0.0)
                        combined_m["Net_Amount_Paid"] = pd.to_numeric(combined_m["Net_Amount_Paid"], errors="coerce").fillna(0.0)
                        merged_month_df = combined_m.groupby("Worker_Name", as_index=False).agg({
                            "Wages_Period": "first",
                            "Worker_ID": "first",
                            "Approved_Days": "sum",
                            "Net_Amount_Paid": "sum",
                            "Uploaded_At": "last"
                        })
                        final_tata = pd.concat([other_m, merged_month_df[TATA_COMPLIANCE_COLS]], ignore_index=True)

                    if save_sheet_df("tata_form_xvii", final_tata[TATA_COMPLIANCE_COLS]):
                        st.success(f"Committed {len(new_records)} records for {wage_period_str} ({import_mode.split('(')[0].strip()})!")
                        st.rerun()
            else:
                if not xvii_file.name.lower().endswith(".pdf") or PYPDF_AVAILABLE:
                    st.error("Could not find 'Net_Amount_Paid' column in uploaded file.")

    st.markdown(f"###### Reconciliation & Payroll Cockpit ({wage_period_str})")

    payroll_results = compute_monthly_payroll_and_advances(w_master, muster_df, adv_df, tata_df, sel_year, month_num)

    recon_rows = []
    boi_batch = []
    neft_batch = []
    salary_slip_records = []

    for wid, p in payroll_results.items():
        if p["Status"] != "ACTIVE" and p["Total_Days"] == 0 and p["Form_XVII_Net"] == 0 and p["Closing_Carry_Forward"] == 0:
            continue

        punch_days = p["Punch_Days"]
        f17_days = p["Form_XVII_Days"]
        f17_net = p["Form_XVII_Net"]
        b_acc = p["Bank_Account_No"]
        b_ifsc = p["Bank_IFSC_Code"]
        b_branch = p["Bank_Branch_Details"]

        status_badge = "🟢 Matched"
        if f17_days == 0 and punch_days > 0:
            status_badge = "🔴 No Punch (Field Only)"
        elif f17_days != punch_days:
            status_badge = f"🟡 Variance ({punch_days - f17_days:+.1f} d)"

        if p["Total_Days"] > 0 or f17_net > 0 or p["Total_Advance_Due"] > 0:
            salary_slip_records.append(p)

        recon_rows.append({
            "Worker ID": wid,
            "Name": p["Worker_Name"],
            "Punch": punch_days,
            "Non-Punch": p["Non_Punch_Days"],
            "Leave (L)": p["Paid_Leave_Days"],
            "Form XVII": f17_days,
            "Status": status_badge,
            "Form XVII Bank (₹)": f17_net,
            "Non-Punch/Fixed Net (₹)": p["Non_Punch_Net_Pay"],
            "Adv. Ded. (₹)": p["Advance_Deducted"],
            "Carry Fwd Adv (₹)": p["Closing_Carry_Forward"]
        })

        if f17_net > 0 and b_acc:
            item = {
                "NAME": p["Worker_Name"],
                "AMOUNT": f17_net,
                "BENEFICIARY ACCOUNT NO": b_acc,
                "IFSC CODE": b_ifsc,
                "BANK & BRANCH DETAILS": b_branch
            }
            if b_ifsc.startswith("BKID"):
                boi_batch.append(item)
            else:
                neft_batch.append(item)

    recon_df = pd.DataFrame(recon_rows)
    if not recon_df.empty:
        st.dataframe(recon_df, use_container_width=True, hide_index=True)

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        st.markdown("###### 🧾 Worker Salary Slips (3 Slips per A4 Page • PF, ESIC, Form XVII & Carry-Forward Advance)")
        if not salary_slip_records:
            st.info("No active attendance or Form XVII payouts found for this month to generate Salary Slips.")
        else:
            slip_options = {
                f"{r['Worker_ID']} — {r['Worker_Name']} (Net: ₹{r['Net_Payable']:,.0f} | CF Adv: ₹{r['Closing_Carry_Forward']:,.0f})": r["Worker_ID"]
                for r in salary_slip_records
            }
            sc_1, sc_2 = st.columns([2.1, 1.1])
            with sc_1:
                sel_slip_labels = st.multiselect(
                    "Select Specific Employees for Salary Slips (Leave empty to print all)",
                    options=list(slip_options.keys()),
                    placeholder="Choose specific employees or leave empty for all...",
                    label_visibility="collapsed"
                )
            with sc_2:
                chosen_slip_wids = [slip_options[l] for l in sel_slip_labels] if sel_slip_labels else list(slip_options.values())
                filtered_slips = [r for r in salary_slip_records if r["Worker_ID"] in chosen_slip_wids]
                if FPDF_AVAILABLE and filtered_slips:
                    slips_pdf_bytes = generate_compact_salary_slips_pdf(filtered_slips, wage_period_str)
                    slip_pages = (len(filtered_slips) + 2) // 3
                    st.download_button(
                        label=f"📥 Download Salary Slips PDF ({len(filtered_slips)} Slips • {slip_pages} Page)",
                        data=slips_pdf_bytes,
                        file_name=f"M_s_INDU_SAO_Salary_Slips_{wage_period_str}.pdf",
                        mime="application/pdf",
                        type="primary",
                        use_container_width=True
                    )
                elif not FPDF_AVAILABLE:
                    st.warning("Add 'fpdf2' to requirements.txt.")

    st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)
    st.markdown("###### Automated Bank Covering Letters (Strict Form XVII Bank Payouts)")

    p_c1, p_c2 = st.columns([1.2, 1.2])
    with p_c1:
        chq_no = st.text_input("Cheque Number", value="238883", key="bank_chq_no").strip()
    with p_c2:
        chq_date = st.text_input("Cheque Date (DD.MM.YYYY)", value=datetime.date.today().strftime("%d.%m.%Y"), key="bank_chq_dt").strip()

    tot_boi = sum([r["AMOUNT"] for r in boi_batch])
    tot_neft = sum([r["AMOUNT"] for r in neft_batch])

    m1, m2, m3 = st.columns(3)
    m1.metric("Bank of India (BOI)", f"₹{tot_boi:,.0f}", f"{len(boi_batch)} Beneficiaries")
    m2.metric("Other Banks (NEFT)", f"₹{tot_neft:,.0f}", f"{len(neft_batch)} Beneficiaries")
    m3.metric("Total Transfer", f"₹{(tot_boi + tot_neft):,.0f}", "Compliance Locked")

    dl_c1, dl_c2 = st.columns(2)
    with dl_c1:
        if boi_batch:
            if FPDF_AVAILABLE:
                boi_pdf_bytes = generate_bank_authority_letter_pdf("BOI", boi_batch, chq_no, chq_date)
                st.download_button(
                    label=f"📥 Download BOI Covering Letter PDF ({len(boi_batch)} Workers • ₹{tot_boi:,.0f})",
                    data=boi_pdf_bytes,
                    file_name=f"BOI_Transfer_Letter_{wage_period_str}.pdf",
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True
                )
            else:
                st.warning("Add 'fpdf2' to requirements.txt for direct PDF download.")
        else:
            st.info("No Bank of India transfers in this batch.")

    with dl_c2:
        if neft_batch:
            if FPDF_AVAILABLE:
                neft_pdf_bytes = generate_bank_authority_letter_pdf("NEFT", neft_batch, chq_no, chq_date)
                st.download_button(
                    label=f"📥 Download NEFT Covering Letter PDF ({len(neft_batch)} Workers • ₹{tot_neft:,.0f})",
                    data=neft_pdf_bytes,
                    file_name=f"NEFT_Transfer_Letter_{wage_period_str}.pdf",
                    mime="application/pdf",
                    type="primary" if not boi_batch else "secondary",
                    use_container_width=True
                )
            else:
                st.warning("Add 'fpdf2' to requirements.txt for direct PDF download.")
        else:
            st.info("No NEFT transfers in this batch.")


# ----------------- VIEW 8: BILLING MODULE PLACEHOLDERS -----------------
elif current_view in ["Daily Logsheet Entry", "Monthly Statement & Abstract", "Locked RA Bills Repository", 
                      "Delivery Order (DO) Contract Management", "Vehicle Fleet Master Mapping", "Commercial Rate Master"]:
    st.markdown(f"#### {current_view}")
    st.caption("Commercial Fleet Logs, Contract Abstracts and Billing Repository")
    st.info(f"Module '{current_view}' is ready.")


# ----------------- VIEW 9: SITE FUEL RECORD (DELEGATED TO fuel_and_audit.py) -----------------
elif current_view in ["Daily Diesel Issue Slip", "Fleet Fuel Benchmark Master", "Theft & Pilferage Audit"]:
    render_fuel_module(
        current_view, load_sheet_df, save_sheet_df,
        st.session_state.current_user, st.session_state.current_role, draw_indu_sao_letterhead
    )


# ----------------- VIEW 10: ADMIN & USER PERMISSIONS (ADMIN ONLY) -----------------
elif current_view == "User Accounts & RBAC" and st.session_state.current_role == "ADMIN":
    st.markdown("#### User Accounts & Access Control (RBAC)")
    st.caption("Provision credentials and configure sub-menu permissions per user")

    col_u1, col_u2 = st.columns([1.1, 1.9], gap="large")

    with col_u1:
        with st.container(border=True):
            st.markdown("###### Provision / Update User")
            
            with st.form("create_user_form", clear_on_submit=False):
                new_username = st.text_input("Username").strip()
                new_password = st.text_input("Password", type="password").strip()
                new_name = st.text_input("Full Name").strip()
                new_contact = st.text_input("Contact Number").strip()
                
                f_c1, f_c2 = st.columns(2)
                with f_c1:
                    new_role = st.selectbox("Role", ["USER", "ADMIN"])
                with f_c2:
                    new_status = st.selectbox("Status", ["ACTIVE", "INACTIVE"])

                st.markdown("**Sub-Menu Permissions:**")
                selected_permissions = []
                
                with st.expander("Worker Attendance", expanded=True):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Attendance":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                with st.expander("Tata Steel Billing", expanded=(new_role == "ADMIN")):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Billing":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                with st.expander("Site Fuel Record", expanded=(new_role == "ADMIN")):
                    for t_name, m_type in TAB_REGISTRY.items():
                        if m_type == "Fuel":
                            is_checked = (new_role == "ADMIN")
                            if st.checkbox(t_name, key=f"perm_{t_name}_{new_username}", value=is_checked):
                                selected_permissions.append(t_name)

                submit_user = st.form_submit_button("Save User Account", use_container_width=True, type="primary")

                if submit_user:
                    if not new_username or not new_password:
                        st.error("Username and password are required.")
                    else:
                        users_df = load_sheet_df("app_users")
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
                            st.success(f"User '{new_username}' saved.")
                            st.rerun()

    with col_u2:
        with st.container(border=True):
            st.markdown("###### Active System Users")
            users_table = load_sheet_df("app_users")
            if not users_table.empty and "Username" in users_table.columns:
                disp_cols = [c for c in ["Username", "Password", "Full_Name", "Role", "Status", "Allowed_Tabs"] if c in users_table.columns]
                st.dataframe(users_table[disp_cols], use_container_width=True, hide_index=True)
