# SDL (Stream Detection & Liaison)

SDL 是一個專為多平台直播設計的**監控與通知系統**。透過整合多個第三方分析工具與 Discord Webhook API，SDL 能夠即時偵測多位實況主的開台狀態，並向指定的伺服器發送豐富的通知卡片。

## 主要功能

* **多平台狀態監控**：支援 YouTube、Rplay、Withny (前 Withlink)、FC2 Live 等多個實況平台。
* **智慧開台偵測**：背景執行輪詢，以極低的資源消耗即時取得直播間狀態。
* **Discord Webhook 通知**：支援發送包含直播標題、封面圖片與直達連結的 Rich Embed 通知卡片。
* **關鍵字過濾通知**：可自訂關鍵字（例如：「歌枠」、「Singing」），僅在標題匹配時觸發通知。
* **極簡圖形介面**：使用現代化的 Dark Mode 介面設計，方便新增、修改或批次匯入監控頻道。
* **睡眠防護管理**：在執行監控任務時，動態防止系統休眠以確保監控不中斷。

## 技術架構與使用資源

本項目採用 Python 與 CustomTkinter 構建，並在狀態偵測與協定解析中依賴/整合了以下開源與第三方資源：

1. **CustomTkinter**：用於建置現代且流暢的 GUI 介面。
2. **yt-dlp**：用於 YouTube 與 Rplay 平台的直播狀態監控、元數據提取。
3. **withny-dl**：用於 Withny (withny.fun) 平台直播狀態檢查與集中式監控。
4. **fc2-live-dl**：用於 FC2 Live 直播間開台與流狀態偵測。
5. **ytarchive**：用於 YouTube 直播流的監控與輔助解析。
6. **FFmpeg**：用於流協定封裝與多媒體容器格式檢測。

## 系統需求

* Windows 10/11
* Python 3.10+

## 授權說明

本項目僅供學習與交流使用，相關第三方工具（如 `yt-dlp` 等）之智慧財產權與授權條款歸原作者所有。
