"""News processing module"""
from .news_dedup import deduplicate_articles_smart
from .news_filter import filter_news_lenient
from .pipeline import process_news_pipeline

__all__ = ['deduplicate_articles_smart', 'filter_news_lenient', 'process_news_pipeline']
