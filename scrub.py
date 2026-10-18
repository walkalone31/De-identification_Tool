#!/usr/bin/env python3
"""
scrub.py — 文件去識別化 (真的刪掉文字，不是畫黑色方塊)

支援 PDF (有文字層的) 與純文字檔 (.txt / .md / 原始碼 ...)。

用法：
    # 一般字串：PDF 原地遮蔽／純文字就地取代
    python3 scrub.py spec.pdf -o out.pdf -t Apple -t Banana
    python3 scrub.py notes.md -t Apple -t Banana

    # 換成代號，文件還讀得懂 (PDF 的代號請用 ASCII)
    python3 scrub.py spec.pdf -t Apple=VENDOR -t Banana=SUPPLIER

    # -w：PDF 刪掉整個文字物件 (浮水印)；純文字刪掉整行 (橫幅)
    python3 scrub.py spec.pdf -o out.pdf -t Apple -w 'Apple Confidential'
    python3 scrub.py notes.md -w 'Apple Confidential'

    # 字串放檔案，一行一個，# 開頭是註解；- 表示 stdin
    python3 scrub.py spec.pdf -T terms.txt

    # 只輸出 12-15 頁 (縮小暴露面，建議搭配使用；僅 PDF)
    python3 scrub.py spec.pdf --only-pages 12-15 -T terms.txt

    # 副檔名不在清單內的純文字檔，用 --as-text 強制
    python3 scrub.py spec.adoc --as-text -t Apple

    # 先看會清到什麼，不產生檔案
    python3 scrub.py spec.pdf -T terms.txt --dry-run

需要：PDF 需 pip install pymupdf；純文字檔不需要任何套件
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

try:
    import pymupdf                    # PyMuPDF >= 1.24
except ImportError:
    try:
        import fitz as pymupdf        # 舊版只有 fitz 這個名字
    except ImportError:
        pymupdf = None                # txt / md 不需要它，等真的要開 PDF 再檢查


# 這些副檔名直接走純文字模式；其他副檔名當 PDF，或用 --as-text 強制
TEXT_EXTS = {".txt", ".md", ".markdown", ".mdown", ".mkd", ".rst",
             ".text", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
             ".c", ".h", ".cpp", ".hpp", ".py", ".sh", ".ini", ".conf"}

# -t 沒給代號時，純文字模式用這個標記取代 (PDF 模式是塗黑方塊)
TEXT_REDACT_MARK = "[REDACTED]"


# --------------------------------------------------------------------------
# 參數解析
# --------------------------------------------------------------------------

def parse_pages(spec, n_pages):
    """'1,3,5-7' / '12-' / '-5' -> 0-based page index set"""
    pages = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo = int(lo) if lo.strip() else 1
            hi = int(hi) if hi.strip() else n_pages
            pages.update(range(lo - 1, hi))
        else:
            pages.add(int(part) - 1)
    bad = {p + 1 for p in pages if not (0 <= p < n_pages)}
    if bad:
        sys.exit(f"頁碼超出範圍 (共 {n_pages} 頁): {sorted(bad)}")
    return pages


def parse_term(raw):
    """'Banana' -> ('Banana', None)；'Banana=VENDOR' -> ('Banana', 'VENDOR')"""
    if "=" in raw:
        pat, repl = raw.rsplit("=", 1)      # 用最後一個 = 切，pattern 裡才能有 =
        pat, repl = pat.strip(), repl.strip()
        if pat:
            return (pat, repl or None)
    return (raw.strip(), None)


def load_terms(args):
    terms = [parse_term(t) for t in (args.term or [])]

    for src in (args.terms_file or []):
        lines = (sys.stdin.read() if src == "-"
                 else Path(src).read_text(encoding="utf-8")).splitlines()
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                terms.append(parse_term(line))

    terms = [t for t in terms if t[0]]

    # 不分大小寫時 Apple / APPLE 是同一條，留著只會重複計算。
    # 但在真的區分大小寫的模式下它們是兩條，不能合併。
    cs = args.case_sensitive and (args.regex or getattr(args, "text_mode", False))
    seen, uniq, dupes = set(), [], []
    for pat, repl in terms:
        key = (pat if cs else pat.lower(), repl)
        if key in seen:
            dupes.append(pat)
            continue
        seen.add(key)
        uniq.append((pat, repl))
    if dupes and not args.quiet:
        print(f"注意：忽略重複的字串 {', '.join(repr(d) for d in dupes)}"
              f" (比對不分大小寫)", file=sys.stderr)
    return uniq


# --------------------------------------------------------------------------
# 比對
# --------------------------------------------------------------------------

def find_hits(page, terms, use_regex, case_sensitive):
    """回傳 [(rect, replacement, matched_text), ...]"""
    hits = []

    if use_regex:
        # word 層級比對：支援 regex 與大小寫區分，但跨 word 的片語比不到
        words = page.get_text("words")   # (x0, y0, x1, y1, word, block, line, word_no)
        flags = 0 if case_sensitive else re.IGNORECASE
        for pat, repl in terms:
            try:
                rx = re.compile(pat, flags)
            except re.error as e:
                sys.exit(f"regex 有誤 {pat!r}: {e}")
            for w in words:
                if rx.search(w[4]):
                    hits.append((pymupdf.Rect(w[:4]), repl, w[4]))
    else:
        # 字面比對：可跨 word 比片語，但 PyMuPDF 的 search_for 一律不分大小寫
        for pat, repl in terms:
            for rect in page.search_for(pat):
                hits.append((rect, repl, pat))

    return hits


def split_by_area(hits, page, max_pct):
    """把命中切成 (正常, 過大)。過大的多半是斜向浮水印的外接矩形。"""
    page_area = page.rect.get_area() or 1.0
    ok, big = [], []
    for h in hits:
        pct = 100.0 * h[0].get_area() / page_area
        (big if pct > max_pct else ok).append((h, pct))
    return [h for h, _ in ok], big


def apply_hits(page, hits):
    for rect, repl, _ in hits:
        if repl:
            # 留下代號：白底黑字。代號請用 ASCII，helv 無法顯示中日韓文字
            # 代號比原字長時縮小字級，免得被擠到換行
            try:
                unit_w = pymupdf.get_text_length(repl, fontname="helv", fontsize=1.0)
            except Exception:
                unit_w = 0.55 * max(1, len(repl))
            fit_w = rect.width * 0.95 / unit_w if unit_w > 0 else 11.0
            size = max(3.0, min(11.0, rect.height * 0.8, fit_w))
            page.add_redact_annot(rect, text=repl, fontname="helv", fontsize=size,
                                  fill=(1, 1, 1), text_color=(0, 0, 0), cross_out=False)
        else:
            page.add_redact_annot(rect, fill=(0, 0, 0), cross_out=False)
    if not hits:
        return
    try:
        # 向量圖形不刪 (時序圖被矩形蓋到也不會消失)
        # 圖片只塗掉被蓋住的像素，不整張砍掉
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                              graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
    except (TypeError, AttributeError):
        page.apply_redactions()          # 舊版沒有這些參數


# --------------------------------------------------------------------------
# 浮水印：刪掉整個文字物件，而不是遮一大塊區域
# --------------------------------------------------------------------------

BT_ET  = re.compile(rb"BT\b.*?\bET\b", re.S)
PDFSTR = re.compile(rb"<([0-9A-Fa-f\s]*)>|\((?:\\.|[^\\()])*\)", re.S)


ENCODINGS = ("utf-8", "latin-1", "utf-16-be")


def _decode_candidates(block):
    """
    把一個 BT..ET 區塊裡被繪出的字串解出來。

    字型編碼未知，所以每種編碼各給一個完整候選字串，由呼叫端逐一比對。
    (不能把各種編碼的結果串在一起，那會讓長度膨脹，比例判斷就失準了)
    """
    raws = []
    for m in PDFSTR.finditer(block):
        if m.group(1) is not None:                       # <hex>
            h = re.sub(rb"\s", b"", m.group(1))
            if len(h) % 2:
                h += b"0"
            try:
                raws.append(bytes.fromhex(h.decode("ascii")))
            except ValueError:
                continue
        else:                                            # (literal)
            raw = m.group(0)[1:-1]
            raws.append(re.sub(rb"\\([nrtbf()\\])", lambda x: x.group(1), raw))

    out = []
    for enc in ENCODINGS:
        try:
            out.append("".join(r.decode(enc) for r in raws))
        except (UnicodeDecodeError, ValueError):
            pass
    return out


def _nows(s):
    return re.sub(r"\s+", "", s)


def _strip_stream(data, terms, ratio):
    """
    只刪「幾乎整個物件就是這個字」的文字物件。

    浮水印自己獨佔一個 BT..ET，所以 term 佔該物件文字的比例接近 1。
    內文裡夾雜 term 的段落比例很低，會被保留 —— 這就是不會誤刪內文的關鍵。
    """
    removed, samples = 0, []

    def repl(m):
        nonlocal removed
        for text in _decode_candidates(m.group(0)):
            flat = _nows(text)
            if not flat:
                continue
            low = flat.lower()
            for t in terms:
                tf = _nows(t).lower()
                if tf and tf in low and len(tf) / len(flat) >= ratio:
                    removed += 1
                    if len(samples) < 3:
                        samples.append(text.strip()[:60])
                    return b""
        return m.group(0)

    return BT_ET.sub(repl, data), removed, samples


def strip_text_objects(doc, page, terms, ratio, dry_run=False):
    """頁面本體 + Form XObject 都掃一遍。回傳 (刪掉幾個, 範例文字)。"""
    total, samples = 0, []

    data = page.read_contents()
    if data:
        new, n, s = _strip_stream(data, terms, ratio)
        if n and not dry_run:
            xref = doc.get_new_xref()
            doc.update_object(xref, "<<>>")
            doc.update_stream(xref, new, new=True)
            page.set_contents(xref)
        total += n
        samples += s

    for item in page.get_xobjects():
        xref = item[0]
        try:
            if doc.xref_get_key(xref, "Subtype")[1] != "/Form":
                continue
            raw = doc.xref_stream(xref)
        except Exception:
            continue
        if not raw:
            continue
        new, n, s = _strip_stream(raw, terms, ratio)
        if n and not dry_run:
            doc.update_stream(xref, new)
        total += n
        samples += s

    return total, samples


# --------------------------------------------------------------------------
# 損傷統計 + 驗證
# --------------------------------------------------------------------------

def build_matcher(terms, wm_terms, use_regex, case_sensitive):
    flags = 0 if case_sensitive else re.IGNORECASE
    pats = []
    for pat, _ in terms:
        pats.append(re.compile(pat if use_regex else re.escape(pat), flags))
    for t in wm_terms:
        # 浮水印是整個物件刪掉，所以它的每個字都算預期內
        for word in t.split():
            pats.append(re.compile(re.escape(word), flags))
    return lambda s: any(p.search(s) for p in pats)


def verify(path, terms, wm_terms, use_regex, case_sensitive):
    doc = pymupdf.open(path)
    flags = 0 if case_sensitive else re.IGNORECASE
    checks = [(pat, re.compile(pat if use_regex else re.escape(pat), flags))
              for pat, _ in terms]
    checks += [(t, re.compile(re.escape(t), flags)) for t in wm_terms]
    leaks = []

    for pno, page in enumerate(doc, start=1):
        text = page.get_text()
        if not text:
            continue
        for label, rx in checks:
            if rx.search(text):
                leaks.append((f"p{pno}", label))

    meta = " ".join(str(v) for v in (doc.metadata or {}).values() if v)
    for label, rx in checks:
        if rx.search(meta):
            leaks.append(("metadata", label))

    doc.close()
    return leaks


# --------------------------------------------------------------------------
# 純文字模式 (.txt / .md / ...)
# --------------------------------------------------------------------------

# 先 UTF-8，再舊的中文編碼。用哪個解就用哪個寫回去，
# 免得把原本不是 UTF-8 的檔案寫成 UTF-8 而弄壞其他字。
TEXT_ENCODINGS = ("utf-8", "cp950", "big5", "gb18030", "latin-1")
BOM_UTF8 = b"\xef\xbb\xbf"


def read_text_file(path):
    """
    回傳 (內容, 編碼, 有沒有 BOM)。二進位檔直接拒絕。

    BOM 單獨記錄，不靠 utf-8-sig —— 那個解碼器對沒有 BOM 的檔案也會成功，
    寫回去時卻一定會加上 BOM，等於偷偷改了原本沒有 BOM 的檔案。
    """
    raw = path.read_bytes()
    if b"\x00" in raw:
        sys.exit(f"{path.name} 看起來是二進位檔 (含 NUL)，純文字模式不處理。")

    had_bom = raw.startswith(BOM_UTF8)
    if had_bom:
        raw = raw[len(BOM_UTF8):]

    for enc in TEXT_ENCODINGS:
        try:
            return raw.decode(enc), enc, had_bom
        except (UnicodeDecodeError, LookupError):
            continue
    sys.exit(f"{path.name} 無法解碼，請確認編碼。")


def compile_terms(terms, use_regex, case_sensitive):
    """回傳 [(rx, 取代字串, 原 pattern), ...]"""
    flags = 0 if case_sensitive else re.IGNORECASE
    out = []
    for pat, repl in terms:
        try:
            rx = re.compile(pat if use_regex else re.escape(pat), flags)
        except re.error as e:
            sys.exit(f"regex 有誤 {pat!r}: {e}")
        out.append((rx, repl or TEXT_REDACT_MARK, pat))
    return out


def drop_watermark_lines(text, wm_terms, ratio, case_sensitive):
    """
    -w 在純文字裡的對應動作：整行刪掉。

    一樣套用 --wm-ratio —— 只有「這一行幾乎就是這個字」才刪，
    所以 'Apple Confidential' 的橫幅行會刪掉，
    內文裡夾著這幾個字的段落會留下來走一般取代。
    """
    if not wm_terms:
        return text, [], []

    flat_terms = [(_nows(t) if case_sensitive else _nows(t).lower())
                  for t in wm_terms]
    flat_terms = [t for t in flat_terms if t]

    kept, dropped, near = [], [], []
    for line in text.splitlines(keepends=True):
        flat = _nows(line)
        if flat:
            cmp_line = flat if case_sensitive else flat.lower()
            hit = [t for t in flat_terms if t in cmp_line]
            if hit:
                best = max(len(t) / len(flat) for t in hit)
                if best >= ratio:
                    dropped.append(line.strip())
                    continue
                near.append((line.strip(), best))   # 含該字但比例不足
        kept.append(line)
    return "".join(kept), dropped, near


def run_text(args, src, out, terms, wm_terms):
    text, enc, had_bom = read_text_file(src)
    compiled = compile_terms(terms, args.regex, args.case_sensitive)

    if (args.pages or args.only_pages) and not args.quiet:
        print("注意：純文字沒有頁的概念，--pages / --only-pages 已忽略",
              file=sys.stderr)

    # 1. 浮水印／橫幅：整行刪掉
    new, dropped, near = drop_watermark_lines(text, wm_terms, args.wm_ratio,
                                              args.case_sensitive)

    # 2. 一般字串取代。用 lambda 當 repl，避免代號裡的 \1 之類被當成反向參照
    counts = []
    for rx, repl, pat in compiled:
        new, n = rx.subn(lambda m, r=repl: r, new)
        counts.append((pat, repl, n))

    total_hits = sum(n for _, _, n in counts)

    if not args.quiet:
        print(f"  {src.name} ({enc}{'+BOM' if had_bom else ''}, "
              f"{len(text.splitlines())} 行)")
        for pat, repl, n in counts:
            if n:
                print(f"    {pat!r} -> {repl!r}  {n} 處")
        if dropped:
            print(f"    刪除 {len(dropped)} 行 (-w)")
        if not total_hits and not dropped:
            print("    沒有任何命中")

    if dropped and not args.quiet:
        print("\n-w 刪掉的整行長這樣 (確認沒有內文被誤刪)：")
        for line in dropped[:5]:
            print(f"    {line[:70]!r}")
        if len(dropped) > 5:
            print(f"    ... 另外還有 {len(dropped) - 5} 行")

    if near and not args.quiet:
        print(f"\n-w 跳過的行 (含該字但比例 < {args.wm_ratio}，所以沒整行刪)：")
        for line, pct in near[:5]:
            print(f"    比例 {pct:.2f}  {line[:70]!r}")
        if len(near) > 5:
            print(f"    ... 另外還有 {len(near) - 5} 行")
        print("   這些行會交給 -t 處理。真的要整行刪就調低 --wm-ratio")

    if wm_terms and not dropped and not args.quiet:
        print(f"\n注意：-w 一行都沒刪到。確認浮水印／橫幅的完整文字是否正確"
              + ("" if near else "，或該行是否存在"))

    if args.dry_run:
        print(f"\n[dry-run] 取代 {total_hits} 處"
              + (f"、刪除 {len(dropped)} 行" if dropped else "")
              + "，未寫出檔案")
        return

    # 原檔有 BOM 才寫 BOM；編碼跟來源一致，不偷偷轉成 UTF-8
    out.write_bytes((BOM_UTF8 if had_bom else b"") + new.encode(enc))

    print(f"\n已寫出 {out}  (取代 {total_hits} 處"
          + (f"、刪除 {len(dropped)} 行" if dropped else "") + ")")

    # 驗證：重讀輸出檔，確認字串真的不在了
    check, _, _ = read_text_file(out)
    flags = 0 if args.case_sensitive else re.IGNORECASE
    leaks = []
    for _, _, pat in compiled:
        rx = re.compile(pat if args.regex else re.escape(pat), flags)
        if rx.search(check):
            leaks.append(pat)
    for t in wm_terms:
        if re.search(re.escape(t), check, flags):
            leaks.append(t)

    if leaks:
        print("✗ 驗證失敗，輸出檔仍有殘留：")
        for pat in sorted(set(leaks)):
            print(f"    {pat}")
        print("   -w 的字串殘留 = 該行比例不足沒被刪，可改用 -t 取代，"
              "或調低 --wm-ratio")
        sys.exit(1)

    print("✓ 驗證通過：輸出檔找不到這些字串")
    print(f"  另外請自己確認：檔名 ({out.name})、"
          f"程式碼註解、URL、路徑裡的名字")


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="文件去識別化：真的刪除文字，並清掉 metadata (PDF / 純文字)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法：", 1)[1] if "用法：" in __doc__ else None,
    )
    ap.add_argument("input", help="來源檔 (PDF，或 .txt / .md 等純文字)")
    ap.add_argument("-o", "--output",
                    help="輸出檔 (預設 <input>.scrubbed.<原副檔名>)")
    ap.add_argument("-t", "--term", action="append", metavar="TEXT[=REPL]",
                    help="要遮掉的字串，可重複；寫成 Apple=VENDOR 會換成代號")
    ap.add_argument("-T", "--terms-file", action="append", metavar="FILE",
                    help="從檔案讀字串，一行一個，- 表示 stdin")
    ap.add_argument("-w", "--watermark", action="append", metavar="TEXT", default=[],
                    help="PDF：刪掉含這段文字的整個文字物件 (浮水印專用，不遮區域)；"
                         "純文字：把含這段文字的整行刪掉")
    ap.add_argument("--as-text", action="store_true",
                    help="不管副檔名，一律當純文字處理")
    ap.add_argument("--pages", metavar="RANGE",
                    help="只處理這些頁 (如 12-15,20)，其他頁原樣保留")
    ap.add_argument("--only-pages", metavar="RANGE",
                    help="輸出只保留這些頁 (同時也只處理這些頁)")
    ap.add_argument("--max-area", type=float, default=5.0, metavar="PCT",
                    help="單一命中超過整頁這個百分比就跳過不遮 (預設 5)")
    ap.add_argument("--wm-ratio", type=float, default=0.8, metavar="R",
                    help="-w 只刪 term 佔該文字物件比例 >= R 的物件 (預設 0.8)，"
                         "內文裡夾雜該字的段落因此不會被刪")
    ap.add_argument("--regex", action="store_true",
                    help="把字串當 regex，以 word 為單位比對")
    ap.add_argument("--case-sensitive", action="store_true",
                    help="區分大小寫。純文字模式一律有效；"
                         "PDF 模式只在 --regex 下有效")
    ap.add_argument("--keep-annots", action="store_true",
                    help="保留註解與表單欄位 (預設刪除，它們常含人名)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列出會清到什麼，不寫檔")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.is_file():
        sys.exit(f"找不到檔案：{src}")

    text_mode = args.as_text or src.suffix.lower() in TEXT_EXTS
    args.text_mode = text_mode          # load_terms 的去重規則要看這個

    if args.case_sensitive and not args.regex and not text_mode:
        print("注意：PDF 模式的 --case-sensitive 只在 --regex 下有效，"
              "字面比對一律不分大小寫 (這樣比較安全)", file=sys.stderr)

    terms = load_terms(args)
    wm_terms = [w.strip() for w in args.watermark if w.strip()]
    if not terms and not wm_terms:
        sys.exit("沒有指定任何字串，請用 -t / -T / -w")

    if args.output:
        out = Path(args.output)
    elif text_mode:
        out = src.with_name(f"{src.stem}.scrubbed{src.suffix}")
    else:
        out = src.with_suffix(".scrubbed.pdf")
    if out.resolve() == src.resolve():
        sys.exit("輸出不能蓋掉原檔")

    # ---- 純文字模式 (.txt / .md / ...) ----
    if text_mode:
        run_text(args, src, out, terms, wm_terms)
        return

    # ---- PDF 模式 ----
    if pymupdf is None:
        sys.exit("處理 PDF 需要 PyMuPDF，請先執行：pip install pymupdf\n"
                 "(純文字檔 .txt / .md 不需要它)")

    doc = pymupdf.open(src)
    if not doc.is_pdf:
        fmt = (doc.metadata or {}).get("format") or "未知格式"
        sys.exit(f"這個檔不是 PDF，格式看起來是 {fmt}。\n"
                 f"純文字檔請改用 --as-text；其他格式請先轉成 PDF。")
    n_pages = doc.page_count

    if args.only_pages:
        keep = sorted(parse_pages(args.only_pages, n_pages))
        doc.select(keep)
        targets = set(range(doc.page_count))
        if not args.quiet:
            print(f"只保留第 {args.only_pages} 頁 ({len(keep)} 頁)")
    elif args.pages:
        targets = parse_pages(args.pages, n_pages)
    else:
        targets = set(range(n_pages))

    is_expected = build_matcher(terms, wm_terms, args.regex, args.case_sensitive)

    total_hits = total_wm = blank_pages = 0
    oversized = []
    wm_samples = []
    before_words, after_words = Counter(), Counter()

    for pno in sorted(targets):
        page = doc[pno]
        page_before = Counter(page.get_text().split())
        before_words += page_before
        if not page_before:
            blank_pages += 1

        # 1. 浮水印先處理：刪物件，不遮區域
        nwm, samples = (strip_text_objects(doc, page, wm_terms, args.wm_ratio,
                                           args.dry_run) if wm_terms else (0, []))
        total_wm += nwm
        for s in samples:
            if len(wm_samples) < 5 and s not in wm_samples:
                wm_samples.append(s)

        # 2. 一般字串
        hits = find_hits(page, terms, args.regex, args.case_sensitive) if terms else []
        hits, big = split_by_area(hits, page, args.max_area)

        for (rect, _, matched), pct in big:
            oversized.append((pno + 1, matched, pct))

        parts = []
        if hits:
            shown = sorted({h[2] for h in hits})
            parts.append(f"遮蔽 {len(hits)} 處  " + ", ".join(shown[:6])
                         + (" ..." if len(shown) > 6 else ""))
        if nwm:
            parts.append(f"刪除 {nwm} 個文字物件")
        if big and not parts:
            parts.append(f"只有 {len(big)} 處過大命中，已跳過")
        if parts and not args.quiet:
            print(f"  p{pno + 1}: " + " / ".join(parts))

        total_hits += len(hits)
        if not args.dry_run:
            apply_hits(page, hits)
            after_words += Counter(page.get_text().split())

    if oversized:
        print(f"\n⚠  以下命中超過整頁 {args.max_area}%，已跳過不遮 "
              f"(遮下去會連同底下內文一起刪掉)：")
        for pno, matched, pct in oversized[:12]:
            print(f"    p{pno}: {matched!r}  佔 {pct:.1f}%")
        if len(oversized) > 12:
            print(f"    ... 另外還有 {len(oversized) - 12} 處")
        print("   這通常是斜向浮水印。改用 -w 刪掉整個文字物件，例如：")
        print(f"       -w {oversized[0][1]!r}")
        print("   確定只是一般大字而想照遮，就調高 --max-area")

    if wm_samples and not args.quiet:
        print("\n-w 刪掉的文字物件長這樣 (確認沒有內文被誤刪)：")
        for s_ in wm_samples:
            print(f"    {s_!r}")

    if args.dry_run:
        print(f"\n[dry-run] 遮蔽 {total_hits} 處"
              + (f"、刪除 {total_wm} 個文字物件" if wm_terms else "")
              + "，未寫出檔案")
        doc.close()
        return

    if not args.keep_annots:
        for page in doc:
            for annot in list(page.annots() or []):
                page.delete_annot(annot)

    for name in [doc.embfile_info(i)["name"] for i in range(doc.embfile_count())]:
        doc.embfile_del(name)

    doc.set_metadata({})     # Info dict: Author / Title / Producer / Company
    doc.del_xml_metadata()   # XMP
    doc.set_toc([])          # 書籤標題也可能有公司名

    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()

    # 損傷統計：哪些字不該消失卻消失了
    lost = before_words - after_words
    collateral = Counter({w: c for w, c in lost.items() if not is_expected(w)})

    print(f"\n已寫出 {out}  (遮蔽 {total_hits} 處"
          + (f"、刪除 {total_wm} 個文字物件" if wm_terms else "") + ")")

    if blank_pages:
        print(f"⚠  有 {blank_pages} 頁抓不到文字，可能是掃描圖檔，"
              f"這種頁面本工具清不到，要先 OCR 或改用座標遮蔽")

    if collateral:
        n = sum(collateral.values())
        print(f"⚠  損傷檢查：有 {n} 個字不該消失卻消失了，請開檔確認：")
        for w, c in collateral.most_common(15):
            print(f"    {w!r} x{c}")
        if len(collateral) > 15:
            print(f"    ... 另外還有 {len(collateral) - 15} 種")
    else:
        print(f"✓ 損傷檢查：消失的 {sum(lost.values())} 個字全部都在預期內")

    leaks = verify(out, terms, wm_terms, args.regex, args.case_sensitive)
    if leaks:
        print("✗ 驗證失敗，以下位置仍有殘留：")
        for where, pat in sorted(set(leaks), key=str):
            print(f"    {where}: {pat}")
        sys.exit(1)

    print("✓ 驗證通過：輸出檔的文字層與 metadata 都找不到這些字串")
    print(f"  另外請自己確認：檔名 ({out.name})、logo、浮水印、圖片內燒死的文字")


if __name__ == "__main__":
    main()
