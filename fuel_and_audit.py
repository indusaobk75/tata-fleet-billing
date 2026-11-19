import io
import time
import base64
import datetime
import calendar
import pandas as pd
import streamlit as st

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ----------------- SCHEMAS -----------------
ATTENDANCE_AUDIT_COLS = [
    "Audit_ID", "Timestamp", "Attendance_Date", "Logged_By_User", "Action_Type", "Summary"
]

FUEL_FLEET_COLS = [
    "Vehicle_ID", "Vehicle_Type", "Fuel_Source_Default", "Meter_Type",
    "Expected_Benchmark", "Current_Meter", "Status", "Remarks"
]

FUEL_ISSUE_COLS = [
    "Txn_ID", "Date", "Shift", "Transaction_Type", "Vehicle_ID", "Driver_ID",
    "Driver_Name", "Litres", "Prev_Meter", "Curr_Meter", "Usage_Delta",
    "Actual_Efficiency", "Expected_Benchmark", "Variance_Litres", "Status_Flag",
    "Slip_Photo_Base64", "Logged_By", "Remarks"
]

FUEL_STOCK_COLS = [
    "Stock_ID", "Date", "Litres_Added", "Slip_Photo_Base64", "Logged_By", "Remarks"
]

FUEL_RECOVERY_COLS = [
    "Recovery_ID", "Period_YM", "Driver_ID", "Driver_Name", "Excess_Litres",
    "Diesel_Rate_Applied", "Calculated_Variance_Amt", "Approved_Deduction_Amt",
    "Approved_By", "Approved_Date", "Remarks"
]

ADVANCE_SYNC_COLS = [
    "Txn_ID", "Date", "Worker_ID", "Worker_Name", "Division", "Amount",
    "Payment_Mode", "Supervisor", "Remarks"
]


# ----------------- HELPERS -----------------
def compress_slip_photo_to_base64(uploaded_img):
    """Compresses slip/meter photo safely under Google Sheets 50k cell character limit."""
    if not uploaded_img:
        return ""
    try:
        if PIL_AVAILABLE:
            im = Image.open(uploaded_img).convert("RGB")
            im.thumbnail((520, 680))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=62)
            b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
            if len(b64_str) > 45000:
                im.thumbnail((360, 480))
                buf2 = io.BytesIO()
                im.save(buf2, format="JPEG", quality=50)
                b64_str = base64.b64encode(buf2.getvalue()).decode("utf-8")
            return b64_str
        else:
            raw = uploaded_img.getvalue()
            if len(raw) < 42000:
                return base64.b64encode(raw).decode("utf-8")
            return ""
    except Exception:
        return ""


def log_attendance_activity(load_fn, save_fn, att_date_str, user_label, action_type, summary_txt):
    """Logs attendance submission/edit and auto-purges entries older than 90 days."""
    try:
        aud_df = load_fn("attendance_audit_log")
        now_dt = datetime.datetime.now()
        cutoff_str = (now_dt - datetime.timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
        if not aud_df.empty and "Timestamp" in aud_df.columns:
            aud_df = aud_df[aud_df["Timestamp"].astype(str) >= cutoff_str].copy()
        new_row = {
            "Audit_ID": f"AUD-{int(time.time() * 1000)}",
            "Timestamp": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "Attendance_Date": str(att_date_str),
            "Logged_By_User": str(user_label),
            "Action_Type": str(action_type),
            "Summary": str(summary_txt)
        }
        upd_aud = pd.concat([aud_df, pd.DataFrame([new_row])], ignore_index=True)
        save_fn("attendance_audit_log", upd_aud[ATTENDANCE_AUDIT_COLS])
    except Exception:
        pass


def compute_bowser_stock_summary(stock_df, issue_df):
    """Calculates Live Bowser Stock = Total Bowser Refilled - Total Issued from Own Bowser."""
    tot_in = 0.0
    if stock_df is not None and not stock_df.empty and "Litres_Added" in stock_df.columns:
        tot_in = float(pd.to_numeric(stock_df["Litres_Added"], errors="coerce").fillna(0.0).sum())

    tot_bowser_out = 0.0
    tot_pump_out = 0.0
    if issue_df is not None and not issue_df.empty and "Litres" in issue_df.columns:
        tx_col = issue_df["Transaction_Type"].astype(str) if "Transaction_Type" in issue_df.columns else pd.Series(["Issue from Own Bowser"] * len(issue_df))
        l_vals = pd.to_numeric(issue_df["Litres"], errors="coerce").fillna(0.0)
        tot_bowser_out = float(l_vals[tx_col == "Issue from Own Bowser"].sum())
        tot_pump_out = float(l_vals[tx_col == "Direct from Fuel Pump"].sum())

    live_balance = tot_in - tot_bowser_out
    return tot_in, tot_bowser_out, tot_pump_out, live_balance


# ----------------- MAIN RENDER ENTRYPOINT FOR FUEL & AUDIT MODULE -----------------
def render_fuel_module(current_view, load_fn, save_fn, current_user, current_role, letterhead_fn):
    is_admin = (str(current_role).upper() == "ADMIN")
    fleet_df = load_fn("fuel_fleet_master")
    issue_df = load_fn("fuel_issue_log")
    stock_df = load_fn("fuel_stock_log")
    worker_df = load_fn("worker_master")
    muster_df = load_fn("muster_roll")

    # =========================================================================
    # VIEW 1: 3-IN-1 UNIFIED DAILY FUEL & BOWSER SLIP (BOWSER / PUMP / RESTOCK)
    # =========================================================================
    if current_view == "Daily Diesel Issue Slip":
        st.markdown("#### Daily Fuel & Diesel Bowser Register")
        st.caption("Unified 1-screen fuel issuance (Own Bowser & Fuel Pump), Bowser Restocking, Fleet Driver Linking, and Slip Photo Proof")

        tot_in, tot_bowser_out, tot_pump_out, live_bowser_bal = compute_bowser_stock_summary(stock_df, issue_df)
        today_str = datetime.date.today().strftime("%Y-%m-%d")

        today_bowser_l = 0.0
        today_pump_l = 0.0
        if not issue_df.empty and "Date" in issue_df.columns:
            tod_sub = issue_df[issue_df["Date"].astype(str) == today_str]
            if not tod_sub.empty:
                l_tod = pd.to_numeric(tod_sub["Litres"], errors="coerce").fillna(0.0)
                tx_tod = tod_sub["Transaction_Type"].astype(str)
                today_bowser_l = float(l_tod[tx_tod == "Issue from Own Bowser"].sum())
                today_pump_l = float(l_tod[tx_tod == "Direct from Fuel Pump"].sum())

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("⛽ Live Bowser Balance", f"{live_bowser_bal:,.1f} L", f"Total Refilled: {tot_in:,.0f} L")
        k2.metric("Today's Bowser Issue", f"{today_bowser_l:,.1f} L", "Fixed Site Machines")
        k3.metric("Today's Fuel Pump Issue", f"{today_pump_l:,.1f} L", "Road / Hyva Vehicles")
        k4.metric("Cumulative Pump Diesel", f"{tot_pump_out:,.1f} L", "External Pump Slips")

        if live_bowser_bal < 500:
            st.warning(f"⚠️ **Low Bowser Fuel Alert:** Only **{live_bowser_bal:,.1f} Litres** remaining in Own Diesel Bowser.")

        st.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)

        # Prepare Active Fleet Vehicles & Active Fleet Operators/Drivers from Worker Master
        active_vehicles = fleet_df[fleet_df["Status"].astype(str).str.upper() == "ACTIVE"].copy() if (not fleet_df.empty and "Status" in fleet_df.columns) else pd.DataFrame()
        fleet_drivers = pd.DataFrame()
        if not worker_df.empty and "Division" in worker_df.columns and "Status" in worker_df.columns:
            fleet_drivers = worker_df[
                (worker_df["Division"].astype(str).str.strip() == "Fleet Operations") &
                (worker_df["Status"].astype(str).str.upper() == "ACTIVE")
            ].copy()

        c_form, c_log = st.columns([1.15, 1.85], gap="large")

        with c_form:
            with st.container(border=True):
                st.markdown("###### 📝 New Fuel / Bowser Entry")
                tx_type = st.selectbox(
                    "Transaction Type",
                    ["Issue from Own Bowser", "Direct from Fuel Pump", "Bowser Restock / Refill"],
                    key="fuel_tx_type"
                )

                f_date = st.date_input("Date", value=datetime.date.today(), key="fuel_entry_dt")
                f_date_str = f_date.strftime("%Y-%m-%d")

                # OPTION C: BOWSER RESTOCK / REFILL IN SAME SCREEN
                if tx_type == "Bowser Restock / Refill":
                    st.info("Record bulk diesel refilled into M/s INDU SAO's own Diesel Bowser.")
                    refill_litres = st.number_input("Diesel Refilled into Bowser (Litres)", min_value=10.0, value=1000.0, step=50.0, key="bowser_refill_l")
                    refill_photo = st.file_uploader("Upload Tanker / Refill Slip Photo (Required)", type=["jpg", "jpeg", "png"], key="bowser_refill_img")
                    if refill_photo:
                        st.image(refill_photo, width=140)
                    refill_rem = st.text_input("Remarks (Optional)", placeholder="e.g. Bulk Tanker Refill", key="bowser_refill_rem").strip()

                    if st.button("💾 Save Bowser Restock Entry", type="primary", use_container_width=True):
                        if not refill_photo:
                            st.error("Please upload the Tanker / Refill Slip photo for cross-verification.")
                        else:
                            b64_img = compress_slip_photo_to_base64(refill_photo)
                            new_stock = {
                                "Stock_ID": f"STK-{int(time.time())}",
                                "Date": f_date_str,
                                "Litres_Added": float(refill_litres),
                                "Slip_Photo_Base64": b64_img,
                                "Logged_By": current_user,
                                "Remarks": refill_rem
                            }
                            upd_stock = pd.concat([stock_df, pd.DataFrame([new_stock])], ignore_index=True)
                            if save_fn("fuel_stock_log", upd_stock[FUEL_STOCK_COLS]):
                                st.success(f"Added {refill_litres:,.1f} Litres to Bowser Stock!")
                                st.rerun()

                # OPTION A & B: ISSUE FROM OWN BOWSER OR DIRECT FROM FUEL PUMP
                else:
                    if active_vehicles.empty:
                        st.warning("No active vehicles/machines found. Please add vehicles in 'Vehicle Fuel Benchmark' first.")
                    elif fleet_drivers.empty:
                        st.warning("No active 'Fleet Operations' drivers/operators found in Employee Master.")
                    else:
                        f_shift = st.selectbox("Shift", ["Day Shift", "Night Shift"], key="fuel_shift")

                        veh_map = {}
                        for _, vr in active_vehicles.iterrows():
                            vid = str(vr["Vehicle_ID"])
                            vtype = str(vr.get("Vehicle_Type", ""))
                            mtype = str(vr.get("Meter_Type", "HMR (Hours)"))
                            bench = float(pd.to_numeric(vr.get("Expected_Benchmark", 0.0), errors="coerce") or 0.0)
                            cur_m = float(pd.to_numeric(vr.get("Current_Meter", 0.0), errors="coerce") or 0.0)
                            veh_map[f"{vid} — {vtype}"] = (vid, vtype, mtype, bench, cur_m)

                        sel_veh_lbl = st.selectbox("Vehicle / Machine No.", list(veh_map.keys()), key="fuel_veh_sel")
                        vid, vtype, mtype, bench, prev_meter = veh_map[sel_veh_lbl]

                        drv_map = {
                            f"{dr['Worker_ID']} — {dr['Full_Name']} ({dr['Designation']})": (str(dr["Worker_ID"]), str(dr["Full_Name"]))
                            for _, dr in fleet_drivers.iterrows()
                        }
                        sel_drv_lbl = st.selectbox("Driver / Operator on Duty (Fleet Master)", list(drv_map.keys()), key="fuel_drv_sel")
                        drv_id, drv_name = drv_map[sel_drv_lbl]

                        # Live Attendance Cross-Check for Selected Driver on Selected Date
                        att_warning = ""
                        if not muster_df.empty and "Date" in muster_df.columns and "Worker_ID" in muster_df.columns:
                            m_hit = muster_df[(muster_df["Date"].astype(str) == f_date_str) & (muster_df["Worker_ID"].astype(str) == drv_id)]
                            if not m_hit.empty:
                                att_st = str(m_hit.iloc[0].get("Attendance", "")).upper()
                                if att_st in ["A", "WO"]:
                                    att_warning = f"🚨 Attendance Alert: {drv_name} is marked '{att_st}' on {f_date_str}!"
                                    st.error(att_warning)
                                else:
                                    st.caption(f"🟢 Attendance Verified: {drv_name} is marked `{att_st}` on {f_date_str}.")

                        unit_short = "Hrs" if "HMR" in mtype.upper() else "KM"
                        st.caption(f"Last Recorded Meter ({unit_short}): **{prev_meter:,.1f}** | Benchmark: **{bench:,.2f} {'L/Hr' if unit_short == 'Hrs' else 'KM/L'}**")

                        fc_1, fc_2 = st.columns(2)
                        with fc_1:
                            litres_val = st.number_input("Fuel Quantity (Litres)", min_value=1.0, max_value=2000.0, value=50.0, step=1.0, key="fuel_litres")
                        with fc_2:
                            curr_meter = st.number_input(f"Current Meter ({unit_short})", min_value=0.0, value=float(prev_meter), step=1.0, key=f"fuel_curr_m_{vid}")

                        slip_photo = st.file_uploader(
                            "Upload Pump Slip / Meter Photo (Required for Cross-Verification)",
                            type=["jpg", "jpeg", "png"],
                            key="fuel_slip_img"
                        )
                        if slip_photo:
                            st.image(slip_photo, width=140)

                        f_rem = st.text_input("Remarks (Optional)", placeholder="e.g. Full tank / Site shift fueling", key="fuel_rem").strip()

                        if st.button("💾 Save Fuel Entry", type="primary", use_container_width=True):
                            if not slip_photo:
                                st.error("Slip / Meter photo is mandatory for cross-verification.")
                            elif tx_type == "Issue from Own Bowser" and litres_val > live_bowser_bal and live_bowser_bal > 0:
                                st.error(f"Insufficient Bowser Stock! Available balance is {live_bowser_bal:,.1f} L.")
                            elif curr_meter < prev_meter and prev_meter > 0:
                                st.error(f"Current Meter ({curr_meter:,.1f}) cannot be less than Previous Meter ({prev_meter:,.1f}).")
                            else:
                                usage_delta = max(0.0, curr_meter - prev_meter) if prev_meter > 0 else 0.0
                                actual_eff = 0.0
                                expected_litres = 0.0
                                variance_litres = 0.0
                                status_flag = "🟢 Normal"

                                if usage_delta > 0 and bench > 0:
                                    if unit_short == "Hrs":
                                        # Litres per Hour (Higher L/Hr = More Fuel Consumed)
                                        actual_eff = round(litres_val / usage_delta, 2)
                                        expected_litres = round(usage_delta * bench, 2)
                                        variance_litres = round(litres_val - expected_litres, 2)
                                    else:
                                        # KM per Litre (Lower KM/L = More Fuel Consumed)
                                        actual_eff = round(usage_delta / litres_val, 2)
                                        expected_litres = round(usage_delta / bench, 2)
                                        variance_litres = round(litres_val - expected_litres, 2)

                                    if variance_litres > 2.0:
                                        status_flag = f"🔴 Excess (+{variance_litres:.1f} L)"
                                    elif att_warning:
                                        status_flag = "🟠 Absent Driver Alert"
                                elif prev_meter == 0:
                                    status_flag = "⚪ Opening Reading"

                                b64_slip = compress_slip_photo_to_base64(slip_photo)
                                new_issue = {
                                    "Txn_ID": f"FL-{int(time.time())}",
                                    "Date": f_date_str,
                                    "Shift": f_shift,
                                    "Transaction_Type": tx_type,
                                    "Vehicle_ID": vid,
                                    "Driver_ID": drv_id,
                                    "Driver_Name": drv_name,
                                    "Litres": float(litres_val),
                                    "Prev_Meter": float(prev_meter),
                                    "Curr_Meter": float(curr_meter),
                                    "Usage_Delta": float(usage_delta),
                                    "Actual_Efficiency": float(actual_eff),
                                    "Expected_Benchmark": float(bench),
                                    "Variance_Litres": float(variance_litres),
                                    "Status_Flag": status_flag,
                                    "Slip_Photo_Base64": b64_slip,
                                    "Logged_By": current_user,
                                    "Remarks": f"{f_rem} {att_warning}".strip()
                                }
                                upd_issues = pd.concat([issue_df, pd.DataFrame([new_issue])], ignore_index=True)
                                if save_fn("fuel_issue_log", upd_issues[FUEL_ISSUE_COLS]):
                                    # Update Vehicle's Current Meter in Fleet Master
                                    fleet_df.loc[fleet_df["Vehicle_ID"].astype(str) == vid, "Current_Meter"] = float(curr_meter)
                                    save_fn("fuel_fleet_master", fleet_df[FUEL_FLEET_COLS])
                                    st.success(f"Fuel entry saved for {vid} ({litres_val:,.1f} L • Driver: {drv_name}).")
                                    st.rerun()

        # RIGHT COLUMN: RECENT LOGS + SLIP PHOTO CROSS-VERIFICATION VIEWER
        with c_log:
            with st.container(border=True):
                st.markdown("###### 📋 Recent Fuel Entries & Slip Photo Cross-Verification")
                if issue_df.empty or "Txn_ID" not in issue_df.columns:
                    st.info("No fuel entries recorded yet.")
                else:
                    disp_iss = issue_df.iloc[::-1].copy()
                    disp_iss["Slip"] = disp_iss["Slip_Photo_Base64"].apply(lambda x: "📸 Yes" if str(x).strip() else "—")
                    show_cols = ["Date", "Transaction_Type", "Vehicle_ID", "Driver_Name", "Litres", "Curr_Meter", "Variance_Litres", "Status_Flag", "Slip"]
                    st.dataframe(disp_iss[show_cols].head(25), use_container_width=True, hide_index=True)

                    st.markdown("---")
                    st.markdown("###### 🔍 Inspect Uploaded Slip / Meter Photo")
                    photo_entries = disp_iss[disp_iss["Slip_Photo_Base64"].astype(str).str.len() > 50]
                    if photo_entries.empty:
                        st.caption("No uploaded slip photos available yet.")
                    else:
                        p_opts = {
                            f"{r['Date']} | {r['Vehicle_ID']} | {r['Driver_Name']} ({r['Litres']} L) [{r['Txn_ID']}]": r
                            for _, r in photo_entries.head(40).iterrows()
                        }
                        sel_p_lbl = st.selectbox("Select Entry to View Slip Photo", list(p_opts.keys()), label_visibility="collapsed")
                        chosen_rec = p_opts[sel_p_lbl]
                        pv_c1, pv_c2 = st.columns([1.1, 1.3])
                        with pv_c1:
                            b64_data = str(chosen_rec["Slip_Photo_Base64"]).strip()
                            st.markdown(f'<img src="data:image/jpeg;base64,{b64_data}" style="max-width: 100%; max-height: 300px; border-radius: 8px; border: 1px solid #cbd5e1;" />', unsafe_allow_html=True)
                        with pv_c2:
                            st.markdown(f"**Txn ID:** `{chosen_rec['Txn_ID']}`")
                            st.markdown(f"**Source:** {chosen_rec['Transaction_Type']}")
                            st.markdown(f"**Vehicle:** `{chosen_rec['Vehicle_ID']}`")
                            st.markdown(f"**Driver:** {chosen_rec['Driver_Name']} (`{chosen_rec['Driver_ID']}`)")
                            st.markdown(f"**Fuel Issued:** **{chosen_rec['Litres']} Litres**")
                            st.markdown(f"**Meter Reading:** `{chosen_rec['Prev_Meter']}` → **`{chosen_rec['Curr_Meter']}`**")
                            st.markdown(f"**Audit Flag:** {chosen_rec['Status_Flag']}")
                            if str(chosen_rec.get("Remarks", "")).strip():
                                st.caption(f"Remarks: {chosen_rec['Remarks']}")

            if not stock_df.empty and "Stock_ID" in stock_df.columns:
                with st.expander("🛢️ Bowser Restock / Refill History", expanded=False):
                    st.dataframe(stock_df.iloc[::-1][["Date", "Litres_Added", "Logged_By", "Remarks"]], use_container_width=True, hide_index=True)

    # =========================================================================
    # VIEW 2: VEHICLE / MACHINE FUEL BENCHMARK MASTER (NO FIXED DRIVER)
    # =========================================================================
    elif current_view == "Fleet Fuel Benchmark Master":
        st.markdown("#### Vehicle & Machine Fuel Benchmark Master")
        st.caption("Configure site machines & vehicles with standard fuel benchmarks (No fixed driver assignment — drivers are selected dynamically at fueling time)")

        c_v1, c_v2 = st.columns([1.1, 1.9], gap="large")
        with c_v1:
            with st.container(border=True):
                st.markdown("###### ➕ Add / Update Vehicle or Machine")
                with st.form("add_fuel_vehicle_form", clear_on_submit=True):
                    v_id = st.text_input("Vehicle / Machine No. (e.g. Excavator 215L, JH-02-AB-1234)").strip().upper()
                    v_type = st.selectbox(
                        "Equipment / Vehicle Category",
                        ["Excavator", "Vibratory Roller", "Hyva Tipper", "Wheel Loader", "JCB Backhoe", "Dozer", "Hydra Crane", "Service Pickup / Bolero", "Other"]
                    )
                    def_src = st.selectbox("Primary Fueling Source", ["Issue from Own Bowser", "Direct from Fuel Pump"])
                    m_type = st.selectbox("Meter Unit", ["HMR (Hours)", "ODOMETER (KM)"])
                    bench_val = st.number_input(
                        "Expected Benchmark (L/Hr for HMR, or KM/L for Odometer)",
                        min_value=0.5, value=14.0, step=0.5
                    )
                    open_meter = st.number_input("Current / Opening Meter Reading", min_value=0.0, value=0.0, step=1.0)
                    v_stat = st.selectbox("Status", ["ACTIVE", "INACTIVE"])
                    v_rem = st.text_input("Remarks (Optional)").strip()

                    if st.form_submit_button("💾 Save Vehicle Benchmark", type="primary", use_container_width=True):
                        if not v_id:
                            st.error("Vehicle / Machine No. is required.")
                        else:
                            cur_f = load_fn("fuel_fleet_master")
                            if not cur_f.empty and "Vehicle_ID" in cur_f.columns:
                                cur_f = cur_f[cur_f["Vehicle_ID"].astype(str).str.upper() != v_id]
                            new_v = {
                                "Vehicle_ID": v_id,
                                "Vehicle_Type": v_type,
                                "Fuel_Source_Default": def_src,
                                "Meter_Type": m_type,
                                "Expected_Benchmark": float(bench_val),
                                "Current_Meter": float(open_meter),
                                "Status": v_stat,
                                "Remarks": v_rem
                            }
                            upd_f = pd.concat([cur_f, pd.DataFrame([new_v])], ignore_index=True)
                            if save_fn("fuel_fleet_master", upd_f[FUEL_FLEET_COLS]):
                                st.success(f"Saved {v_id} ({bench_val} {'L/Hr' if 'HMR' in m_type else 'KM/L'}).")
                                st.rerun()

        with c_v2:
            with st.container(border=True):
                st.markdown("###### 📋 Active Fleet & Machine Fuel Benchmarks")
                if fleet_df.empty or "Vehicle_ID" not in fleet_df.columns:
                    st.info("No vehicles or machines added yet.")
                else:
                    edited_fleet = st.data_editor(
                        fleet_df[FUEL_FLEET_COLS],
                        use_container_width=True,
                        hide_index=True,
                        disabled=["Vehicle_ID"] if not is_admin else [],
                        key="fuel_fleet_editor"
                    )
                    if is_admin and st.button("💾 Save Table Edits", type="primary"):
                        if save_fn("fuel_fleet_master", edited_fleet[FUEL_FLEET_COLS]):
                            st.success("Vehicle benchmark table updated!")
                            st.rerun()

    # =========================================================================
    # VIEW 3: VARIANCE AUDIT & ADMIN REVIEW ADVANCE DEDUCTION
    # =========================================================================
    elif current_view == "Theft & Pilferage Audit":
        st.markdown("#### Fuel Variance Audit & Admin Recovery Deduction")
        st.caption("Vehicle-wise and Driver-wise fuel efficiency audit with Admin-reviewed recovery posting directly to the Worker Advance Ledger")

        rec_log_df = load_fn("fuel_recovery_log")

        ac1, ac2, ac3 = st.columns([1.0, 1.1, 1.1])
        with ac1:
            aud_year = st.selectbox("Audit Year", [2025, 2026, 2027], index=1, key="faud_yr")
        with ac2:
            aud_month = st.selectbox("Audit Month", list(calendar.month_name)[1:], index=datetime.date.today().month - 1, key="faud_mo")
        with ac3:
            diesel_rate_inr = st.number_input("Diesel Rate for Variance Valuation (₹/Litre)", min_value=50.0, max_value=150.0, value=92.0, step=0.5, key="faud_rate")

        m_num = list(calendar.month_name).index(aud_month)
        target_ym = f"{aud_year}-{m_num:02d}"

        if issue_df.empty or "Date" not in issue_df.columns:
            st.info(f"No fuel issue records found for {aud_month} {aud_year}.")
            return

        m_issues = issue_df[issue_df["Date"].astype(str).str.startswith(target_ym)].copy()
        if m_issues.empty:
            st.info(f"No fuel entries recorded in {aud_month} {aud_year}.")
            return

        for num_c in ["Litres", "Usage_Delta", "Variance_Litres", "Expected_Benchmark"]:
            m_issues[num_c] = pd.to_numeric(m_issues[num_c], errors="coerce").fillna(0.0)

        # 1. TABLE 1: VEHICLE / MACHINE-WISE FUEL VARIANCE
        with st.container(border=True):
            st.markdown(f"###### 🚜 1. Vehicle / Machine-Wise Fuel Variance ({aud_month} {aud_year})")
            veh_rows = []
            for vid, grp in m_issues.groupby("Vehicle_ID"):
                tot_l = float(grp["Litres"].sum())
                tot_u = float(grp["Usage_Delta"].sum())
                tot_var_l = float(grp["Variance_Litres"].sum())
                bench_v = float(grp["Expected_Benchmark"].iloc[-1])

                m_unit = "Hrs"
                if not fleet_df.empty and "Vehicle_ID" in fleet_df.columns:
                    f_hit = fleet_df[fleet_df["Vehicle_ID"].astype(str) == str(vid)]
                    if not f_hit.empty and "ODOMETER" in str(f_hit.iloc[0].get("Meter_Type", "")).upper():
                        m_unit = "KM"

                if tot_u > 0:
                    act_avg = round(tot_l / tot_u, 2) if m_unit == "Hrs" else round(tot_u / tot_l, 2)
                else:
                    act_avg = 0.0

                avg_lbl = f"{act_avg:.2f} {'L/Hr' if m_unit == 'Hrs' else 'KM/L'}" if act_avg > 0 else "N/A"
                bench_lbl = f"{bench_v:.2f} {'L/Hr' if m_unit == 'Hrs' else 'KM/L'}"
                var_amt = round(max(0.0, tot_var_l) * diesel_rate_inr, 0)
                st_flag = f"🔴 Excess (+{tot_var_l:.1f} L)" if tot_var_l > 5.0 else "🟢 Within Benchmark"

                veh_rows.append({
                    "Vehicle / Machine": vid,
                    "Refuels": len(grp),
                    "Total Diesel (L)": round(tot_l, 1),
                    f"Total Run ({m_unit})": round(tot_u, 1),
                    "Actual Avg": avg_lbl,
                    "Benchmark": bench_lbl,
                    "Excess Fuel (L)": round(max(0.0, tot_var_l), 1),
                    "Variance Value (₹)": var_amt,
                    "Audit Status": st_flag
                })
            st.dataframe(pd.DataFrame(veh_rows), use_container_width=True, hide_index=True)

        # 2. TABLE 2: DRIVER / OPERATOR-WISE FUEL ACCOUNTABILITY & VARIANCE
        with st.container(border=True):
            st.markdown(f"###### 👷 2. Driver / Operator-Wise Fuel Accountability & Variance ({aud_month} {aud_year})")

            recovered_map = {}
            if not rec_log_df.empty and "Period_YM" in rec_log_df.columns:
                r_sub = rec_log_df[rec_log_df["Period_YM"].astype(str) == target_ym]
                for _, rr in r_sub.iterrows():
                    d_k = str(rr.get("Driver_ID", ""))
                    recovered_map[d_k] = recovered_map.get(d_k, 0.0) + float(pd.to_numeric(rr.get("Approved_Deduction_Amt", 0.0), errors="coerce") or 0.0)

            drv_rows = []
            drv_candidates = {}

            for (did, dname), dgrp in m_issues.groupby(["Driver_ID", "Driver_Name"]):
                tot_dl = float(dgrp["Litres"].sum())
                tot_du = float(dgrp["Usage_Delta"].sum())
                excess_dl = round(max(0.0, float(dgrp["Variance_Litres"].sum())), 1)
                calc_var_inr = round(excess_dl * diesel_rate_inr, 0)
                machines_used = ", ".join(sorted(dgrp["Vehicle_ID"].astype(str).unique().tolist()))
                absent_alerts = int(dgrp["Status_Flag"].astype(str).str.contains("Absent Driver").sum())
                already_rec = recovered_map.get(str(did), 0.0)

                if already_rec > 0:
                    d_status = f"✅ Recovered ₹{already_rec:,.0f} in Advance Ledger"
                elif excess_dl > 3.0 or absent_alerts > 0:
                    d_status = "🔴 Review Required"
                else:
                    d_status = "🟢 Clean"

                drv_rows.append({
                    "Driver ID": did,
                    "Driver / Operator Name": dname,
                    "Machines Operated": machines_used,
                    "Total Diesel Drawn (L)": round(tot_dl, 1),
                    "Meter Run Logged": round(tot_du, 1),
                    "Excess Fuel (L)": excess_dl,
                    "Calculated Variance (₹)": calc_var_inr,
                    "Absent Alerts": absent_alerts,
                    "Recovery Status": d_status
                })

                if excess_dl > 0 or absent_alerts > 0:
                    drv_candidates[f"{did} — {dname} (Excess: {excess_dl} L | Est. ₹{calc_var_inr:,.0f})"] = {
                        "Driver_ID": str(did),
                        "Driver_Name": str(dname),
                        "Excess_Litres": excess_dl,
                        "Calculated_Variance_Amt": calc_var_inr,
                        "Already_Recovered": already_rec
                    }

            st.dataframe(pd.DataFrame(drv_rows), use_container_width=True, hide_index=True)

        # 3. ADMIN REVIEW & POST DEDUCTION TO WORKER ADVANCE LEDGER
        if is_admin:
            with st.container(border=True):
                st.markdown("###### ⚖️ 3. Admin Review: Post Fuel Variance Deduction to Worker Advance Ledger")
                st.caption("Review the variance after considering site conditions. Approved deduction posts directly to the worker's Advance Ledger and automatically deducts from their Monthly Salary Slip.")

                if not drv_candidates:
                    st.success("No excess fuel variance or attendance mismatch pending review for this month.")
                else:
                    sel_cand_lbl = st.selectbox("Select Driver / Operator for Recovery Review", list(drv_candidates.keys()), key="rec_drv_sel")
                    cand = drv_candidates[sel_cand_lbl]

                    if cand["Already_Recovered"] > 0:
                        st.info(f"Note: ₹{cand['Already_Recovered']:,.0f} has already been posted to {cand['Driver_Name']}'s Advance Ledger for {aud_month} {aud_year}.")

                    rc1, rc2, rc3 = st.columns([1.0, 1.0, 1.8])
                    with rc1:
                        st.metric("Calculated Variance", f"₹{cand['Calculated_Variance_Amt']:,.0f}", f"{cand['Excess_Litres']:.1f} L @ ₹{diesel_rate_inr}/L")
                    with rc2:
                        approved_amt = st.number_input(
                            "Final Approved Deduction (₹)",
                            min_value=0.0,
                            value=float(cand["Calculated_Variance_Amt"]),
                            step=50.0,
                            key=f"app_ded_amt_{cand['Driver_ID']}"
                        )
                    with rc3:
                        rec_remarks = st.text_input(
                            "Admin Audit Justification / Remarks",
                            value=f"Fuel Variance Recovery ({aud_month} {aud_year}: {cand['Excess_Litres']:.1f} L excess)",
                            key=f"app_ded_rem_{cand['Driver_ID']}"
                        ).strip()

                    if st.button("🚨 Confirm & Post Deduction to Worker Advance Ledger", type="primary", use_container_width=True):
                        if approved_amt <= 0:
                            st.warning("Approved deduction amount must be greater than ₹0.")
                        else:
                            now_dt = datetime.datetime.now()
                            # Post to worker_advances so payroll engine automatically deducts it
                            adv_df = load_fn("worker_advances")
                            new_adv_row = {
                                "Txn_ID": f"FUEL-REC-{int(time.time())}",
                                "Date": now_dt.strftime("%Y-%m-%d"),
                                "Worker_ID": cand["Driver_ID"],
                                "Worker_Name": cand["Driver_Name"],
                                "Division": "Fleet Operations",
                                "Amount": float(approved_amt),
                                "Payment_Mode": "Fuel Variance Recovery",
                                "Supervisor": current_user,
                                "Remarks": rec_remarks
                            }
                            upd_adv = pd.concat([adv_df, pd.DataFrame([new_adv_row])], ignore_index=True)
                            adv_saved = save_fn("worker_advances", upd_adv[ADVANCE_SYNC_COLS])

                            # Record in fuel_recovery_log for audit trail
                            new_rec_row = {
                                "Recovery_ID": f"FREC-{int(time.time())}",
                                "Period_YM": target_ym,
                                "Driver_ID": cand["Driver_ID"],
                                "Driver_Name": cand["Driver_Name"],
                                "Excess_Litres": float(cand["Excess_Litres"]),
                                "Diesel_Rate_Applied": float(diesel_rate_inr),
                                "Calculated_Variance_Amt": float(cand["Calculated_Variance_Amt"]),
                                "Approved_Deduction_Amt": float(approved_amt),
                                "Approved_By": current_user,
                                "Approved_Date": now_dt.strftime("%Y-%m-%d"),
                                "Remarks": rec_remarks
                            }
                            upd_rec = pd.concat([rec_log_df, pd.DataFrame([new_rec_row])], ignore_index=True)
                            rec_saved = save_fn("fuel_recovery_log", upd_rec[FUEL_RECOVERY_COLS])

                            if adv_saved and rec_saved:
                                st.success(f"₹{approved_amt:,.0f} posted to {cand['Driver_Name']}'s Advance Ledger! It will automatically deduct from their {aud_month} {aud_year} Salary Slip.")
                                st.rerun()

            if not rec_log_df.empty and "Recovery_ID" in rec_log_df.columns:
                with st.expander("📜 Approved Fuel Recovery Deductions History", expanded=False):
                    st.dataframe(rec_log_df.iloc[::-1][["Approved_Date", "Period_YM", "Driver_ID", "Driver_Name", "Excess_Litres", "Calculated_Variance_Amt", "Approved_Deduction_Amt", "Remarks"]], use_container_width=True, hide_index=True)
