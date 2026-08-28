"""
Deal Desk Swarm — Streamlit UI (Business Stakeholder Edition)

Replaces raw event log with:
- Plain-English timeline with elapsed times
- Deal health banner (Green / Amber / Red)
- Per-specialist collapsible cards showing reply content
- Summary bullets extracted from coordinator output
- .docx download
"""

import os
import re
import tempfile
import time
from pathlib import Path

import streamlit as st
from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent

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
    "Pricing Specialist":        "Pricing",
    "Legal Reviewer":            "Legal",
    "Technical Fit Specialist":  "Tech Fit",
    "Solution Architect":        "Architect",
    "Competitive Intel Analyst": "Competitive",
}

ALL_SPECIALISTS = ["Pricing", "Legal", "Tech Fit", "Architect", "Competitive"]

SPECIALIST_ICONS = {
    "Pricing":     "💰",
    "Legal":       "⚖️",
    "Tech Fit":    "🔧",
    "Architect":   "🏗️",
    "Competitive": "🎯",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_client() -> Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.error("ANTHROPIC_API_KEY is not set. Add it to .env and restart.")
        st.stop()
    return Anthropic(
        api_key=api_key,
        default_headers={"anthropic-beta": "managed-agents-2026-04-01"},
    )


def load_coordinator_env() -> tuple[str, str]:
    coord_path = PROJECT_ROOT / ".coordinator_id"
    env_path   = PROJECT_ROOT / ".environment_id"
    if not coord_path.exists() or not env_path.exists():
        st.error(
            "Missing `.coordinator_id` or `.environment_id`. "
            "Run create_specialists.py, upload_skills.py, create_coordinator.py first."
        )
        st.stop()
    return coord_path.read_text().strip(), env_path.read_text().strip()


def read_uploaded_file(uploaded_file) -> str:
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


def detect_deal_health(text: str) -> tuple[str, str, str]:
    """Return (colour_hex, emoji, label) based on coordinator output."""
    lower = text.lower()
    if any(w in lower for w in ["walk-away", "walk away", "no-go", "decline", "reject"]):
        return "#c0392b", "🔴", "HIGH RISK — Walk-away conditions present. Review before proceeding."
    if any(w in lower for w in ["blocker", "hard block", "uncapped liability", "refused"]):
        return "#e67e22", "🟡", "AMBER — Blockers identified. Negotiation required before signing."
    return "#27ae60", "🟢", "GREEN — Strong fit. Proceed to negotiation."


def extract_summary_bullets(text: str) -> list[str]:
    """Pull bold-prefixed lines from 'What the desk concluded' section."""
    match = re.search(
        r"(?:What the desk concluded|SUMMARY|summary)[:\s\n]+(.+?)(?=\n##|\Z)",
        text, re.DOTALL | re.IGNORECASE,
    )
    section = match.group(1) if match else text

    bullets = re.findall(
        r"\*\*(.+?)\*\*\s*[—–-]\s*(.+?)(?=\n\n|\n\*\*|\Z)",
        section, re.DOTALL,
    )
    if bullets:
        return [
            f"**{title.strip()}** — {body.strip()[:200].rstrip()}"
            for title, body in bullets[:5]
        ]

    # Fallback: first 5 non-empty sentences
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 40]
    return sentences[:5]


# ---------------------------------------------------------------------------
# Timeline renderer
# ---------------------------------------------------------------------------


def render_timeline(placeholder, events: list[dict]) -> None:
    if not events:
        placeholder.empty()
        return

    items = ""
    for i, e in enumerate(events):
        is_last = i == len(events) - 1
        colour   = e.get("colour", "#3498db")
        pulse    = "animation:pulse 1.2s ease-in-out infinite" if is_last else ""
        items += (
            f"<div style='position:relative;padding:5px 0 5px 44px;min-height:30px'>"
            f"  <div style='position:absolute;left:7px;top:8px;width:14px;height:14px;"
            f"              border-radius:50%;background:{colour};border:2px solid white;"
            f"              box-shadow:0 0 0 2px {colour};{pulse}'></div>"
            f"  <span style='color:#aaa;font-size:12px;margin-right:10px'>{e['elapsed']}</span>"
            f"  <span style='font-size:14px'>{e['icon']}&nbsp;{e['text']}</span>"
            f"</div>"
        )

    placeholder.markdown(
        f"<style>@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.35}}}}</style>"
        f"<div style='position:relative;padding:4px 0'>"
        f"  <div style='position:absolute;left:13px;top:0;bottom:0;width:2px;background:#e8e8e8'></div>"
        f"  {items}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Specialist card renderer
# ---------------------------------------------------------------------------


def render_specialist_cards(holders: dict, statuses: dict, content: dict) -> None:
    icons_status = {"waiting": "⬜", "running": "🔄", "done": "✅"}
    labels       = {"waiting": "Waiting", "running": "Analysing…", "done": "Done"}
    for spec, ph in holders.items():
        s    = statuses.get(spec, "waiting")
        icon = SPECIALIST_ICONS.get(spec, "🔍")
        ph.markdown(
            f"<div style='border:1px solid #ddd;border-radius:8px;padding:12px;text-align:center'>"
            f"<div style='font-size:28px'>{icon}</div>"
            f"<div style='font-weight:600;margin:4px 0'>{spec}</div>"
            f"<div style='font-size:20px'>{icons_status[s]}</div>"
            f"<div style='color:#666;font-size:13px'>{labels[s]}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Core swarm runner
# ---------------------------------------------------------------------------


def run_swarm(
    client: Anthropic,
    coordinator_id: str,
    environment_id: str,
    context_text: str,
    card_holders: dict,
    timeline_placeholder,
) -> tuple[list[tuple[str, bytes]], str]:
    statuses:            dict[str, str] = {s: "waiting" for s in ALL_SPECIALISTS}
    specialist_content:  dict[str, str] = {}
    render_specialist_cards(card_holders, statuses, specialist_content)

    timeline_events: list[dict] = []
    t0 = time.time()

    def elapsed() -> str:
        return f"{time.time() - t0:.0f}s"

    def add(icon: str, text: str, colour: str = "#3498db") -> None:
        timeline_events.append({"icon": icon, "elapsed": elapsed(), "text": text, "colour": colour})
        render_timeline(timeline_placeholder, timeline_events)

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

    session = client.beta.sessions.create(
        agent=coordinator_id,
        environment_id=environment_id,
        title="Deal Desk — RFP",
    )
    add("🚀", "Deal Desk session started — coordinator ready", colour="#6c5ce7")

    final_text_parts: list[str] = []

    with client.beta.sessions.events.stream(session.id) as stream:
        client.beta.sessions.events.send(
            session.id,
            events=[{
                "type": "user.message",
                "content": [{"type": "text", "text": user_message}],
            }],
        )

        for event in stream:
            t = event.type

            if t == "session.thread_created":
                agent_name = getattr(event, "agent_name", "")
                display    = SPECIALIST_MAP.get(agent_name, agent_name)
                add(SPECIALIST_ICONS.get(display, "📤"), f"Dispatched to **{display}**", colour="#3498db")
                if display in statuses:
                    statuses[display] = "running"
                    render_specialist_cards(card_holders, statuses, specialist_content)

            elif t == "session.thread_status_running":
                agent_name = getattr(event, "agent_name", "")
                display    = SPECIALIST_MAP.get(agent_name, agent_name)
                if display in statuses and statuses[display] == "waiting":
                    statuses[display] = "running"
                    render_specialist_cards(card_holders, statuses, specialist_content)

            elif t == "agent.thread_message_sent":
                pass  # dispatch already shown on thread_created

            elif t == "agent.thread_message_received":
                from_name = getattr(event, "from_agent_name", "")
                display   = SPECIALIST_MAP.get(from_name, from_name)

                reply_text = ""
                for block in getattr(event, "content", []) or []:
                    if getattr(block, "type", None) == "text":
                        reply_text += block.text
                if reply_text and display in ALL_SPECIALISTS:
                    specialist_content[display] = reply_text

                if display in statuses:
                    statuses[display] = "done"
                    render_specialist_cards(card_holders, statuses, specialist_content)
                add("✅", f"**{display}** analysis complete", colour="#27ae60")

            elif t == "agent.message":
                for block in getattr(event, "content", []):
                    if getattr(block, "type", None) == "text":
                        final_text_parts.append(block.text)

            elif t == "agent.tool_use":
                pass  # hide all tool calls — not meaningful to business users

            elif t == "session.status_idle":
                all_done = all(statuses[s] == "done" for s in ALL_SPECIALISTS)
                if all_done:
                    add("📝", "Synthesising inputs into final proposal…", colour="#e67e22")
                    break
                else:
                    waiting = [s for s in ALL_SPECIALISTS if statuses[s] != "done"]
                    add("⏳", f"Waiting for: {', '.join(waiting)}", colour="#95a5a6")

    add("📄", "Checking for deliverable files…", colour="#6c5ce7")

    docx_files: list[tuple[str, bytes]] = []
    try:
        files = client.beta.files.list(
            scope_id=session.id,
            betas=["managed-agents-2026-04-01"],
        )
        for f in files.data:
            if f.filename.lower().endswith(".docx"):
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                    tmp_path = tmp.name
                client.beta.files.download(f.id).write_to_file(tmp_path)
                with open(tmp_path, "rb") as fh:
                    docx_bytes = fh.read()
                Path(tmp_path).unlink(missing_ok=True)
                docx_files.append((f.filename, docx_bytes))
                add("📥", f"Proposal ready: **{f.filename}**", colour="#27ae60")
    except Exception as exc:
        add("⚠️", f"File retrieval error: {type(exc).__name__}: {exc}")

    return docx_files, "".join(final_text_parts), specialist_content


# ---------------------------------------------------------------------------
# UI layout
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Deal Desk Swarm",
        page_icon="📋",
        layout="wide",
    )

    st.title("📋 Deal Desk Swarm")
    st.caption(
        "Five specialist agents — Pricing, Legal, Tech Fit, Solution Architect, "
        "Competitive Intel — analyse your RFP in parallel and produce a branded proposal."
    )

    # -----------------------------------------------------------------------
    # Upload
    # -----------------------------------------------------------------------
    with st.expander("1. Upload RFP", expanded=True):
        rfp_file = st.file_uploader(
            "RFP document (required)", type=["md", "txt", "pdf"], key="rfp",
        )
        supporting_files = st.file_uploader(
            "Supporting documents — past wins, pricing sheets, product overview (optional)",
            type=["md", "txt", "pdf", "json"],
            accept_multiple_files=True,
            key="supporting",
        )

    # -----------------------------------------------------------------------
    # Specialist progress board
    # -----------------------------------------------------------------------
    st.markdown("### 2. Specialist Progress")
    cols = st.columns(5)
    card_holders: dict[str, object] = {}
    for col, name in zip(cols, ALL_SPECIALISTS):
        with col:
            card_holders[name] = st.empty()
            card_holders[name].markdown(
                f"<div style='border:1px solid #ddd;border-radius:8px;padding:12px;text-align:center'>"
                f"<div style='font-size:28px'>{SPECIALIST_ICONS[name]}</div>"
                f"<div style='font-weight:600;margin:4px 0'>{name}</div>"
                f"<div style='font-size:20px'>⬜</div>"
                f"<div style='color:#666;font-size:13px'>Waiting</div></div>",
                unsafe_allow_html=True,
            )

    # -----------------------------------------------------------------------
    # Run button
    # -----------------------------------------------------------------------
    st.markdown("### 3. Run")
    run_clicked = st.button(
        "Run Deal Desk Swarm", type="primary", disabled=(rfp_file is None)
    )
    if rfp_file is None:
        st.info("Upload an RFP document above to enable the run button.")

    # -----------------------------------------------------------------------
    # Timeline (always visible, populated during run)
    # -----------------------------------------------------------------------
    st.markdown("### 4. Live Progress")
    timeline_placeholder = st.empty()

    # -----------------------------------------------------------------------
    # Results area
    # -----------------------------------------------------------------------
    results_area = st.container()

    # -----------------------------------------------------------------------
    # Swarm execution
    # -----------------------------------------------------------------------
    if run_clicked and rfp_file is not None:
        client = get_client()
        coordinator_id, environment_id = load_coordinator_env()

        rfp_text = read_uploaded_file(rfp_file)
        extras: list[tuple[str, str]] = []

        if supporting_files:
            for sf in supporting_files:
                extras.append((sf.name, read_uploaded_file(sf)))
        else:
            for default_path in [
                PROJECT_ROOT / "synthetic-data" / "past-wins.json",
                PROJECT_ROOT / "synthetic-data" / "product-overview.md",
            ]:
                if default_path.exists():
                    extras.append((default_path.name, default_path.read_text("utf-8")))

        context_text = build_context(rfp_text, extras)

        with st.spinner("Swarm running…"):
            try:
                docx_files, text_output, specialist_content = run_swarm(
                    client,
                    coordinator_id,
                    environment_id,
                    context_text,
                    card_holders,
                    timeline_placeholder,
                )
            except Exception as exc:
                st.error(f"Swarm failed: {exc}")
                st.stop()

        # -------------------------------------------------------------------
        # Results
        # -------------------------------------------------------------------
        with results_area:
            st.success("Swarm complete!")

            # Deal health banner
            colour, emoji, label = detect_deal_health(text_output)
            st.markdown(
                f"<div style='background:{colour};color:white;padding:16px 20px;"
                f"border-radius:8px;font-size:18px;font-weight:600;margin:16px 0'>"
                f"{emoji}&nbsp;&nbsp;{label}</div>",
                unsafe_allow_html=True,
            )

            # Summary bullets
            bullets = extract_summary_bullets(text_output)
            if bullets:
                st.markdown("#### Key Findings")
                for b in bullets:
                    st.markdown(f"- {b}")

            st.divider()

            # Specialist detail expanders
            st.markdown("#### Specialist Reports")
            spec_cols = st.columns(5)
            for col, name in zip(spec_cols, ALL_SPECIALISTS):
                with col:
                    icon = SPECIALIST_ICONS[name]
                    content = specialist_content.get(name, "")
                    with st.expander(f"{icon} {name}", expanded=False):
                        if content:
                            st.markdown(content)
                        else:
                            st.caption("Full analysis included in the downloaded proposal.")

            st.divider()

            # Download
            st.markdown("#### Download Proposal")
            if docx_files:
                for filename, docx_bytes in docx_files:
                    st.download_button(
                        label=f"⬇️  Download {filename}",
                        data=docx_bytes,
                        file_name=filename,
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        type="primary",
                    )
            elif text_output.strip():
                st.info("No .docx file retrieved — showing full proposal text below.")
                st.markdown(text_output)
            else:
                st.warning("The swarm produced no output. Check the timeline above.")


if __name__ == "__main__":
    main()
