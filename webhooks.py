"""Webhook sending to Option Alpha"""
import requests
from datetime import datetime
import pytz
from config.loader import get_config

ET_TZ = pytz.timezone('US/Eastern')


def send_webhook(signal_data):
    """Send webhook to Option Alpha"""
    config = get_config()
    
    webhook_urls = {
        'TRADE_AGGRESSIVE': config.get('TRADE_AGGRESSIVE_URL'),
        'TRADE_NORMAL': config.get('TRADE_NORMAL_URL'),
        'TRADE_CONSERVATIVE': config.get('TRADE_CONSERVATIVE_URL'),
        'NO_TRADE': config.get('NO_TRADE_URL')
    }
    
    signal = signal_data['signal']
    timestamp = datetime.now(ET_TZ).isoformat()
    
    if signal == "SKIP":
        url = webhook_urls.get('NO_TRADE')
        if url:
            try:
                payload = {'signal': 'SKIP', 'timestamp': timestamp}
                response = requests.post(url, json=payload, timeout=10)
                return {'success': response.status_code in [200, 201, 202]}
            except:
                return {'success': False}
        return {'success': True}
    
    url = webhook_urls.get(signal)
    if not url:
        return {'success': False}
    
    try:
        payload = {'signal': signal, 'timestamp': timestamp}
        response = requests.post(url, json=payload, timeout=10)
        return {'success': response.status_code in [200, 201, 202]}
    except:
        return {'success': False}
