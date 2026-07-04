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

_VALID_KEYS = {k for k, _ in MESSAGE_KEYS}

# این پیام‌ها در زمان اجرا مقدارهای سیستمی دارند؛ مثل قیمت، موجودی، لینک دعوت و...
# برای همین متن سفارشی آن‌ها با {body} رندر می‌شود تا بخش سیستمی حذف نشود.
DYNAMIC_MESSAGE_KEYS = {"menu_buy", "menu_wallet", "menu_referral"}
PLACEHOLDERS = ("{body}", "{{body}}", "{default}", "{{default}}")


def is_valid_key(key):
    return key in _VALID_KEYS


def is_dynamic_key(key):
    return key in DYNAMIC_MESSAGE_KEYS


def has_system_placeholder(text):
    text = text or ""
    return any(ph in text for ph in PLACEHOLDERS)


def render_template(key, template_text, body_text):
    """
    رندر امن متن‌های قابل ویرایش.
    - برای پیام‌های معمولی، متن سفارشی جایگزین متن پیش‌فرض می‌شود.
    - برای پیام‌های داینامیک، اگر ادمین {body} نگذارد، برای جلوگیری از حذف قیمت/موجودی/لینک،
      متن سفارشی بالای متن سیستمی قرار می‌گیرد.
    """
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


def get(key):
    row = db.get_message(key)

    if not row:
        return None, None

    return row["text"], row["photo_file_id"]


def get_draft(key):
    row = db.get_message(key)
    if not row:
        return None, None
    draft_text = row["draft_text"] if "draft_text" in row.keys() else None
    draft_photo = row["draft_photo_file_id"] if "draft_photo_file_id" in row.keys() else None
    return draft_text, draft_photo


def compose(key, default_text):
    """
    متن منتشرشده ادمین با پشتیبانی از قالب امن رندر می‌شود.
    برای پیام‌های سیستمی/داینامیک، {body} نماینده متن اصلی ربات است.
    Draft فقط برای پیش‌نمایش ادمین است و تا انتشار نهایی برای کاربر نمایش داده نمی‌شود.
    """
    custom_text, photo_file_id = get(key)

    if custom_text and custom_text.strip():
        return render_template(key, custom_text, default_text), photo_file_id

    return default_text, photo_file_id


def compose_preview(key, default_text):
    """
    نسخه پیش‌نمایش: اگر draft وجود داشته باشد با متن نمونه/پیش‌فرض رندر می‌شود؛
    وگرنه متن منتشرشده/پیش‌فرض نمایش داده می‌شود.
    """
    draft_text, draft_photo = get_draft(key)
    if draft_text and draft_text.strip():
        return render_template(key, draft_text, default_text), draft_photo
    final_text, final_photo = compose(key, default_text)
    return final_text, draft_photo or final_photo


async def send(target, key, body_text, reply_markup=None):
    final_text, photo_file_id = compose(key, body_text)

    if photo_file_id:
        if final_text and len(final_text) <= 1024:
            await target.answer_photo(
                photo_file_id,
                caption=final_text,
                reply_markup=reply_markup,
            )
        else:
            await target.answer_photo(photo_file_id)
            if final_text:
                await target.answer(final_text, reply_markup=reply_markup)
            elif reply_markup:
                await target.answer("از منوی پایین استفاده کنید.", reply_markup=reply_markup)
        return

    await target.answer(final_text, reply_markup=reply_markup)


def set_text(key, text):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")

    row = db.get_message(key)

    if row is None:
        db.cur.execute("INSERT INTO messages(key, text, published_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))", (key, text))
    else:
        db.cur.execute("UPDATE messages SET text=?, published_at=datetime('now'), updated_at=datetime('now') WHERE key=?", (text, key))

    db.conn.commit()


def set_photo(key, photo_file_id):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")

    row = db.get_message(key)

    if row is None:
        db.cur.execute(
            "INSERT INTO messages(key, photo_file_id, published_at, updated_at) VALUES (?, ?, datetime('now'), datetime('now'))",
            (key, photo_file_id),
        )
    else:
        db.cur.execute(
            "UPDATE messages SET photo_file_id=?, published_at=datetime('now'), updated_at=datetime('now') WHERE key=?",
            (photo_file_id, key),
        )

    db.conn.commit()


def set_draft_text(key, text):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")
    db.set_message_draft_text(key, text)


def set_draft_photo(key, photo_file_id):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")
    db.set_message_draft_photo(key, photo_file_id)


def publish_draft(key):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")
    return db.publish_message_draft(key)


def clear_draft(key):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")
    db.clear_message_draft(key)


def clear(key):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")

    db.clear_message(key)
