import os
from typing import Any

import httpx
import pandas as pd
import streamlit as st

from taskforge_python.dashboard_client import cancel_job, fetch_overview, submit_job

st.set_page_config(page_title="TaskForge · Job monitor", page_icon="⚙", layout="wide")
st.markdown(
    """
<style>
.block-container {padding-top: 2.5rem; padding-bottom: 2rem; max-width: 1600px;}
[data-testid="stMetric"] {background: #131e30; border: 1px solid #24334a;
    border-radius: 12px; padding: 18px 20px;}
[data-testid="stMetricValue"] {font-size: 2rem;}
h1 {letter-spacing: -1.5px; font-weight: 750;}
h3 {letter-spacing: -.3px;}
[data-testid="stSidebar"] {border-right: 1px solid #24334a;}
[data-testid="stForm"] {border-color: #24334a;}
</style>
""",
    unsafe_allow_html=True,
)


def show_error(exc: httpx.HTTPError) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        st.error(f"Backend returned {exc.response.status_code}: {exc.response.text[:500]}")
    else:
        st.error("Cannot reach the backend. Start the API and check its address in the sidebar.")


with st.sidebar:
    st.markdown("### ⚙ TaskForge")
    st.caption("LOCAL JOB PROCESSING")
    base_url = st.text_input(
        "Backend address", os.getenv("TASKFORGE_API_URL", "http://127.0.0.1:8000")
    )
    st.divider()
    st.markdown("### Submit a job")
    job_type = st.selectbox(
        "Job type", ["http_fetch", "download_file", "file_checksum", "csv_summary"]
    )
    with st.form("new_job"):
        payload: dict[str, object] = {}
        if job_type in {"http_fetch", "download_file"}:
            payload["url"] = st.text_input("URL", "http://127.0.0.1:8001/hello")
            payload["timeout"] = st.number_input("Timeout (seconds)", 0.1, 120.0, 10.0)
        if job_type != "http_fetch":
            default_path = {
                "csv_summary": "data/demo/scores.csv",
                "file_checksum": "data/demo/sample.txt",
                "download_file": "data/demo/download.txt",
            }[job_type]
            payload["path"] = st.text_input("File path on backend", default_path)
        st.caption(
            "Paths refer to the backend machine. Downloads require an existing parent folder."
        )
        if st.form_submit_button("Queue job", width="stretch", type="primary"):
            try:
                with httpx.Client(base_url=base_url, timeout=10) as client:
                    job = submit_job(client, job_type, payload)
                st.success(f"Queued {job['id'][:8]}")
            except httpx.HTTPError as exc:
                show_error(exc)
    st.divider()
    st.caption("One process · SQLite storage · Async workers")
    st.caption("Start the local demo with the commands in the README.")

st.caption("OPERATIONS / OVERVIEW")
title, badge = st.columns([4, 1])
with title:
    st.title("TaskForge")
    st.markdown("Your jobs, from queue to completion.")
with badge:
    st.caption("LIVE MONITOR")
    refresh = st.toggle("Refresh every 2 seconds", value=True)
st.divider()


@st.fragment(run_every=2 if refresh else None)
def monitor() -> None:
    try:
        with httpx.Client(base_url=base_url, timeout=5) as client:
            overview = fetch_overview(client)
    except httpx.HTTPError as exc:
        show_error(exc)
        return
    metrics = overview["metrics"]
    jobs: list[dict[str, Any]] = overview["jobs"]
    workers = overview["workers"]
    labels = [
        ("Total jobs", "total_jobs"),
        ("Queued", "queued"),
        ("Running", "running"),
        ("Succeeded", "succeeded"),
        ("Failed", "failed"),
        ("Success rate", "success_rate"),
    ]
    for column, (label, key) in zip(st.columns(6), labels, strict=True):
        column.metric(label, f"{metrics[key]}%" if key == "success_rate" else metrics[key])
    st.caption(
        f"{metrics['retrying']} retrying · {metrics['cancelled']} cancelled · "
        f"{metrics['total_retries']} retries · "
        f"{metrics['average_duration']:.2f}s average completed duration"
    )

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader("Worker activity")
        for column, worker in zip(st.columns(len(workers) or 1), workers, strict=False):
            with column.container(border=True):
                st.markdown(f"**{worker['id']}**")
                st.markdown(
                    f"{'🟢' if worker['state'] == 'idle' else '🟠'} {worker['state'].title()}"
                )
                st.caption(
                    f"Job {worker['job_id'][:8]}" if worker["job_id"] else "Ready for the next job"
                )
    with right:
        st.subheader("Job outcomes")
        outcomes = pd.DataFrame(
            {
                "Status": ["Succeeded", "Failed", "Cancelled", "In progress"],
                "Jobs": [
                    metrics["succeeded"],
                    metrics["failed"],
                    metrics["cancelled"],
                    metrics["queued"] + metrics["running"] + metrics["retrying"],
                ],
            }
        )
        st.bar_chart(outcomes, x="Status", y="Jobs", color="#38d9c5", height=180)

    st.subheader("Recent jobs")
    st.caption("Latest 200 jobs · newest first · durations include retry backoff")
    status_filter = st.segmented_control(
        "Status",
        ["All", "Active", "Succeeded", "Failed", "Cancelled"],
        default="All",
        label_visibility="collapsed",
    )
    filtered = [
        job
        for job in jobs
        if status_filter in {"All", None}
        or (status_filter == "Active" and job["status"] in {"queued", "running", "retrying"})
        or job["status"] == str(status_filter).lower()
    ]
    if not jobs:
        st.info("No jobs yet. Queue a job from the sidebar or run the local demo.")
        return
    rows = [
        {
            "Job": job["id"][:8],
            "Type": job["job_type"],
            "Status": job["status"],
            "Duration (s)": round(job["duration"], 2) if job["duration"] is not None else None,
            "Attempts": job["attempt"],
            "Retries": max(0, job["attempt"] - 1),
            "Worker": job["worker_id"] or "—",
            "Created": job["created_at"],
            "Failure": job["error_type"] or "—",
        }
        for job in filtered
    ]
    table = pd.DataFrame(rows)
    if not table.empty:
        table["Created"] = pd.to_datetime(table["Created"], utc=True)
        st.dataframe(
            table,
            hide_index=True,
            width="stretch",
            column_config={"Created": st.column_config.DatetimeColumn(format="HH:mm:ss")},
        )
    else:
        st.info("No jobs match this status. Choose another filter to see recent jobs.")
    details, charts = st.columns([3, 2], gap="large")
    with details:
        selected = st.selectbox(
            "Inspect job",
            jobs,
            format_func=lambda job: f"{job['id'][:8]} · {job['job_type']} · {job['status']}",
        )
        if selected:
            st.code(selected["id"], language=None)
            if selected["error"]:
                st.error(selected["error"])
            with st.expander("Payload and result", expanded=False):
                st.json({"payload": selected["payload"], "result": selected["result"]})
            if selected["status"] in {"queued", "running", "retrying"} and st.button(
                "Cancel selected job"
            ):
                try:
                    with httpx.Client(base_url=base_url, timeout=120) as client:
                        cancel_job(client, selected["id"])
                    st.success("Job cancelled")
                    st.rerun(scope="fragment")
                except httpx.HTTPError as exc:
                    show_error(exc)
    with charts, st.expander("Duration and failure details", expanded=True):
        durations = pd.DataFrame(
            [
                {"Job": job["id"][:8], "Seconds": job["duration"]}
                for job in jobs[:20]
                if job["duration"] is not None
            ]
        )
        if not durations.empty:
            st.bar_chart(durations, x="Job", y="Seconds", color="#7d9bff", height=160)
        if metrics["failure_types"]:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"Failure type": key, "Jobs": count}
                        for key, count in metrics["failure_types"].items()
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.caption("No failures recorded.")
    st.caption(
        "Success rate = succeeded / (succeeded + failed). Cancelled and active jobs are excluded."
    )


monitor()
