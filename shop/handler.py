from aiogram import types
from shop.menu import buy_menu

async def open_buy(msg: types.Message):
    await msg.answer("🛒 انتخاب پلن:", reply_markup=buy_menu())
