# bot.py
import os
import re
import logging
from typing import List, Dict
import requests
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ============ CONFIG ============
BOT_TOKEN = "8506637173:AAE3VwPm7DLVMzEsubB5ar2TGzRdB6F5jeE"  # Replace with your bot token

# ============ SETUP ============
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# ============ SHOPIFY CHECKER ============
class ShopifyChecker:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
    def check_site(self, url: str) -> Dict:
        """Check single site and extract prices"""
        result = {
            'url': url,
            'shopify': False,
            'working': False,
            'price_range': None,
            'min_price': None,
            'max_price': None,
            'product_count': 0,
            'error': None
        }
        
        try:
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url
                
            response = self.session.get(url, timeout=10)
            
            if response.status_code == 200:
                html = response.text.lower()
                
                # Check if Shopify
                shopify_signs = ['cdn.shopify.com', 'myshopify.com', 'shopify-section', 'variant_id']
                if any(sign in html for sign in shopify_signs):
                    result['shopify'] = True
                    
                    # Extract prices
                    prices = re.findall(r'\$(\d+\.?\d*)', html)
                    prices = [float(p) for p in prices if 0.01 < float(p) < 1000000]
                    prices = list(set(prices))[:100]  # Unique prices
                    
                    if prices:
                        result['working'] = True
                        result['min_price'] = min(prices)
                        result['max_price'] = max(prices)
                        result['price_range'] = f"${min(prices):.2f} - ${max(prices):.2f}"
                        result['product_count'] = len(prices)
                        
        except Exception as e:
            result['error'] = str(e)
            
        return result
        
    def check_multiple(self, urls: List[str]) -> List[Dict]:
        """Check multiple sites"""
        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(self.check_site, url) for url in urls]
            for future in futures:
                results.append(future.result())
        return results

# ============ BOT HANDLERS ============
checker = ShopifyChecker()
user_data = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Shopify Site Checker Bot*\n\n"
        "Send me URLs or upload a file with URLs\n"
        "I'll check which sites are Shopify and show price ranges\n\n"
        "Commands:\n"
        "/start - Show this\n"
        "/stats - Show stats\n"
        "/export - Export results\n"
        "/clear - Clear results",
        parse_mode='Markdown'
    )

async def handle_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Extract URLs
    urls = [u.strip() for u in text.split('\n') if u.strip() and ('http' in u or '.' in u)]
    
    if not urls:
        await update.message.reply_text("❌ No valid URLs found")
        return
        
    if len(urls) > 50:
        await update.message.reply_text("⚠️ Maximum 50 URLs at once")
        return
        
    msg = await update.message.reply_text(f"🔍 Checking {len(urls)} sites...")
    
    # Check sites
    results = checker.check_multiple(urls)
    user_data[user_id] = results
    
    # Filter working Shopify sites
    working = [r for r in results if r['working'] and r['shopify']]
    
    if working:
        reply = "✅ *Working Shopify Sites with Prices:*\n\n"
        for site in working:
            reply += f"• {site['url']}\n  Price Range: {site['price_range']}\n  Products: {site['product_count']}\n\n"
    else:
        reply = "❌ No working Shopify sites found"
        
    await msg.edit_text(reply, parse_mode='Markdown')

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    file = update.message.document
    
    if file.file_size > 5 * 1024 * 1024:
        await update.message.reply_text("❌ File too large (max 5MB)")
        return
        
    msg = await update.message.reply_text("📥 Processing file...")
    
    try:
        # Download file
        file_obj = await file.get_file()
        file_bytes = await file_obj.download_as_bytearray()
        
        # Parse file
        if file.file_name.endswith('.csv'):
            df = pd.read_csv(BytesIO(file_bytes))
        elif file.file_name.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(BytesIO(file_bytes))
        else:
            df = pd.read_csv(BytesIO(file_bytes), header=None, names=['url'])
            
        # Extract URLs
        urls = []
        for col in df.columns:
            if 'url' in col.lower() or 'link' in col.lower() or 'site' in col.lower():
                urls.extend(df[col].dropna().tolist())
                break
        if not urls:
            # Try all columns
            for col in df.columns:
                for val in df[col]:
                    if isinstance(val, str) and ('http' in val or '.' in val):
                        urls.append(val)
                        
        urls = list(set([str(u).strip() for u in urls if str(u).strip()]))
        
        if not urls:
            await msg.edit_text("❌ No URLs found in file")
            return
            
        if len(urls) > 100:
            await msg.edit_text("⚠️ Too many URLs (max 100)")
            return
            
        await msg.edit_text(f"🔍 Checking {len(urls)} sites from file...")
        
        # Check sites
        results = checker.check_multiple(urls)
        user_data[user_id] = results
        
        # Show results
        working = [r for r in results if r['working'] and r['shopify']]
        
        if working:
            reply = "✅ *Working Shopify Sites Found:*\n\n"
            for site in working[:20]:  # Show first 20
                reply += f"• {site['url']}\n  💰 {site['price_range']}\n  📦 {site['product_count']} products\n\n"
            if len(working) > 20:
                reply += f"\n... and {len(working)-20} more sites"
        else:
            reply = "❌ No working Shopify sites found"
            
        await msg.edit_text(reply, parse_mode='Markdown')
        
    except Exception as e:
        await msg.edit_text(f"❌ Error: {str(e)}")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    results = user_data.get(user_id, [])
    
    if not results:
        await update.message.reply_text("No data. Send URLs first.")
        return
        
    total = len(results)
    shopify = sum(1 for r in results if r['shopify'])
    working = sum(1 for r in results if r['working'] and r['shopify'])
    
    await update.message.reply_text(
        f"📊 *Statistics*\n\n"
        f"Total Sites: {total}\n"
        f"Shopify Sites: {shopify}\n"
        f"Working Sites: {working}\n"
        f"Success Rate: {(working/total*100):.1f}%" if total > 0 else "0%",
        parse_mode='Markdown'
    )

async def export_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    results = user_data.get(user_id, [])
    
    if not results:
        await update.message.reply_text("No results to export")
        return
        
    # Create CSV
    df = pd.DataFrame(results)
    csv_data = df.to_csv(index=False)
    
    await update.message.reply_document(
        document=BytesIO(csv_data.encode()),
        filename='shopify_results.csv',
        caption='📊 Shopify Site Check Results'
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_data:
        del user_data[user_id]
    await update.message.reply_text("🗑️ Results cleared")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Cancelled")

# ============ MAIN ============
def main():
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("export", export_results))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("cancel", cancel))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_urls))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    
    # Start bot
    print("🤖 Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
