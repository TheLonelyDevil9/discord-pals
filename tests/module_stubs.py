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
    discord.app_commands = types.SimpleNamespace(CommandTree=CommandTree)
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
