# healing_game_v2.py
"""
Healingizz V2.0 — Single-file Streamlit app
Chạy: streamlit run healing_game_v2.py
"""
from pathlib import Path
from datetime import datetime, timedelta, date, time as dtime
import time
import random
import json
import uuid
import streamlit as st
import pandas as pd
import altair as alt
import io

# ----- Config -----
APP_TITLE = "🌱 Healingizz V2.0 — Hành trình chữa lành"
DATA_DIR = Path("healing_data")
DATA_DIR.mkdir(exist_ok=True)

# ----- Storage helpers -----
def user_file(user_id: str) -> Path:
    return DATA_DIR / f"{user_id}.json"

def init_user_state(user_id: str):
    return {
        "user_id": user_id,
        "created_at": datetime.utcnow().isoformat(),
        "profile": {
            "nickname": user_id.replace("user-",""),
            "bio": "",
        },
        "game": {
            "points": 0,
            "streak": 0,
            "last_checkin_date": None,
            "badges": [],
            "quests": {},        # quest_id -> dict
            "moods": [],        # [{date, mood, note}]
            "journal": [],      # [{date, title, content}]
            "reminders": [],    # [{id, time_iso, label, done}]
            "quest_counts": {}, # type -> count
        }
    }

def load_user(user_id: str):
    f = user_file(user_id)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            # if corrupted, reinit (but backup)
            backup = DATA_DIR / f"{user_id}.backup.json"
            backup.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            data = init_user_state(user_id)
            save_user(data)
            return data
    data = init_user_state(user_id)
    save_user(data)
    return data

def save_user(data: dict):
    f = user_file(data["user_id"])
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ----- Basic mechanics -----
def add_points(data: dict, amount: int, reason: str = ""):
    data["game"]["points"] += amount
    save_user(data)
    st.success(f"+{amount} điểm 🎉 {('— ' + reason) if reason else ''}")

def update_streak_on_checkin(data: dict):
    today = date.today()
    last = data["game"]["last_checkin_date"]
    if last is None:
        data["game"]["streak"] = 1
    else:
        try:
            last_date = datetime.fromisoformat(last).date()
        except Exception:
            last_date = None
        if last_date is None:
            data["game"]["streak"] = 1
        else:
            if today == last_date:
                pass
            elif today == (last_date + timedelta(days=1)):
                data["game"]["streak"] += 1
            else:
                data["game"]["streak"] = 1
    data["game"]["last_checkin_date"] = datetime.combine(today, datetime.min.time()).isoformat()
    save_user(data)

def check_badges(data: dict):
    pts = data["game"]["points"]
    streak = data["game"]["streak"]
    qcounts = data["game"].get("quest_counts", {})
    badges = set(data["game"]["badges"])
    new = []

    # basic thresholds
    if pts >= 50 and "Người khởi đầu" not in badges:
        new.append("Người khởi đầu")
    if pts >= 150 and "Nhà thám hiểm nội tâm" not in badges:
        new.append("Nhà thám hiểm nội tâm")
    if streak >= 3 and "3 ngày liên tục" not in badges:
        new.append("3 ngày liên tục")
    if streak >= 7 and "7 ngày liên tục" not in badges:
        new.append("7 ngày liên tục")

    # type-based badges
    if qcounts.get("gratitude",0) >= 5 and "Nhà viết nhật ký" not in badges:
        new.append("Nhà viết nhật ký")
    if qcounts.get("breathing",0) >= 10 and "Người kiên nhẫn" not in badges:
        new.append("Người kiên nhẫn")
    if qcounts.get("kind_act",0) >= 5 and "Người tử tế" not in badges:
        new.append("Người tử tế")

    if new:
        data["game"]["badges"].extend(new)
        save_user(data)
        for b in new:
            st.balloons()
            st.success(f"🏅 Mở khóa huy hiệu: **{b}**")

# ----- Quests -----
QUEST_TEMPLATES = [
    {
        "type": "breathing",
        "title": "Thở 4-7-8 (1 phút)",
        "desc": "Thở vào 4s – nín 7s – thở ra 8s. Lặp lại nhiều vòng.",
        "points": 20,
        "duration_sec": 60
    },
    {
        "type": "gratitude",
        "title": "Nhật ký biết ơn (3 điều)",
        "desc": "Viết 3 điều bạn biết ơn hôm nay, càng cụ thể càng tốt.",
        "points": 25
    },
    {
        "type": "reframe",
        "title": "Tái cấu trúc suy nghĩ (CBT)",
        "desc": "Chọn 1 suy nghĩ tiêu cực, tìm bằng chứng ủng hộ/phản bác, rồi viết lại phiên bản cân bằng.",
        "points": 30
    },
    {
        "type": "mindful_walk",
        "title": "Đi bộ chánh niệm (5 phút)",
        "desc": "Đi chậm rãi, chú ý bàn chân chạm đất, nhịp thở, âm thanh xung quanh.",
        "points": 20
    },
    {
        "type": "kind_act",
        "title": "Hành động tử tế ngẫu nhiên",
        "desc": "Làm 1 việc tử tế nhỏ (khen ngợi chân thành, giúp đỡ, nhắn lời cảm ơn).",
        "points": 20
    },
    {
        "type": "mini_mindful",
        "title": "Mini-chánh niệm (30s)",
        "desc": "Nhắm mắt, chú ý cảm giác 30 giây. Không dùng điện thoại.",
        "points": 10,
        "duration_sec": 30
    },
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

def mark_quest_completed(data: dict, quest: dict, payload: dict):
    qid = quest["quest_id"]
    if qid not in data["game"]["quests"]:
        now = datetime.utcnow().isoformat()
        data["game"]["quests"][qid] = {
            "quest_id": qid,
            "type": quest["type"],
            "title": quest["title"],
            "completed_at": now,
            "payload": payload,
            "points": quest.get("points", 0)
        }
        # increment type count
        tc = data["game"].setdefault("quest_counts", {})
        tc[quest["type"]] = tc.get(quest["type"], 0) + 1
        save_user(data)
        add_points(data, quest.get("points", 0), reason=f"Hoàn thành: {quest['title']}")
        check_badges(data)
    else:
        st.info("Bạn đã hoàn thành nhiệm vụ này hôm nay ✔️")

def is_quest_done(data: dict, quest_id: str) -> bool:
    return quest_id in data["game"]["quests"]

# ----- UI helpers -----
def ui_sidebar(data: dict):
    st.sidebar.title("👤 Hồ sơ")
    nickname = st.sidebar.text_input("Nickname", value=data["profile"].get("nickname",""))
    bio = st.sidebar.text_area("Giới thiệu ngắn", value=data["profile"].get("bio",""), help="Tùy chọn")
    if nickname != data["profile"].get("nickname","") or bio != data["profile"].get("bio",""):
        data["profile"]["nickname"] = nickname
        data["profile"]["bio"] = bio
        save_user(data)

    st.sidebar.markdown("---")
    points = data['game']['points']
    level = points // 100 + 1
    next_level_pts = level * 100
    progress = points % 100
    st.sidebar.metric("Điểm", points, delta=None)
    st.sidebar.metric("Streak 🔥", data['game']['streak'])
    st.sidebar.markdown(f"**Level:** {level}")
    st.sidebar.progress(progress/100.0)
    st.sidebar.caption(f"{progress}/100 đến level {level+1}")

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Huy hiệu**")
    badges = data["game"].get("badges", [])
    if badges:
        cols = st.sidebar.columns(3)
        for i,b in enumerate(badges):
            cols[i%3].write(f"🏅 {b}")
    else:
        st.sidebar.write("Chưa có huy hiệu nào.")

    st.sidebar.markdown("---")
    st.sidebar.markdown("⚙️ Tùy chọn")
    if st.sidebar.button("Đặt lại điểm (dev)", key="reset_points"):
        data["game"]["points"] = 0
        save_user(data)
        st.sidebar.success("Đã đặt lại điểm.")
    st.sidebar.caption("Dữ liệu lưu cục bộ (JSON mỗi người dùng).")

def mood_emoji(score: int):
    if score <= 2: return "😢"
    if score <= 4: return "😟"
    if score <= 6: return "😐"
    if score <= 8: return "🙂"
    return "🤩"

def ui_breathing(duration_sec: int):
    st.write("**Hướng dẫn:** Thở vào 4s → nín 7s → thở ra 8s.")
    placeholder = st.empty()
    phase = [("Hít vào", 4), ("Nín thở", 7), ("Thở ra", 8)]
    start = time.time()
    while time.time() - start < duration_sec:
        for label, secs in phase:
            for t in range(secs, 0, -1):
                placeholder.markdown(f"### {label} — {t}s")
                time.sleep(1)
                if time.time() - start >= duration_sec:
                    break
            if time.time() - start >= duration_sec:
                break
    placeholder.markdown("### ✅ Hoàn tất phiên thở!")

# ----- Leaderboard -----
def load_all_users():
    users = []
    for f in DATA_DIR.glob("user-*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            users.append(d)
        except Exception:
            continue
    return users

def top_leaderboard(n=5):
    users = load_all_users()
    users_sorted = sorted(users, key=lambda u: u["game"].get("points",0), reverse=True)
    rows = []
    for u in users_sorted[:n]:
        rows.append({
            "nickname": u["profile"].get("nickname", u["user_id"]),
            "points": u["game"].get("points",0),
            "streak": u["game"].get("streak",0),
            "badges": ", ".join(u["game"].get("badges", []))
        })
    return pd.DataFrame(rows)

# ----- Quotes & media -----
QUOTES = [
    "Hãy tử tế với bản thân — bạn đang làm tốt lắm.",
    "Hít sâu. Một bước nhỏ cũng là tiến bộ.",
    "Không phải ai cũng có một ngày tốt, và điều đó ổn.",
    "Cảm xúc là tạm thời. Bạn không phải là những gì bạn cảm thấy.",
    "Hãy dành 5 phút cho bản thân — bạn xứng đáng."
]

MEDITATION_VIDEO = "https://www.youtube.com/watch?v=inpok4MKVLM"  # gentle breathing music (example)

# ----- Journal helpers -----
def export_journal_to_txt(data: dict):
    lines = []
    for entry in data["game"].get("journal", []):
        lines.append(f"=== {entry.get('date','')} — {entry.get('title','(No title)')} ===")
        lines.append(entry.get("content",""))
        lines.append("\n")
    return "\n".join(lines)

# ----- Reminders helpers -----
def add_reminder(data: dict, at_iso: str, label: str):
    rid = str(uuid.uuid4())
    data["game"].setdefault("reminders", []).append({
        "id": rid,
        "time_iso": at_iso,
        "label": label,
        "done": False
    })
    save_user(data)
    st.success("Đã đặt nhắc nhở cục bộ.")

def cleanup_past_reminders(data: dict):
    # optional: mark past reminders done if older than 1 day
    now = datetime.utcnow()
    for r in data["game"].get("reminders", []):
        try:
            t = datetime.fromisoformat(r["time_iso"])
            if t < now - timedelta(days=1):
                r["done"] = True
        except Exception:
            continue
    save_user(data)

# ----- Main App -----
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🌱", layout="wide", initial_sidebar_state="expanded")
    st.title(APP_TITLE)
    st.caption("Biến các bài tập chữa lành thành nhiệm vụ thú vị mỗi ngày.")

    # --- login ---
    st.markdown("#### Đăng nhập (local)")
    col1, col2 = st.columns([4,1], vertical_alignment="center")  # thêm vertical_alignment
    with col1:
        nickname = st.text_input(
            "Nhập nickname (tạo mới nếu chưa có):",
            value=st.session_state.get("nickname", "")
        )
    with col2:
        btn_login = st.button("Bắt đầu 🚀", use_container_width=True)

    if btn_login and nickname.strip():
        user_id = f"user-{nickname.strip().lower().replace(' ','_')}"
        st.session_state["user_id"] = user_id
        st.session_state["nickname"] = nickname.strip()
    if "user_id" not in st.session_state:
        st.info("Nhập nickname và bấm **Bắt đầu** để vào app.")
        st.stop()

    user_id = st.session_state["user_id"]
    data = load_user(user_id)
    if data["profile"].get("nickname","") != st.session_state.get("nickname",""):
        data["profile"]["nickname"] = st.session_state["nickname"]
        save_user(data)

    ui_sidebar(data)
    cleanup_past_reminders(data)

    # Top area: dashboard
    st.markdown("---")
    st.header("🏠 Dashboard")
    col1, col2, col3 = st.columns([2,2,2])
    with col1:
        st.subheader("Tóm tắt hôm nay")
        last_mood = data["game"]["moods"][-1] if data["game"]["moods"] else None
        st.metric("Điểm", data["game"]["points"])
        st.metric("Streak 🔥", data["game"]["streak"])
        if last_mood:
            m = last_mood["mood"]
            st.write(f"Check-in gần nhất: {mood_emoji(m)} ({m}) — {datetime.fromisoformat(last_mood['date']).strftime('%Y-%m-%d %H:%M')}")
        else:
            st.write("Chưa có check-in nào.")
        # random quote
        if st.button("Gợi cảm hứng — Quote mới"):
            st.info(random.choice(QUOTES))

    with col2:
        st.subheader("Biểu đồ mood (30 ngày)")
        moods = data["game"].get("moods", [])
        if moods:
            df = pd.DataFrame(moods)
            df["date_parsed"] = pd.to_datetime(df["date"])
            df_plot = df.sort_values("date_parsed").tail(60)[["date_parsed","mood"]]
            chart = alt.Chart(df_plot).mark_line(point=True).encode(
                x=alt.X("date_parsed:T", title="Ngày"),
                y=alt.Y("mood:Q", title="Mood (1-10)")
            ).properties(height=200)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.caption("Chưa có dữ liệu mood để vẽ.")

    with col3:
        st.subheader("Leaderboard (Top 5)")
        lb = top_leaderboard(5)
        if not lb.empty:
            st.table(lb)
        else:
            st.caption("Chưa có user nào khác.")

    st.markdown("---")
    # --- Mood check-in & quick actions ---
    st.header("🧭 Check-in cảm xúc & Nhiệm vụ nhanh")
    col1, col2 = st.columns([2,3])
    with col1:
        default_mood = 6
        mood = st.slider("Tâm trạng (1 rất tệ → 10 rất tốt):", 1, 10, default_mood)
        note = st.text_area("Ghi chú (tùy chọn):", placeholder="Ví dụ: Hôm nay hơi lo lắng vì deadline…", height=80)
        if st.button("Lưu check-in ✅"):
            data["game"].setdefault("moods", []).append({
                "date": datetime.utcnow().isoformat(),
                "mood": int(mood),
                "note": note.strip()
            })
            update_streak_on_checkin(data)
            add_points(data, 10, reason="Check-in cảm xúc")
            check_badges(data)
    with col2:
        st.markdown("**Quick actions**")
        if st.button("Phiên thở 1 phút"):
            ui_breathing(60)
            # mark a quick completion (ad-hoc)
            q = {"type":"breathing","title":"Phiên thở nhanh","quest_id":f"quick-breath-{datetime.utcnow().date().isoformat()}","points":15}
            mark_quest_completed(data, q, {"completed": True})
        if st.button("Ghi 3 điều biết ơn"):
            st.session_state["_open_grat"] = True
            st.experimental_rerun()

    # --- Daily quests ---
    st.markdown("---")
    st.header("🎯 Nhiệm vụ hôm nay")
    quests = daily_quests(user_id, k=4)
    for q in quests:
        done = is_quest_done(data, q["quest_id"])
        with st.expander(f"{'✅' if done else '🕹️'} {q['title']}", expanded=not done):
            st.caption(q["desc"])
            if done:
                st.success("Đã hoàn thành.")
            else:
                if q["type"] == "breathing":
                    if st.button("Bắt đầu phiên thở", key=q["quest_id"]+"start"):
                        ui_breathing(q.get("duration_sec",60))
                    if st.button("Đánh dấu hoàn thành", key=q["quest_id"]+"done"):
                        mark_quest_completed(data, q, {"completed": True})
                elif q["type"] == "gratitude":
                    g1 = st.text_input("Biết ơn #1", key=q["quest_id"]+"g1")
                    g2 = st.text_input("Biết ơn #2", key=q["quest_id"]+"g2")
                    g3 = st.text_input("Biết ơn #3", key=q["quest_id"]+"g3")
                    if st.button("Lưu & hoàn thành", key=q["quest_id"]+"save"):
                        entries = [g1.strip(), g2.strip(), g3.strip()]
                        if sum(1 for x in entries if x) >= 3:
                            mark_quest_completed(data, q, {"gratitude": entries})
                        else:
                            st.error("Hãy điền đủ 3 điều biết ơn nhé!")
                elif q["type"] == "reframe":
                    neg = st.text_area("Suy nghĩ tiêu cực", key=q["quest_id"]+"neg")
                    pro = st.text_area("Bằng chứng ủng hộ", key=q["quest_id"]+"pro")
                    con = st.text_area("Bằng chứng phản bác", key=q["quest_id"]+"con")
                    bal = st.text_area("Phiên bản cân bằng", key=q["quest_id"]+"bal")
                    if st.button("Lưu & hoàn thành", key=q["quest_id"]+"save"):
                        if neg.strip() and bal.strip():
                            payload = {"negative": neg.strip(), "evidence_for": pro.strip(), "evidence_against": con.strip(), "balanced": bal.strip()}
                            mark_quest_completed(data, q, payload)
                        else:
                            st.error("Điền ít nhất Suy nghĩ tiêu cực và Phiên bản cân bằng.")
                elif q["type"] == "mindful_walk":
                    st.info("Hẹn giờ 5 phút, tập trung cảm nhận bước chân.")
                    if st.button("Tôi đã hoàn thành", key=q["quest_id"]+"done"):
                        mark_quest_completed(data, q, {"completed": True})
                elif q["type"] == "kind_act":
                    desc = st.text_area("Bạn đã làm điều tử tế gì?", key=q["quest_id"]+"desc")
                    if st.button("Lưu & hoàn thành", key=q["quest_id"]+"save"):
                        if desc.strip():
                            mark_quest_completed(data, q, {"act": desc.strip()})
                        else:
                            st.error("Mô tả ngắn gọn hành động tử tế nhé!")
                elif q["type"] == "mini_mindful":
                    if st.button("Bắt đầu 30s chánh niệm", key=q["quest_id"]+"start"):
                        ui_breathing(q.get("duration_sec",30))
                    if st.button("Hoàn thành", key=q["quest_id"]+"done"):
                        mark_quest_completed(data, q, {"completed": True})

    # --- Journal & export ---
    st.markdown("---")
    st.header("📔 Nhật ký & Xuất")
    colj1, colj2 = st.columns([2,1])
    with colj1:
        with st.expander("Viết nhật ký mới"):
            jtitle = st.text_input("Tiêu đề", key="jtitle")
            jcontent = st.text_area("Nội dung", key="jcontent", height=200)
            if st.button("Lưu nhật ký"):
                if jcontent.strip():
                    data["game"].setdefault("journal", []).append({
                        "date": datetime.utcnow().isoformat(),
                        "title": jtitle.strip() if jtitle.strip() else "(No title)",
                        "content": jcontent.strip()
                    })
                    save_user(data)
                    add_points(data, 5, reason="Viết nhật ký")
                    st.success("Đã lưu nhật ký.")
                else:
                    st.error("Nhật ký trống.")

        with st.expander("Lịch sử nhật ký"):
            j = data["game"].get("journal", [])
            if j:
                for e in reversed(j[-50:]):
                    st.write(f"**{e.get('title','(No title)')}** — {datetime.fromisoformat(e['date']).strftime('%Y-%m-%d %H:%M')}")
                    st.write(e.get("content",""))
                    st.markdown("---")
            else:
                st.caption("Chưa có nhật ký nào.")
    with colj2:
        txt = export_journal_to_txt(data)
        if txt:
            b = txt.encode("utf-8")
            st.download_button("Tải nhật ký (.txt)", data=b, file_name=f"{user_id}_journal.txt", mime="text/plain")
        else:
            st.caption("Không có gì để xuất.")

    # --- Reminders ---
    st.markdown("---")
    st.header("⏰ Nhắc nhở (local)")
    with st.expander("Quản lý nhắc nhở"):
        rtime = st.time_input("Chọn giờ (local):", value=dtime(hour=20, minute=0))
        rlabel = st.text_input("Nội dung nhắc:", value="Check-in cảm xúc")
        if st.button("Thêm nhắc nhở"):
            # assume local time -> convert to UTC naive by today's date
            dt_local = datetime.combine(date.today(), rtime)
            # store ISO (naive) and compare by local time when showing (we'll compare using local)
            add_reminder(data, dt_local.isoformat(), rlabel)

        # list reminders
        rems = data["game"].get("reminders", [])
        if rems:
            for r in rems:
                tstr = r.get("time_iso","")
                done = r.get("done", False)
                cols = st.columns([3,1,1])
                cols[0].write(f"🕒 {tstr} — {r.get('label','')}")
                if not done:
                    if cols[1].button("Đánh dấu xong", key=f"done_{r['id']}"):
                        r["done"] = True
                        save_user(data)
                        st.success("Đã đánh dấu xong.")
                else:
                    cols[1].write("✅ Đã xong")
                if cols[2].button("Xóa", key=f"del_{r['id']}"):
                    data["game"]["reminders"] = [x for x in rems if x["id"] != r["id"]]
                    save_user(data)
                    st.experimental_rerun()
        else:
            st.caption("Chưa có nhắc nhở nào.")

    # Show due reminders (simple check comparing hour/minute local)
    now_local = datetime.now()
    due = []
    for r in data["game"].get("reminders", []):
        try:
            t = datetime.fromisoformat(r["time_iso"])
            if not r.get("done", False) and t.hour == now_local.hour and t.minute == now_local.minute:
                due.append(r)
        except Exception:
            continue
    if due:
        for d in due:
            st.warning(f"🔔 Nhắc: {d.get('label')} — {d.get('time_iso')}")

    # --- Meditation video & extras ---
    st.markdown("---")
    st.header("🧘 Thiền & Tài nguyên")
    st.write("Video thiền gợi ý:")
    st.video(MEDITATION_VIDEO)
    if st.button("Quote of the day"):
        st.info(random.choice(QUOTES))

    # --- History & progress ---
    st.markdown("---")
    st.header("📊 Lịch sử & tiến trình")
    colh1, colh2 = st.columns([2,1])
    with colh1:
        with st.expander("Lịch sử cảm xúc (mới nhất 50)"):
            moods = data["game"].get("moods", [])
            if moods:
                for m in reversed(moods[-50:]):
                    dt = datetime.fromisoformat(m["date"]).strftime("%Y-%m-%d %H:%M")
                    st.write(f"{dt} — {mood_emoji(m['mood'])} ({m['mood']}) — {m.get('note','')}")
            else:
                st.caption("Chưa có check-in nào.")
        with st.expander("Nhiệm vụ đã hoàn thành"):
            qs = list(data["game"].get("quests", {}).values())
            if qs:
                qs_sorted = sorted(qs, key=lambda x: x.get("completed_at",""), reverse=True)
                for item in qs_sorted[:100]:
                    ts = datetime.fromisoformat(item["completed_at"]).strftime("%Y-%m-%d %H:%M")
                    st.write(f"✅ {item['title']} — {ts}  (+{item.get('points',0)} điểm)")
            else:
                st.caption("Chưa hoàn thành nhiệm vụ nào.")
    with colh2:
        st.subheader("Thống kê nhanh")
        st.write(f"- Tổng nhiệm vụ hoàn thành: {len(data['game'].get('quests', {}))}")
        qcounts = data["game"].get("quest_counts", {})
        if qcounts:
            for k,v in qcounts.items():
                st.write(f"  - {k}: {v}")
        else:
            st.write("Chưa có nhiệm vụ theo loại nào.")

    st.markdown("---")
    st.caption("⚠️ Lưu ý: Ứng dụng không thay thế chuyên gia sức khỏe tinh thần. Nếu bạn gặp khủng hoảng, hãy tìm sự hỗ trợ y tế/chuyên gia ngay.")

if __name__ == "__main__":
    main()
