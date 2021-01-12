# -*- coding: utf-8 -*-
from __future__ import absolute_import
import logging
import codecs
from functools import partial
from six.moves.urllib.parse import urlsplit

from scrapy.exceptions import CloseSpider, NotConfigured
from scrapy import signals
from scrapy.utils.misc import load_object
from scrapy.utils.url import add_http_if_no_scheme
from twisted.internet import task

from .expire import Proxies, exp_backoff_full_jitter


logger = logging.getLogger(__name__)


class RotatingProxyMiddleware(object):
    """
    Scrapy downloader 中间件，它循环使用代理池为每个请求选择一个随机代理。

    在Scrapy配置文件或Spider的 CUSTOM_SETTING中，添加
    RotatingProxyMiddleware 和 BanDetectionMiddleware
        DOWNLOADER_MIDDLEWARES = {
            # ...
            'rotating_proxies.middlewares.RotatingProxyMiddleware': 610,
            'rotating_proxies.middlewares.BanDetectionMiddleware': 620,
            # ...
        }

    BanDetection中间件，用于跟踪代理可用性，并避免使用无效代理。
        如果request.meta ['_ ban']为True，并且有效，则认为代理无效
        如果request.meta ['_ ban']为False； 设置为在使用中或已使用
     
        使用随机指数退避重新检查死代理。

    注意！
    默认情况下，所有默认的Scrapy并发配置（DOWNLOAD_DELAY，
     AUTHTHROTTLE _...，CONCURRENT_REQUESTS_PER_DOMAIN等），在处理请求时，将自动启用RotatingProxyMiddleware。
     例如，
     如果您设置CONCURRENT_REQUESTS_PER_DOMAIN = 2，则，每个Spider将与每个代理最多建立2个并发连接。

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
    """
    def __init__(self, proxy_list, logstats_interval, stop_if_no_proxies,
                 max_proxies_to_try, backoff_base, backoff_cap, crawler):

        backoff = partial(exp_backoff_full_jitter, base=backoff_base, cap=backoff_cap)
        self.proxies = Proxies(self.cleanup_proxy_list(proxy_list),
                               backoff=backoff)
        self.logstats_interval = logstats_interval
        self.reanimate_interval = 5
        self.stop_if_no_proxies = stop_if_no_proxies
        self.max_proxies_to_try = max_proxies_to_try
        self.stats = crawler.stats

        self.log_task = None
        self.reanimate_task = None

    @classmethod
    def from_crawler(cls, crawler):
        s = crawler.settings
        proxy_path = s.get('ROTATING_PROXY_LIST_PATH', None)
        if proxy_path is not None:
            with codecs.open(proxy_path, 'r', encoding='utf8') as f:
                proxy_list = [line.strip() for line in f if line.strip()]
        else:
            proxy_list = s.getlist('ROTATING_PROXY_LIST')
        if not proxy_list:
            raise NotConfigured()
        mw = cls(
            proxy_list=proxy_list,
            logstats_interval=s.getfloat('ROTATING_PROXY_LOGSTATS_INTERVAL', 30),
            stop_if_no_proxies=s.getbool('ROTATING_PROXY_CLOSE_SPIDER', False),
            max_proxies_to_try=s.getint('ROTATING_PROXY_PAGE_RETRY_TIMES', 5),
            backoff_base=s.getfloat('ROTATING_PROXY_BACKOFF_BASE', 300),
            backoff_cap=s.getfloat('ROTATING_PROXY_BACKOFF_CAP', 3600),
            crawler=crawler,
        )
        crawler.signals.connect(mw.engine_started,
                                signal=signals.engine_started)
        crawler.signals.connect(mw.engine_stopped,
                                signal=signals.engine_stopped)
        return mw

    def engine_started(self):
        if self.logstats_interval:
            self.log_task = task.LoopingCall(self.log_stats)
            self.log_task.start(self.logstats_interval, now=True)

        if self.reanimate_interval:
            self.reanimate_task = task.LoopingCall(self.reanimate_proxies)
            self.reanimate_task.start(self.reanimate_interval, now=False)

    def reanimate_proxies(self):
        n_reanimated = self.proxies.reanimate()
        if n_reanimated:
            logger.debug("%s proxies moved from 'dead' to 'reanimated'",
                         n_reanimated)

    def engine_stopped(self):
        if self.log_task and self.log_task.running:
            self.log_task.stop()

        if self.reanimate_task and self.reanimate_task.running:
            self.reanimate_task.stop()

    def process_request(self, request, spider):
        if 'proxy' in request.meta and not request.meta.get('_rotating_proxy'):
            return
        proxy = self.proxies.get_random()
        if not proxy:
            if self.stop_if_no_proxies:
                raise CloseSpider("no_proxies")
            else:
                logger.warn("No proxies available; marking all proxies "
                            "as unchecked")
                self.proxies.reset()
                proxy = self.proxies.get_random()
                if proxy is None:
                    logger.error("No proxies available even after a reset.")
                    raise CloseSpider("no_proxies_after_reset")

        request.meta['proxy'] = proxy
        request.meta['download_slot'] = self.get_proxy_slot(proxy)
        request.meta['_rotating_proxy'] = True

    def get_proxy_slot(self, proxy):
        """
        Return downloader slot for a proxy.
        By default it doesn't take port in account, i.e. all proxies with
        the same hostname / ip address share the same slot.
        """
        # FIXME: an option to use website address as a part of slot as well?
        return urlsplit(proxy).hostname

    def process_exception(self, request, exception, spider):
        return self._handle_result(request, spider)

    def process_response(self, request, response, spider):
        return self._handle_result(request, spider) or response

    def _handle_result(self, request, spider):
        proxy = self.proxies.get_proxy(request.meta.get('proxy', None))
        if not (proxy and request.meta.get('_rotating_proxy')):
            return
        self.stats.set_value('proxies/unchecked', len(self.proxies.unchecked) - len(self.proxies.reanimated))
        self.stats.set_value('proxies/reanimated', len(self.proxies.reanimated))
        self.stats.set_value('proxies/mean_backoff', self.proxies.mean_backoff_time)
        ban = request.meta.get('_ban', None)
        if ban is True:
            self.proxies.mark_dead(proxy)
            self.stats.set_value('proxies/dead', len(self.proxies.dead))
            return self._retry(request, spider)
        elif ban is False:
            self.proxies.mark_good(proxy)
            self.stats.set_value('proxies/good', len(self.proxies.good))

    def _retry(self, request, spider):
        retries = request.meta.get('proxy_retry_times', 0) + 1
        max_proxies_to_try = request.meta.get('max_proxies_to_try',
                                              self.max_proxies_to_try)

        if retries <= max_proxies_to_try:
            logger.debug("Retrying %(request)s with another proxy "
                         "(failed %(retries)d times, "
                         "max retries: %(max_proxies_to_try)d)",
                         {'request': request, 'retries': retries,
                          'max_proxies_to_try': max_proxies_to_try},
                         extra={'spider': spider})
            retryreq = request.copy()
            retryreq.meta['proxy_retry_times'] = retries
            retryreq.dont_filter = True
            return retryreq
        else:
            logger.debug("Gave up retrying %(request)s (failed %(retries)d "
                         "times with different proxies)",
                         {'request': request, 'retries': retries},
                         extra={'spider': spider})

    def log_stats(self):
        logger.info('%s' % self.proxies)

    @classmethod
    def cleanup_proxy_list(cls, proxy_list):
        lines = [line.strip() for line in proxy_list]
        return list({
            add_http_if_no_scheme(url)
            for url in lines
            if url and not url.startswith('#')
        })


class BanDetectionMiddleware(object):
    """ 
    用于检测 代理是否被墙的中间件.
    如果Response被墙,则,令request.meta的值为true.

    启用方式参照 class RotatingProxyMiddleware 中的说明::
    
        DOWNLOADER_MIDDLEWARES = {
            # ...
            'rotating_proxies.middlewares.BanDetectionMiddleware': 620,
            # ...
        }

    注意::
        该检测器与RotatingProxyMiddleware组合使用, 其优先级少于RotatingProxyMiddleware(610)



    默认, client会使用BanDectionPolicy检测代理是否被墙，当请求失败时,将视为 被墙；
    接收到 response 则视为 正常；如需复写检测逻辑，可以通过继承 BanDectionPolicy 来自定义检测逻辑，

    例如::
    ROTATING_PROXY_BAN_POLICY = 'myproject.policy.MyBanPolicy'

    The policy must be a class with ``response_is_ban``
    and ``exception_is_ban`` methods. 
    注意::
    继承BanDectionPolicy必须实现 ``response_is_ban`` 和 ``exception_is_ban``。
    这两个方法返回值必须是以下三种：
    True (ban detected)
    False (not a ban)
    None (unknown). It can be convenient to subclass and modify default BanDetectionPolicy::

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

    """
    def __init__(self, stats, policy):
        self.stats = stats
        self.policy = policy

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.stats, cls._load_policy(crawler))

    @classmethod
    def _load_policy(cls, crawler):
        policy_path = crawler.settings.get(
            'ROTATING_PROXY_BAN_POLICY',
            'rotating_proxies.policy.BanDetectionPolicy'
        )
        policy_cls = load_object(policy_path)
        if hasattr(policy_cls, 'from_crawler'):
            return policy_cls.from_crawler(crawler)
        else:
            return policy_cls()

    def process_response(self, request, response, spider):
        is_ban = getattr(spider, 'response_is_ban',
                         self.policy.response_is_ban)
        ban = is_ban(request, response)
        request.meta['_ban'] = ban
        if ban:
            self.stats.inc_value("bans/status/%s" % response.status)
            if not len(response.body):
                self.stats.inc_value("bans/empty")
        return response

    def process_exception(self, request, exception, spider):
        is_ban = getattr(spider, 'exception_is_ban',
                         self.policy.exception_is_ban)
        ban = is_ban(request, exception)
        if ban:
            ex_class = "%s.%s" % (exception.__class__.__module__,
                                  exception.__class__.__name__)
            self.stats.inc_value("bans/error/%s" % ex_class)
        request.meta['_ban'] = ban
