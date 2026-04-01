import sys
import types


if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv


if "aiohttp" not in sys.modules:
    aiohttp = types.ModuleType("aiohttp")

    class ClientTimeout:
        def __init__(self, *args, **kwargs):
            pass

    class ClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def close(self):
            return None

    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.ClientSession = ClientSession
    sys.modules["aiohttp"] = aiohttp


if "discord" not in sys.modules:
    discord = types.ModuleType("discord")

    class User:
        pass

    class Member(User):
        pass

    class Message:
        pass

    class Attachment:
        pass

    class Emoji:
        pass

    class Guild:
        emojis = []

    class DMChannel:
        pass

    class HTTPException(Exception):
        pass

    class Intents:
        @staticmethod
        def default():
            return types.SimpleNamespace(message_content=False, members=False, emojis=False)

    class Client:
        def __init__(self, *args, **kwargs):
            self.loop = types.SimpleNamespace(is_running=lambda: False)

        def event(self, func):
            return func

    class CommandTree:
        def __init__(self, client):
            self.client = client

        async def sync(self):
            return []

        def add_command(self, command):
            return command

    class Choice:
        def __init__(self, name=None, value=None):
            self.name = name
            self.value = value

    class Group:
        def __init__(self, name=None, description=None):
            self.name = name
            self.description = description

        def command(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator

    def _passthrough_decorator(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    discord.User = User
    discord.Member = Member
    discord.Message = Message
    discord.Attachment = Attachment
    discord.Emoji = Emoji
    discord.Guild = Guild
    discord.DMChannel = DMChannel
    discord.HTTPException = HTTPException
    discord.Intents = Intents
    discord.Client = Client
    discord.app_commands = types.SimpleNamespace(
        CommandTree=CommandTree,
        Choice=Choice,
        Group=Group,
        describe=_passthrough_decorator,
        choices=_passthrough_decorator,
    )
    discord.utils = types.SimpleNamespace(get=lambda iterable, **kwargs: None)

    sys.modules["discord"] = discord


if "openai" not in sys.modules:
    openai = types.ModuleType("openai")

    class AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            pass

    class RateLimitError(Exception):
        pass

    class APIError(Exception):
        pass

    openai.AsyncOpenAI = AsyncOpenAI
    openai.RateLimitError = RateLimitError
    openai.APIError = APIError
    sys.modules["openai"] = openai


if "emoji" not in sys.modules:
    emoji = types.ModuleType("emoji")
    _emoji_names = {
        "😀": "grinning_face",
        "🔥": "fire",
        "❤️": "red_heart",
        "❤": "red_heart",
    }

    def emoji_list(text):
        matches = []
        for idx, char in enumerate(text or ""):
            if char in _emoji_names:
                matches.append({
                    "emoji": char,
                    "match_start": idx,
                    "match_end": idx + len(char),
                })
        return matches

    def demojize(value, delimiters=(":", ":")):
        name = _emoji_names.get(value, "emoji")
        return f"{delimiters[0]}{name}{delimiters[1]}"

    emoji.emoji_list = emoji_list
    emoji.demojize = demojize
    emoji.__version__ = "2.15.0"
    sys.modules["emoji"] = emoji


if "prometheus_client" not in sys.modules:
    prometheus_client = types.ModuleType("prometheus_client")

    class _Metric:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            return None

        def observe(self, *args, **kwargs):
            return None

        def set(self, *args, **kwargs):
            return None

        def info(self, *args, **kwargs):
            return None

    prometheus_client.Counter = _Metric
    prometheus_client.Histogram = _Metric
    prometheus_client.Gauge = _Metric
    prometheus_client.Info = _Metric
    prometheus_client.start_http_server = lambda *args, **kwargs: None
    sys.modules["prometheus_client"] = prometheus_client
