# healing_game_v2_supabase.py
"""
Healingizz V2.1 — Supabase-only Streamlit app
Run: streamlit run healing_game_v2_supabase.py

All user data is stored in Supabase; no local JSON fallback.
"""
from datetime import datetime, timedelta, date
import time
import random
import json
import uuid
import base64
import hashlib
import os
import streamlit as st
import pandas as pd

# ----- Supabase client -----
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ttcgantxykrnwhskpqsl.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InR0Y2dhbnR4eWtybndoc2twcXNsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjAxNDU1MzMsImV4cCI6MjA3NTcyMTUzM30.9tD4CJwiEeuMzhAmtORGETAyfSsUd7Js7eOanMEdthA")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("⚠️ SUPABASE_URL or SUPABASE_KEY missing in environment.")
    st.stop()

try:
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"Không thể kết nối Supabase: {e}")
    st.stop()

# ----- Config -----
APP_TITLE = "🌱 Healingizz (Beta 2.1 — Supabase-only)"

# ----- Helpers: password hashing -----
def make_salt() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).decode()

def hash_password(password: str, salt: str) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return base64.urlsafe_b64encode(dk).decode()

def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    return hash_password(password, salt, stored_hash) == stored_hash

# ----- Supabase storage -----
def supabase_get_user_record(username: str):
    try:
        res = supabase.table("users").select("*").eq("username", username).execute()
        rows = getattr(res, "data", res)
        if rows:
            return rows[0]
        return None
    except Exception:
        return None

def supabase_create_user_record(username: str, pw_hash: str, salt: str, data_json: dict):
    payload = {"username": username, "pw_hash": pw_hash, "salt": salt, "data": json.dumps(data_json)}
    try:
        res = supabase.table("users").insert(payload).execute()
        return getattr(res, "data", res)[0]
    except Exception:
        return None

def supabase_update_user_data(username: str, data_json: dict):
    try:
        supabase.table("users").update({"data": json.dumps(data_json)}).eq("username", username).execute()
        return True
    except Exception:
        return False

def supabase_load_user_data(username: str):
    rec = supabase_get_user_record(username)
    if not rec:
        return None
    d = rec.get("data")
    if isinstance(d, str):
        try:
            return json.loads(d)
        except Exception:
            return None
    return d

# ----- User state -----
def init_user_state(user_id: str):
    return {
        "user_id": user_id,
        "created_at": datetime.utcnow().isoformat(),
        "profile": {"nickname": user_id.replace("user-",""), "bio": ""},
        "game": {
            "points": 0,
            "streak": 0,
            "last_checkin_date": None,
            "badges": [],
            "quests": {},
            "moods": [],
            "journal": [],
            "reminders": [],
            "quest_counts": {},
            "garden": []
        }
    }

def load_user(user_id: str):
    username = user_id.replace("user-", "")
    rec = supabase_get_user_record(username)
    if rec:
        d = supabase_load_user_data(username)
        if d:
            d.setdefault("user_id", user_id)
            if "game" not in d:
                d["game"] = init_user_state(user_id)["game"]
            return d
        else:
            new = init_user_state(user_id)
            supabase_update_user_data(username, new)
            return new
    else:
        # create new
        data = init_user_state(user_id)
        supabase_create_user_record(username, "", "", data)
        return data

def save_user(data: dict):
    user_id = data.get("user_id")
    if not user_id:
        return
    username = user_id.replace("user-", "")
    supabase_update_user_data(username, data)

# ----- Auth -----
def register_user(username: str, password: str):
    username = username.strip().lower()
    rec = supabase_get_user_record(username)
    if rec:
        return None, "Username đã tồn tại."
    salt = make_salt()
    pw_hash = hash_password(password, salt)
    user_id = f"user-{username}"
    data_obj = init_user_state(user_id)
    created = supabase_create_user_record(username, pw_hash, salt, data_obj)
    if created:
        return user_id, None
    else:
        return None, "Không thể tạo tài khoản (Supabase)."

def verify_user(username: str, password: str):
    username = username.strip().lower()
    rec = supabase_get_user_record(username)
    if not rec:
        return None, "Tài khoản không tồn tại."
    salt = rec.get("salt", "")
    stored = rec.get("pw_hash", "")
    if hash_password(password, salt) == stored:
        user_id = f"user-{username}"
        return user_id, None
    else:
        return None, "Sai password."

def register_or_login(username: str, password: str, register: bool=False):
    if register:
        return register_user(username, password)
    else:
        return verify_user(username, password)

# ----- Quests & game mechanics -----
QUEST_TEMPLATES = [
    {"type": "breathing","title": "Thở 4-7-8","desc":"Hít vào 4s – nín 7s – thở ra 8s","points":20,"duration_sec":60},
    {"type": "gratitude","title": "Điều ý nghĩa hôm nay","desc":"Viết 1 điều bạn thấy ý nghĩa trong ngày","points":25},
    {"type": "mini_mindful","title": "Nhắm mắt thở 30s","desc":"Nhắm mắt, chú ý cảm giác 30s","points":10,"duration_sec":30}
]

def todays_seed(user_id: str):
    base = f"{user_id}-{date.today().isoformat()}"
    return abs(hash(base)) % (2**32)

def daily_quests(user_id: str, k=4):
    rnd = random.Random(todays_seed(user_id))
    picks = rnd.sample(QUEST_TEMPLATES, k=min(k, len(QUEST_TEMPLATES)))
    result = []
    for q in picks:
        qid = f"{q['type']}-{date.today().isoformat()}"
        result.append({**q, "quest_id": qid})
    return result

def mark_quest_completed(data: dict, quest: dict, payload: dict) -> bool:
    qid = quest["quest_id"]
    if qid not in data["game"].get("quests", {}):
        now = datetime.utcnow().isoformat()
        data["game"].setdefault("quests", {})[qid] = {
            "quest_id": qid,
            "type": quest["type"],
            "title": quest["title"],
            "completed_at": now,
            "payload": payload,
            "points": quest.get("points", 0)
        }
        tc = data["game"].setdefault("quest_counts", {})
        tc[quest["type"]] = tc.get(quest["type"], 0) + 1
        add_points(data, quest.get("points", 0))
        save_user(data)
        return True
    return False

def add_points(data: dict, amount: int, reason: str=""):
    data["game"]["points"] += amount
    save_user(data)
    if reason:
        st.success(f"+{amount} điểm — {reason}")
    else:
        st.success(f"+{amount} điểm")

def is_quest_done(data: dict, quest_id: str) -> bool:
    return quest_id in data["game"].get("quests", {})

# ----- Streamlit App -----
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🌱", layout="wide")
    st.title(APP_TITLE)
    st.caption("Không gian để chậm lại và lắng nghe chính mình.")

    # --- Auth ---
    if "user_id" not in st.session_state:
        st.session_state["user_id"] = None

    with st.expander("🔐 Đăng nhập / Đăng ký"):
        col1, col2 = st.columns([2,1])
        with col1:
            ui_username = st.text_input("Username:", key="auth_user")
            ui_password = st.text_input("Mật khẩu:", type="password", key="auth_pw")
        with col2:
            st.write("Supabase-only ✅")
            if st.button("Đăng nhập"):
                uid, err = register_or_login(ui_username, ui_password, register=False)
                if uid:
                    st.session_state["user_id"] = uid
                    st.success("Đăng nhập thành công.")
                    st.rerun()
                else:
                    st.error(err)
            if st.button("Đăng ký"):
                uid, err = register_or_login(ui_username, ui_password, register=True)
                if uid:
                    st.session_state["user_id"] = uid
                    st.success("Đăng ký & đăng nhập thành công.")
                    st.rerun()
                else:
                    st.error(err)

    if not st.session_state.get("user_id"):
        st.info("Vui lòng đăng nhập hoặc đăng ký.")
        st.stop()

    user_id = st.session_state["user_id"]
    data = load_user(user_id)

    # Simple dashboard
    st.subheader(f"Xin chào, {data['profile'].get('nickname', user_id)}")
    st.write(f"Điểm hiện tại: {data['game'].get('points',0)}")
    st.write(f"Số hoạt động đã hoàn thành: {len(data['game'].get('quests',{}))}")

    # Daily quests
    st.markdown("---")
    st.header("🎯 Hoạt động hôm nay")
    quests = daily_quests(user_id)
    for q in quests:
        done = is_quest_done(data, q["quest_id"])
        with st.expander(f"{q['title']} {'✅' if done else ''}"):
            st.write(q.get("desc",""))
            if not done:
                if "duration_sec" in q:
                    with st.spinner(f"Thực hành {q['duration_sec']} giây..."):
                        time.sleep(min(q['duration_sec'], 5))  # demo max 5s
                if st.button(f"Hoàn thành '{q['title']}'"):
                    mark_quest_completed(data, q, payload={"demo":True})

    # Journal
    st.markdown("---")
    st.header("📓 Nhật ký")
    entry = st.text_area("Ghi gì đó cho hôm nay...")
    if st.button("Thêm nhật ký"):
        if entry.strip():
            data["game"].setdefault("journal", []).append({
                "ts": datetime.utcnow().isoformat(),
                "text": entry.strip()
            })
            save_user(data)
            st.success("Đã thêm vào nhật ký.")

    # Mood check
    st.markdown("---")
    st.header("😌 Tâm trạng hôm nay")
    mood = st.slider("Chọn mood (1 tệ – 10 tuyệt vời)", 1, 10, 5)
    if st.button("Lưu mood"):
        data["game"].setdefault("moods", []).append({
            "ts": datetime.utcnow().isoformat(),
            "value": mood
        })
        save_user(data)
        st.success(f"Đã lưu mood: {mood}")

if __name__ == "__main__":
    main()
