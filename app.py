"""
app.py — Streamlit UI for TripMate, backed by the n8n agent workflow.

    python3 -m streamlit run app.py

WHAT CHANGED FROM THE PYTHON-AGENT VERSION: this file no longer contains
or imports any planning logic at all. There's no agent.py, no tools.py, no
schema.py — all of that (the tool-calling loop, the three real API calls,
the structured-output schema) now lives inside the n8n workflow
("TripMate_Agent_Webhook.json"). This file's job is:

  1. Collect trip preferences from the sidebar (same UI as before)
  2. Quickly validate the destination is a real place (see below)
  3. POST them as JSON to the n8n webhook
  4. Render whatever itinerary JSON comes back

If you want to change how the agent plans trips (which tools it uses, the
system prompt, the output schema), you edit the n8n workflow — not this
file. This file only needs to change if you want to change what's
COLLECTED from the user, how the destination is PRE-VALIDATED, or how the
result is DISPLAYED.

WHY THERE'S A DIRECT NOMINATIM CALL HERE: this file has no access to
tools.py's geocode_destination() — that logic lives inside n8n now. So the
destination_looks_valid() pre-check below makes its own small, direct call
to the same free geocoding service, purely to reject an obvious typo like
"asdlkfj" in under a second instead of sending it all the way to n8n,
waiting through a full agent run, and getting back a raw HTTP error.
"""

import json
import requests
import streamlit as st

st.set_page_config(page_title="TripMate", page_icon="🗺️", layout="wide")


# =========================================================
# N8N WEBHOOK
# =========================================================

# Paste your n8n workflow's Webhook node URL here — it's the "Production
# URL" shown on the Webhook node once the workflow is active, e.g.:
#   https://your-instance.app.n8n.cloud/webhook/plan-trip

N8N_WEBHOOK_URL = "https://lobali.app.n8n.cloud/webhook/plan-trip"


def destination_looks_valid(destination_name: str) -> bool:
    """
    Quick pre-check: confirms the destination resolves to a real place via
    Nominatim (a free geocoding service, no API key needed) BEFORE POSTing
    to n8n and waiting through a full agent run over a typo.

    This duplicates the geocoding n8n's own agent will do anyway — that's
    fine, it's a free service and this call is deliberately tiny (limit=1,
    8-second timeout). The point isn't to avoid n8n calling it too; it's
    to fail fast, locally, with a friendly message.

    Returns:
        True if the destination looks real, or if the geocoding service
        itself couldn't be reached (fails open — better to let the n8n
        workflow's own attempt surface a real problem than to block
        someone here over a flaky network blip).
    """
    try:
        response = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": destination_name, "format": "json", "limit": 1},
            headers={"User-Agent": "TripMate-Validator/1.0"},
            timeout=8,
        )
        response.raise_for_status()
        return bool(response.json())  # empty list = Nominatim found nothing real
    except requests.exceptions.RequestException:
        return True


def pick_emoji_for_category(category_text: str) -> str:
    """
    Small COSMETIC helper: pick a fun emoji for a stop based on its
    category text, purely so the itinerary is easier to scan at a glance.
    Has zero effect on planning — pure presentation.
    """
    category_text = (category_text or "").lower()
    keyword_to_emoji = {
        "museum": "🏛️", "art": "🖼️", "gallery": "🖼️",
        "restaurant": "🍽️", "food": "🍽️", "cafe": "☕",
        "park": "🌳", "garden": "🌳", "nature": "🌳",
        "church": "⛪", "cathedral": "⛪", "temple": "⛩️", "historic": "🏰",
        "view": "🌅", "viewpoint": "🌅", "lookout": "🌅",
        "beach": "🏖️", "market": "🛍️", "shopping": "🛍️",
        "bar": "🍸", "nightlife": "🌙", "zoo": "🦁", "aquarium": "🐠",
    }
    for keyword, emoji in keyword_to_emoji.items():
        if keyword in category_text:
            return emoji
    return "📍"


st.title("🗺️ TripMate")
st.caption("Tell me where and how long — I'll build a real, walkable day-by-day plan around what you actually like.")

if not N8N_WEBHOOK_URL:
    st.error(
        "Set N8N_WEBHOOK_URL near the top of app.py to your n8n workflow's "
        "Webhook node URL, e.g. https://your-instance.app.n8n.cloud/webhook/plan-trip"
    )
    st.stop()

# ---------------------------------------------------------------------------
# Same curated destination/interest lists as before — this UI is unchanged
# from the direct-OpenAI version. Only what happens after the button click
# is different.
# ---------------------------------------------------------------------------
DESTINATION_OPTIONS = [
    "Lisbon, Portugal", "Barcelona, Spain", "Rome, Italy", "Paris, France",
    "Amsterdam, Netherlands", "Prague, Czech Republic", "Athens, Greece",
    "Vienna, Austria", "Berlin, Germany", "Istanbul, Turkey",
    "Marrakech, Morocco", "Bangkok, Thailand", "Tokyo, Japan",
    "Other (type my own)",
]

INTEREST_OPTIONS = [
    "Museums", "Viewpoints", "Local food", "Parks & nature",
    "Historic sites", "Art & galleries", "Nightlife", "Shopping", "Beaches",
]

with st.sidebar:
    st.header("✈️ Trip details")

    selected_destination_option = st.selectbox("📍 Destination", DESTINATION_OPTIONS)
    if selected_destination_option == "Other (type my own)":
        destination_name = st.text_input("Type your destination", "")
    else:
        destination_name = selected_destination_option

    number_of_days = st.slider("📅 Duration (days)", 1, 7, 3)
    pace = st.select_slider("🏃 Pace", options=["relaxed", "moderate", "packed"], value="moderate")

    st.markdown("❤️ **What are you into?**")
    checked_interests = []
    for interest_label in INTEREST_OPTIONS:
        default_checked = interest_label in ("Museums", "Local food")
        if st.checkbox(interest_label, value=default_checked, key=f"interest_{interest_label}"):
            checked_interests.append(interest_label.lower())
    interests_text = ", ".join(checked_interests)

    number_of_people = st.number_input("👥 Number of people", min_value=1, max_value=20, value=2)
    total_budget_usd = st.number_input("💰 Total budget in USD (0 = no limit)", min_value=0, value=0, step=50)
    st.divider()

    # Guard against generating with no destination or no interests picked.
    # (Destination REALNESS is checked after the click, below — not on
    # every keystroke, so the sidebar itself stays fast.)
    can_generate = bool(destination_name.strip()) and bool(checked_interests)
    if not can_generate:
        st.caption("⚠️ Pick a destination and at least one interest to continue.")
    generate_button_clicked = st.button(
        "✨ Generate itinerary", type="primary", use_container_width=True, disabled=not can_generate
    )


if generate_button_clicked:

    # -------------------------------------------------------------------
    # STEP 0: validate the destination BEFORE POSTing to n8n and waiting
    # through a full agent run. This is what turns "ri" or "asdlkfj" into
    # an instant, friendly error instead of ~15 seconds followed by a raw
    # HTTPError traceback bubbling up from n8n's geocode_destination call.
    # -------------------------------------------------------------------
    with st.spinner("Checking destination..."):
        destination_is_valid = destination_looks_valid(destination_name)

    if not destination_is_valid:
        st.error(
            f"⚠️ Couldn't find **\"{destination_name}\"** as a real place. "
            "Check the spelling, or try a broader name (e.g. 'Porto' instead of a specific street)."
        )
        st.stop()

    # This is the entire payload the n8n workflow's "Build Trip Request"
    # Code node expects — field names must match exactly, since that node
    # reads them straight off the webhook's JSON body.
    request_payload = {
        "destination_name": destination_name,
        "number_of_days": number_of_days,
        "pace": pace,
        "interests_text": interests_text,
        "number_of_people": number_of_people,
        "total_budget_usd": total_budget_usd,
    }

    itinerary = None

    with st.status("🤖 Agent is planning your trip...", expanded=True) as status:
        st.write("📡 Sending your request to the agent...")

        try:
            # timeout is generous because the n8n workflow itself runs a
            # full tool-calling agent loop (multiple LLM + API round trips)
            # before it can respond — this can genuinely take 15-30+ seconds.
            response = requests.post(N8N_WEBHOOK_URL, json=request_payload, timeout=120)

            # DEBUG: requests silently converts POST to GET when following a
            # 301/302 redirect. If N8N_WEBHOOK_URL doesn't exactly match
            # n8n's expected URL (trailing slash, http vs https, etc.), a
            # redirect can happen here and your POST becomes a GET before
            # n8n ever sees it — which is exactly the "not registered for
            # GET requests" error. This shows if that happened.
            if response.history:
                redirect_chain = " -> ".join(r.url for r in response.history) + " -> " + response.url
                st.warning(f"⚠️ Request was redirected (this may have turned your POST into a GET): {redirect_chain}")

            response.raise_for_status()
            result = response.json()

            if result.get("status") != "success" or "itinerary" not in result:
                status.update(label="❌ Something went wrong", state="error")
                st.error(
                    "The agent didn't return a valid itinerary. This can happen with very obscure "
                    "destinations or a temporary hiccup in the workflow — try again, or try a nearby larger city."
                )
                with st.expander("Technical details (for debugging)"):
                    st.code(json.dumps(result, indent=2)[:1000])
                st.stop()

            itinerary = result["itinerary"]
            status.update(label="✅ Your trip is ready!", state="complete")

        except requests.exceptions.Timeout:
            status.update(label="❌ Timed out", state="error")
            st.error("The agent took too long to respond. It may still be running in n8n — try again in a moment.")
            st.stop()
        except requests.exceptions.HTTPError as e:
            status.update(label="❌ Request failed", state="error")
            st.error(
                "The agent workflow returned an error while planning this trip. "
                "This can happen with very obscure destinations — try again, or try a nearby larger city."
            )
            with st.expander("Technical details (for debugging)"):
                st.code(str(e))
            st.stop()
        except requests.exceptions.RequestException as e:
            status.update(label="❌ Request failed", state="error")
            st.error(f"Couldn't reach the agent: {e}")
            st.stop()


        st.header(f"{itinerary['destination_name']} · {itinerary['number_of_days']} days")

        stat_col1, stat_col2, stat_col3 = st.columns(3)
        stat_col1.metric("👥 Travelers", itinerary["number_of_people"])
        stat_col2.metric("💵 Estimated cost", f"${itinerary['total_estimated_cost_usd']:.0f}")
        stat_col3.metric("🏃 Pace", pace.capitalize())
        st.info(f"💡 {itinerary['budget_summary']}")

        st.divider()

        day_tabs = st.tabs([f"Day {day['day_number']}" for day in itinerary["days"]])

        for day_tab, day in zip(day_tabs, itinerary["days"]):
            with day_tab:
                st.subheader(f"🗓️ {day['day_theme']}")

                stops_in_visit_order = sorted(day["stops"], key=lambda stop: stop["visit_order"])

                for stop in stops_in_visit_order:
                    walking_minutes = stop["walking_minutes_from_previous_stop"]
                    cost_per_person = stop["estimated_cost_per_person_usd"]
                    cost_display_text = f"${cost_per_person:.0f}/person" if cost_per_person else "Free"
                    emoji = pick_emoji_for_category(stop["category"])

                    card_header = f"{emoji} {stop['visit_order']}. {stop['place_name']}"
                    if walking_minutes:
                        card_header += f"  ·  🚶 {walking_minutes} min away"

                    with st.expander(card_header, expanded=True):
                        st.markdown(stop["why_this_stop"])
                        badge_col1, badge_col2, badge_col3 = st.columns(3)
                        badge_col1.markdown(f"🏷️ **{stop['category']}**")
                        badge_col2.markdown(f"⏱️ **{stop['estimated_visit_minutes']} min**")
                        badge_col3.markdown(f"💵 **{cost_display_text}**")
