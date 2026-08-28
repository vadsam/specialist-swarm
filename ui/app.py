"""
Deal Desk Swarm — Streamlit UI

Upload an RFP, watch the four specialist agents work in parallel,
then download the generated proposal .docx.
"""

import io
import os
import tempfile
from pathlib import Path

import streamlit as st
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent

# Load .env — search from project root upward until found
if not os.environ.get("ANTHROPIC_API_KEY"):
    _search = [PROJECT_ROOT, *PROJECT_ROOT.parents]
    for _d in _search:
        _env_file = _d / ".env"
        if _env_file.exists():
            for _line in _env_file.read_text().splitlines():
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    os.environ.setdefault(_k.strip(), _v.strip())
            break

SPECIALIST_MAP = {
    "Pricing Specialist": "Pricing",
    "Legal Reviewer": "Legal",
    "Technical Fit Specialist": "Tech Fit",
    "Solution Architect": "Architect",
    "Competitive Intel Analyst": "Competitive",
}

ALL_SPECIALISTS = ["Pricing", "Legal", "Tech Fit", "Architect", "Competitive"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.error("ANTHROPIC_API_KEY is not set in the environment. Set it and restart.")
        st.stop()
    return Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )


def load_coordinator_env() -> tuple[str, str]:
    coord_path = PROJECT_ROOT / ".coordinator_id"
    env_path = PROJECT_ROOT / ".environment_id"
    if not coord_path.exists() or not env_path.exists():
        st.error(
            "Missing `.coordinator_id` or `.environment_id` in project root. "
            "Run create_specialists.py, upload_skills.py, then create_coordinator.py first."
        )
        st.stop()
    return coord_path.read_text().strip(), env_path.read_text().strip()


def read_uploaded_file(uploaded_file) -> str:
    """Decode an uploaded file to a UTF-8 string (best-effort)."""
    raw = uploaded_file.read()
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def build_context(rfp_text: str, extras: list[tuple[str, str]]) -> str:
    blocks = [f"=====  DOCUMENT: rfp  =====\n{rfp_text}"]
    for name, text in extras:
        blocks.append(f"=====  DOCUMENT: {name}  =====\n{text}")
    return "\n\n".join(blocks)


def render_cards(holders: dict, statuses: dict) -> None:
    """Re-render each specialist card placeholder with current status."""
    icons = {"waiting": "⬜", "running": "🔄", "done": "✅"}
    labels = {"waiting": "Waiting", "running": "Running…", "done": "Done"}
    for spec, ph in holders.items():
        s = statuses.get(spec, "waiting")
        ph.markdown(
            f"**{spec}**\n\n{icons[s]} {labels[s]}"
        )


# ---------------------------------------------------------------------------
# Core swarm runner (called once, runs synchronously in the main thread)
# ---------------------------------------------------------------------------


def run_swarm(
    client: Anthropic,
    coordinator_id: str,
    environment_id: str,
    context_text: str,
    card_holders: dict,
    log_placeholder,
) -> tuple[list[tuple[str, bytes]], str]:
    """
    Stream events from the deal-desk session, updating card placeholders live.

    Returns:
        docx_files  – list of (filename, bytes) for any .docx outputs
        text_output – concatenated agent.message text (fallback if no docx)
    """
    statuses = {s: "waiting" for s in ALL_SPECIALISTS}
    render_cards(card_holders, statuses)

    user_message = (
        "An RFP has just landed. Please run the standard Deal Desk process:\n"
        "1. Read the RFP yourself.\n"
        "2. Delegate to all five specialists in parallel.\n"
        "3. Synthesise their replies.\n"
        "4. Produce the final proposal response as a branded Word document "
        "if you have access to a docx skill; otherwise output the response "
        "as a structured markdown document.\n\n"
        "Specialists have their own skills attached for their respective "
        "domains. Move fast — the RFP deadline is real.\n\n"
        f"{context_text}"
    )

    log_lines: list[str] = []

    def log(msg: str) -> None:
        log_lines.append(msg)
        log_placeholder.code("\n".join(log_lines))

    log("Creating session…")
    session = client.beta.sessions.create(
        agent=coordinator_id,
        environment_id=environment_id,
        title="Deal Desk — RFP",
    )
    log(f"Session: {session.id}")

    final_text_parts: list[str] = []

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": user_message}],
                }
            ],
        )

        for event in stream:
            t = event.type

            if t == "session.thread_created":
                agent_name = getattr(event, "agent_name", "")
                display = SPECIALIST_MAP.get(agent_name)
                log(f"[thread spawned]  {agent_name or '?'}")
                if display:
                    statuses[display] = "running"
                    render_cards(card_holders, statuses)

            elif t == "session.thread_status_running":
                agent_name = getattr(event, "agent_name", "")
                display = SPECIALIST_MAP.get(agent_name)
                log(f"[thread running]  {agent_name or '?'}")
                if display:
                    statuses[display] = "running"
                    render_cards(card_holders, statuses)

            elif t == "agent.thread_message_sent":
                to_name = getattr(event, "to_agent_name", "")
                log(f"[delegate ->]     {to_name}")

            elif t == "agent.thread_message_received":
                from_name = getattr(event, "from_agent_name", "")
                display = SPECIALIST_MAP.get(from_name)
                log(f"[reply <-]        {from_name}")
                if display:
                    statuses[display] = "done"
                    render_cards(card_holders, statuses)

            elif t == "agent.message":
                for block in getattr(event, "content", []):
                    if getattr(block, "type", None) == "text":
                        final_text_parts.append(block.text)

            elif t == "agent.tool_use":
                log(f"[tool]            {getattr(event, 'name', '?')}")

            elif t == "session.status_idle":
                # Only stop when all specialists have reported back
                all_done = all(statuses[s] == "done" for s in ALL_SPECIALISTS)
                if all_done:
                    log("[swarm finished]")
                    render_cards(card_holders, statuses)
                    break
                else:
                    still_waiting = [s for s in ALL_SPECIALISTS if statuses[s] != "done"]
                    log(f"[idle — waiting for: {', '.join(still_waiting)}]")

    # Attempt to pull any files the agents produced
    log("Checking for file deliverables…")
    docx_files: list[tuple[str, bytes]] = []
    try:
        files = client.beta.files.list(
            scope_id=session.id,
            betas=["managed-agents-2026-04-01"],
        )
        for f in files.data:
            log(f"  found: {f.filename}")
            if f.filename.lower().endswith(".docx"):
                # write_to_file is the reliable way to grab binary content
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                    tmp_path = tmp.name
                content_obj = client.beta.files.download(f.id)
                content_obj.write_to_file(tmp_path)
                with open(tmp_path, "rb") as fh:
                    docx_bytes = fh.read()
                Path(tmp_path).unlink(missing_ok=True)
                docx_files.append((f.filename, docx_bytes))
    except Exception as exc:
        log(f"  ERROR listing files: {type(exc).__name__}: {exc}")

    return docx_files, "".join(final_text_parts)


# ---------------------------------------------------------------------------
# UI layout
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Deal Desk Swarm",
        page_icon="📋",
        layout="wide",
    )

    st.title("Deal Desk Swarm")
    st.caption("Powered by four specialist agents running in parallel via the Anthropic Agent SDK.")

    # -----------------------------------------------------------------------
    # Section 1: File uploaders
    # -----------------------------------------------------------------------
    st.header("1. Upload RFP")

    rfp_file = st.file_uploader(
        "RFP document (required)",
        type=["md", "txt", "pdf"],
        key="rfp",
        help="The main request-for-proposal you want to respond to.",
    )

    st.subheader("Supporting documents (optional)")
    supporting_files = st.file_uploader(
        "Past wins, product overview, pricing sheets, etc.",
        type=["md", "txt", "pdf", "json"],
        accept_multiple_files=True,
        key="supporting",
        help="Any extra context for the specialists. Leave empty to use default synthetic-data/ files.",
    )

    # -----------------------------------------------------------------------
    # Section 2: Progress board (always visible, cards start empty)
    # -----------------------------------------------------------------------
    st.header("2. Specialist Progress")

    cols = st.columns(5)
    card_holders: dict[str, st.delta_generator.DeltaGenerator] = {}
    for col, name in zip(cols, ALL_SPECIALISTS):
        with col:
            st.markdown(f"### {name}")
            card_holders[name] = st.empty()
            card_holders[name].markdown("⬜ Waiting")

    # -----------------------------------------------------------------------
    # Section 3: Run button
    # -----------------------------------------------------------------------
    st.header("3. Run")

    run_clicked = st.button("Run Deal Desk Swarm", type="primary", disabled=(rfp_file is None))

    if rfp_file is None:
        st.info("Upload an RFP document above to enable the run button.")

    # -----------------------------------------------------------------------
    # Event log area (shown during/after run)
    # -----------------------------------------------------------------------
    log_placeholder = st.empty()

    # -----------------------------------------------------------------------
    # Result area
    # -----------------------------------------------------------------------
    result_area = st.empty()

    # -----------------------------------------------------------------------
    # Run the swarm when the button is clicked
    # -----------------------------------------------------------------------
    if run_clicked and rfp_file is not None:
        client = get_client()
        coordinator_id, environment_id = load_coordinator_env()

        # Build context text
        rfp_text = read_uploaded_file(rfp_file)
        extras: list[tuple[str, str]] = []

        if supporting_files:
            for sf in supporting_files:
                extras.append((sf.name, read_uploaded_file(sf)))
        else:
            # Fall back to synthetic-data/ files
            for default_path in [
                PROJECT_ROOT / "synthetic-data" / "past-wins.json",
                PROJECT_ROOT / "synthetic-data" / "product-overview.md",
            ]:
                if default_path.exists():
                    extras.append((default_path.name, default_path.read_text("utf-8")))

        context_text = build_context(rfp_text, extras)

        with st.spinner("Swarm running — streaming events…"):
            try:
                docx_files, text_output = run_swarm(
                    client,
                    coordinator_id,
                    environment_id,
                    context_text,
                    card_holders,
                    log_placeholder,
                )
            except Exception as exc:
                st.error(f"Swarm failed: {exc}")
                st.stop()

        st.success("Swarm finished!")

        # ------------------------------------------------------------------
        # Section 4: Download
        # ------------------------------------------------------------------
        st.header("4. Download Proposal")

        if docx_files:
            for filename, docx_bytes in docx_files:
                result_area.download_button(
                    label=f"Download {filename}",
                    data=docx_bytes,
                    file_name=filename,
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
        elif text_output.strip():
            st.info("No .docx file was produced. Showing coordinator text output below.")
            result_area.markdown(text_output)
        else:
            st.warning("The swarm produced no output. Check the event log above.")


if __name__ == "__main__":
    main()
