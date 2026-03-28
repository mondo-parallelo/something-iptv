"""
╔══════════════════════════════════════════════════════════════╗
║   CHUOICHIENTV SCRAPER v3 - Dựa trên script chạy được       ║
╚══════════════════════════════════════════════════════════════╝

Sửa từ v2:
  ✅ Đúng API endpoint: /v1/matches?page=1&limit=100&type=blv
  ✅ Thêm Bearer JWT token (bắt buộc, API trả 401 nếu thiếu)
  ✅ Dùng curl_cffi thay requests (giả lập Chrome thật, tránh block)
  ✅ Đúng cấu trúc JSON: data['matches'] thay vì data['data']
  ✅ Parse đúng: teams.home/away, blvs[0].streams
  ✅ Tạo thumbnail bằng Pillow (lồng logo 2 đội lên ảnh nền)
  ✅ Xuất file stream/{id}.json (child) + chuoichientv.json (master)
  ✅ Xuất all.m3u cho TV Box

Cài đặt:
  pip install curl_cffi python-dateutil Pillow requests
"""

import sys
import io as _io
sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = _io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import os
import json
import io
import re
from datetime import datetime, timezone, timedelta
from curl_cffi import requests
from dateutil import parser as dateparser
from PIL import Image

# ──────────────────────────────────────────────────────────────
# CẤU HÌNH
# ──────────────────────────────────────────────────────────────

# API đúng: lấy tất cả trận, giới hạn 100, loại blv (bình luận viên)
API_URL = "https://api.chuoichientv.com/v1/matches?page=1&limit=100&sport=&type=blv"

# Bearer token lấy từ DevTools → Network → bất kỳ request tới api.chuoichientv.com
# Token này có thể hết hạn, cần cập nhật lại nếu API trả 401
BEARER_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJndWVzdElkIjoiZTM0Zjk3ZmQtNWMxMC00MGEzLWE1OGYtZDE3MmQwMmIxNDZjIiwidHlwZSI6Imd1ZXN0IiwiaXAiOiIxNjIuMTU5Ljk4LjIyMCIsInVzZXJBZ2VudCI6Ik1vemlsbGEvNS4wIChXaW5kb3dzIE5UIDEwLjA7IFdpbjY0OyB4NjQpIEFwcGxlV2ViS2l0LzUzNy4zNiAoS0hUTUwsIGxpa2UgR2Vja28pIENocm9tZS8xMzEuMC4wLjAgU2FmYXJpLzUzNy4zNiIsIm5hbWUiOiJCw7puIMSQ4buPIDQ1MyIsInRpbWVzdGFtcCI6MTc3MjI5MTc4NzEwNCwiaWF0IjoxNzcyMjkxNzg3LCJleHAiOjE4MDM4Mjc3ODd9"
    ".iHhwdQaDRcrjyRfCVGCbSZb6dFj-EuzJblTD1wmttV0"
)

HEADERS = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
    "Origin":        "https://live18.chuoichientv.com",
    "Referer":       "https://live18.chuoichientv.com/",
    "User-Agent":    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

# GitHub raw URL prefix (đổi thành repo của bạn)
GITHUB_USER    = "mondo-parallelo"   # ← đổi thành username GitHub của bạn
GITHUB_REPO    = "something-iptv"         # ← đổi thành tên repo của bạn
GITHUB_BRANCH  = "main"
GITHUB_RAW     = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}"

BASE_STREAM_URL = f"{GITHUB_RAW}/stream/"
BASE_THUMB_URL  = f"{GITHUB_RAW}/thumbs/"

# URL ảnh nền thumbnail (dùng ảnh từ repo mẫu, hoặc đổi thành ảnh của bạn)
BG_IMAGE_URL = (
    "https://raw.githubusercontent.com/nghehoang007-wq/HFB321/main/HFB/Thump/nguonphat5.png"
)

# Output
OUTPUT_DIR  = "docs"
STREAM_DIR  = "stream"
THUMBS_DIR  = "thumbs"
OUT_JSON    = "chuoichientv.json"           # Master JSON (import vào app)
OUT_M3U     = os.path.join(OUTPUT_DIR, "all.m3u")
OUT_M3U_CCT = os.path.join(OUTPUT_DIR, "chuoichientv.m3u")

TZ_VN = timezone(timedelta(hours=7))

# Tạo thư mục
for d in [OUTPUT_DIR, STREAM_DIR, THUMBS_DIR]:
    os.makedirs(d, exist_ok=True)


# ──────────────────────────────────────────────────────────────
# HELPER: Rút ngắn tên đội (hiển thị thumbnail)
# ──────────────────────────────────────────────────────────────
TEAM_SHORTNAMES = {
    "Manchester United": "Man Utd",
    "Manchester City":   "Man City",
    "Barcelona":         "Barca",
    "Real Madrid":       "Real",
    "Villarreal":        "Villarr",
    "Atletico Madrid":   "Atletico",
    "Inter Milan":       "Inter",
    "AC Milan":          "Milan",
    "Juventus":          "Juve",
    "Bayern Munich":     "Bayern",
    "Borussia Dortmund": "Dortmund",
    "Paris Saint-Germain": "PSG",
    "Tottenham Hotspur": "Spurs",
    "Newcastle United":  "Newcastle",
    "West Ham United":   "West Ham",
    "Aston Villa":       "Villa",
    "Liverpool":         "Liverpool",
    "Chelsea":           "Chelsea",
    "Arsenal":           "Arsenal",
}

def shorten_name(name):
    name = str(name).strip()
    for full, short in TEAM_SHORTNAMES.items():
        if full.lower() in name.lower():
            return short
    return name[:12] + ".." if len(name) > 13 else name


# ──────────────────────────────────────────────────────────────
# BƯỚC 1: GỌI API LẤY DANH SÁCH TRẬN
# ──────────────────────────────────────────────────────────────
def fetch_matches():
    """
    Gọi API /matches với Bearer token và curl_cffi (giả lập Chrome).

    Response cấu trúc:
    {
      "matches": [
        {
          "_id": "abc123",
          "matchTime": "2025-03-01T20:00:00.000Z",
          "teams": {
            "home": {"name": "MU", "logo": "https://..."},
            "away": {"name": "Arsenal", "logo": "https://..."}
          },
          "tournament": {"name": "Premier League", "logo": "https://..."},
          "blvs": [
            {
              "name": "BLV Chuối",
              "streams": [
                {"url": "https://cdn.../index.m3u8", "label": "HD"}
              ]
            }
          ]
        },
        ...
      ],
      "total": 50
    }
    """
    print(f"\n[1/4] Goi API: {API_URL}")
    try:
        r = requests.get(
            API_URL,
            headers=HEADERS,
            impersonate="chrome110",   # curl_cffi giả lập Chrome 110 thật
            timeout=30
        )
        print(f"      HTTP Status: {r.status_code}")

        if r.status_code == 401:
            print("      [LOI 401] Token het han!")
            print("      -> Can lay token moi tu DevTools (xem huong dan ben duoi)")
            return []

        data = r.json()

        # Debug cấu trúc
        if isinstance(data, dict):
            print(f"      Keys: {list(data.keys())}")
            total = data.get('total', '?')
            print(f"      Total tu API: {total}")

        matches = data.get('matches', [])
        print(f"      Lay duoc: {len(matches)} tran")
        return matches

    except Exception as e:
        print(f"      [LOI] {e}")
        return []


# ──────────────────────────────────────────────────────────────
# BƯỚC 2: TẠO THUMBNAIL (LỒNG LOGO 2 ĐỘI VÀO ẢNH NỀN)
# ──────────────────────────────────────────────────────────────
def load_base_bg():
    """Tải ảnh nền 1 lần duy nhất."""
    try:
        res = requests.get(BG_IMAGE_URL, timeout=10, impersonate="chrome110")
        bg = Image.open(io.BytesIO(res.content)).convert("RGBA")
        bg = bg.resize((640, 360))
        print(f"      [OK] Tai anh nen thanh cong: 640x360")
        return bg
    except Exception as e:
        print(f"      [!] Khong tai duoc anh nen, dung nen den: {e}")
        return Image.new('RGBA', (640, 360), (20, 20, 40, 255))


def make_thumbnail(match_id, home_logo_url, away_logo_url, base_bg):
    """
    Tạo thumbnail bằng cách lồng logo 2 đội vào ảnh nền.

    Layout:
    ┌─────────────────────────────────────────────┐
    │                                             │
    │   [LOGO HOME]         vs        [LOGO AWAY] │
    │   120x120 tại (100,100)      120x120 tại (420,100)
    │                                             │
    └─────────────────────────────────────────────┘
    """
    try:
        bg_copy = base_bg.copy()
        pasted = False

        if home_logo_url:
            try:
                h_res = requests.get(home_logo_url, timeout=5, impersonate="chrome110")
                if h_res.status_code == 200:
                    h_img = Image.open(io.BytesIO(h_res.content)).convert("RGBA")
                    h_img = h_img.resize((120, 120), Image.LANCZOS)
                    bg_copy.paste(h_img, (100, 100), h_img)
                    pasted = True
            except Exception as e:
                print(f"        [!] Logo home loi: {e}")

        if away_logo_url:
            try:
                a_res = requests.get(away_logo_url, timeout=5, impersonate="chrome110")
                if a_res.status_code == 200:
                    a_img = Image.open(io.BytesIO(a_res.content)).convert("RGBA")
                    a_img = a_img.resize((120, 120), Image.LANCZOS)
                    bg_copy.paste(a_img, (420, 100), a_img)
                    pasted = True
            except Exception as e:
                print(f"        [!] Logo away loi: {e}")

        if pasted:
            thumb_path = f"{THUMBS_DIR}/{match_id}.png"
            bg_copy.save(thumb_path, "PNG")
            return f"{BASE_THUMB_URL}{match_id}.png"

    except Exception as e:
        print(f"        [!] Loi tao thumbnail: {e}")

    return BG_IMAGE_URL  # Fallback: dùng ảnh nền gốc


# ──────────────────────────────────────────────────────────────
# BƯỚC 3: XỬ LÝ TỪNG TRẬN → TẠO FILE STREAM + KÊNH
# ──────────────────────────────────────────────────────────────
def process_match(match, base_bg):
    """
    Xử lý 1 trận đấu:
      1. Parse thông tin: teams, time, tournament, streams
      2. Tạo thumbnail (lồng logo)
      3. Ghi file stream/{match_id}.json
      4. Trả về channel dict cho master JSON và M3U

    Trả về (channel_for_master, channel_for_m3u) hoặc None nếu lỗi.
    """
    try:
        match_id = match.get('_id') or match.get('id', '')
        if not match_id:
            return None

        # ── Teams ──────────────────────────────────────────
        teams    = match.get('teams', {})
        home     = teams.get('home', {})
        away     = teams.get('away', {})
        team1    = home.get('name', 'Home')
        team2    = away.get('name', 'Away')
        home_logo = home.get('logo', '')
        away_logo = away.get('logo', '')

        # ── Giải đấu ───────────────────────────────────────
        tournament = match.get('tournament', {})
        league_name = tournament.get('name', 'Chuoi Chien TV')

        # ── Thời gian ──────────────────────────────────────
        match_time_raw = match.get('matchTime', '')
        time_display   = "00h00"
        if match_time_raw:
            try:
                dt = dateparser.parse(match_time_raw).astimezone(TZ_VN)
                time_display = dt.strftime("%Hh%M")
            except Exception:
                pass

        # ── BLV và Streams ─────────────────────────────────
        blvs = match.get('blvs', [])
        if not blvs:
            return None  # Không có BLV = không có stream

        # Gộp stream từ tất cả BLV (thường chỉ có 1)
        all_streams = []
        for blv in blvs:
            blv_name = blv.get('name', 'BLV Chuoi')
            for s in blv.get('streams', []):
                url = s.get('url', '')
                if not url:
                    continue
                all_streams.append({
                    "blv":   blv_name,
                    "label": s.get('label', 'HD'),
                    "url":   url,
                })

        if not all_streams:
            return None

        # ── Tạo thumbnail ──────────────────────────────────
        thumb_url = make_thumbnail(match_id, home_logo, away_logo, base_bg)

        # ── Ghi file stream/{match_id}.json ────────────────
        # File này chứa danh sách link stream trực tiếp
        # App TV sẽ fetch file này khi người dùng bấm vào kênh
        child_json = {"stream_links": []}
        master_stream_list = []

        for idx, s in enumerate(all_streams):
            stream_type = "hls" if ".m3u8" in s["url"] else "flv"
            s_name = f"{s['blv']} - {s['label']}"

            child_json["stream_links"].append({
                "id":      str(idx + 1),
                "name":    s_name,
                "type":    stream_type,
                "url":     s["url"],
                "default": idx == 0,
                "request_headers": [
                    {"key": "Referer", "value": "https://api.chuoichientv.com/"}
                ]
            })

            master_stream_list.append({
                "id":          f"{match_id}_{idx}",
                "name":        s_name,
                "remote_data": {
                    "url": f"{BASE_STREAM_URL}{match_id}.json"
                }
            })

        stream_file = f"{STREAM_DIR}/{match_id}.json"
        with open(stream_file, "w", encoding="utf-8") as f:
            json.dump(child_json, f, ensure_ascii=False, indent=2)

        # ── Tên hiển thị ───────────────────────────────────
        display_name = f"{team1} vs {team2}"
        team1_short  = shorten_name(team1)
        team2_short  = shorten_name(team2)

        # ── Channel cho Master JSON ────────────────────────
        channel_master = {
            "id":   match_id,
            "name": display_name,
            "labels": [
                {
                    "position":   "bottom-center",
                    "text":       time_display,
                    "color":      "#FF0000",
                    "text_color": "#FFFFFF"
                },
                {
                    "position":   "top-left",
                    "text":       team1_short,
                    "color":      "#80000000",
                    "text_color": "#FFFFFF"
                },
                {
                    "position":   "top-right",
                    "text":       team2_short,
                    "color":      "#80000000",
                    "text_color": "#FFFFFF"
                }
            ],
            "image": {
                "url":     thumb_url,
                "height":  360,
                "width":   640,
                "display": "cover"
            },
            "type":    "single",
            "display": "thumbnail-only",
            "sources": [
                {
                    "id":   f"src-{match_id}",
                    "name": "Chuoi Chien",
                    "contents": [
                        {
                            "id":      f"c-{match_id}",
                            "name":    display_name,
                            "streams": master_stream_list
                        }
                    ]
                }
            ]
        }

        # ── Channel cho M3U (TV Box) ───────────────────────
        # M3U dùng link stream đầu tiên trực tiếp
        channel_m3u = {
            "name":   f"[{time_display}] {display_name}",
            "group":  league_name,
            "logo":   thumb_url,
            "url":    all_streams[0]["url"],
        }

        return channel_master, channel_m3u

    except Exception as e:
        print(f"        [!] Loi xu ly tran: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# BƯỚC 4: XUẤT MASTER JSON
# ──────────────────────────────────────────────────────────────
def write_master_json(channels_master):
    """
    Xuất chuoichientv.json — file master import vào app TV.
    Cấu trúc này là format đặc thù của app dùng trong repo mẫu.
    """
    master = {
        "id":          "chuoichienhfb",
        "url":         f"{GITHUB_RAW}/chuoichientv.json",
        "name":        "Chuoi Chien TV",
        "color":       "#003A17",
        "description": "Cap nhat truc tiep tu Chuoi Chien TV",
        "image": {
            "url": "https://raw.githubusercontent.com/nghehoang007-wq/HFB321/main/HFB/Thump/chuoichien.png"
        },
        "groups": [
            {
                "id":          "live",
                "name":        "TRUC TIEP CHUOI CHIEN",
                "display":     "vertical",
                "grid_number": 2,
                "channels":    channels_master
            }
        ],
        "notice": {
            "id":         "notice",
            "link":       "https://t.me/",
            "icon":       "https://raw.githubusercontent.com/nghehoang007-wq/HFB321/main/HFB/Thump/chuoichien.png",
            "closeable":  True
        },
        "option": {
            "save_history":        False,
            "save_search_history": False,
            "save_wishlist":       False
        }
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(master, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] Master JSON -> {OUT_JSON}")


# ──────────────────────────────────────────────────────────────
# BƯỚC 5: XUẤT M3U CHO TV BOX
# ──────────────────────────────────────────────────────────────
def write_m3u(channels_m3u, path):
    """
    Xuất file M3U chuẩn — nạp thẳng vào TiviMate, IPTV Smarters...

    Mỗi kênh:
      #EXTINF:-1 tvg-logo="..." group-title="...",Tên kênh
      #EXTVLCOPT:http-referrer=...   ← CDN chuoichientv check Referer
      https://cdn.../index.m3u8
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        f.write(f"# Source  : https://live18.chuoichientv.com/\n")
        f.write(f"# Updated : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n")
        f.write(f"# Total   : {len(channels_m3u)} tran\n\n")

        for ch in channels_m3u:
            f.write(
                f'#EXTINF:-1 '
                f'tvg-logo="{ch["logo"]}" '
                f'group-title="{ch["group"]}"'
                f',{ch["name"]}\n'
            )
            # Referer quan trọng — thiếu sẽ bị CDN từ chối
            f.write(f'#EXTVLCOPT:http-referrer=https://live18.chuoichientv.com/\n')
            f.write(f'#EXTVLCOPT:http-user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\n')
            f.write(f'{ch["url"]}\n\n')

    print(f"[OK] M3U -> {path}  ({len(channels_m3u)} kenh)")


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    print("=" * 60)
    print("  CHUOICHIENTV SCRAPER v3")
    print(f"  {datetime.now(TZ_VN).strftime('%Y-%m-%d %H:%M ICT')}")
    print("=" * 60)

    # ── BƯỚC 1: Lấy danh sách trận ───────────────────────
    matches = fetch_matches()
    if not matches:
        print("\n[LOI] Khong lay duoc tran nao.")
        print("\n--- HUONG DAN LAY TOKEN MOI ---")
        print("1. Mo Chrome, vao https://live18.chuoichientv.com/")
        print("2. Nhan F12 -> tab Network -> loc 'api.chuoichientv.com'")
        print("3. Click vao bat ky request nao -> tab Headers")
        print("4. Tim dong 'Authorization: Bearer <token>'")
        print("5. Copy token moi vao bien BEARER_TOKEN o dau file")
        return

    # ── BƯỚC 2: Tải ảnh nền ──────────────────────────────
    print(f"\n[2/4] Tai anh nen thumbnail...")
    base_bg = load_base_bg()

    # ── BƯỚC 3: Xử lý từng trận ──────────────────────────
    print(f"\n[3/4] Xu ly {len(matches)} tran...")
    channels_master = []
    channels_m3u    = []

    for i, match in enumerate(matches):
        teams    = match.get('teams', {})
        team1    = teams.get('home', {}).get('name', '?')
        team2    = teams.get('away', {}).get('name', '?')
        print(f"  [{i+1:03d}/{len(matches)}] {team1} vs {team2}")

        result = process_match(match, base_bg)
        if result:
            ch_master, ch_m3u = result
            channels_master.append(ch_master)
            channels_m3u.append(ch_m3u)
            print(f"        -> OK | Stream: {ch_m3u['url'][:65]}...")
        else:
            print(f"        -> SKIP (khong co stream)")

    # ── BƯỚC 4: Xuất file ─────────────────────────────────
    print(f"\n[4/4] Xuat file... ({len(channels_master)} tran co stream)")

    write_master_json(channels_master)
    write_m3u(channels_m3u, OUT_M3U)
    write_m3u(channels_m3u, OUT_M3U_CCT)

    print(f"\n{'='*60}")
    print(f"  XONG!")
    print(f"  {len(channels_master)} tran da duoc xu ly")
    print(f"  stream/   : {len(channels_master)} file JSON con")
    print(f"  thumbs/   : {len(channels_master)} anh thumbnail")
    print(f"  {OUT_JSON}  : Master JSON cho app")
    print(f"  output/all.m3u  : Playlist cho TV Box")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
