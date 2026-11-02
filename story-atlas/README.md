# Story Atlas：翻譯流程、完整節點與進度

This is an unfinished translation workbench and a dated data snapshot, **not a translated playable game**. 此目錄不是已翻譯完成的遊戲。

- [翻譯與修正流程 / Workflow](WORKFLOW.md)
- [本次進度 / Snapshot progress](PROGRESS.md)
- [機器可讀摘要](snapshot/summary.json)
- [逐檔SHA-256清單](snapshot/manifest.json)
- [工作台程式與測試](workbench/)

## 資料範圍

完整靜態抽取，不是只上傳已走過的路線：

| 資料 | 數量／位置 |
|---|---|
| 原始location抽取 | 1,397份，`snapshot/nodes/locations/*.json.gz` |
| 事件／選項等靜態節點 | 19,168，`snapshot/nodes/static-story/graph.json.gz` |
| 候選關係 | 141,109條，同上；動態條件與目的地並非全已解析 |
| 全部原文目錄單位 | 248,199，`snapshot/nodes/translation-catalog.json.gz` |
| eligible／待判定review | 235,431／12,768；後者不能當成已可翻譯 |
| 完整抽取索引 | `snapshot/nodes/extraction-graph.json.gz` |
| 所有／無環／排除循環邊編號 | `snapshot/nodes/static-story/branches-*.json.gz` |
| 已保存正文、字典、提案、批次、分支及保留失敗 | `snapshot/progress/*.jsonl.gz` |

來源SHA-256：`f29cae3e172c73d0351ef91f11482a805e9fb1f20f403dd031547918310a61bb`。

節點名稱、source span、條件線索及原文資料保持原始抽取結構。循環／共用節點只引用，不以無限路徑展開。無環版本是依DFS順序移除回邊所得，不證明所有靜態關係都能在遊戲實際抵達；17,986條未解析關係仍保留說明。詞法字串也不全等於玩家可見正文。

## 如何讀取

Gzip只壓縮，不裁切內容。JSONL每行一筆表紀錄；JSON字串欄位（如targets、path、output）沿原欄位格式保存，需要時再`json.loads`。大型表分成約8MiB未壓縮資料的多檔；依檔名排序讀取，沒有只保留前幾筆。

```python
import gzip, json
from pathlib import Path

base = Path('story-atlas/snapshot')
with gzip.open(base / 'nodes/static-story/graph.json.gz', 'rt', encoding='utf-8') as f:
    graph = json.load(f)
print(len(graph['nodes']), len(graph['edges']))

for part in sorted((base / 'progress').glob('translations-*.jsonl.gz')):
    with gzip.open(part, 'rt', encoding='utf-8') as f:
        for line in f:
            translation = json.loads(line)
            # unit_id connects this saved target to the original catalog.
```

`progress/schema.json`列出已匯出欄位；`summary.json`記錄各表筆數、原輪狀態、失敗工作及快照時間。正式工作仍可能繼續，因此GitHub數字不是即時進度。快照保留曾失敗但已補修的標記與目前狀態，不以刪失敗紀錄偽裝零錯誤。

從儲存庫根目錄執行`python3 story-atlas/verify_snapshot.py`，可核對每個檔案的雜湊、全部source span、完整節點／邊集合、每張進度表筆數及本輪完成計數。驗證只讀資料，不連模型。

## 程式與驗證

工作台程式在`workbench/`。在備有Python及Node的環境可執行合成／mock測試；測試不需要真金鑰，也不發送付費請求：

```sh
cd story-atlas/workbench
python3 -m unittest test_repair_evidence test_extract test_static_tree test_acyclic test_adaptive_translation test_translation test_translation_http test_bazaarlink test_round_status test_workspace_location test_branch_scheduler test_branch_http test_round_runner test_handover_guard test_continue_batches test_sparse_recovery test_credit_gate test_estimate_remaining test_all_flash
node --check translate.js
```

此次修正版181項遠端測試通過，另做真失敗審計的隔離回放，沒有以mock冒充供應商結果。正式付費補修也已另核對：821／825／954／981合法正文不變，923只修1段名稱大小寫，舊審計保留。細節見[流程](WORKFLOW.md)。

這份公開快照刻意不含完整私人模型請求／回覆審計、金鑰與SQLite，**不能直接當成正式DB備份或啟動後自動續跑**。工作台啟動不會從此公開JSONL隱式匯入或開始付費；若要另建工作區，須自己配置合法來源、儲存及憑證，並明確確認費用。

## 權利與敏感資料

本目錄含抽取遊戲原文及衍生翻譯資料，內容權利仍屬各原作者；本repo的程式LICENSE不代表取得或授予全部遊戲文字的再授權。請查閱[原始遊戲與社群](../README.md#original-game-and-community)，使用者須自行確認使用／翻譯／再發布權利。沒有附QSP binary、圖片、影片、音效或玩家存檔。

不發布API金鑰、OAuth token／登入cookie、帳號、原始DB、伺服器logs、部署備份或其他私人專案設定。
