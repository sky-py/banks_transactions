# This module is for sending service sync messages through sync telegram library.

import telebot
from config.settings import TelegramSettings


def build_service_tg_sender(settings: TelegramSettings):
    bot_tools = telebot.TeleBot(settings.token)

    def send_service_tg_message(text: str, silent: bool = False) -> None:
        text = text[0:settings.max_message_length]
        if settings.enabled:
            bot_tools.send_message(settings.admin_chat_id, text, disable_notification=silent)
        else:
            print('===TEST=== ', text)

    return send_service_tg_message
