"""
fetchers — 多源内容抓取
所有 fetcher 必须遵守统一接口:
    fetch() -> list[dict]
返回的每条 record 至少包含:
    {
      "id":           唯一 id,
      "source":       "Economist" | "Reader's Digest" | "China Daily" | ...,
      "url":          原文链接,
      "title":        英文标题,
      "title_zh":     中文标题(可空,稍后由摘要模块补),
      "published":    ISO 字符串,
      "body":         原文正文(严格保留原文,不做改写),
      "summary":      摘要(如来源未提供,可为空)
    }

⚠️ 严格约定:不得修改 body / title 中的原文措辞、标点、大小写;
   只允许抽取与清洗(去广告/导航/版权)。
"""