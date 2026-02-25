from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    # Database
    database_path: str = "family_events.db"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Weather
    weather_api_key: str = ""  # OpenWeatherMap
    weather_lat: float = 30.2241  # Lafayette, LA
    weather_lon: float = -92.0198

    # Twilio SMS (secrets only — recipient is per-user)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # Telegram (secrets only — chat_id could be per-user later)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Email / Resend (secret + sender identity)
    resend_api_key: str = ""
    email_from: str = "Family Events <onboarding@resend.dev>"

    # App
    host: str = "0.0.0.0"
    port: int = 8000
    session_secret: str = ""

    # Basic API rate limiting (per-IP, per-route)
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 20


settings = Settings()
