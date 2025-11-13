from pathlib import Path
from datetime import datetime, timedelta, date
import random
import json
import uuid
from typing import Optional
import re
import time as _t
import json as _json
import uuid as _uuid
import base64
import html as _html
import time

import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh


# ====== Third-party for Mongo Auth ======
try:
    import bcrypt
except Exception:
    bcrypt = None

# ====== App config ======
APP_TITLE = "🌱 Healingizz 2.1.0"
APP_TAGLINE = "Hỗ trợ cân bằng tâm lý học sinh"
DATA_DIR = Path("healing_data"); DATA_DIR.mkdir(exist_ok=True)

# ---------------- UI Lock helpers ----------------
def _lock_ui(on: bool = True):
    st.session_state["_ui_locked"] = bool(on)

def is_ui_locked() -> bool:
    return bool(st.session_state.get("_ui_locked", False))

def _sync_ui_lock_with_timers():
    """Giữ khóa UI đúng trạng thái nếu có bất kỳ timer nào đang chạy."""
    running = any(
        (k.endswith("_state") and v == "running" and (k.startswith("br_") or k.startswith("tm_")))
        for k, v in st.session_state.items()
    )
    _lock_ui(running)

# ---------------- Local storage (always-on) ----------------
def user_file(local_key: str) -> Path:
    return DATA_DIR / f"{local_key}.json"

def init_user_state(local_key: str, nickname_hint: str = ""):
    return {
        "user_id": local_key,
        "created_at": datetime.utcnow().isoformat(),
        "profile": {"nickname": nickname_hint or local_key.replace("user-",""), "bio": ""},
        "game": {
            "streak": 0,
            "last_checkin_date": None,
            "badges": [],
            "quests": {},
            "moods": [],
            "journal": [],
            "reminders": [],
            "quest_counts": {},
            "garden": [],
        }
    }

def _save_local(data: dict):
    f = user_file(data["user_id"])
    f.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

def _load_local(local_key: str, nickname_hint: str = ""):
    f = user_file(local_key)
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            backup = DATA_DIR / f"{local_key}.backup.json"
            try:
                backup.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
            data = init_user_state(local_key, nickname_hint)
            _save_local(data); return data
    data = init_user_state(local_key, nickname_hint)
    _save_local(data); return data

# =====================================================
# 🧠 MongoDB Cloud Integration (Atlas)
# =====================================================
from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

@st.cache_resource(show_spinner=False)
def get_mongo_client() -> MongoClient:
    """Tạo client MongoDB Atlas từ [mongo] trong .streamlit/secrets.toml"""
    import certifi
    mongo = st.secrets["mongo"]  # 🔹 Lấy section [mongo]
    uri = mongo["uri"]

    try:
        client = MongoClient(
            uri,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=12000,
            connectTimeoutMS=12000,
            socketTimeoutMS=12000,
            retryWrites=True,
            retryReads=True,
            appname="healingizz",
        )
        client.admin.command("ping")  # test ping
        return client
    except Exception as e:
        st.error(
            "Không kết nối được MongoDB Atlas.\n\n"
            f"Chi tiết: {e}\n\n"
            "Gợi ý: kiểm tra URI, mở IP whitelist hoặc cập nhật certifi/pymongo."
        )
        raise
    
def _mongo_col_data():
    client = get_mongo_client()
    mongo = st.secrets["mongo"]
    col = client[mongo.get("db", "healingizz")][mongo.get("col", "healing_users")]
    col.create_index([("user_id", ASCENDING)], unique=True, background=True)
    return col

def _mongo_col_auth():
    """Collection lưu tài khoản username/password (tùy chọn)"""
    client = get_mongo_client()
    dbname = st.secrets.get("mongo_db", "healingizz")
    colname = st.secrets.get("mongo_auth_col", "users_auth")
    col = client[dbname][colname]
    try:
        col.create_index([("username", ASCENDING)], unique=True, background=True)
    except Exception:
        pass
    return col

# --------- Cloud CRUD for user data ----------
def _cloud_upsert_mongo(user_id: str, data: dict):
    try:
        col = _mongo_col_data()
        col.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "data": data,
                "updated_at": datetime.utcnow().isoformat()
            }},
            upsert=True
        )
    except PyMongoError as e:
        st.warning(f"⚠️ Không lưu được lên cloud Mongo: {e}")

def _cloud_load_mongo(user_id: str) -> Optional[dict]:
    try:
        col = _mongo_col_data()
        doc = col.find_one({"user_id": user_id}, {"_id": 0})
        return (doc or {}).get("data")
    except PyMongoError as e:
        st.warning(f"⚠️ Không tải được từ cloud Mongo: {e}")
        return None

# --------- Auth on Mongo (username/password) ----------
def _username_exists_mongo(username: str) -> bool:
    try:
        col = _mongo_col_auth()
        doc = col.find_one({"username": username}, {"_id": 1})
        return bool(doc)
    except Exception:
        return False

def _create_user_mongo(username: str, password: str):
    if bcrypt is None:
        raise RuntimeError("Thiếu thư viện bcrypt. Hãy `pip install bcrypt` để dùng đăng ký/đăng nhập.")
    if len(username.strip()) < 3:
        raise RuntimeError("Tên người dùng tối thiểu 3 ký tự.")
    if len(password) < 6:
        raise RuntimeError("Mật khẩu tối thiểu 6 ký tự.")
    if _username_exists_mongo(username.strip()):
        raise RuntimeError("Tên người dùng đã tồn tại, vui lòng chọn tên khác.")
    col = _mongo_col_auth()
    pass_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    res = col.insert_one({
        "username": username.strip(),
        "pass_hash": pass_hash,
        "created_at": datetime.utcnow().isoformat()
    })
    # user_id = string of inserted id
    return str(res.inserted_id)

def _login_user_mongo(username: str, password: str):
    if bcrypt is None:
        return None, "Thiếu thư viện bcrypt. Hãy `pip install bcrypt`."
    col = _mongo_col_auth()
    row = col.find_one({"username": username.strip()})
    if not row:
        return None, "Sai username hoặc password."
    ok = bcrypt.checkpw(password.encode("utf-8"), row["pass_hash"].encode("utf-8"))
    if not ok:
        return None, "Sai username hoặc password."
    return str(row["_id"]), None  # dùng _id làm auth_user_id

# --------- High-level user state load/save ----------
def load_user_cloud_or_local(auth_user_id: str, nickname_hint: str = "") -> dict:
    """
    Có auth_user_id → ưu tiên Mongo; nếu chưa có → dùng local & sync lên.
    """
    if auth_user_id:
        cloud_data = _cloud_load_mongo(auth_user_id)
        if cloud_data:
            if nickname_hint and not cloud_data.get("profile", {}).get("nickname"):
                cloud_data.setdefault("profile", {})["nickname"] = nickname_hint
            return cloud_data
        # Không có trên cloud → lấy local rồi đẩy lên
        local_key = (f"user-{nickname_hint.strip().lower().replace(' ', '_')}"
                     if nickname_hint else f"user-local-{auth_user_id}")
        local_data = _load_local(local_key, nickname_hint)
        local_data["user_id"] = auth_user_id
        _cloud_upsert_mongo(auth_user_id, local_data)
        return local_data
    else:
        local_key = (f"user-{nickname_hint.strip().lower().replace(' ', '_')}"
                     if nickname_hint else "user-local")
        return _load_local(local_key, nickname_hint)

def save_user(data: dict):
    """
    Lưu song song:
    - Local JSON (luôn)
    - Cloud Mongo (nếu có auth_user_id)
    """
    nickname = data.get("profile", {}).get("nickname") or "local"
    local_key = f"user-{nickname.strip().lower().replace(' ', '_')}"
    _save_local({**data, "user_id": local_key})

    auth_user_id = st.session_state.get("auth_user_id")
    if auth_user_id:
        try:
            _cloud_upsert_mongo(auth_user_id, {**data, "user_id": auth_user_id})
        except Exception as e:
            st.warning(f"⚠️ Lưu cloud chậm, đã lưu local: {e}")

# ====== UI: Login header (center) ======
def show_login_header():
    st.markdown("""
    <style>
    [data-testid="stHeader"] {display: none;}
    footer {visibility: hidden;}
    .block-container {padding-top: 0 !important;}
    .center-header { text-align:center; margin-top:40px; margin-bottom:30px; }
    .center-header h1 { font-size:36px; font-weight:800; color:#2C3E2B; margin-bottom:6px; }
    .center-header p  { font-size:15px; color:#2C3E2B; margin:0; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown(f"""
    <div class="center-header">
        <h1>🌱 Healingizz <span style="font-weight:400; color:##2C3E2B;">2.1.0</span></h1>
        <p>{APP_TAGLINE}</p>
    </div>
    """, unsafe_allow_html=True)

def auth_block():
    left, center, right = st.columns([1, 0.8, 1])
    with center:
        with st.container(border=True):
            tabs = st.tabs(["**Đăng nhập**", "**Đăng ký**"])
            with tabs[0]:
                with st.form("login_form", clear_on_submit=False):
                    u1 = st.text_input("Tên người dùng", key="login_username", placeholder="Username")
                    p1 = st.text_input("Mật khẩu", key="login_password", type="password", placeholder="••••••••")
                    submit = st.form_submit_button("Đăng nhập")
                if submit:
                    if not u1 or not p1:
                        st.error("Nhập đầy đủ username và password.")
                    else:
                        auth_id, err = _login_user_mongo(u1.strip(), p1)
                        if err:
                            st.error(err)
                        else:
                            st.session_state["auth_user_id"] = auth_id
                            st.session_state["username"] = u1.strip()
                            st.session_state["nickname"] = u1.strip()
                            st.session_state["just_logged_in"] = True
                            st.rerun(); st.stop()
            with tabs[1]:
                with st.form("signup_form", clear_on_submit=False):
                    u2  = st.text_input("Username", key="signup_username", placeholder="New username")
                    p2  = st.text_input("Password", key="signup_password", type="password", placeholder="Tối thiểu 6 ký tự")
                    p2r = st.text_input("Confirm Password", key="signup_password2", type="password", placeholder="Nhập lại mật khẩu")
                    submit = st.form_submit_button("Tạo tài khoản")
                if submit:
                    if not u2 or not p2 or not p2r:
                        st.error("Vui lòng nhập đầy đủ thông tin.")
                    elif p2 != p2r:
                        st.error("Mật khẩu nhập lại không khớp.")
                    else:
                        try:
                            _create_user_mongo(u2.strip(), p2)
                            st.success("Tạo tài khoản thành công. Bạn có thể đăng nhập ngay.")
                        except Exception as e:
                            st.error(f"Tạo tài khoản thất bại: {e}")

# ====== Achievement Toasts (robust) ======
def _hz_now_ms():
    import time as _time
    return int(_time.time() * 1000)

def _hz_notifier_init():
    if "_hz_toasts" not in st.session_state:
        st.session_state["_hz_toasts"] = []
    # dọn item hết hạn
    now = _hz_now_ms()
    st.session_state["_hz_toasts"] = [
        t for t in st.session_state["_hz_toasts"]
        if now < t["start_ms"] + t["duration_ms"]
    ]

def notify_achievement(title: str,
                       subtitle: str = "Đã mở khóa!",
                       icon: str = "🏅",
                       delay_ms: int = 0):
    # Tổng 5s: 1s vào + 3s giữ + 1s ra
    duration_ms = 5000

    if "_hz_toasts" not in st.session_state:
        st.session_state["_hz_toasts"] = []
    now = _hz_now_ms()

    # Chống trùng trong 10s
    for t in st.session_state["_hz_toasts"]:
        if t["title"] == title and t["subtitle"] == subtitle and (now - t["start_ms"]) < 10000:
            return

    st.session_state["_hz_toasts"].append({
        "id": str(_uuid.uuid4()),
        "title": title,
        "subtitle": subtitle,
        "icon": icon,
        "start_ms": now + max(0, int(delay_ms)),
        "duration_ms": max(3000, int(duration_ms)),
    })

    payload = _json.dumps([st.session_state["_hz_toasts"][-1]], ensure_ascii=False)
    html = """
    <style>
      @keyframes hz_in { 0%{opacity:0; transform:translateX(24px)} 100%{opacity:1; transform:translateX(0)} }
      @keyframes hz_out{ 0%{opacity:1; transform:translateX(0)} 100%{opacity:0; transform:translateX(24px)} }
      .hz_wrap_fixed{
        position:fixed; top:18px; right:18px; z-index:2147483647;
        display:flex; flex-direction:column; gap:12px; pointer-events:none;
        font-family: 'Segoe UI', system-ui, -apple-system, 'Segoe UI Emoji', sans-serif;
      }
      .hz_card{
        min-width:320px; max-width:460px; pointer-events:auto;
        border-radius:14px; padding:12px 14px; display:flex; align-items:flex-start; gap:12px;
        background: linear-gradient(135deg, rgba(145,199,136,.98), rgba(129,183,121,.98)); /* theo theme */
        color:#2C3E2B;
        border:1px solid rgba(44,62,43,.15);
        box-shadow: 0 10px 28px rgba(0,0,0,.25), 0 0 0 2px rgba(255,255,255,.08) inset;
        opacity:0; transform:translateX(24px);
      }
      .hz_icon{ font-size:20px; line-height:1.1; filter:drop-shadow(0 0 4px rgba(255,255,255,.35)) }
      .hz_t   { display:flex; flex-direction:column; line-height:1.25 }
      .hz_t .ttl{ font-weight:800; font-size:15px }
      .hz_t .sub{ opacity:.95; font-size:13px; margin-top:2px }
    </style>
    <div id="hz_wrap_fixed"></div>
    <script>
      (function(){
        const data = __PAYLOAD__;
        let wrap = document.getElementById('hz_wrap_fixed');
        if(!wrap){
          wrap = document.createElement('div');
          wrap.id = 'hz_wrap_fixed';
          wrap.className = 'hz_wrap_fixed';
          document.body.appendChild(wrap);
        }
        function spawn(item){
          const el = document.createElement('div');
          el.className = 'hz_card';
          el.innerHTML =
            '<div class="hz_icon">'+item.icon+'</div>'+
            '<div class="hz_t"><div class="ttl">'+item.title+'</div>'+
            '<div class="sub">'+item.subtitle+'</div></div>';
          wrap.appendChild(el);

          // 1s in
          el.style.animation = 'hz_in 1000ms cubic-bezier(.2,.8,.2,1) forwards';

          // giữ 3s, sau đó 1s out
          const hold = 3000;
          setTimeout(function(){
            try{
              el.style.animation = 'hz_out 1000ms ease forwards';
              setTimeout(function(){ el.remove(); }, 1050);
            }catch(e){}
          }, 1000 + hold);
        }
        data.forEach(function(item){ setTimeout(function(){ spawn(item); }, Math.max(0, (item.start_ms || 0) - Date.now())); });
      })();
    </script>
    """
    components.html(html.replace("__PAYLOAD__", payload), height=0)

def render_notifier():
    """Chuẩn hóa lại thời gian còn lại của các toast (giữ đúng 5s)."""
    if "_hz_toasts" not in st.session_state:
        st.session_state["_hz_toasts"] = []
    now = _hz_now_ms()
    st.session_state["_hz_toasts"] = [
        t for t in st.session_state["_hz_toasts"]
        if now < t["start_ms"] + t["duration_ms"]
    ]
    # Chỉ cần 'anchor' rỗng để đảm bảo wrap hiện diện, còn spawn từng cái đã lo trong notify_achievement.
    components.html('<div id="hz_wrap_fixed"></div>', height=0)

# ====== Streak + badges ======
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

def progress_snapshot(data: dict) -> dict:
    g = data.get("game", {})
    streak = int(g.get("streak", 0))
    moods = g.get("moods", [])
    qcounts = g.get("quest_counts", {})
    garden = g.get("garden", [])
    journal = g.get("journal", [])
    breathing = int(qcounts.get("breathing", 0))
    gratitude = int(qcounts.get("gratitude", 0))
    mindful   = int(qcounts.get("mini_mindful", 0))
    plant_total = len(garden)
    rare_total  = sum(1 for p in garden if p.get("rare"))
    journal_total = len(journal)
    checkins = len(moods)
    return {
        "streak": streak, "breathing": breathing, "gratitude": gratitude, "mindful": mindful,
        "plant_total": plant_total, "rare_total": rare_total, "journal_total": journal_total,
        "checkins": checkins, "all_quests_done_today": False,
    }

BADGE_RULES = [
    ("streak_3",  "3 ngày liên tục",       lambda p: p["streak"] >= 3,           "🏅", "Giữ nhịp thật đều!"),
    ("streak_7",  "7 ngày liên tục",       lambda p: p["streak"] >= 7,           "🏅", "Một tuần kiên trì!"),
    ("checkin_1", "Check-in lần đầu",      lambda p: p["checkins"] >= 1,         "🏅", "Ghi nhận bước đầu tiên"),
    ("plant_1",   "Hạt mầm đầu tiên",      lambda p: p["plant_total"] >= 1,      "🏅", "Gieo hạt đầu tiên"),
    ("quests_all","Hoàn tất hôm nay",      lambda p: p["all_quests_done_today"], "🏅", "Xong toàn bộ hoạt động hôm nay"),
]

def check_badges(data: dict, *, set_all_done_today: bool = False):
    def _clean_title(s: str) -> str:
        return re.sub(r'^\W+\s*', '', s or "").strip()
    p = progress_snapshot(data)
    if set_all_done_today:
        p["all_quests_done_today"] = True
    owned_titles = set(_clean_title(t) for t in data["game"].get("badges", []))
    newly = []
    for bid, title, cond, icon, sub in BADGE_RULES:
        try:
            ok = bool(cond(p))
        except Exception:
            ok = False
        if not ok: continue
        t_clean = _clean_title(title)
        if t_clean in owned_titles: continue
        newly.append((t_clean, icon, sub))
    if newly:
        data["game"].setdefault("badges", []).extend([t for (t,_,_) in newly])
        save_user(data)
        for i, (title, _icon, sub) in enumerate(newly):
            notify_achievement(title=title, subtitle=sub, icon="🏅", delay_ms=i*350)
    return True

# ====== Quests ======
QUEST_TEMPLATES = [
    {"type": "breathing","title": "Thở 4-7-8","desc": "Thở vào 4s – nín 7s – thở ra 8s. Lặp lại trong hai vòng.","duration_sec": 60},
    {"type": "gratitude","title": "Điều ý nghĩa hôm nay","desc": "Viết 1 điều mà bạn cảm thấy có ý nghĩa trong ngày hôm nay"},
    {"type": "mini_mindful","title": "Mắt nhắm thư giãn","desc": "Mắt nhắm, nghe nhạc và chú ý cảm giác trong 30 giây","duration_sec": 30},
]

def todays_seed(user_id: str):
    base = f"{user_id}-{date.today().isoformat()}"
    return abs(hash(base)) % (2**32)

def daily_quests(user_id: str, k=3):
    rnd = random.Random(todays_seed(user_id))
    picks = rnd.sample(QUEST_TEMPLATES, k=min(k, len(QUEST_TEMPLATES)))
    result = []
    for q in picks:
        qid = f"{q['type']}-{date.today().isoformat()}"
        result.append({**q, "quest_id": qid})
    return result

def mark_quest_completed(data: dict, quest: dict, payload: dict) -> bool:
    qid = quest["quest_id"]
    if qid in data["game"]["quests"]:
        st.info("Bạn đã hoàn thành các hoạt động hôm nay ✔️")
        return False
    now = datetime.utcnow().isoformat()
    data["game"]["quests"][qid] = {
        "quest_id": qid,
        "type": quest["type"],
        "title": quest["title"],
        "completed_at": now,
        "payload": payload
    }
    tc = data["game"].setdefault("quest_counts", {})
    qt = quest["type"]; tc[qt] = tc.get(qt, 0) + 1
    save_user(data)
    check_badges(data)
    st.success(f"Hoàn thành: {quest['title']} 🎉")
    return True

def is_quest_done(data: dict, quest_id: str) -> bool:
    return quest_id in data["game"]["quests"]

# ====== Timers (stateful + lock UI) ======
def _start_timer_state(qid: str, total_sec: int, prefix: str):
    st.session_state[f"{prefix}_{qid}_state"] = "running"
    now = _t.time()
    st.session_state[f"{prefix}_{qid}_start_ts"] = now
    st.session_state[f"{prefix}_{qid}_end_ts"]   = now + total_sec

def _stop_timer_state(qid: str, prefix: str):
    for k in [f"{prefix}_{qid}_state", f"{prefix}_{qid}_start_ts", f"{prefix}_{qid}_end_ts"]:
        if k in st.session_state: del st.session_state[k]

def _get_timer_left(qid: str, prefix: str) -> int:
    end_ts = st.session_state.get(f"{prefix}_{qid}_end_ts")
    if not end_ts: return 0
    return max(0, int(round(end_ts - _t.time())))

def breathing_478_stateful(qid: str, rounds: int = 2):
    """
    Thở 4-7-8 kiểu cũ: vòng lặp time.sleep() cập nhật UI.
    - Start: set state 'running', khóa UI, rerun.
    - Running: hiển thị đếm ngược từng pha; kết thúc → set 'done', mở khóa, rerun.
    - Stop: về 'idle', mở khóa, rerun.
    """
    key_state = f"br_{qid}_state"
    state = st.session_state.get(key_state, "idle")

    phases = [("Hít vào", 4), ("Nín thở", 7), ("Thở ra", 8)]

    c1, _ = st.columns([3, 7])

    # --- Idle
    if state == "idle":
        if c1.button("Bắt đầu thực hiện", key=f"{qid}_start", disabled=is_ui_locked()):
            st.session_state[key_state] = "running"
            _lock_ui(True)
            st.rerun()
        return

    # --- Running
    if state == "running":
        if c1.button("Dừng thực hiện", key=f"{qid}_stop_btn"):
            st.session_state[key_state] = "idle"
            st.session_state.pop("active_quest_id", None)
            _lock_ui(False)
            st.rerun()   # 👈 thêm dòng này để rerender ngay nút "Bắt đầu thực hiện"
            return

        round_info = st.empty()
        status = st.empty()

        for r in range(1, rounds + 1):
            round_info.markdown(f"Vòng {r}/{rounds}")
            for label, sec in phases:
                # nếu user vừa nhấn Stop rồi rerun thì state sẽ khác; nhưng trong 1 run thì không thể stop giữa chừng – giữ nguyên hành vi cũ
                for s in range(sec, 0, -1):
                    status.markdown(f"### {label} {s}s")
                    time.sleep(1)

        status.empty()
        round_info.empty()

        # Kết thúc bài tập: set 'done' để main chấm điểm, mở khóa và rerun
        st.session_state[key_state] = "done"
        _lock_ui(False)
        st.rerun()
        return

    # --- Done
    if state == "done":
        st.success("✅ Hoàn thành thở 4-7-8 🎉")
        _lock_ui(False)
        return

# ====== Mindful 30s audio ======
AUDIO_ASSET_DIR = Path("assets")
MINDFUL_30S_FILE = AUDIO_ASSET_DIR / "mindful_30s.MP3"

@st.cache_data(show_spinner=False)
def _load_audio_base64(path: Path) -> str | None:
    try:
        if path.exists():
            import base64
            return "data:audio/mpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        pass
    return None

def mindful_30s_with_music(qid: str, total_sec: int = 30):
    """
    Bản mindful 30s có nhạc, dùng vòng lặp sleep để giữ DOM ổn định
    → audio không bị restart vì rerun.
    UI bị khóa trong 30s (giống behavior hiện tại).
    """
    key_state = f"tm_{qid}_state"
    state = st.session_state.get(key_state, "idle")

    c1, _ = st.columns([3,7])
    status = st.empty()
    note = st.empty()

    if state == "idle":
        start_disabled = is_ui_locked() and st.session_state.get("active_quest_id") not in (None, qid)
        if c1.button("Bắt đầu thực hiện", key=f"{qid}_start_btn", disabled=start_disabled):
            _lock_ui(True)
            st.session_state["active_quest_id"] = qid
            st.session_state[key_state] = "running"
            st.session_state[f"tm_{qid}_started_at"] = time.time()
            st.session_state[f"tm_{qid}_target_sec"] = int(total_sec)
            st.rerun()
        return

    if state == "running":
        if c1.button("Dừng thực hiện", key=f"{qid}_stop_btn"):
            st.session_state[key_state] = "idle"
            st.session_state.pop("active_quest_id", None)
            _lock_ui(False)
            st.rerun()
            return
        
        # Render audio đúng 1 lần, không autoplay lại
        audio_b64 = _load_audio_base64(MINDFUL_30S_FILE)
        if audio_b64:
            # autoplay vì user vừa bấm "Bắt đầu", thường được phép
            components.html(f"""
                <audio id="mindful_{qid}" autoplay>
                    <source src="{audio_b64}" type="audio/mpeg">
                </audio>
                <script>
                    try {{
                      const a = document.getElementById("mindful_{qid}");
                      if (a) {{
                        a.volume = 0.7;   // chỉnh âm lượng
                        a.play().catch(()=>{{}});
                      }}
                    }} catch(e) {{}}
                </script>
            """, height=0)
        else:
            st.info("Không tìm thấy assets/mindful_30s.mp3 – vẫn tiếp tục đếm 30 giây.")

        # Đếm ngược ngay trong một vòng lặp (không autorefresh)
        target = int(st.session_state.get(f"tm_{qid}_target_sec", total_sec))
        for sec in range(target, 0, -1):
            status.markdown(f"Thời gian còn lại: **{sec} giây**")
            # note.caption("Nhắm mắt, chú ý cảm giác…")
            time.sleep(1)

        # Kết thúc
        status.empty(); note.empty()
        st.session_state[key_state] = "done"
        st.session_state.pop("active_quest_id", None)
        _lock_ui(False)
        st.rerun()
        return

    if state == "done":
        st.success("✅ Hoàn thành 🎉")
        return

# ====== Garden (2 loại cây: 98%/2%) ======
MAX_TREES_PER_DAY = 5
TREE_ASSET_DIR = Path("assets")
PROB_RARE = 0.10
NORMAL_FILES = ["tree_normal.png", "tree1.png"]
RARE_FILES   = ["tree_rare.png", "tree6.png"]

TREE_MEANINGS = {
    "binh_thuong": "Điều tốt đẹp đang lớn lên.",
    "hiem":        "Duyên lành hiếm có – ánh sáng lan tỏa.",
}
def _rarity_label_vi(rarity: str) -> str:
    return {"binh_thuong":"Bình thường","hiem":"Hiếm"}.get(rarity,"Bình thường")

@st.cache_data(show_spinner=False)
def _cache_first_existing(files: tuple[str, ...]) -> tuple[Optional[str], Optional[str]]:
    for name in files:
        f = TREE_ASSET_DIR / name
        if f.exists():
            return "data:image/png;base64," + base64.b64encode(f.read_bytes()).decode("ascii"), name
    return None, None

def pick_random_tree_asset() -> tuple[Optional[str], str, Optional[str]]:
    rarity = "hiem" if random.random() < PROB_RARE else "binh_thuong"
    if rarity == "hiem":
        img64, fname = _cache_first_existing(tuple(RARE_FILES))
    else:
        img64, fname = _cache_first_existing(tuple(NORMAL_FILES))
    return img64, rarity, fname

def _load_tree_asset_base64() -> Optional[str]:
    img64, _ = _cache_first_existing(tuple(NORMAL_FILES + RARE_FILES))
    return img64

def _date_key_from_iso(iso: str) -> str:
    try: return datetime.fromisoformat(iso).date().isoformat()
    except Exception: return datetime.utcnow().date().isoformat()

def _group_garden_by_day(garden: list[dict]) -> dict[str, list[dict]]:
    days = {}
    for p in garden or []:
        k = _date_key_from_iso(p.get("date",""))
        days.setdefault(k, []).append(p)
    for k, arr in days.items():
        days[k] = sorted(arr, key=lambda x: x.get("date",""))
    return days

def _get_all_days_sorted(garden: list[dict]) -> list[str]:
    days = sorted({_date_key_from_iso(p.get("date","")) for p in (garden or [])})
    today_key = datetime.utcnow().date().isoformat()
    if today_key not in days: days.append(today_key)
    return sorted(days)

def _get_current_day_for_ui(key="garden_day_page") -> str:
    cur = st.session_state.get(key)
    if not cur:
        cur = datetime.utcnow().date().isoformat()
        st.session_state[key] = cur
    return cur

def _goto_day(day_iso: str, key="garden_day_page"):
    st.session_state[key] = day_iso
    # Button click tự rerun rồi, không cần gọi st.rerun()

def render_garden_day_ui(data: dict, allow_planting: bool=True):
    garden = data["game"].get("garden", [])
    days_sorted = _get_all_days_sorted(garden)
    grouped = _group_garden_by_day(garden)

    cur_day = _get_current_day_for_ui()
    if cur_day not in days_sorted:
        days_sorted.append(cur_day); days_sorted = sorted(days_sorted)

    idx = days_sorted.index(cur_day)
    has_prev = idx > 0; has_next = idx < len(days_sorted) - 1

    # Nav
    col_left, col_mid, col_right = st.columns([1,2.5,1], gap="small")
    with col_left:
        if st.button("◀ Ngày trước", disabled=not has_prev, key="garden_prev"):
            if has_prev: _goto_day(days_sorted[idx-1]); st.rerun()
    with col_mid:
        st.markdown(f"<div style='text-align:center;font-weight:800;font-size:18px;'>Ngày {cur_day}</div>", unsafe_allow_html=True)
    with col_right:
        r1, r2 = st.columns([1,1])
        with r2:
            if st.button("Ngày sau ▶", disabled=not has_next, key="garden_next"):
                if has_next: _goto_day(days_sorted[idx+1]); st.rerun()

    todays_plants = list(grouped.get(cur_day, []))
    left_slots = max(0, MAX_TREES_PER_DAY - len(todays_plants))

    # CSS grid
    st.markdown("""
    <style>
    .day-grid-fixed{ display:grid; grid-template-columns:repeat(5,1fr); gap:16px; margin-top:12px; }
    .slot{ position:relative; background:rgba(255,255,255,.05); border:1px solid rgba(255,255,255,.08);
           border-radius:14px; padding:14px; text-align:center; min-height:160px;
           display:flex; flex-direction:column; align-items:center; justify-content:center;
           transition:transform .25s ease, box-shadow .25s ease, border-color .25s ease;}
    .slot:hover{ transform: translateY(-3px); }
    .slot img{ max-width:84px; height:auto }
    .slot .cap{ font-size:13px; opacity:.9; margin-top:8px; line-height:1.3; max-width:100%;
                white-space:nowrap; overflow:hidden; text-overflow:ellipsis; font-style:italic; }
    .slot-empty{ opacity:.5; font-style:italic }
    .slot.rare{ border-color:#FFD54A; box-shadow:0 0 14px rgba(255,213,74,.45), inset 0 0 2px rgba(255,213,74,.85); }
    .slot.sparkle{ animation: glowPulse 1.2s ease-in-out infinite alternate; }
    @keyframes glowPulse{ 0%{box-shadow:0 0 12px rgba(255,215,64,.35)} 100%{box-shadow:0 0 22px rgba(255,215,64,.7)} }
    .slot.sparkle::before{ content:""; position:absolute; inset:-3px; border-radius:14px; pointer-events:none;
      background: radial-gradient(circle, rgba(255,255,255,0.95) 0 22%, transparent 24%) 0 0/8px 8px repeat,
                  radial-gradient(circle, rgba(255,255,255,0.6) 0 18%, transparent 20%) 4px 4px/10px 10px repeat;
      opacity:.35; filter:blur(.6px); animation: glitterMove 1.2s linear infinite; }
    @keyframes glitterMove{ 0%{background-position:0 0,4px 4px} 100%{background-position:100px 60px,104px 64px} }
    .slot[data-tip]:hover::after{
      content: attr(data-tip);
      position:absolute; bottom:100%; left:50%; transform:translate(-50%,-10px);
      background:rgba(20,30,25,.95); color:#eaf4ee; border:1px solid rgba(255,255,255,.12);
      box-shadow:0 6px 16px rgba(0,0,0,.35); padding:10px 12px; border-radius:10px;
      width:max-content; max-width:260px; text-align:left; font-size:13px; line-height:1.35;
      opacity:1; z-index:9999; white-space: pre-line;
    }
    .slot[data-tip]::after{ opacity:0; transition:opacity .15s ease, transform .15s ease; }
    </style>
    """, unsafe_allow_html=True)

    # Render plants
    display_plants = todays_plants[:MAX_TREES_PER_DAY]
    left_slots = MAX_TREES_PER_DAY - len(display_plants)
    now_utc = datetime.utcnow()
    cards_html = []

    for p in display_plants:
        img64 = p.get("img") or _load_tree_asset_base64()
        rarity = p.get("rarity") or ("hiem" if p.get("rare") else "binh_thuong")
        cat_label = p.get("category_label") or _rarity_label_vi(rarity)
        meaning = p.get("meaning") or TREE_MEANINGS.get(rarity, "Điều tốt đẹp đang lớn lên.")
        safe_meaning = _html.escape(meaning, quote=True)
        aff_text = (p.get("affirmation","") or "").strip()
        cap_user = _html.escape(aff_text) if aff_text else " "

        is_new = False
        try:
            nu = p.get("new_until")
            if nu: is_new = datetime.fromisoformat(nu) > now_utc
        except Exception:
            pass

        classes = ["slot"]
        if rarity == "hiem": classes.append("rare")
        if is_new and rarity == "hiem": classes.append("sparkle")
        tip_attr = f"Thể loại: {cat_label}&#10;Ý nghĩa: “{safe_meaning}”"

        if img64:
            card = f'<div class="{" ".join(classes)}" data-tip="{tip_attr}">' \
                   f'<img src="{img64}" alt="tree"/><div class="cap">“{cap_user}”</div></div>'
        else:
            card = f'<div class="{" ".join(classes)}" data-tip="{tip_attr}">' \
                   f'<div style="font-size:48px">🌳</div><div class="cap">“{cap_user}”</div></div>'
        cards_html.append(card)

    for _ in range(left_slots):
        cards_html.append('<div class="slot slot-empty" data-tip="Chưa có cây ở ô này. Hãy gieo một điều tích cực nhé!">Ô đất trống</div>')

    st.markdown('<div class="day-grid-fixed">' + "".join(cards_html) + "</div>", unsafe_allow_html=True)

    # Plant form (only if today, free slots, allowed, not locked)
    is_today_page = (cur_day == datetime.utcnow().date().isoformat())
    if not is_today_page:
        st.info("Đây là ngày khác. Chỉ gieo ở **hôm nay**."); return
    if left_slots <= 0:
        st.success(f"Hôm nay đã đủ {MAX_TREES_PER_DAY} cây 🌿"); return
    if not allow_planting:
        st.info("🌱 Hãy hoàn tất **tất cả hoạt động hôm nay** trước khi gieo cây."); return
    if is_ui_locked():
        st.info("⏳ Đang thực hiện bài tập — gieo cây tạm khóa."); return

    aff = st.text_input("Điều tích cực để gieo hôm nay", key="affirm_today_v2",
                        placeholder="Gieo điều tích cực, Cơ hội 10% gặp cây hiếm")
    # st.info("Cơ hội 10% xuất hiện cây hiếm khi gieo.")
    if st.button("Gieo cây 🌱", key="plant_today_btn"):
        if not aff.strip():
            st.error("Hãy viết một điều tích cực trước khi gieo.")
        else:
            img64, rarity, fname = pick_random_tree_asset()
            meaning = TREE_MEANINGS.get(rarity, "Điều tốt đẹp đang lớn lên.")
            plant = {
                "id": str(uuid.uuid4()),
                "date": datetime.utcnow().isoformat(),
                "rarity": rarity,
                "category_label": _rarity_label_vi(rarity),
                "meaning": meaning,
                "affirmation": aff.strip(),
                "img": img64,
                "tree_file": fname,
                "new_until": (datetime.utcnow() + timedelta(seconds=8)).isoformat(),
            }
            data["game"].setdefault("garden", []).append(plant)
            save_user(data)
            st.rerun()

# ====== Sidebar ======
st.markdown("""
<style>
.logout-wrap .stButton > button{
  background:#ef4444 !important; border-color:#ef4444 !important; color:white !important;
  font-weight:700; width:100%;
}
.logout-wrap .stButton > button:hover{ filter: brightness(0.95); }
</style>
""", unsafe_allow_html=True)

def ui_sidebar(data: dict):
    st.sidebar.title("👤 Hồ sơ")
    nickname = st.sidebar.text_input("Nickname", value=data["profile"].get("nickname",""), disabled=is_ui_locked())
    bio = st.sidebar.text_area("Giới thiệu ngắn", value=data["profile"].get("bio",""), help="Tùy chọn", disabled=is_ui_locked())
    if (not is_ui_locked()) and (nickname != data["profile"].get("nickname","") or bio != data["profile"].get("bio","")):
        data["profile"]["nickname"] = nickname
        data["profile"]["bio"] = bio
        save_user(data)

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Huy hiệu**")

    st.sidebar.markdown("""
        <style>
          .badge-list { list-style:none; margin:0; padding:0; }
          .badge-list li { margin: 6px 0; white-space: nowrap; display: flex; align-items: center; gap: .5rem; }
          .badge-list .medal { filter: drop-shadow(0 0 4px rgba(255,255,255,.12)); }
        </style>
    """, unsafe_allow_html=True)

    badges = data["game"].get("badges", [])
    if badges:
        html = ['<ul class="badge-list">']
        for b in badges:
            try:
                display = re.sub(r'^\W+\s*', '', str(b)).strip()
            except Exception:
                display = str(b)
            html.append(f'<li><span class="medal">🏅</span> {display}</li>')
        html.append('</ul>')
        st.sidebar.markdown("\n".join(html), unsafe_allow_html=True)
    else:
        st.sidebar.write("Chưa có huy hiệu nào.")

    # Nút đồng bộ cloud theo yêu cầu (không auto fetch mỗi rerun)
    # if st.session_state.get("auth_user_id"):
    #     if st.sidebar.button("↻ Đồng bộ lại từ cloud (Mongo)", disabled=is_ui_locked()):
    #         fresh = _cloud_load_mongo(st.session_state.get("auth_user_id"))
    #         if fresh:
    #             st.session_state["user_data"] = fresh
    #             save_user(st.session_state["user_data"])
    #             st.success("Đã tải lại dữ liệu.")
    #             st.rerun()
    #         else:
    #             st.info("Không có dữ liệu mới trên cloud.")

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Tài khoản**")
    st.sidebar.markdown('<div class="logout-wrap">', unsafe_allow_html=True)
    if st.sidebar.button("Đăng xuất", key="logout_sidebar", disabled=is_ui_locked()):
        for k in ["auth_user_id","username","nickname","finished_today","active_quest_id","user_data"]:
            if k in st.session_state: del st.session_state[k]
        st.success("Đã đăng xuất."); st.rerun()
    st.sidebar.markdown('</div>', unsafe_allow_html=True)

# ====== Misc ======
def mood_emoji(score: int):
    if score <= 2: return "😢"
    if score <= 4: return "😟"
    if score <= 6: return "😐"
    if score <= 8: return "🙂"
    return "🤩"

QUOTES = [
    "Hôm nay dù nhỏ, bạn vẫn tiến một bước rồi đó.",
    "Bạn xứng đáng với cảm giác bình yên, không cần phải cố.",
    "Mọi chuyện không hoàn hảo cũng được, chỉ cần thật lòng.",
    "Bạn đã làm tốt trong khả năng của mình rồi.",
    "Không cần giỏi hơn ai, chỉ cần hơn chính mình hôm qua.",
    "Cứ kiên nhẫn, những điều đẹp sẽ đến vào lúc cần đến.",
    "Một ngày yên ả cũng là một ngày đáng trân trọng.",
    "Mỗi lần bạn chọn bình tĩnh, là bạn đang mạnh mẽ hơn.",
    "Thật tốt khi bạn vẫn ở đây, tiếp tục cố gắng.",
]

def export_journal_to_txt(data: dict):
    lines = []
    for entry in data["game"].get("journal", []):
        lines.append(f"=== {entry.get('date','')} — {entry.get('title','(No title)')} ===")
        lines.append(entry.get("content","")); lines.append("\n")
    return "\n".join(lines)

# ====== Main ======
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="🌱", layout="wide", initial_sidebar_state="expanded")
    _sync_ui_lock_with_timers()
    render_notifier()

    if st.session_state.pop("just_logged_in", False):
        st.markdown("""
        <style>
        [data-testid="stSidebar"] { z-index: 0 !important; }
        #healing-loader { z-index: 2147483647 !important; }
        </style>

        <div id="healing-loader"> ... </div>

        <style>
        #healing-loader {
            position: fixed;
            inset: 0;
            background: #E2F1E1;  /* nền xanh cốm nhạt */
            z-index: 9999;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            font-family: 'Segoe UI', sans-serif;
            color: #2C3E2B;       /* chữ xanh lá đậm tự nhiên */
            text-align: center;
            opacity: 1;
            animation: healFade 1s ease forwards;
            animation-delay: 1s;
            pointer-events: all;
        }
        #healing-loader h1 {
            font-size: 1.8rem;
            font-weight: 700;
            margin-bottom: 0.75rem;
        }
        .spinner {
            border: 4px solid rgba(44,62,43,0.2); /* viền mờ xanh đậm */
            border-top: 4px solid #91C788;        /* viền xoay xanh cốm */
            border-radius: 100%;
            width: 48px;
            height: 48px;
            animation: spin 1s linear infinite;
            margin-top: 1rem;
        }
        @keyframes spin {
            from { transform: rotate(0); }
            to { transform: rotate(360deg); }
        }
        @keyframes healFade {
            0% { opacity: 1; visibility: visible; }
            99% { opacity: 0; visibility: visible; }
            100% { opacity: 0; visibility: hidden; pointer-events: none; }
        }
        </style>

        <div id="healing-loader">
        <h1>🌿 Đang đăng nhập vào Healingizz</h1>
        <div class="spinner"></div>
        </div>
        """, unsafe_allow_html=True)

    # Gate
    if "auth_user_id" not in st.session_state and "username" not in st.session_state:
        show_login_header(); auth_block(); st.stop()

    # Title
    st.title(APP_TITLE); st.caption(APP_TAGLINE)

    auth_user_id = st.session_state.get("auth_user_id")
    nickname_hint = st.session_state.get("nickname", st.session_state.get("username", "guest"))

    # chỉ load user 1 lần/phiên — giảm lag
    with st.spinner("Đang tải dữ liệu người dùng..."):
        if "user_data" not in st.session_state:
            st.session_state["user_data"] = load_user_cloud_or_local(auth_user_id or "", nickname_hint)
        data = st.session_state["user_data"]

    if data["profile"].get("nickname","") != nickname_hint and nickname_hint:
        data["profile"]["nickname"] = nickname_hint; save_user(data)

    cloud_flag = "☁️" if auth_user_id else "💾"
    st.markdown(
        f"<div style='font-size:22px; font-weight:800;'>Xin chào, {data['profile'].get('nickname','bạn')}!"
        f" <span style='font-size:18px; font-weight:600;'>{cloud_flag}</span></div>",
        unsafe_allow_html=True
    )

    ui_sidebar(data)

    st.markdown("---")
    st.header("Góc chậm lại hôm nay")

    if "daily_quote" not in st.session_state:
        st.session_state["daily_quote"] = random.choice(QUOTES)

    st.markdown(
        f"""
        <div style='background: linear-gradient(120deg,#A8D5BA,#91C788);
            padding:2rem;border-radius:16px;text-align:center;
            font-size:1.25rem;font-style:italic;color:#2C3E2B;
            box-shadow:0 0 20px rgba(145,199,136,.3);'>
        💬 “{st.session_state["daily_quote"]}”
        </div>
        """,
        unsafe_allow_html=True
    )
    st.caption("Một lời nhắc nhỏ — chỉ cần hít sâu và mỉm cười, bạn đã đủ rồi.")

    # Check-in
    st.markdown("---")
    today = datetime.utcnow().date()
    done_today = any(datetime.fromisoformat(m["date"]).date() == today for m in data["game"].get("moods", []))
    ui_locked = is_ui_locked()
    mood = st.slider("Tâm trạng của bạn (1 rất tệ → 10 rất tốt):", 1, 10, 5, key="mood_slider", disabled=done_today or ui_locked)
    st.markdown(f"### Cảm xúc hiện tại: {mood_emoji(mood)} (điểm: {mood})")
    if done_today:
        st.button("Đã check-in hôm nay 🎉", disabled=True)
    else:
        if st.button("Lưu check-in ✅", disabled=ui_locked):
            data["game"].setdefault("moods", []).append({"date": datetime.utcnow().isoformat(), "mood": int(mood)})
            update_streak_on_checkin(data)
            check_badges(data)
            st.rerun()

    # Daily quests
    st.markdown("---")
    st.header("🎯 Hoạt động hôm nay")
    seed_id = (st.session_state.get("auth_user_id") or st.session_state.get("username") or "guest")
    quests = daily_quests(str(seed_id), k=3)

    active_q = st.session_state.get("active_quest_id")
    for q in quests:
        qid = q["quest_id"]
        doneQ = is_quest_done(data, qid)
        br_state = st.session_state.get(f"br_{qid}_state")
        tm_state = st.session_state.get(f"tm_{qid}_state")
        expanded_now = (
            st.session_state.get("active_quest_id") == qid
            or br_state in ("running", "done")
            or tm_state in ("running", "done")
            or (st.session_state.get("active_quest_id") is None and not doneQ)
        )

        with st.expander(f"{'✅' if doneQ else '🕹️'} {q['title']}", expanded=expanded_now):
            st.caption(q["desc"])

            if doneQ:
                st.success("Đã hoàn thành.")
                continue

            if q["type"] == "breathing":
                breathing_478_stateful(qid, rounds=2)
                if st.session_state.get(f"br_{qid}_state") == "done":
                    if mark_quest_completed(data, q, {"completed": True}):
                        st.rerun()

            elif q["type"] == "mini_mindful":
                mindful_30s_with_music(qid, total_sec=q.get("duration_sec", 30))
                if st.session_state.get(f"tm_{qid}_state") == "done":
                    if mark_quest_completed(data, q, {"completed": True}):
                        st.rerun()

            elif q["type"] == "gratitude":
                g = st.text_input("Điều ý nghĩa hôm nay", key=f"{qid}_g1", disabled=is_ui_locked())
                if st.button("Lưu & hoàn thành", key=f"{qid}_save", disabled=is_ui_locked()):
                    if g.strip():
                        if mark_quest_completed(data, q, {"gratitude": [g.strip()]}):
                            st.rerun()
                    else:
                        st.error("Hãy điền ít nhất 1 điều ý nghĩa hôm nay.")

    all_completed = all(is_quest_done(data, q["quest_id"]) for q in quests)
    if all_completed and quests and not st.session_state.get("finished_today", False):
        st.session_state["finished_today"] = True
        try:
            check_badges(data, set_all_done_today=True)
        except TypeError:
            badge = "Hoàn tất hôm nay"
            if badge not in data["game"].get("badges", []):
                data["game"].setdefault("badges", []).append(badge)
                save_user(data)

    # Garden
    st.markdown("---")
    st.header("🌻 Khu vườn tích cực của bạn")
    render_garden_day_ui(data, allow_planting=(all_completed and not is_ui_locked()))

    # Journal
    st.markdown("---")
    st.header("📔 Nhật ký")
    colj1, colj2 = st.columns([2,1])
    with colj1:
        with st.expander("Viết nhật ký mới"):
            jtitle = st.text_input("Tiêu đề", key="jtitle", disabled=is_ui_locked())
            jcontent = st.text_area("Nội dung", key="jcontent", height=200, disabled=is_ui_locked())
            if st.button("Lưu nhật ký", disabled=is_ui_locked()):
                if jcontent.strip():
                    data["game"].setdefault("journal", []).append({
                        "date": datetime.utcnow().isoformat(),
                        "title": jtitle.strip() if jtitle.strip() else "(No title)",
                        "content": jcontent.strip()
                    })
                    save_user(data)
                    check_badges(data)
                    st.success("Đã lưu nhật ký.")
                else:
                    st.error("Nhật ký trống.")
        with st.expander("Lịch sử nhật ký"):
            j = data["game"].get("journal", [])
            if j:
                for e in reversed(j[-50:]):
                    st.write(f"**{e.get('title','(No title)')}** — {datetime.fromisoformat(e['date']).strftime('%Y-%m-%d %H:%M')}")
                    st.write(e.get("content","")); st.markdown("---")
            else:
                st.caption("Chưa có nhật ký nào.")
    with colj2:
        txt = export_journal_to_txt(data)
        if txt:
            st.download_button("Tải nhật ký (.txt)", data=txt.encode("utf-8"),
                               file_name=f"{data['profile'].get('nickname','user')}_journal.txt",
                               mime="text/plain")
        else:
            st.caption("Chưa ghi nhận nhật ký nào")

    # History
    st.markdown("---")
    st.header("📊 Lịch sử & tiến trình")
    colh1, colh2 = st.columns([2,1])
    with colh1:
        with st.expander("Lịch sử cảm xúc (mới nhất 50)"):
            moods = data["game"].get("moods", [])
            if moods:
                for m in reversed(moods[-50:]):
                    dt = datetime.fromisoformat(m["date"]).strftime("%Y-%m-%d %H:%M")
                    st.write(f"{dt} — {mood_emoji(m['mood'])} ({m['mood']})")
            else:
                st.caption("Chưa có check-in nào.")
        with st.expander("Hoạt động đã hoàn thành"):
            qs = list(data["game"].get("quests", {}).values())
            if qs:
                qs_sorted = sorted(qs, key=lambda x: x.get("completed_at",""), reverse=True)
                for item in qs_sorted[:100]:
                    ts = datetime.fromisoformat(item["completed_at"]).strftime("%Y-%m-%d %H:%M")
                    st.write(f"✅ {item['title']} — {ts}")
            else:
                st.caption("Chưa hoàn thành hoạt động nào.")
    with colh2:
        st.subheader("Thống kê nhanh")
        st.write(f"- Streak hiện tại: {data['game'].get('streak',0)} ngày")
        if data["game"].get("badges"): st.write("- Huy hiệu:");
        for b in data["game"].get("badges", []):
            try:
                display = re.sub(r'^\W+\s*', '', str(b)).strip()
            except Exception:
                display = str(b)
            st.write(f"  • {display}")

    st.markdown("---")
    st.caption("LƯU Ý: Ứng dụng chỉ mang tính hỗ trợ. Không thay thế cho chẩn đoán hoặc điều trị chuyên môn.")

if __name__ == "__main__":
    if "active_quest_id" not in st.session_state: st.session_state["active_quest_id"] = None
    main()









