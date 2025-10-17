from src.plugin_system import (
    BasePlugin, register_plugin, BaseAction, BaseCommand,
    ComponentInfo, ActionActivationType, ChatMode, ConfigField
)
from src.plugin_system.apis import send_api, database_api, chat_api
from src.common.database.database_model import Messages, PersonInfo
from src.common.logger import get_logger
from src.config.config import global_config
from PIL import Image
from typing import Tuple, Dict, Optional, List, Any, Type
from pathlib import Path
import traceback
import tomlkit
import json
import random
import asyncio
import aiohttp
import base64
import toml
import io
import os
import re

logger = get_logger("tarots")

class TarotsAction(BaseAction):
    action_name = "tarots"

    # 双激活类型配置
    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.KEYWORD
    activation_keywords = ["抽一张塔罗牌", "抽张塔罗牌", "塔罗占卜", "塔罗牌"]
    keyword_case_sensitive = False

    # 模式和并行控制
    mode_enable = ChatMode.ALL
    parallel_action = False

    action_description = "执行塔罗牌占卜，支持多种抽牌方式"
    action_parameters = {
        "card_type": "塔罗牌的抽牌范围，必填，只能填一个参数，这里请根据用户的要求填'全部'或'大阿卡纳'或'小阿卡纳'，如果用户的要求并不明确，默认填'全部'",
        "formation": "塔罗牌的抽牌方式，必填，只能填一个参数，这里请根据用户的要求填'单张'或'圣三角'或'时间之流'或'四要素'或'五牌阵'或'吉普赛十字'或'马蹄'或'六芒星'，如果用户的要求并不明确，默认填'单张'",
        "target_message": "提出抽塔罗牌的对方的发言内容，格式必须为：（用户名:发言内容），若不清楚是回复谁的话可以为None"
    }
    action_require = [
        "当消息包含'抽塔罗牌''塔罗牌占卜'等关键词，且用户明确表达了要求你帮忙抽牌的意向时，你看心情调用就行（这意味着你可以拒绝抽塔罗牌，拒绝执行这个动作）。",
        "用户需要明确指定抽牌范围和抽牌类型，如果用户未明确指定抽牌范围则默认为'全部'，未明确指定抽牌类型则默认为'单张'。",
        "请仔细辨别对方到底是不是在让你抽塔罗牌！如果用户只是单独说了'抽卡'，'抽牌'，'占卜'，'算命'等，而且并没有上文内容验证用户是想抽塔罗牌的意思，就不要抽塔罗牌，不要执行这个动作！",
        "在完成一次抽牌后，请仔细确定用户有没有明确要求再抽一次，没有再次要求就不要继续执行这个动作。"
    ]

    associated_types = ["image", "text"]

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
        """实现基类要求的入口方法"""
        try:
            if not self.card_map:
                await self.send_text("❌ 没有可用的牌组，无法进行占卜")
                return False, "没有牌组，无法使用"
            
            logger.info("开始执行塔罗占卜")
            
            # 参数解析
            request_type = self.action_data.get("card_type", "全部") 
            formation_name = self.action_data.get("formation", "单张")
            card_type = self.get_available_card_type(request_type)
            
            # 参数校验
            if card_type not in ["全部", "大阿卡纳", "小阿卡纳"]:
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
            valid_ids = self._get_card_range(card_type)
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
    
            # 结果处理
            result_text = f"【{formation_name}牌阵 - {self.using_cards}牌组】\n"
            failed_images = []  # 记录获取失败的图片
            
            # 解析目标用户信息
            reply_to = self.action_data.get("target_message", "")
            user_nickname = "用户"
            if reply_to:
                if ":" in reply_to:
                    parts = reply_to.split(":", 1)
                    user_nickname = parts[0].strip()
                elif "：" in reply_to:
                    parts = reply_to.split("：", 1)
                    user_nickname = parts[0].strip()

            # 发送每张卡牌
            for idx, (card_id, is_reverse) in enumerate(selected_cards):
                card_data = self.card_map.get(card_id, {})
                if not card_data:
                    logger.warning(f"卡牌ID不存在: {card_id}")
                    continue
                    
                card_info = card_data.get("info", {})
                pos_name = represent_list[0][idx] if idx < len(represent_list[0]) else f"位置{idx+1}"
                
                # 发送图片
                img_success = await self._send_card_image(card_id, is_reverse)
                if not img_success:
                    failed_images.append(f"{card_data.get('name', '未知卡牌')}({'逆位' if is_reverse else '正位'})")
                    logger.warning(f"卡牌图片发送失败: {card_id}")
                
                # 构建文本
                desc = card_info.get('reverseDescription' if is_reverse else 'description', '暂无描述')
                result_text += (
                    f"\n{pos_name} - {'逆位' if is_reverse else '正位'} {card_data.get('name', '未知')}\n"
                    f"{desc[:100]}...\n"
                )
                await asyncio.sleep(0.3)  # 防止消息频率限制

            if failed_images:
                error_msg = f"❌ 以下卡牌图片获取失败: {', '.join(failed_images)}"
                await self.send_text(error_msg)
                return False, "图片获取失败"
                
            # 发送最终文本
            await asyncio.sleep(1.5)
            
            # 使用AI重新组织回复
            try:
                ai_response = await self._generate_ai_reply(result_text)
                
                if ai_response:
                    await self.send_text(ai_response)
                else:
                    # 如果AI生成失败，发送原始结果
                    await self.send_text(result_text)
                    
            except Exception as e:
                logger.error(f"AI回复生成失败: {e}")
                await self.send_text(result_text)

            # 记录动作信息
            await self.store_action_info(
                action_build_into_prompt=True,
                action_prompt_display=f"已为{user_nickname}抽取了塔罗牌并成功解牌。",
                action_done=True
            )

            return True, f"已为{user_nickname}抽取了塔罗牌并成功解牌"
            
        except Exception as e:
            error_msg = traceback.format_exc()
            logger.error(f"执行失败: {error_msg}")
            await self.send_text(f"❌ 占卜失败: {str(e)}")
            return False, "执行错误"

    async def _generate_ai_reply(self, original_text: str) -> Optional[str]:
        """生成AI回复 - 简化版本"""
        try:
            # 使用框架的聊天API生成回复
            prompt = f"""请根据以下塔罗牌占卜结果，用温暖、神秘的语气为用户解牌：

{original_text}

请用亲切友好的语气解释牌面含义，给用户一些积极的建议和启示："""
            
            # 使用聊天API（根据实际框架API调整）
            if hasattr(self, 'chat_api') and self.chat_api:
                response = await self.chat_api.generate_response(
                    prompt=prompt,
                    context=self.chat_stream,
                    max_tokens=500
                )
                return response
            else:
                # 如果聊天API不可用，返回原始文本的简化版本
                return f"🔮 塔罗牌启示：\n\n{original_text}\n\n愿这些牌面给你带来启示和力量～"
                
        except Exception as e:
            logger.error(f"AI回复生成错误: {e}")
            return None

    def _get_card_range(self, card_type: str) -> list:
        """获取卡牌范围"""
        if card_type == "大阿卡纳":
            return [str(i) for i in range(22)]
        elif card_type == "小阿卡纳":
            return [str(i) for i in range(22, 78)]
        return [str(i) for i in range(78)]

    async def _send_card_image(self, card_id: str, is_reverse: bool) -> bool:
        """发送卡牌图片 - 从本地目录获取并发送"""
        try:
            # 直接从本地牌组目录获取图片
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
                    "enable_original_text": config_data.get("adjustment", {}).get("enable_original_text", False)
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
                "adjustment": {"enable_original_text": False}
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

    def _check_cards(self, cards: str) -> bool:
        """检查牌组是否可用"""
        use_cards = self.config["cards"].get("use_cards", ['bilibili','east'])
        return cards in use_cards
    
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

class TarotsCommand(BaseCommand):
    command_name = "tarots"
    command_description = "塔罗牌管理命令"
    command_pattern = r"^/tarots\s+(?P<target_type>\w+)(?:\s+(?P<action_value>\w+))?\s*$"
    command_help = "使用方法: /tarots check - 检查牌组完整性; /tarots switch 牌组名称 - 切换牌组"
    command_examples = [
        "/tarots check - 检查当前牌组完整性",
        "/tarots switch bilibili - 切换至bilibili牌组"
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 初始化与TarotsAction相同的资源
        self.base_dir = Path(__file__).parent.absolute()
        self.config = self._load_config()
        self.using_cards = self.config["cards"].get("using_cards", 'bilibili')
        self.card_map = {}
        self.formation_map = {}
        self._load_resources()

    def _load_config(self):
        """加载配置"""
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, "config.toml")
            with open(config_path, 'r', encoding='utf-8') as f:
                return toml.load(f)
        except Exception:
            return {"cards": {"using_cards": "bilibili", "use_cards": ["bilibili", "east"]}}

    def _load_resources(self):
        """加载资源"""
        try:
            if not self.using_cards:
                return
            
            cards_json_path = self.base_dir / f"tarot_jsons/{self.using_cards}/tarots.json"
            if cards_json_path.exists():
                with open(cards_json_path, encoding="utf-8") as f:
                    self.card_map = json.load(f)
            
            formation_json_path = self.base_dir / "tarot_jsons/formation.json"
            if formation_json_path.exists():
                with open(formation_json_path, encoding="utf-8") as f:
                    self.formation_map = json.load(f)
                    
        except Exception as e:
            logger.error(f"资源加载失败: {e}")

    async def execute(self) -> Tuple[bool, str, bool]:
        """执行命令"""
        try:
            # 权限检查
            sender_id = str(self.message.message_info.user_info.user_id)
            if not self._check_person_permission(sender_id):
                await self.send_text("❌ 权限不足，你无权使用此命令")    
                return False, "权限不足", True
            
            if not self.card_map:
                await self.send_text("❌ 没有可用的牌组")
                return False, "没有牌组", True
                
            target_type = self.matched_groups.get("target_type", "")
            action_value = self.matched_groups.get("action_value", "")
            
            if target_type == "check" and not action_value:
                return await self._check_card_set()
            elif target_type == "switch" and action_value:
                return await self._switch_card_set(action_value)
            else:
                await self.send_text("❌ 参数错误，使用 /tarots help 查看帮助")
                return False, "参数错误", True

        except Exception as e:
            logger.error(f"命令执行错误: {e}")
            await self.send_text(f"❌ 命令执行失败: {str(e)}")
            return False, f"执行失败: {str(e)}", True

    async def _check_card_set(self) -> Tuple[bool, str, bool]:
        """检查牌组完整性"""
        await self.send_text("🔍 正在检查牌组完整性...")
        
        if not self.card_map:
            await self.send_text("❌ 牌组数据加载失败")
            return False, "牌组数据加载失败", True

        missing_cards = []
        total_cards = 0
        
        # 检查所有卡牌的图片文件是否存在
        for card_id, card_data in self.card_map.items():
            if card_id == "_meta":
                continue
                
            total_cards += 1
            card_name = card_data.get("name", "")
            
            if card_name:
                # 检查正位图片
                normal_filename = self._get_local_image_filename(card_name, False)
                normal_path = self.base_dir / f"tarot_jsons/{self.using_cards}" / normal_filename
                
                # 检查逆位图片
                reverse_filename = self._get_local_image_filename(card_name, True)
                reverse_path = self.base_dir / f"tarot_jsons/{self.using_cards}" / reverse_filename
                
                if not normal_path.exists() or not reverse_path.exists():
                    missing_cards.append(card_name)

        if not missing_cards:
            await self.send_text(f"✅ 牌组完整性检查通过！共检查 {total_cards} 张卡牌，所有图片文件完整。")
            return True, "牌组完整性检查通过", True
        else:
            missing_list = "\n".join([f"• {card}" for card in missing_cards])
            await self.send_text(f"❌ 发现 {len(missing_cards)} 张卡牌图片缺失：\n{missing_list}")
            return False, f"发现 {len(missing_cards)} 张卡牌图片缺失", True

    def _get_local_image_filename(self, card_name: str, is_reverse: bool) -> str:
        """根据卡牌名称和位置构建本地图片文件名"""
        # 处理卡牌名称中的特殊字符和空格
        cleaned_name = card_name.replace("ACE", "王牌").replace("2", "二").replace("3", "三").replace("4", "四").replace("5", "五").replace("6", "六").replace("7", "七").replace("8", "八").replace("9", "九").replace("10", "十")
        
        # 构建文件名
        position = "逆位" if is_reverse else "正位"
        filename = f"{cleaned_name}{position}.jpg"
        
        return filename

    async def _switch_card_set(self, card_set: str) -> Tuple[bool, str, bool]:
        """切换牌组"""
        if self._check_cards(card_set):
            self._set_card_config(card_set)
            await self.send_text(f"✅ 已切换牌组至: {card_set}")
            return True, f"切换牌组至 {card_set}", True
        else:
            available_sets = self.config["cards"].get("use_cards", [])
            await self.send_text(f"❌ 牌组 {card_set} 不可用，可用牌组: {', '.join(available_sets)}")
            return False, f"牌组 {card_set} 不可用", True

    def _check_person_permission(self, user_id: str) -> bool:
        """权限检查"""
        admin_users = self.config.get("permissions", {}).get("admin_users", [])
        return user_id in admin_users

    def _check_cards(self, cards: str) -> bool:
        """检查牌组是否可用"""
        use_cards = self.config["cards"].get("use_cards", ['bilibili','east'])
        return cards in use_cards

    def _set_card_config(self, card_set: str):
        """设置牌组配置"""
        try:
            config_path = self.base_dir / "config.toml"
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = tomlkit.load(f)
                config_data["cards"]["using_cards"] = card_set
            
            with open(config_path, 'w', encoding='utf-8') as f:
                tomlkit.dump(config_data, f)
        except Exception as e:
            logger.error(f"更新牌组配置失败: {e}")

@register_plugin
class TarotsPlugin(BasePlugin):
    """塔罗牌插件 - 支持多种牌阵和卡牌类型的占卜功能"""

    plugin_name = "tarots_plugin"
    enable_plugin = True
    config_file_name = "config.toml"
    dependencies = []
    python_dependencies = ["Pillow", "aiohttp", "tomlkit"]

    plugin_description = "塔罗牌占卜插件，支持多种牌阵和卡牌类型"
    plugin_version = "2.1.1"
    plugin_author = "升级版 - 本地牌库"

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
            "config_version": ConfigField(type=str, default="2.1.1", description="配置文件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "components": {
            "enable_tarots": ConfigField(type=bool, default=True, description="启用塔罗牌占卜功能"),
            "enable_tarots_command": ConfigField(type=bool, default=True, description="启用塔罗牌管理命令")
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
            "enable_original_text": ConfigField(type=bool, default=False, description="启用原始文本显示")
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

        if self.get_config("components.enable_tarots_command", True):
            components.append((TarotsCommand.get_command_info(), TarotsCommand))

        return components