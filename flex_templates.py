from typing import List, Tuple, Dict, Any
from linebot.v3.messaging.models import FlexMessage, FlexContainer


# 職位確立
def create_position_message() -> FlexMessage:
    """建立 Flex Message 讓受試者選擇在實驗中的職位（行銷、營運、人資）。"""
    title = "【職位選擇】確立團隊角色"
    instruction = "請選擇你在公司擔任的職位"
    
    # 定義職位選項 (標籤, 觸發的訊息文字)
    options = [
        ("人資長", "我的職位是人資長"), 
        ("營運長", "我的職位是營運長"), 
        ("行銷長", "我的職位是行銷長")
    ]

    # 按鈕內容 (以 dict 形式定義 Flex 元件)
    button_components: List[Dict[str, Any]] = [
        {
            "type": "button",
            "style": "primary",
            "height": "sm",
            "margin": "md",
            "color": "#1976D2",
            "action": {
                "type": "message",
                "label": label,
                "text": text,
            },
        }
        for label, text in options
    ]

    body_content: Dict[str, Any] = {
        "type": "box",
        "layout": "vertical",
        "spacing": "md",
        "contents": [
            {"type": "text", "text": title, "weight": "bold", "size": "lg", "color": "#0D47A1", "align": "start"},
            {"type": "separator", "margin": "md", "color": "#E0E0E0"},
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "backgroundColor": "#E3F2FD",
                "cornerRadius": "8px",
                "paddingAll": "10px",
                "contents": [
                    {"type": "text", "text": instruction, "wrap": True, "size": "md", "color": "#333333"}
                ],
            },
            {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "margin": "md",
                "contents": button_components,
            },
        ],
    }

    bubble: Dict[str, Any] = {
        "type": "bubble",
        "styles": {"body": {"backgroundColor": "#FFFFFF"}},
        "body": body_content,
    }

    return FlexMessage(
        alt_text=f"{title} - 請點擊按鈕選擇您的職位",
        contents=FlexContainer.from_dict(bubble),
    )


# 初始投票
def create_vote_flex_message(phase: str, bot_role: str) -> FlexMessage:
    """建立 Flex Message 讓群組成員進行初始投票。"""
    if phase == 'warmup':
        title = "【暖身活動】Team Building"
        instruction = "為了讓活動安排更符合大家期待，請分享對這次團建活動的初步傾向。"
        options = [("玩桌遊", "我選玩桌遊"), ("公益淨灘", "我選公益淨灘"),("包場看電影", "我選包場看電影")]
    else:  # main
        title = "【正式實驗】CFO 候選人選擇"
        instruction = "在我們開始深入討論之前，請先選擇目前最支持的 CFO 候選人，這將有助於我們掌握團隊的初始意見分佈。"
        options = [("Amy", "我選 Amy"), ("Sally", "我選 Sally"), ("Nancy", "我選 Nancy")]

    # 按鈕內容 (以 dict 形式定義 Flex 元件)
    button_components: List[Dict[str, Any]] = [
        {
            "type": "button",
            "style": "primary",
            "height": "sm",
            "margin": "md",
            "color": "#1976D2",
            "action": {
                "type": "message",
                "label": label,
                "text": text,
            },
        }
        for label, text in options
    ]

    if not button_components:
        button_components = [
            {
                "type": "text",
                "text": "目前沒有可選項",
                "wrap": True,
                "size": "md",
            }
        ]

    body_content: Dict[str, Any] = {
        "type": "box",
        "layout": "vertical",
        "spacing": "md",
        "contents": [
            {"type": "text", "text": title, "weight": "bold", "size": "lg", "color": "#0D47A1", "align": "center"},
            {"type": "separator", "margin": "md", "color": "#E0E0E0"},
            {
                "type": "box",
                "layout": "vertical",
                "margin": "lg",
                "backgroundColor": "#E3F2FD",
                "cornerRadius": "8px",
                "paddingAll": "10px",
                "contents": [
                    {"type": "text", "text": instruction, "wrap": True, "size": "md", "color": "#333333"}
                ],
            },
            {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "margin": "md",
                "contents": button_components,
            },
        ],
    }

    bubble: Dict[str, Any] = {
        "type": "bubble",
        "styles": {"body": {"backgroundColor": "#FFFFFF"}},
        "body": body_content,
    }

    return FlexMessage(
        alt_text=f"{title} - 請點擊按鈕進行投票",
        contents=FlexContainer.from_dict(bubble),
    )

# 共識確認
def create_consensus_check_message(phase: str, elapsed_time: str) -> FlexMessage:
    """建立 Flex Message 詢問團隊是否已達成共識。"""
    note_text = ""
    if phase == 'main':
        note_text = "小提醒：我們的最終目標是向執行長推薦最合適的 CFO 人選。若團隊充分討論並成功選出最佳候選人，將可獲得額外報酬。"

    # 先建立一定會存在的元件
    body_contents_list: List[Dict[str, Any]] = [
        {
            "type": "box",
            "layout": "horizontal",
            "margin": "md",
            "contents": [
                {"type": "text", "text": f"目前已討論時間：{elapsed_time}", "weight": "bold", "size": "lg", "color": "#0D47A1"},
            ],
        },
        {"type": "separator", "margin": "md"},
        {
            "type": "text",
            "text": "為了確保效率，我們可以來快速確認一下彼此的共識。",
            "backgroundColor": "#E3F2FD",
            "wrap": True,
            "size": "md",
            "color": "#333333",
            "margin": "md",
        },
    ]

    # 💡【修改】只有當 note_text 有內容時，才將其加入列表
    if note_text:
        body_contents_list.append({
            "type": "text",
            "text": note_text,
            "wrap": True,
            "size": "sm",
            "color": "#7a7a7a",
            "margin": "md",
        })

    # 最後加入按鈕區塊
    body_contents_list.append({
        "type": "box",
        "layout": "vertical",
        "spacing": "sm",
        "margin": "md",
        "contents": [
            {
                "type": "button",
                "style": "primary",
                "height": "sm",
                "margin": "md",
                "color": "#1976D2",
                "action": {"type": "message", "label": "✅ 已有共識", "text": "已有共識"},
            },
            {
                "type": "button",
                "style": "primary",
                "height": "sm",
                "margin": "md",
                "color": "#a2a7ab",
                "action": {"type": "message", "label": "💬 需要再討論", "text": "需要再討論"},
            },
        ],
    })

    # 將組合好的列表放入 body
    body_content: Dict[str, Any] = {
        "type": "box",
        "layout": "vertical",
        "spacing": "md",
        "contents": body_contents_list,
    }

    bubble: Dict[str, Any] = {
        "type": "bubble",
        "styles": {"body": {"backgroundColor": "#FFFFFF"}},
        "body": body_content,
    }

    return FlexMessage(
        alt_text="共識檢查",
        contents=FlexContainer.from_dict(bubble),
    )


# 最終選擇
def create_final_selection_message(phase: str) -> FlexMessage:
    """建立 Flex Message 讓團隊進行最終選擇。"""
    if phase == 'warmup':
        title = "【最終決策】Team Building 選擇"
        instruction = "請選擇下週最適合團隊參與的 Team Building 活動。"
        options = [ ("玩桌遊", "我們最終選擇【玩桌遊】"), ("公益淨灘", "我們最終選擇【公益淨灘】"),("包場看電影", "我們最終選擇【包場看電影】")]
    else:
        title = "【最終決策】CFO 候選人選擇"
        instruction = "請團隊推薦最適合公司的 CFO 。"
        options = [("Amy", "我們最終選擇 Amy"), ("Sally", "我們最終選擇 Sally"), ("Nancy", "我們最終選擇 Nancy")]

    button_components: List[Dict[str, Any]] = [
        {
            "type": "button",
            "style": "primary",
            "height": "sm",
            "margin": "md",
            "color": "#1976D2",
            "action": {"type": "message", "label": label, "text": text},
        }
        for label, text in options
    ]

    body_content: Dict[str, Any] = {
        "type": "box",
        "layout": "vertical",
        "spacing": "md",
        "contents": [
            {"type": "text", "text": title, "weight": "bold", "size": "lg", "color": "#0D47A1", "align": "start"},
            {"type": "separator", "margin": "md", "color": "#E0E0E0"},
            {
                "type": "box",
                "layout": "vertical",
                "margin": "md",
                "backgroundColor": "#E3F2FD",
                "cornerRadius": "8px",
                "paddingAll": "10px",
                "contents": [
                    # {"type": "text", "text": "✅ 決策階段", "weight": "bold", "size": "sm", "color": "#555555"},
                    {"type": "text", "text": instruction, "wrap": True, "size": "md", "margin": "sm", "color": "#333333"},
                ],
            },
            {"type": "box", 
            "layout": "vertical", 
            "spacing": "sm", 
            "margin": "md",
            "contents": button_components},
        ],
    }

    bubble: Dict[str, Any] = {
        "type": "bubble",
        "styles": {"body": {"backgroundColor": "#FFFFFF"}},
        "body": body_content,
    }

    return FlexMessage(
        alt_text=title,
        contents=FlexContainer.from_dict(bubble),
    )

# 為了方便測試，這裡可以添加一個簡單的測試調用 (不會在生產環境運行)
if __name__ == '__main__':
    import json
    vote_msg = create_vote_flex_message('main', '中立觀察者')
    print("--- 初始投票 JSON (模型轉換後) ---")
    print(json.dumps(vote_msg.contents.to_dict(), indent=2, ensure_ascii=False))

    check_msg = create_consensus_check_message('00:35')
    print("\n--- 共識檢查 JSON (模型轉換後) ---")
    print(json.dumps(check_msg.contents.to_dict(), indent=2, ensure_ascii=False))
