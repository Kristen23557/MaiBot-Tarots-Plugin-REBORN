from typing import List, Tuple, Type, Any, Dict, Optional
import random
import asyncio
import json
import base64
import toml
import tomlkit
import traceback
from pathlib import Path
import os

# 导入新版插件系统
from src.plugin_system import BasePlugin, register_plugin, ComponentInfo, ActionActivationType
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.apis import llm_api
from src.common.logger import get_logger

logger = get_logger("tarots")

class TarotsAction(BaseAction):
    """塔罗牌占卜动作 - 直接发送图片和简短解读"""
    
    action_name = "tarots"
    
    # 激活配置
    activation_type = ActionActivationType.KEYWORD
    activation_keywords = ["抽一张塔罗牌", "抽张塔罗牌", "塔罗占卜", "塔罗牌", "占卜", "算一卦"]
    keyword_case_sensitive = False

    # 动作描述
    action_description = "执行塔罗牌占卜，立即发送牌面图片并进行简短解读"
    action_parameters = {
        "card_type": "塔罗牌的抽牌范围，必填，只能填一个参数，这里请根据用户的要求填'全部'或'大阿卡纳'或'小阿卡纳'，如果用户的要求并不明确，默认填'全部'",
        "formation": "塔罗牌的抽牌方式，必填，只能填一个参数，这里请根据用户的要求填'单张'或'圣三角'或'时间之流'或'四要素'或'五牌阵'或'吉普赛十字'或'马蹄'或'六芒星'，如果用户的要求并不明确，默认填'单张'",
        "target_user": "提出抽塔罗牌的用户名"
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化基本路径
        self.base_dir = Path(__file__).parent.absolute()

        # 扫描并更新可用牌组
        self.config = self._load_config()
        self._update_available_card_sets()

        # 初始化路径
        self.using_cards = self.config["cards"].get("using_cards", 'bilibili')
        if not self.using_cards:
            self.cache_dir = self.base_dir / "tarots_cache" / "default"
        else:
            self.cache_dir = self.base_dir / "tarots_cache" / self.using_cards
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 加载卡牌数据
        self.card_map: Dict = {}
        self.formation_map: Dict = {}
        self._load_resources()

    def _load_resources(self):
        """同步加载资源文件"""
        try:
            if not self.using_cards:
                logger.info("没有加载到任何可用牌组")
                return
            
            # 加载卡牌数据
            cards_json_path = self.base_dir / f"tarot_jsons/{self.using_cards}/tarots.json"
            if cards_json_path.exists():
                with open(cards_json_path, encoding="utf-8") as f:
                    self.card_map = json.load(f)
            else:
                logger.error(f"卡牌数据文件不存在: {cards_json_path}")
                return
            
            # 加载牌阵配置
            formation_json_path = self.base_dir / "tarot_jsons/formation.json"
            if formation_json_path.exists():
                with open(formation_json_path, encoding="utf-8") as f:
                    self.formation_map = json.load(f)
            else:
                logger.error(f"牌阵配置文件不存在: {formation_json_path}")
                return
                
            logger.info(f"已加载{self.card_map['_meta']['total_cards']}张卡牌和{len(self.formation_map)}种抽牌方式")
        except Exception as e:
            logger.error(f"资源加载失败: {str(e)}")
            raise

    async def execute(self) -> Tuple[bool, str]:
        """执行塔罗牌占卜 - 直接发送图片和简短解读"""
        try:
            if not self.card_map:
                await self.send_text("❌ 没有可用的牌组，无法进行占卜")
                return False, "没有牌组"
            
            logger.info("开始执行塔罗占卜")
            
            # 解析参数
            request_type = self.action_data.get("card_type", "全部")
            formation_name = self.action_data.get("formation", "单张")
            target_user = self.action_data.get("target_user", "用户")
            
            # 参数映射（支持简写）
            request_type = self._map_card_type(request_type)
            formation_name = self._map_formation(formation_name)
            
            logger.info(f"占卜参数: card_type={request_type}, formation={formation_name}, target_user={target_user}")
            
            # 参数校验
            if request_type not in ["全部", "大阿卡纳", "小阿卡纳"]:
                await self.send_text("❌ 不存在的抽牌范围")
                return False, "参数错误"
                
            if formation_name not in self.formation_map:
                await self.send_text("❌ 不存在的抽牌方法")
                return False, "参数错误"
    
            # 获取牌阵配置
            formation = self.formation_map[formation_name]
            cards_num = formation["cards_num"]
            is_cut = formation["is_cut"]
            represent_list = formation["represent"]
    
            # 获取有效卡牌范围
            valid_ids = self._get_card_range(request_type)
            if not valid_ids:
                await self.send_text("❌ 当前牌组配置错误")
                return False, "参数错误"
    
            # 抽牌逻辑
            selected_ids = random.sample(valid_ids, cards_num)
            if is_cut:
                selected_cards = [
                    (cid, random.random() < 0.5)  # 切牌时50%概率逆位
                    for cid in selected_ids
                ]
            else:
                selected_cards = [
                    (cid, False)  # 不切牌时全部正位
                    for cid in selected_ids
                ]
    
            logger.info(f"抽中卡牌: {selected_cards}")
            
            # 1. 立即发送每张牌面图片
            card_details = []
            sent_images = []
            
            for idx, (card_id, is_reverse) in enumerate(selected_cards):
                card_data = self.card_map.get(card_id, {})
                if not card_data:
                    logger.warning(f"卡牌ID不存在: {card_id}")
                    continue
                    
                # 发送图片
                image_sent = await self._send_card_image(card_id, is_reverse)
                if image_sent:
                    sent_images.append(card_id)
                    await asyncio.sleep(0.5)  # 防止消息频率限制
                
                # 收集卡牌信息用于解读
                card_info = card_data.get("info", {})
                pos_name = self._get_position_name(represent_list, idx, formation_name)
                pos_meaning = self._get_position_meaning(represent_list, idx, formation_name)
                
                card_details.append({
                    'position': pos_name,
                    'name': card_data.get('name', '未知'),
                    'is_reverse': is_reverse,
                    'description': card_info.get('reverseDescription' if is_reverse else 'description', '暂无描述'),
                    'position_meaning': pos_meaning
                })

            if not sent_images:
                await self.send_text("❌ 卡牌图片发送失败，无法进行占卜")
                return False, "图片发送失败"

            # 2. 生成并发送简短文字解读
            await asyncio.sleep(1)  # 给用户一点时间看图片
            
            try:
                short_interpretation = await self._generate_short_interpretation(card_details, formation_name, target_user)
                await self.send_text(short_interpretation)
                    
            except Exception as e:
                logger.error(f"解读生成失败: {e}")
                # 发送最简解读
                card_names = [card['name'] for card in card_details]
                basic_text = f"✨ 为{target_user}抽到了：{'、'.join(card_names)}～愿塔罗牌给你带来好运！"
                await self.send_text(basic_text)

            logger.info("塔罗牌占卜执行成功")
            return True, f"已为{target_user}抽取塔罗牌"
            
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"执行失败: {error_msg}")
            await self.send_text(f"❌ 占卜失败: {str(e)}")
            return False, "执行错误"

    async def _generate_short_interpretation(self, card_details: List[Dict], formation_name: str, user_nickname: str) -> str:
        """生成简短自然的解读"""
        try:
            # 使用AI生成简短解读
            prompt = self._build_short_prompt(card_details, formation_name, user_nickname)
            
            models = llm_api.get_available_models()
            chat_model_config = models.get("replyer")

            success, thinking_result, _, _ = await llm_api.generate_with_model(
                prompt, model_config=chat_model_config, request_type="tarots_interpretation"
            )

            if success and len(thinking_result) < 100:  # 确保回复简短
                return thinking_result
            else:
                # 如果AI回复太长或失败，使用备用简短解读
                return self._generate_fallback_short_interpretation(card_details, formation_name, user_nickname)
                
        except Exception as e:
            logger.error(f"AI解读生成错误: {e}")
            return self._generate_fallback_short_interpretation(card_details, formation_name, user_nickname)

    def _build_short_prompt(self, card_details: List[Dict], formation_name: str, user_nickname: str) -> str:
        """构建简短解读提示词"""
        cards_info = ""
        for card in card_details:
            status = "逆位" if card['is_reverse'] else "正位"
            cards_info += f"{card['name']}（{status}）"

        prompt = f"""请用轻松自然的语气为{user_nickname}解读塔罗牌，保持非常简短（2-3句话）。

抽到的牌：{cards_info}

请用1句话总结牌面意思，再用1句话给出实用建议。
就像朋友聊天一样自然，不要用专业术语，不要讲大道理。
可以带点小幽默，保持温暖亲切。

你的解读（请控制在50字以内）："""

        return prompt

    def _generate_fallback_short_interpretation(self, card_details: List[Dict], formation_name: str, user_nickname: str) -> str:
        """生成备用简短解读"""
        card_names = []
        reverse_count = 0
        
        for card in card_details:
            status = "逆位" if card['is_reverse'] else "正位"
            card_names.append(f"{card['name']}（{status}）")
            if card['is_reverse']:
                reverse_count += 1
    
        card_list = "、".join(card_names)
        
        # 根据逆位牌数量给出不同语气
        if reverse_count == len(card_details):
            # 全是逆位
            interpretations = [
                f"🌙 哇{user_nickname}，抽到了{card_list}～看来最近需要放慢脚步调整一下呢！",
                f"🌀 {user_nickname}的牌面是{card_list}～能量有点特别，给自己多点耐心哦！",
                f"💫 抽到{card_list}呢{user_nickname}～最近可能有些小挑战，但都是成长的机会！"
            ]
        elif reverse_count > 0:
            # 有逆位牌
            interpretations = [
                f"✨ {user_nickname}抽到了{card_list}～牌面有些小波动，不过问题不大！",
                f"🌟 为{user_nickname}抽到{card_list}～有些地方可能需要微调，但整体还不错！",
                f"🔮 {user_nickname}的塔罗牌是{card_list}～能量有起有伏，保持平常心就好～"
            ]
        else:
            # 全是正位
            interpretations = [
                f"💖 {user_nickname}抽到了{card_list}～牌面能量超棒，继续保持！",
                f"⭐ 哇{user_nickname}，{card_list}～都是正位呢，最近运势不错哦！",
                f"🌞 {user_nickname}的塔罗牌是{card_list}～能量很正向，放心前进吧！"
            ]
        
        return random.choice(interpretations)

    def _map_card_type(self, card_type: str) -> str:
        """映射卡牌类型参数"""
        mapping = {
            "全": "全部", "全部": "全部",
            "大": "大阿卡纳", "大阿": "大阿卡纳", "大阿卡纳": "大阿卡纳",
            "小": "小阿卡纳", "小阿": "小阿卡纳", "小阿卡纳": "小阿卡纳"
        }
        return mapping.get(card_type, card_type)

    def _map_formation(self, formation: str) -> str:
        """映射牌阵参数"""
        mapping = {
            "单": "单张", "单张": "单张",
            "圣": "圣三角", "圣三角": "圣三角",
            "时": "时间之流", "时间": "时间之流", "时间之流": "时间之流",
            "四": "四要素", "四要素": "四要素",
            "五": "五牌阵", "五牌": "五牌阵", "五牌阵": "五牌阵",
            "吉": "吉普赛十字", "吉普赛": "吉普赛十字", "吉普赛十字": "吉普赛十字",
            "马": "马蹄", "马蹄": "马蹄",
            "六": "六芒星", "六芒": "六芒星", "六芒星": "六芒星"
        }
        return mapping.get(formation, formation)

    async def _send_card_image(self, card_id: str, is_reverse: bool) -> bool:
        """发送卡牌图片"""
        try:
            card_data = self.card_map.get(card_id, {})
            if not card_data:
                logger.error(f"卡牌ID不存在: {card_id}")
                return False
                
            card_name = card_data.get("name", "")
            if not card_name:
                logger.error(f"卡牌名称不存在: {card_id}")
                return False
            
            # 构建本地图片路径
            image_filename = self._get_local_image_filename(card_name, is_reverse)
            image_path = self.base_dir / f"tarot_jsons/{self.using_cards}" / image_filename
            
            if not image_path.exists():
                logger.error(f"本地图片文件不存在: {image_path}")
                return False
                
            # 读取图片文件并转换为base64
            with open(image_path, "rb") as f:
                img_data = f.read()
            
            # 将图片数据转换为base64字符串
            img_base64 = base64.b64encode(img_data).decode('utf-8')
            
            # 发送图片
            await self.send_image(img_base64)
            
            logger.info(f"成功发送本地图片: {image_filename}")
            return True

        except Exception as e:
            logger.error(f"发送本地图片失败: {str(e)}")
            return False

    def _get_local_image_filename(self, card_name: str, is_reverse: bool) -> str:
        """根据卡牌名称和位置构建本地图片文件名"""
        # 处理卡牌名称中的特殊字符和空格
        cleaned_name = card_name.replace("ACE", "王牌").replace("2", "二").replace("3", "三").replace("4", "四").replace("5", "五").replace("6", "六").replace("7", "七").replace("8", "八").replace("9", "九").replace("10", "十")
        
        # 构建文件名
        position = "逆位" if is_reverse else "正位"
        filename = f"{cleaned_name}{position}.jpg"
        
        return filename

    def _get_card_range(self, card_type: str) -> list:
        """获取卡牌范围"""
        if card_type == "大阿卡纳":
            return [str(i) for i in range(22)]
        elif card_type == "小阿卡纳":
            return [str(i) for i in range(22, 78)]
        return [str(i) for i in range(78)]

    def _get_position_name(self, represent_list: List, idx: int, formation_name: str) -> str:
        """安全获取位置名称"""
        try:
            if (isinstance(represent_list, list) and len(represent_list) > 0 and 
                isinstance(represent_list[0], list) and idx < len(represent_list[0])):
                return represent_list[0][idx]
        except (IndexError, TypeError):
            pass
        return f"位置{idx+1}"

    def _get_position_meaning(self, represent_list: List, idx: int, formation_name: str) -> str:
        """安全获取位置含义"""
        try:
            if (isinstance(represent_list, list) and len(represent_list) > 1 and 
                isinstance(represent_list[1], list) and idx < len(represent_list[1])):
                return represent_list[1][idx]
        except (IndexError, TypeError):
            pass
        
        # 根据牌阵类型提供默认含义
        default_meanings = {
            "单张": "当前状况",
            "圣三角": ["过去", "现在", "未来"],
            "时间之流": ["过去", "现在", "未来"],
            "四要素": ["行动", "情感", "思想", "物质"],
            "五牌阵": ["现状", "挑战", "选择", "环境", "结果"],
            "吉普赛十字": ["现状", "障碍", "目标", "过去", "未来"],
            "马蹄": ["过去", "现在", "隐藏", "环境", "期望", "结果"],
            "六芒星": ["过去", "现在", "未来", "原因", "环境", "结果"]
        }
        
        if formation_name in default_meanings:
            meanings = default_meanings[formation_name]
            if isinstance(meanings, list) and idx < len(meanings):
                return meanings[idx]
            elif isinstance(meanings, str):
                return meanings
        
        return "未知"

    def _load_config(self) -> Dict[str, Any]:
        """加载配置"""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "config.toml")
            
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = toml.load(f)
            
            config = {
                "permissions": {
                    "admin_users": config_data.get("permissions", {}).get("admin_users", [])
                },
                "proxy": {
                    "enable_proxy": config_data.get("proxy", {}).get("enable_proxy", False),
                    "proxy_url": config_data.get("proxy", {}).get("proxy_url", "")
                },
                "cards": {
                    "using_cards": config_data.get("cards", {}).get("using_cards", 'bilibili'),
                    "use_cards": config_data.get("cards", {}).get("use_cards", ['bilibili','east'])
                },
                "adjustment": {
                    "enable_original_text": config_data.get("adjustment", {}).get("enable_original_text", False),
                    "ai_interpretation": config_data.get("adjustment", {}).get("ai_interpretation", True)
                }
            }
            return config
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            # 返回默认配置
            return {
                "permissions": {"admin_users": []},
                "proxy": {"enable_proxy": False, "proxy_url": ""},
                "cards": {"using_cards": "bilibili", "use_cards": ["bilibili", "east"]},
                "adjustment": {
                    "enable_original_text": False,
                    "ai_interpretation": True
                }
            }
        
    def get_available_card_type(self, user_requested_type):
        """获取当前牌组支持的卡牌类型"""
        supported_type = self.card_map.get("_meta", {}).get("card_types", "")
        if supported_type == '全部' or user_requested_type == supported_type:
            return user_requested_type
        else:
            return supported_type
        
    def _update_available_card_sets(self):
        """更新配置文件中的可用牌组列表"""
        try:
            current_using = self.config["cards"].get("using_cards", "")
            available_sets = self._scan_available_card_sets()

            if not current_using or current_using not in available_sets:
                new_using = available_sets[0] if available_sets else ""
                if new_using:
                    logger.warning(f"自动切换牌组至: {new_using}")
                    self.set_card(new_using)

            if available_sets:
                self.set_cards(available_sets)
                logger.info(f"可用牌组: {available_sets}")
            else:
                logger.error("未发现任何可用牌组")
                self.set_card("")
                self.set_cards([])
                
            self.config = self._load_config()
        except Exception as e:
            logger.error(f"更新牌组配置失败: {e}")
        
    def _scan_available_card_sets(self) -> List[str]:
        """扫描可用牌组"""
        try:
            tarot_jsons_dir = self.base_dir / "tarot_jsons"
            available_sets = []
            
            if not tarot_jsons_dir.exists():
                logger.warning(f"tarot_jsons目录不存在: {tarot_jsons_dir}")
                return []
            
            for item in tarot_jsons_dir.iterdir():
                if item.is_dir():
                    tarots_json_path = item / "tarots.json"
                    if tarots_json_path.exists():
                        available_sets.append(item.name)
                        logger.info(f"发现牌组: {item.name}")
            
            return available_sets
        except Exception as e:
            logger.error(f"扫描牌组失败: {e}")
            return []
        
    def set_cards(self, cards: List):
        """更新可用牌组配置"""
        try:
            config_path = self.base_dir / "config.toml"
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = tomlkit.load(f)
                config_data["cards"]["use_cards"] = tomlkit.array(cards)
            
            with open(config_path, 'w', encoding='utf-8') as f:
                tomlkit.dump(config_data, f)
                
        except Exception as e:
            logger.error(f"更新牌组配置失败: {e}")

    def set_card(self, cards: str):
        """设置当前使用牌组"""
        try:
            config_path = self.base_dir / "config.toml"
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = tomlkit.load(f)
                config_data["cards"]["using_cards"] = cards
            
            with open(config_path, 'w', encoding='utf-8') as f:
                tomlkit.dump(config_data, f)
                
        except Exception as e:
            logger.error(f"更新使用牌组失败: {e}")

@register_plugin
class TarotsPlugin(BasePlugin):
    """塔罗牌插件 - 支持多种牌阵和卡牌类型的占卜功能"""

    plugin_name = "tarots_plugin"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ["Pillow", "aiohttp", "tomlkit"]

    plugin_description = "塔罗牌占卜插件，支持多种牌阵和卡牌类型，提供简短自然解读"
    plugin_version = "2.2.1"
    plugin_author = "升级版 - 简短解读"

    config_section_descriptions = {
        "plugin": "插件基本配置",
        "components": "组件启用控制",
        "proxy": "代理设置",
        "cards": "牌组配置",
        "adjustment": "功能调整",
        "permissions": "权限管理",
    }

    config_schema = {
        "plugin": {
            "config_version": ConfigField(type=str, default="2.2.1", description="配置文件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "components": {
            "enable_tarots": ConfigField(type=bool, default=True, description="启用塔罗牌占卜功能"),
        },
        "proxy": {
            "enable_proxy": ConfigField(type=bool, default=False, description="是否启用代理"),
            "proxy_url": ConfigField(type=str, default="", description="代理服务器地址")
        },
        "cards": {
            "using_cards": ConfigField(type=str, default='bilibili', description="当前使用牌组"),
            "use_cards": ConfigField(type=list, default=['bilibili','east'], description="可用牌组列表")
        },
        "adjustment": {
            "enable_original_text": ConfigField(type=bool, default=False, description="启用原始文本显示"),
            "ai_interpretation": ConfigField(type=bool, default=True, description="启用AI智能解读")
        },
        "permissions": {
            "admin_users": ConfigField(type=list, default=["123456789"], description="管理员用户ID列表")
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """返回插件组件"""
        components = []

        if self.get_config("components.enable_tarots", True):
            components.append((TarotsAction.get_action_info(), TarotsAction))

        return components