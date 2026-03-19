"""News processing pipeline - orchestrates deduplication and filtering"""
from .news_dedup import deduplicate_articles_smart
from .news_filter import filter_news_lenient


def process_news_pipeline(raw_articles):
    """
    Process raw news articles through the triple-layer pipeline:
    1. Layer 1: Deduplication
    2. Layer 2: Keyword filtering
    3. Format for GPT
    
    Returns processed news data with filter stats
    """
    raw_count = len(raw_articles)
    
    if not raw_articles:
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
    unique_articles = deduplicate_articles_smart(raw_articles)
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
