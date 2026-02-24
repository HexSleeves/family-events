from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Database
    database_path: str = "family_events.db"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Weather
    weather_api_key: str = ""  # OpenWeatherMap
    weather_lat: float = 30.2241  # Lafayette, LA
    weather_lon: float = -92.0198

    # Twilio SMS
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_to_number: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Email (Resend)
    resend_api_key: str = ""
    email_to: str = ""
    email_from: str = "Family Events <events@example.com>"

    # App
    notification_channels: list[str] = ["console"]  # "console", "sms", "telegram", "email"
    host: str = "0.0.0.0"
    port: int = 8000


settings = Settings()
