from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router()


@router.message(Command("start"))
async def start_handler(message: Message) -> None:
    await message.reply("สวัสดี! พิมพ์อะไรก็ได้เลย\nใช้ /help เพื่อดูคำสั่งที่ใช้ได้")


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.reply(
        "<b>คำสั่งที่ใช้ได้:</b>\n"
        "/start — เริ่มต้นใช้งาน\n"
        "/help — แสดงคำสั่งทั้งหมด\n"
        "/clear — ล้างประวัติสนทนา\n"
        "/remember &lt;ข้อความ&gt; — จำข้อมูลระยะยาว\n"
        "/recall &lt;คำค้น&gt; — ค้นหาข้อมูลที่จำไว้\n"
        "/note &lt;ข้อความ&gt; — บันทึก note\n"
        "/remind &lt;ข้อความ&gt; &lt;เวลา&gt; — ตั้ง reminder\n"
        "/reminders — ดู reminders ที่รออยู่\n"
        "/summary — สรุปสัปดาห์ที่ผ่านมา\n"
        "/quota — ดู Gemini quota วันนี้",
    )
