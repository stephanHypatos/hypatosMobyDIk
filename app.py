"""
MobyDik + Hypatos Streamlit App

Fetches documents from the Hypatos API, lets the user define a field mapping
(Hypatos entity key → MobyDik column), and applies the MobyDik sorting /
classification logic. Results can be downloaded as CSV and saved to a local
output directory. Alert emails are sent via SMTP for flagged rows.
A background scheduler allows the pipeline to run automatically every X minutes.
"""

from __future__ import annotations

import datetime
import os
import time

import pandas as pd
import streamlit as st
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import dotenv_values

from email_sender import SMTPConfig, is_configured, send_all_alerts, test_connection
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
# Load .env on startup (silently — UI values override these)
# ---------------------------------------------------------------------------
_env = dotenv_values(".env")


def _env_get(key: str, default: str = "") -> str:
    return _env.get(key, os.environ.get(key, default))


# ---------------------------------------------------------------------------
# Session-state defaults
# ---------------------------------------------------------------------------
def _init_state():
    defaults: dict = {
        "client": None,
        "projects": [],
        "entity_fields": [],
        "mapping": {},
        "result": None,
        "scheduler": None,
        "sched_running": False,
        "sched_interval": 10,
        "last_run": None,
        "run_log": [],
        "email_log": [],
        "config": ProcessingConfig(),
        "smtp": SMTPConfig(
            host=_env_get("SMTP_HOST"),
            port=int(_env_get("SMTP_PORT", "587")),
            username=_env_get("SMTP_USERNAME"),
            password=_env_get("SMTP_PASSWORD"),
            from_address=_env_get("SMTP_FROM"),
            use_tls=_env_get("SMTP_USE_TLS", "true").lower() == "true",
            use_ssl=_env_get("SMTP_USE_SSL", "false").lower() == "true",
        ),
        "output_dir": _env_get("OUTPUT_DIR", "./output"),
        "selected_project_ids": [],
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


def _save_csv(rows: list[dict], output_dir: str, timestamp: str) -> str | None:
    """Saves sorted rows as CSV. Returns the file path or None on failure."""
    try:
        os.makedirs(output_dir, exist_ok=True)
        fname = f"mobydik_{timestamp.replace(' ', '_').replace(':', '-')}.csv"
        fpath = os.path.join(output_dir, fname)
        with open(fpath, "w", newline="", encoding="utf-8") as f:
            f.write(rows_to_csv(rows))
        return fpath
    except Exception as e:
        return None


def _run_pipeline(
    client: HypatosDocumentClient,
    config: ProcessingConfig,
    project_ids: list[str],
    smtp: SMTPConfig,
    output_dir: str,
):
    """Full pipeline: fetch → process → save → email. Thread-safe (no st calls)."""
    docs = client.get_documents(
        project_ids=project_ids or None,
        state=["done", "doneAutomatically"],
    )
    result = process_documents(docs, config)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result.run_at = timestamp

    # Save CSV
    saved_path = _save_csv(result.rows, output_dir, timestamp) if result.rows else None

    # Send emails
    email_log = send_all_alerts(
        smtp,
        result,
        config.float_recipients,
        config.direct_recipients,
        config.info_recipients,
        config.employee_mapping,
    )

    st.session_state["result"] = result
    st.session_state["last_run"] = timestamp
    st.session_state["run_log"] = result.log
    st.session_state["email_log"] = email_log
    st.session_state["saved_path"] = saved_path


def _start_scheduler(interval_minutes: int, client, config, project_ids, smtp, output_dir):
    if st.session_state["scheduler"] is not None:
        st.session_state["scheduler"].shutdown(wait=False)

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_pipeline,
        "interval",
        minutes=interval_minutes,
        args=[client, config, project_ids, smtp, output_dir],
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


def _clean_mapping() -> dict:
    """Returns the field mapping without the internal _rev sentinel."""
    return {k: v for k, v in st.session_state["mapping"].items() if k != "_rev"}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("🐋 MobyDik · Hypatos")

    # ── API Credentials ──────────────────────────────────────────────────
    st.subheader("API Credentials")
    region = st.radio("Region", ["EU", "US"], horizontal=True,
                      index=0 if _env_get("HYPATOS_REGION", "EU") == "EU" else 1)
    base_url = (
        "https://api.cloud.hypatos.ai/v2"
        if region == "EU"
        else "https://api.cloud.hypatos.com/v2"
    )
    st.caption(f"`{base_url}`")

    client_id = st.text_input(
        "Client ID",
        value=_env_get("HYPATOS_CLIENT_ID"),
        placeholder="your-client-id",
    )
    client_secret = st.text_input(
        "Client Secret",
        value=_env_get("HYPATOS_CLIENT_SECRET"),
        type="password",
        placeholder="••••••••",
    )

    if st.button("Connect", use_container_width=True, type="primary"):
        if not client_id or not client_secret:
            st.error("Enter Client ID and Client Secret.")
        else:
            client = HypatosDocumentClient(client_id, client_secret, base_url)
            with st.spinner("Authenticating…"):
                ok = client.authenticate()
            if ok:
                st.session_state["client"] = client
                projects_resp = client.get_projects()
                st.session_state["projects"] = (
                    projects_resp.get("data", []) if projects_resp else []
                )
                st.success("Connected!")
            else:
                st.error(f"Auth failed: {client.last_error}")

    st.divider()

    # ── SMTP Settings ────────────────────────────────────────────────────
    st.subheader("Email (SMTP)")
    smtp: SMTPConfig = st.session_state["smtp"]

    smtp.host = st.text_input("SMTP Host", value=smtp.host, placeholder="smtp.office365.com")
    col_port, col_tls, col_ssl = st.columns([2, 1, 1])
    smtp.port = col_port.number_input("Port", value=smtp.port, min_value=1, max_value=65535, step=1)
    smtp.use_tls = col_tls.checkbox("TLS", value=smtp.use_tls)
    smtp.use_ssl = col_ssl.checkbox("SSL", value=smtp.use_ssl)
    smtp.username = st.text_input("Username", value=smtp.username, placeholder="user@company.com")
    smtp.password = st.text_input("Password", value=smtp.password, type="password")
    smtp.from_address = st.text_input("From address", value=smtp.from_address, placeholder="user@company.com")
    st.session_state["smtp"] = smtp

    if st.button("Test connection", use_container_width=True, disabled=not is_configured(smtp)):
        with st.spinner("Testing…"):
            ok, msg = test_connection(smtp)
        (st.success if ok else st.error)(msg)

    st.divider()

    # ── Output Directory ─────────────────────────────────────────────────
    st.subheader("Output")
    output_dir = st.text_input("CSV output folder", value=st.session_state["output_dir"])
    st.session_state["output_dir"] = output_dir
    st.caption("Sorted CSVs are saved here after each run.")

    # Status summary
    if st.session_state["client"]:
        st.divider()
        st.caption("API: Connected")
    if st.session_state["last_run"]:
        st.caption(f"Last run: {st.session_state['last_run']}")
    if st.session_state["sched_running"]:
        st.caption(f"Scheduler: every {st.session_state['sched_interval']} min")

# ---------------------------------------------------------------------------
# Main tabs
# ---------------------------------------------------------------------------

tab_mapping, tab_rules, tab_run = st.tabs(["Field Mapping", "Sorting & Alerts", "Run / Schedule"])

# ── Tab 1: Field Mapping ─────────────────────────────────────────────────────

with tab_mapping:
    st.header("Field Mapping")
    st.write(
        "Map each Hypatos entity field to a MobyDik column. "
        "Connect first, then click **Load fields from API** to populate dropdowns."
    )

    client = _get_client()
    projects = st.session_state["projects"]
    project_options = {p.get("name", p["id"]): p["id"] for p in projects} if projects else {}

    col_proj, col_load = st.columns([3, 1])
    with col_proj:
        selected_project_names = st.multiselect(
            "Projects (leave empty = all)",
            options=list(project_options.keys()),
        )
    with col_load:
        st.write("")
        st.write("")
        if st.button("Load fields from API", disabled=client is None):
            sel_ids = [project_options[n] for n in selected_project_names]
            with st.spinner("Fetching sample documents…"):
                fields = client.get_sample_entity_fields(project_ids=sel_ids or None)
            st.session_state["entity_fields"] = fields
            st.success(f"{len(fields)} field(s) found.") if fields else st.warning("No fields found.")

    entity_fields = st.session_state["entity_fields"]
    field_options = ["(not mapped)"] + entity_fields

    st.subheader("Mapping table")
    header1, header2 = st.columns(2)
    header1.markdown("**MobyDik Column**")
    header2.markdown("**Hypatos Entity Field**")

    mapping: dict[str, str] = {}
    rev_map = st.session_state["mapping"].get("_rev", {})

    for col_name in MOBYDIK_COLUMNS:
        c1, c2 = st.columns(2)
        c1.write(col_name)
        current = rev_map.get(col_name, "")

        if entity_fields:
            default_idx = field_options.index(current) if current in field_options else 0
            selected = c2.selectbox(
                col_name, field_options, index=default_idx,
                label_visibility="collapsed", key=f"map_{col_name}",
            )
            hypatos_key = selected if selected != "(not mapped)" else ""
        else:
            hypatos_key = c2.text_input(
                col_name, value=current,
                placeholder="e.g. vendorName or items.articleNumber",
                label_visibility="collapsed", key=f"map_{col_name}",
            )

        if hypatos_key:
            mapping[hypatos_key] = col_name

    if st.button("Save mapping", type="primary"):
        rev = {v: k for k, v in mapping.items()}
        st.session_state["mapping"] = {**mapping, "_rev": rev}
        cfg: ProcessingConfig = st.session_state["config"]
        cfg.field_mapping = mapping
        st.session_state["config"] = cfg
        st.success(f"Mapping saved ({len(mapping)} field(s)).")

    st.session_state["selected_project_ids"] = [
        project_options[n] for n in selected_project_names
    ]

# ── Tab 2: Sorting & Alerts ───────────────────────────────────────────────────

with tab_rules:
    st.header("Sorting & Alert Rules")
    cfg: ProcessingConfig = st.session_state["config"]

    # Sorting
    st.subheader("Sorting")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        menge_raw = st.text_area(
            "Quantity-first suppliers",
            value="\n".join(cfg.menge_lieferanten),
            height=120,
            help="Sort by Menge Gebinde first, then REF. One name per line.",
        )
    with col_s2:
        auftrags_raw = st.text_area(
            "Order-sequence suppliers (no re-sort)",
            value="\n".join(cfg.lieferanten_auftragsinfo),
            height=120,
            help="Keep original document order. One name per line.",
        )

    st.divider()

    # Alert triggers
    st.subheader("Alert triggers")
    info_raw = st.text_area(
        "Info article patterns",
        value="\n".join(cfg.info_articles),
        height=80,
        help="Article number patterns to watch. Matching rows trigger an info alert.",
    )

    st.divider()

    # Email recipients
    st.subheader("Email recipients")
    st.write(
        "Addresses entered here always receive the alert. "
        "Additionally, the employee (Sachbearbeiter) on each flagged row is resolved "
        "via the mapping below and also notified."
    )
    col_r1, col_r2, col_r3 = st.columns(3)
    with col_r1:
        float_recip_raw = st.text_area(
            "Float quantity alerts",
            value="\n".join(cfg.float_recipients),
            height=120,
            placeholder="one@email.com\ntwo@email.com",
        )
    with col_r2:
        direct_recip_raw = st.text_area(
            "Direct delivery alerts",
            value="\n".join(cfg.direct_recipients),
            height=120,
            placeholder="one@email.com\ntwo@email.com",
        )
    with col_r3:
        info_recip_raw = st.text_area(
            "Info article alerts",
            value="\n".join(cfg.info_recipients),
            height=120,
            placeholder="one@email.com\ntwo@email.com",
        )

    st.divider()

    # Employee mapping
    st.subheader("Employee mapping")
    st.write(
        "Maps the **Sachbearbeiter** code in each document to an email address. "
        "Format: `code=email@address.com` — one entry per line."
    )
    emp_lines = "\n".join(f"{k}={v}" for k, v in cfg.employee_mapping.items())
    emp_raw = st.text_area(
        "Sachbearbeiter → email",
        value=emp_lines,
        height=120,
        placeholder="MU=max.muster@company.com\nEF=erika.fried@company.com",
    )

    if st.button("Save rules", type="primary"):
        cfg.menge_lieferanten = [s.strip() for s in menge_raw.splitlines() if s.strip()]
        cfg.lieferanten_auftragsinfo = [s.strip() for s in auftrags_raw.splitlines() if s.strip()]
        cfg.info_articles = [s.strip() for s in info_raw.splitlines() if s.strip()]
        cfg.float_recipients = [s.strip() for s in float_recip_raw.splitlines() if s.strip()]
        cfg.direct_recipients = [s.strip() for s in direct_recip_raw.splitlines() if s.strip()]
        cfg.info_recipients = [s.strip() for s in info_recip_raw.splitlines() if s.strip()]

        emp_mapping = {}
        for line in emp_raw.splitlines():
            line = line.strip()
            if "=" in line:
                code, _, email = line.partition("=")
                if code.strip() and email.strip():
                    emp_mapping[code.strip()] = email.strip()
        cfg.employee_mapping = emp_mapping
        cfg.field_mapping = _clean_mapping()
        st.session_state["config"] = cfg
        st.success("Rules saved.")

# ── Tab 3: Run / Schedule ─────────────────────────────────────────────────────

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
                cfg.field_mapping = _clean_mapping()
                project_ids = st.session_state.get("selected_project_ids", [])
                with st.spinner("Fetching & processing documents…"):
                    _run_pipeline(
                        client, cfg, project_ids,
                        st.session_state["smtp"],
                        st.session_state["output_dir"],
                    )
                st.rerun()

        with col_sched:
            st.subheader("Scheduler")
            interval = st.number_input(
                "Run every (minutes)",
                min_value=1, max_value=1440,
                value=st.session_state["sched_interval"], step=1,
            )
            st.session_state["sched_interval"] = interval

            sc1, sc2 = st.columns(2)
            with sc1:
                if st.button("Start scheduler", use_container_width=True,
                             disabled=st.session_state["sched_running"]):
                    cfg = st.session_state["config"]
                    cfg.field_mapping = _clean_mapping()
                    project_ids = st.session_state.get("selected_project_ids", [])
                    _start_scheduler(
                        interval, client, cfg, project_ids,
                        st.session_state["smtp"],
                        st.session_state["output_dir"],
                    )
                    st.success(f"Started — runs every {interval} min.")
                    st.rerun()
            with sc2:
                if st.button("Stop scheduler", use_container_width=True,
                             disabled=not st.session_state["sched_running"]):
                    _stop_scheduler()
                    st.info("Scheduler stopped.")
                    st.rerun()

            if st.session_state["sched_running"]:
                st.success(f"Scheduler running every {st.session_state['sched_interval']} min.")

        st.divider()

        # ── Results ──────────────────────────────────────────────────────

        result: ProcessingResult | None = st.session_state.get("result")

        if result is None:
            st.info("No results yet. Click **Run pipeline** or start the scheduler.")
        else:
            st.subheader(f"Results — {result.run_at}")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Rows processed", len(result.rows))
            m2.metric("Float quantities", len(result.floats))
            m3.metric("Direct deliveries", len(result.directs))
            m4.metric("Info article hits", len(result.info_hits))

            # Saved file
            saved_path = st.session_state.get("saved_path")
            if saved_path:
                st.success(f"CSV saved → `{saved_path}`")
            elif result.rows:
                st.warning("CSV not saved (check output folder path in sidebar).")

            # Email log
            email_log = st.session_state.get("email_log", [])
            if email_log:
                with st.expander("Email status"):
                    for line in email_log:
                        st.write(line)

            # Main table
            st.subheader("Sorted rows")
            if result.rows:
                display_cols = [c for c in MOBYDIK_COLUMNS if any(r.get(c) for r in result.rows)]
                df = pd.DataFrame(result.rows)[display_cols or MOBYDIK_COLUMNS]
                st.dataframe(df, use_container_width=True)

                st.download_button(
                    "Download CSV",
                    data=rows_to_csv(result.rows),
                    file_name=f"mobydik_{result.run_at.replace(' ', '_').replace(':', '-')}.csv",
                    mime="text/csv",
                )
            else:
                st.info("No rows returned.")

            # Flagged rows
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

        # Auto-refresh while scheduler is running
        if st.session_state["sched_running"]:
            time.sleep(2)
            st.rerun()
