import logging
import sqlite3
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Request, HTTPException
import httpx
import uvicorn

app = FastAPI(title="OrionTrading Thai Full Engine")
logging.basicConfig(level=logging.INFO)

# ==========================================
# 1. ตั้งค่า Telegram (ใส่ Token และ Chat ID ของคุณ)
# ==========================================
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"  # วาง Token จาก BotFather
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"      # วาง Chat ID ติดลบ (-100...)

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

# ==========================================
# 2. ระบบฐานข้อมูล SQLite บันทึกสถิติ
# ==========================================
def init_db():
    conn = sqlite3.connect("trading_stats.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT,
            symbol TEXT,
            status TEXT,
            pips INTEGER,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def record_trade_result(symbol: str, status: str, pips_str: str):
    try:
        numbers = re.findall(r'[-+]?\d+', pips_str.replace(',', ''))
        pips_value = int(numbers[0]) if numbers else 0
        
        now_th = datetime.now(BANGKOK_TZ)
        today_date = now_th.strftime("%Y-%m-%d")
        timestamp_str = now_th.strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect("trading_stats.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO trade_history (trade_date, symbol, status, pips, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (today_date, symbol, status, pips_value, timestamp_str))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Error recording trade result: {e}")

# ==========================================
# 3. ฟังก์ชัน Telegram & ฟอร์แมตชื่อสินทรัพย์
# ==========================================
async def send_to_telegram(text: str):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(TELEGRAM_API_URL, json=payload, timeout=10.0)
            response.raise_for_status()
        except Exception as e:
            logging.error(f"Failed to send Telegram message: {e}")

def format_symbol(symbol: str) -> str:
    sym = symbol.upper()
    if "XAUUSD" in sym or "GOLD" in sym:
        return "🏆 XAUUSD (Gold)"
    elif "USOIL" in sym or "WTI" in sym or "OIL" in sym:
        return "🛢️ USOIL (Crude Oil)"
    elif "BTCUSD" in sym or "BTC" in sym:
        return "⚡ BTCUSD (Bitcoin)"
    elif "ETHUSD" in sym or "ETH" in sym:
        return "🔷 ETHUSD (Ethereum)"
    elif "EURUSD" in sym:
        return "💶 EURUSD"
    elif "GBPUSD" in sym:
        return "💷 GBPUSD"
    elif "USDJPY" in sym:
        return "💴 USDJPY"
    elif "GBPJPY" in sym:
        return "🐉 GBPJPY"
    return f"📊 {sym}"

# ==========================================
# 4. ระบบข่าวแรง (High Impact) + แยกสินทรัพย์ (ขึ้น/ลง)
# ==========================================
async def fetch_and_send_daily_news():
    now_th = datetime.now(BANGKOK_TZ)
    today_str = now_th.strftime("%m-%d-%Y")
    date_display = now_th.strftime("%d/%m/%Y")
    
    high_impact_news = []
    url = "https://www.forexfactory.com/ff_calendar_thisweek.xml"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10.0)
            if resp.status_code == 200:
                root = ET.fromstring(resp.content)
                for event in root.findall('event'):
                    date_val = event.findtext('date', '')
                    impact_val = event.findtext('impact', '')
                    
                    if date_val == today_str and impact_val == 'High':
                        title = event.findtext('title', '')
                        country = event.findtext('country', '')
                        time_str = event.findtext('time', '')
                        forecast = event.findtext('forecast', 'N/A')
                        previous = event.findtext('previous', 'N/A')
                        
                        thai_time = "ไม่ระบุเวลา"
                        if time_str and time_str.lower() not in ['all day', 'day 1', 'day 2']:
                            try:
                                et_time = datetime.strptime(f"{date_val} {time_str}", "%m-%d-%Y %I:%M%p")
                                th_time_obj = et_time + timedelta(hours=11)
                                thai_time = th_time_obj.strftime("%H:%M น.")
                            except Exception:
                                thai_time = time_str
                        
                        high_impact_news.append({
                            "country": country,
                            "time": thai_time,
                            "title": title,
                            "forecast": forecast if forecast else "N/A",
                            "previous": previous if previous else "N/A"
                        })
    except Exception as e:
        logging.error(f"Failed to fetch news feed: {e}")

    if not high_impact_news:
        news_msg = (
            f"🚨 <b>OrionTrading Thai - สรุปข่าวแรงประจำวัน</b>\n"
            f"📅 <b>ประจำวันที่:</b> <code>{date_display}</code> (เวลาไทย)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🟢 <b>ไม่มีข่าวกล่องแดง (High Impact) ในวันนี้</b>\n"
            f"💡 ตลาดเคลื่อนไหวตามปัจจัยทางเทคนิคอล สามารถรันตามสัญญาณ Indicator ได้ตามปกติ"
        )
        await send_to_telegram(news_msg)
        return

    news_list_str = "\n".join([
        f"• 🌐 <b>{n['country']} | {n['time']}</b> - {n['title']} (คาดการณ์: <code>{n['forecast']}</code>)"
        for n in high_impact_news
    ])

    asset_analysis = []
    countries_present = {n['country'] for n in high_impact_news}
    titles_present = " ".join([n['title'].upper() for n in high_impact_news])

    if 'USD' in countries_present:
        usd_news = [n for n in high_impact_news if n['country'] == 'USD']
        usd_titles = ", ".join([f"{n['title']} ({n['time']})" for n in usd_news])
        asset_analysis.append(
            f"🏆 <b>XAUUSD (ทองคำ) & ⚡ BTCUSD</b>\n"
            f"• <b>ข่าวที่กระทบ:</b> 🇺🇸 USD {usd_titles}\n"
            f"• 🟢 <b>โอกาสขึ้น:</b> ตัวเลขจริงแย่กว่าคาด (USD อ่อนค่า)\n"
            f"• 🔴 <b>โอกาสลง:</b> ตัวเลขจริงดีกว่าคาด (USD แข็งค่า)"
        )

    if 'EUR' in countries_present or 'USD' in countries_present:
        eur_news = [n for n in high_impact_news if n['country'] in ['EUR', 'USD']]
        eur_titles = ", ".join([f"{n['country']} {n['title']} ({n['time']})" for n in eur_news])
        asset_analysis.append(
            f"💶 <b>EURUSD</b>\n"
            f"• <b>ข่าวที่กระทบ:</b> {eur_titles}\n"
            f"• 🟢 <b>โอกาสขึ้น:</b> ข่าว EUR ดีกว่าคาด หรือ USD แย่กว่าคาด\n"
            f"• 🔴 <b>โอกาสลง:</b> ข่าว EUR แย่กว่าคาด หรือ USD ดีกว่าคาด"
        )

    if 'GBP' in countries_present or 'JPY' in countries_present:
        gbp_news = [n for n in high_impact_news if n['country'] in ['GBP', 'JPY']]
        gbp_titles = ", ".join([f"{n['country']} {n['title']} ({n['time']})" for n in gbp_news])
        asset_analysis.append(
            f"💷 <b>GBPUSD / 🐉 GBPJPY</b>\n"
            f"• <b>ข่าวที่กระทบ:</b> {gbp_titles}\n"
            f"• 🟢 <b>โอกาสขึ้น:</b> ตัวเลข GBP ออกมาดีกว่าคาด\n"
            f"• 🔴 <b>โอกาสลง:</b> ตัวเลข GBP ออกมาแย่กว่าคาด"
        )

    if 'CAD' in countries_present or 'OIL' in titles_present or 'INVENTORIES' in titles_present:
        asset_analysis.append(
            f"🛢️ <b>USOIL (น้ำมันดิบ)</b>\n"
            f"• <b>ข่าวที่กระทบ:</b> ตารางสต็อกน้ำมัน / เศรษฐกิจสินค้าโภคภัณฑ์\n"
            f"• 🟢 <b>โอกาสขึ้น:</b> สต็อกน้ำมันดิบลดลงมากกว่าคาด\n"
            f"• 🔴 <b>โอกาสลง:</b> สต็อกน้ำมันดิบเพิ่มขึ้นมากกว่าคาด"
        )

    asset_analysis_str = "\n\n".join(asset_analysis)

    news_msg = (
        f"🚨 <b>OrionTrading Thai - สรุปข่าวแรงประจำวัน & แยกวิเคราะห์ตามสินทรัพย์</b>\n"
        f"📅 <b>ประจำวันที่:</b> <code>{date_display}</code> (เวลาประเทศไทย)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 <b>รายการข่าวแรงระดับ High Impact วันนี้:</b>\n"
        f"{news_list_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>วิเคราะห์แนวโน้มราคาแยกตามสินทรัพย์ (Asset Breakdown):</b>\n\n"
        f"{asset_analysis_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>คำแนะนำ: หลีกเลี่ยงการเปิดออเดอร์ก่อนข่าวออก 15-30 นาที</i>"
    )
    await send_to_telegram(news_msg)

# ==========================================
# 5. ฟังก์ชันสรุปผลรายวัน (TP/SL)
# ==========================================
async def generate_and_send_daily_summary():
    now_th = datetime.now(BANGKOK_TZ)
    today_date = now_th.strftime("%Y-%m-%d")
    date_display = now_th.strftime("%d/%m/%Y")

    conn = sqlite3.connect("trading_stats.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT symbol, status, pips FROM trade_history WHERE trade_date = ?
    """, (today_date,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        summary_msg = (
            f"📊 <b>OrionTrading Thai - สรุปผลการเทรดประจำวัน</b>\n"
            f"📅 <b>ประจำวันที่:</b> <code>{date_display}</code> (เวลาไทย)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"😴 วันนี้ไม่มีสัญญาณปิดทำกำไร/ตัดขาดทุน (TP/SL)"
        )
        await send_to_telegram(summary_msg)
        return

    total_trades = len(rows)
    wins = sum(1 for r in rows if "TP" in r[1])
    losses = sum(1 for r in rows if r[1] == "SL")
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    net_pips = sum(r[2] for r in rows)
    pips_sign = "+" if net_pips > 0 else ""

    asset_stats = {}
    for sym, status, pips in rows:
        if sym not in asset_stats:
            asset_stats[sym] = {"win": 0, "loss": 0, "pips": 0}
        if "TP" in status:
            asset_stats[sym]["win"] += 1
        elif status == "SL":
            asset_stats[sym]["loss"] += 1
        asset_stats[sym]["pips"] += pips

    asset_text_list = []
    for sym, stat in asset_stats.items():
        sym_display = format_symbol(sym)
        sym_pips_sign = "+" if stat["pips"] > 0 else ""
        asset_text_list.append(
            f"{sym_display}\n"
            f"• ผลการเทรด: 🟢 Win {stat['win']} | 🔴 Loss {stat['loss']}\n"
            f"• รวมกำไร/ขาดทุน: <code>{sym_pips_sign}{stat['pips']:,} Points</code>"
        )

    asset_breakdown_str = "\n\n".join(asset_text_list)

    summary_msg = (
        f"📊 <b>OrionTrading Thai - สรุปผลการเทรดประจำวัน</b>\n"
        f"📅 <b>ประจำวันที่:</b> <code>{date_display}</code> (เวลาไทย)\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📈 <b>สถิติภาพรวม (Overview):</b>\n"
        f"• <b>ออเดอร์ปิดทั้งหมด:</b> <code>{total_trades}</code> ออเดอร์\n"
        f"• 🟢 <b>ชนะ (Win):</b> <code>{wins}</code> | 🔴 <b>แพ้ (Loss):</b> <code>{losses}</code>\n"
        f"• 🎯 <b>Win Rate:</b> <code>{win_rate:.1f}%</code>\n"
        f"• 💰 <b>กำไร/ขาดทุนสุทธิ:</b> <code>{pips_sign}{net_pips:,} Points</code>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>สรุปผลแยกตามสินทรัพย์ (Asset Breakdown):</b>\n\n"
        f"{asset_breakdown_str}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <i>ระบบบันทึกผลอัตโนมัติ OrionTrading Thai Engine</i>"
    )
    await send_to_telegram(summary_msg)

# ==========================================
# 6. Webhook Receiver & Endpoints
# ==========================================
async def handle_new_signal(data: dict):
    symbol_display = format_symbol(data.get("symbol", "UNKNOWN"))
    action = data.get("action", "BUY")
    action_emoji = "🟢" if action == "BUY" else "🔴"
    
    msg = (
        f"🌌 <b>OrionTrading Thai Signal</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>สินทรัพย์:</b> {symbol_display}\n"
        f"<b>คำสั่ง:</b> {action_emoji} <b>{action} LIMIT / NOW</b>\n\n"
        f"📍 <b>โซนราคาเข้า (Entry Zone):</b>\n"
        f"<code>{data.get('entry_min')} - {data.get('entry_max')}</code>\n"
        f"⚡ <i>ถ้าราคาอยู่ในช่วงนี้ สามารถเข้าเทรดได้ทันที</i>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 <b>เป้าหมายทำกำไร (Take Profit):</b>\n"
        f"• <b>TP 1:</b> <code>{data.get('tp1')}</code>\n"
        f"• <b>TP 2:</b> <code>{data.get('tp2')}</code>\n"
        f"• <b>TP 3:</b> <code>{data.get('tp3')}</code>\n\n"
        f"🛑 <b>จุดตัดขาดทุน (Stop Loss):</b>\n"
        f"• <b>SL:</b> <code>{data.get('sl')}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <i>คำแนะนำ: ควบคุมความเสี่ยงไม่เกิน 1-2% Per Trade</i>"
    )
    await send_to_telegram(msg)

async def handle_update_signal(data: dict):
    symbol = data.get("symbol", "UNKNOWN")
    status = data.get("status", "UPDATE")
    pips = data.get("pips", "0 Points")
    
    record_trade_result(symbol, status, pips)
    
    symbol_display = format_symbol(symbol)
    is_tp = "TP" in status
    status_header = f"🎉 <b>HIT {status} REACHED!</b>" if is_tp else "🛑 <b>STOP LOSS HIT</b>"
    
    msg = (
        f"🎯 <b>OrionTrading Thai Update</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>สินทรัพย์:</b> {symbol_display}\n"
        f"<b>สถานะ:</b> {status_header}\n\n"
        f"📌 <b>ราคาปัจจุบัน/ชนเป้า:</b> <code>{data.get('hit_price')}</code>\n"
        f"💰 <b>ผลลัพธ์:</b> <code>{pips}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>คำแนะนำการจัดการพอร์ต:</b>\n"
        f"<i>{data.get('advice')}</i>"
    )
    await send_to_telegram(msg)

@app.post("/webhook")
async def webhook_receiver(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
        
    type_event = data.get("type", "NEW_SIGNAL")
    if type_event == "NEW_SIGNAL":
        await handle_new_signal(data)
    elif type_event == "UPDATE":
        await handle_update_signal(data)
        
    return {"status": "success"}

@app.get("/daily-news")
async def trigger_daily_news():
    await fetch_and_send_daily_news()
    return {"status": "daily news sent"}

@app.get("/daily-summary")
async def trigger_daily_summary():
    await generate_and_send_daily_summary()
    return {"status": "summary sent"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)