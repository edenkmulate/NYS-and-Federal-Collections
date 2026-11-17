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
    api_url = "https://ny.gov"
    limit = 10000
    offset = 0
    all_records = []

    while True:
        params = {
            "$limit": limit,
            "$offset": offset,
            "$order": ":id"
        }
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        batch = response.json()

        if not batch:
            break

        all_records.extend(batch)

        if len(batch) < limit:
            break

        offset += limit
        time.sleep(0.25)

    mod_df = pd.DataFrame(all_records)

    numeric_cols = [
        "fiscal_year_ending",
        "amount_collected_in_thousands",
        "month_of_collections_sort_order",
        "tax_type_sort_order",
        "tax_or_fee_sort_order"
    ]
    for col in numeric_cols:
        if col in mod_df.columns:
            mod_df[col] = pd.to_numeric(mod_df[col], errors="coerce")

    month_map = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "may": 5, "jun": 6, "jul": 7, "aug": 8,
        "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }

    def standardize_ny_date(date_str):
        if pd.isna(date_str) or str(date_str).strip() == "":
            return None, None

        val = str(date_str).strip()

        if "-" in val:
            parts = val.split("-")
            month_part = parts.strip().lower()[:3]
            year_part = parts.strip()
            calendar_month = month_map.get(month_part)

            if len(year_part) == 2 and year_part.isdigit():
                calendar_year = int("19" + year_part if int(year_part) >= 96 else "20" + year_part)
            else:
                calendar_year = int(year_part) if year_part.isdigit() else None

            return calendar_year, calendar_month
        else:
            year_part = val[-4:].strip()
            month_part = val[:-4].strip().lower()[:3]
            calendar_year = int(year_part) if year_part.isdigit() else None
            calendar_month = month_map.get(month_part)

            return calendar_year, calendar_month

    if "month_of_collections" in mod_df.columns:
        parsed_dates = mod_df["month_of_collections"].apply(standardize_ny_date)
        mod_df["calendar_year"] = pd.Series([x for x in parsed_dates], index=mod_df.index, dtype="Int64")
        mod_df["month_num"] = pd.Series([x for x in parsed_dates], index=mod_df.index, dtype="Int64")
        
        # Add full month name ('January', 'February', etc.) to match Federal slicer UX
        mod_df["calendar_month_name"] = mod_df["month_num"].apply(
            lambda m: calendar.month_name[int(m)] if pd.notna(m) and 1 <= int(m) <= 12 else None
        )
        # Create a proper datetime column for chronological plotting
        mod_df["record_date"] = pd.to_datetime(
            dict(year=mod_df["calendar_year"], month=mod_df["month_num"], day=1),
            errors="coerce"
        )

    # Convert thousands to actual dollars and rename columns
    mod_df["amount_collected_in_thousands"] = mod_df["amount_collected_in_thousands"] * 1000
    mod_df = mod_df.rename(columns={
        "amount_collected_in_thousands": "amount",
        "fiscal_year_ending": "nys_fiscal_year"
    })

    return mod_df
# ==============================================================================
# 3. MAIN TAB NAVIGATION
# ==============================================================================
tab_fed, tab_nys = st.tabs(["🇺🇸 Federal Treasury Receipts", "🗽 New York State Collections"])

# ------------------------------------------------------------------------------
# TAB 1: FEDERAL TREASURY RECEIPTS (Your Original Dashboard Logic)
# ------------------------------------------------------------------------------
with tab_fed:
    df = load_federal_data()

    if df.empty:
        st.warning("Federal data structure appears to be empty.")
        st.stop()

    st.subheader("🎛️ Federal Dashboard Slicers")
    f_col1, f_col2, f_col3, f_col4 = st.columns(4)

    with f_col1:
        all_years = sorted(df['record_calendar_year'].dropna().unique().tolist(), reverse=True)
        selected_year = st.selectbox("Select Calendar Year", options=all_years, index=0, key="fed_year")

    year_filtered_df = df[df['record_calendar_year'] == selected_year]
    unique_months_df = year_filtered_df[['record_calendar_month', 'month_num']].drop_duplicates().sort_values('month_num')
    all_months = unique_months_df['record_calendar_month'].tolist()
    month_options = ["All Months"] + all_months

    with f_col2:
        selected_month = st.selectbox("Select Calendar Month", options=month_options, index=0, key="fed_month")

    all_classifications = sorted(df['classification_desc'].dropna().unique().tolist())
    class_default = ["Corporation Income Taxes"] if "Corporation Income Taxes" in all_classifications else ([all_classifications[0]] if all_classifications else [])

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

    filter_mask = (
        (df['record_calendar_year'] == selected_year) &
        (df['classification_desc'].isin(selected_classifications if selected_classifications else all_classifications)) &
        (df['receipt_type'].isin(selected_receipt_types if selected_receipt_types else all_receipt_types))
    )
    if selected_month != "All Months":
        filter_mask = filter_mask & (df['record_calendar_month'] == selected_month)

    filtered_df = df[filter_mask]

    st.markdown("### 📊 Federal Key Summary Metrics")
    if not filtered_df.empty:
        avg_monthly_receipts = filtered_df.groupby(['record_calendar_year', 'record_calendar_month'])['amount'].sum().mean()
        unique_classes = filtered_df['classification_desc'].nunique()

        if selected_month != "All Months":
            target_month_name = selected_month
            target_year = selected_year
            target_df = filtered_df
            label_suffix = f" ({target_month_name} {target_year})"
        else:
            latest_date = filtered_df['record_date'].max()
            target_month_name = latest_date.strftime('%B')
            target_year = latest_date.year
            target_df = filtered_df[filtered_df['record_calendar_month'] == target_month_name]
            label_suffix = f" (Latest: {target_month_name} {target_year})"

        total_receipts_m = target_df['amount'].sum() / 1_000_000

        prev_year = target_year - 1
        prev_year_month_df = df[
            (df['record_date'].dt.year == prev_year) &
            (df['record_date'].dt.strftime('%B') == target_month_name) &
            (df['classification_desc'].isin(selected_classifications if selected_classifications else all_classifications)) &
            (df['receipt_type'].isin(selected_receipt_types if selected_receipt_types else all_receipt_types))
        ]
        prev_receipts_m = prev_year_month_df['amount'].sum() / 1_000_000
        yoy_delta_m = total_receipts_m - prev_receipts_m if not prev_year_month_df.empty else None

        k1, k2, k3 = st.columns(3)
        k1.metric(
            label=f"Total Receipts{label_suffix}",
            value=f"${total_receipts_m:,.1f}M",
            delta=f"${yoy_delta_m:,.1f}M Y/Y" if yoy_delta_m is not None else None
        )
        k2.metric(label="Avg Monthly Receipts (Selected Period)", value=f"${avg_monthly_receipts / 1_000_000:,.1f}M")
        k3.metric(label="Selected Classifications", value=unique_classes)

        # Monthly Trend Chart
        trend_df = (
            filtered_df.groupby(['record_date', 'classification_desc'], as_index=False)['amount']
            .sum()
            .sort_values('record_date')
        )
        fig_fed = px.bar(
            trend_df,
            x='record_date',
            y='amount',
            color='classification_desc',
            title=f"Federal Monthly Receipts ({selected_year})",
            labels={'amount': 'Amount ($)', 'record_date': 'Month', 'classification_desc': 'Classification'}
        )
        st.plotly_chart(fig_fed, use_container_width=True)

        with st.expander("View Raw Federal Data"):
            st.dataframe(filtered_df, use_container_width=True)
    else:
        st.info("No Federal records match the current filter selection.")

# ------------------------------------------------------------------------------
# TAB 2: NEW YORK STATE COLLECTIONS
# ------------------------------------------------------------------------------
with tab_nys:
    nys_df = load_nys_data()

    if nys_df.empty:
        st.warning("NYS data structure appears to be empty.")
        st.stop()

    st.subheader("🎛️ NYS Dashboard Slicers")
    n_col1, n_col2, n_col3, n_col4 = st.columns(4)

    with n_col1:
        nys_years = sorted(nys_df['calendar_year'].dropna().unique().tolist(), reverse=True)
        sel_nys_year = st.selectbox("Select Calendar Year", options=nys_years, index=0, key="nys_year")

    nys_year_df = nys_df[nys_df['calendar_year'] == sel_nys_year]
    nys_months_df = nys_year_df[['calendar_month_name', 'month_num']].drop_duplicates().sort_values('month_num')
    nys_month_options = ["All Months"] + nys_months_df['calendar_month_name'].dropna().tolist()

    with n_col2:
        sel_nys_month = st.selectbox("Select Calendar Month", options=nys_month_options, index=0, key="nys_month")

    all_tax_types = sorted(nys_df['tax_type'].dropna().unique().tolist())
    default_tax_type = ["Personal Income Tax"] if "Personal Income Tax" in all_tax_types else ([all_tax_types[0]] if all_tax_types else [])

    with n_col3:
        sel_tax_types = st.multiselect(
            "Tax Type",
            options=all_tax_types,
            default=default_tax_type,
            key="nys_tax_type"
        )

    # Filter available specific Tax/Fee lines based on selected Tax Type
    available_fees_df = nys_df[nys_df['tax_type'].isin(sel_tax_types if sel_tax_types else all_tax_types)]
    all_taxes_fees = sorted(available_fees_df['tax_or_fee'].dropna().unique().tolist())
    
    # Pre-select "Net Collections" if available so subtotals don't double-count
    default_fee = ["Net Collections"] if "Net Collections" in all_taxes_fees else ([all_taxes_fees[0]] if all_taxes_fees else [])

    with n_col4:
        sel_taxes_fees = st.multiselect(
            "Tax or Fee Line Item",
            options=all_taxes_fees,
            default=default_fee,
            key="nys_tax_fee"
        )

    nys_mask = (
        (nys_df['calendar_year'] == sel_nys_year) &
        (nys_df['tax_type'].isin(sel_tax_types if sel_tax_types else all_tax_types)) &
        (nys_df['tax_or_fee'].isin(sel_taxes_fees if sel_taxes_fees else all_taxes_fees))
    )
    if sel_nys_month != "All Months":
        nys_mask = nys_mask & (nys_df['calendar_month_name'] == sel_nys_month)

    filtered_nys = nys_df[nys_mask]

    st.markdown("### 📊 NYS Key Summary Metrics")
    if not filtered_nys.empty:
        nys_avg_monthly = filtered_nys.groupby(['calendar_year', 'month_num'])['amount'].sum().mean()
        nys_unique_items = filtered_nys['tax_or_fee'].nunique()

        if sel_nys_month != "All Months":
            nys_target_month = sel_nys_month
            nys_target_df = filtered_nys
            nys_suffix = f" ({nys_target_month} {sel_nys_year})"
        else:
            latest_nys_date = filtered_nys['record_date'].max()
            nys_target_month = latest_nys_date.strftime('%B')
            nys_target_df = filtered_nys[filtered_nys['calendar_month_name'] == nys_target_month]
            nys_suffix = f" (Latest: {nys_target_month} {sel_nys_year})"

        nys_total_m = nys_target_df['amount'].sum() / 1_000_000

        # Year-over-Year comparison for NYS
        nys_prev_year = sel_nys_year - 1
        nys_prev_df = nys_df[
            (nys_df['calendar_year'] == nys_prev_year) &
            (nys_df['calendar_month_name'] == nys_target_month) &
            (nys_df['tax_type'].isin(sel_tax_types if sel_tax_types else all_tax_types)) &
            (nys_df['tax_or_fee'].isin(sel_taxes_fees if sel_taxes_fees else all_taxes_fees))
        ]
        nys_prev_m = nys_prev_df['amount'].sum() / 1_000_000
        nys_yoy_m = nys_total_m - nys_prev_m if not nys_prev_df.empty else None

        nk1, nk2, nk3 = st.columns(3)
        nk1.metric(
            label=f"Total NYS Collections{nys_suffix}",
            value=f"${nys_total_m:,.1f}M",
            delta=f"${nys_yoy_m:,.1f}M Y/Y" if nys_yoy_m is not None else None
        )
        nk2.metric(label="Avg Monthly Collections (Selected Period)", value=f"${nys_avg_monthly / 1_000_000:,.1f}M")
        nk3.metric(label="Selected Tax/Fee Lines", value=nys_unique_items)

        # Monthly Trend Chart for NYS
        nys_trend_df = (
            filtered_nys.groupby(['record_date', 'tax_or_fee'], as_index=False)['amount']
            .sum()
            .sort_values('record_date')
        )
        fig_nys = px.bar(
            nys_trend_df,
            x='record_date',
            y='amount',
            color='tax_or_fee',
            title=f"NYS Monthly Collections ({sel_nys_year})",
            labels={'amount': 'Amount ($)', 'record_date': 'Month', 'tax_or_fee': 'Tax or Fee'}
        )
        st.plotly_chart(fig_nys, use_container_width=True)

        with st.expander("View Raw NYS Data"):
            st.dataframe(filtered_nys, use_container_width=True)
    else:
        st.info("No NYS records match the current filter selection.")