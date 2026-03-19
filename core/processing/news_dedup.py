"""Layer 1: News deduplication with fuzzy matching"""
import re
from difflib import SequenceMatcher


def normalize_title(title):
    """Normalize title for comparison"""
    normalized = title.lower()
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = ' '.join(normalized.split())
    return normalized


def titles_are_similar(title1, title2, threshold=0.85):
    """Check if two titles are 85%+ similar"""
    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)
    similarity = SequenceMatcher(None, norm1, norm2).ratio()
    return similarity >= threshold


def deduplicate_articles_smart(articles):
    """LAYER 1: Algorithmic deduplication with fuzzy matching"""
    if not articles:
        return []
    
    source_priority = {
        'Reuters': 1,
        'Bloomberg': 2,
        'Google News': 3,
        'Yahoo Finance': 4,
        'CNBC': 5,
        'MarketWatch': 6,
        'Other': 99
    }
    
    def get_source_priority(article):
        source = article.get('source', 'Other')
        for key in source_priority:
            if key.lower() in source.lower():
                return source_priority[key]
        return source_priority['Other']
    
    articles_sorted = sorted(
        articles,
        key=lambda x: (
            -x['published_time'].timestamp(),
            get_source_priority(x)
        )
    )
    
    unique = []
    seen_normalized = {}
    
    for article in articles_sorted:
        title = article['title']
        norm_title = normalize_title(title)
        
        if norm_title in seen_normalized:
            continue
        
        is_duplicate = False
        for seen_norm, seen_data in seen_normalized.items():
            if titles_are_similar(title, seen_data['original']):
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique.append(article)
            seen_normalized[norm_title] = {
                'original': title,
                'article': article
            }
    
    return unique
