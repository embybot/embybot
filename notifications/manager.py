# -*- coding: utf-8 -*-

from . import telegram_driver
from .telegram_driver import is_super_admin, is_user_authorized
from ..core import config

def send_notification(text: str, photo_url: str = None, chat_id=None, inline_buttons: list = None, disable_preview: bool = False):
    telegram_driver.send_telegram_notification(
        text=text,
        photo_url=photo_url,
        chat_id=chat_id,
        inline_buttons=inline_buttons,
        disable_preview=disable_preview
    )

def send_deletable_notification(text: str, photo_url: str = None, chat_id=None, inline_buttons: list = None, delay_seconds: int = 60, disable_preview: bool = False):
    telegram_driver.send_deletable_telegram_notification(
        text=text,
        photo_url=photo_url,
        chat_id=chat_id,
        inline_buttons=inline_buttons,
        delay_seconds=delay_seconds,
        disable_preview=disable_preview
    )

def send_to_targets(target_ids: list, is_deletable: bool, **kwargs):
    if not target_ids:
        return

    text = kwargs.get('text')
    photo_url = kwargs.get('photo_url')
    inline_buttons = kwargs.get('inline_buttons')
    delay_seconds = kwargs.get('delay_seconds', 60)
    disable_preview = kwargs.get('disable_preview', False)

    for chat_id in target_ids:
        if is_deletable:
            telegram_driver.send_deletable_telegram_notification(
                text=text, 
                photo_url=photo_url, 
                chat_id=chat_id, 
                inline_buttons=inline_buttons, 
                delay_seconds=delay_seconds, 
                disable_preview=disable_preview
            )
        else:
            telegram_driver.send_telegram_notification(
                text=text, 
                photo_url=photo_url, 
                chat_id=chat_id, 
                inline_buttons=inline_buttons, 
                disable_preview=disable_preview
            )

def send_simple_message(text: str, chat_id=None, delay_seconds: int = 60):
    telegram_driver.send_simple_telegram_message(
        text=text,
        chat_id=chat_id,
        delay_seconds=delay_seconds
    )

def edit_message(chat_id, message_id, text: str, inline_buttons: list = None, disable_preview: bool = False):
    return telegram_driver.edit_telegram_message(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        inline_buttons=inline_buttons,
        disable_preview=disable_preview
    )

def delete_message(chat_id, message_id):
    telegram_driver.delete_telegram_message(chat_id, message_id)


def safe_edit_or_send(chat_id, message_id, text: str, buttons: list = None, disable_preview: bool = True, delete_after: int = None):
    telegram_driver.safe_edit_or_send_message(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        buttons=buttons,
        disable_preview=disable_preview,
        delete_after=delete_after
    )