from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def buy_menu():
    kb = InlineKeyboardMarkup(row_width=2)

    kb.add(
        InlineKeyboardButton("1️⃣", callback_data="buy_1"),
        InlineKeyboardButton("2️⃣", callback_data="buy_2"),
        InlineKeyboardButton("3️⃣", callback_data="buy_3"),
        InlineKeyboardButton("4️⃣", callback_data="buy_4"),
    )

    kb.add(
        InlineKeyboardButton("🔥 خرید عمده", callback_data="buy_bulk")
    )

    kb.add(
        InlineKeyboardButton("🏠 بازگشت", callback_data="back_main")
    )

    return kb
