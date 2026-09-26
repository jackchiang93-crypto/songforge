# Mureka 官方 API 接通

工作台以 Mureka 官方 API 製作音樂。未提供 `MUREKA_API_KEY` 時，API 送件按鈕停用；歌曲創作、審稿和作品庫照常使用。

## 啟用

1. 在 [Mureka API 平台](https://platform.mureka.ai/docs/en/quickstart.html)建立 API key，確認 API 帳號可用額度。網頁版 Free／訂閱方案不等同於已確認的 API 額度。
2. 在本機終端執行 `python3 scripts/set_mureka_key.py`，依提示貼入 key（輸入不會回顯或記在命令歷史）；它會存入 Git 忽略的 `data/.mureka_api_key`，權限僅本人可讀。也可自行設啟動服務程序的環境變數 `MUREKA_API_KEY`。不要貼進網頁、聊天室、程式碼或 Git。設定後重啟本機服務。
3. 開啟「Mureka 待製作」，狀態會以唯讀的 `GET /v1/account/billing` 驗證 key；**驗證通過不代表 API 有餘額**，仍需到 Mureka API 平台的 Billing 頁確認。每首預設 1 個版本；增加版本可能增加費用。可在送件前預覽實際 `prompt`，並為整批設定同一聲線描述或合法取得的 Vocal ID。
4. 送件後儲存 Mureka 任務 ID，背景查詢 `GET /v1/song/query/{task_id}`。完成時將 HTTPS 音檔連結寫回作品庫，並嘗試將第一個版本保存到本機 `data/audio/`。若保存失敗，仍保留來源連結與錯誤；不會重複生成。若送件逾時或結果不明，工作標為「須核對」，**不會自動重送**，避免重複計費。服務重啟後，已有任務 ID 的工作可以續查。

## 欄位對照

| 工作台 | 官方 `POST /v1/song/generate` |
|---|---|
| 歌詞 | `lyrics`，最多 5000 字 |
| 風格、固定聲線、單曲修正、語言咬字、編曲與混音方向 | 合成 `prompt`，最多 1024 字；可預覽，不保證模型逐項遵從 |
| 人聲性別 | `gender`，僅 male／female |
| 授權人聲樣本 | 可填既有 `vocal_id`；Mureka O2 不支援 |
| 模型、版本數 | `model`、`n`（1–3，預設 1） |

標題保存在本機作品庫；官方這個 API 的送件欄位沒有 `title`。舊版 Suno 的 Weirdness、Style Influence、Duration 等開關不會偷偷換算或傳送；Exclude 會作為文字方向附於 prompt，並非 API 獨立參數。純音樂需另一個 API 端點，本階段不會將空歌詞送到「歌詞轉歌曲」端點。參考歌曲與 Remix 尚未接入；固定聲線文字不等於 Vocal ID，也不保證每首是同一個聲音。若使用 Vocal ID，須先有權使用該 15–30 秒人聲樣本並在 Mureka 官方建立 ID；本程式不會擷取或克隆他人聲音。

若用 Mureka Co 桌面版的 Gold 而非 API 額度，先按「批量下載 Mureka Co 提示詞」，逐首貼入桌面版製作。這會準備完整歌詞與製作規格，**不代表桌面版會自動提交**。完成後以任務 ID 匯入；本機下載成功時，進度卡會顯示「下載 MP3」。

目前完成程式與模擬測試，也驗證過真實 API key。Mureka Co 桌面版已成功製作一首測試歌；這不代表 API 送件已可使用，也不能單憑一次測試宣稱音樂品質適合 YouTube。待 API 有額度後仍需做 API 端對端驗收，確認回應格式、可播放音檔、權利條款與帳單，再考慮擴大到 10–15 首。

2026-09-24 首次真實送件測試：key 驗證通過，但 `POST /v1/song/generate` 收到 HTTP 429，沒有取得任務 ID；Mureka API Billing 畫面顯示餘額 $0、總消費 $0。工作保留為「送件結果不明」，不自動重試。網站版金幣與 API 餘額分開，不能用網頁金幣支付 API 請求。若使用網站免費金幣測試，需於網站介面另行製作。

同日 Mureka Co 桌面版 Pro 帳號成功生成〈留給今天的我〉，任務 ID `162665945169921`，模型 mureka-9.5，長 3 分 43 秒。使用官方查詢 API 將既有任務**只讀匯入**歌曲庫，沒有再次呼叫生成端點。MP3 已保存於 `data/audio/mureka-162665945169921.mp3`，避免 CDN 連結過期。未來桌面版生成的任務可在工作台「匯入 Mureka Co 已完成的音樂」輸入任務 ID 連回對應歌曲。

官方文件：[快速開始](https://platform.mureka.ai/docs/en/quickstart.html) · [歌詞轉歌曲](https://platform.mureka.ai/docs/api/operations/post-v1-song-generate.html) · [查詢任務](https://platform.mureka.ai/docs/api/operations/get-v1-song-query-%7Btask_id%7D.html) · [查詢帳務](https://platform.mureka.ai/docs/api/operations/get-v1-account-billing.html)
