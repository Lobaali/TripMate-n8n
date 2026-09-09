"""
app.py — Streamlit UI for TripMate, backed by the n8n agent workflow.

    python3 -m streamlit run app.py

This file has no planning logic — that lives inside the n8n workflow.
This file: collect trip preferences -> pre-validate the destination ->
POST to n8n -> render whatever itinerary comes back.
"""

import json
import time
import requests
import streamlit as st

st.set_page_config(page_title="TripMate", page_icon="🗺️", layout="wide")


# =========================================================
# N8N WEBHOOK
# =========================================================
N8N_WEBHOOK_URL = "https://lobali.app.n8n.cloud/webhook/plan-trip"

# Paste your free Geoapify API key here — https://myprojects.geoapify.com
# (free tier: 3000 requests/day). Used for the local pre-check below.
GEOAPIFY_KEY = "PASTE_YOUR_GEOAPIFY_KEY_HERE"


# ---------------------------------------------------------------------------
# Known coordinates for every city in the curated dropdown — no API call
# needed for these at all.
# ---------------------------------------------------------------------------
KNOWN_DESTINATION_COORDINATES = {
    "Lisbon, Portugal": (38.7223, -9.1393),
    "Barcelona, Spain": (41.3874, 2.1686),
    "Rome, Italy": (41.9028, 12.4964),
    "Paris, France": (48.8566, 2.3522),
    "Amsterdam, Netherlands": (52.3676, 4.9041),
    "Prague, Czech Republic": (50.0755, 14.4378),
    "Athens, Greece": (37.9838, 23.7275),
    "Vienna, Austria": (48.2082, 16.3738),
    "Berlin, Germany": (52.5200, 13.4050),
    "Istanbul, Turkey": (41.0082, 28.9784),
    "Marrakech, Morocco": (31.6295, -7.9811),
    "Bangkok, Thailand": (13.7563, 100.5018),
    "Tokyo, Japan": (35.6762, 139.6503),
}


def check_destination(destination_name: str, max_retries: int = 2):
    """
    Confirms the destination resolves to a real place, BEFORE POSTing to
    n8n and waiting through a full agent run.

    USES GEOAPIFY, NOT NOMINATIM — see tools.py / the n8n canvas notes for
    why. Geoapify rate-limits per API KEY, not per shared Streamlit Cloud
    IP, which is what made Nominatim unreliable here even for correctly
    spelled destinations.

    Returns:
        (status, detail): status is "valid" / "invalid" / "unknown".
        detail is None unless status is "unknown", in which case it's the
        actual exception/status code from the last failed attempt.
    """
    if destination_name in KNOWN_DESTINATION_COORDINATES:
        return "valid", None

    if "destination_check_cache" not in st.session_state:
        st.session_state.destination_check_cache = {}
    cached = st.session_state.destination_check_cache.get(destination_name)
    if cached is not None:
        return cached, None

    last_detail = None

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                "https://api.geoapify.com/v1/geocode/search",
                params={"text": destination_name, "limit": 1, "apiKey": GEOAPIFY_KEY},
                timeout=8,
            )
            response.raise_for_status()
            features = response.json().get("features", [])
            result = "valid" if features else "invalid"
            st.session_state.destination_check_cache[destination_name] = result
            return result, None
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "no response"
            response_snippet = (e.response.text[:200] if e.response is not None else "")
            last_detail = f"Attempt {attempt}/{max_retries}: HTTP {status_code} — {e}\nBody: {response_snippet}"
            if status_code == 429 and attempt < max_retries:
                time.sleep(attempt)
                continue
            return "unknown", last_detail
        except requests.exceptions.RequestException as e:
            last_detail = f"Attempt {attempt}/{max_retries}: {type(e).__name__} — {e}"
            if attempt < max_retries:
                time.sleep(attempt)
                continue
            return "unknown", last_detail

    return "unknown", last_detail


def pick_emoji_for_category(category_text: str) -> str:
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
    st.error("Set N8N_WEBHOOK_URL near the top of app.py to your n8n workflow's Webhook node URL.")
    st.stop()

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

    can_generate = bool(destination_name.strip()) and bool(checked_interests)
    if not can_generate:
        st.caption("⚠️ Pick a destination and at least one interest to continue.")
    generate_button_clicked = st.button(
        "✨ Generate itinerary", type="primary", use_container_width=True, disabled=not can_generate
    )


if generate_button_clicked:

    with st.spinner("Checking destination..."):
        validation_result, validation_detail = check_destination(destination_name)

    if validation_result == "invalid":
        st.error(
            f"⚠️ Couldn't find **\"{destination_name}\"** as a real place. "
            "Check the spelling, or try a broader name (e.g. 'Porto' instead of a specific street)."
        )
        st.stop()
    elif validation_result == "unknown":
        st.warning(
            f"⏳ Couldn't verify **\"{destination_name}\"** right now — the geocoding check failed. "
            "See the technical details below for the actual cause."
        )
        with st.expander("Technical details (for debugging)", expanded=True):
            st.code(validation_detail or "No detail captured.")
        st.stop()

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
            response = requests.post(N8N_WEBHOOK_URL, json=request_payload, timeout=120)

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
