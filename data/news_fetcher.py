"""News fetching from multiple RSS sources"""
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from dateutil import parser as date_parser
import pytz
from processing.news_dedup import deduplicate_articles_smart
from processing.news_filter import filter_news_lenient

ET_TZ = pytz.timezone('US/Eastern')


def parse_rss_feed(url, source_name):
    """Parse RSS feed using direct HTTP + XML parsing"""
    try:
        response = requests.get(
            url, 
            timeout=15, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        articles = []
        now = datetime.now(ET_TZ)
        
        for item in root.findall('.//item'):
            try:
                title_elem = item.find('title')
                title = title_elem.text if title_elem is not None and title_elem.text else 'No title'
                
                link_elem = item.find('link')
                link = link_elem.text if link_elem is not None and link_elem.text else ''
                
                description_elem = item.find('description')
                description = description_elem.text if description_elem is not None and description_elem.text else ''
                
                pubdate_elem = item.find('pubDate')
                if pubdate_elem is not None and pubdate_elem.text:
                    try:
                        pub_time = date_parser.parse(pubdate_elem.text)
                        if pub_time.tzinfo is None:
                            pub_time = ET_TZ.localize(pub_time)
                        else:
                            pub_time = pub_time.astimezone(ET_TZ)
                    except:
                        pub_time = now
                else:
                    pub_time = now
                
                hours_ago = (now - pub_time).total_seconds() / 3600
                
                articles.append({
                    'title': title,
                    'published_time': pub_time,
                    'hours_ago': hours_ago,
                    'source': source_name,
                    'description': description,
                    'link': link
                })
                
            except Exception as e:
                print(f"Error parsing item from {source_name}: {e}")
                continue
        
        return articles
        
    except Exception as e:
        print(f"Error fetching {source_name}: {e}")
        return []


def fetch_yahoo_finance_news():
    """Source 1: Yahoo Finance RSS"""
    try:
        rss_feeds = [
            ('https://finance.yahoo.com/news/rssindex', 'Yahoo Finance - Market'),
            ('https://finance.yahoo.com/rss/headline?s=^GSPC', 'Yahoo Finance - S&P 500'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US', 'Yahoo Finance - Apple'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US', 'Yahoo Finance - Microsoft'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL&region=US&lang=en-US', 'Yahoo Finance - Google'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=AMZN&region=US&lang=en-US', 'Yahoo Finance - Amazon'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US', 'Yahoo Finance - Nvidia'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US', 'Yahoo Finance - Tesla'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=META&region=US&lang=en-US', 'Yahoo Finance - Meta'),
        ]
        
        all_articles = []
        for feed_url, feed_name in rss_feeds:
            articles = parse_rss_feed(feed_url, feed_name)
            all_articles.extend(articles)
        
        return all_articles
        
    except Exception as e:
        print(f"ERROR in Yahoo Finance: {e}")
        return []


def fetch_google_news_rss():
    """Source 2: Google News RSS"""
    try:
        queries = [
            'stock+market+OR+S%26P+500',
            'earnings+OR+guidance',
            'Apple+OR+Microsoft+OR+Google+OR+Amazon',
            'Nvidia+OR+Tesla+OR+Meta',
            'Federal+Reserve+OR+inflation'
        ]
        
        all_articles = []
        for query in queries:
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            articles = parse_rss_feed(url, 'Google News')
            all_articles.extend(articles)
        
        return all_articles
        
    except Exception as e:
        print(f"ERROR in Google News: {e}")
        return []


def fetch_news_multi_source():
    """Triple-layer news processing"""
    try:
        all_articles = []
        
        yahoo_articles = fetch_yahoo_finance_news()
        google_articles = fetch_google_news_rss()
        
        all_articles.extend(yahoo_articles)
        all_articles.extend(google_articles)
        
        raw_count = len(all_articles)
        
        if not all_articles:
            return {
                'count': 0,
                'summary': 'No news available.',
                'articles': [],
                'filter_stats': {
                    'raw_articles': 0,
                    'duplicates_removed': 0,
                    'unique_articles': 0,
                    'junk_filtered': 0,
                    'sent_to_gpt': 0
                }
            }
        
        # LAYER 1: Deduplication
        unique_articles = deduplicate_articles_smart(all_articles)
        duplicates_removed = raw_count - len(unique_articles)
        
        # LAYER 2: Keyword filter
        filtered_articles, filter_stats = filter_news_lenient(unique_articles, verbose=False)
        
        if not filtered_articles:
            return {
                'count': 0,
                'summary': 'No actionable news after filtering.',
                'articles': [],
                'filter_stats': {
                    'raw_articles': raw_count,
                    'duplicates_removed': duplicates_removed,
                    'unique_articles': len(unique_articles),
                    'junk_filtered': filter_stats['filtered_junk'],
                    'sent_to_gpt': 0
                }
            }
        
        filtered_articles.sort(key=lambda x: x['published_time'], reverse=True)
        
        # Format for GPT
        news_summary = ""
        for article in filtered_articles[:30]:
            time_str = article['published_time'].strftime("%I:%M %p")
            hours_ago = article['hours_ago']
            
            if hours_ago < 1:
                recency = "⚠️ VERY RECENT"
            elif hours_ago < 3:
                recency = "🔸 RECENT"
            elif hours_ago < 6:
                recency = "• Somewhat recent"
            else:
                recency = "• Earlier today"
            
            priority = article.get('priority', 'NORMAL')
            priority_marker = "🔥" if priority == 'HIGH' else ""
            
            news_summary += f"[{time_str}] {recency} {priority_marker} ({article['source']})\n"
            news_summary += f"   {article['title']}\n"
            if article['description']:
                desc = article['description'][:150]
                news_summary += f"   {desc}...\n"
            news_summary += "\n"
        
        return {
            'count': len(filtered_articles),
            'summary': news_summary.strip(),
            'articles': filtered_articles[:30],
            'filter_stats': {
                'raw_articles': raw_count,
                'duplicates_removed': duplicates_removed,
                'unique_articles': len(unique_articles),
                'junk_filtered': filter_stats['filtered_junk'],
                'sent_to_gpt': filter_stats['kept']
            }
        }
        
    except Exception as e:
        print(f"CRITICAL ERROR in fetch_news_multi_source: {e}")
        import traceback
        traceback.print_exc()
        return {
            'count': 0,
            'summary': f'News fetch failed: {str(e)}',
            'articles': [],
            'filter_stats': {
                'raw_articles': 0,
                'duplicates_removed': 0,
                'unique_articles': 0,
                'junk_filtered': 0,
                'sent_to_gpt': 0
            }
        }
