# Girl Life HTML 建置器

English documentation: [README.md](README.md)

使用官方 QSPFoundation qSpider 播放器，將原始英文版 Girl Life QSP 遊戲打包成可在瀏覽器執行的本機網站。

此 repo 只包含建置工具，不包含遊戲本體、圖片、音效或生成後的遊戲包。請自行放入你有權使用的遊戲與素材。

## 需求

- macOS 或 Windows 10/11
- 英文 `.qsp` 遊戲檔
- `images/` 資料夾或可辨識的圖片 ZIP
- `sound/` 資料夾或選用的音效 ZIP
- 首次建置需要網路，以下載 qSpider v1.3.1 播放器

啟動器會先檢查是否已有 Python 3.10 以上版本。若沒有，會先詢問是否從 [python.org](https://www.python.org/downloads/) 下載官方安裝程式；輸入 `y` 才繼續，直接按 Enter 則取消。下載後會驗證安裝程式簽章才開啟或執行。macOS 會打開系統安裝介面；Windows 只安裝到目前使用者，不要求系統管理員權限。

不需要安裝第三方 Python 套件。

## 資料夾配置

將建置器檔案與英文 `.qsp`、素材放在一起：

```text
GirlLife/
├── Build-GirlLife-HTML.bat
├── Build-GirlLife-HTML.ps1
├── Build-GirlLife-HTML.sh
├── build_girllife_html.py
├── girllife_server.py       # 專案維護者部署的雲端存檔服務
├── Girl Life 0.9.9.2.qsp
├── images/
└── sound/                 # 可選；未提供時遊戲沒有音效
```

建置器只會選擇檔名不含 `_cn` 的英文 `.qsp`。若找到多個，會詢問要使用哪個。若遊戲資料夾沒有 `sound/`，但上一層只有一個相鄰的 `sound/` 資料夾，也會採用它。

## 建置與執行

### macOS

在 Terminal 執行：

```sh
./Build-GirlLife-HTML.sh
```

如果系統提示腳本沒有執行權限：

```sh
chmod +x Build-GirlLife-HTML.sh
./Build-GirlLife-HTML.sh
```

### Windows

雙擊 `Build-GirlLife-HTML.bat`。它會呼叫附帶的 PowerShell 啟動器，檢查 Python；需要下載時先詢問同意，再執行建置。

建置器會在同一層建立 `<英文遊戲檔名>_HTML` 資料夾，統計缺失素材，接著啟動動態埠號的本機網站、顯示網址並嘗試開啟瀏覽器。下載遊戲本體或素材時會顯示進度條，下載完成後在準備遊戲期間顯示已等待秒數；建置器會建立可重用的 QSP gzip 壓縮副本，支援的伺服器會自動使用，將目前約 91 MB 的遊戲檔傳輸量減至約 11 MB，原始 `.qsp` 保持不變。瀏覽器不會預載整個素材庫，而是以有限佇列預熱目前場景和目前顯示的選項按鈕已引用的圖片／影片；影片先預載 metadata，其他素材仍會在遊戲要求時載入。窄螢幕（CSS 寬度不超過 900px）下，Classic 與 Aero 都會配合視窗尺寸、保持文字和選項可捲動，並提供適合觸控的控制項；圖片與影片會縮放至可用空間。Classic 手機版中，主視窗與選項視窗各佔上下半部；開啟「物品」時取代上半部，開啟「狀態」時取代下半部。兩個面板各有一顆開關，位於右上與右下，同一時間只會開啟一個。彈出媒體會置中顯示在深色遮罩上；點實際圖片／影片以外的空白處只關閉該媒體視窗。已用手機與平板尺寸的瀏覽器視窗測試，尚未以實體 iPhone/Safari 驗收。遊玩期間請保留啟動器視窗開啟；按 `Ctrl+C` 停止網站。

### 目前手機版驗證進度（2026-10-30）

已在瀏覽器以 320×568、390×844、430×932、768×1024 測試 Classic 上下分區及 Stats 開關。測試時遊戲尚未顯示物品，因此 Items 面板只用測試 DOM 確認版面，實際持有物品時的內容尚未驗證。尚未以實體 iPhone/Safari 或 Windows 建置驗收。

若有效的輸出資料夾已存在，建置器會保留它，改為檢查並啟動既有產物，不會覆寫。

## 雲端存檔

生成的遊戲會將編號存檔、命名存檔與快速存檔放在 GirlLife 雲端服務 `girllife.mewcat.me`。雲端存檔必須先有帳號，因此打開 **Saves** 面板或標題畫面的 **LOAD** 按鈕時，會先要求帳號。在這台裝置從未登入過時，對話框會以 **Create account** 為主，並把它排在 **Sign in** 前面，直接引導新玩家註冊；已有帳號的人仍可按 **Sign in**，而按 **Create account** 時若名稱已存在，對話框會自動切回登入模式並保留剛輸入的內容。建立帳號只需輸入名稱與一個密碼（不用再打第二次確認）。其他裝置登入同一帳號後即可讀取相同存檔。登入狀態會以瀏覽器 `localStorage` 記住，重新載入頁面或重開瀏覽器都不用再登入，直到伺服器端的 7 天 session 到期；要換帳號或共用電腦前，可在暫停選單按 **Cloud account** 再按 **Sign out** 清除。帳號名稱限 3–32 個英文字母、數字、句點、連字號或底線；密碼沒有最小長度限制，只要不是空的、且不超過 1024 個字元即可，並只以加鹽 scrypt 雜湊儲存。雲端存檔需要網路，遊戲本身仍可在本機網站執行。

密碼以加鹽 scrypt 雜湊保存；資料庫也只保存登入 token 的雜湊。登入後 token 由瀏覽器 `localStorage` 記住（key `girllife.cloud.session`），因此共用電腦請記得登出；登出只會清掉本機記錄，伺服器上的 session 仍保留到 7 天到期。存檔內容存在伺服器 SQLite 資料庫，未做應用層加密。單一存檔上限 4 MB，每個帳號最多 512 個存檔或總計 256 MB。重要存檔請另行備份；服務可用性取決於雲端主機。

### 執行雲端存檔服務

`girllife_server.py` 同一個行程同時提供遊戲檔案與 `/api/...`。它讓 HTTP/1.1 連線保持開啟、只在啟動時建立一次 SQLite schema（不再每個請求都跑建表）、每個工作執行緒重複使用一條 WAL 連線，並用資料庫 trigger 維護 `account_usage` 表中的存檔數與位元組總量，存檔時不必再掃描整個帳號的資料列。超過 `GIRLLIFE_MAX_CONCURRENT_REQUESTS`（預設 96）的工作會直接回 `503` 與 `Retry-After`，不會無上限開執行緒；閒置連線在 `GIRLLIFE_REQUEST_TIMEOUT` 秒（預設 65）後關閉。其他設定：`GIRLLIFE_SAVE_DATABASE`（資料庫路徑）、`GIRLLIFE_PORT`（預設 8081）。若前面有反向代理，需在代理端啟用 HTTP/1.1 與 upstream keep-alive，連線重用才會生效。舊資料庫在第一次啟動時會就地升級，並補齊用量計數。

## 素材偵測與缺失檔案

- 有 `images/` 時優先使用資料夾，不解壓圖片 ZIP；沒有資料夾時可選擇匹配的 ZIP 並安全解壓。
- 有 `sound/` 時使用資料夾；否則可選擇音效 ZIP。兩者皆無時，遊戲仍可執行但沒有音效。
- 遊戲資料夾或上一層中，檔名同時包含 `image` 與 `bugfix`、`patch`、`update` 任一字樣的 ZIP 會視為圖片更新包。重複內容會去重；若同時有不同更新包會停止，避免套錯版本。
- 缺少的素材引用會略過，不會建立假檔。
- 最後的統計會列出可辨識的 `images/`、`sound/` 固定路徑之唯一缺檔數、引用次數，以及圖片／影片／音效分類。由 QSP 變數或字串動態組合的路徑無法由靜態掃描計數。
- ZIP 解壓會拒絕不安全路徑、符號連結、重複路徑和超過大小限制的壓縮檔。不支援 RAR 或 7z。

原始 `.qsp`、素材資料夾和素材壓縮檔會保留不變。若磁碟支援，輸出素材會使用硬連結；否則複製。圖片更新只套用到輸出包。

## 修復範圍

此工具以 qSpider 執行原始 QSP，並不會改寫遊戲或修復 QSP 內部的遊戲邏輯缺陷。缺失素材會回報並略過；原始遊戲邏輯中的問題仍然存在。

## 授權

此 repo 未對建置器額外授予授權。qSpider 使用 MIT License；完整授權文字會放在生成包的 `QSPIDER-LICENSE.txt`。請參閱 [qSpider v1.3.1 release](https://github.com/QSPFoundation/qspider/releases/tag/v1.3.1)。
