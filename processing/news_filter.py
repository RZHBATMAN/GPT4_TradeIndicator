"""Layer 2: Keyword filtering for news articles"""
import re


def is_obvious_junk(title, description=""):
    """LAYER 2: Lenient keyword filter"""
    obvious_junk_patterns = [
        r'secret to', r'trick to', r'\d+ ways to', r'you won\'t believe',
        r'shocking', r'amazing', r'incredible',
        r'^why you should', r'^how to', r'^what you need to know about investing',
        r'last week.*recap', r'last month.*review', r'year in review'
    ]
    
    content = (title + " " + description).lower()
    is_junk = any(re.search(pattern, content) for pattern in obvious_junk_patterns)
    return is_junk


def classify_priority(title, description=""):
    """Mark high-priority events"""
    high_priority_patterns = [
        r'(beats|misses|reports) earnings',
        r'earnings (beat|miss)',
        r'q[1-4] (earnings|results)',
        r'(raises|cuts|lowers|increases) (guidance|forecast|outlook)',
        r'stock (sinks|soars|jumps|plunges) \d+%',
        r'shares (fall|rise|jump) \d+%',
        r'(up|down) (1[0-9]|[2-9][0-9])%',
        r'(apple|microsoft|google|alphabet|amazon|nvidia|tesla|meta).*'
        r'(upgrade|downgrade|price target)',
        r'announces (acquisition|merger|layoffs|ceo)',
        r'completes (acquisition|merger)',
        r'sec (approves|rejects|investigates)',
        r'fda (approves|rejects)',
    ]
    
    content = (title + " " + description).lower()
    is_high_priority = any(re.search(pattern, content) for pattern in high_priority_patterns)
    return 'HIGH' if is_high_priority else 'NORMAL'


def filter_news_lenient(articles, verbose=False):
    """LAYER 2: Lenient keyword filter"""
    filtered = []
    stats = {'filtered_junk': 0, 'kept': 0}
    
    for article in articles:
        title = article.get('title', '')
        description = article.get('description', '')
        
        if is_obvious_junk(title, description):
            stats['filtered_junk'] += 1
            continue
        
        article['priority'] = classify_priority(title, description)
        stats['kept'] += 1
        filtered.append(article)
    
    return filtered, stats
