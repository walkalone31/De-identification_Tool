#!/usr/bin/env bash
#
# scrub.sh — scrub.py 的包裝，把常用設定記在 scrub.conf，不用每次回想參數
#
#   ./scrub.sh spec.pdf              先 dry-run，確認後問你要不要真的做
#   ./scrub.sh spec.pdf -y           跳過確認，直接做
#   ./scrub.sh spec.pdf -n           只看 dry-run，不產檔
#   ./scrub.sh spec.pdf -o out.pdf   指定輸出檔名
#   ./scrub.sh -h                    說明（會印出目前生效的設定）
#
# 真正的公司名寫在 scrub.conf，不要寫進這個檔，也不要把 scrub.conf 進版控。
# 沒有 scrub.conf 時用下面的範例值（Apple / Banana），那只是佔位用的。
#
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SCRUB_PY="${SCRUB_PY:-$SCRIPT_DIR/scrub.py}"
CONF="${SCRUB_CONF:-$SCRIPT_DIR/scrub.conf}"

# ============ 預設值（範例；真正的值放 scrub.conf 覆蓋） ============

# 要遮掉的字串。"原字=代號" 會換成代號，只寫 "原字" 就塗黑。
# 比對不分大小寫，所以不用再加 APPLE、apple。
TERMS=( "Apple=VENDOR" "Banana=SUPPLIER" )

# 浮水印：刪掉整個文字物件，不遮區域。要寫浮水印的「完整」文字。
# 不確定完整字串時先跑 -n，輸出會提示真正的文字是什麼。
WATERMARKS=( "Apple Confidential" )

# 只保留這些頁。"2-" = 砍掉封面；留空 = 全部保留。
ONLY_PAGES=""

# 單一命中超過整頁幾 % 就跳過不遮（防呆，別關掉）
MAX_AREA="5"

# -w 的比例門檻：term 佔該文字物件的比例要 >= 這個值才刪
WM_RATIO="0.8"

# ===================================================================

# shellcheck source=/dev/null
[[ -f "$CONF" ]] && source "$CONF"

die() { printf '%s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '3,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  printf '\n目前設定（%s）：\n' \
    "$([[ -f "$CONF" ]] && printf '來自 %s' "${CONF##*/}" || printf '範例值，尚未建立 %s' "${CONF##*/}")"
  printf '  遮蔽   : %s\n' "${TERMS[*]:-(無)}"
  printf '  浮水印 : %s\n' "${WATERMARKS[*]:-(無)}"
  printf '  保留頁 : %s\n' "${ONLY_PAGES:-全部}"
  exit 0
}

# ---- 參數 ----
IN=""; OUT=""; ASSUME_YES=0; DRY_ONLY=0
while (( $# )); do
  case "$1" in
    -h|--help) usage ;;
    -y|--yes)  ASSUME_YES=1; shift ;;
    -n|--dry-run) DRY_ONLY=1; shift ;;
    -o|--output) OUT="${2:-}"; [[ -n "$OUT" ]] || die "-o 後面要接檔名"; shift 2 ;;
    -*) die "不認得的選項：$1（-h 看說明）" ;;
    *) [[ -z "$IN" ]] || die "一次只能處理一個檔案"; IN="$1"; shift ;;
  esac
done

[[ -n "$IN" ]] || usage
[[ -f "$IN" ]] || die "找不到檔案：$IN"
[[ -f "$SCRUB_PY" ]] || die "找不到 scrub.py（找過 $SCRUB_PY，可用 SCRUB_PY=... 指定）"
command -v python3 >/dev/null || die "找不到 python3"
python3 -c 'import pymupdf' 2>/dev/null || python3 -c 'import fitz' 2>/dev/null \
  || die "缺少 PyMuPDF，請先執行：pip install pymupdf"
(( ${#TERMS[@]} || ${#WATERMARKS[@]} )) || die "TERMS 和 WATERMARKS 都是空的，請編輯 $CONF"

# 還停在範例值 = 多半忘了建 scrub.conf
if [[ ! -f "$CONF" ]]; then
  printf '注意：找不到 %s，正在用範例值 (Apple / Banana)，八成不是你要的。\n' \
    "${CONF##*/}" >&2
  printf '      建一個 %s，內容像這樣：\n' "${CONF##*/}" >&2
  printf '        TERMS=( "公司名=VENDOR" )\n        WATERMARKS=( "浮水印完整文字" )\n        ONLY_PAGES="2-"\n\n' >&2
fi

# ---- 輸出檔名：順便把公司名從檔名拿掉（檔名本身也會外洩） ----
if [[ -z "$OUT" ]]; then
  base="${IN##*/}"; base="${base%.[Pp][Dd][Ff]}"
  for t in "${TERMS[@]}"; do
    pat="${t%%=*}"
    # 只處理單純的字詞，含特殊字元的就跳過不動檔名
    if [[ "$pat" =~ ^[A-Za-z0-9_]+$ ]]; then
      base="$(printf '%s' "$base" | sed -E "s/[-_ ]*${pat}[-_ ]*/_/Ig")"
    fi
  done
  base="$(printf '%s' "$base" | sed -E 's/_+/_/g; s/^_//; s/_$//')"
  [[ -n "$base" ]] || base="scrubbed"
  OUT="${base}.scrubbed.pdf"
fi

# ---- 組參數 ----
ARGS=()
for t in "${TERMS[@]:-}";      do [[ -n "$t" ]] && ARGS+=( -t "$t" ); done
for w in "${WATERMARKS[@]:-}"; do [[ -n "$w" ]] && ARGS+=( -w "$w" ); done
[[ -n "${ONLY_PAGES:-}" ]] && ARGS+=( --only-pages "$ONLY_PAGES" )
[[ -n "${MAX_AREA:-}"   ]] && ARGS+=( --max-area  "$MAX_AREA" )
[[ -n "${WM_RATIO:-}"   ]] && ARGS+=( --wm-ratio  "$WM_RATIO" )

printf '來源 : %s\n輸出 : %s\n\n' "$IN" "$OUT"

# ---- 1. dry-run ----
printf '=== 試跑（不產檔）===\n'
python3 "$SCRUB_PY" "$IN" "${ARGS[@]}" --dry-run

if (( DRY_ONLY )); then
  printf '\n只做試跑，結束。確認沒問題後拿掉 -n 再跑一次。\n'
  exit 0
fi

# ---- 2. 確認 ----
if (( ! ASSUME_YES )); then
  printf '\n上面看起來對嗎？特別注意：\n'
  printf '  - 有沒有「過大命中」沒被 -w 收掉\n'
  printf '  - -w 刪掉的物件是不是只有浮水印，沒有內文\n'
  read -r -p $'\n要產生 '"$OUT"' 嗎？[y/N] ' ans
  [[ "$ans" =~ ^[Yy]$ ]] || { printf '取消。\n'; exit 0; }
fi

# ---- 3. 正式跑 ----
printf '\n=== 正式產出 ===\n'
python3 "$SCRUB_PY" "$IN" -o "$OUT" "${ARGS[@]}"

# ---- 4. 剩下的圖片（logo 在文字層看不到） ----
printf '\n=== 圖片檢查（logo 不在文字層，驗證抓不到）===\n'
python3 - "$OUT" <<'PY'
import sys, pymupdf
d = pymupdf.open(sys.argv[1])
rows = [(i, len(p.get_images())) for i, p in enumerate(d, 1) if p.get_images()]
if not rows:
    print("  沒有內嵌圖片")
else:
    for i, n in rows:
        print(f"  p{i}: {n} 張圖")
    print("  每頁張數一樣的多半是頁首 logo，開檔確認一下")
PY

printf '\n完成：%s\n' "$OUT"
printf '傳出去前請自己翻一遍，重點看表格有沒有破、logo 還在不在。\n'
