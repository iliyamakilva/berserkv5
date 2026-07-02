from aiogram import Bot, Dispatcher, executor, types
from config import BOT_TOKEN
from keyboards.main import main_reply_kb
from shop.handler import open_buy

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=['start'])
async def start(msg: types.Message):
    await msg.answer("⚡ Berserk VPN Ready", reply_markup=main_reply_kb(msg.from_user.id))

@dp.message_handler(text="🛒 خرید سرویس")
async def buy(msg: types.Message):
    await open_buy(msg)

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
