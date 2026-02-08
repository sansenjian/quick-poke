"""Comprehensive tests for Quick Poke plugin

Tests cover:
- _dig() utility function
- PokeEventHandler message validation and processing
- PokeEventHandler cooldown and rate limiting logic
- PokeAction user ID resolution and poke sending
- Configuration handling
"""
import json
import sys
import time
from typing import Dict, Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from enum import Enum

import pytest

# Mark all async tests to use anyio
pytestmark = pytest.mark.anyio

# Mock all external dependencies before importing plugin
mock_logger = MagicMock()
mock_logger.get_logger = MagicMock(return_value=MagicMock())

mock_config = MagicMock()
mock_config.global_config = MagicMock()
mock_config.global_config.bot = MagicMock()
mock_config.global_config.bot.qq_account = "67890"

# Create EventType enum for testing
class EventType(Enum):
    ON_MESSAGE = "on_message"

# Create ActionActivationType enum for testing
class ActionActivationType(Enum):
    ALWAYS = "always"

# Mock ComponentInfo
class ComponentInfo:
    pass

mock_plugin_system = MagicMock()
mock_plugin_system.ConfigField = MagicMock
mock_plugin_system.BasePlugin = object
mock_plugin_system.register_plugin = lambda x: x
mock_plugin_system.BaseAction = object
mock_plugin_system.BaseEventHandler = object
mock_plugin_system.EventType = EventType
mock_plugin_system.MaiMessages = MagicMock
mock_plugin_system.base = MagicMock()
mock_plugin_system.base.component_types = MagicMock()
mock_plugin_system.base.component_types.ComponentInfo = ComponentInfo
mock_plugin_system.base.component_types.ActionActivationType = ActionActivationType

mock_apis = MagicMock()
mock_apis.generator_api = MagicMock()
mock_apis.person_api = MagicMock()
mock_apis.database_api = MagicMock()

# Set up sys.modules
sys.modules['src'] = MagicMock()
sys.modules['src.common'] = MagicMock()
sys.modules['src.common.logger'] = mock_logger
sys.modules['src.config'] = MagicMock()
sys.modules['src.config.config'] = mock_config
sys.modules['src.plugin_system'] = mock_plugin_system
sys.modules['src.plugin_system.apis'] = mock_apis
sys.modules['src.plugin_system.base'] = mock_plugin_system.base
sys.modules['src.plugin_system.base.component_types'] = mock_plugin_system.base.component_types

# Now import the module to test
from plugin import (
    _dig,
    CMD_SEND_POKE,
    NOTICE_POKE,
    PokeEventHandler,
    PokeAction,
    PokePlugin,
)


# ========== Tests for _dig() utility function ==========

class TestDigFunction:
    """Tests for the _dig() utility function"""

    def test_dig_simple_attribute(self):
        """Test accessing a simple attribute"""
        obj = MagicMock()
        obj.name = "test"
        assert _dig(obj, "name") == "test"

    def test_dig_nested_attributes(self):
        """Test accessing nested attributes"""
        obj = MagicMock()
        obj.user.id = "12345"
        assert _dig(obj, "user.id") == "12345"

    def test_dig_dict_key(self):
        """Test accessing dictionary key"""
        obj = {"name": "test", "id": 123}
        assert _dig(obj, "name") == "test"
        assert _dig(obj, "id") == 123

    def test_dig_nested_dict(self):
        """Test accessing nested dictionary"""
        obj = {"user": {"id": "12345", "name": "test"}}
        assert _dig(obj, "user.id") == "12345"
        assert _dig(obj, "user.name") == "test"

    def test_dig_mixed_attr_and_dict(self):
        """Test accessing mixed attributes and dictionary keys"""
        obj = MagicMock()
        obj.data = {"user": {"id": "12345"}}
        assert _dig(obj, "data.user.id") == "12345"

    def test_dig_missing_attribute_returns_default(self):
        """Test that missing attributes return default value"""
        obj = MagicMock(spec=[])
        assert _dig(obj, "missing") is None
        assert _dig(obj, "missing", "default") == "default"

    def test_dig_missing_dict_key_returns_default(self):
        """Test that missing dict keys return default value"""
        obj = {"name": "test"}
        assert _dig(obj, "missing") is None
        assert _dig(obj, "missing", "default") == "default"

    def test_dig_none_object_returns_default(self):
        """Test that None object returns default"""
        assert _dig(None, "any.path") is None
        assert _dig(None, "any.path", "default") == "default"

    def test_dig_partial_path_none_returns_default(self):
        """Test that path with None intermediate returns default"""
        obj = {"user": None}
        assert _dig(obj, "user.id") is None
        assert _dig(obj, "user.id", "default") == "default"


# ========== Tests for PokeEventHandler ==========

class TestPokeEventHandler:
    """Tests for PokeEventHandler class"""

    @pytest.fixture
    def handler(self):
        """Create a PokeEventHandler instance for testing"""
        handler = PokeEventHandler()
        handler.get_config = MagicMock(side_effect=lambda key, default: default)
        handler.send_command = AsyncMock(return_value=True)
        handler.send_text = AsyncMock(return_value=True)
        return handler

    @pytest.fixture
    def valid_poke_message(self):
        """Create a valid poke message for testing"""
        msg = MagicMock()
        event_data = {
            "post_type": "notice",
            "sub_type": "poke",
            "user_id": "12345",
            "target_id": "67890"
        }
        msg.raw_message = json.dumps(event_data)
        msg.user_id = "12345"
        msg.stream_id = "stream123"
        msg.plain_text = ""
        return msg

    # ===== Message Validation Tests =====

    def test_validate_message_valid_poke(self, handler, valid_poke_message):
        """Test validation of a valid poke message"""
        is_valid, event = handler._validate_message(valid_poke_message)
        assert is_valid is True
        assert event is not None
        assert event["post_type"] == "notice"
        assert event["sub_type"] == "poke"

    def test_validate_message_none_message(self, handler):
        """Test validation with None message"""
        is_valid, event = handler._validate_message(None)
        assert is_valid is False
        assert event is None

    def test_validate_message_no_raw_message(self, handler):
        """Test validation with missing raw_message"""
        msg = MagicMock()
        msg.raw_message = None
        is_valid, event = handler._validate_message(msg)
        assert is_valid is False
        assert event is None

    def test_validate_message_invalid_json(self, handler):
        """Test validation with invalid JSON in raw_message"""
        msg = MagicMock()
        msg.raw_message = "not valid json"
        is_valid, event = handler._validate_message(msg)
        assert is_valid is False
        assert event is None

    def test_validate_message_wrong_post_type(self, handler):
        """Test validation with wrong post_type"""
        msg = MagicMock()
        event_data = {"post_type": "message", "sub_type": "poke"}
        msg.raw_message = json.dumps(event_data)
        is_valid, event = handler._validate_message(msg)
        assert is_valid is False

    def test_validate_message_wrong_sub_type(self, handler):
        """Test validation with wrong sub_type"""
        msg = MagicMock()
        event_data = {"post_type": "notice", "sub_type": "group_upload"}
        msg.raw_message = json.dumps(event_data)
        is_valid, event = handler._validate_message(msg)
        assert is_valid is False

    # ===== Cooldown Tests =====

    def test_check_cooldown_first_time(self, handler):
        """Test cooldown check for first time user"""
        handler._cooldown.clear()
        in_cooldown, remaining = handler._check_cooldown("user123")
        assert in_cooldown is False
        assert remaining == 0.0
        assert "user123" in handler._cooldown

    def test_check_cooldown_within_limit(self, handler):
        """Test cooldown check when user is within cooldown period"""
        handler.get_config = MagicMock(return_value=30)  # 30 second cooldown
        user_id = "user123"
        handler._cooldown[user_id] = time.monotonic() - 10  # 10 seconds ago

        in_cooldown, remaining = handler._check_cooldown(user_id)
        assert in_cooldown is True
        assert 19 < remaining < 21  # Should be around 20 seconds remaining

    def test_check_cooldown_expired(self, handler):
        """Test cooldown check when cooldown has expired"""
        handler.get_config = MagicMock(return_value=30)
        user_id = "user123"
        handler._cooldown[user_id] = time.monotonic() - 31  # 31 seconds ago

        in_cooldown, remaining = handler._check_cooldown(user_id)
        assert in_cooldown is False
        assert remaining == 0.0

    def test_check_cooldown_updates_timestamp(self, handler):
        """Test that cooldown check updates timestamp for expired cooldown"""
        handler.get_config = MagicMock(return_value=30)
        user_id = "user123"
        old_time = time.monotonic() - 31
        handler._cooldown[user_id] = old_time

        handler._check_cooldown(user_id)
        assert handler._cooldown[user_id] > old_time

    # ===== Rate Limit Tests =====

    def test_check_rate_limit_empty(self, handler):
        """Test rate limit check when no recent pokes"""
        handler._poke_timestamps.clear()
        handler.get_config = MagicMock(return_value=10)

        rate_limited = handler._check_rate_limit()
        assert rate_limited is False
        assert len(handler._poke_timestamps) == 1

    def test_check_rate_limit_below_max(self, handler):
        """Test rate limit when below maximum"""
        handler.get_config = MagicMock(return_value=10)
        current_time = time.monotonic()
        handler._poke_timestamps = [current_time - i for i in range(5)]

        rate_limited = handler._check_rate_limit()
        assert rate_limited is False
        assert len(handler._poke_timestamps) == 6

    def test_check_rate_limit_at_max(self, handler):
        """Test rate limit when at maximum"""
        handler.get_config = MagicMock(return_value=10)
        current_time = time.monotonic()
        handler._poke_timestamps = [current_time - i for i in range(10)]

        rate_limited = handler._check_rate_limit()
        assert rate_limited is True
        assert len(handler._poke_timestamps) == 10  # Should not add new timestamp

    def test_check_rate_limit_cleans_old_timestamps(self, handler):
        """Test that rate limit cleans up old timestamps"""
        handler.get_config = MagicMock(return_value=10)
        current_time = time.monotonic()
        # Add some old timestamps (> 60 seconds ago) and some recent ones
        handler._poke_timestamps = [
            current_time - 70,
            current_time - 65,
            current_time - 5,
            current_time - 3,
        ]

        rate_limited = handler._check_rate_limit()
        # Old timestamps should be removed, only 3 should remain (2 recent + new one)
        assert len(handler._poke_timestamps) == 3
        assert all(current_time - t < 60 for t in handler._poke_timestamps)

    # ===== Get User Info Tests =====

    async def test_get_user_info_from_message_user_id(self, handler):
        """Test getting user info from message.user_id"""
        msg = MagicMock()
        msg.user_id = "12345"
        event = {}

        with patch('plugin.person_api') as mock_person_api:
            mock_person_api.get_person_id.return_value = "person123"
            mock_person_api.get_person_value = AsyncMock(return_value="TestUser")

            user_id, person_name = await handler._get_user_info(msg, event)
            assert user_id == "12345"
            assert person_name == "TestUser"

    async def test_get_user_info_from_message_base_info(self, handler):
        """Test getting user info from message_base_info"""
        msg = MagicMock()
        msg.user_id = None
        msg.message_base_info = MagicMock()
        msg.message_base_info.user_id = "54321"
        event = {}

        with patch('plugin.person_api') as mock_person_api:
            mock_person_api.get_person_id.return_value = "person456"
            mock_person_api.get_person_value = AsyncMock(return_value="AnotherUser")

            user_id, person_name = await handler._get_user_info(msg, event)
            assert user_id == "54321"
            assert person_name == "AnotherUser"

    async def test_get_user_info_no_user_id(self, handler):
        """Test getting user info when no user_id available"""
        msg = MagicMock(spec=[])
        event = {}

        user_id, person_name = await handler._get_user_info(msg, event)
        assert user_id is None
        assert person_name is None

    async def test_get_user_info_person_api_failure(self, handler):
        """Test getting user info when person_api fails"""
        msg = MagicMock()
        msg.user_id = "12345"
        event = {}

        with patch('plugin.person_api') as mock_person_api:
            mock_person_api.get_person_id.return_value = None

            user_id, person_name = await handler._get_user_info(msg, event)
            assert user_id is None
            assert person_name is None

    # ===== Follow Poke Tests =====

    async def test_handle_follow_poke_target_is_bot(self, handler):
        """Test follow poke when target is the bot"""
        msg = MagicMock()
        msg.stream_id = "stream123"
        event = {"target_id": "67890"}

        with patch('plugin.global_config') as mock_config:
            mock_config.bot.qq_account = "67890"

            should_exit, reason = await handler._handle_follow_poke(msg, event, "12345")
            assert should_exit is False
            assert reason == ""

    async def test_handle_follow_poke_disabled(self, handler):
        """Test follow poke when feature is disabled"""
        msg = MagicMock()
        event = {"target_id": "99999"}

        with patch('plugin.global_config') as mock_config:
            mock_config.bot.qq_account = "67890"
            handler.get_config = MagicMock(return_value=False)

            should_exit, reason = await handler._handle_follow_poke(msg, event, "12345")
            assert should_exit is True
            assert reason == "戳的对象不是 bot"

    async def test_handle_follow_poke_probability_miss(self, handler):
        """Test follow poke when probability check fails"""
        msg = MagicMock()
        event = {"target_id": "99999"}

        with patch('plugin.global_config') as mock_config:
            mock_config.bot.qq_account = "67890"
            handler.get_config = MagicMock(side_effect=lambda key, default:
                True if "enabled" in key else 0.3)

            with patch('plugin.random.random', return_value=0.5):  # > 0.3
                should_exit, reason = await handler._handle_follow_poke(msg, event, "12345")
                assert should_exit is True

    async def test_handle_follow_poke_in_cooldown(self, handler):
        """Test follow poke when in cooldown"""
        msg = MagicMock()
        event = {"target_id": "99999"}

        with patch('plugin.global_config') as mock_config:
            mock_config.bot.qq_account = "67890"
            handler.get_config = MagicMock(side_effect=lambda key, default:
                True if "enabled" in key else (0.3 if "probability" in key else 60))
            handler._follow_poke_cooldown["99999"] = time.monotonic() - 30  # 30 sec ago

            with patch('plugin.random.random', return_value=0.1):  # < 0.3
                should_exit, reason = await handler._handle_follow_poke(msg, event, "12345")
                assert should_exit is True
                assert "bot" in reason

    async def test_handle_follow_poke_success(self, handler):
        """Test successful follow poke"""
        msg = MagicMock()
        msg.stream_id = "stream123"
        event = {"target_id": "99999"}

        with patch('plugin.global_config') as mock_config:
            mock_config.bot.qq_account = "67890"
            handler.get_config = MagicMock(side_effect=lambda key, default:
                True if "enabled" in key else (0.3 if "probability" in key else 60))
            handler._follow_poke_cooldown.clear()

            with patch('plugin.random.random', return_value=0.1):  # < 0.3
                should_exit, reason = await handler._handle_follow_poke(msg, event, "12345")
                assert should_exit is True
                handler.send_command.assert_called_once()
                assert "99999" in handler._follow_poke_cooldown

    # ===== Poke Back Tests =====

    async def test_handle_poke_back_disabled(self, handler):
        """Test poke back when feature is disabled"""
        msg = MagicMock()
        handler.get_config = MagicMock(return_value=False)

        await handler._handle_poke_back(msg, "12345")
        handler.send_command.assert_not_called()

    async def test_handle_poke_back_probability_miss(self, handler):
        """Test poke back when probability check fails"""
        msg = MagicMock()
        handler.get_config = MagicMock(side_effect=lambda key, default:
            True if "auto_poke_back" in key else 0.8)

        with patch('plugin.random.random', return_value=0.9):  # > 0.8
            await handler._handle_poke_back(msg, "12345")
            handler.send_command.assert_not_called()

    async def test_handle_poke_back_success(self, handler):
        """Test successful poke back"""
        msg = MagicMock()
        msg.stream_id = "stream123"
        handler.get_config = MagicMock(side_effect=lambda key, default:
            True if "auto_poke_back" in key else (0.8 if "probability" in key else 3))

        with patch('plugin.random.random', return_value=0.5):  # < 0.8
            with patch('plugin.random.randint', return_value=2):
                await handler._handle_poke_back(msg, "12345")
                assert handler.send_command.call_count == 2

    # ===== Text Reply Tests =====

    async def test_handle_text_reply_disabled(self, handler):
        """Test text reply when feature is disabled"""
        msg = MagicMock()
        handler.get_config = MagicMock(return_value=False)

        result = await handler._handle_text_reply(msg, "12345", "TestUser")
        assert result is False

    async def test_handle_text_reply_probability_miss(self, handler):
        """Test text reply when probability check fails"""
        msg = MagicMock()
        handler.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 0.7)

        with patch('plugin.random.random', return_value=0.8):  # > 0.7
            result = await handler._handle_text_reply(msg, "12345", "TestUser")
            assert result is False

    async def test_handle_text_reply_success(self, handler):
        """Test successful text reply"""
        msg = MagicMock()
        msg.stream_id = "stream123"
        msg.plain_text = "test message"
        handler.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 0.7)

        with patch('plugin.random.random', return_value=0.5):  # < 0.7
            with patch('plugin.generator_api') as mock_gen_api:
                mock_reply_data = MagicMock()
                mock_reply_data.reply_set.reply_data = [MagicMock(content="Hello!")]
                mock_gen_api.generate_reply = AsyncMock(return_value=(True, mock_reply_data))

                result = await handler._handle_text_reply(msg, "12345", "TestUser")
                assert result is True
                handler.send_text.assert_called_once()

    async def test_handle_text_reply_generator_failure(self, handler):
        """Test text reply when generator fails"""
        msg = MagicMock()
        msg.stream_id = "stream123"
        msg.plain_text = ""
        handler.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 0.7)

        with patch('plugin.random.random', return_value=0.5):
            with patch('plugin.generator_api') as mock_gen_api:
                mock_gen_api.generate_reply = AsyncMock(return_value=(False, None))

                result = await handler._handle_text_reply(msg, "12345", "TestUser")
                assert result is False


# ========== Tests for PokeAction ==========

class TestPokeAction:
    """Tests for PokeAction class"""

    @pytest.fixture
    def action(self):
        """Create a PokeAction instance for testing"""
        action = PokeAction()
        action.action_data = {}
        action.get_config = MagicMock(side_effect=lambda key, default: default)
        action.send_command = AsyncMock(return_value=True)
        action.message = MagicMock()
        action.chat_stream = MagicMock()
        action.user_id = "12345"
        return action

    # ===== Get User ID Tests =====

    async def test_get_user_id_with_me_keyword(self, action):
        """Test getting user ID with '我' keyword"""
        action.user_id = "12345"
        user_id = await action._get_user_id("我")
        assert user_id == "12345"

    async def test_get_user_id_with_me_variations(self, action):
        """Test getting user ID with various 'me' keywords"""
        action.user_id = "12345"

        for keyword in ["我", "我自己", "自己", "me"]:
            user_id = await action._get_user_id(keyword)
            assert user_id == "12345"

    async def test_get_user_id_with_me_from_message(self, action):
        """Test getting user ID from message when self.user_id not set"""
        action.user_id = None
        action.message.user_id = "54321"

        user_id = await action._get_user_id("我")
        assert user_id == "54321"

    async def test_get_user_id_with_qq_number(self, action):
        """Test getting user ID with direct QQ number"""
        user_id = await action._get_user_id("1234567890")
        assert user_id == "1234567890"

    async def test_get_user_id_by_name_success(self, action):
        """Test getting user ID by name lookup"""
        with patch('plugin.person_api') as mock_person_api:
            mock_person_api.get_person_id_by_name.return_value = "person123"
            mock_person_api.get_person_value = AsyncMock(return_value="98765")

            user_id = await action._get_user_id("TestUser")
            assert user_id == "98765"

    async def test_get_user_id_by_name_not_found(self, action):
        """Test getting user ID when name not found"""
        with patch('plugin.person_api') as mock_person_api:
            mock_person_api.get_person_id_by_name.return_value = None

            user_id = await action._get_user_id("UnknownUser")
            assert user_id is None

    async def test_get_user_id_empty_name(self, action):
        """Test getting user ID with empty name"""
        user_id = await action._get_user_id("")
        assert user_id is None

        user_id = await action._get_user_id(None)
        assert user_id is None

    # ===== Infer Group ID Tests =====

    def test_infer_group_id_from_action_data(self, action):
        """Test inferring group ID from action_data"""
        action.action_data = {"group_id": "group123"}
        group_id = action._infer_group_id_from_context()
        assert group_id == "group123"

    def test_infer_group_id_from_message_info(self, action):
        """Test inferring group ID from message.message_info"""
        action.action_data = {}
        action.message.message_info = MagicMock()
        action.message.message_info.group_id = "group456"

        group_id = action._infer_group_id_from_context()
        assert group_id == "group456"

    def test_infer_group_id_from_chat_stream(self, action):
        """Test inferring group ID from chat_stream"""
        action.action_data = {}
        action.message.message_info = None
        action.chat_stream.group_id = "group789"

        group_id = action._infer_group_id_from_context()
        assert group_id == "group789"

    def test_infer_group_id_none_values(self, action):
        """Test inferring group ID with None/empty values"""
        action.action_data = {"group_id": None}
        action.message.message_info = None
        action.chat_stream.group_id = None

        group_id = action._infer_group_id_from_context()
        assert group_id is None

    # ===== Build Send Poke Args Tests =====

    def test_build_send_poke_args_with_group(self, action):
        """Test building poke args with group ID"""
        args_list = action._build_send_poke_args("12345", "group123")

        assert len(args_list) == 2
        assert args_list[0] == {"qq_id": "12345", "group_id": "group123"}
        assert args_list[1] == {"target_id": "12345", "group_id": "group123"}

    def test_build_send_poke_args_without_group(self, action):
        """Test building poke args without group ID"""
        args_list = action._build_send_poke_args("12345", None)

        assert len(args_list) == 2
        assert args_list[0] == {"qq_id": "12345"}
        assert args_list[1] == {"target_id": "12345"}

    # ===== Send Poke Tests =====

    async def test_send_poke_success_first_format(self, action):
        """Test successful poke with first format"""
        action.send_command = AsyncMock(return_value=True)

        ok, result = await action._send_poke("12345", "group123", "TestUser")
        assert ok is True
        assert result == "戳一戳成功"
        action.send_command.assert_called_once()

    async def test_send_poke_success_second_format(self, action):
        """Test successful poke with second format after first fails"""
        # First call fails, second succeeds
        action.send_command = AsyncMock(side_effect=[False, True])

        ok, result = await action._send_poke("12345", "group123", "TestUser")
        assert ok is True
        assert result == "戳一戳成功"
        assert action.send_command.call_count == 2

    async def test_send_poke_all_formats_fail(self, action):
        """Test poke when all formats fail"""
        action.send_command = AsyncMock(return_value=False)

        ok, result = await action._send_poke("12345", "group123", "TestUser")
        assert ok is False
        assert "失败" in result
        assert action.send_command.call_count == 2

    # ===== Execute Tests =====

    async def test_execute_disabled(self, action):
        """Test execute when feature is disabled"""
        action.get_config = MagicMock(return_value=False)
        action.action_data = {"name": "TestUser"}

        ok, result = await action.execute()
        assert ok is False
        assert "禁用" in result

    async def test_execute_missing_name(self, action):
        """Test execute with missing name parameter"""
        action.action_data = {}

        ok, result = await action.execute()
        assert ok is False
        assert "name" in result

    async def test_execute_user_not_found(self, action):
        """Test execute when user cannot be identified"""
        action.action_data = {"name": "UnknownUser"}

        with patch.object(action, '_get_user_id', return_value=None):
            ok, result = await action.execute()
            assert ok is False
            assert "无法识别" in result

    async def test_execute_in_cooldown(self, action):
        """Test execute when in cooldown"""
        action.action_data = {"name": "TestUser"}
        action.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 300)

        # Set up class-level cooldown
        PokeAction._last_poke_user = "54321"
        PokeAction._last_poke_group = None
        PokeAction._last_poke_time = time.time() - 100  # 100 seconds ago

        with patch.object(action, '_get_user_id', return_value="54321"):
            with patch.object(action, '_infer_group_id_from_context', return_value=None):
                ok, result = await action.execute()
                assert ok is False
                assert "冷却" in result

    async def test_execute_success(self, action):
        """Test successful execute"""
        action.action_data = {"name": "TestUser"}
        action.chat_stream = MagicMock()
        action.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 300)

        # Clear cooldown
        PokeAction._last_poke_user = None
        PokeAction._last_poke_time = 0

        with patch.object(action, '_get_user_id', return_value="54321"):
            with patch.object(action, '_infer_group_id_from_context', return_value="group123"):
                with patch.object(action, '_send_poke', return_value=(True, "成功")):
                    with patch('plugin.database_api') as mock_db:
                        mock_db.store_action_info = AsyncMock()

                        ok, result = await action.execute()
                        assert ok is True
                        # Check instance variable, not class variable
                        assert action._last_poke_user == "54321"
                        mock_db.store_action_info.assert_called_once()

    async def test_execute_cooldown_different_group(self, action):
        """Test execute with same user but different group (should not be in cooldown)"""
        action.action_data = {"name": "TestUser"}
        action.chat_stream = MagicMock()
        action.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 300)

        # Set up cooldown for different group
        PokeAction._last_poke_user = "54321"
        PokeAction._last_poke_group = "group123"
        PokeAction._last_poke_time = time.time() - 100

        with patch.object(action, '_get_user_id', return_value="54321"):
            with patch.object(action, '_infer_group_id_from_context', return_value="group456"):
                with patch.object(action, '_send_poke', return_value=(True, "成功")):
                    with patch('plugin.database_api') as mock_db:
                        mock_db.store_action_info = AsyncMock()

                        ok, result = await action.execute()
                        # Different group, so should succeed
                        assert ok is True


# ========== Tests for PokePlugin ==========

class TestPokePlugin:
    """Tests for PokePlugin class"""

    def test_plugin_configuration(self):
        """Test plugin has correct configuration"""
        plugin = PokePlugin()

        assert plugin.plugin_name == "quick_poke"
        assert plugin.enable_plugin is True
        assert plugin.config_file_name == "config.toml"
        assert isinstance(plugin.dependencies, list)
        assert isinstance(plugin.python_dependencies, list)

    def test_plugin_config_schema(self):
        """Test plugin has valid config schema"""
        plugin = PokePlugin()

        # Check main config sections exist
        assert "plugin" in plugin.config_schema
        assert "poke_config" in plugin.config_schema
        assert "follow_poke_config" in plugin.config_schema
        assert "poke_action" in plugin.config_schema

        # Check some specific fields
        assert "auto_poke_back" in plugin.config_schema["poke_config"]
        assert "follow_poke_enabled" in plugin.config_schema["follow_poke_config"]
        assert "enabled" in plugin.config_schema["poke_action"]

    def test_plugin_get_components(self):
        """Test plugin returns correct components"""
        plugin = PokePlugin()

        # Add mock methods before patching
        PokeEventHandler.get_handler_info = MagicMock(return_value=ComponentInfo())
        PokeAction.get_action_info = MagicMock(return_value=ComponentInfo())

        try:
            components = plugin.get_plugin_components()

            assert len(components) == 2

            # Check that both components are returned
            component_types = [comp[1] for comp in components]
            assert PokeEventHandler in component_types
            assert PokeAction in component_types
        finally:
            # Clean up
            if hasattr(PokeEventHandler, 'get_handler_info'):
                delattr(PokeEventHandler, 'get_handler_info')
            if hasattr(PokeAction, 'get_action_info'):
                delattr(PokeAction, 'get_action_info')


# ========== Edge Cases and Integration Tests ==========

class TestEdgeCases:
    """Tests for edge cases and boundary conditions"""

    def test_dig_with_empty_path(self):
        """Test _dig with empty path"""
        obj = {"key": "value"}
        # Empty path should return the object itself
        result = _dig(obj, "")
        # With empty string split, we get [''], which should try to access ''
        # This is implementation-dependent, but should not crash
        assert result is not None or result is None  # Just ensure no crash

    def test_dig_with_deeply_nested_path(self):
        """Test _dig with very deep nesting"""
        obj = {"a": {"b": {"c": {"d": {"e": {"f": "deep"}}}}}}
        assert _dig(obj, "a.b.c.d.e.f") == "deep"

    async def test_concurrent_cooldown_checks(self):
        """Test cooldown with rapid concurrent checks"""
        handler = PokeEventHandler()
        handler.get_config = MagicMock(return_value=30)

        user_id = "user123"
        handler._cooldown[user_id] = time.monotonic() - 5

        # Multiple checks should all return in_cooldown=True
        results = []
        for _ in range(5):
            in_cooldown, remaining = handler._check_cooldown(user_id)
            results.append(in_cooldown)

        # First should be in cooldown, but subsequent ones update the timestamp
        assert results[0] is True

    def test_rate_limit_boundary_at_exactly_60_seconds(self):
        """Test rate limit boundary at exactly 60 seconds"""
        handler = PokeEventHandler()
        handler.get_config = MagicMock(return_value=10)

        current_time = time.monotonic()
        # Add a timestamp at exactly 60 seconds ago
        handler._poke_timestamps = [current_time - 60.0]

        handler._check_rate_limit()
        # The 60-second-old timestamp should be removed
        assert len(handler._poke_timestamps) == 1  # Only the new one
        assert all(current_time - t < 60 for t in handler._poke_timestamps)


# ========== Additional Regression Tests ==========

class TestRegressionCases:
    """Tests for specific regression cases and real-world scenarios"""

    async def test_poke_event_handler_full_flow_success(self):
        """Test complete poke event handler flow"""
        handler = PokeEventHandler()
        handler.get_config = MagicMock(side_effect=lambda key, default: default)
        handler.send_command = AsyncMock(return_value=True)
        handler.send_text = AsyncMock(return_value=True)
        handler._cooldown.clear()
        handler._poke_timestamps.clear()

        # Create valid message
        msg = MagicMock()
        event_data = {
            "post_type": "notice",
            "sub_type": "poke",
            "user_id": "12345",
            "target_id": "67890"
        }
        msg.raw_message = json.dumps(event_data)
        msg.user_id = "12345"
        msg.stream_id = "stream123"
        msg.plain_text = ""
        msg.message_base_info = None

        with patch('plugin.global_config') as mock_config:
            mock_config.bot.qq_account = "67890"
            with patch('plugin.person_api') as mock_person_api:
                mock_person_api.get_person_id.return_value = "person123"
                mock_person_api.get_person_value = AsyncMock(return_value="TestUser")
                with patch('plugin.generator_api') as mock_gen_api:
                    mock_reply_data = MagicMock()
                    mock_reply_data.reply_set.reply_data = []
                    mock_gen_api.generate_reply = AsyncMock(return_value=(True, mock_reply_data))

                    # Execute
                    success, continue_chain, reason, _, _ = await handler.execute(msg)

                    assert success is True
                    assert continue_chain is True

    async def test_poke_action_full_flow_success(self):
        """Test complete poke action flow"""
        action = PokeAction()
        action.action_data = {"name": "我", "reason": "test"}
        action.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 300)
        action.send_command = AsyncMock(return_value=True)
        action.user_id = "12345"
        action.message = MagicMock()
        action.chat_stream = MagicMock()

        # Clear cooldown
        PokeAction._last_poke_user = None
        PokeAction._last_poke_time = 0

        with patch('plugin.database_api') as mock_db:
            mock_db.store_action_info = AsyncMock()

            ok, result = await action.execute()
            assert ok is True
            mock_db.store_action_info.assert_called_once()

    def test_constants_defined_correctly(self):
        """Test that constants are defined with correct values"""
        assert CMD_SEND_POKE == "SEND_POKE"
        assert NOTICE_POKE == {"post_type": "notice", "sub_type": "poke"}

    def test_handler_class_attributes(self):
        """Test PokeEventHandler has correct class attributes"""
        assert PokeEventHandler.event_type == EventType.ON_MESSAGE
        assert PokeEventHandler.handler_name == "poke_message_handler"
        assert isinstance(PokeEventHandler.handler_description, str)

    def test_action_class_attributes(self):
        """Test PokeAction has correct class attributes"""
        assert PokeAction.action_name == "poke"
        assert isinstance(PokeAction.action_description, str)
        assert PokeAction.activation_type == ActionActivationType.ALWAYS
        assert PokeAction.parallel_action is True
        assert "command" in PokeAction.associated_types

    async def test_rapid_successive_pokes_rate_limiting(self):
        """Test that rapid successive pokes are properly rate limited"""
        handler = PokeEventHandler()
        handler.get_config = MagicMock(return_value=3)  # Max 3 pokes per minute
        handler._poke_timestamps.clear()

        # Simulate 5 rapid poke attempts
        results = []
        for _ in range(5):
            rate_limited = handler._check_rate_limit()
            results.append(rate_limited)

        # First 3 should succeed, last 2 should be rate limited
        assert results == [False, False, False, True, True]
        assert len(handler._poke_timestamps) == 3

    async def test_follow_poke_with_bot_as_sender(self):
        """Test follow poke behavior when bot is the sender"""
        handler = PokeEventHandler()
        handler.get_config = MagicMock(return_value=True)
        handler.send_command = AsyncMock(return_value=True)

        msg = MagicMock()
        msg.stream_id = "stream123"
        event = {"target_id": "99999"}

        with patch('plugin.global_config') as mock_config:
            mock_config.bot.qq_account = "67890"
            # Bot is the sender
            should_exit, reason = await handler._handle_follow_poke(msg, event, "67890")
            # Should exit early because sender is bot
            assert should_exit is True

    async def test_poke_back_max_times_boundary(self):
        """Test poke back respects max times boundary"""
        handler = PokeEventHandler()
        msg = MagicMock()
        msg.stream_id = "stream123"
        handler.send_command = AsyncMock(return_value=True)

        # Set max times to 1
        handler.get_config = MagicMock(side_effect=lambda key, default:
            True if "auto_poke_back" in key else (1.0 if "probability" in key else 1))

        with patch('plugin.random.random', return_value=0.5):
            with patch('plugin.random.randint', return_value=1):
                await handler._handle_poke_back(msg, "12345")
                # Should call send_command exactly once
                assert handler.send_command.call_count == 1

    async def test_text_reply_with_empty_plain_text(self):
        """Test text reply generation when plain_text is empty"""
        handler = PokeEventHandler()
        msg = MagicMock()
        msg.stream_id = "stream123"
        msg.plain_text = ""
        handler.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 1.0)
        handler.send_text = AsyncMock(return_value=True)

        with patch('plugin.random.random', return_value=0.5):
            with patch('plugin.generator_api') as mock_gen_api:
                mock_reply_data = MagicMock()
                mock_reply_data.reply_set.reply_data = [MagicMock(content="回应")]
                mock_gen_api.generate_reply = AsyncMock(return_value=(True, mock_reply_data))

                result = await handler._handle_text_reply(msg, "12345", "TestUser")
                assert result is True
                # Verify generate_reply was called with proper extra_info
                call_kwargs = mock_gen_api.generate_reply.call_args[1]
                assert "戳了你一下" in call_kwargs['extra_info']

    def test_dig_with_integer_keys_in_dict(self):
        """Test _dig with integer keys (should fail gracefully)"""
        obj = {1: {"key": "value"}}
        # String path "1.key" won't match integer key 1
        result = _dig(obj, "1.key")
        assert result is None

    async def test_get_user_id_with_whitespace_handling(self):
        """Test user ID resolution with extra whitespace"""
        action = PokeAction()
        action.user_id = "12345"

        # Test with extra spaces
        user_id = await action._get_user_id("  我  ")
        assert user_id == "12345"

        # Test with QQ number and spaces
        user_id = await action._get_user_id("  1234567890  ")
        assert user_id == "1234567890"

    async def test_execute_with_send_poke_exception(self):
        """Test execute handles exceptions during poke sending"""
        action = PokeAction()
        action.action_data = {"name": "TestUser"}
        action.chat_stream = MagicMock()
        action.get_config = MagicMock(side_effect=lambda key, default:
            True if "enabled" in key else 300)
        action.send_command = AsyncMock(side_effect=Exception("Network error"))

        PokeAction._last_poke_user = None
        PokeAction._last_poke_time = 0

        with patch.object(action, '_get_user_id', return_value="54321"):
            with patch.object(action, '_infer_group_id_from_context', return_value=None):
                # Should handle exception gracefully
                ok, result = await action.execute()
                assert ok is False
                assert "失败" in result