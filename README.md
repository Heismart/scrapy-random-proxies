scrapy-rotating-proxies
=======================
This package provides a Scrapy_ middleware to use rotating proxies,
check that they are alive and adjust crawling speed.

.. _Scrapy: https://scrapy.org/


License is MIT.

安装
------------

::
    1. 进入虚拟环境
    2. 进入该中间件源码目录 cd scrapy-rotating-proxies
    3. 安装该中间件到虚拟环境 python setup.py install

使用
-----

在项目的settings.py中添加``ROTATING_PROXY_LIST`` 配置项::

    ROTATING_PROXY_LIST = [
        'proxy1.com:8000',
        'proxy2.com:8031',
        # ...
    ]

或 使用 ``ROTATING_PROXY_LIST_PATH`` 配置 一行一个代理连接串的代理集合文件::

   ROTATING_PROXY_LIST_PATH = '/my/path/proxies.txt'

``ROTATING_PROXY_LIST_PATH`` 和 ``ROTATING_PROXY_LIST`` 两个配置项同时存在时，``ROTATING_PROXY_LIST_PATH``将覆盖``ROTATING_PROXY_LIST`` 

然后在settings.py找到DOWNLOADER_MIDDLEWARES配置项，
并将RotatingProxyMiddleware 和 BanDetectionMiddleware 添加进去。::

    DOWNLOADER_MIDDLEWARES = {
        # ...
        'rotating_proxies.middlewares.RotatingProxyMiddleware': 610,
        'rotating_proxies.middlewares.BanDetectionMiddleware': 620,
        # ...
    }
注意优先级值大小。

如果 你希望启用 自定义的BanDetectionMiddleware，参照rotating_proxies源码中的middlewares.py下,BanDetectionMiddleware的注释说明。
BanDetectionMiddleware 是用于检测代理可用性的中间件,默认的检测规则为 200,301,302, IgnoeRequest均为正常，否则，为无效。
-----
After this all requests will be proxied using one of the proxies from
the ``ROTATING_PROXY_LIST`` / ``ROTATING_PROXY_LIST_PATH``.

Requests with "proxy" set in their meta are not handled by
scrapy-rotating-proxies. To disable proxying for a request set
``request.meta['proxy'] = None``; to set proxy explicitly use
``request.meta['proxy'] = "<my-proxy-address>"``.


Concurrency
-----------

By default, all default Scrapy concurrency options (``DOWNLOAD_DELAY``,
``AUTHTHROTTLE_...``, ``CONCURRENT_REQUESTS_PER_DOMAIN``, etc) become
per-proxy for proxied requests when RotatingProxyMiddleware is enabled.
For example, if you set ``CONCURRENT_REQUESTS_PER_DOMAIN=2`` then
spider will be making at most 2 concurrent connections to each proxy,
regardless of request url domain.

Customization
-------------

``scrapy-rotating-proxies`` keeps track of working and non-working proxies,
and re-checks non-working from time to time.

Detection of a non-working proxy is site-specific.
By default, ``scrapy-rotating-proxies`` uses a simple heuristic:
if a response status code is not 200, response body is empty or if
there was an exception then proxy is considered dead.

You can override ban detection method by passing a path to
a custom BanDectionPolicy in ``ROTATING_PROXY_BAN_POLICY`` option, e.g.::

    # settings.py
    ROTATING_PROXY_BAN_POLICY = 'myproject.policy.MyBanPolicy'

The policy must be a class with ``response_is_ban``
and ``exception_is_ban`` methods. These methods can return True
(ban detected), False (not a ban) or None (unknown). It can be convenient
to subclass and modify default BanDetectionPolicy::

    # myproject/policy.py
    from rotating_proxies.policy import BanDetectionPolicy

    class MyPolicy(BanDetectionPolicy):
        def response_is_ban(self, request, response):
            # use default rules, but also consider HTTP 200 responses
            # a ban if there is 'captcha' word in response body.
            ban = super(MyPolicy, self).response_is_ban(request, response)
            ban = ban or b'captcha' in response.body
            return ban

        def exception_is_ban(self, request, exception):
            # override method completely: don't take exceptions in account
            return None

Instead of creating a policy you can also implement ``response_is_ban``
and ``exception_is_ban`` methods as spider methods, for example::

    class MySpider(scrapy.Spider):
        # ...

        def response_is_ban(self, request, response):
            return b'banned' in response.body

        def exception_is_ban(self, request, exception):
            return None

It is important to have these rules correct because action for a failed
request and a bad proxy should be different: if it is a proxy to blame
it makes sense to retry the request with a different proxy.

Non-working proxies could become alive again after some time.
``scrapy-rotating-proxies`` uses a randomized exponential backoff for these
checks - first check happens soon, if it still fails then next check is
delayed further, etc. Use ``ROTATING_PROXY_BACKOFF_BASE`` to adjust the
initial delay (by default it is random, from 0 to 5 minutes). The randomized
exponential backoff is capped by ``ROTATING_PROXY_BACKOFF_CAP``.

参数配置：

*``ROTATING_PROXY_LIST``-代理连接串列表, 每代理连接串英文半角逗号分隔，如：" user1:passwd1@heismart.cn,http://heismart.cn:8080";
*``ROTATING_PROXY_LIST_PATH``-代理连接串列表的文件的路径;
*``ROTATING_PROXY_LOGSTATS_INTERVAL``-统计记录间隔以秒为单位， 默认为:30;
*``ROTATING_PROXY_CLOSE_SPIDER``-为True时，即没有可用代理,则,Spider将自动停止。如果为False（默认值），则，对所有代理进行重新检查。
*``ROTATING_PROXY_PAGE_RETRY_TIMES``-代理请求的重试次数。重试逻辑主要用于解决误判当前代理为故障代理。
    具体逻辑：
        若使用当前代理下载页面失败时，会使用下一个代理去尝试，当多个代理尝试下载均失败时，则，视为当前需要下载的页面有问题，而不是代理无效。
        这个逻辑会消耗额外的代理，慎用!!!
        默认为：5
            !!! 即，最多使用5个代理尝试下载当前请求页面。
    *``ROTATING_PROXY_BACKOFF_BASE``-基本退避时间，以秒为单位，默认值为300（即5分钟），用于 Dead状态代理的复活检测周期判断。
    *``ROTATING_PROXY_BACKOFF_CAP``-退避时间上限，以秒为单位，默认值为3600（即60分钟）,用于 Dead状态代理的复活检测周期上限，
                                    超过这个时间则，强制设置为unchecked，使其能获得复活机会。
``ROTATING_PROXY_BAN_POLICY`` - 代理可用性检测类,即:是否被墙. 默认是 ``'rotating_proxies.policy.BanDetectionPolicy'``.
  