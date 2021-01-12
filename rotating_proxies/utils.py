from __future__ import absolute_import
try:
    from urllib2 import _parse_proxy
except ImportError:
    from urllib.request import _parse_proxy


def extract_proxy_hostport(proxy):
    """
    
    提取代理连接串中的host和port:
    
    >>> extract_proxy_hostport('heismart.cn')
    'heismart.cn'
    >>> extract_proxy_hostport('http://www.heismart.cn')
    'www.heismart.cn'
    >>> extract_proxy_hostport('127.0.0.1:8000')
    '127.0.0.1:8000'
    >>> extract_proxy_hostport('127.0.0.1')
    '127.0.0.1'
    >>> extract_proxy_hostport('localhost')
    'localhost'
    >>> extract_proxy_hostport('zot:4321')
    'zot:4321'
    >>> extract_proxy_hostport('http://username:password@heismart.cn:8080')
    'baz:1234'
    """
    return _parse_proxy(proxy)[3]
