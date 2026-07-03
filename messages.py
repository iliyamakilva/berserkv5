import db

MESSAGE_KEYS = [
    ("welcome", "پیام خوش‌آمد /start"),
    ("menu_buy", "پیام صفحه خرید"),
    ("menu_wallet", "پیام صفحه کیف پول"),
    ("menu_referral", "پیام صفحه دعوت دوستان"),
]

_VALID_KEYS = {k for k, _ in MESSAGE_KEYS}


def get(key):
    row = db.get_message(key)

    if not row:
        return None, None

    return row["text"], row["photo_file_id"]


def compose(key, default_text):
    """
    اگر ادمین از پنل «ویرایش پیام‌ها» متنی ثبت کرده باشد،
    آن متن جایگزین کامل متن پیش‌فرض می‌شود.

    نسخه‌های قبلی متن ادمین را به بالای متن پیش‌فرض اضافه می‌کردند؛
    همین باعث می‌شد پیام Start یا صفحه خرید دو تکه و شلوغ دیده شود.
    """
    custom_text, photo_file_id = get(key)

    if custom_text and custom_text.strip():
        return custom_text.strip(), photo_file_id

    return default_text, photo_file_id


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
        db.cur.execute("INSERT INTO messages(key, text) VALUES (?, ?)", (key, text))
    else:
        db.cur.execute("UPDATE messages SET text=? WHERE key=?", (text, key))

    db.conn.commit()


def set_photo(key, photo_file_id):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")

    row = db.get_message(key)

    if row is None:
        db.cur.execute(
            "INSERT INTO messages(key, photo_file_id) VALUES (?, ?)",
            (key, photo_file_id),
        )
    else:
        db.cur.execute(
            "UPDATE messages SET photo_file_id=? WHERE key=?",
            (photo_file_id, key),
        )

    db.conn.commit()


def clear(key):
    if key not in _VALID_KEYS:
        raise ValueError("invalid message key")

    db.clear_message(key)
