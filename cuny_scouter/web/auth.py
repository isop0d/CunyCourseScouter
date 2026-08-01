"""
Discord OAuth2 helpers.

Flow:
  1. /auth/discord          → redirect user to Discord authorization URL
  2. Discord redirects to   → /auth/discord/callback?code=...
  3. Exchange code for token, fetch user info, upsert student, set session
"""
import httpx

from cuny_scouter.config import settings

DISCORD_API = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"


def discord_authorize_url() -> str:
    params = (
        f"client_id={settings.discord_client_id}"
        f"&redirect_uri={settings.discord_redirect_uri}"
        f"&response_type=code"
        f"&scope=identify%20guilds.join"
    )
    return f"{AUTHORIZE_URL}?{params}"


async def exchange_code(code: str) -> dict:
    """Exchange an OAuth code for an access token. Returns the token response dict."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{DISCORD_API}/oauth2/token",
            data={
                "client_id": settings.discord_client_id,
                "client_secret": settings.discord_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


async def fetch_discord_user(access_token: str) -> dict:
    """Fetch the authenticated user's profile from Discord."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{DISCORD_API}/users/@me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def join_guild(user_id: str, access_token: str) -> None:
    """Add the user to the configured Discord server using the bot token."""
    if not settings.discord_bot_token or not settings.discord_guild_id:
        return
    async with httpx.AsyncClient() as client:
        await client.put(
            f"{DISCORD_API}/guilds/{settings.discord_guild_id}/members/{user_id}",
            headers={"Authorization": f"Bot {settings.discord_bot_token}"},
            json={"access_token": access_token},
        )
