"""News processing module"""
from .news_dedup import deduplicate_articles_smart
from .news_filter import filter_news_lenient

__all__ = ['deduplicate_articles_smart', 'filter_news_lenient']
