import streamlit as st
import pandas as pd
import plotly.express as px
import requests
import time
import calendar

# ==============================================================================
# 1. PAGE CONFIGURATION (Must be the very first Streamlit command)
# ==============================================================================
st.set_page_config(
    page_title="Federal & NYS Tax Receipts Dashboard",
    page_icon="🏦",
    layout="wide"
)

st.title("🏦 Government Tax Receipts Dashboard")
st.markdown("Interactive analysis of **Federal (U.S. Treasury)** and **New York State** revenue collections.")

# ==============================================================================
# 2. CACHED DATA INGESTION ENGINES
# ==============================================================================
@st.cache_data(ttl=86400, show_spinner="Fetching Federal Receipts from U.S. Treasury API...")
def load_federal_data():
    base_url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    endpoint = "/v1/accounting/mts/mts_table_4"

    params = {
        "format": "json",
        "sort": "-record_date",
        "page[size]": 1000,
        "page[number]": 1
    }

    response = requests.get(base_url + endpoint, params=params, timeout=30)
    response.raise_for_status()
    json_data = response.json()

    all_data = json_data["data"]
    total_pages = json_data.get("meta", {}).get("total-pages", 1)

    for page in range(2, total_pages + 1):
        params["page[number]"] = page
        response = requests.get(base_url + endpoint, params=params, timeout=30)
        response.raise_for_status()
        all_data.extend(response.json()["data"])

    df = pd.DataFrame(all_data)
    df['record_date'] = pd.to_datetime(df['record_date'], errors='coerce')
    for col in df.columns:
        if col.endswith('_amt'):
            df[col] = pd.to_numeric(df[col], errors='coerce')

    id_vars = [
        'record_date', 'classification_desc',
        'record_fiscal_year', 'record_calendar_year',
        'record_calendar_month', 'record_calendar_day'
    ]
    value_vars = [
        'current_month_gross_rcpt_amt',
        'current_month_refund_amt',
        'current_month_net_rcpt_amt'
    ]

    df_clean = pd.melt(
        df,
        id_vars=id_vars,
        value_vars=value_vars,
        var_name='receipt_type',
        value_name='amount'
    )

    df_clean['amount'] = pd.to_numeric(df_clean['amount'], errors='coerce').fillna(0)
    df_clean['record_calendar_year'] = df_clean['record_date'].dt.year
    df_clean['record_calendar_month'] = df_clean['record_date'].dt.strftime('%B')
    df_clean['month_num'] = df_clean['record_date'].dt.month

    return df_clean


@st.cache_data(ttl=86400, show_spinner="Fetching NYS Tax Collections from NY Open Data...")
def load_nys_data():
    api_url = "https://data.ny.gov/resource/2vni-8tmb.json"

    limit = 10000
    offset = 0
    all_records = []

    while True:
        params = {
            "$limit": limit,
            "$offset": offset,
            "$order": ":id"  # Keeps row order consistent across pages
        }
        
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        
        batch = response.json()
        
        # Stop when the API returns an empty list
        if not batch:
            break
            
        all_records.extend(batch)
        
        # Stop early if this batch is smaller than the limit (last page)
        if len(batch) < limit:
            break
            
        offset += limit
        time.sleep(0.25)  # Brief pause to respect API rate limits

    # Load into DataFrame
    df = pd.DataFrame(all_records)

    # Convert numeric columns from strings to actual numbers
    numeric_cols = [
        "fiscal_year_ending",
        "amount_collected_in_thousands",
        "month_of_collections_sort_order",
        "tax_type_sort_order",
        "tax_or_fee_sort_order"
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    mod_df = df.copy()

    # Dictionary to map 3-letter month abbreviations to month numbers
    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "may": 5, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }

    def standardize_ny_date(date_str):
        if pd.isna(date_str) or str(date_str).strip() == "":
            return None, None
            
        val = str(date_str).strip()
        
        # 1. Historical Format Rule: Contains a hyphen (e.g., Sep-23 to earliest)
        if "-" in val:
            parts = val.split("-")
            month_part = parts[0].strip().lower()[:3]  # Slice first 3 chars (handles "Sept")
            year_part = parts[1].strip()
            
            calendar_month = month_map.get(month_part)
            
            # Standardize 2-digit years to 4-digit years (data starts in 1996)
            if len(year_part) == 2 and year_part.isdigit():
                calendar_year = int("19" + year_part if int(year_part) >= 96 else "20" + year_part)
            else:
                calendar_year = int(year_part) if year_part.isdigit() else None
                
            return calendar_year, calendar_month

        # 2. Modern Format Rule: October 2023 to now (e.g., "October 2023")
        else:
            year_part = val[-4:].strip()
            month_part = val[:-4].strip().lower()[:3]  # Converts "October" -> "oct"
            
            calendar_year = int(year_part) if year_part.isdigit() else None
            calendar_month = month_map.get(month_part)
            
            return calendar_year, calendar_month

    if "month_of_collections" in mod_df.columns:
        parsed_dates = mod_df["month_of_collections"].apply(standardize_ny_date)
        
        # Use Int64 so pandas preserves integers even if there are None/NaN values
        mod_df["calendar_year"] = pd.Series([x[0] for x in parsed_dates], index=mod_df.index, dtype="Int64")
        mod_df["calendar_month"] = pd.Series([x[1] for x in parsed_dates], index=mod_df.index, dtype="Int64")
        
        # Compatibility columns required by Tab 2 slicers and Plotly charts
        mod_df["month_num"] = mod_df["calendar_month"]
        mod_df["calendar_month_name"] = mod_df["calendar_month"].apply(
            lambda m: calendar.month_name[int(m)] if pd.notna(m) and 1 <= int(m) <= 12 else None
        )
        mod_df["record_date"] = pd.to_datetime(
            dict(year=mod_df["calendar_year"], month=mod_df["calendar_month"], day=1),
            errors="coerce"
        )

    # Convert to numeric, multiply by 1000, and rename to 'amount'
    mod_df["amount_collected_in_thousands"] = (
        pd.to_numeric(mod_df["amount_collected_in_thousands"], errors="coerce") * 1000
    )
    mod_df = mod_df.rename(columns={
        "amount_collected_in_thousands": "amount",
        "fiscal_year_ending": "nys_fiscal_year"
    })

    return mod_df

def render_dashboard_charts(
    full_df: pd.DataFrame,
    selected_year: int,
    selected_month: str,
    year_range: tuple,
    category_col: str,
    selected_categories: list,
    type_col: str,
    selected_types: list,
    cal_year_col: str,
    cal_month_col: str,
    fy_start_month: int,
    key_prefix: str,
    title_prefix: str
):
    """
    Renders the KPIs and 3-row (6-chart) historical & YTD analytical layout
    filtered to the user's selected historical year window.
    """
    cat_mask = full_df[category_col].isin(selected_categories) if selected_categories else True
    type_mask = full_df[type_col].isin(selected_types) if selected_types else True
    
    # Keep full history temporarily so YoY % for the first year in the window is accurate
    all_hist_df = full_df[cat_mask & type_mask].copy()

    if all_hist_df.empty:
        st.info(f"No {title_prefix} records match the selected criteria.")
        return

    # Compute Fiscal Year & Fiscal Month Order
    all_hist_df["_calc_f_month_ord"] = ((all_hist_df["month_num"] - fy_start_month) % 12) + 1
    all_hist_df["_calc_fy"] = all_hist_df[cal_year_col] + (all_hist_df["month_num"] >= fy_start_month).astype(int)

    # Determine target month
    year_subset = all_hist_df[all_hist_df[cal_year_col] == selected_year]
    if selected_month != "All Months":
        target_month_name = selected_month
        target_month_num = int(all_hist_df.loc[all_hist_df[cal_month_col] == selected_month, "month_num"].iloc[0])
    else:
        if not year_subset.empty:
            latest_row = year_subset.sort_values("month_num").iloc[-1]
            target_month_name = latest_row[cal_month_col]
            target_month_num = int(latest_row["month_num"])
        else:
            st.info(f"No data available for {selected_year}.")
            return

    start_yr, end_yr = year_range

    # --------------------------------------------------------------------------
    # KPI SUMMARY ROW
    # --------------------------------------------------------------------------
    st.markdown(f"### 📊 {title_prefix} Key Summary Metrics ({target_month_name} {selected_year})")
    
    curr_m_val = all_hist_df[
        (all_hist_df[cal_year_col] == selected_year) & 
        (all_hist_df["month_num"] == target_month_num)
    ]["amount"].sum()
    
    prev_m_val = all_hist_df[
        (all_hist_df[cal_year_col] == selected_year - 1) & 
        (all_hist_df["month_num"] == target_month_num)
    ]["amount"].sum()

    yoy_diff_m = (curr_m_val - prev_m_val) / 1_000_000 if prev_m_val != 0 else None
    yoy_pct = ((curr_m_val - prev_m_val) / abs(prev_m_val) * 100) if prev_m_val != 0 else None

    k1, k2, k3 = st.columns(3)
    k1.metric(
        label=f"{target_month_name} {selected_year} Collections",
        value=f"${curr_m_val / 1_000_000:,.1f}M",
        delta=f"${yoy_diff_m:,.1f}M ({yoy_pct:+.1f}%) Y/Y" if yoy_diff_m is not None else None
    )
    
    cytd_curr = all_hist_df[
        (all_hist_df[cal_year_col] == selected_year) & 
        (all_hist_df["month_num"] <= target_month_num)
    ]["amount"].sum()
    k2.metric(
        label=f"Calendar YTD (Jan–{target_month_name[:3]} {selected_year})",
        value=f"${cytd_curr / 1_000_000:,.1f}M"
    )
    k3.metric(
        label="Chart Display Window",
        value=f"{start_yr} – {end_yr}"
    )

    st.divider()

    # ==========================================================================
    # ROW 1: HISTORICAL SINGLE-MONTH COLLECTIONS & YOY GROWTH RATE
    # ==========================================================================
    st.markdown(f"#### 1️⃣ Historical {target_month_name} Collections & YoY Growth ({start_yr}–{end_yr})")
    
    hist_month_df = (
        all_hist_df[all_hist_df["month_num"] == target_month_num]
        .groupby(cal_year_col, as_index=False)["amount"]
        .sum()
        .sort_values(cal_year_col)
    )
    # Calculate YoY % on full series first, then slice to user's year window
    hist_month_df["yoy_growth_pct"] = hist_month_df["amount"].pct_change() * 100
    hist_month_df = hist_month_df[
        (hist_month_df[cal_year_col] >= start_yr) & 
        (hist_month_df[cal_year_col] <= end_yr)
    ].copy()
    hist_month_df[cal_year_col] = hist_month_df[cal_year_col].astype(str)

    r1_c1, r1_c2 = st.columns(2)
    with r1_c1:
        fig_m_amt = px.bar(
            hist_month_df,
            x=cal_year_col,
            y="amount",
            title=f"{target_month_name} Collections ({start_yr}–{end_yr}) ($)",
            labels={cal_year_col: "Calendar Year", "amount": "Collections ($)"}
        )
        st.plotly_chart(fig_m_amt, use_container_width=True, key=f"{key_prefix}_r1_amt")

    with r1_c2:
        fig_m_yoy = px.bar(
            hist_month_df.dropna(subset=["yoy_growth_pct"]),
            x=cal_year_col,
            y="yoy_growth_pct",
            title=f"{target_month_name} Year-over-Year Growth Rate (%)",
            labels={cal_year_col: "Calendar Year", "yoy_growth_pct": "YoY Growth (%)"},
            color="yoy_growth_pct",
            color_continuous_scale=["#d9534f", "#f0ad4e", "#5cb85c"],
            color_continuous_midpoint=0
        )
        fig_m_yoy.update_coloraxes(showscale=False)
        st.plotly_chart(fig_m_yoy, use_container_width=True, key=f"{key_prefix}_r1_yoy")

    st.divider()

    # ==========================================================================
    # ROW 2: CALENDAR YEAR-TO-DATE (CYTD) LINE CHART & GROWTH RATE BAR CHART
    # ==========================================================================
    st.markdown(f"#### 2️⃣ Calendar Year-to-Date (Jan – {target_month_name}) ({start_yr}–{end_yr})")
    
    cytd_raw = all_hist_df[all_hist_df["month_num"] <= target_month_num].copy()
    
    cytd_monthly = (
        cytd_raw[
            (cytd_raw[cal_year_col] >= start_yr) & 
            (cytd_raw[cal_year_col] <= end_yr)
        ]
        .groupby([cal_year_col, "month_num", cal_month_col], as_index=False)["amount"]
        .sum()
        .sort_values([cal_year_col, "month_num"])
    )
    cytd_monthly["cytd_cumulative"] = cytd_monthly.groupby(cal_year_col)["amount"].cumsum()
    cytd_monthly[cal_year_col] = cytd_monthly[cal_year_col].astype(str)

    cytd_annual = (
        cytd_raw.groupby(cal_year_col, as_index=False)["amount"]
        .sum()
        .sort_values(cal_year_col)
    )
    cytd_annual["cytd_yoy_pct"] = cytd_annual["amount"].pct_change() * 100
    cytd_annual = cytd_annual[
        (cytd_annual[cal_year_col] >= start_yr) & 
        (cytd_annual[cal_year_col] <= end_yr)
    ].copy()
    cytd_annual[cal_year_col] = cytd_annual[cal_year_col].astype(str)

    r2_c1, r2_c2 = st.columns(2)
    with r2_c1:
        fig_cytd_line = px.line(
            cytd_monthly,
            x="month_num",
            y="cytd_cumulative",
            color=cal_year_col,
            markers=True,
            title=f"Cumulative Calendar YTD Collections (Thru {target_month_name})",
            labels={"month_num": "Calendar Month (1=Jan)", "cytd_cumulative": "Cumulative YTD ($)", cal_year_col: "Year"}
        )
        st.plotly_chart(fig_cytd_line, use_container_width=True, key=f"{key_prefix}_r2_line")

    with r2_c2:
        fig_cytd_yoy = px.bar(
            cytd_annual.dropna(subset=["cytd_yoy_pct"]),
            x=cal_year_col,
            y="cytd_yoy_pct",
            title=f"Calendar YTD YoY Growth Rate (Jan–{target_month_name[:3]}) (%)",
            labels={cal_year_col: "Calendar Year", "cytd_yoy_pct": "CYTD Growth (%)"},
            color="cytd_yoy_pct",
            color_continuous_scale=["#d9534f", "#f0ad4e", "#5cb85c"],
            color_continuous_midpoint=0
        )
        fig_cytd_yoy.update_coloraxes(showscale=False)
        st.plotly_chart(fig_cytd_yoy, use_container_width=True, key=f"{key_prefix}_r2_yoy")

    st.divider()


    # ==========================================================================
    # ROW 3: FISCAL YEAR-TO-DATE (FYTD) LINE CHART & GROWTH RATE BAR CHART
    # ==========================================================================
    target_f_month_ord = ((target_month_num - fy_start_month) % 12) + 1
    target_fy = int(selected_year + (1 if target_month_num >= fy_start_month else 0))

    st.markdown(f"#### 3️⃣ Fiscal Year-to-Date (Thru Fiscal Month {target_f_month_ord} — {target_month_name} / FY {target_fy})")
    
    fytd_raw = all_hist_df[all_hist_df["_calc_f_month_ord"] <= target_f_month_ord].copy()
    
    # Map FY display window cleanly alongside the selected calendar year range
    fy_start_yr = start_yr + (1 if fy_start_month == 10 else 0)
    fy_end_yr = end_yr + (1 if target_month_num >= fy_start_month else 0)

    fytd_monthly = (
        fytd_raw[
            (fytd_raw["_calc_fy"] >= fy_start_yr) & 
            (fytd_raw["_calc_fy"] <= fy_end_yr)
        ]
        .groupby(["_calc_fy", "_calc_f_month_ord", cal_month_col], as_index=False)["amount"]
        .sum()
        .sort_values(["_calc_fy", "_calc_f_month_ord"])
    )
    fytd_monthly["fytd_cumulative"] = fytd_monthly.groupby("_calc_fy")["amount"].cumsum()
    fytd_monthly["_calc_fy"] = fytd_monthly["_calc_fy"].astype(str)

    fytd_annual = (
        fytd_raw.groupby("_calc_fy", as_index=False)["amount"]
        .sum()
        .sort_values("_calc_fy")
    )
    fytd_annual["fytd_yoy_pct"] = fytd_annual["amount"].pct_change() * 100
    fytd_annual = fytd_annual[
        (fytd_annual["_calc_fy"] >= fy_start_yr) & 
        (fytd_annual["_calc_fy"] <= fy_end_yr)
    ].copy()
    fytd_annual["_calc_fy"] = fytd_annual["_calc_fy"].astype(str)

    r3_c1, r3_c2 = st.columns(2)
    with r3_c1:
        fig_fytd_line = px.line(
            fytd_monthly,
            x="_calc_f_month_ord",
            y="fytd_cumulative",
            color="_calc_fy",
            markers=True,
            title=f"Cumulative Fiscal YTD Collections by FY (Thru Fiscal Month {target_f_month_ord})",
            labels={
                "_calc_f_month_ord": f"Fiscal Month Order (1 = Month {fy_start_month})",
                "fytd_cumulative": "Cumulative FYTD ($)",
                "_calc_fy": "Fiscal Year"
            }
        )
        st.plotly_chart(fig_fytd_line, use_container_width=True, key=f"{key_prefix}_r3_line")

    with r3_c2:
        fig_fytd_yoy = px.bar(
            fytd_annual.dropna(subset=["fytd_yoy_pct"]),
            x="_calc_fy",
            y="fytd_yoy_pct",
            title=f"Fiscal YTD YoY Growth Rate (Thru Fiscal Month {target_f_month_ord}) (%)",
            labels={"_calc_fy": "Fiscal Year", "fytd_yoy_pct": "FYTD Growth (%)"},
            color="fytd_yoy_pct",
            color_continuous_scale=["#d9534f", "#f0ad4e", "#5cb85c"],
            color_continuous_midpoint=0
        )
        fig_fytd_yoy.update_coloraxes(showscale=False)
        st.plotly_chart(fig_fytd_yoy, use_container_width=True, key=f"{key_prefix}_r3_yoy")

    with st.expander(f"View Underlying {title_prefix} Data Table ({start_yr}–{end_yr})"):
        display_df = all_hist_df[
            (all_hist_df[cal_year_col] >= start_yr) & 
            (all_hist_df[cal_year_col] <= end_yr)
        ].drop(columns=["_calc_f_month_ord", "_calc_fy"])
        st.dataframe(display_df.sort_values("record_date", ascending=False), use_container_width=True)


# ==============================================================================
# 3. MAIN TAB NAVIGATION
# ==============================================================================
tab_fed, tab_nys = st.tabs(["🇺🇸 Federal Treasury Receipts", "🗽 New York State Collections"])

# ------------------------------------------------------------------------------
# TAB 1: FEDERAL TREASURY RECEIPTS
# ------------------------------------------------------------------------------
with tab_fed:
    df = load_federal_data()

    if df.empty:
        st.warning("Federal data structure appears to be empty.")
        st.stop()

    st.subheader("🎛️ Federal Dashboard Slicers")
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)

    all_years = sorted(df['record_calendar_year'].dropna().unique().tolist(), reverse=True)
    min_fed_yr, max_fed_yr = int(min(all_years)), int(max(all_years))

    with f_col1:
        selected_year = st.selectbox("Select Reference Year", options=all_years, index=0, key="fed_year")

    year_filtered_df = df[df['record_calendar_year'] == selected_year]
    unique_months_df = year_filtered_df[['record_calendar_month', 'month_num']].drop_duplicates().sort_values('month_num')
    month_options = ["All Months"] + unique_months_df['record_calendar_month'].tolist()

    with f_col2:
        selected_month = st.selectbox("Select Reference Month", options=month_options, index=0, key="fed_month")

    all_classifications = sorted(df['classification_desc'].dropna().unique().tolist())
    class_default = ["Corporation Income Taxes"] if "Corporation Income Taxes" in all_classifications else ([all_classifications] if all_classifications else [])

    with f_col3:
        selected_classifications = st.multiselect(
            "Classification Description",
            options=all_classifications,
            default=class_default,
            key="fed_class"
        )

    all_receipt_types = sorted(df['receipt_type'].dropna().unique().tolist())
    receipt_default = ["current_month_net_rcpt_amt"] if "current_month_net_rcpt_amt" in all_receipt_types else all_receipt_types

    with f_col4:
        selected_receipt_types = st.multiselect(
            "Receipt Type",
            options=all_receipt_types,
            default=receipt_default,
            key="fed_rcpt"
        )

    # Historical Lookback Window Slider (Defaults to past 10 years)
    default_fed_start = max(min_fed_yr, max_fed_yr - 9)
    fed_year_range = st.slider(
        "Historical Chart Window (Years)",
        min_value=min_fed_yr,
        max_value=max_fed_yr,
        value=(default_fed_start, max_fed_yr),
        key="fed_year_window"
    )

    render_dashboard_charts(
        full_df=df,
        selected_year=selected_year,
        selected_month=selected_month,
        year_range=fed_year_range,
        category_col="classification_desc",
        selected_categories=selected_classifications if selected_classifications else all_classifications,
        type_col="receipt_type",
        selected_types=selected_receipt_types if selected_receipt_types else all_receipt_types,
        cal_year_col="record_calendar_year",
        cal_month_col="record_calendar_month",
        fy_start_month=10,
        key_prefix="fed",
        title_prefix="Federal"
    )

# ------------------------------------------------------------------------------
# TAB 2: NEW YORK STATE COLLECTIONS
# ------------------------------------------------------------------------------
with tab_nys:
    nys_df = load_nys_data()

    if nys_df.empty:
        st.warning("NYS data structure appears to be empty.")
        st.stop()

    st.subheader("🎛️ NYS Dashboard Slicers")
    n_col1, n_col2, n_col3 = st.columns()

    nys_years = sorted(nys_df['calendar_year'].dropna().unique().tolist(), reverse=True)
    min_nys_yr, max_nys_yr = int(min(nys_years)), int(max(nys_years))

    with n_col1:
        sel_nys_year = st.selectbox("Select Reference Year", options=nys_years, index=0, key="nys_year")

    nys_year_df = nys_df[nys_df['calendar_year'] == sel_nys_year]
    nys_months_df = nys_year_df[['calendar_month_name', 'month_num']].drop_duplicates().sort_values('month_num')
    nys_month_options = ["All Months"] + nys_months_df['calendar_month_name'].dropna().tolist()

    with n_col2:
        sel_nys_month = st.selectbox("Select Reference Month", options=nys_month_options, index=0, key="nys_month")

    # Single dropdown for Tax or Fee Line Item defaulting to Corporation Franchise Tax
    all_taxes_fees = sorted(nys_df['tax_or_fee'].dropna().unique().tolist())
    target_nys_default = "Corporation Franchise Tax (Articles 9A & 13)"
    default_fee = [target_nys_default] if target_nys_default in all_taxes_fees else ([all_taxes_fees] if all_taxes_fees else [])

    with n_col3:
        sel_taxes_fees = st.multiselect(
            "Tax or Fee Line Item",
            options=all_taxes_fees,
            default=default_fee,
            key="nys_tax_fee"
        )

    # Historical Lookback Window Slider (Defaults to past 10 years, expandable to 1996/1997)
    default_nys_start = max(min_nys_yr, max_nys_yr - 9)
    nys_year_range = st.slider(
        "Historical Chart Window (Years)",
        min_value=min_nys_yr,
        max_value=max_nys_yr,
        value=(default_nys_start, max_nys_yr),
        key="nys_year_window"
    )

    render_dashboard_charts(
        full_df=nys_df,
        selected_year=sel_nys_year,
        selected_month=sel_nys_month,
        year_range=nys_year_range,
        category_col="tax_or_fee",
        selected_categories=sel_taxes_fees if sel_taxes_fees else all_taxes_fees,
        type_col="tax_or_fee",
        selected_types=sel_taxes_fees if sel_taxes_fees else all_taxes_fees,
        cal_year_col="calendar_year",
        cal_month_col="calendar_month_name",
        fy_start_month=4,
        key_prefix="nys",
        title_prefix="NYS"
    )
