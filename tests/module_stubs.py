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

    class HTTPException(Exception):
        pass

    discord.User = User
    discord.Member = Member
    discord.Message = Message
    discord.Attachment = Attachment
    discord.Emoji = Emoji
    discord.Guild = Guild
    discord.HTTPException = HTTPException
    discord.utils = types.SimpleNamespace(get=lambda iterable, **kwargs: None)

    sys.modules["discord"] = discord
