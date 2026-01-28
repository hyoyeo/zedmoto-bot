import json
import os
import time
import random
import logging
from datetime import datetime, time as dt_time

# Selenium import (여기서부터 핵심)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager

# Telegram import
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue
# ---------------- 설정 ----------------
TELEGRAM_TOKEN = "7900531497:AAGHUYjnIAG7ib5cKgf0uKoCE10EFrwNVAI"
ALLOWED_CHAT_ID = 1715917739
DATA_FILE = "bikes.json"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_driver():
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

# JSON 관리
def load_bikes():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_bikes(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# 크롤링 함수 (매물 페이지 기반 - 실제 클래스명 확인 필요)
def scrape_bike_data(brand, model, min_year, max_year):
    driver = get_driver()
    try:
        # 검색어로 필터링 (사이트 구조상 query 파라미터 사용)
        query = f"{brand} {model}"
        url = f"https://www.reitwagen.co.kr/products/home/used?query={query.replace(' ', '%20')}"
        driver.get(url)
        time.sleep(random.uniform(5, 8))  # Cloudflare/로딩 대기

        # 가격 요소 찾기 - 개발자도구(F12)로 실제 클래스 확인 후 수정!
        # 예: div[class*="price"], span.price, strong.price 등
        price_elements = driver.find_elements(By.CSS_SELECTOR, 'div[class*="price"], span[class*="price"], strong, .price, [class*="amount"]')

        prices = []
        for elem in price_elements:
            text = elem.text.strip()
            if '만원' in text or '₩' in text:
                cleaned = text.replace('만원', '').replace(',', '').replace(' ', '').replace('~', '').replace('₩', '')
                try:
                    p = int(cleaned)
                    if 100 <= p <= 10000:  # 현실적 범위 필터
                        prices.append(p)
                except ValueError:
                    pass

        count = len(prices)
        if count == 0:
            return None, None, None, 0

        avg = round(sum(prices) / count)
        min_p = min(prices)
        max_p = max(prices)
        return avg, min_p, max_p, count

    except Exception as e:
        logging.error(f"스크래핑 실패 ({brand} {model}): {e}")
        return None, None, None, 0
    finally:
        driver.quit()

# 매일 보고서
async def send_daily_report(context: ContextTypes.DEFAULT_TYPE):
    bikes = load_bikes()
    if not bikes:
        return

    message = f"[{datetime.now().strftime('%Y-%m-%d %H:%M KST')}] 오늘 라이트바겐 중고 바이크 시세\n\n"

    for key, info in bikes.items():
        brand = info.get('brand', '')
        model = info.get('model', '')
        years = info.get('years', [])
        if not years:
            continue
        min_y, max_y = min(years), max(years)

        avg, min_p, max_p, count = scrape_bike_data(brand, model, min_y, max_y)

        if avg is None:
            message += f"{brand} {model} ({min_y}~{max_y}): 매물 없거나 오류 발생\n\n"
        else:
            message += f"📌 {brand} {model} ({min_y}~{max_y})\n"
            message += f"   • 평균: {avg:,}만원\n"
            message += f"   • 최저: {min_p:,}만원\n"
            message += f"   • 최고: {max_p:,}만원\n"
            message += f"   • 매물: {count}대\n\n"

    if len(message) > 100:  # 내용 있으면 보내기
        await context.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=message)

# 명령어
async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID:
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text("사용법: /add 브랜드 모델 년식시작-년식끝\n예: /add 혼다 PCX125 2021-2024")
        return
    try:
        brand = args[0]
        model = args[1]
        year_str = args[2]
        miny, maxy = map(int, year_str.split('-'))
        if miny > maxy:
            await update.message.reply_text("년식 범위 오류 (시작 > 끝)")
            return
        years = list(range(miny, maxy + 1))

        bikes = load_bikes()
        key = f"{brand}_{model}".replace(' ', '_').lower()
        bikes[key] = {"brand": brand, "model": model, "years": years}
        save_bikes(bikes)
        await update.message.reply_text(f"추가 완료: {brand} {model} ({miny}-{maxy})")
    except:
        await update.message.reply_text("형식 오류! 예시처럼 입력해주세요")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID: return
    if not context.args:
        await update.message.reply_text("/remove 키\n예: /remove 혼다_pcx125")
        return
    key = context.args[0]
    bikes = load_bikes()
    if key in bikes:
        del bikes[key]
        save_bikes(bikes)
        await update.message.reply_text(f"{key} 삭제 완료")
    else:
        await update.message.reply_text("그런 기종 없음")

async def list_bikes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ALLOWED_CHAT_ID: return
    bikes = load_bikes()
    if not bikes:
        await update.message.reply_text("등록된 기종 없음")
        return
    msg = "현재 등록 목록:\n"
    for key, v in bikes.items():
        ys = v['years']
        msg += f"- {key}: {v['brand']} {v['model']} ({min(ys)}~{max(ys)})\n"
    await update.message.reply_text(msg)

# 메인
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("list", list_bikes))

    # 매일 한국 9시 (Render UTC 00:00 = KST 09:00, DST 고려 필요 시 조정)
    job_queue: JobQueue = app.job_queue
    job_queue.run_daily(send_daily_report, time=dt_time(0, 0), days=tuple(range(7)))

    print("봇 시작 중...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
