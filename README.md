# scrub — 文件去識別化

把文件裡的公司名、專案代號、人名、浮水印**真正刪掉**（不是畫黑色方塊蓋住），
並清掉 metadata，讓文件可以安全地交給外部工具或第三方。

支援 **PDF** 與 **純文字檔**（`.txt` / `.md` / 原始碼……）。

畫黑色方塊**不算**去識別化 —— 底下的文字還在，複製貼上就出來了。
這個工具走 PDF redaction 和 content stream 手術，刪掉的文字是真的不存在了。

---

## 安裝

| 需要 | 版本 | 備註 |
|---|---|---|
| Python | 3.8+ | 實測 3.13 |
| PyMuPDF | 1.24+ | **只有處理 PDF 時需要**；純文字檔零相依 |
| bash | 4+ | 只有 `scrub.sh` 需要；`scrub.py` 可單獨使用 |

```bash
pip install pymupdf     # 只處理 .txt / .md 的話可以跳過
```

Debian / Ubuntu 若遇到 externally-managed-environment：

```bash
pip install pymupdf --break-system-packages
# 或
python3 -m venv .venv && . .venv/bin/activate && pip install pymupdf
```

舊版 PyMuPDF 只有 `fitz` 這個模組名，程式會自動 fallback，不用改。

檢查安裝：

```bash
python3 -c "import pymupdf; print(pymupdf.__doc__.splitlines()[0])"
```

---

## 支援的檔案類型

### PDF（`.pdf`）

必須是**有文字層**的 PDF —— 也就是用 Word / LaTeX / Illustrator 等軟體匯出的，
文字可以選取、可以搜尋。

判斷方式：

```bash
pdftotext -l 1 your.pdf - | head
# 有文字跑出來 = 可以處理
```

或者直接跑 `--dry-run`，程式會告訴你有幾頁抓不到文字。

### 純文字檔

以下副檔名自動走純文字模式，**不需要安裝 PyMuPDF**：

```
.txt  .md  .markdown  .mdown  .mkd  .rst  .text  .log
.csv  .tsv  .json  .yaml  .yml
.c  .h  .cpp  .hpp  .py  .sh  .ini  .conf
```

其他副檔名（或完全沒有副檔名）用 `--as-text` 強制：

```bash
python3 scrub.py CHANGELOG --as-text -t Apple
```

純文字模式的行為：

- 編碼自動偵測（UTF-8 → cp950 → big5 → gb18030 → latin-1），**用原編碼寫回去**，
  不會偷偷把 cp950 的檔案轉成 UTF-8
- **BOM 與 CRLF 原樣保留** —— 原本沒有 BOM 的不會被加上，原本是 CRLF 的不會變成 LF
- `-t Apple` 沒給代號時，取代成 `[REDACTED]`（PDF 模式是塗黑方塊）
- `--case-sensitive` **一律有效**（PDF 模式只在 `--regex` 下有效）
- 連 URL、檔案路徑、程式碼註解裡的名字都會一起換掉

含 NUL 的二進位檔會被擋下來：

```
xxx.bin 看起來是二進位檔 (含 NUL)，純文字模式不處理。
```

### 不能處理

| 類型 | 為什麼 | 怎麼辦 |
|---|---|---|
| 掃描 PDF（整頁是圖片） | 沒有文字層，搜尋不到字 | 先 OCR（`ocrmypdf in.pdf out.pdf`），或手動用座標遮蔽 |
| 圖片裡燒死的文字、logo、圖形浮水印 | 同上，那是像素不是文字 | 本工具抓不到，必須自己看過 |
| `.docx` / `.pptx` / `.xlsx` | 是壓縮檔，不是純文字也不是 PDF | 先另存成 PDF，或用該格式自己的工具 |
| 加密／有密碼的 PDF | 開不了 | 先用 `qpdf --decrypt` 解開 |

餵非 PDF、副檔名又不在清單內的檔案進去，會提示你用 `--as-text`：

```
這個檔不是 PDF，格式看起來是 Text。
純文字檔請改用 --as-text；其他格式請先轉成 PDF。
```

### 已知限制

- **子集化字型搭配自訂 CMap** 時，`-w` 的 content stream 比對可能失效
  （Word 匯出的 PDF 偶爾會這樣）。失效時會顯示「刪除 0 個文字物件」，不會默默放過。
- **跨行被拆開的字串**抓不到，這是 PDF 文字切片的先天限制。
  所以 `--dry-run` 的命中數如果比預期少，要自己確認。
- **字面比對一律不分大小寫**（PyMuPDF `search_for` 的行為），
  所以不用加 `-t APPLE -t apple`，加了反而重複計算。`--case-sensitive` 只在 `--regex` 下有效。

---

## 快速開始

```bash
# 1. 建立設定檔
cp scrub.conf.example scrub.conf
vim scrub.conf          # 填入真正的公司名、浮水印文字

# 2. 試跑，確認會清到什麼
./scrub.sh spec.pdf -n

# 3. 正式產出（會先試跑再問你要不要繼續）
./scrub.sh spec.pdf
./scrub.sh notes.md     # 純文字檔同一套流程
```

輸出檔名預設是 `<原檔名去掉公司名>.scrubbed.<原副檔名>`。
**檔名本身也會外洩**，所以程式會自動把 TERMS 裡的字從檔名拿掉：

```
Apple_CAN_notes_BANANA.md  ->  CAN_notes.scrubbed.md
```

---

## 檔案說明

| 檔案 | 用途 | 可否公開 |
|---|---|---|
| `scrub.py` | 核心工具，可單獨使用 | ✅ 無公司名 |
| `scrub.sh` | 包裝腳本，記住常用參數 | ✅ 無公司名 |
| `scrub.conf.example` | 設定範本 | ✅ 佔位值 |
| `scrub.conf` | **你的實際設定** | ❌ **有真實公司名，不要進版控** |

```bash
echo 'scrub.conf'      >> .gitignore
echo '*.scrubbed.pdf'  >> .gitignore
```

---

## scrub.conf

```bash
# 要遮掉的字串。"原字=代號" 換成代號，只寫 "原字" 就塗黑。
TERMS=( "Apple=VENDOR" "Banana=SUPPLIER" )

# 浮水印：刪掉整個文字物件。要寫「完整」文字。
WATERMARKS=( "Apple Confidential" )

# 只保留這些頁。"2-" = 砍掉封面；留空 = 全部保留。
ONLY_PAGES="2-"

# 單一命中超過整頁幾 % 就跳過不遮（防呆，別關掉）
MAX_AREA="5"

# -w 的比例門檻：term 佔該文字物件比例 >= 這個值才刪
WM_RATIO="0.8"
```

多個客戶可以各留一份：`SCRUB_CONF=acme.conf ./scrub.sh spec.pdf`

**建議砍掉封面**（`ONLY_PAGES="2-"`）。標題、版本、修訂記錄、作者通常都擠在第一頁，
而技術內容一個字都不在上面。

---

## 兩種清理方式

### 一般字串 `-t` —— 原地遮蔽

適合散落在內文裡的名字。在原位置塗黑，或換成代號。

```bash
python3 scrub.py spec.pdf -t Apple=VENDOR -t Banana=SUPPLIER
```

代號請用 **ASCII**，PDF 內建的 `helv` 字型顯示不了中日韓文字。
代號比原字長時會自動縮小字級，不會被擠到換行。

### 浮水印／橫幅 `-w`

| 模式 | 動作 |
|---|---|
| PDF | 刪掉**整個文字物件**（不遮區域） |
| 純文字 | 刪掉**整行** |

斜向浮水印的外接矩形可能佔整頁 30%，直接遮下去**會連同底下的內文一起刪掉**。
所以 PDF 的 `-w` 不遮區域，而是把該文字物件從 content stream 整個拿掉。

```bash
python3 scrub.py spec.pdf -w 'Apple Confidential'
python3 scrub.py notes.md -w 'Apple Confidential'   # 刪掉那一整行
```

實測差異（含斜向浮水印、14 行內文的測試檔）：

| 做法 | 內文保留 |
|---|---|
| 用 `-t` 遮區域 | 5 / 14 |
| 用 `-w` 刪物件 | **14 / 14** |

`-w` 要寫**完整**的浮水印文字。只寫一部分（例如浮水印是 `Apple Confidential` 卻只寫 `Apple`）
會因為比例不足而刪不掉。不確定完整文字時先跑 `--dry-run`，輸出會提示。

`--wm-ratio`（預設 0.8）是這個判斷的門檻：只有「這個物件／這一行幾乎就是這個字」才刪。
所以橫幅行會被刪掉，而內文裡夾著這幾個字的段落會留下來走一般取代。
純文字模式會把比例不足、因此沒刪的行列出來給你看：

```
-w 跳過的行 (含該字但比例 < 0.8，所以沒整行刪)：
    比例 0.46  '/* Apple Confidential - do not distribute */'
   這些行會交給 -t 處理。真的要整行刪就調低 --wm-ratio
```

---

## 三道安全網

**1. 面積防呆（`--max-area`，預設 5%）**

單一命中超過整頁 5% 就跳過不遮，並提示你改用 `-w`：

```
⚠  以下命中超過整頁 5.0%，已跳過不遮 (遮下去會連同底下內文一起刪掉)：
    p1: 'Apple'  佔 9.0%
   這通常是斜向浮水印。改用 -w 刪掉整個文字物件，例如：
       -w 'Apple'
```

**2. 損傷檢查**

比對清理前後的文字，列出**不該消失卻消失的字**：

```
✓ 損傷檢查：消失的 70 個字全部都在預期內
```

誤刪時會變成 `⚠`，並列出是哪些字。

**3. 輸出驗證**

重新打開產出的檔案，掃描文字層和 metadata。有殘留就 `exit 1`：

```
✗ 驗證失敗，以下位置仍有殘留：
    p10: Apple
```

---

## scrub.py 參數

```
scrub.py INPUT [-o OUT] [-t TEXT[=REPL]] [-T FILE] [-w TEXT]
         [--pages RANGE] [--only-pages RANGE]
         [--max-area PCT] [--wm-ratio R]
         [--regex] [--case-sensitive] [--keep-annots] [--dry-run] [-q]
```

| 參數 | 適用 | 說明 |
|---|---|---|
| `-t TEXT[=REPL]` | 都 | 要遮掉的字串，可重複。`-t Apple=VENDOR` 換成代號 |
| `-T FILE` | 都 | 從檔案讀字串，一行一個，`#` 開頭是註解，`-` 表示 stdin |
| `-w TEXT` | 都 | PDF 刪整個文字物件；純文字刪整行 |
| `--as-text` | — | 不管副檔名，一律當純文字處理 |
| `--wm-ratio R` | 都 | `-w` 的比例門檻（預設 0.8） |
| `--regex` | 都 | 把字串當 regex。PDF 以 word 為單位比對 |
| `--case-sensitive` | 都 | 區分大小寫。純文字一律有效；**PDF 只在 `--regex` 下有效** |
| `--dry-run` | 都 | 只列出會清到什麼，不寫檔 |
| `-q` | 都 | 安靜模式 |
| `--pages RANGE` | PDF | 只處理這些頁，其他頁原樣保留 |
| `--only-pages RANGE` | PDF | 輸出只保留這些頁。支援 `2-`、`-5`、`1,3,5-7` |
| `--max-area PCT` | PDF | 命中超過整頁這個百分比就跳過（預設 5） |
| `--keep-annots` | PDF | 保留註解與表單欄位（預設刪除，它們常含人名） |

**注意**：`--only-pages` 砍頁後**頁碼會往前移**，輸出的 p1 是原本的 p2。
在純文字模式下 `--pages` / `--only-pages` 會被忽略並提示（`scrub.sh` 會自動不傳）。

---

## PDF 模式還會自動清掉

- 註解與表單欄位（常含人名；`--keep-annots` 可保留）
- 內嵌附件
- Info dict metadata（Author / Title / Producer / Company）
- XMP metadata
- 書籤（標題也可能有公司名）

純文字檔沒有這些東西，所以不適用。

---

## 傳出去之前，自己再確認一次

共通：

- [ ] 檔名有沒有公司名（程式會自動處理，但還是看一眼）
- [ ] `-w` 有沒有「一行都沒刪到」或「跳過」的提示

PDF —— 工具驗證過的**只有文字層和 metadata**：

- [ ] 頁首頁尾的 logo —— 那是圖片，文字層看不到
- [ ] 圖片裡燒死的文字、圖形浮水印
- [ ] 表格和時序圖有沒有被遮破
- [ ] 損傷檢查有沒有出現 `⚠`

`scrub.sh` 跑完會列出每頁的圖片數量，每頁張數一樣的多半就是頁首 logo。

純文字 —— 取代是精確的，但這些地方容易漏：

- [ ] 程式碼註解、commit message、TODO 裡的人名
- [ ] URL、email、內網主機名（`bob@apple.example.com` 只換掉網域那半）
- [ ] 檔案路徑與 `#include` 的路徑
- [ ] 專案代號的各種縮寫與變體（`LM_ECU1`、`lm-ecu1`、`LMECU1`）

> 去識別化只是移除名字。內容本身若受 NDA 約束，拿掉公司名並不會改變這一點。
> 真的要外流，請只挑需要的那幾頁（`--only-pages`），不要整份。
