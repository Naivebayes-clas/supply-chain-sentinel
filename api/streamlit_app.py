"""
Streamlit UI: Chat interface + KPI Dashboard for Supply Chain Sentinel.
Run: streamlit run api/streamlit_app.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import json

# ──────────────────────────────────────────────
# Page config
# ──────────────────────────────────────────────
st.set_page_config(
    page_title="Supply Chain Sentinel",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Supply Chain Sentinel")
st.caption("Agentic data platform for logistics ops — NL querying, anomaly detection, RAG")

# ──────────────────────────────────────────────
# Tabs
# ──────────────────────────────────────────────
tab_chat, tab_dashboard, tab_anomaly, tab_docs = st.tabs([
    "💬 Chat", "📊 Dashboard", "🚨 Anomalies", "📖 EDI Docs"
])

# ──────────────────────────────────────────────
# Tab 1: Chat
# ──────────────────────────────────────────────
with tab_chat:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {"role": "assistant", "content": "Hi! Ask me about carrier performance, late shipments, anomalies, or EDI formats."}
        ]

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("Ask a question..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    from agent.graph import ask
                    answer = ask(prompt)
                except Exception as e:
                    answer = f"⚠️ Error: {str(e)}"
            st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})

    # Suggested questions
    st.markdown("---")
    st.markdown("**Try these:**")
    suggestions = [
        "Which carrier has the worst on-time rate?",
        "Are there any anomalies in the daily KPIs?",
        "What segments are required in an 856?",
        "Where are the most late shipments going?",
    ]
    for s in suggestions:
        if st.button(s, key=f"btn_{s[:20]}"):
            st.session_state.messages.append({"role": "user", "content": s})
            with st.chat_message("user"):
                st.markdown(s)
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    try:
                        from agent.graph import ask
                        answer = ask(s)
                    except Exception as e:
                        answer = f"⚠️ Error: {str(e)}"
                st.markdown(answer)
                st.session_state.messages.append({"role": "assistant", "content": answer})
            st.rerun()

# ──────────────────────────────────────────────
# Tab 2: Dashboard
# ──────────────────────────────────────────────
with tab_dashboard:
    try:
        from agent.tools.mcp_server import get_carrier_performance
        import pandas as pd

        carrier_data = json.loads(get_carrier_performance())
        df = pd.DataFrame(carrier_data)

        st.subheader("Carrier Performance")

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Carriers", len(df))
        with col2:
            avg_on_time = df["on_time_rate"].astype(float).mean()
            st.metric("Avg On-Time Rate", f"{avg_on_time:.1%}")
        with col3:
            worst = df.loc[df["on_time_rate"].astype(float).idxmin()]
            st.metric("Worst Carrier", f"{worst['carrier_name']} ({float(worst['on_time_rate']):.0%})")

        # Bar chart
        st.bar_chart(
            df.set_index("carrier_name")[["on_time_rate"]].astype(float),
            use_container_width=True,
        )

        # Table
        st.dataframe(df, use_container_width=True, hide_index=True)

    except Exception as e:
        st.warning(f"Dashboard not available: {str(e)}")

# ──────────────────────────────────────────────
# Tab 3: Anomalies
# ──────────────────────────────────────────────
with tab_anomaly:
    try:
        from agent.nodes.anomaly import detect_anomalies
        result = detect_anomalies()

        st.subheader("Anomaly Detection (Z-Score)")
        st.markdown(result["summary"])

        if result["anomalies"]:
            st.dataframe(result["anomalies"], use_container_width=True, hide_index=True)
        else:
            st.success("No anomalies detected. All KPIs within normal range.")

    except Exception as e:
        st.warning(f"Anomaly detection not available: {str(e)}")

# ──────────────────────────────────────────────
# Tab 4: EDI Docs (RAG)
# ──────────────────────────────────────────────
with tab_docs:
    st.subheader("EDI Specification Search (RAG)")
    doc_query = st.text_input("Search EDI specs...", placeholder="e.g. 'What is the BGM segment in 850?'")

    if doc_query:
        with st.spinner("Searching..."):
            try:
                from agent.nodes.rag_lookup import rag_lookup
                context = rag_lookup(doc_query)
                st.markdown(context)
            except Exception as e:
                st.error(f"RAG error: {str(e)}")
   
