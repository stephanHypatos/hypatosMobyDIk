"""
MobyDik + Hypatos Streamlit App

Fetches documents from the Hypatos API, lets the user define a field mapping
(Hypatos entity key → MobyDik column), and applies the MobyDik sorting /
classification logic. Results can be downloaded as CSV.
A background scheduler allows the pipeline to run automatically every X minutes.
"""

from __future__ import annotations

import json
import threading
import time
import datetime
import pandas as pd
import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler

from hypatos_client import HypatosDocumentClient
from moby_processor import (
    MOBYDIK_COLUMNS,
    ProcessingConfig,
    ProcessingResult,
    process_documents,
    rows_to_csv,
)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="MobyDik · Hypatos",
    page_icon="🐋",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session-state defaults
# ---------------------------------------------------------------------------
def _init_state():
    defaults = {
        "client": None,          # HypatosDocumentClient instance
        "projects": [],          # [{id, name}, ...]
        "entity_fields": [],     # Hypatos entity field names from sample docs
        "mapping": {},           # hypatos_key -> mobydik_col
        "result": None,          # ProcessingResult
        "scheduler": None,       # APScheduler instance
        "sched_running": False,
        "sched_interval": 10,
        "last_run": None,
        "run_log": [],
        "config": ProcessingConfig(),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client() -> HypatosDocumentClient | None:
    return st.session_state.get("client")


def _run_pipeline(client: HypatosDocumentClient, config: ProcessingConfig, project_ids: list[str]):
    """Fetches documents and runs MobyDik processing. Thread-safe (no st calls)."""
    docs = client.get_documents(project_ids=project_ids or None, state=["done", "doneAutomatically"])
    result = process_documents(docs, config)
    st.session_state["result"] = result
    st.session_state["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.session_state["run_log"] = result.log


def _start_scheduler(interval_minutes: int, client, config, project_ids):
    if st.session_state["scheduler"] is not None:
        st.session_state["scheduler"].shutdown(wait=False)

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_pipeline,
        "interval",
        minutes=interval_minutes,
        args=[client, config, project_ids],
        next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=2),
    )
    scheduler.start()
    st.session_state["scheduler"] = scheduler
    st.session_state["sched_running"] = True


def _stop_scheduler():
    sched = st.session_state.get("scheduler")
    if sched:
        sched.shutdown(wait=False)
    st.session_state["scheduler"] = None
    st.session_state["sched_running"] = False


# ---------------------------------------------------------------------------
# Sidebar – credentials
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🐋 MobyDik · Hypatos")
    st.subheader("API Credentials")

    region = st.radio("Region", ["EU", "US"], horizontal=True)
    base_url = (
        "https://api.cloud.hypatos.ai/v2"
        if region == "EU"
        else "https://api.cloud.hypatos.com/v2"
    )
    st.caption(f"Base URL: `{base_url}`")

    client_id = st.text_input("Client ID", type="default", placeholder="your-client-id")
    client_secret = st.text_input("Client Secret", type="password", placeholder="••••••••")

    if st.button("Connect", use_container_width=True, type="primary"):
        if not client_id or not client_secret:
            st.error("Please enter Client ID and Client Secret.")
        else:
            client = HypatosDocumentClient(client_id, client_secret, base_url)
            with st.spinner("Authenticating…"):
                ok = client.authenticate()
            if ok:
                st.session_state["client"] = client
                projects_resp = client.get_projects()
                st.session_state["projects"] = projects_resp.get("data", []) if projects_resp else []
                st.success("Connected!")
            else:
                st.error(f"Authentication failed: {client.last_error}")

    if st.session_state["client"]:
        st.divider()
        st.caption("Status: Connected")
        if st.session_state["last_run"]:
            st.caption(f"Last run: {st.session_state['last_run']}")
        if st.session_state["sched_running"]:
            st.caption(f"Scheduler: running every {st.session_state['sched_interval']} min")

# ---------------------------------------------------------------------------
# Main tabs
# ---------------------------------------------------------------------------

tab_mapping, tab_rules, tab_run = st.tabs(["Field Mapping", "Sorting Rules", "Run / Schedule"])

# ── Tab 1: Field Mapping ────────────────────────────────────────────────────

with tab_mapping:
    st.header("Field Mapping")
    st.write(
        "Map each Hypatos entity field to a MobyDik column. "
        "You can type a field name directly or load available fields from a sample document."
    )

    client = _get_client()

    # Project selector (needed to load sample fields)
    projects = st.session_state["projects"]
    project_options = {p.get("name", p["id"]): p["id"] for p in projects} if projects else {}

    col_proj, col_load = st.columns([3, 1])
    with col_proj:
        selected_project_names = st.multiselect(
            "Projects (leave empty = all)",
            options=list(project_options.keys()),
            help="Filter documents by project when running.",
        )
    with col_load:
        st.write("")  # vertical align
        st.write("")
        if st.button("Load fields from API", disabled=client is None):
            sel_ids = [project_options[n] for n in selected_project_names]
            with st.spinner("Fetching sample documents…"):
                fields = client.get_sample_entity_fields(project_ids=sel_ids or None)
            st.session_state["entity_fields"] = fields
            if fields:
                st.success(f"Found {len(fields)} entity field(s).")
            else:
                st.warning("No entity fields found in sample documents.")

    entity_fields = st.session_state["entity_fields"]
    field_options = ["(not mapped)"] + entity_fields

    st.subheader("Mapping table")
    mapping: dict[str, str] = {}

    # Two columns: MobyDik column (left) | Hypatos field (right)
    header_col1, header_col2 = st.columns(2)
    header_col1.markdown("**MobyDik Column**")
    header_col2.markdown("**Hypatos Entity Field**")

    for col_name in MOBYDIK_COLUMNS:
        c1, c2 = st.columns(2)
        c1.write(col_name)
        current = st.session_state["mapping"].get("_rev", {}).get(col_name, "")

        if entity_fields:
            default_idx = field_options.index(current) if current in field_options else 0
            selected = c2.selectbox(
                label=col_name,
                options=field_options,
                index=default_idx,
                label_visibility="collapsed",
                key=f"map_{col_name}",
            )
            hypatos_key = selected if selected != "(not mapped)" else ""
        else:
            hypatos_key = c2.text_input(
                label=col_name,
                value=current,
                placeholder="e.g. vendorName or items.articleNumber",
                label_visibility="collapsed",
                key=f"map_{col_name}",
            )

        if hypatos_key:
            mapping[hypatos_key] = col_name

    if st.button("Save mapping", type="primary"):
        # Store forward map and reverse map
        st.session_state["mapping"] = mapping
        # Rebuild config
        cfg = st.session_state["config"]
        cfg.field_mapping = mapping
        st.session_state["config"] = cfg
        # Store reverse for UI repopulation
        rev = {v: k for k, v in mapping.items()}
        # Hack: store reverse in mapping dict under sentinel key
        mapping_with_rev = dict(mapping)
        mapping_with_rev["_rev"] = rev
        st.session_state["mapping"] = mapping_with_rev
        st.success(f"Mapping saved ({len(mapping)} field(s) mapped).")

    # Persist selected project IDs for use in Run tab
    st.session_state["selected_project_ids"] = [project_options[n] for n in selected_project_names]

# ── Tab 2: Sorting Rules ─────────────────────────────────────────────────────

with tab_rules:
    st.header("Sorting Rules")
    cfg: ProcessingConfig = st.session_state["config"]

    st.subheader("Quantity-first suppliers")
    st.write(
        "Suppliers listed here will be sorted by **Menge Gebinde** first, then by REF number. "
        "All others default to REF-first sorting."
    )
    menge_raw = st.text_area(
        "One supplier name per line",
        value="\n".join(cfg.menge_lieferanten),
        height=120,
        key="menge_lieferanten_input",
    )

    st.subheader("Order-sequence suppliers (no re-sort)")
    st.write("Documents from these suppliers keep their original order.")
    auftrags_raw = st.text_area(
        "One supplier name per line",
        value="\n".join(cfg.lieferanten_auftragsinfo),
        height=80,
        key="auftrags_input",
    )

    st.subheader("Info articles")
    st.write(
        "Article number patterns to watch. Matching rows are highlighted in the results."
    )
    info_raw = st.text_area(
        "One pattern per line",
        value="\n".join(cfg.info_articles),
        height=80,
        key="info_articles_input",
    )

    if st.button("Save rules", type="primary"):
        cfg.menge_lieferanten = [s.strip() for s in menge_raw.splitlines() if s.strip()]
        cfg.lieferanten_auftragsinfo = [s.strip() for s in auftrags_raw.splitlines() if s.strip()]
        cfg.info_articles = [s.strip() for s in info_raw.splitlines() if s.strip()]
        cfg.field_mapping = {k: v for k, v in st.session_state["mapping"].items() if k != "_rev"}
        st.session_state["config"] = cfg
        st.success("Rules saved.")

# ── Tab 3: Run / Schedule ────────────────────────────────────────────────────

with tab_run:
    st.header("Run / Schedule")
    client = _get_client()

    if not client:
        st.warning("Connect to the Hypatos API first (sidebar).")
    else:
        col_run, col_sched = st.columns([1, 2])

        with col_run:
            st.subheader("Run now")
            if st.button("Run pipeline", type="primary", use_container_width=True):
                cfg = st.session_state["config"]
                cfg.field_mapping = {k: v for k, v in st.session_state["mapping"].items() if k != "_rev"}
                project_ids = st.session_state.get("selected_project_ids", [])
                with st.spinner("Fetching & processing documents…"):
                    _run_pipeline(client, cfg, project_ids)
                st.rerun()

        with col_sched:
            st.subheader("Scheduler")
            interval = st.number_input(
                "Run every (minutes)",
                min_value=1,
                max_value=1440,
                value=st.session_state["sched_interval"],
                step=1,
            )
            st.session_state["sched_interval"] = interval

            sched_col1, sched_col2 = st.columns(2)
            with sched_col1:
                if st.button(
                    "Start scheduler",
                    use_container_width=True,
                    disabled=st.session_state["sched_running"],
                ):
                    cfg = st.session_state["config"]
                    cfg.field_mapping = {k: v for k, v in st.session_state["mapping"].items() if k != "_rev"}
                    project_ids = st.session_state.get("selected_project_ids", [])
                    _start_scheduler(interval, client, cfg, project_ids)
                    st.success(f"Scheduler started. Runs every {interval} minute(s).")
                    st.rerun()

            with sched_col2:
                if st.button(
                    "Stop scheduler",
                    use_container_width=True,
                    disabled=not st.session_state["sched_running"],
                ):
                    _stop_scheduler()
                    st.info("Scheduler stopped.")
                    st.rerun()

            if st.session_state["sched_running"]:
                st.success(f"Scheduler is running every {st.session_state['sched_interval']} min.")

        st.divider()

        # ── Results ──────────────────────────────────────────────────────────

        result: ProcessingResult | None = st.session_state.get("result")

        if result is None:
            st.info("No results yet. Click **Run pipeline** or start the scheduler.")
        else:
            st.subheader(f"Results — {result.run_at}")

            metrics = st.columns(4)
            metrics[0].metric("Documents processed", len(result.rows))
            metrics[1].metric("Float quantities", len(result.floats))
            metrics[2].metric("Direct deliveries", len(result.directs))
            metrics[3].metric("Info article hits", len(result.info_hits))

            st.subheader("Sorted rows")
            if result.rows:
                display_cols = [c for c in MOBYDIK_COLUMNS if any(r.get(c) for r in result.rows)]
                df = pd.DataFrame(result.rows)[display_cols if display_cols else MOBYDIK_COLUMNS]
                st.dataframe(df, use_container_width=True)

                csv_data = rows_to_csv(result.rows)
                st.download_button(
                    "Download CSV",
                    data=csv_data,
                    file_name=f"mobydik_{result.run_at.replace(' ', '_').replace(':', '-')}.csv",
                    mime="text/csv",
                )
            else:
                st.info("No rows returned.")

            # Flags
            if result.floats:
                with st.expander(f"Float quantities ({len(result.floats)})"):
                    st.dataframe(pd.DataFrame(result.floats)[MOBYDIK_COLUMNS], use_container_width=True)

            if result.directs:
                with st.expander(f"Direct deliveries ({len(result.directs)})"):
                    st.dataframe(pd.DataFrame(result.directs)[MOBYDIK_COLUMNS], use_container_width=True)

            if result.info_hits:
                with st.expander(f"Info article hits ({len(result.info_hits)})"):
                    st.dataframe(pd.DataFrame(result.info_hits)[MOBYDIK_COLUMNS], use_container_width=True)

            with st.expander("Processing log"):
                st.code("\n".join(result.log), language=None)

        # Auto-refresh when scheduler is running
        if st.session_state["sched_running"]:
            time.sleep(2)
            st.rerun()
