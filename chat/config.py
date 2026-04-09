from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    bot_token: str= "8600565062:AAF8P9BXO7YFL1F1QFoliEvcuArD2NIQnpo"
    db_host: str = "127.0.0.1"
    db_user: str = "root"
    db_password: str = "1234"
    db_name: str = "fitbot"
    web_app_url : str = "https://snnfitmate.ru/"

    class Config:
        env_file = ".env"

settings = Settings()