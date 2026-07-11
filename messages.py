"""Editable message templates and safe Telegram rendering."""

from __future__ import annotations

import db

MESSAGE_KEYS = [
    ("welcome", "پیام خوش‌آمد /start"),
    ("main_menu", "پیام منوی اصلی"),
    ("menu_buy", "پیام صفحه خرید"),
    ("menu_wallet", "پیام صفحه کیف پول"),
    ("menu_referral", "پیام صفحه دعوت دوستان"),
    ("my_services_empty", "متن خالی بودن سرویس‌های من"),
    ("guide_home", "متن اصلی آموزش اتصال"),
    ("guide_android", "آموزش اتصال اندروید"),
    ("guide_ios", "آموزش اتصال آیفون"),
    ("guide_windows", "آموزش اتصال ویندوز"),
    ("guide_mac", "آموزش اتصال مک"),
    ("guide_troubleshoot", "راهنمای مشکل اتصال"),
    ("guide_update", "آموزش بروزرسانی ساب‌لینک"),
    ("support_intro", "متن شروع پشتیبانی"),
    ("rules", "قوانین و شرایط خرید"),
]

_VALID_KEYS = {key for key, _ in MESSAGE_KEYS}
DYNAMIC_MESSAGE_KEYS = {"menu_buy", "menu_wallet", "menu_referral"}
PLACEHOLDERS = ("{body}", "{{body}}", "{default}", "{{default}}")
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


def is_valid_key(key):
    return key in _VALID_KEYS


def is_dynamic_key(key):
    return key in DYNAMIC_MESSAGE_KEYS


def has_system_placeholder(text):
    text = text or ""
    return any(placeholder in text for placeholder in PLACEHOLDERS)


def render_template(key, template_text, body_text):
    """Render a template without allowing dynamic system data to disappear."""
    template_text = (template_text or "").strip()
    body_text = body_text or ""

    if not template_text:
        return body_text

    rendered = template_text
    for placeholder in PLACEHOLDERS:
        rendered = rendered.replace(placeholder, body_text)

    if is_dynamic_key(key) and not has_system_placeholder(template_text) and body_text.strip():
        rendered = f"{template_text}\n\n{body_text}".strip()

    return rendered.strip()


def split_text(text, limit=TELEGRAM_TEXT_LIMIT):
    text = str(text or "")
    if len(text) <= limit:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()
    if remaining or not chunks:
        chunks.append(remaining)
    return chunks


def get(key):
    row = db.get_message(key)
    if not row:
        return None, None
    return row["text"], row["photo_file_id"]


def get_draft(key):
    row = db.get_message(key)
    if not row:
        return None, None
    return row["draft_text"], row["draft_photo_file_id"]


def compose(key, default_text):
    custom_text, photo_file_id = get(key)
    if custom_text and custom_text.strip():
        return render_template(key, custom_text, default_text), photo_file_id
    return default_text, photo_file_id


def compose_preview(key, default_text):
    draft_text, draft_photo = get_draft(key)
    if draft_text and draft_text.strip():
        return render_template(key, draft_text, default_text), draft_photo
    final_text, final_photo = compose(key, default_text)
    return final_text, draft_photo or final_photo


async def send(target, key, body_text, reply_markup=None):
    """Send an editable message safely, splitting oversized text when needed."""
    final_text, photo_file_id = compose(key, body_text)
    sent_messages = []

    if photo_file_id:
        if final_text and len(final_text) <= TELEGRAM_CAPTION_LIMIT:
            sent_messages.append(
                await target.answer_photo(
                    photo_file_id,
                    caption=final_text,
                    reply_markup=reply_markup,
                )
            )
            return sent_messages

        sent_messages.append(await target.answer_photo(photo_file_id))
        chunks = split_text(final_text) if final_text else []
        for index, chunk in enumerate(chunks):
            sent_messages.append(
                await target.answer(
                    chunk,
                    reply_markup=reply_markup if index == len(chunks) - 1 else None,
                )
            )
        if not chunks and reply_markup:
            sent_messages.append(await target.answer("از منوی پایین استفاده کنید.", reply_markup=reply_markup))
        return sent_messages

    chunks = split_text(final_text)
    for index, chunk in enumerate(chunks):
        sent_messages.append(
            await target.answer(
                chunk,
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
            )
        )
    return sent_messages


def _validate_key(key):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")


def set_text(key, text):
    _validate_key(key)
    db.set_message_text(key, text)


def set_photo(key, photo_file_id):
    _validate_key(key)
    db.set_message_photo(key, photo_file_id)


def set_draft_text(key, text):
    _validate_key(key)
    db.set_message_draft_text(key, text)


def set_draft_photo(key, photo_file_id):
    _validate_key(key)
    db.set_message_draft_photo(key, photo_file_id)


def publish_draft(key):
    _validate_key(key)
    return db.publish_message_draft(key)


def clear_draft(key):
    _validate_key(key)
    db.clear_message_draft(key)


def clear(key):
    _validate_key(key)
    db.clear_message(key)
